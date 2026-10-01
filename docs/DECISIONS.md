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

### D40. Framing is static; the panning camera is removed

Every shot gets one frame that holds its subject's whole range of movement, and
the frame changes only where the source cuts (D39). When that range is wider
than a 9:16 slice, the frame widens (`fit_crop`: the wider region over a blurred
copy of itself) rather than trimming or following.

The failure that forced this, reported with a screenshot: a close-up showing an
ear and the back of a head. The speaker leaned across the shot, his face
swinging over 700px; the static crop could not hold that, so the planner fell
back to the panning camera, which is deliberately slow and speed-limited so it
does not shake (D33). It trailed him the whole way. Measured: the face was
fully in the crop in **12 of 27** samples.

The audit that followed found the static crops were not clean either. They sized
themselves to a face range trimmed **20% at each end** -- chosen so more subjects
would squeeze into 9:16, with the panning camera meant to absorb the rest. For a
static crop nothing absorbs it. Across every cropped shot of the five clips the
face was fully in frame in **84%** of samples (419/497).

Now: 5% trim (enough to drop a stray detection), the face box padded for hair
and ears, and a frame that grows to fit. Same five clips: **99.0%** (491/496);
the remaining five samples are exactly the trimmed extremes.

**What it costs.** A 9:16 slice is now used only when the subject genuinely
fits one. Many close-ups come out as a `fit_crop` 700-980px wide, so the picture
fills 60-85% of the output height with a blur above and below, instead of
filling it edge to edge. That is the trade: every frame holds the whole face,
and some frames are less full. The constants are `EXTENT_TRIM`,
`HEAD_PADDING_RATIO` and `FRAME_MARGIN_RATIO` in `layouts.py`.

Removed with it: `smooth_trajectory`, `plan_follow_crop`, the `sendcmd`
trajectory files, and the `pan_smoothing`, `max_pan_speed` and `pan_deadzone`
settings. A config file still carrying those keys will be rejected, since
config is strict (`extra="forbid"`); delete the three lines.

### D41. Graphics beside the speaker are detected and kept in frame

Edited podcasts put subtitle bars, pop-up cards and circular photo inserts in
the half of the 16:9 frame a vertical crop discards. Reported with screenshots:
a Dumbo card cut in half at the frame edge, and a 1128px subtitle bar cut to its
middle 608px.

The first two detectors tried failed on the real footage, and why they failed
decided the design:

* *Sharp and pixel-static* also matches the set's in-focus posters.
* *Appears part-way through the shot* missed both reported cases. The card and
  the subtitle bar were each on screen for their **entire** shot, and the card
  is animated, so it is never pixel-static either.

So detection is by appearance, one detector per kind of graphic seen:

| kind | method | why this one |
|---|---|---|
| text | morphological gradient, Otsu, horizontal close, line-shaped components | subtitle bars are clean, high-contrast lines |
| card | closed convex contour, rectangular fill | the Dumbo card has a solid border |
| circle | Hough transform | circular inserts sit on busy backgrounds; their outlines merge with neighbouring edges and never close into a contour |

Text counts only at **25% of frame width or more**. The goal is sentences a
viewer must read; the same detector finds a book title on the set (13%), a shirt
logo (4%) and stadium signage in b-roll (20%). The signage was admitted at an
earlier 20% cut-off and widened a whole shot to 1834px for nothing, which is
where 25% came from; real subtitle lines measured 44-59%.

A graphic changes the framing only if it recurs: in at least 20% of a shot's
samples and no fewer than three. The animated card was found in 5 of 19.

A graphic present for only part of a long shot splits that shot at its
appearance and disappearance, so only that part is widened (smoothed first,
because detection is intermittent). Without this, a 27-second close-up with a
10-second insert was framed wide for all 27 seconds.

**Evidence, and its limit.** On 13 hand-labelled real frames: every insert
present was found and nothing was flagged on the 8 frames without one, posters
and heads included. One frame of the card was missed, which persistence covers.
Across all shots of the five clips, all 6 recurring graphics ended up inside
their frame. That is one source and 13 labelled frames -- enough to trust on
this kind of footage, not enough to claim it generalises. The likeliest misses
elsewhere are graphics that are neither text, rectangular cards nor circles.

### D42. The speaker is whoever the source shows

Reported: "the incorrect guy is framed because he's not talking." At that moment
the source itself is a close-up of the listener -- a reaction shot the original
editor chose -- and the speaker is not in the picture at all. No crop can frame
someone who is not in the frame, and cutting in footage of the speaker from
another moment would put his face on screen out of sync with his words.

This is recorded as a limit, not a bug. Following the source's cuts (D39) means
inheriting its reaction shots.

### D43. Cuts are placed on their exact frame

Cuts are detected between samples 0.2s apart, and were recorded at the sample
that saw them -- up to six frames after the real cut. Those frames of the new
shot were then rendered with the previous shot's framing: a visible flash at
the start of a shot. The frames in between are already decoded during the scan,
so on a detected cut they are compared and the cut is moved to the frame where
the picture changes, minus half a frame so the render's `trim` boundary falls
between two frames. Cost is only paid at cuts: at most five small histograms each.

### D44. When two people share a shot and do not fit one frame, frame the one talking

Reported: in clip 001's b-roll interview, the player answered ("monkey never
cramp...") while the frame held the interviewer. Unlike D42 the speaker was in
the picture, so this was fixable.

Two causes, both measured on that shot:

1. **The player was never a candidate.** His face measured 12.5% of frame width,
   just under the 13% line separating people from reaction-cam overlays
   (6.5-7.7%). A face now also counts if it is at least 9% wide and at least
   0.75x the size of a subject it shares the screen with: two similar faces on
   screen together are a two-shot, not an inset.
2. **Nothing asked who was talking.** The score is mouth change between samples
   divided by eye change, from fixed bands of the face box. The first version --
   mouth change alone, centred on YuNet's mouth landmarks -- picked the
   *interviewer* (0.71 vs 0.38) because he was in profile and profile landmarks
   jitter. Dividing by the eyes cancels head movement and jitter; box bands
   instead of landmarks removed the jitter at source.

| shot | who talks | talker | other |
|---|---|---|---|
| stadium interview | player | **1.10** | 0.64 |
| studio two-shot | Paul | **1.11** | 0.91 |

Two labelled shots is thin, so it only acts when the leader beats everyone by
1.2x; otherwise the layout shows both as before. It is consulted only when the
people do not fit one frame together -- if they fit, showing both is still right.

### D45. Caption words misheard by speech recognition are fixed from context

Reported: "I heard the *picture* from Los Angeles Dodgers" (a pitcher). Whisper
writes what things sound like; which homophone was meant is only clear from
context. Each clip's words go to the LLM with ~30 words of context either side.

The LLM is not trusted to rewrite. Every guard below was added because of a
proposal actually seen on the real clips:

| guard | real proposal it stopped |
|---|---|
| returns edits (index, original, replacement), never text; the original must match the word at that index | -- (a miscounted index would otherwise change the wrong word) |
| replacement must sound like the original: spelling or consonant-skeleton similarity >= 0.5, same opening sound (or spelling >= 0.8) | "river" -> "jungle"; "pickies" -> "chiggers" |
| a second, focused question per edit: sentence A vs B, answer "B" / "A" / "unsure"; only "B" applies | "lay bot flies on *there*" -> "their" (it was right); "rode" -> "rowed" (text cannot settle it) |
| at most 10% of a clip's words (min 2) may change, or nothing applies | -- |

The sound-alike rule was set on 20 real homophone pairs against 15 meaning
changes: all 20 pass, all 15 fail, including the rhyme "pitcher" -> "catcher"
that similarity alone let through (hence the opening-sound rule).

