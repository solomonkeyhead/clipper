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

---

## 2026-09-21 — Phase 4

### End-to-end run, real Gemini backend — measured

Source: a 217-second (3m37s) locally synthesised speech fixture, five distinct
topic blocks. Full resolution (1080x1920), not draft.

| Stage | Wall clock |
|---|---|
| Transcribe (`large-v3`) | 17.0 s |
| Score (ingest + signals + LLM) | 114.4 s |
| Render + QA, 3 clips | 68.1 s |
| **Total** | **~2 m 40 s** |

**3 clips accepted, 0 rejected**, spread across the first, middle and final
thirds of the source. Composites 0.873 / 0.870 / 0.546. 47 candidates scored,
13 hard-dropped by the LLM.

The picks are the three strongest distinct stories in the script by inspection:
the pricing change, the completion-rate habit, and the unused dashboard. Their
hooks were written by the model, not copied from the transcript:

> "Why raising prices can reduce your workload"
> "Why most people actually quit"
> "We wasted six weeks building this"

Campaign hashtags and the required credit were folded into every caption and the
compliance gate passed all five rules per clip.

### Extrapolation to a 60-minute source — **estimated, not measured**

No 30-60 minute source was run: none is available here that is both long enough
and authorized. From the measured stage rates:

| Stage | Basis | 60-minute estimate |
|---|---|---|
| Transcribe | 12.8x realtime measured | ~4.7 min |
| Score | candidates cap at 60, so ≈ the measured 47-candidate cost | ~2.5 min |
| Render + QA | 22.7 s per clip measured, 5 clips | ~1.9 min |
| **Total** | | **~9 min** |

That sits inside the 25-minute Phase 4 budget with substantial margin, but it is
an extrapolation from a 3.6-minute source and should be re-measured on real
long-form footage before being relied on.

### Performance bugs found and fixed

| | Before | After |
|---|---|---|
| Face scan, 20 s of video | **15.4 s** | **3.4 s** |
| QA detection passes per clip | 3 decodes (1.10 + 1.37 + 0.92 s) | 1 decode |

Two causes, both real:

1. **Per-sample seeking.** The face scan called `CAP_PROP_POS_MSEC` for every
   sample, costing a keyframe seek plus decode each time -- slower than realtime.
   Sequential decode with frame skipping does the same work far more cheaply.
2. **Detecting at source resolution.** YuNet is trained at 320x320 and was being
   run at 1920x1080. Detection now happens on a 640-wide copy with the boxes
   scaled back to source coordinates.

`blackdetect`, `freezedetect` and `silencedetect` are independent filters over
the same frames, so they now run in one FFmpeg invocation instead of three.

### Correctness bugs found by running it

1. **Clip IDs collided.** The rank counter was decremented when a clip was
   rejected and then reused, so two rejected files could share both an id and
   nearly a filename. Ids now come from a monotonic attempt counter.
2. **The stop reason blamed the wrong gate.** A run reported "27 scored below
   min_llm_total 5.5/10" when only **5** actually had -- the count lumped
   `min_composite` rejections in with quality ones, and the real binding
   constraint was clip placement. The note now reports the actual distribution
   of rejection reasons.
3. **`min_composite: 0.35` rejected the bottom third of candidates.** Percentile
   ranks spread evenly over [0, 1] by construction, so that is not a "tail" -- and
   worse, a candidate rejected there cannot become a reserve, starving the
   QA-failure replacement pool. Lowered to 0.15; the absolute gate does the
   quality work.

### Two QA checks were measuring the wrong thing

**Silence.** `candidates.max_silence_ratio` estimates silence from gaps between
word timings; `qa.max_silence_ratio` measures acoustic silence in the rendered
audio. Measured on the same clip, the acoustic figure reads **~8 points higher**
(30-34% vs 23-25%). With both set to 0.25 the gate rejected clips the filter had
already passed, wasting a full render each time. The QA threshold is now 0.35
and the difference is documented in both files.

