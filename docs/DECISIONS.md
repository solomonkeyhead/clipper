# Decisions

Choices made without stopping to ask, per BUILD_BRIEF.md section 4. Each says
what was decided, why, and what it would cost to reverse.

---

## Phase 0

### D1. Project root is `C:\Users\marc\Clipper`

Empty, outside OneDrive (`C:\Users\marc\OneDrive - Colostate`), and already an
allowed working directory. `data/` therefore sits outside the synced tree by
default, which is what section 17 asks for. `clipper doctor` still checks and
warns, because `CLIPPER_DATA_DIR` can move it somewhere worse.

### D2. Python 3.12.14, installed and managed by `uv`

The machine had 3.14.4, 3.13.3 and 3.13.2 but no 3.11/3.12. Rather than change
the system Python, `uv python install 3.12` puts a standalone interpreter under
`%APPDATA%\uv` and the venv points at it. Nothing on the user's PATH changes.

3.12 over 3.11 for newer wheels; `<3.13` because that is where the dependency set
is best covered today (onnxruntime requires >=3.11, so 3.10 is out regardless).

### D3. OpenCV YuNet for face detection, not MediaPipe

Section 11.1 offers either. YuNet wins:

- `cv2.FaceDetectorYN` is in OpenCV core (verified present in 5.0.0.93), so it
  adds **zero** new dependencies — OpenCV is needed for frame IO anyway.
- MediaPipe pins narrow Python/protobuf ranges and its Windows wheel support
  trails new releases; it is the single most likely thing to break this install.
- YuNet gives 5 facial landmarks, enough for the headroom framing in section 11.1.

Cost to reverse: `faces.py` is written behind a small detector interface, so
swapping in MediaPipe means one new implementation of that interface.

### D4. No PySceneDetect — histogram-difference cut detection instead

Section 11.1 permits either. PySceneDetect depends on `opencv-python`, which
collides with the `opencv-python-headless` already required (two cv2 installs in
one environment). Scene-cut detection for our purpose is a frame-to-frame
histogram distance with a threshold — perhaps 30 lines against frames we are
already decoding for face detection. Not worth a dependency conflict.

### D5. No librosa — numpy + scipy + soundfile for the audio signal

Section 9.2 asks for RMS energy, dynamic range, spectral-flux peaks, speech rate
and pause structure, and says "keep this cheap". All of that is a few dozen lines
of numpy/scipy over a WAV. librosa would pull in numba and a long import time for
features we do not use.

### D6. `src/clipper/evaluate/`, not `src/clipper/eval/`

The layout in section 5 has both a package `src/clipper/eval/` and a top-level
`eval/` directory holding `videos.yaml` and `report.md`. Two things named `eval`
in one repo is a readability trap, and `eval` shadows a builtin. The package is
`evaluate`; the top-level data directory keeps the name `eval` that the CLI
already references.

### D7. Encoders are probed, never assumed

`ffmpeg -encoders` lists `h264_nvenc` on this machine and it then fails to open
(driver 591.86 exposes NVENC API 13.0; FFmpeg 9.0.1 needs 13.1). So
`render.encoder: auto` runs a one-frame encode to decide, caches the answer, and
falls back to libx264 — measured at 10.4x realtime for 1080x1920, comfortably
inside the Phase 4 budget.

An **explicit** `h264_nvenc` raises instead of falling back: if you asked for the
GPU encoder, silently getting the CPU one hides the problem.

### D8. Fonts and the face model are downloaded, not committed

`Anton-Regular.ttf` (OFL) for `bold_pop`, `Inter.ttf` (OFL) for the quieter
styles, and the YuNet ONNX weights — 1.3 MB of binary that upstream already
hosts. `clipper doctor --fix` fetches them into the git-ignored `assets/`, with
size floors so a captive-portal HTML error page cannot masquerade as a font.

Anton is the display face for `bold_pop` specifically: single weight, very heavy,
designed for headlines, which is what a burned-in caption is.

### D9. Config is `frozen=True` and `extra="forbid"`