**Model choice, measured.** The default free model (gemini-flash-lite-latest,
0.7s per call) fixes "picture" -> "pitcher" but misses "clap" -> "cramp", which
needs knowing bananas prevent cramps. gemini-3-flash-preview catches both -- but
on the free tier it took ~87s per call or returned 503/429, and a 5-clip run went
from 3m20s to 22m. So the stronger model is opt-in (`llm.correction_model`), tried
first with no retries and a timeout (`llm.correction_timeout`, 30s) before
falling back. With the default the run takes 3m43s, 23s more than without
correction.

The Gemini backend's `timeout` had never been passed to the SDK, so a call could
wait indefinitely; it is now, raised to the API's 10s minimum (a shorter deadline
is rejected with a 400, verified).

Every applied fix is listed per clip in `report.md` and in the manifest's
`caption_fixes` column, so a wrong one can be found and reverted.

### D46. A caption fix must be heard in the audio, not just make sense

Superseded the text-only verification in D45. On review the user ruled two fixes
the stronger model made wrong: "leishmaniasis from the *bot*" -> "bite" and
"the *pickies*" -> "piques". Both sound like the original, so the sound-alike
check passed them, and the model confirmed them because they are more
*accurate*: its own reason for "bite" was that leishmaniasis comes from sand
flies, not bot flies. Captions must show what was said, not what is true.

Five text-only judges were then tried on the real proposals, and none held up:

| judge | failure on the real proposals |
|---|---|
| A-vs-B comparison | preferred the accurate word ("bite", "piques") |
| A-vs-B, "caption what was said, not what is true" | flash-lite still accepted "bite" 3/3 |
| blind "could A have been said?" | "bot" and "pickies" flipped between runs |
| blind, 3 unanimous votes | "bite" 3/3 when batched with other items |
| "why would it change?" classifier | "pickies" swung from "unusual" to applied when framed differently |

A text judge cannot tell *misheard* from *misspoken*: both look wrong on the page.
The audio can. Whisper re-transcribes 2.0s before to 1.5s after the word, nudged
toward the replacement; the fix applies only if the replacement took the
original's place. Stable across repeated runs on the real cases: allows
picture -> pitcher and clap -> cramp; vetoes bot -> bite, there -> their,
does -> do, through -> to and rode -> rowed. It cannot separate words that sound
nearly identical ("pickies"/"piques"), so fixes the user rules wrong go in
`llm.rejected_caption_fixes` and are never applied again.

Without audio, nothing is applied.

**The stronger model is on.** Verified free: its 429 names the quota
`generate_content_free_tier_requests` /
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value 20 per day for
gemini-3-flash-preview. A free-tier key is refused past its quota, not billed.
One request per clip; past the quota, or when the model is overloaded, the fast
model proposes instead.

### D47. Test sources chosen by Claude must be Creative Commons, from the owner

When the user asked Claude to research and pick a test video, the constraint
from BUILD_BRIEF.md ("authorized source material only") was applied strictly:
YouTube's CC-BY licence *as set by the show's own channel*, verified from the
video's metadata. A CC label on a re-upload grants nothing, and the search
results were full of those. The attribution the licence requires is burned
into every clip (`required_credit_text`, `burn_credit_in_video`).

### D48. A drop about what is said applies to every window that contains it

See VERIFIED.md 2026-09-22 (late). Sponsor-read and high-policy-risk drops spread
to candidates sharing at least half their duration with a dropped one.
"Needs prior context" does not spread: a longer window can supply the context.

### D49. Scripted TV: frame everyone, and never let a clip leave its scene

Reported on FX clips: the camera cut to the wrong speaker, split screens showed
one shot duplicated, the speaker was sometimes out of frame, and clips opened or
closed on a flash of another scene or stopped mid-exchange.

**Framing.** Speaker detection was built for podcasts, where one person talks at
a time to a fixed camera. In a sitcom the editor already cuts to whoever
matters, several people react in the same shot, and a two-shot stacked into
two panes repeats the same picture. With `campaign.scripted`, each shot is
framed to keep every person who is a real part of it (face at least 5% of the
frame width, seen in at least 15% of samples), widening into `fit_crop` when
they do not fit a 9:16 crop. No stacks, no speaker following. Faces falling
outside the frame dropped from up to 20% of samples to 5% or less.

**Scenes.** A `scenes` stage runs for scripted sources. Boundaries are the union
of two signals, each required to land on a real camera cut:

- every fade to black;
- the LLM splitting the transcript into scenes, snapped to the most dissimilar
  cut near its first line -- kept only if that cut is itself among the most
  dissimilar or falls in a pause of 1s or more. The LLM also marks story
  beats inside one room; three of those cut through a running exchange and
  produced clips opening on "Do it!" or ending 0.06s before the reply;
- a cut among the 5% most visually dissimilar in the episode (best colour
  match between any shot overlapping the 8s either side) with 2.5s or more of
  silence across it;
- a cut unlike anything within 8s either side (likeness below 0.7), in any
  real pause (0.15s or more). Every such cut in the five FX sources was
  checked by eye and changes place. A line running across the cut vetoes it:
  one episode opens on a one-second office flash under the friends at home
  asking "What did you say?" -- splitting there started the clip mid-word.

Counting only shots that *start* within 8s was a bug: a return from a 4s
insert (someone elsewhere, then the library conversation again) read as a
change of place because the first library shot began earlier, and the clip
ended just before the reply "Of course."

Neither alone was good enough on episode 201: the LLM's split was unstable
between runs and missed the move from the dinner table to the kitchen at 8:07;
colour alone flagged changes of camera angle mid-conversation. The rule errs
towards splitting, because a missed boundary puts another scene in a clip,
while an extra one only removes some candidate windows.

Candidates must lie inside one scene. They start at the scene's opening
or after a pause of at least 0.7s, and end at the scene's close or before a
pause of at least 1.2s. At 0.7s, clips ended on a line with the reply under a
second later ("What is it doing?" / "No, don't eat that."). At a scene's own edges they snap to its camera cuts. Boundary
refinement is clamped to the scene afterwards, and a clip that opens or closes
with its scene keeps the scene's cut: refinement had trimmed a 2.3s wordless
opening, so a clip began mid-line.

### D50. One performance log; watch-through, not views, is what `learn` optimises

Section 14.2 asks `clipper learn` to correlate sub-scores with views at 24h and
7d. On the first real posts (a brand-new TikTok account, FX Adults S2) views
mostly measured the account: TikTok shows a new account's posts to a small
test audience whatever the clip. Average watch time over clip length
("watch-through") measures what the people who did see a clip did with it, so
it is the primary target; views are still logged and reported.

The per-source `performance.csv` templates were written once and never
refreshed, so after a re-run they listed clips that no longer existed. They are
replaced by one log, `data/performance.xlsx`, that every run appends its clips
to (never rewriting a row the user has filled in). The manifest now records
`candidate_id`, which joins a posted clip back to every score behind it. It is an Excel
file rather than CSV so it keeps column widths and a frozen header, and it
leads with the caption as posted -- TikTok Studio lists posts by caption, so the
user fills it in from Studio alone. It reads what Studio shows as typed ("9.8s",
"12%", "1.2K"). If the file is open in Excel when a run ends, the run's clips
wait in a side file and are added next time instead of failing the run.

Unchanged from the brief: under 20 clips with results, `learn` only describes.
With more, it reports Spearman correlations with bootstrap intervals and
proposes signal weights moved 20% of the way toward each signal's share of
positive correlation with watch-through; `--apply` writes them (keeping the
YAML's comments) and appends `config/weights_history.json`.

### D51. Post stats come from TikTok's official Display API, never from Studio pages

Copying numbers from TikTok Studio by hand is tedious, but scraping Studio (or
driving a browser through it) breaks TikTok's terms and puts the account at
risk. TikTok Studio's own export covers only account-wide daily totals, not
individual posts. The Display API is the sanctioned route: `clipper tiktok
login` runs TikTok's desktop OAuth (localhost redirect, PKCE with the hex
SHA-256 challenge TikTok specifies), with the app in sandbox mode so no app
review is needed for the user's own account. `clipper tiktok sync` then fills
the performance log's views, likes, comments and shares, read-only.

