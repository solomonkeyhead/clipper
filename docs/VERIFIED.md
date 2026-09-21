# Verified facts

Everything here was checked by running it on this machine, on the date shown.
Nothing in this file is from memory. Anything unverified says so explicitly.

**Machine:** Windows 11 Pro 10.0.26200, NVIDIA GeForce RTX 2070 SUPER (8192 MiB),
16 logical CPUs, 131 GB free on C:.

---

## 2026-09-21 — Phase 0

### GPU / driver

| Fact | Value | How checked |
|---|---|---|
| GPU | NVIDIA GeForce RTX 2070 SUPER, 8192 MiB | `nvidia-smi` |
| Driver | 591.86 | `nvidia-smi` |
| Driver CUDA level | 13.1 | `nvidia-smi` |
| CUDA toolkit installed | 13.2 (V13.2.78) | `nvcc --version` |

### Python

| Fact | Value |
|---|---|
| Pre-existing interpreters | 3.14.4 (`C:\Python314`), 3.13.3, 3.13.2 (miniconda) |
| 3.11 / 3.12 present before Phase 0 | **No** |
| Installed for this project | CPython **3.12.14**, via `uv python install 3.12` |
| Venv | `C:\Users\marc\Clipper\.venv` |

`uv python install 3.12` printed `error: Missing expected target directory for
Python minor version link` but still produced a working interpreter at
`%APPDATA%\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe`, which
`uv venv --python 3.12` then used successfully. Cosmetic, not a blocker.

### FFmpeg

Installed in Phase 0 with `winget install --id Gyan.FFmpeg`.

| Fact | Value |
|---|---|
| Version | **9.0.1-full_build-www.gyan.dev** (gcc 16.1.0) |
| Path | `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_…\ffmpeg-9.0.1-full_build\bin` |
| libass | yes (`--enable-libass`, `ass` filter present) |
| Font stack | `--enable-libfreetype --enable-libfribidi --enable-libharfbuzz --enable-fontconfig` |
| QA filters | `loudnorm`, `silencedetect`, `blackdetect`, `freezedetect`, `gblur`, `boxblur` all present |
| NVDEC / `-hwaccel cuda` | **works** (decoded a test file with no error) |

### NVENC — does not work on this machine

```
$ ffmpeg -f lavfi -i testsrc2=... -c:v h264_nvenc ... out.mp4
[h264_nvenc] Driver does not support the required nvenc API version.
             Required: 13.1 Found: 13.0
[h264_nvenc] The minimum required Nvidia driver for nvenc is 610.00 or newer
```

`h264_nvenc` **is** compiled into this build and **is** listed by
`ffmpeg -encoders`, but the installed 591.86 driver exposes NVENC API 13.0 while
FFmpeg 9.0.1 was built against SDK 13.1. It fails at encoder-open time.

This is why `render/ffmpeg.py` probes encoders by *actually encoding a frame*
rather than trusting `-encoders`, and why `doctor` reports NVENC as a WARN with
an x264 fallback rather than assuming the GPU path works.

Two ways to get GPU encoding back, neither required: update the NVIDIA driver to
610.00+, or install an FFmpeg built against an older NVENC SDK (7.x).

### libx264 fallback speed — measured

1080x1920, 30 fps, 30 s of `testsrc2`, CRF 20, on the 16-thread CPU:

| Preset | Wall clock | Speed |
|---|---|---|
| `veryfast` | 2.89 s | **10.4x** realtime |
| `faster` | 3.80 s | 7.9x realtime |
| `medium` | 5.10 s | 5.9x realtime |

Caveat, stated honestly: `testsrc2` is synthetic and easier to encode than real
footage, so these are an upper bound. Even at a third of this, five 45-second
clips encode in well under a minute, so losing NVENC does not threaten the
25-minute budget in BUILD_BRIEF.md section 15 / Phase 4.

### Transcription stack

| Package | Version | Note |
|---|---|---|
| faster-whisper | 1.2.1 | requires-python >=3.9 |
| ctranslate2 | 4.8.2 | Windows wheels for cp310–cp314 |
| onnxruntime | 1.30.0 | **requires-python >=3.11** — this is what rules out 3.10 |
| av | 18.1.0 | |

**CUDA requirement, verified from the faster-whisper package metadata (not from
memory):** *"GPU execution requires the following NVIDIA libraries to be
installed: cuBLAS for CUDA 12 and cuDNN 9 for CUDA 12. The latest versions of
ctranslate2 only support CUDA 12 and cuDNN 9."* Older CUDA needs ctranslate2
3.24.0 (CUDA 11) or 4.4.0 (CUDA 12 + cuDNN 8).

Neither library ships with ctranslate2, so they are installed as wheels:

```
uv pip install "nvidia-cublas-cu12" "nvidia-cudnn-cu12>=9,<10"
```

Resolved to `nvidia-cublas-cu12==12.9.2.10`, `nvidia-cudnn-cu12==9.26.0.51`,
`nvidia-cuda-nvrtc-cu12==12.9.86`. A CUDA-12 build running against a CUDA-13.1
driver is fine — driver compatibility is forward, not backward.

Their DLLs land in `site-packages/nvidia/*/bin`, which Windows does not search,
so `clipper/utils/cuda.py` registers those directories with
`os.add_dll_directory` before ctranslate2 is imported.

**Proven end to end**, not merely imported:

```
ctranslate2 4.8.2
cuda device count: 1
supported compute types (cuda:0): float16, float32, int8, int8_float16, int8_float32
model load (whisper tiny, float16, cuda): 4.4 s
transcribe 3 s of audio: 0.80 s   lang=en
gpu mem used while loaded: 648 MiB
```