One `Config` object is threaded through every stage, so accidental mutation would
be a genuinely nasty bug. `extra="forbid"` turns a YAML typo (`widht: 1080`) into
an error at load instead of a silently-defaulted value discovered three stages
later.

### D10. Stub commands exit 2 and name their phase

`clipper score abc` prints that it lands in Phase 3 rather than failing with an
import error or, worse, appearing to work.

---

## Proposed starter eval set (Phase 5) — for the user to edit

BUILD_BRIEF.md section 14.1 asks for 10–15 varied long-form videos with heatmap
data, preferring Creative Commons or authorized footage. **Not yet assembled** —
this needs URLs the user is comfortable downloading, and a heatmap only exists on
videos with enough watch time. The shape to aim for:

| Category | Count | Why it is in the set |
|---|---|---|
| Long-form interview / podcast | 4–5 | The primary use case; sparse peaks in a flat field |
| Conference or lecture talk | 3 | Structured, signposted — text signal should do well here |
| Panel or debate (2+ speakers) | 2–3 | Exercises the two-speaker stack layout |
| Solo monologue / vlog | 2 | Tests the audio signal without turn-taking cues |
| One deliberately flat video | 1 | The set needs a video where the right answer is *few clips* |

That last row matters: without it, the harness can only measure ranking, never
the tool's willingness to return two clips instead of five.

The eval set will be proposed concretely at the start of Phase 5 so the user can
approve the specific videos before anything is downloaded.

---

## Phase 1

### D11. One FFmpeg process per clip, with `sendcmd` driving the follow-crop

BUILD_BRIEF.md section 11.1 proposes decoding with OpenCV, applying the crop
trajectory per frame in Python, and piping raw frames to FFmpeg. It also says to
use a cleaner FFmpeg-only approach if one meets the budget. One does.

`crop`'s `x` and `y` options are marked command-capable (`T` in
`ffmpeg -h filter=crop`), so a time-varying crop can be driven by FFmpeg's own
`sendcmd` filter reading a generated script of `<t> crop x <px>;` lines. The
whole render — cut, reframe, burn captions, loudnorm, encode — becomes a single
invocation.

Why this is better than the frame pipe:

- Raw 1080x1920 at 30 fps is ~93 MB/s through a pipe, with a Python loop in the
  hot path. The FFmpeg-only path has neither.
- Decode can use NVDEC, which **does** work on this machine even though NVENC
  does not.
- One process means one failure mode and one stderr to read.

**Verified**, not assumed: a 1280x720 source was panned across its full width by
a generated sendcmd script and the extracted frames show the crop tracking. The
integration test `test_the_crop_actually_moves` keeps it honest — it renders the
moving-disc fixture and asserts the disc stays near the centre of the output,
which only holds if sendcmd actually drove the crop.

Face detection still decodes frames in Python, but at `face_sample_fps` (5 fps),
not 30, and only to *plan* the trajectory — never in the render path.

Cost to reverse: `render/graph.py` builds the command; a frame-pipe renderer
would be an alternative implementation behind the same `RenderSpec`.

### D12. `-fps_mode` instead of `-vsync`

FFmpeg 9 removed `-vsync` outright — passing it is a hard error, not a warning.
Found while building the fixture generator. There is a unit test asserting the
built command never contains it.

### D13. Synthetic fixtures carry their own analytic trajectory

`moving_face_video` returns both the file and a `FaceTrack` describing exactly
where the stand-in face is at any time. That lets a test assert the crop followed
*the right path* rather than only that a file appeared, which is the difference
between testing the render and testing that FFmpeg exists.

---

## Phase 3

### D14. Selection gates on an absolute LLM total, not only the composite

The change proposed in PLAN.md P1, now implemented. `selection.min_llm_total`
(default 5.5, on the raw 0-10 rubric scale) is the gate that decides whether a
clip is good enough; `min_composite` is retained as a relative guard and its
default lowered from 0.55 to 0.35.

