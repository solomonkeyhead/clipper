# BUILD BRIEF: "clipper", an automated short-form clipping tool

> Source of truth for scope, architecture, and acceptance criteria.
> Re-read at the start of every phase and after any context compaction.

## 0. First message to paste into Claude Code

Read BUILD_BRIEF.md completely. Then run Phase 0 (environment audit) and write PLAN.md summarizing the concrete stack versions you will use, any deviations from the brief you propose (with reasons), and risks you see. After PLAN.md is written, continue into Phase 1 without waiting for me, unless the environment audit finds a blocker. Stop and report at each checkpoint marked CHECKPOINT. Never claim a stage works unless you ran it and saw it work.

## 1. Mission

Build a local, Windows-native command line tool that takes one long-form source video (a local file, or a URL the user is authorized to use) and produces a small set of finished vertical clips (1080x1920 MP4, burned-in captions), ranked by predicted performance, plus a manifest describing each clip.

Nobody judges clips while the tool runs. The tool must:

1. Decide which moments are worth clipping using several independent signals, not one opinion.
2. Cut and format them (9:16 reframing, captions, loudness, encoding).
3. Check its own output with automated QA and drop or replace failures.
4. Improve over time from real view-count feedback that the user logs in a CSV.

Quality over quantity: if only two moments in a video are strong, output two clips. Weak filler clips hurt the user (low views, originality flags), so the tool must be willing to output fewer than requested.

### Who this is for

The user is a college engineering student running paid clipping campaigns (per-1,000-view payouts on platforms like Whop Content Rewards and Vyro). They post clips manually. They are technical, like working code over abstract explanation, and give short corrective feedback. Keep status reports concise and concrete.

## 2. Hard constraints and non-goals

These are firm. If a task seems to require breaking one, stop and ask.

* **Authorized source material only.** Every campaign config must include a `source_authorization` field (campaign name or URL, or a note that the creator explicitly permits clipping). The tool refuses to run without it and prints a one-line reminder that unauthorized reposting risks copyright claims and strikes.
* **No auto-posting.** No TikTok, YouTube, or Instagram upload code, no browser automation of social sites, no account management, no multi-account features.
* **No evasion features.** Nothing designed to defeat spam or bot detection, fingerprint matching, or duplicate detection.
* **Variant renders** exist for A/B testing what performs (for example two caption styles of the same moment). They are not for reuploading the same clip repeatedly. Document this in the README.
* **Secrets stay in `.env`**, which is git-ignored. Provide `.env.example`.
* **Free and local by default.** Paid APIs are optional swappable backends, never required.
* **Windows 11 native.** Do not assume WSL, bash, or Unix paths. Use `pathlib` everywhere.
* **Honesty in reporting.** Do not fabricate benchmark numbers, test results, or file contents. If something cannot be verified on this machine, say so.

## 3. Target environment

* Windows 11, native (PowerShell or Windows Terminal).
* GPU: NVIDIA GeForce RTX 2070 Super, 8 GB VRAM (Turing architecture: supports fp16 and NVENC, does not support bf16).
* Python 3.11 or 3.12, managed with `uv` if available, otherwise `venv` + `pip`.
* FFmpeg (full build with libass and NVENC support) on PATH.
* The user has no paid API budget for this tool. Default LLM backend is a free tier (see section 9).

VRAM is shared across stages. Run GPU stages sequentially and free GPU memory between them (transcription, then optional local LLM, then face detection).

## 4. How to work

* Work in the phases in section 15. Each phase ends with a commit and a short report.
* Initialize a git repo in Phase 0. Commit at the end of every phase with a clear message.
* Write tests as you go, not at the end. Run the full test suite before each commit.
* Prefer boring, well-maintained libraries. Pin versions in `pyproject.toml` after verifying they work together on this machine.
* Verify current facts at build time instead of trusting memory: faster-whisper CUDA/cuDNN requirements, current free-tier model names and rate limits for the default LLM backend, yt-dlp's `heatmap` field behavior, MediaPipe/OpenCV API versions. Record what you verified in `docs/VERIFIED.md` with dates.
* Make every pipeline stage resumable and cached (see section 6). Re-running with no changes should be near-instant.
* Ask the user a question only at a marked decision point or when blocked. Otherwise make a reasonable choice and log it in `docs/DECISIONS.md`.

## 5. Repository layout