**Freezes.** `freezedetect` at `-60 dB` fires on near-identical frames, and a
1.0-second absolute limit failed a clip for 1.2 s of stillness in a 50-second
clip. That is a held shot, not a stalled render. Now judged as a *fraction* of
the clip (fail above 30%) with the absolute seconds only warning.

**Lead silence** was downgraded from fail to warn. Whisper's word timestamps
lead the actual audio onset by roughly 0.4 s on these fixtures, so clips snapped
to a word boundary routinely open with a little silence. That makes a clip
slightly weaker, not broken -- and failing it would substitute a lower-scoring
clip, which is a worse outcome.

### Visual inspection — done

Frames extracted from all three clips at t=1.2 s and t=12 s show: the model's
hook text across the top in the safe area for the first two seconds, word-level
captions at the bottom with the active word highlighted, and the `blurred_fit`
layout placing the source strip over a blurred, darkened fill.

The fill renders very dark on this fixture because the fixture's own background
is near-black; `eq=brightness` was softened from -0.22 to -0.15 so a dark source
does not crush the fill to solid black (which would also risk tripping
`blackdetect`).

### Fixture limitations worth stating

- **YuNet found faces in 0% of frames**, correctly: the stand-in "faces" are flat
  coloured discs, not faces. So every clip used `blurred_fit`. The follow-crop
  and two-speaker paths are covered by unit tests and by the Phase 1 integration
  tests with scripted trajectories, **not** end to end here. Whether YuNet finds
  real faces is **unverified on this machine** -- what is verified is that it
  loads, runs, and produces no false positives across 101 sampled frames.
- The first synthetic fixture was a static image, so `freezedetect` reported the
  whole clip frozen. Temporal noise was added (which is what real sensor noise
  provides) after discovering that slow sinusoidal motion was not enough: a sine
  has near-zero velocity at its turning points.
- An earlier attempt to add motion by animating hue manufactured **7 false scene
  cuts**, because the cut detector compares hue/saturation histograms.

---

## 2026-09-21 — First run on real long-form footage

Source: a 20m51s (1251 s) 1080p YouTube video supplied by the user, 459 MB
downloaded. Processed locally for technical verification only; the campaign
config records that explicitly and nothing was published.

### Result: 4 clips accepted, 0 rejected, all QA-passing

| Clip | Start | Duration | Composite | Layout | QA |
|---|---|---|---|---|---|
| 001 | 7m24s | 26 s | 0.971 | follow_crop | pass (13 checks) |
| 002 | 5m05s | 39 s | 0.745 | follow_crop | pass (13 checks) |
| 003 | 1m58s | 49 s | 0.524 | follow_crop | pass (13 checks) |
| 004 | 12m42s | 52 s | 0.423 | follow_crop | pass (13 checks) |

### Timings — measured

| Stage | Wall clock | Rate |
|---|---|---|
| Download | included below | 459 MB |
| Transcribe (`large-v3`) | 142.0 s | **8.8x realtime**, 3736 words |
| Score (candidates + signals + LLM) | 267 s | 60 candidates |
| Render + QA, 4 clips | 76 s | ~19 s per clip |
| **Total** | **343.5 s (5m43s)** | **3.6x faster than realtime** |

**Extrapolated to 60 minutes: ~13 minutes.** Transcription scales linearly
(~6.8 min), rendering scales with clip count not source length (~1.6 min for 5),
and scoring is roughly flat because candidates are capped at 60 regardless of
source length. Inside the 25-minute budget with margin. Still an extrapolation,
but now from a 21-minute real source rather than a 3.6-minute synthetic one.

### Face detection — verified on real faces for the first time

Every clip chose `follow_crop`, with one dominant face found across 131, 194,
245 and 261 sampled frames respectively. Extracted frames confirm the speaker
stays centred and well-framed throughout each clip as the crop tracks him.

This closes the gap recorded at CHECKPOINT 2: until now YuNet had only been
shown to *load and produce no false positives* on synthetic discs. It finds real
faces, the trajectory smoothing holds, and the QA face-ratio check passes.