The reasoning, restated because it is the most consequential departure in the
build: every component of the composite is a **per-video percentile rank**, and
percentile ranks are uniform by construction. The best candidate in any video
scores near 1.0 whether that video is excellent or worthless, so a threshold on
the composite cannot express "this source contains nothing worth clipping" --
which is precisely what BUILD_BRIEF.md section 1 promises the tool will do.

The LLM total is the only quantity on an absolute scale, because the prompts
instruct the model that most clips should score 3-6 and that 8+ is exceptional.
That makes it a genuine across-video judgement.

`selection.use_absolute_gate: false` restores the brief's literal behaviour, so
Phase 5 can measure whether this helps rather than assuming it does.

### D15. `needs_prior_context` with the second opinion disabled

Section 9.1 hard-drops on `needs_prior_context` "with both prompts agreeing",
which is undefined when `llm.use_second_opinion` is off. Resolved: the single
verdict is used, and the drop reason says "(single opinion)" so `explain` makes
the weaker basis visible.

### D16. The LLM cache is keyed per candidate, not per batch

A batch sharing seven of eight candidates with an earlier run costs one call for
the new one, not a full miss. This matters because batch composition shifts
whenever candidate generation changes, which would otherwise invalidate every
cached score on every re-run.

### D17. Signals are computed cheapest-first

Text, then audio, then heatmap, then the LLM. A failure in a free signal
surfaces before any quota is spent, and the LLM -- the only stage that can cost
money or hit a rate limit -- runs last.

### D18. A failed signal excludes a candidate from that signal, not from scoring

If the LLM cannot score one candidate, that candidate is ranked on the signals
it does have, with the weights renormalised for it individually. Treating a
missing signal as a zero would penalise a candidate for a transport failure
rather than for its content.

---

## Phase 4

### D19. Clips are rendered and checked one at a time, not in a batch

Rendering is the most expensive stage, so a clip is rendered, QA'd, and either
accepted or replaced before the next one starts. A failure then costs exactly
one extra render rather than re-running a batch.

The replacement always comes from the *reserve* list, which `select/pick.py`
fills only with candidates that already cleared the quality gate. So filling a
failed clip's slot can never quietly substitute filler -- if no qualifying
reserve exists, the run returns fewer clips and says so.

### D20. QA failures are for broken output; imperfections warn

A `fail` discards the clip and promotes a lower-scoring one, so the bar for
failing has to be "this output is broken", not "this output is imperfect".
Applying that consistently moved three checks:

- **frozen frames** -- fails only when a large *fraction* of the clip is frozen
  (a stalled render); an absolute overrun merely warns (a held shot or a slide).
- **lead silence** -- warns. Whisper's word timestamps lead the audio by ~0.4 s,
  so this fires routinely on clips that are fine.
- **fps drift** -- warns. A clip at 29.97 instead of 30 is playable.

Genuine failures remain: wrong resolution, missing audio, out-of-range loudness,
black frames, duration outside campaign bounds, captions outside the clip, and
too few captioned words.

### D21. The two "max_silence_ratio" settings are deliberately different numbers

`candidates.max_silence_ratio` (0.25) estimates silence from word-timing gaps;
`qa.max_silence_ratio` (0.35) measures acoustic silence in the rendered audio.
The acoustic measure reads about 8 points higher on the same clip, so setting
them equal made the gate reject what the filter had just passed -- after paying
for the render. Both config files now say why they differ.

The alternative -- making the candidate filter measure acoustically -- was
rejected because that filter runs on every candidate before any audio is
decoded, and the whole point of it is to be cheap.

### D22. Face detection runs downscaled and sequentially

Two changes, both measured: detect on a 640-wide copy rather than the source
resolution (YuNet is trained at 320x320, so the extra pixels buy nothing), and
decode sequentially with frame skipping rather than seeking per sample. Together
these took a 20-second scan from 15.4 s to 3.4 s.

This is the only place in the pipeline that decodes frames in Python, and it
only ever *plans* the crop trajectory -- the render itself stays a single FFmpeg
process (D11).

### D23. `runner.py` rather than extending `pipeline.py`

