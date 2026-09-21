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