It does not provide watch time or completion; those need the Business API,
which needs a Business account and loses non-commercial sounds, a real reach
cost for a clips account. So `avg_watch_s` and `watched_full_pct` stay manual.
View snapshots are only taken inside their window (24h: 1-3 days after
posting; 7d: 7-10 days) so a late sync never mislabels an older count.

The rest (watch time, completion, saves, new followers, and where most viewers
stopped) comes from `clipper tiktok collect`: the user opens a post's
analytics in TikTok Studio and copies the page (Ctrl+A, Ctrl+C); the command
watches the clipboard only while it runs, ignores anything that is not a
Studio analytics page, and parses the page's fixed layout -- no LLM needed.
A post with no views gets no watch time recorded ("0s" there is not a result).
The first real page showed most viewers leaving at 0:01 on a clip that opens
on a wordless establishing shot, which is what `drop_off_s` is there to test.

### D52. Scripted clips are built for the first second

The first three FX posts with views all lost most viewers at 0:01 (TikTok
Studio: "Most viewers stopped watching at 0:01"), with 8-31% average
watch-through. Two opened on a 16:9 picture over blurred bars (faces a third
of their full-screen size), one on a wordless establishing shot, none with text
on screen. A research pass on short-form retention (TikTok's creative guidance:
land the proposition in the first 3 seconds; TikTok says finishing a video is
weighted strongly; larger third-party datasets on length) pointed the same way.
For `scripted` campaigns:

- **Open on dialogue.** At most 0.5s of silence before the first word, even
  where the scene's own cut starts earlier (replacing D49's "keep the scene's
  cut" at the start). End a beat after the last line: about 1s of reaction held
  (stopping 0.15s short of the next word), never more than 1.5s of silence.
- **Fill the screen for the first 3 seconds.** The opening shot frames the
  speaker (`clear_talker`, else the most prominent face) full-screen, widening
  to at most 4:5 (70% of the screen's height) for whoever is beside them. A long
  wide first shot is split at 3s so only its opening is tight; later shots
  keep everyone in frame as before.
- **A factual hook line on screen for 3 seconds**, 3-8 words: the situation,
  stakes, an identity or a question, never an outcome the clip doesn't deliver
  (prompt v2). Per campaign (`hook_overlay`); briefs may count added text as
  modifying their content.
- **20-45s by default, 90s at most**: candidates past 45s are scored down 1%
  per 2s over (at most 25%), so long clips win only when they score clearly
  better.
- **Scene choice**: the rubric favours conflict, awkwardness, one-liners and
  relatable situations that read without knowing the show.
- **Safe zone**: captions keep above y=1520 (bottom margin 400) and clear of
  the right-hand button rail (right margin 180).

Most of the numbers are practitioner consensus, not measured (the research
labels them weak). So each clip's opening framing, silent lead-in and hook go
into the performance log, and `clipper learn` compares posts with and without
each change on watch-through and the drop-off second.

### D53. Night scenes: hand-picked ranges, dark-cut detection, close-ups fill the screen

The Chad Powers S2 brief names its moments, and the romance plays out in looks
in dark scenes. Measured on the official Ep 4 and Ep 6 footage:

- **Selection can't see a wordless scene.** Candidates are built from dialogue;
  the Ep 4 ending (40s without a line) never became one, and with the brief's
  focus applied nothing scored above 2.6. `clipper cut <source> -c <campaign>
  -r 25:11.9-26:13.4 ...` renders exact ranges -- no refinement, scene clamp or
  silence trim -- through the same framing, captions, QA and manifest.
  `--first N` continues the campaign's hook and caption rotation across runs.
- **Dark shots hid their cuts.** Tile histograms of a night frame are one
  spike near black, so different shots correlate at 0.95-1.00: 2 of ~12 cuts
  found. A second test compares z-normalised 32x18 thumbnails (a dip under
  0.40, or under 0.65 with a stretched-contrast histogram or a 12%-of-width face
  jump agreeing), and only counts a lone dip between two steady samples --
  handheld crowd footage dips every sample. Ep 4's ending: 2 -> 10 cuts; a
  podcast and a bright banquet: unchanged (+1 real cut).
- **Merged shots letterboxed.** Scripted campaigns allow 32 separately framed
  shots (was 8); the source decodes in order, so branches cost no buffering.
- **Close-ups widened to keep hair.** One face at least half a 9:16 slice wide
  that stays put (centre travel under 35% of the slice) is cropped full-screen.
  A moving podcast host is still held by a wider frame.
- **Faceless shots** in scripted campaigns get the opening's centred 4:5 frame
  (70% of the screen) instead of the whole frame (32%); two small faces on
  screen throughout (down to 3% of the width) now count as people.
- **QA called night pictures black.** blackdetect's pixel threshold is 0.05
  (was ffmpeg's 0.10): true black and fades still fail.
- **The focus text made the LLM call clips ads** ("This is a paid campaign"):
  12 of 60 dropped as sponsor reads. The focus now says it is never a reason to
  set is_sponsor_or_ad (prompt v4).

### D54. Captions readable at a glance; hand-picked ranges re-transcribed

User report on the Chad Powers clips: captions were on screen too briefly to
read ("probably double the time"), and the Ep 6 field scene missed whole lines.

- **Two lines, up to 6 words** (was one line of 3). In fast dialogue a caption
  flipped about once a second; two lines of 18 characters roughly doubles that.
- **Held into the pause after it**: at least twice as long as it took to say
  and at least 1.2s, never over the next caption or past the clip's end.
- **`clipper cut` re-transcribes each range on its own** and uses that when it
  heard more. The whole-episode pass had 10 words of the field scene's 32 (quiet
  dialogue under crowd noise); the 36 seconds alone came out whole. VAD stays on
  so long wordless looks are not filled with hallucinated words, and nothing is
  conditioned on earlier text.

### D55. Instagram Reels through Instagram's official API

With a professional (Creator) account, Instagram's API with Instagram Login
gives each Reel's permalink and insights -- no Facebook Page, no scraping.
`clipper instagram login` takes a token generated in the Meta App Dashboard
(entered at a hidden prompt, stored under data/instagram/, extended to a fresh
60 days once it is 30 days old); `clipper instagram sync` and the existing
6-hourly scheduled task fill the log.

- **One row per Reel**, platform "instagram", copied from the clip's row the
  first time a Reel's caption matches and found by media id after that. The
  clip's own row stays its TikTok row; TikTok's sync and the Studio collector
  skip Instagram rows.
- Instagram reports average watch time itself (ms, stored as seconds) and
  `reels_skip_rate`, the share of plays skipped within 3 seconds -- the hook's
  own number -- in a new `skip_rate_pct` column.
- `clipper links` lists both platforms (`--platform` to pick one).
- Read-only: nothing is published through the API (no auto-posting stands).

### D56. The Control Center, the clip library, and posting under a toggle

The user's goal is one control center for research, clipping and posting, eventually
a standalone app others can use; finding clips across run folders, "-ready"
folders and file names like "POSTED1 bench..." was the daily pain.

- **Library**: every accepted clip is copied to `data/library/<campaign>/` by the
  run itself (`manifest.write_outputs` -> `studio.library.register`) and recorded
  in `data/clipper.db` (SQLite). Run folders moved to `data/work/runs/` -- working
  files. The old ready folders were imported (`clipper library import`) with
  captions from POSTING.md / captions.txt and posted state from "POSTED" names,
  then recycled; 32 clips.
- **`clipper studio`** (desktop shortcut "Clipper Control Center") serves a local
  page at 127.0.0.1:8765: campaigns with their rules, each clip with a playable
  preview, hook, caption (copy button), status (ready / posted / submitted /
  skipped), notes, and its TikTok and Instagram links and stats; "Copy all
  links"; "Sync now"; archive. Post stats stay in performance.xlsx (the syncs'
  store) and are joined on (campaign, source_id, clip_id); a clip with a live
  post shows as posted.