Captions also verified on real output: word-level timing with the active word
highlighted in yellow, inside the bottom safe area.

### Finding: 77% of candidates were dropped for needing prior context

46 of 60 candidates were hard-dropped, **all** for `needs_prior_context` agreed
by both prompts. Inspecting them, the model is right — this is chess commentary,
and the dropped windows genuinely open mid-explanation:

> "**Again, keep in mind,** this is a machine programmed to make decisions based
> on the neurotransmission of a literal, basically microscopic fruit fly..."

So the behaviour is correct, but the rate is worth recording. It left 14 scored
candidates, of which 12 cleared the quality bar and 4 survived placement. On a
less clippable source that margin would have produced nothing.

Whether `needs_prior_context` should be a **hard drop** or a heavy **penalty** is
a real open question for Phase 5 to measure, not to assume. The brief specifies a
hard drop; on this evidence that is defensible but aggressive.

### Finding: the pre-score is doing far more work than expected

5171 windows were enumerated, 4829 survived the hard filters, and
`max_candidates: 60` cut that to 60 — so **the cheap pre-score chose the final
60 out of 4829**, and the LLM never saw 98.8% of the candidate space.

The pre-score is transcript-only heuristics (speech density, hook openers,
filler, duration, confidence) and was designed as a crude cost cap. On a
20-minute source it is effectively the primary selector. That makes it a far
more important component than its implementation suggests, and a prime suspect
if Phase 5 finds the ranking weak.

### Bug found and fixed: yt-dlp could not find FFmpeg

The first attempt failed after the metadata fetch:

```
ERROR: You have requested merging of multiple formats but ffmpeg is not
installed. Aborting due to --abort-on-error
```

YouTube serves video and audio as separate streams, so yt-dlp needs FFmpeg to
merge them — and it looks on PATH, which is precisely the thing that is
unreliable here: a winget install lands on the *user* PATH, which an
already-running process does not see. `render/ffmpeg.py` exists to solve exactly
this and had already located the binary; the location simply was not being
passed through. `ingest/download.py` now sets yt-dlp's `ffmpeg_location`.

### Heatmap: absent here, but verified against real data elsewhere

This video has **no heatmap** despite 2.1M views — it was a week old, and
YouTube only exposes "Most replayed" after enough accumulated watch time. The
signal was dropped and the remaining weights renormalised (llm 0.62, audio 0.19,
text 0.19), which is the intended behaviour.

yt-dlp *does* still extract heatmaps: metadata-only probes of two older videos
returned 100 segments each. Real shape, confirmed:

```json
{"start_time": 0.0, "end_time": 2.14, "value": 1.0}
```

`normalize_heatmap` parses it correctly — the first time it has seen anything
but synthetic input.

**Intro inflation is real and measured**: on that data the first 30 seconds read
0.300 against a whole-video mean of 0.159, i.e. **1.89x inflated**, exactly the
bias the brief predicted.

**And the correction was under-performing.** The rolling median window was fixed
at 180 s; on a 213-second video that covers most of the series, making it a
global median that detrends almost nothing. The window is now capped at a third
of the duration, which improved the corrected intro from +0.113 to +0.087.

**Residual limit, stated honestly:** a rolling median cannot fully remove a spike
at the very edge of the series — a median is deliberately insensitive to a narrow
spike, and at t=0 there is no earlier neighbourhood, so the edge padding mirrors
the spike into its own baseline. This does not affect clip selection, because
candidate generation already discards the first and last 20 s of any source over
five minutes. It **will** skew the Phase 5 eval harness unless that harness
applies the same exclusion.

---

## 2026-09-22 — Second real source: a 32-minute reaction video

Source: a 31m56s 1080p YouTube video, 265 MB. Processed locally for technical
verification only, per the same campaign config as the first.

This is the first source in the brief's stated 30-60 minute acceptance range.

### Result: 5 clips accepted, 0 rejected

