# PLAN

Phase 0 output, per BUILD_BRIEF.md section 0. Stack as actually resolved on this
machine, proposed deviations with reasons, and risks.

Every number below was measured, not estimated, unless labelled otherwise. The
raw evidence is in [VERIFIED.md](VERIFIED.md); the choices are in
[DECISIONS.md](DECISIONS.md).

---

## 1. Stack

| Layer | Choice | Version | Note |
|---|---|---|---|
| Python | uv-managed CPython | **3.12.14** | Machine had only 3.13/3.14; system PATH untouched |
| Package manager | uv | 0.12.17 | Installed in Phase 0 |
| CLI | typer + rich | 0.27.2 / 15.0.0 | |
| Config | pydantic v2 + PyYAML | 2.13.5 / 6.0.3 | `frozen`, `extra="forbid"` |
| Transcription | faster-whisper / ctranslate2 | 1.2.1 / 4.8.2 | float16 on CUDA, **verified running on GPU** |
| CUDA runtime | nvidia-cublas-cu12, nvidia-cudnn-cu12 | 12.9.2.10 / 9.26.0.51 | pip wheels; DLL dirs registered at import |
| Media | FFmpeg (Gyan full build) | **9.0.1** | libass + fontconfig + harfbuzz; installed in Phase 0 |
| Download | yt-dlp | 2026.08.19 | |
| Vision | opencv-python-headless + YuNet | 5.0.0.93 | `cv2.FaceDetectorYN` verified present |
| Audio DSP | numpy / scipy / soundfile | 2.5.3 / 1.18.1 / 0.14.0 | no librosa |
| LLM | google-genai (default), httpx (Ollama), anthropic (optional) | 2.24.0 | |
| Test / lint | pytest, hypothesis, ruff | 9.1.1 / 6.168.0 / 0.16.8 | |

Prerequisites installed during Phase 0: **FFmpeg** and **uv** (both absent),
**Python 3.12** (absent), and the CUDA 12 runtime wheels.

---

## 2. Environment audit: two findings

### Not a blocker, but the brief is wrong about it: NVENC does not work here

BUILD_BRIEF.md sections 3 and 11.3 both assume NVENC is available because the
2070 Super supports it. The card does. The **driver** does not, against this
FFmpeg:

```
[h264_nvenc] Driver does not support the required nvenc API version.
             Required: 13.1 Found: 13.0
[h264_nvenc] The minimum required Nvidia driver for nvenc is 610.00 or newer
```

Installed driver is 591.86. FFmpeg 9.0.1 is built against NVENC SDK 13.1.
`ffmpeg -encoders` lists `h264_nvenc` anyway — the encoder only fails when you
try to open it. This is exactly the kind of thing that would have surfaced as a
mysterious Phase 4 failure.

Handled: `render.encoder: auto` **probes** by encoding one real frame and caches
the result, then falls back to libx264. Measured fallback cost at 1080x1920/30fps:

| Preset | Speed |
|---|---|
| veryfast | **10.4x** realtime |
| faster | 7.9x realtime |
| medium | 5.9x realtime |

(Synthetic source, so an upper bound.) Five 45-second clips encode in under a
minute even at a third of that. NVDEC decode **does** work, so GPU-accelerated
decoding of the source is still on the table.

To recover GPU encoding: update the NVIDIA driver to 610.00+, or install an
FFmpeg 7.x build. Neither is needed; your call.

### Python 3.11/3.12 was absent

3.14.4 and 3.13.3 were installed, neither in the supported range, and 3.14 has
patchy wheel coverage for this dependency set. Resolved with
`uv python install 3.12` — a standalone interpreter under `%APPDATA%\uv`. Your
system Python and PATH are unchanged.

---

## 3. Deviations from the brief

Six, all small, all logged in DECISIONS.md with reversal cost:

1. **OpenCV YuNet instead of MediaPipe** for face detection (D3). Section 11.1
   allows either; YuNet adds zero dependencies since OpenCV is already required,
   and MediaPipe is the most likely thing in this stack to break on Windows.