```
clipper/
  BUILD_BRIEF.md
  PLAN.md
  README.md
  pyproject.toml
  .env.example
  .gitignore
  config/
    default.yaml
  campaigns/
    example.yaml
  docs/
    ARCHITECTURE.md
    DECISIONS.md
    VERIFIED.md
    TUNING.md
  src/clipper/
    __init__.py
    cli.py                 # Typer app
    config.py              # pydantic models for global + campaign config
    doctor.py              # environment self-check
    models.py              # Word, Sentence, Candidate, Scores, ClipPlan, etc.
    ingest/
      download.py          # yt-dlp wrapper, local file handling, heatmap extraction
      probe.py             # ffprobe wrapper
    transcribe/
      whisper.py           # faster-whisper wrapper
      segment.py           # words -> sentences -> paragraphs
    candidates/
      windows.py           # candidate window generation
      boundaries.py        # sentence/pause snapping, filler trimming
    signals/
      llm.py               # rubric scoring via LLM backends
      audio.py             # energy, pace, dynamics
      heatmap.py           # "most replayed" alignment
      text.py              # hook and structure features
      combine.py           # normalization, weighting, penalties
    llm/
      base.py              # backend interface
      gemini.py
      ollama.py
      anthropic_backend.py
      prompts.py           # rubric prompts (versioned)
      cache.py             # disk cache keyed by content hash
    select/
      pick.py              # greedy non-overlapping selection with thresholds
    render/
      ffmpeg.py            # command builders, NVENC/x264 selection
      faces.py             # face detection + tracking
      layouts.py           # follow-crop, two-speaker stack, blurred fallback
      captions.py          # ASS subtitle generation
    qa/
      checks.py
    campaign/
      compliance.py
      manifest.py
    eval/
      harness.py
      metrics.py
      baselines.py
    learn/
      feedback.py
    utils/
      cache.py
      logging.py
      timecode.py
  tests/
    unit/
    integration/
    fixtures/
  data/                    # git-ignored: downloads/, work/, out/, eval_cache/
```

## 6. Pipeline overview

Stages run in this order. Each stage reads and writes JSON artifacts under `data/work/<source_id>/` so it can resume.

```
ingest -> transcribe -> segment -> candidates -> signals -> combine -> select -> refine -> render -> qa -> manifest
```

1. **Ingest.** Accept a local path or URL. For URLs use `yt-dlp`, saving to `data/downloads/`. Capture the info dict, including the `heatmap` field when YouTube provides one ("Most replayed" data, list of segments with start_time, end_time, value). Run `ffprobe` for duration, resolution, fps, audio streams. Extract 16 kHz mono WAV for analysis. Artifact: `info.json`, `audio.wav`.
2. **Transcribe.** faster-whisper with word timestamps and VAD filtering. Artifact: `transcript.json` (words with start, end, text, probability).
3. **Segment.** Group words into sentences using punctuation and pause length (pauses over ~0.6 s force a boundary). Group sentences into paragraphs (topic-ish chunks) using long pauses. Artifact: `sentences.json`.
4. **Candidates.** Generate 30 to 60 overlapping candidate windows (section 8). Artifact: `candidates.json`.
5. **Signals.** Compute four independent signal families per candidate (section 9). Artifact: `signals.json`.
6. **Combine.** Normalize and weight into one composite score with penalties and hard drops. Artifact: `scored.json`.
7. **Select.** Greedy non-overlapping selection above a quality threshold. Artifact: `selection.json`.
8. **Refine.** Snap boundaries, trim leading filler, add padding, extend to finish a thought.
9. **Render.** Reframe to 9:16, burn captions, normalize loudness, encode.
10. **QA.** Automated checks. Failed clips go to `rejected/` with a reason file, and the next-best candidate is rendered to fill the quota.
11. **Manifest.** Write `manifest.csv` and `manifest.json`, plus a blank `performance.csv` template.

**Caching:** hash the source file (size + first/last MB hash is fine) to get `source_id`. Cache LLM calls by hash of (backend, model, prompt version, candidate text). Provide `--force <stage>` to invalidate.

## 7. Configuration

### 7.1 Global config: `config/default.yaml`