| Clip | Start | Duration | Composite | Layout | QA |
|---|---|---|---|---|---|
| 001 | 14m19s | 28 s | 0.871 | follow_crop | pass (13) |
| 002 | 22m58s | 35 s | 0.758 | follow_crop | pass (13) |
| 003 | 12m27s | 54 s | 0.750 | follow_crop | pass (13) |
| 004 | 6m17s | 36 s | 0.735 | follow_crop | pass (13) |
| 005 | 26m25s | 37 s | 0.660 | blurred_fit | pass (12) |

### Timings — measured on a 30-60 minute source

| Stage | Wall clock |
|---|---|
| Transcribe (`large-v3`) | 235.7 s — **8.1x realtime**, 6293 words |
| Score (60 candidates, real Gemini) | 397 s |
| Render + QA, 5 clips | 174 s |
| **Total** | **571 s (9m31s)** |

**This satisfies the Phase 4 acceptance criterion directly**, without
extrapolation: a real source in the 30-60 minute range, five QA-passing clips,
9m31s against a 25-minute budget. Transcription held at 8.1x realtime on a
source nine times longer than the earlier fixture, so that rate is now measured
across two very different real inputs rather than inferred from one.

Scoring is the largest stage and is roughly flat in source length, because
candidates are capped at 60 regardless. A 60-minute source should land near 17
minutes, still inside budget.

### A layout failure this source exposed, and the fix

The video composites **two small circular reaction cams** over footage that
fills the frame. Measured: the dominant face is 6.3-8.2% of frame width, and
2+ faces are visible in 74-100% of sampled frames.

The screen-share detector correctly found nothing -- there is no *separate*
content region, because the footage is the whole frame. But the fallback then
saw two persistent faces and chose `two_speaker_stack`, which would have blown
two tiny circular avatars up to fill the output and discarded the entire
subject of the clip. Worse than the problem it was meant to solve.

Fixed by the rule in docs/DECISIONS.md D31: a face below
`render.min_subject_face_ratio` with no separate content region means the whole
frame is the content, so `blurred_fit` keeps all of it.

Only clip 005 changed. Clips 001-004 stayed `follow_crop` and inspection
confirms that is correct -- the video cuts to full-screen studio shots for those
moments, where the speaker genuinely is the subject and fills the frame.

### What I could not separate, stated plainly

Clip 005 is a genuine **wide two-shot**, not an overlay. It now gets
`blurred_fit` when a stacked treatment would arguably have been more engaging.
I tried to distinguish the two cases and the obvious signals do not separate
them:

| | face width | distance to nearest frame edge |
|---|---|---|
| Wide two-shot (clip 005) | 7.4% / 8.7% | 0.29 / 0.22 |
| Reaction-cam overlays | 6.5-7.7% | 0.10-0.21 |

The ranges overlap on both axes. Doing this properly means detecting that a
region is *composited* -- a hard circular or rectangular boundary whose contents
are unrelated to the surrounding pixels -- which was not attempted.

So the rule is deliberately conservative, and the cost is asymmetric: letterboxing
a two-shot that could have been stacked loses a little engagement, while cropping
to an overlay avatar loses the content entirely. `render.min_subject_face_ratio`
exposes the line for tuning.

### Other observations

- **No heatmap again**, despite 2.6M views and three weeks since upload. Two
  real sources, neither with heatmap data. The signal degrades cleanly both
  times (weights renormalised to llm 0.62 / audio 0.19 / text 0.19), but its
  practical availability on recent uploads now looks doubtful enough to matter
  for Phase 5, which depends on it entirely.
- **45% of candidates hard-dropped** for needing prior context (27 of 60),
  against 77% on the chess source. Lower, as expected for a narrative reaction
  format, and still substantial.
- 9714 windows enumerated, 8467 surviving filters, capped to 60 — so the cheap
  pre-score again chose the final set from **99.3%** more candidates than the
  LLM ever saw.

---

## 2026-09-22 — Scene-cut detection and per-shot framing