`pipeline.py` owns stage boundaries and caching for the scoring half, which the
eval harness (Phase 5) reuses without ever rendering anything. The render/QA/
manifest half lives in `runner.py` so the eval harness does not import the
rendering stack to score a video.

### D24. Clip ids come from a monotonic attempt counter

Not from the accepted-clip rank. Reusing the rank after a rejection made two
rejected files collide on both id and filename, so one silently overwrote the
other's reason file. The accepted clips still read `001`, `002`, ... in order
when nothing fails, which is the common case.

---

## Reframing fixes (post-Phase 4, from real-footage feedback)

### D25. Camera movement has a deadzone

The follow-crop chased every small head movement, and because the trajectory is
applied as discrete steps at the 5 Hz sampling rate those corrections read as
*shake* rather than as motion. The camera now holds completely still until the
subject drifts past `render.pan_deadzone` (default 18%) of the crop width.

When it does move it aims for the **edge** of the deadzone, not dead centre.
Recentring fully would make it twitch back and forth every time the subject
crossed the boundary.

Measured by replaying a real clip's face path: 57 camera moves became 0. A
seated speaker on a fixed webcam now yields a genuinely fixed crop, which is
the correct output for that input.

### D26. `content_stack`: a fourth layout for screen-share sources

BUILD_BRIEF.md section 11.1 lists three layouts, all of which assume the face is
the subject. That assumption fails for screen-share content -- gameplay, a board,
a slide deck, a code editor -- where the webcam is a small inset and the subject
of the video is elsewhere on screen. On the first real test source a 9:16 crop
centred on the face captured the webcam and the move list and cut the chess
board out entirely.

`content_stack` puts the content above the speaker, each pane scaled to cover
and centre-cropped. The split follows the content's own aspect ratio rather than
a fixed ratio, clamped so neither pane collapses, with the **webcam** absorbing
the slack: a webcam crops gracefully because its subject is centred with slack
around it, whereas cropping a board or a slide loses information.

`render.detect_screen_share: false` disables it entirely.

### D27. Content is found by distance from the background tone, not by motion

The first detector used temporal variance and edge density -- "content is what
moves and has structure". It found nothing on real footage, because a chess
board is *static* between moves and its flat squares carry few edges at grid
resolution. The activity map spread thinly across the whole frame.

What actually separates content from background is how far a region sits from
the frame's dominant background tone. That found the board as a single blob of
1120x1080 (aspect 1.04 -- the board exactly) and the webcam as a separate
740x480 blob. Temporal variance is retained at a quarter weight so a moving
element on a background-toned surface is not missed entirely.

### D28. Regions are identified by which one contains the face

Rather than inferring a webcam box from a margin around the face. The margin
approach had to guess how far a webcam extends beyond its occupant, and got it
badly wrong: from a 138x182 face it inferred a 685x802 box that masked out most
of the frame and left no content to find.

Taking the blob *containing* the face as the webcam, and the largest blob that
does not as the content, needs no such guess.

### D29. Blob bounding boxes have their sparse edges trimmed

Connected-component boxes are generous. At grid resolution a bright overlay
title bridged to the chess board and dragged its box from 1120 out to 1320 wide,
which put a sliver of title graphic in the content pane and a band of title text
above the webcam. Boundary rows and columns that are less than 55% filled are
now trimmed away.

### D30. The background tone comes from the frame border

Not from the whole frame. The global mode inverts the detection whenever the
content area is large and flat-toned: it wins the histogram, gets classified as
background, and the actual background is then treated as content.

Real footage hid this -- a chess board's alternating squares spread across
several histogram bins while the black surround concentrated in one, so the mode
happened to land correctly. A synthetic test with a uniformly-toned content
block caught it. A plain slide or a solid-colour game background would have
triggered it in production.

Background is by definition what *surrounds* the content, so the outer ring of
the frame is a far stronger prior. The mode of the border rather than its mean,
because a border that is half dark surround and half bright content would land
between the two and match neither.

### D31. A face too small to be the subject means "keep the whole frame"