```yaml
transcription:
  model: large-v3            # verify; fall back to distil-large-v3 (English) or medium if slow
  compute_type: float16      # fall back to int8_float16 on OOM
  language: auto             # or "en"
  vad_filter: true
  beam_size: 5

candidates:
  min_seconds: 20
  max_seconds: 55
  target_seconds: [25, 45]
  max_candidates: 60
  min_gap_seconds: 30        # between selected clips

weights:                     # renormalized over available signals
  llm: 0.50
  heatmap: 0.20
  audio: 0.15
  text: 0.15

selection:
  top_n: 5
  min_composite: 0.55        # percentile-scale threshold, tune via eval
  max_from_same_third: 3     # spread picks across the video

llm:
  backend: gemini            # gemini | ollama | anthropic
  model: null                # resolved at runtime from verified list in docs/VERIFIED.md
  batch_size: 8              # candidates per call
  max_retries: 5
  temperature: 0.2

render:
  width: 1080
  height: 1920
  fps: 30
  encoder: auto              # auto = h264_nvenc if available else libx264
  loudness_lufs: -14
  caption_style: bold_pop
  safe_area:                 # keep captions clear of platform UI
    top: 220
    bottom: 320
    side: 90

qa:
  min_face_ratio: 0.5        # only enforced for face-centric layouts
  max_silence_ratio: 0.25
  max_black_seconds: 0.3
  min_lufs: -18
  max_lufs: -11
```

### 7.2 Campaign config: `campaigns/<name>.yaml`

```yaml
name: example-campaign
source_authorization: "Whop Content Rewards campaign 'X', source content bank"   # REQUIRED
platform_targets: [tiktok, youtube_shorts, instagram_reels]
duration:
  min_seconds: 15
  max_seconds: 60
required_hashtags: ["#example"]
required_credit_text: "Source: @creator"      # appended to suggested caption
burn_credit_in_video: false                   # some campaigns require an on-screen credit
credit_position: top_left
forbidden_terms: []                           # scanned in the clip transcript; matches drop the clip
mask_profanity_in_captions: true
brand_mentions:
  required: false
  terms: []
language: en
max_clips_per_source: 8
notes: "Free text: campaign rules the user pasted in, for reference."
```

`compliance.py` validates each finished clip against this file and records pass/fail per rule in the manifest.

## 8. Candidate generation (this determines the ceiling of everything else)

* Start from sentence boundaries. For each sentence as a potential start, extend forward over sentences until duration is in `[min_seconds, max_seconds]`. Emit windows that end on a sentence boundary. Emit multiple lengths per start where sensible (for example short, medium, long), then dedupe near-identical windows (IoU above 0.8, keep the higher-scoring later).
* Hard filters before any scoring:
  * Discard windows that begin mid-sentence.
  * Discard windows with more than 25 percent silence or no speech.
  * Discard windows in the first 20 seconds and last 20 seconds of the source unless the source is shorter than 5 minutes (intros and outros are usually low value).
  * Discard windows dominated by sponsor reads (keyword and phrase heuristics, then confirmed by the LLM flag).
* Cap at `max_candidates` by a cheap pre-score (audio energy variance plus text hook heuristics) so LLM cost stays bounded.

## 9. Signals and scoring

Each signal produces a raw value per candidate. Convert each to a per-video percentile rank in `[0, 1]` so signals are comparable.

### 9.1 LLM rubric signal (highest weight, lowest trust on its own)

Score each candidate with two independent prompts (A and B) at low temperature. Batch candidates (default 8 per call). Request strict JSON. Validate with pydantic. On invalid JSON, retry once with a repair instruction, then skip and log.

Per candidate, return integers 0 to 10 for:

* `hook_strength`: would the first 3 seconds stop a scroll?
* `standalone_clarity`: understandable with zero prior context?
* `payoff`: is there a clear point, punchline, reveal, or insight?
* `emotional_intensity`: humor, surprise, controversy, vulnerability, awe.
* `quotability`: contains a line someone would repeat or comment on.
* `ending_completeness`: does it end on a finished thought?

And booleans / fields:

* `needs_prior_context` (bool)
* `is_sponsor_or_ad` (bool)
* `policy_risk` (none | low | high), covering hate, harassment, dangerous claims, sexual content, unmarked medical or financial claims
* `hook_text`: a punchy on-screen hook of 10 words or fewer, faithful to the clip (no invented claims)
* `suggested_caption`: one line, plus 3 to 5 hashtags relevant to the content

**Prompt A (editor voice):**