Prompted by user feedback with two screenshots of clip `002_22m58s`. Both were
traced to the same cause by extracting the exact source frames they came from.

### What the screenshots showed

`002` was rendered as a single `follow_crop`. Its source span contains two
compositions:

| source time | composition | largest face |
|---|---|---|
| 0.00 - 8.55s | wide two-shot, speakers at the frame edges, b-roll box between them | 18.9% of frame width |
| 8.55 - 35.43s | close-up of one speaker, graphic overlays on the left | 23.3% |

The close-up supplied 129 of 170 face samples, so the single crop was planted at
x=745 (608px wide). Over the wide shot that column contains the wall between the
two people, the book on the table and their inner arms — and neither face. That
is exactly the frame in the first screenshot.

The second screenshot's clipped text was confirmed to be the **source's own
burned-in graphic** ("BOTFLY REMOVED FROM PAUL'S BACK"), sliced by our crop, not
a caption of ours running off the frame. Our caption was correctly centred and
inside the safe area in both frames.

### Why the cut was missed

Cuts were compared with a single hue/saturation histogram of the whole frame.
Measured on that span: the 8.55s cut scored **0.752**, against a 0.70 threshold
— so it was not a cut. The whole 35-second clip was reported as having **one**
cut, at a b-roll insert, and not the real shot change.

A 4x4 grid of per-tile luminance histograms scores the same cut at **0.512**.
Swept across all nine clip spans of both real sources (1733 sample pairs):

| source | spans | median | minimum | pairs below 0.70 |
|---|---|---|---|---|
| edited podcast `R7duSFu_oeg` | 5 | 0.985 - 0.997 | **-0.175** | 2, 2, 5, 14, 17 |
| static screen capture `LQHVKbJtNuY` | 4 | 1.000 | **0.977** | 0, 0, 0, 0 |

The static source — which genuinely contains no cuts — stays 0.28 above the
threshold across 827 pairs, so this produces no false cuts on it. The choice of
0.70 within the 0.6-0.8 range changes the podcast's cut count by at most two.

### Per-shot framing, measured

With cuts detected, `002` splits into two shots and each is framed on its own:

```
[0]  0.00- 8.55  blurred_fit   a subject-sized face appears in only 15% of frames
[1]  8.55-35.43  follow_crop   one subject spanning 473px fit inside a 608px crop
```

Rendered and inspected: the frame that previously showed a wall and two arms now
shows both speakers, the book and the b-roll insert; the close-up section is
unchanged. Captions stay in sync across the join (checked against the ASS
timings at 3.4s and 22.0s). Output duration 35.50s against 35.43s planned, which
is the usual frame-boundary rounding and matches the single-layout path.

### Render cost

Single FFmpeg pass via `trim` / `setpts` / `concat`, not one render per shot.

| clip | before | after |
|---|---|---|
| `001_14m19s` (27.5s) | 9.8s, `blurred_fit` | 6.9s, 7 shots |
| `002_22m58s` (35.4s) | 7.0s, `follow_crop` | 10.0s, 2 shots |

Not slower in any systematic way; the variation is dominated by which layouts
the shots choose (a `blurred_fit` branch blurs a full-size copy of the frame).

### Known limitation, unchanged

A `blurred_fit` shot still occupies about a third of the output height. Per-shot
framing means fewer shots are letterboxed, but it does not make a letterboxed
shot fill more frame. A moderate crop — to 4:3 or 3:2 rather than all the way to
9:16 — remains unbuilt (D37).

Cropping also still clips the source's own burned-in graphics whenever a shot is
cropped at all, which is inherent: those graphics sit outside any 9:16 slice.
Only keeping the whole frame avoids it entirely.

### Full run on the podcast source, per-shot framing enabled

All five clips now mix framings where the source changes composition. Before,
four of the five were a single `blurred_fit` for their whole length.