- **Stack**: FastAPI + uvicorn over a static page (no build step), so the same
  API can later sit behind a hosted front end.
- **BUILD_BRIEF amended by the user**: posting through official APIs only,
  auto-post a setting (global + per campaign) that is off by default, several
  accounts allowed. The toggle is stored now; posting itself is the next step.

### D57. Searchable descriptions under the caption line

TikTok advises that longer descriptions help posts get found. Campaigns can set
`long_description` (with `description_context` and `description_keywords`); the
caption becomes three paragraphs -- the caption line with any #ad first, so the
disclosure shows before "more"; 2-3 sentences on what happens; the hashtags.
Written at render time from the clip's corrected words (campaign/description.py),
or for existing unposted clips with `clipper library describe <campaign>`.
Posted clips keep their caption (what is live, and what the syncs match on).

Measured on the five unposted Chad Powers clips: gemini-flash-lite invented
settings and secrets ("during practice", "keep it hidden from the team") and
guessed speakers -- the transcript has no speaker labels -- one description
had "I love you" said by the wrong person. A stricter prompt (d2) cut the
inventions but not the guessed speakers. Those five were written by hand
from the verified frames and dialogue; automatic descriptions need a look in
the Control Center before posting.

### D58. The Control Center rebuilt to the UX research spec

The user commissioned a research report on app UX (usability, visual design,
motion, habit, performance, accessibility, stack) and asked for it to be
followed. Built now, for everything that exists today:

- **Stack** as specified: React 19 + Vite + TypeScript, Tailwind v4 reading the
  report's OKLCH design tokens (dark default, light and system themes, 4px
  spacing, motion durations/easings, reduced motion), Radix primitives,
  TanStack Query + Router, Zustand, cmdk, sonner, lucide. Types are generated
  from FastAPI's OpenAPI schema (`npm run gen:api`), so a backend rename breaks
  the build. Source in `studio/web`; its build in `studio/static` is committed,
  so running Clipper needs no Node. Initial JS 186 KB gzipped (budget 200).
- **Pipeline sidebar** (Home, Campaigns, Clips, Queue, Submissions, Stats;
  Accounts, Settings) with live count badges, collapsible with `[`; bottom tabs
  on phones; deep links with filters in the URL.
- **Keyboard first**: Ctrl+K palette (every page, action, campaign and clip,
  with shortcuts shown; whole-word matching, titles ranked over captions), G+key
  navigation, J/K/Enter/C/L/P/X on clip lists and in the clip sheet, `?` sheet,
  single-key shortcuts off while typing and switchable off (WCAG 2.1.4).
- **Undo, not confirm**: status and submitted changes are optimistic with a
  5-second Undo toast.
- **Honest numbers**: metrics TikTok's API lacks show "n/a" with the reason,
  never 0; Instagram posts under 48 h are "settling", and their 0s watch time /
  0% skip rate read as not yet reported. Estimated earnings (views/1000 x the
  campaign's `reward_per_1k_usd`, after `min_payout_usd`, capped at
  `max_payout_usd`) lead, in the money colour; each post shows x-median once a
  platform+campaign has 3 posts.
- **Live**: the server syncs TikTok + Instagram every 15 minutes while open
  (setting), snapshots every post's numbers into SQLite for later growth
  charts, and pushes events over SSE (one queue per page) so pages refresh
  themselves; "Synced x ago" in the top bar; an offline banner.
- **"Since you were last here"** counts from the end of the previous session
  (last activity before a 30-minute gap), not from the last page load.
- **User requests**: "Copy all links" is gone -- every post has its own
  copy-link button on cards, in the sheet, in Submissions and in Stats;
  submissions are tracked per link.
- **Not built yet** (no backend for them): posting and its compliance form,
  the render triage queue, notifications, streaks. The report's TikTok caveat
  stands for posting: unaudited API clients post privately only, and a
  single-team upload tool is outside TikTok's intended use -- so manual posting
  and drafts must stay first-class paths.

### D59. Editing to the short-form retention playbook

The user commissioned a research report on what holds and loses short-form
viewers ([P]latform / [S]tudy / [O]pinion-labelled) and asked for cuts when
needed and edits when allowed. Built, by the report's own priorities:

- **Permission gate** (campaign/edits.py): edit classes -- captions, added
  text, internal cuts, re-edits, visual effects, audio additions, overlays --
  resolved per campaign: an explicit `edits:` setting, else anything the
  brief's `brief_rules` wording forbids ("do not alter", "no jump cuts",
  "keep the original audio", ...), else the content type's default. Container
  edits (crop, head/tail trim, loudness) are always allowed. Scripted scenes:
  no cuts inside a scene. Podcasts: pauses and fillers tightened.
- **Tightening** (render/tighten.py, podcasts): sentence pauses > 0.50 s cut to
  0.28 s, mid-sentence > 0.70 s to 0.30 s, the pause before the payoff kept up
  to 0.80 s; standalone um/uh and immediate repeats removed. Never within 40 ms
  of a word, never after the payoff starts, never in a gap that isn't quiet --
  measured on the audio (80th-percentile level 18 dB under speech): on 85 South,
  a live-audience show, every "pause" in a sampled clip was laughter at -18 to
  -29 dB and was kept; true dead air read -45 dB. Budgets: <= 20% removed,
  cuts >= 2.5 s apart, pieces >= 0.6 s, never under the campaign minimum.
  Pieces are joined into an intermediate file (12 ms audio fades, every other
  piece punched in 10% to hide the jump cut when effects are allowed), and
  framing, captions and QA run on that timeline. Measured: a 40 s stretch lost
  2.9 s of dead air; QA passed.
- **Openings**: speech within 0.15 s of the first frame (was 0.5 s, D52); a
  black first frame is skipped (luma < 0.12), never past the first word.
- **Loudness**: two-pass loudnorm to -14 LUFS, true peak -1.5 dBTP, LRA 8 --
  for every clip, since normalising is a container edit (it used to be off for
  "keep original audio" briefs). Measured on a render: peak -1.5, LRA 4.4.
- **Safe zones**: captions end by y=1240 and stay within x 300-780 (clear of
  TikTok's text and button rail -- they had sat at y=1520, under the
  description); hook band from y=300, white with a black stroke, 69 px, up for
  max(2.5 s, 0.3 s/word + 0.8 s), at most 3.5 s. A caption page that would cover
  a face moves below the chin (inside the box) or to the top band; in extreme
  close-ups where neither is free it keeps the lower third.
- **Caption pages**: sentence case (hook stays upper); never ending on a weak
  word ("the", "to"); at most 3 words when speech runs over 3.3 words/s; no page
  up under 0.5 s; 2-frame gap between pages; active word at 105% (was 108%).
- **Dark footage**: lifted with gamma 1.12 (power 0.89, keeps blacks) when mean
  luma < 0.22 and the 95th percentile < 0.55, where effects are allowed.