```
You are a short-form video editor picking moments from a long video for TikTok,
YouTube Shorts and Reels. You will receive a numbered list of candidate clips,
each with its transcript and duration. Score each one on the rubric using
integers 0-10. Be harsh: most clips should score 3-6. Reserve 8+ for moments
that are exceptional. Judge only the text given. Do not invent context.
Return ONLY a JSON array, one object per candidate, matching the schema.
```

**Prompt B (skeptical viewer voice):**

```
You are a distracted viewer scrolling a short-video feed. For each clip
transcript, decide how quickly you would swipe away and why. Score each on the
rubric using integers 0-10, where 0 means you swipe in one second and 10 means
you watch to the end and send it to a friend. Penalize slow starts, missing
context, rambling, and endings that cut off mid-thought. Return ONLY a JSON
array, one object per candidate, matching the schema.
```

**Combine:** `llm_score = mean(A_total, B_total) - 0.25 * |A_total - B_total|`, where `*_total` is a weighted rubric sum (default weights: hook 0.30, clarity 0.20, payoff 0.20, emotion 0.15, quotability 0.10, ending 0.05). Large disagreement between prompts lowers the score.

**Hard drops:** `is_sponsor_or_ad`, `policy_risk == high`, `needs_prior_context` with both prompts agreeing.

**Backends** (all behind `llm/base.py`):

* `gemini`: default, free tier via the official Google GenAI SDK. Verify current free-tier model names and limits at build time and write them to `docs/VERIFIED.md`. Implement exponential backoff on rate-limit errors, a request-per-minute limiter, and clear error messages when the key is missing.
* `ollama`: local model on the RTX 2070 Super. Unload Whisper from VRAM before use. Pick a model that fits in 8 GB and supports reliable JSON output. Verify at build time.
* `anthropic`: optional paid backend using the official SDK. Model name comes from config.

### 9.2 Audio signal

From `audio.wav`, compute per candidate: RMS energy z-score relative to the whole video, energy dynamic range, sudden-increase events (spectral flux peaks), speech rate (words per second from the transcript) and its variance, and pause structure. Keep this cheap (numpy/scipy/librosa). Laughter detection is optional and out of scope for v1 unless a lightweight local model is easy to add.

### 9.3 Heatmap signal (when available)

If `info.json` contains a `heatmap`, resample it to 1-second resolution and compute per candidate the mean and peak value inside the window. Correct for the known bias that replay data is inflated near the start of videos and around commonly linked timestamps: subtract a rolling median (about 3 minutes) before ranking. If there is no heatmap (local files, campaign footage), drop this signal and renormalize the other weights. Log which signals were available.

### 9.4 Text structure signal

Features from the candidate transcript: opens with a question, a numeric claim, a contrast ("but", "however"), a direct address, or a named entity; low filler-word density in the first 5 seconds; presence of a complete question-answer or setup-payoff pattern; sentence-length rhythm; whether the first sentence is self-contained (no dangling pronouns like "that" or "it" with no referent). Use simple, transparent rules plus a small weighted sum. Keep it explainable.

### 9.5 Combine

```
component_i = percentile_rank(raw_i)            # per video, per signal
composite   = sum(w_i * component_i) / sum(w_i)  # over available signals
composite  *= soft_penalties                      # e.g. moderate policy risk, weak hook
```

Write all components and the final composite to `scored.json` so any choice can be explained. Provide `clipper explain <source_id> <candidate_id>` that prints the transcript, every signal value, and why it was picked or dropped.

## 10. Selection and refinement

* Sort by composite. Pick greedily with: no overlap, at least `min_gap_seconds` between picks, at most `max_from_same_third` picks from any third of the video, and stop at `top_n` or when the next candidate falls below `min_composite`.
* Refinement (per selected clip):
  * Snap start to the nearest sentence start; snap end to a sentence end with terminal punctuation.
  * If the first 1.5 seconds are filler ("so", "um", "uh", "like", "you know", "and", "well"), trim to the first content word.
  * Add pre-roll (about 0.15 s) and post-roll (about 0.35 s), but only inside silence, never cutting into a neighboring word.
  * If the last sentence is unfinished, extend up to 8 seconds to complete the thought, respecting the campaign max duration.
  * Enforce campaign duration bounds. If a clip cannot satisfy them, drop it.

## 11. Rendering

### 11.1 Reframing (16:9 to 9:16)