| clip | before | after |
|---|---|---|
| `001_14m19s` | `blurred_fit` | 7 shots: 6x `follow_crop` + 1 `blurred_fit` |
| `002_22m58s` | `follow_crop` | 2 shots: `blurred_fit` + `follow_crop` |
| `003_12m27s` | `blurred_fit` | 8 shots: 6x `blurred_fit` + 2x `follow_crop` |
| `004_6m17s` | `blurred_fit` | 3 shots: `blurred_fit` + `follow_crop` + `blurred_fit` |
| `005_26m25s` | `blurred_fit` | 4 shots: `blurred_fit` + `follow_crop` + 2x `blurred_fit` |

All five passed QA (12 checks each). Run total 193.6s, render+QA 3m12s.

Clip `001` is the clearest gain: previously letterboxed end to end, it now cuts
between full-frame close-ups of each speaker and the news b-roll, following the
source's own cuts. Clip `004` — the one reported as "stayed in the middle of
them" — now holds a correctly framed close-up for its middle 29 seconds, with
the wide two-shot kept whole at each end. Verified by extracting rendered frames
at 29.5, 30.5, 31.0 and 32.0s.

### No A/V drift across the concat

Concatenating trimmed branches could drop a frame per boundary, which would
accumulate as video running ahead of audio. Checked on `003`, which has the most
boundaries (8 shots, 7 joins), by detecting where the framing actually changes
in the rendered file and comparing against the planned times:

| planned | observed | delta |
|---|---|---|
| 19.81s | 19.80s | -0.01s |
| 39.62s | 39.70s | +0.08s |
| 51.72s | 51.80s | +0.08s |

The error does not grow with each boundary, so nothing is accumulating; ±0.08s
is the resolution of the check (every third frame). Stream durations across all
five clips: video is 0.00-0.07s short of audio, i.e. at most two frames at the
very end, which the single-layout path also produces.

---

## 2026-09-22 (later) — Static framing, graphic detection, frame-accurate cuts

Prompted by three more screenshots from the per-shot render. Each was traced to
its source frame with the planned crop and detected faces drawn on it.

| screenshot | cause |
|---|---|
| clip 001, 17.1s: listener framed, Dumbo card cut in half | the source shows only the listener here (a reaction shot); the card sat outside the 608px crop |
| clip 003, 24.1s: an ear and the back of a head | the speaker's face swung over 700px; the fallback panning camera trailed him |
| clip 005, 21.8s: subtitle bar unreadable | a 1128px source subtitle bar inside a 608px crop |

### Face containment

Share of face samples fully inside the crop, across every cropped shot of the
five clips:

| | faces fully in frame |
|---|---|
| before: 20%-trimmed static crops + panning fallback | **419/497 = 84%** (panning shots 41% and 68%) |
| after: 5% trim, head padding, frame widens to fit | **491/496 = 99.0%** |

The five remaining samples are the trimmed extremes, by construction.

### Graphic detection

On 13 hand-labelled real frames (5 with graphics, 8 without, including posters,
heads, stadium b-roll and a map): every graphic kind present was found on 12 of
13 frames; the one miss was a single frame of the animated card; **no false
detections on the 8 frames without a graphic**. Per shot, the persistence rule
kept: subtitle bar 33/33 samples, Dumbo card 5/19, circles 42/129 and text
27/129 in the 27-second close-up, nothing in the ear shot.

After the change, all 6 recurring graphics across the five clips sit inside
their shot's frame (6/6).

One false positive was found in the full run and fixed: stadium signage in
b-roll at 20% of frame width widened a shot to 1834px. The text-width cut-off
went from 20% to 25%; real subtitle lines measured 44-59%.

### The three reported moments, re-rendered

Extracted from the new renders at the reported timestamps: the Dumbo card is
whole with the speaker fully in frame; the ear shot shows the whole face; the
subtitle bar reads end to end.

### Frame-accurate cuts

Found while checking the new renders, not reported: at each cut, the first
frames of the new shot kept the previous shot's crop, because cuts were located
at the 5 fps sample that detected them — up to six frames late. Seen as a flash
of a chair and a lap for three frames at clip 004's 31.49s cut (every frame
from 31.0s inspected).

