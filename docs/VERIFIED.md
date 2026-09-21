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

---

## 2026-09-21 — Gemini backend (verified early, key supplied by the user)

`GEMINI_API_KEY` is set in `.env` (git-ignored; confirmed with `git check-ignore`).

**58 models visible, 41 supporting `generateContent`.** Flash/lite tier, which is
what the rubric scorer will use:

```
gemini-flash-latest          gemini-flash-lite-latest      (moving aliases)
gemini-3.8-flash             gemini-3.7-flash              gemini-3.6-flash
gemini-3.5-flash             gemini-3.5-flash-lite
gemini-3.1-flash-lite        gemini-3-flash-preview        gemini-2.5-flash
```

**Do not hardcode a model name.** `gemini-2.5-flash-lite` — which is still widely
documented — returns:

> `404 NOT_FOUND … This model is no longer available to new users. Please update
> your code to use models/gemini-3.5-flash-lite`

This confirms the design in `config/default.yaml` (`llm.model: null`): the backend
enumerates models at runtime and picks from a preference list. The `*-latest`
aliases are the safest default since they cannot 404.

**Structured output works server-side.** One real call with
`response_mime_type="application/json"` and a pydantic `response_schema` of
`list[Rubric]`:

| Model | Latency | Tokens in/out | Result |
|---|---|---|---|
| `gemini-3.5-flash-lite` | 1.20 s | 77 / 207 | valid JSON, validated by pydantic first try |

Judgement on two deliberately-opposite test clips was sensible: the rambling one
scored hook=0 payoff=0 `needs_prior_context=true`; the specific one scored
hook=10 payoff=10.

Two notes carried into Phase 3:

- `response_schema` enforces the shape server-side, so the brief's "retry once
  with a repair instruction" path (section 9.1) should rarely fire. It will still
  be implemented, as a safety net rather than the normal case.
- The model returned `hook_text` as a verbatim copy of the transcript instead of
  a punchy line of ≤10 words. That is a prompt-design problem to fix in Phase 3,
  not a model limitation.
- Pass `automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)`
  to suppress the SDK's AFC warning on every call.

**Rate limits were not measured** — doing so means deliberately exhausting the
free-tier quota. The client-side limiter (`llm.requests_per_minute`) plus
exponential backoff handles this instead.

---

## 2026-09-21 — Phase 2

### Transcription speed and VRAM — measured on this GPU

75.5 s source, `float16` on the RTX 2070 SUPER, VAD on, word timestamps on:

| Model | Wall clock | Realtime factor | Words |
|---|---|---|---|
| **large-v3** | 8.4 s | **9.0x** | 186 |
| distil-large-v3 | 2.0 s | **38.5x** | 187 |
| small | 3.2 s | 23.4x | 186 |

Extrapolating `large-v3` to a 60-minute source gives roughly **6.7 minutes** of
transcription — comfortably inside the 25-minute Phase 4 budget. `distil-large-v3`
would do the same source in about 1.6 minutes for a near-identical word count,
and is the obvious fallback if the budget is ever tight.

VRAM, sampled at 0.15 s intervals across a `large-v3` run:

| | MiB |
|---|---|
| Baseline before load | 457 |
| **Peak during transcription** | **4793** |
| After the model is freed | 569 |

Model footprint ~4.3 GB, leaving ~3.4 GB headroom on the 8192 MiB card, and the
memory is genuinely released. This is what makes the brief's sequential-stage
requirement workable.

### Transcription accuracy against known ground truth

Windows SAPI ("Microsoft David Desktop" / "Microsoft Zira Desktop") synthesises
speech locally, so fixtures have **exact known wording**. faster-whisper
transcribed a 9-sentence script **word for word, with correct punctuation**:

> Most people think the hardest part is getting started. It isn't. The hardest
> part is deciding what to stop doing. …

Segmentation then recovered exactly the **9 expected sentences**. This means the
Phase 2 tests assert against ground truth rather than merely asserting output is
non-empty, and it needed no downloads.

### Silence measurement — checked, not assumed

Concern: if the silence ratio counts every inter-word micro-gap, it measures the
transcriber's bracketing rather than dead air. Measured on a real transcript:

```
gaps between consecutive words: n=77, median 0.000 s
gaps > 0.3 s: n=10, totalling 16.0 s
naive silence ratio 42.07%  ==  dead-air ratio 42.07%
```

The median gap is **zero** — whisper emits contiguous word timings within
continuous speech — so the simple metric already measures real dead air and no
gap threshold is needed.

Note for tuning: the 25% `max_silence_ratio` from the brief is tight. On the
realistic-cadence fixture the whole source measures 29.6% silence while the
windows that survive measure 23–25%. It works, but it is close enough to the
line to deserve checking during Phase 5 eval.

### Windows gotcha: huggingface_hub symlinks

Downloading `large-v3` failed **after** the multi-gigabyte download completed:

```
OSError: [WinError 1314] A required privilege is not held by the client:
  '..\..\blobs\75336fea…' -> '…\snapshots\…\config.json'
```

huggingface_hub symlinks snapshot files to blobs, which needs Developer Mode or
admin. `transcribe/whisper.py` now sets `HF_HUB_DISABLE_SYMLINKS=1` before the
import, so it copies instead. Costs disk, not a failed run.

### Bug found and fixed: end-snapping could truncate a clip