* Detect faces on sampled frames (about 5 fps) using MediaPipe face detection or OpenCV YuNet (verify what installs cleanly on Windows). Track and smooth positions (exponential moving average or Kalman), cap horizontal pan speed, and reset tracking at scene cuts (PySceneDetect or a histogram-difference detector).
* Layouts, chosen automatically per clip and per shot:
  * **Follow-crop**: one dominant face; 1080x1920 crop centered on it with headroom.
  * **Two-speaker stack**: two persistent faces; two 1080x960 crops stacked, captions on the seam, returning to follow-crop when only one face is present.
  * **Blurred-background fit**: no reliable face; source scaled to full width, centered, over a blurred and darkened fill of the same video.
* Rendering approach: default to decoding with OpenCV, applying the crop trajectory per frame, and piping raw frames to FFmpeg for encoding, then muxing audio. If you find a cleaner FFmpeg-only approach that meets the performance budget, use it and log the decision.

### 11.2 Captions

* Generate ASS subtitles (libass) with word-level timing from the transcript. Group into 2 to 4 word chunks, at most 2 lines. Highlight the active word. Styles: `bold_pop` (large, white with thick outline and a colored active word), `clean_white`, `yellow_highlight`.
* Respect the safe area from config so captions avoid platform UI (top and bottom bands).
* Use a font with a permissive license (OFL), bundled or downloaded at setup. Set the fonts directory explicitly so libass finds it on Windows.
* Optional on-screen `hook_text` for the first 2 seconds, and an optional burned credit if the campaign requires it.
* Profanity masking in captions when the campaign config asks for it.

### 11.3 Audio and encoding

* Loudness normalize with `loudnorm` (target from config, true peak about -1.5 dBTP).
* Encode with `h264_nvenc` when available (RTX 2070 Super supports it), with a `libx264` fallback. Output MP4, `yuv420p`, 30 fps, AAC 192 kbps at 48 kHz, `+faststart`.
* Provide a `--draft` flag that renders 540x960 at lower quality for fast iteration.

## 12. Automated QA gate

Run on every rendered clip. Each check returns pass, warn, or fail with a machine-readable reason.

* Duration inside campaign bounds; resolution 1080x1920; fps as configured.
* Audio present; integrated loudness inside the configured range; no clipping.
* Silence ratio under threshold; no long dead air at start or end.
* Black frames or frozen frames beyond the configured limit (`blackdetect`, `freezedetect`).
* Face presence ratio (only for face-centric layouts).
* Caption sync: every transcript word inside the clip appears in the ASS file within tolerance; no caption event outside the clip.
* Starts within 0.3 s of speech onset; ends after the final word plus padding.
* Compliance rules from the campaign config.

Failed clips move to `out/<source_id>/rejected/` with `<clip>.reason.json`. The pipeline then renders the next-best candidate to fill the quota, until candidates run out or fall below `min_composite`.

## 13. Outputs

```
out/<source_id>/
  clips/
    001_<slug>.mp4
    002_<slug>.mp4
  rejected/
  manifest.csv
  manifest.json
  performance.csv        # blank template for the user to fill in
  report.md              # human-readable summary of picks and scores
```

`manifest.csv` columns: `clip_id, source_id, source_title, campaign, start, end, duration, composite, llm_a, llm_b, audio, heatmap, text, layout, caption_style, hook_text, suggested_caption, hashtags, credit_text, qa_status, compliance_status, file, created_at`.

`performance.csv` columns (user-filled): `clip_id, platform, url, posted_at, views_24h, views_7d, views_30d, verified_views, payout_usd, notes`.

## 14. Evaluation and learning

### 14.1 Heatmap eval harness

Purpose: measure whether the composite predicts real audience behavior, with no human labeling.

* Input: `eval/videos.yaml`, a list of URLs (or local files) with `has_heatmap: true`. Prefer Creative Commons-licensed videos or footage the user is authorized to use, and keep downloads local in `data/eval_cache/`. Propose a starter list of 10 to 15 varied long-form videos (podcast, interview, lecture, talk) in `docs/DECISIONS.md` and let the user edit it.
* For each video, run the pipeline through scoring only (no rendering), then compute:
  * **Lift**: mean heatmap value (bias-corrected) inside selected clips divided by mean over random windows of equal length.
  * **Precision@k**: fraction of top-k picks overlapping the top decile of the corrected heatmap.
  * **Spearman correlation** between composite score and window mean heatmap across all candidates.