Not built yet (need signals the tool doesn't have): speaker-aware turn gaps and
L-cuts, split-screen for rapid turns, punch-ins on emphasis, laughter/reaction
detection beyond loudness, watermark and duplicate detection, caption contrast
boxes, re-hook checks, and the paired A/B harness (the report's thresholds are
mostly [O] starting values for it).

### D60. Downloads, delete with undo, and making clips from the Control Center

- **Show in folder** passed Explorer `/select,<path>` as one list argument; for
  names with spaces or brackets ("5b laundry room - ... (SHORT, safer).mp4")
  Python's quoting broke it and Explorer opened Documents. It now gets one
  command string with the path in quotes, verified selecting that file.
- **Download** (cards, Queue, clip sheet, key D): `/media/{id}?download=true`
  serves the clip as "<title> - <campaign>.mp4".
- **Delete** (cards, Queue, sheet, Delete key): instant, with a 5-second Undo
  (research: undo over confirm). The clip goes to a trash -- hidden, file
  kept -- and after 30 days the file goes to the Windows Recycle Bin (the
  user's rule: nothing is deleted permanently), checked at server start.
- **New clips** page: pick a campaign, drop in the footage (streamed to
  data/downloads, any size, with a progress bar) or pick a video already in
  Downloads, choose how many clips, and a background job runs the normal
  `runner.run` -- one at a time, since transcription holds the GPU. Its log
  lines become stages and a percentage, pushed live over SSE; the clips land
  in the library and on the campaign's page. Hand-picked ranges (`clipper
  cut`) and creating a campaign from a pasted brief are not in the page yet.

## D61 -- Self-service Control Center; clip count, scores and learning from ratings (2026-09-29)

**Campaigns from the page.** New campaign / Edit campaign write campaigns/*.yaml
through `CampaignConfig` (campaign/editor.py); the previous file is kept in
campaigns/.history/. A pasted brief fills the form via the configured LLM, which
is told to leave out passwords and footage links (and links to Drive, Dropbox,
Discord... are dropped even if it doesn't). Campaigns gained `title` (renameable)
and `marketplace`; `name` stays the id and library folder.

**Navigation.** Queue and Submissions became Clips filters (Ready to post, To
submit); a clip is marked submitted as a whole (every post link), with undo. A
post found after that reopens it. Home is Dashboard.

**New users.** Keys (Gemini, TikTok app) are set from the page into .env and
applied at once; the page learns only whether a key is set. Accounts are one
token file each (data/<platform>/accounts/), several per platform; TikTok's
consent page opens from the page. Settings runs `clipper doctor`'s checks. The
Dashboard shows a four-step checklist until done. Non-GET requests carrying a
foreign Origin are refused (any open website could otherwise post to 127.0.0.1).

**How many clips.** "Let Clipper decide" passes the campaign's
`max_clips_per_source` as the cap and lets the absolute quality gate
(`min_llm_total`) decide -- the gate already expresses "this video has N clips
worth making". "Set a number" is the old top-N; "I'll pick them" is `clipper cut`.

**Scores.** Each clip keeps the raw 0-10 LLM total (absolute, comparable across
videos -- unlike the per-video percentile composite), the six rubric parts and
its rank among the video's moments. Older clips were backfilled from their
work/ scoring files via the performance log's candidate ids.

**Learning.** 1-5 ratings with reason tags. With 8+ rated, scored clips the
rubric weights move toward each part's rank correlation with the ratings,
blended with the defaults at n/(n+20), capped at 60%; weights only reweight
cached rubric scores, so no model calls repeat. With 3+ ratings the scoring
prompt gets a "taste" block (liked/disliked examples, common reasons), placed
after and explicitly weaker than the campaign focus. The Learning page shows
whether the score agrees with ratings and views (Spearman), by score band. A
setting turns learning off. Tests never learn from the real library (conftest).

## D62 -- Research section: chat, niche radar, saved; plan tiers (2026-09-29)

The user chose "chat + niche radar" over a chat alone or a dashboard alone.

**Ask.** Gemini (flash, falling back to flash-lite) with function calling over
Clipper's own data (clips with scores, ratings and post stats; campaigns;
niches; footage) and two outside sources: web search and top YouTube Shorts.
Web results are numbered and cited inline; answers are Markdown.

**Web search is Tavily, not Gemini grounding.** Google Search grounding
returned 429 on every model for the user's free Gemini key (2026-09-29), while
plain calls worked -- free keys have no grounding quota. Tavily is built for
AI tools and free for 1,000 searches a month; YouTube Data API v3 (free) gives
the week's most-viewed Shorts. Both are optional keys with in-page setup;
without them the chat still answers from the user's data and the model's
knowledge, and says what the key would add. No site is scraped.

**Niches.** A brief per niche: top Shorts, two recent web searches, and the
configured LLM's reading (summary, topics citing sources, hook lines). Refreshed
on demand and once a day while the Control Center runs (one niche a minute at
most). `live` false marks a brief made without either source.

**Tiers.** The user sees Research as a paid tier and actions as the top tier,
"further down the line". studio/plans.py: Free < Research (the section) < Pro
(the chat may propose changes -- hook lines, a clip job, a new campaign from a
brief -- run only when the user presses Confirm, once). Enforced server-side
(402); a local install is Pro; Settings has a plan preview. No billing.

## D63 -- After the market research: slimmer Research, dispute packs, fast link capture (2026-09-29)

The competitor report (docs/research/, requested with competitor-research-prompt.md)
found the gap is the campaign workflow, not the edit: the most-cited clipper loss
is rejection after views accrue; Whop lets brands reject only for failing a
written requirement; a 30-minute submission window is reported (unverified).
It judged niche briefs, chat actions and a web-wide campaign finder as bloat
(Apify scrapers already aggregate campaigns). The user chose to slim Research
and build the dispute pack and fast link capture now; Drive/Dropbox import,
honest-results and duplicate checks wait.

- **Research slimmed.** Niches, Saved, chat actions and YouTube lookups removed.
  The Ask chat (own data + optional Tavily web search, answers only) is a panel
  from the top bar (I), not a page. Plans: Research = Ask; Pro = later auto-post.
- **Find campaigns** (Campaigns page): the watcher now records every campaign it
  finds (found_campaigns), and "Check a campaign" reads a pasted brief and
  shows plain fit checks against the user's own record (connected platforms,
  median views -> earnings a post, footage they've clipped, deadline, rules to
  check themselves). "Add campaign" opens the form already filled.
- **Proof for disputes.** Each clip keeps an evidence snapshot when it's made:
  the campaign's rules (full YAML), the compliance and QA checks, the recorded
  permission. The sync now keeps each post's caption as actually posted, which
  is re-checked against the saved rules. "Download proof pack" zips proof.html,
  brief.yaml, evidence.json, stats.csv and the clip. Older clips get a snapshot
  marked late, and the pack says so.
- **Fast link capture.** Mark posted -> syncs every 2 minutes for 30 minutes
  until the post is found. A post link can be pasted by hand (TikTok gets its
  video id for the next sync; Instagram is adopted by URL). "Copy link & submit"
  copies the post link and opens the campaign page.

Still not self-service for other users: the campaign watcher (email forwarding +
app password). A hosted Clipper would give each user a forwarding address.

## D64 -- Footage from shared links, run results, duplicate check (2026-09-29)

From the D63 research list; scheduling stays on hold (platform audits).

- **Import from a link** (New clips > Footage): public Google Drive files and
  folders (the folder's embeddable view lists its videos; the user picks which),
  Dropbox files and folders (dl=1; a folder arrives as a zip whose videos are
  kept), or a direct video URL. Downloads run one at a time in the background
  with progress. HTML where a file was expected means the link is private or
  over its limit, and says so. WeTransfer, Frame.io and password-protected links
  are refused with "download it by hand". Each file's link is remembered and
  goes into its clips' proof packs. Tested with mocked responses only; the Drive
  folder page format is unverified against a live folder.
- **Run results**: every job keeps a report (select/report.py) -- moments found,
  how many cleared the bar, why the rest were left out in plain words, and the
  five closest calls with a "Make it anyway" button (a hand-picked job on that
  range). Finished jobs are kept in the database, so results survive restarts.
- **Duplicate check** (studio/duplicates.py): a clip that repeats an
  already-posted one -- same source with half the shorter clip's time in common,
  or most of the shorter clip's meaningful words (a re-download has a new
  source id) -- shows a warning with the accounts it's on. Clips keep up to
  1,500 characters of their words for this; clips without words (imports) are
  transcribed once in the background with Whisper small. On the user's library
  it found three real repeats (FX Adults imports of scenes already posted).

## D65 -- Campaign alerts from Discord, not email (2026-09-30)

The email watcher (D-watch) depends on forwarding rules the user found
unreliable, and Whop sends no new-campaign emails at all. Checked before
choosing: Whop's API has no Content Rewards listing (Bounties are fixed-price
tasks); contentrewards.com/discover has no feed, API or alerts; Whop's terms
(section 5) forbid "accessing or monitoring any material or information from
the Service using any automated means, including robots, spiders, or scrapers"
without written consent -- so third-party scrapers (Apify) stay out, for the
user's payout account and for a product that may later want Whop as a partner.

- **Discord channel following.** Campaigns are announced in Discord
  announcement channels, which anyone can Follow into a server where they have
  Manage Webhooks. The user makes a private server, follows the campaign
  channels into it, and adds a bot they create (View Channel + Read Message
  History, Message Content intent on). Clipper reads only that server, with the
  user's bot, through Discord's REST API (watch/discord.py): polled, no gateway,
  no discord.py dependency.
- **Same judge, same list.** Each new post (40+ characters, embeds included) goes
  through the campaign judge (now `judge_text`, told it may be a Discord post);
  campaigns land in found_campaigns (`via` = discord) and fitting ones are pushed
  through ntfy once (a campaign already found by email or another post isn't
  pushed again). Without a campaign-site link the entry links to the post.
- **Nothing missed while off.** Discord keeps the messages; each channel
  remembers the last post read, and the next check reads everything after it
  (up to 500). A newly watched channel reads its last 25 posts but judges only
  the last 14 days'. A post the AI can't judge is retried twice more; its
  unusable answer is dropped from the LLM cache so the retry really asks again.
- **Checks** every 5 minutes while the Control Center runs (auto-sync mode), and
  on "Check now".
- **Self-service setup** on Campaigns > Find campaigns: five steps (make the bot,
  private server with an invite link Clipper builds, follow channels, pick
  channels from the bot's view, say what fits you). The fit profile and minimum
  rate are now settings (default: config's watch profile), and the ntfy topic
  and bot token are keys set from the page. A hosted Clipper would run one
  company bot and server instead of one per user.
- The email watcher still works and still records into the same list; it can be
  retired once the user's Discord alerts are running.

## D66 -- Whop feed through the official API (experiment); pruning (2026-09-30)

The Content Rewards Discord's "discover campaigns" channel only links to Whop,
so Discord alerts can't cover Content Rewards. Its campaigns are posted in the
Content Rewards Whop's Home feed by @contentrewardsbot. Whop's API lists a
forum/feed's posts (`GET /api/v1/forum_posts?experience_id=...`, `forum:read`)
and accepts a user OAuth token, whose reach is what that user can see -- so a
member signing in to their own Whop app may read it. The docs don't say
whether member tokens get `forum:read`, or whether that Home feed is a forum
experience; `clipper whop login / find / feed` (watch/whop.py) tests it. If it
works it joins the alerts beside Discord; if not, the code is removed.

Pruned, at the user's request (and from now on, as features are replaced):
unused functions and classes found by vulture (and confirmed by search) and
their tests; the per-post "submitted" endpoint and hook (clips carry it now);
the Apps Script email watcher (never installed); the Playwright screenshot
scripts and two unused front-end packages.

## D67 -- The "watch it" pass: the model watches the shortlist (2026-09-30)

Every score came from the transcript, so a joke carried by a look was invisible:
in Adults S2 ep. 1 a guest asks for "black" beans and the host's eyes go to the
one Black man in the room -- on paper a joke about a word, on screen the joke.
Checked first on the user's free Gemini key: gemini-flash-lite-latest watched the
33s finished clip (low media resolution, ~3,100 input tokens, 8s) and named the
look and the table freezing. gemini-flash-latest answered 503 (overloaded).

- After transcript scoring, the 12 best distinct moments (overlapping windows
  share one verdict) are cut small -- 360p, 2 fps, mono, 1.5s margin each side --
  and watched with their transcript, three at a time. The model re-scores the
  same six-part rubric as a viewer, rates how much the picture adds (0-10), says
  what it sees that the words miss, and may offer a hook line.
- Selection gates and ranks on the blend: half the read total, half the watched
  one (`SignalValues.rubric_total`). Unwatched candidates keep the read total.
  The watched hook replaces the transcript's when the picture adds 6+/10 and the
  campaign has no hook texts of its own.
- Cached per source and window; any failure leaves the transcript score (never a
  failed run). `llm.watch_video` turns it off, `watch_shortlist` sizes it,
  `watch_model` picks another model. Backends without video (Ollama, Claude
  here) skip it.
- Measured on an 85 South podcast episode: 8 calls, ~35k input tokens, 62s
  (6 minutes before parallel calls and de-duplication); picture scores 2-5 as a
  podcast should get; one moment rose from 20th to 3rd on the couch's reactions.
- The clip sheet shows read vs watched and "What the AI saw". Not yet built:
  candidates for moments with no dialogue (the Chad Powers ep. 4 field scene),
  and a before/after on the FX and Chad Powers episodes, whose source files were
  cleaned up after clipping and need re-adding.

## D68 -- Moments with no dialogue become candidates (2026-09-30)

Candidates were built from sentences, dropped when mostly silent, and scored
from their words, so a scene that plays out in looks could never be picked --
the Chad Powers brief's Episode 4 field scene (38s between "I promise you I
will" and "See you tomorrow, Coach") was invisible, and scene detection had
left that stretch out of every scene too.

- Each wordless stretch of 10s+ between two lines (not before the first or
  after the last: titles and credits) gets one "quiet" window: the exchange
  leading into it, back to a 3s pause and at most 15s, through the line after it
  if within 8s; kept inside its scene and the length bounds; longest stretches
  first, at most 6 a source.
- Quiet windows skip the transcript scorer and the silence filter, are always
  watched (D67), and are dropped if they couldn't be watched. Selected ones are
  cut as found: refinement would snap them to sentences and trim the silence.
- On the saved Chad Powers ep. 4 transcript the field scene is the first quiet
  window (25:12-26:01, 49s, "Tell me to walk away..." through the look);
  Adults ep. 1, all dialogue, gets none. Not yet run end to end: the episode
  files need re-adding for the before/after (D67).

## D69 -- Content Rewards campaigns from Whop feeds, through Whop's API (2026-09-30)

The D66 experiment worked. The user made a Whop app (website type; redirect
http://localhost:3456/callback; permissions oauth:token_exchange, forum:read,
member:basic:read, company:basic:read) and signed in with it. Findings:

- Whop's app "scopes" dialog is only a reference; nothing there is saved. The
  sign-in must use the app's API key as the client secret, and that key needs
  the oauth:token_exchange permission ("client_secret lacks oauth:token_exchange
  permission" otherwise; with no secret, "client_secret is required").
- A user token reads members-only feeds only while the membership is live: both
  of the user's memberships had lapsed ("You do not have access to read these
  posts") until they rejoined.
- @contentrewardsbot posts each new campaign as a forum post with a fixed shape
  (title; Budget; CPM; Platforms; Campaign link to contentrewards.com/discover).
  The Content Rewards community's "New Campaigns" feed carried 50 posts in 2.3
  days (~22 a day) -- in effect every new campaign; Whop Clips' "Content Rewards
  New Campaigns" feed carries a hand-picked few (50 in a month). Content Rewards
  has two identical "New Campaigns" feeds; the picker labels the second "(2)".

Built: Whop is a second alert source beside Discord (studio/alerts.py shares one
judge-keep-push path). Campaigns > Find campaigns > Set up alerts has a Whop tab:
make the app (redirect URI and each permission's reason to copy), save its ID and
key, "Sign in with Whop" from the page, join the communities, tick the feeds.
Each check reads posts newer than the last one seen (up to 100, so a day with the
PC off is caught up; the first check judges the newest 25 from the last 14
days). All reading is through Whop's official API as the signed-in user; no site
is scraped. A hosted Clipper would run one company app for everyone.

## D70 -- YouTube Shorts stats through YouTube's official APIs (2026-09-30)

The user made a YouTube channel (a Brand Account channel on their Google
account -- a new Gmail was refused for phone-number reuse, and a channel needs
none). Its Shorts sync like TikTok and Instagram:

- youtube/api.py signs in through the user's own Google Cloud app (Desktop app
  OAuth client, PKCE, loopback redirect on 127.0.0.1:3457, read-only scopes
  youtube.readonly + yt-analytics.readonly). The consent page's channel picker
  chooses the Brand Account channel; one token file per channel.
- The consent screen must be "In production": in "Testing" Google expires the
  refresh token after 7 days. Unverified, it shows a warning the owner clicks
  through; an expired sign-in says so.
- Data API: the uploads playlist, then each video's title, description, views,
  likes and comments; uploads over 3 minutes aren't Shorts. Analytics API adds
  average watch time and shares (it lags a day or two; optional -- counts still
  sync without it). ~3 quota units per sync against 10,000 a day.
- The log is filled by instagram/sync.apply, now given a `platform`: a Short
  gets its own row copied from its clip's, matched by caption (title +
  description) the first time and by video id after.
- Accounts page: a YouTube Shorts card with the one-time Google Cloud setup
  steps. The TikTok and YouTube "add account" flows are one component.

## D71 -- As many clips as qualify, on Pro (2026-09-30)

The user, with a 3.8 GB footage bank for one campaign: paying users should be
able to "milk as much content as possible". Four caps held a video to a handful:

- "Set a number" stopped at 10 -- and the campaign's max_clips_per_source
  silently overrode it (asking for 10 on a campaign capped at 4 gave 4). An
  explicit count now wins (runner.clip_limit); the campaign cap only bounds
  "Let Clipper decide", and is optional: empty means every moment that clears
  the quality bar. The user's six campaigns had 4-6 with no recorded reason and
  now have none (backups in campaigns/.history).
- At most 3 clips per third of the video meant at most 9 a video. Spreading is
  now a preference: a candidate passed over for a full third fills whatever the
  other thirds couldn't, so five strong early moments give five clips, not two.
- 60 candidate windows whatever the length: the pool is now 2.5 per minute when
  that's more (a 2-hour source gets ~300), and the watch pass sees at least
  twice the requested count (12-40 moments).
- Plans: Pro (the local install) has no cap -- counts 1-30 or any number to
  500, auto has no limit. Below Pro, every job is held to 10 clips a video,
  server-side (plans.clip_count); the page says "Pro makes as many as qualify".

Cost scales with it: on paid Gemini a 2-hour source is roughly $0.30-0.40 to
score, and each clip made adds about a cent of AI (caption fixes, description).

## D72 -- Clip several videos in one go (2026-09-30)

New clips takes any number of ticked videos and queues one job per video, run
in turn (the job runner already ran one at a time), each with its own progress
and results. A repeat in the list is queued once. "I'll pick them" stays one
video: its times belong to that video. An import's videos are all ticked.
Limits per "Make clips": Free 3 videos, Research 10, Pro none (plans.BATCH_LIMIT,
enforced server-side, 402). A hosted Clipper's real limit would be minutes a
month; this only stops one click queuing hours of footage on a free account.

## D73 -- Footage sorted by campaign (2026-09-30)

The video list (62 files, most in the user's Downloads) is grouped by campaign:
the picked campaign's videos first and open, each other campaign folded, then
"Not sorted yet". Files are never moved. A video's campaign (studio/footage.py)
comes from: clips already made from it or a job run on it ("clipped", the
`footage` table plus clips -> work/<id>/info.json); an upload or Drive/Dropbox
import made with that campaign picked ("added"); a distinctive word or the
initials of the campaign's title in the file name ("ADULTS 205", "ChadPowers_",
"PLM_"); or the same name pattern as its neighbours (204.mov beside 201, 207 and
208.mov). Clipper's own clip downloads ("<title> - <campaign>.mp4") are clips,
not footage, and are left out. On the user's PC: 60 of 62 sorted; 1.mp4 and
2.mp4 say nothing and stay unsorted until clipped for a campaign.

## D74 -- Captions that never stack; clipping shown everywhere; "Not good" (2026-09-30)

- **Stacked ("smeared") captions.** The user's Please Like Me clip showed every
  line twice, offset. Not the face-avoiding placement: the transcriber squashed
  a stammer ("a... Why don't you have a back me up") so six words started at
  11.40s, each word's highlight state became an event at the same instant, and
  libass stacks overlapping events vertically. Squashed words are now spread
  over the time they span (captions.spread_squashed), and caption events never
  overlap: each ends when the next begins and a state under two frames is
  dropped (captions.one_at_a_time). Fixes new renders; old clips keep theirs.
- **Clipping in the background.** Jobs always ran on the server whatever page
  was open, but only New clips showed them. A "Clipping · N left · %" pill now
  sits in the top bar on every page, and each finished video raises a toast.
- **Queue order.** New clips lists what's clipping top to bottom in the order it
  runs (running first, then queued oldest first), with finished jobs below.
- **"Not good".** Rating every clip is a chore nobody keeps up. A ready clip has
  a Not good button (card and panel): it skips the clip and records a 1, with
  undo. Posting a clip now counts as a 4 unless rated (learn/feedback.POSTED_AS),
  so learning needs no ratings at all; the panel's five stars became Good / Not
  good with optional reasons (older 1-5 ratings still count).
- **The Learning tab** was mostly a statistics page and a list of clips to rate.
  It left the sidebar: its switch and a one-line summary ("Learning from 11
  clips") are in Settings, linking to the trimmed details page (no rating list).

## D75 -- Clipping about 3x faster (2026-09-30)

Measured first. The user's batch of short Please Like Me sources took 244s,
121s and 73s; the log showed where: ~29s per clip waiting on the overloaded
gemini-3-flash-preview for caption fixes, ~29s more for the description (both
then fell back to flash-lite), one hung request costing 130s at a 120s timeout,
Whisper large-v3 reloaded for every video (~4s) and again for rechecks, clips
rendered one at a time, and videos one at a time. Transcription itself was fast.

- A "try first" model (no retries) that fails rests for 15 minutes
  (LLMBackend._resting); calls go straight to the fallback meanwhile.
- Request timeout 120s -> 60s; calls, video included, take ~5-30s.
- One Whisper model stays loaded for the process (whisper.shared_model), used by
  transcription and caption-fix rechecks; all Whisper GPU work is serialised by
  whisper.GPU_LOCK (8 GB card). A local Ollama LLM would now share the GPU with
  a resident Whisper -- not used here; revisit if it is.
- Clips render 3 at a time within a job (runner.RENDER_WORKERS), in waves that
  never render more than the quota needs.
- Two videos clip at once (jobs.JOB_WORKERS). Job threads are named after the
  job and the threads they start inherit the name, so each job's progress only
  reads its own log lines. The AI's per-minute budget is shared across all of
  them (RateLimiter.shared), so concurrency doesn't draw 429s.
- Scoring batches of 15 moments instead of 8: half the calls.
- Measured on the same three videos, fresh data folder (no caches): 135s for
  all three against 438s before -- about 3.2x.
- Not done: NVENC. The bundled FFmpeg needs NVIDIA driver 610+ (NVENC API 13.1);
  this PC's driver has 13.0, so renders use libx264. A driver update moves
  encoding to the GPU.
- Follow-up: the user updated the NVIDIA driver (617.14) and NVENC now passes
  its probe. On a 37s clip that alone saved little (13.4s -> 12.0s): the full-
  size gblur behind blurred_fit/fit_crop was the slow part. The fill is now
  blurred at quarter size and scaled up (graph.BLUR_DOWNSCALE); frames compared
  side by side on a 16:9 Adults episode look the same, and 30s of it renders in
  5.4s instead of 9.9s.

## D76 -- Ask reads active campaigns' briefs (2026-09-30)

The brief as pasted is now kept whole (campaign_briefs table, up to 30,000
characters) whenever a campaign is saved after "Fill in the form"; the form
only keeps what Clipper acts on, and eligibility, payout terms or what gets
rejected live in the text. Ask has a campaign_brief tool: an active campaign's
pasted brief plus the rules Clipper follows for it (by id, title, or a part of
the title that fits one campaign); archived campaigns aren't offered. Briefs
are fetched only when a question needs one, so storing all of it costs nothing
until then, and unrelated questions cost what they did. The edit page has a
folded "Paste the brief" so existing campaigns can get theirs. Checked live:
"what hashtags ... and what gets rejected" for Chad Powers answered from the
rules (#chadpowers #hulu #tvedits, #ad; football, comedy-only, no Ricky+Russ).

## D77 -- "Post it": the platforms' upload pages, one click each (2026-09-30)

The user asked for tabs per platform a campaign allows, or the sites inside
Clipper. Embedding isn't possible or proper: TikTok, Instagram and YouTube
refuse to be framed (X-Frame-Options / frame-ancestors), Google blocks sign-in
from embedded browsers, and proxying around either would break logins and
their terms. So a ready clip's panel has "Post it": download the video, copy
the caption, open the upload page for each platform the campaign pays for
(TikTok Studio upload, instagram.com's Create, youtube.com/upload) or "Open
all" -- a browser may block the extra tabs until pop-ups are allowed for
Clipper, which it says -- then Mark posted or paste the link. A folded note
names each platform's paid-partnership switch: paid clipping is advertising,
and the FTC (US) and the platforms expect it labelled beyond any #ad. Posting
for the user stays the official-API route, waiting on platform app reviews.

## D78 -- Posts found again when captions share their start; Clips opens on Ready (2026-09-30)

The user's newest Please Like Me post stayed "Ready to post": a post was matched
to its clip by the caption's first 60 characters, and that campaign's required
line opens every caption ("full series is free on youtube (Josh Thomas channel)
In this clip from Please Like Me on..."), so the post matched many clips and
was left as ambiguous. When several clips share the start, the whole captions
now decide (tiktok.sync.best_row: difflib ratio >= 0.8 and 0.05 clear of the
next; the real post scored 0.97 against <= 0.41). Instagram had a second bug:
candidates were keyed by clip id, which repeats across sources ("001_0m00s"),
so 20 clips collapsed into one; they're keyed by source and clip id now. On
the live accounts: TikTok 11 of 11 posts matched, Instagram 6 of 6.

The Clips page opens on "Ready to post" (the user clicked it first every time),
and its filters follow a clip's path: Ready to post, To submit, Submitted,
Skipped, then All.

## D79 -- YouTube setup guide that actually gets through Google's checks (2026-09-30)

Following the first guide, the user hit "Valid app name, support email,
homepage url, and privacy policy url are required for switching the app to
external production mode", and some steps were missing. Publishing (needed so
the sign-in doesn't lapse every 7 days) requires Branding with a public
homepage and privacy policy on an authorized domain. Clipper now ships both
pages (docs/site/index.html, privacy.html: read-only scopes, data kept on the
user's PC, Limited Use statement, how to revoke), served for copying at
/api/setup/site/<page>. The Accounts guide is six parts with every sub-step and
the exact value for each field: project, the two APIs, the Get started wizard,
GitHub Pages (free; repository YOUR-USERNAME.github.io), Branding (authorized
domain, home page, privacy link, no logo so no review) and Publish, then the
Desktop client and its keys. A hosted Clipper would use one verified company
app instead.

## D80 -- YouTube Shorts found when the title sits above the caption (2026-09-30)

The user connected the channel (solomonkey_clips) and its one Short didn't show
on its clip: the sync matched by caption, and a Short's text started with its
title ("the most underrated gay show ... @JoshThomasChannel") with the clip's
caption in the description. A Short now matches by its description first, then
title + description, then the title (Short.alternatives); the proof pack keeps
title and description together (Short.full_text). Live: the Short matched the
same clip as its TikTok and Instagram posts.

## D81 -- Brief rules followed to a T, per platform (2026-10-01)

The AI's captions missed a brief rule ("On YouTube, tag @JoshThomasChannel in the
title") on 40 clips: it sat in free text nothing checked. Rules on a post's text
are now structured (CampaignConfig.caption_rules: text, include/avoid, caption or
YouTube title, which platforms, the brief's quote) next to the existing required
text, hashtags, credit and banned words. Each platform's text is worked out from
the stored caption and the campaign's current rules (campaign/rules.py): missing
requirements are added, banned hashtags dropped, YouTube gets a title (the clip's
hook plus any title rule, cut to 100 characters), and every rule plus each
platform's limits is checked one by one. Banned words are never silently removed;
the check fails and the user edits the caption (now editable until posted).

A second reader (campaign/audit.py) reads each clip's texts against the brief as
pasted, for rules the structured ones miss. A small model raised false alarms (an
@mention called an extra hashtag, an example caption read as mandatory), so it is
told which rules code already checks and to skip them, and each problem is asked
again as one yes/no question before it is shown. Live on the user's clips it found
nothing; with the YouTube rule removed it reported it, with "add @JoshThomasChannel".
A finding with text to add becomes a caption rule for every clip in one click.

studio/rulecheck.py keeps unposted clips current: on start, after a run, and when
a campaign or its brief is saved, stored captions get the every-platform rules
(library and performance log), and the AI check re-reads only clips whose texts or
brief changed. The brief reader now fills caption_rules and posting_rules (what
only the poster can do, shown as a checklist when posting) and is told to drop none.

## D82 -- Masking only the words that get posts flagged (2026-10-01)

The user asked to avoid shadowbans and bans without cutting vulgar jokes: "as
close to the line as possible". Vyro's content requirements ban pornography and
sexually explicit material, hate speech, self-harm and drug promotion, but list no
words, and the platforms read captions, titles and on-screen text. So
campaign/safety.py masks a short list in a post's caption, description, YouTube
title and new clips' hook lines: explicit sexual terms, self-harm and hard drugs
lose one letter ("p*nis", "s*icide"), slurs all but the first; a hashtag of one is
dropped. Ordinary swearing stays. @mentions, links and words the campaign itself
uses (a show called "Sex Education") are untouched. On by default, a switch per
campaign (censor_flagged_words). Burned-in subtitles keep following
mask_profanity_in_captions; hooks already rendered into a video can't change.
On the user's clips it masked nine Please Like Me descriptions (sex, penis, slut,
suicide, porn).

## D83 -- X posts found and synced; links filed for every platform (2026-10-01)

"Link grabbers for every single possible platform": of the 25 campaigns Clipper
had found, 3 allowed X and none Facebook, Snapchat or Threads. X is now a full
platform: campaigns can target it, its post text is the caption line and hashtags
cut to 280 characters (optional tags dropped first, required ones kept), and a
connected account's posts are found and their numbers synced (x/api.py). X's API
is pay-per-use since February 2026 ($0.005 per post read, each post charged once
per UTC day, empty requests free, no free tier), so Clipper asks only for posts
newer than the last one it saw, hourly, and refreshes views only for posts up to
14 days old: about $2 a month at one clip a day. It reads with the app's Bearer
Token (public metrics carry impressions); the user buys the credits themselves.
Facebook, Snapchat and Threads links can be pasted and are filed for submitting,
marked as having no numbers: none has an official API that gives a post's views
to a personal account. Matching a post to its clip now compares the openings up
to the shorter one (20 characters at least), since a cut-down X post may lose the
hashtags of a short caption.

## D84 -- One-click YouTube for a packaged copy (2026-10-01)

Connecting YouTube took a six-part Google Cloud setup (D79). A copy of Clipper
packaged for someone else can now carry its owner's Google app:
`clipper youtube bundle` writes src/clipper/youtube/app.json from .env, and
youtube.api.client() falls back to it when the user has no app of their own, so
the Accounts page goes straight to "Connect with YouTube". Google treats a desktop
app's secret as not confidential (PKCE protects the sign-in), but the user's rule
is that secrets stay out of the code, so the file is git-ignored and only written
when packaging. Until Google verifies the app (homepage and privacy policy on an
owned domain, a review of the two read-only scopes), its users see "Google hasn't
verified this app" and it is capped at 100 users. TikTok and Instagram still need
each user's own developer app until Clipper's own apps pass review (business
registration needed).