Note `bfloat16` is **absent** from the supported compute types, confirming Turing
as BUILD_BRIEF.md section 3 states. `float16` is available and is the default.

### Other libraries

| Package | Version | Note |
|---|---|---|
| opencv-python-headless | 5.0.0.93 | `cv2.FaceDetectorYN` present — YuNet usable |
| yt-dlp | 2026.08.19 | `heatmap` field behaviour **not yet verified** (Phase 2) |
| google-genai | 2.24.0 | free-tier model list **not yet verified** (Phase 3) |
| pydantic | 2.13.5 | |
| numpy / scipy / pandas | 2.5.3 / 1.18.1 / 3.0.6 | |
| typer / rich | 0.27.2 / 15.0.0 | |
| pytest / ruff / hypothesis | 9.1.1 / 0.16.8 / 6.168.0 | |

### Windows ASS filter path escaping — verified

The hazard in BUILD_BRIEF.md section 17. Both approaches were tested against
ffmpeg 9.0.1 with a directory name containing spaces
(`…\Temp\ass test dir\sub folder\sub title.ass`):

| Approach | Result |
|---|---|
| Absolute path, escaped to `C\:/Users/…/sub title.ass` | **works** (rc=0) |
| `cwd` set to the file's directory, bare filename passed | **works** (rc=0) |

clipper uses the escaped-absolute form (`ffmpeg.escape_filter_path`) because it
avoids juggling the working directory. Spaces need no escaping inside a quoted
filter argument; the drive colon and backslashes do.

Caption rendering was confirmed **visually**, not just by exit code: a single
frame was extracted and inspected, showing heavy white text with a black outline,
one word highlighted in yellow, sitting inside the configured bottom safe area.

### Assets downloaded by `clipper doctor --fix`

| File | Size | Licence |
|---|---|---|
| `Anton-Regular.ttf` | 167 KB | SIL OFL 1.1 |
| `Inter.ttf` (variable) | 856 KB | SIL OFL 1.1 |
| `face_detection_yunet.onnx` (2023mar) | 227 KB | Apache-2.0 (opencv_zoo) |

### `clipper doctor` on this machine

16 checks: **12 PASS, 4 WARN, 0 FAIL** before `--fix`; after `--fix` the font and
face-model warnings clear, leaving NVENC (driver) and the missing
`GEMINI_API_KEY`.

---

## Not yet verified

These are called out so nothing downstream assumes them:

- **yt-dlp `heatmap` field** — shape, availability, and whether it is still
  populated for "Most replayed". Phase 2.
- **Gemini free-tier model IDs and rate limits** — the public rate-limit page now
  defers to AI Studio for the free tier, so the backend will enumerate models at
  runtime rather than hardcoding. Phase 3.
- **large-v3 real-time factor on this GPU** — Phase 2 acceptance criterion.
- **Ollama** — not installed on this machine at all.
- Real end-to-end run on a 30–60 minute source. Phase 4.

---

## 2026-09-21 — Phase 1

### Dynamic crop via FFmpeg `sendcmd` — verified

`ffmpeg -h filter=crop` shows `x` and `y` carrying the `T` flag, meaning they
accept runtime commands. A generated script of `<t> crop x <px>;` lines fed to
`sendcmd` was used to pan a 405x720 crop across the full width of a 1280x720
source over 4 seconds. Extracted frames at n=0, 60 and 119 show the left, middle
and right thirds of the test pattern respectively: the crop tracked.

Output confirmed by ffprobe as `1080,1920,30/1,120` — correct size, frame rate
and frame count. This removes the need to pipe raw frames through Python
(see docs/DECISIONS.md D11).

### FFmpeg 9 flag removal

`-vsync` no longer exists in FFmpeg 9.0.1 (`Unrecognized option 'vsync'`), which
is a hard error rather than a deprecation warning. `-fps_mode` replaces it.

### Windows path handling under load — verified

The integration suite renders from a directory containing spaces
(`…\my source videos\work dir\a source clip.mp4`) to an output directory that
also contains spaces, with the ASS file in a third spaced directory. It passes.
Non-ASCII caption text (`café`, `naïve`, `日本語`) round-trips through the ASS
file and into the burned-in render.

### Render timings — measured on this machine

6-second clips from a 1920x1080 source to 1080x1920, libx264 `veryfast` CRF 20,
captions and loudnorm included, single FFmpeg process:

| Layout | Wall clock |
|---|---|
| follow_crop (31 sendcmd keyframes) | 1.06 s |
| two_speaker_stack | 1.26 s |
| blurred_fit | 2.44 s |

`blurred_fit` is the slowest because `gblur` runs over the full output frame.
Extrapolating to five 45-second clips gives roughly 40-90 s of rendering, well
inside the Phase 4 budget even without NVENC.

### Visual inspection — done, not skipped

All three layouts were rendered from synthetic fixtures and frames extracted at
t=1.0 s and t=4.0 s were inspected:

- **follow_crop**: the moving disc stays centred in the output at both times
  while it travels across the source — the camera followed it.
- **two_speaker_stack**: two panes, correct speakers top and bottom, clean seam.
- **blurred_fit**: source full-width and centred, blurred darkened fill above and
  below, **no black bars**.
- Captions sit inside the bottom safe area in all three, active word highlighted
  in yellow, hook text at the top safe margin, credit top-left.

### Test suite

| Suite | Result |
|---|---|
| Full (`pytest`) | **212 passed** in 31.6 s |
| Fast (`pytest -m "not slow"`) | **199 passed**, 13 deselected, in 2.8 s |
| `ruff check` | clean |