* Baselines to compare against: random windows, LLM-only, audio-only, text-only, and each signal ablated.
* Weight tuning: grid search or simple regularized regression over signal weights on a train split of videos, evaluated on a held-out split. Report validation numbers only. Do not tune to hit a target. Write `eval/report.md` with tables and a plain-language reading of the results, including where the tool is weak.
* Known limit to state in the report: heatmap data reflects replay behavior on YouTube, which is a proxy for short-form performance, not the same thing.

### 14.2 Performance feedback loop

* `clipper learn --performance performance.csv` reads the user's logged results.
* With fewer than about 20 rows, report descriptive stats only and change nothing.
* With enough rows, compute rank correlation between each sub-score and views (24h and 7d), with confidence intervals, and propose new weights using shrinkage toward the current weights (small steps). Print the proposal and the evidence. Apply only with `--apply`, and keep a history file `config/weights_history.json`.
* Build `data/examples/top_clips.jsonl` from the best-performing clips (transcript, hook, views) and optionally inject 3 to 5 as few-shot examples into the LLM prompts, controlled by a config flag. Version the prompts so cached scores are invalidated when prompts change.
* Warn clearly about small-sample noise. Never present a trend as proven from a handful of clips.

## 15. Phases, acceptance criteria, checkpoints

### Phase 0: Environment audit and scaffold

* Run `nvidia-smi`, `ffmpeg -version`, `ffmpeg -encoders | findstr nvenc`, `python --version`. Report results.
* Install missing prerequisites or give the user exact commands (FFmpeg, Git for Windows, CUDA runtime pieces required by the transcription library). Verify the CUDA and cuDNN requirements of the current faster-whisper / CTranslate2 release against this machine and record them in `docs/VERIFIED.md`.
* Scaffold the repo (section 5), `pyproject.toml`, `.gitignore`, `.env.example`, pre-commit or ruff config.
* Implement `clipper doctor` that checks: Python version, FFmpeg + libass + NVENC, GPU visibility from the transcription library, yt-dlp version, API key presence, font availability, free disk space. It must print clear fix instructions per failure.
* Write `PLAN.md`.
* **Acceptance:** `clipper doctor` runs and reports accurately on this machine. Initial commit exists.

### Phase 1: Media plumbing (proven with synthetic video)

* Implement `ffmpeg.py`, `layouts.py`, `captions.py`, `probe.py`, `timecode.py`.
* Create synthetic test fixtures with FFmpeg (test pattern video, sine or speech-like audio, moving colored shapes standing in for faces) so rendering is testable without copyrighted footage.
* Implement cutting, follow-crop with a scripted trajectory, two-speaker stack, blurred fallback, ASS captions, loudnorm, NVENC and x264 paths, draft mode.
* **Acceptance:** unit tests for crop math, ASS generation, filter/command construction, and Windows path/escaping (drive letters and spaces in paths). Integration test renders a 10-second synthetic clip in all three layouts and passes ffprobe and QA checks. Extract a few frames from each output and inspect them visually to confirm caption placement and crop.

### Phase 2: Transcription and candidates

* Implement `download.py` (yt-dlp, local files, heatmap capture), `whisper.py`, `segment.py`, `windows.py`, `boundaries.py`.
* Test with a short freely licensed video or a locally generated speech sample.
* **Acceptance:** transcript JSON with word timestamps; sentence segmentation tested on fixtures; candidate windows always start and end on sentence boundaries; measured transcription speed on this GPU reported honestly (real-time factor).

### Phase 3: Signals, combine, select

* Implement all four signals, the LLM backends with a mock backend for tests, caching, batching, retries, and `combine.py`, `pick.py`, `explain`.
* **Acceptance:** with the mock backend, the full scoring pipeline is deterministic and tested. With the real default backend and a real API key, one real video runs end to end through scoring and `clipper explain` output looks sensible. Report LLM call counts and latency.
* **CHECKPOINT 1:** report results and any deviations. Continue unless blocked.

### Phase 4: End-to-end `run`