The screen-share detector (D26) handles a webcam inset *beside* separable
content. A second real source exposed the other half of the problem: a reaction
video with two small circular reaction cams composited over full-frame footage,
where there is no separate content region because the footage **is** the whole
frame.

The old rule chose `two_speaker_stack` there and would have blown two tiny
circular avatars up to fill the output, discarding everything the clip was
about. So: when the dominant face is below `render.min_subject_face_ratio`
(default 13% of frame width) and no separate content region was found, the
layout falls back to `blurred_fit`, which preserves the entire frame.

**This is deliberately a blunt rule, because the sharp one does not exist.** The
obvious refinement is to distinguish a composited overlay from a genuine wide
two-shot, and the obvious signals do not separate them. Measured on the same
video:

| | face width | distance to nearest edge |
|---|---|---|
| Genuine wide two-shot | 7.4% / 8.7% | 0.29 / 0.22 |
| Reaction-cam overlays | 6.5-7.7% | 0.10-0.21 |

Neither size nor position separates the two cases -- the ranges overlap. Telling
them apart properly means detecting that a region is *composited* (a hard
circular or rectangular boundary whose contents are unrelated to the surrounding
pixels), which is a real piece of work and was not attempted here.

Given that, the conservative choice is right: when it is unclear whether small
faces are the subject or an overlay, keep the whole frame. Letterboxing a wide
two-shot that could have been stacked costs a little engagement. Cropping to an
overlay avatar throws the content away entirely.

The threshold is exposed as `render.min_subject_face_ratio` so it can be tuned
per source type rather than argued about in the abstract.

### D32. Faces are tracked across frames, not recomputed each frame

`_dominant_face` picked the largest face in each frame independently, with no
memory of the previous one. With two people on screen the target teleported
between them: measured on real footage, **10 side-flips across 68% of the frame
width** in a 36-second clip. The pan-speed limit then stopped the camera ever
reaching either of them, so it sat between the two showing **neither face** --
a clip of a sofa and an arm.

Detections are now associated into per-person tracks by nearest-neighbour with a
distance gate, and the follow-crop follows one *track*, not a per-frame winner.

### D33. Only subject-sized tracks count as subjects

Filtering happens on tracks, before any layout decision. Without it, a clip with
one real speaker plus two reaction-cam overlays looked like three people that no
framing could serve, and fell back to letterboxing a speaker who was on screen
throughout.

Coverage is then measured against frames containing a *subject-sized* face
rather than any face at all, so overlays cannot dilute a speaker's presence.

### D34. A static frame is preferred over a moving one

If every subject's typical position fits inside one crop, the layout is a single
keyframe that never moves. Panning is the fallback for a subject who genuinely
travels, not the default.

The extent is trimmed at the 20th percentile rather than using the full range of
motion. Measured on a real talking-head clip: the untrimmed span was 784 px
against a 511 px usable crop, so the subject looked un-framable and the camera
kept correcting; the trimmed span was 478 px and fits comfortably. The deadzone
absorbs the tail.

This is what actually removed the shake. The deadzone (D25) reduced it; framing
the whole range at once eliminates it, because there is nothing left to correct.

### D35. Several subjects who fit in one crop share it

A sofa interview or a desk two-shot often has both people well inside a single
9:16 slice -- measured at 448 px inside a 608 px crop on real footage. That
fills the output frame *and* holds still, which beats both letterboxing the
whole picture and picking one person to follow.

### D36. A stacked two-shot requires the subjects to be on screen together

Two tracks that never appear in the same frame are not two people. They are one
person filmed from two camera setups, which is what cuts produce -- and stacking
those shows the same face twice, from two unrelated moments. A stack now
requires the two tracks to be co-present in at least 40% of frames.

This changed a real clip's verdict: it had been stacking two faces that came
from entirely different shots of the reacted-to footage.

### D37. Uncertainty resolves to keeping the whole frame

A 9:16 slice of a 16:9 frame keeps under a third of the width, so a wrong
framing decision does not degrade gracefully -- it discards most of the picture.
Every ambiguous case therefore falls back to `blurred_fit`.