2. **Histogram-difference scene cuts instead of PySceneDetect** (D4). Section
   11.1 allows either. PySceneDetect depends on `opencv-python`, which collides
   with `opencv-python-headless`.
3. **No librosa** (D5) — numpy/scipy/soundfile cover every feature section 9.2
   asks for, without numba.
4. **`src/clipper/evaluate/` not `src/clipper/eval/`** (D6) — the brief's layout
   has two different things called `eval`.
5. **Encoders probed, not assumed** (D7) — forced by the NVENC finding above.
6. **Extra config keys** beyond section 7.1: a `refine:` block (section 10's
   constants were hardcoded in the brief), `llm.use_second_opinion`,
   `llm.requests_per_minute`, `llm.rubric_weights`, and `candidates` filter
   thresholds. All were magic numbers in the prose; they are now tunable and
   validated.

---

## 4. Three substantive problems with the brief

You asked me to flag what looks off. These are design issues, not typos — the
first one I think is load-bearing.

### P1. `min_composite: 0.55` cannot do the job the brief gives it

The brief's central quality promise (section 1): *"if only two moments in a video
are strong, output two clips… the tool must be willing to output fewer than
requested."* The mechanism for that is `selection.min_composite`, section 10:
stop when the next candidate falls below it.

But section 9 defines every component as a **per-video percentile rank**, and the
composite as a weighted average of those. Percentile ranks are uniform on [0,1]
*by construction, within every video*. The best candidate in a video scores ~1.0
whether the video is a brilliant interview or an hour of someone reading a phone
book. With 60 candidates and `top_n: 5`, the 5th pick sits around the 92nd
percentile — so a 0.55 threshold can never fire. The gate is decorative.

This isn't a tuning problem. A within-video relative score contains no
information about across-video quality, so no threshold value fixes it.

**Proposed fix**, for Phase 3: keep percentile normalization for *combining*
signals — that is genuinely the right tool for making incomparable signals
comparable — but gate on an **absolute** quantity. Concretely:

- Rank with `composite` (percentile-based) exactly as the brief specifies.
- Gate with `selection.min_llm_total`, an absolute threshold on the raw 0–10
  weighted rubric total. The LLM is scoring against a fixed rubric with an
  explicit "be harsh, most clips should score 3–6" instruction, so its output
  *is* an across-video absolute judgement. That is the one signal that can say
  "nothing here is good."
- Where a bias-corrected heatmap exists, add an absolute floor on it too.
- Keep `min_composite` as a secondary relative guard, defaulting lower.

This is a real behaviour change, so I will implement it in Phase 3, report it at
CHECKPOINT 1, and keep the brief's pure-percentile path available behind config
so the eval harness can measure whether it actually helps.

### P2. The eval harness as specified is circular

Section 14.1 scores the composite against bias-corrected heatmap data, and
section 9.3 makes the heatmap a **weighted input to that same composite** (0.20
by default). Measuring lift of a heatmap-containing composite against the heatmap
is partly measuring the heatmap against itself, and the weight-tuning grid search
in the same section will happily push the heatmap weight up to exploit it.

The listed ablation baselines would show this, but only if you read them
correctly; the headline lift number would be inflated.

**Proposed fix:** the primary eval metric is computed with the heatmap weight
forced to **zero** — measuring whether LLM+audio+text predict replay behaviour,
which is the question actually worth answering. The heatmap-included composite
stays as a separate, clearly-labelled line. Weight tuning for the heatmap term
gets excluded from the grid, or tuned only on a split where heatmap is withheld.

### P3. "Two independent prompts" are not independent

Section 9.1 combines prompt A and prompt B as though they were separate judges.
Same model, same call, same context — their errors correlate, so the mean is
narrower than it looks and `- 0.25 * |A - B|` will fire less often than intended.
It is still a useful hedge against prompt-specific artefacts, just not the
variance reduction the framing implies.