* Wire everything: `clipper run <source> --campaign <yaml> --top N --out out/`. Implement refinement, rendering, QA gate with replacement, compliance checks, manifest, and `report.md`.
* **Acceptance:** on a real 30 to 60 minute source video (freely licensed, or the user's authorized footage), the tool produces at least 3 QA-passing clips. Performance budget: transcription plus scoring plus rendering under 25 minutes wall clock for a 60-minute source on this machine, excluding LLM rate-limit waits. Report the actual numbers. If over budget, profile and optimize the top bottleneck, or explain why not.
* **CHECKPOINT 2:** show sample outputs (frame grabs, manifest excerpt) and timing.

### Phase 5: Eval harness and tuning

* Implement `eval/` (section 14.1) and run it on the starter set.
* **Acceptance:** `eval/report.md` with lift, precision@k, correlation, baselines, ablations, and train/validation split. Apply tuned weights only if validation results support it; otherwise keep defaults and say so.
* **CHECKPOINT 3:** report results honestly, including if the composite does not beat baselines. If it does not, propose specific changes and try one or two.

### Phase 6: Campaign layer and feedback loop

* Implement compliance checks, variant renders (A/B caption styles, off by default), `performance.csv` handling, and `clipper learn`.
* **Acceptance:** unit tests for compliance rules and `learn` (with synthetic performance data, including the under-20-rows behavior).

### Phase 7: Hardening and docs

* Error handling for: missing API key, rate limits, download failures, GPU out-of-memory (auto fall back to `int8_float16` or a smaller model), corrupt media, very long sources (6+ hours: chunked transcription), no-speech sources (exit cleanly with a message).
* `README.md` with Windows setup from zero, usage examples, config reference, troubleshooting, and the authorized-source policy. `docs/ARCHITECTURE.md`, `docs/TUNING.md`.
* **Acceptance:** a fresh clone on this machine can follow the README to a working run. Full test suite green. Final commit and tag `v0.1.0`.

## 16. CLI surface

```
clipper doctor
clipper run <source> --campaign campaigns/x.yaml [--top 5] [--out out/] [--draft] [--backend gemini|ollama|anthropic]
clipper transcribe <source>
clipper candidates <source_id>
clipper score <source_id>
clipper explain <source_id> <candidate_id>
clipper render <source_id> [--clips 1,3,4]
clipper eval [--videos eval/videos.yaml]
clipper learn --performance performance.csv [--apply]
```

Use Typer. Every command supports `--verbose` and writes structured logs to `data/logs/`. Progress bars via `rich`.

## 17. Windows-specific gotchas to handle deliberately

* FFmpeg `subtitles`/`ass` filter paths: a drive-letter colon and backslashes break filter parsing. Use a temp working directory with relative paths, or escape correctly, and test with a path that contains spaces.
* Subprocess encoding: force UTF-8 for FFmpeg and yt-dlp output; captions may contain non-ASCII characters.
* Long paths and OneDrive-synced folders: keep `data/` outside synced folders by default and warn if inside one.
* CUDA runtime DLL discovery for the transcription library: verify the library actually runs on GPU (not silently on CPU) and have `doctor` prove it with a tiny test.
* Antivirus or Defender slowing FFmpeg pipes: note in the troubleshooting docs.
* Multiprocessing on Windows uses spawn: guard entry points with `if __name__ == "__main__":` and avoid heavy global imports in worker modules.
* Terminal quoting: examples in the README should use PowerShell syntax.

## 18. Testing strategy

* **Unit:** sentence segmentation, window generation invariants (start/end boundaries, duration bounds, no overlaps after selection), percentile normalization, weight renormalization when signals are missing, refinement (filler trimming, padding without clipping words), ASS output, compliance rules, heatmap bias correction, feedback math.
* **Integration:** synthetic-video renders in all layouts; full pipeline with the mock LLM backend on a generated speech fixture; QA gate rejecting a deliberately broken clip (black frames, silence, bad duration).
* **Smoke:** one real-source run documented in `docs/VERIFIED.md` with dates and machine details.
* Use property-style tests for invariants where cheap. Keep the suite fast (under about 2 minutes excluding the marked slow tests).

## 19. Reporting format at each phase end

Keep it short:

1. What was built (bullets).
2. Tests run and results (paste the summary line).
3. Measured numbers (speed, VRAM, timings, eval metrics), clearly labeled as measured or estimated.
4. Deviations from this brief and why.
5. Known issues and next step.

## 20. Definition of done

* `clipper run` produces ranked, captioned, QA-passing 9:16 clips plus a manifest from a real long video, with no human judgment step.
* The tool can say why each clip was chosen (`explain`) and how much to trust the ranking (eval report).
* Free-tier default works end to end; paid backends are optional.
* All hard constraints in section 2 hold: authorized-source guard present, no posting or evasion features.
* Tests pass, README lets a fresh setup succeed on Windows 11 with an RTX 2070 Super.