Measured with a checker that counts pairs of large frame-to-frame jumps a few
frames apart in the rendered files (a source cut then a late reframe):

| | pairs across 5 clips |
|---|---|
| cuts at sample time | 27 |
| cuts refined to the exact frame | 19 |

Every lagged reframe at a hard cut is gone (002, 003 at 25.33s and 39.4s, 004 at
31.4s). The 19 that remain were inspected frame by frame at the two worst spots:
they are the source's own whip-pan transitions, where the picture smears across
several frames and a framing change inside the smear is not visible.

### Tests

774 passed, 2 skipped; ruff clean.

---

## 2026-09-22 (evening) — Speaker framing and caption correction

### Speaker framing

Clip 001's stadium interview (8.78-13.95s) now plans as
`follow_crop ... framing the person talking (mouth movement 1.25 vs 0.61)`, and
the rendered frames at 9.1, 11.0 and 13.0s show the player -- the one speaking --
with the interviewer out of shot. Previously the interviewer was framed.

### Caption correction, per model, on the five real clips

| model | latency/call | proposals that survived both guards |
|---|---|---|
| gemini-flash-lite-latest (default) | 0.7s | picture -> pitcher |
| gemini-3.1-flash-lite | 2.5s | picture -> pitcher |
| gemini-3-flash-preview | ~87s, often 503/429 | picture -> pitcher, clap -> cramp, pickies -> piques, bot -> bite, through -> to |
| gemini-flash-latest | 503 on 3 of 5 clips, then 429 | (rode -> rowed on one clip) |

Blocked by the sound-alike check: river -> jungle, pickies -> chiggers.
Refused by verification: there -> their (the original was right), rode -> rowed
(ambiguous), does -> do.

End-to-end, default settings: 5/5 clips accepted, 3m43s total (was 3m20s
without correction). With gemini-3-flash-preview preferred and no timeout: 22m.
A 10s timeout on that model was confirmed to give up at 8.8s and 4.5s instead of
hanging; the Gemini API rejects deadlines under 10s with a 400.

The fix appears in the rendered captions ("PITCHER FROM LOS", 5.7s) and in the
report's "Caption corrections" list.

846 passed, 2 skipped; ruff clean.

---

## 2026-09-22 (night) — Audio-checked caption fixes

### Free tier, verified

A 429 from gemini-3-flash-preview reported
`quotaMetric: generativelanguage.googleapis.com/generate_content_free_tier_requests`,
`quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `quotaValue: 20`.
The key is on the free tier. Earlier today the same model answered in ~87s or
returned 503/504; today's 20 were used up in testing.

### Labelled cases

User verdicts: picture -> pitcher (right), clap -> cramp (right),
bot -> bite (wrong), pickies -> piques (wrong); there -> their wrong from context.

The stronger model's real proposals (from the earlier run's cache), replayed
through the final code path with real audio, twice:

| proposal | verdict | outcome | decided by |
|---|---|---|---|
| picture -> pitcher | right | applied | audio heard "I heard the pitcher from" |
| clap -> cramp | right | applied | audio heard "Monkey never cramp." |
| bot -> bite | wrong | kept | audio heard "...from the sand flies"; no "bite" |
| pickies -> piques | wrong | kept | rejected list; audio could not tell them apart |
| does -> do | -- | kept | audio kept "does" |
| through -> to | -- | kept | audio kept "through" |
| rode -> rowed | -- | kept | audio kept "rode" |

Same result on both runs. Audio window 1.2s/0.8s was tried first and sent Whisper
into a repetition loop ("Los Angeles Dodgers" thirty times) on the "clap" case;
2.0s/1.5s did not.

### End-to-end

Default config, stronger model out of quota: it failed immediately each clip and
the fast model proposed. Applied picture -> pitcher; the audio refused there ->
their. 5/5 clips accepted, 4m01s total (3m20s without correction).

858 passed, 2 skipped; ruff clean.