Practical consequence, and the reason I care: the second opinion **doubles** LLM
calls, and on a free tier requests-per-day is the binding constraint. So
`llm.use_second_opinion` is a config flag (default on, per the brief), and Phase
5 will measure whether B actually improves ranking or just costs quota.

Smaller notes, handled rather than escalated:

- Section 9.1's hard drop on `needs_prior_context` requires "both prompts
  agreeing" — undefined when the second opinion is off. Single-prompt mode will
  use the single verdict, and this is written down rather than left implicit.
- Section 12's caption-sync check ("every transcript word appears in the ASS
  file") conflicts with `mask_profanity_in_captions` from section 7.2. Masked
  words will be compared on timing, not text.
- Tuned weights will come from YouTube videos that have heatmaps and be applied
  to campaign footage that does not. That is a real generalization gap and it
  will be stated in `eval/report.md` rather than buried.

---

## 5. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Ranking does not beat the random baseline in Phase 5 | **High** | The honest failure mode for this whole tool. CHECKPOINT 3 reports it plainly if so; P1 and P2 above exist partly to make that measurement trustworthy |
| yt-dlp `heatmap` field changed or is often absent | Medium | Unverified as of Phase 0. The signal is already optional with weight renormalization; Phase 2 verifies and records the real shape |
| Gemini free-tier RPD exhausted mid-run | Medium | Disk cache keyed on (backend, model, prompt version, text); rate limiter; resumable stages; `use_second_opinion` halves calls; mock backend for all tests |
| large-v3 too slow for the 25-minute budget | Medium | Measured in Phase 2. Fallbacks in order: `distil-large-v3`, then `medium`. Config already supports both |
| CUDA OOM with 8 GB shared across stages | Medium | Stages run sequentially and free VRAM between them; auto-fallback to `int8_float16`. Whisper tiny measured at 648 MiB; large-v3 not yet measured |
| Face tracking jitters or cuts to the wrong speaker | Medium | Visual inspection of extracted frames is a Phase 1 acceptance criterion, not a Phase 4 surprise |
| Free-tier LLM refuses or mangles JSON at batch size 8 | Low | Strict pydantic validation, one repair retry, then skip-and-log; batch size is configurable |
| OneDrive touching `data/` | Low | Project root is outside the synced tree; `doctor` warns if `CLIPPER_DATA_DIR` moves it in |

---

## 6. Phase 0 status: complete

Built:

- Repo scaffold per section 5, git-ignoring `data/`, `.env` and downloaded assets.
- `pyproject.toml` with the resolved stack; `ruff` configured and clean.
- `clipper.config` — full pydantic model of both config files, including the
  **authorization gate**: a missing, blank, or placeholder (`TBD`, `n/a`, …)
  `source_authorization` is a load-time error.
- `clipper.paths` — data-root resolution plus the OneDrive guard.
- `clipper.render.ffmpeg` — discovery, UTF-8-safe subprocess wrapper, real
  encoder probing, and Windows filter-path escaping.
- `clipper.utils.cuda` — the `os.add_dll_directory` bootstrap without which
  faster-whisper silently runs on CPU.
- `clipper.assets` + `clipper doctor --fix` — fetches the OFL fonts and YuNet model.
- `clipper.doctor` — 16 checks, each with a concrete fix line.
- `clipper.cli` — full command surface; unimplemented commands exit 2 naming
  their phase.

Verified by running it:

- `clipper doctor`: **12 PASS, 4 WARN, 0 FAIL** (after `--fix`: NVENC driver and
  the unset `GEMINI_API_KEY` remain, both expected).
- `pytest`: **61 passed**, 1.6 s.
- `ruff check`: clean.
- faster-whisper transcribing on the GPU, end to end.
- ASS captions rendered through FFmpeg from a path containing spaces, and the
  output frame **inspected visually** for placement.

Next: **Phase 1**, media plumbing proven against synthetic fixtures — cutting,
the three layouts, ASS generation, loudnorm, draft mode, and frame-level visual
inspection of each layout.