The cost is asymmetric and that is the whole argument: letterboxing a clip that
could have been cropped wastes vertical space, while cropping the wrong region
loses the content outright. On feedback, the full-frame output was the one that
read as acceptable; the confidently-wrong crops were not.

**Known cost, not yet addressed:** `blurred_fit` leaves the content occupying
only about a third of the output height, with blurred bars above and below. A
moderate crop -- to 4:3 or 3:2 rather than all the way to 9:16 -- would fill more
of the frame while still keeping both subjects. That is the obvious next
improvement and has not been built.

### D38. Scene cuts are detected from a grid of tiles, not the whole frame

Cuts were compared using one hue/saturation histogram of the entire frame. That
histogram carries no spatial information, so a cut between a wide two-shot and a
close-up of the same person, in the same room, under the same lights, barely
moves it: measured at **0.752** correlation on a real cut, comfortably inside
the "no cut" range. A 35-second clip containing an obvious shot change was
reported as having **one** cut, at a b-roll insert, and none at the real one.

Comparing a 4x4 grid of per-tile luminance histograms scores that same cut at
**0.512**. Measured across all nine clip spans of the two real sources
(1733 sample pairs, 2026-09-22):

| source | median | minimum | pairs below 0.70 |
|---|---|---|---|
| edited podcast (5 spans) | 0.985-0.997 | -0.175 | 2-17 per span |
| unedited screen capture (4 spans) | 1.000 | **0.977** | **0** |

The static source never comes within 0.28 of the threshold, and the edited one
clears it by a wide margin at every real cut, so 0.70 is not a delicate number.

The comparison uses the **mean** tile correlation rather than the minimum. A
lower-third or a reaction inset appearing in a corner changes one tile
completely while the shot has not changed at all, and this source adds and
removes such overlays constantly.

### D39. Each shot is framed separately

A clip gets one framing per shot, not one framing per clip, and the framing
changes only where the source already cuts.

The failure that forced this, from user feedback with a screenshot: a 35-second
clip held a wide two-shot for 8.5 seconds and then a 27-second close-up. One
framing was chosen for the whole clip; the close-up supplied most of the face
samples, so the clip was cropped to a 608px column in the middle of the frame.
That column is right for the close-up and is the worst available choice for the
wide shot, where the two people sit at the far left and far right of the frame:
the output showed a wall, a book and two disembodied arms for eight seconds.

No single crop can serve both, because the source's editor deliberately changed
the composition. The fix is not a better global crop, it is to stop choosing
globally. On the same clip, per-shot framing now selects:

```
[0]  0.00- 8.55  blurred_fit   a subject-sized face appears in only 15% of frames
[1]  8.55-35.43  follow_crop   one subject spanning 473px fit inside a 608px crop
```

which shows both speakers and the b-roll insert for the wide shot, and the
close-up crop only where it belongs.

This also answers, more cheaply and more reliably than face tracking could, the
question of when to change who the frame is looking at: the source's editor
already decided, and their cuts are visible in the footage. Following them costs
one histogram comparison per sampled frame. Guessing at it from face positions
is what produced the swinging camera in D32.

**Rendering:** one FFmpeg pass, not one render per shot. The source is split,
each branch is trimmed to its shot with `setpts=PTS-STARTPTS`, given its own
layout chain, and the branches are rejoined with `concat`. Captions are burned
after the concat, on the reassembled timeline, so their timings need no
adjustment; a segment's crop trajectory *is* rebased, because its timestamps
restart at zero.

Each branch ends in `setsar=1,format=yuv420p`. This is not defensive: `concat`
refuses inputs whose sample aspect ratios differ, and the layouts round theirs
differently -- a `blurred_fit` branch came out at SAR 1216:1215 beside a
`follow_crop` branch at 10240:10239, which failed the render outright until each
branch was forced to square pixels individually.

A clip whose shots all reach the same framing collapses back to a plain
single-layout plan, so the segmented graph is only paid for when it is used.