Boundary refinement preferred a punctuated sentence end within 3 s of the
requested end — in either direction. With a long unpunctuated tail, the nearest
punctuated end can be *behind* the requested end, so a window
`[0.00, 2.70]` snapped to `[0.00, 0.60]`, discarding a whole sentence and
cutting the clip to a fifth of its length.

Finishing a thought is a forward operation, so the punctuation preference is now
forward-only. Regression test:
`test_end_never_snaps_backwards_past_a_whole_sentence`.

### Bug found and fixed: `--model` silently ignored

A cached `transcript.json` was reused regardless of which model produced it, so
`clipper transcribe --model large-v3` on a source previously transcribed with
`small` returned the small transcript and reported "reusing transcript". The
cache now compares the stored model name against the requested one.

### Test suite

| Suite | Result |
|---|---|
| Full (`pytest`) | **379 passed** in 103.6 s |
| Fast (`pytest -m "not slow"`) | **349 passed**, 30 deselected, in 4.1 s |
| `ruff check` | clean |

---

## 2026-09-21 — Phase 3

### Real Gemini backend, end to end — measured

24 candidates from the 75-second speech fixture, two prompts each, batch size 8:

| | |
|---|---|
| LLM calls | **6** (48 candidate-scorings / 8 per batch × 2 prompts) |
| Failed calls | 0 |
| Tokens | 8,381 in / 7,897 out |
| Mean latency per call | **3.95 s** |
| Rate-limit waiting | 9 s (client limiter at 10 RPM) |
| Wall clock for the whole scoring stage | **34.9 s** |
| Candidates scored | 20 of 24 (4 hard-dropped) |

A second run with a warm cache makes **zero** LLM calls.

### The ranking is sensible — checked, not assumed

Top pick, `c008`, weighted total **8.41/10**:

> Why do people quit? … They quit because the next step was never written down.
> I lost 11 months to that exact mistake. … Every night I wrote one sentence
> describing tomorrow's first action. … My completion rate went from about 10%
> to over 70.

That is the strongest moment in the script by inspection: question hook, a
specific claim, a concrete payoff, a result, and a complete ending. Prompt A and
prompt B both scored its hook 9/10.

`hook_text`: *"Why most people actually quit"* — original, under ten words, and
faithful. The Phase 0 verification found the model echoing the transcript
verbatim here; the explicit rule and worked example in `prompts.py` fixed it.

`suggested_caption`: *"It is not about motivation. It is about not knowing the
next step."* plus four relevant hashtags.

**The two prompts genuinely differ.** On a mid-ranked candidate, A (editor) vs
B (distracted viewer) scored hook 5 vs 3, payoff 7 vs 5, emotion 6 vs 4 — B
consistently harsher, which is what the prompt asks for. They are still not
independent judges (same model, correlated errors; PLAN.md P3), but the second
opinion is doing visible work rather than echoing the first.

**Hard drops fire correctly.** Four candidates were dropped, all for
`needs_prior_context` agreed by both prompts — and correctly so: each begins
mid-conversation with no referent.

### Three bugs found by running it for real

1. **`response_schema` shape.** The google-genai SDK rejects a plain
   `[RubricItem]` list literal with a pydantic `ValidationError`; it requires the
   typing generic `list[RubricItem]`. Verified both forms directly.
2. **A non-retryable error was retried.** That schema failure was wrapped as a
   generic `LLMError` and retried with exponential backoff — **2 m 29 s** across
   six batches before reporting a problem that could never have succeeded.
   Request-construction failures are now `LLMConfigError`, which is not retried
   and aborts the run immediately.
3. **The cache reported hits while still calling the LLM.** `_parse_items`
   unwrapped "the first list-valued key" to handle `{"clips": [...]}` wrappers —
   but a rubric item has its own list-valued key, `hashtags`. A cached single
   item was therefore read back as a list of hashtag strings, parsed as nothing,
   and re-fetched. The run reported "48 hit / 0 miss" **and** made 6 calls.
   Unwrapping now requires a list of objects. Regression test:
   `test_a_bare_object_is_treated_as_one_item`.

Two smaller fixes from the same run: `MockBackend` and `OllamaBackend` were being
throttled at the configured 10 RPM (a `setdefault` losing to an explicit kwarg),
making a mock-backed run spend 30 s purely sleeping; and the CLI claimed "every
score came from cache" when in fact every call had failed.

### Audio signal defects found via `clipper explain`

Printing the raw features made two bad values obvious:

| Feature | Before | After | Why |
|---|---|---|---|
| `dynamic_range_db` | 83.7 | 21.2 | Digital silence is −200 dB once EPS is added. Clamped at a −60 dB floor, and now measured over *voiced* frames only — otherwise it just re-measures "contains pauses", which `silence_ratio` already covers |
| `onset_rate` → `onset_ratio` | 15.1 | 1.01 | The threshold is the 85th percentile over the whole file, so ~15% of frames exceed it *by construction* and the absolute rate carried no information. Now expressed relative to the video's own average |

Both previously saturated their normalisation, making the features dead weight.
Post-fix distribution across the 24 candidates: `dynamic_range_db` 18.8–21.9,
`onset_ratio` 0.99–1.09, audio raw score 0.617–0.673.

### Test suite

| Suite | Result |
|---|---|
| Full (`pytest`) | **554 passed, 1 skipped** in 161.7 s |
| Fast (`pytest -m "not slow"`) | **511 passed**, 44 deselected, in 5.0 s |
| `ruff check` | clean |
