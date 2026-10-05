# Clipper

A local Windows app (Python 3.11-3.12, FastAPI + a React page) for one user, Marc. Two jobs:
1. **Clipping**: long videos in, ranked vertical clips out, for brand campaigns (Whop/Vyro).
2. **Create** (`src/clipper/create/`, D108-D117): original physics Shorts for Marc's own
   channel, the German Professor (@German.Professor, "Marshal" voice on ElevenLabs).

Marc starts it from a desktop icon (`clipper studio`, the Control Center at
127.0.0.1:8765). It pulls master and restarts itself on start (`selfupdate.py`, D112/D116),
so a push to master reaches him on his next start.

## Working here
- Start with `docs/HANDOFF.md`: what to check on the real machine, and the plan to make Create a
  full video editor for anybody.
- Commit and push straight to `master` (Marc's choice). No pull requests unless asked.
- Tests: `python -m pytest tests/unit -q`. Lint: `ruff check src tests/unit`.
  The diagram tests need `assets/fonts/Caveat.ttf` and `Inter.ttf` (git-ignored; the Google
  Fonts repo on GitHub has both under `ofl/`).
- Renders need Marc's GPU, ffmpeg and his local `data/`; a cloud session can edit and test
  code but can't build his videos. Ask him for the video file to review a build.
- Page changes: check them in a real browser, not only by reading code (D127). `scripts/ui/serve.py`
  runs the real server on test data, `scripts/ui/walkthrough.py` clicks through Create in headless
  Chromium and saves screenshots to look at. Extend the walkthrough for what you changed.
- Every real design change gets an entry at the end of `docs/DECISIONS.md` (D-numbers,
  newest last, short: what happened, what was decided, why). It is 2,000+ lines: grep it
  for a D-number or a word, don't read it whole.
- Code style: match the surrounding code; docstrings say why, with the D-number.

## Design rule
Automatic is the default and manual is always there (D120): every step Clipper does for the user
shows what it did and lets them take over, partly or wholly, and nothing manual sits behind a plan
tier. `docs/MANUAL_CONTROLS.md` lists the steps still automatic-only; add a row when you add a step.

## Standing rules
- Never send Marc's email address anywhere.
- Secrets only in `.env` (git-ignored), never in the repo or in logs.
- Deletions go to the Recycle Bin (`utils/recycle.py`), never a hard delete. Exception (Marc, D133):
  Create's build scratch (`work/`, unused cached shots) is deleted outright; it is rebuilt every time.
- No scraping of Whop, Vyro or TikTok Studio; official APIs only.
- Don't clone the ElevenLabs Marshal voice; Marc makes the voiceover by hand.
- Ask before anything that spends Marc's money or Claude usage (test runs through
  `claude -p` count against his plan's weekly limit).
- Writing to Marc: plain words, no em dashes, never "genuinely", no "it's not X, it's Y".

## Create pipeline (src/clipper/create/)
- `topics.py` ideas -> `script.py` writes the script (beats, each with a visual plan),
  physics check, `replan` for "New pictures" -> Marc records the voice -> `voice.py` times
  words -> `build.py` first has `sketch.py` draw the chalk sketches still missing (marks on a
  1000x600 grid, then a look-and-fix review with the rendered PNG; `fit` scales to the
  board; drawn at build, not at writing, D136), then makes shots:
  `diagrams.py` (templates + sketches, animated with Pillow), `stock.py` (Pixabay/Pexels,
  judged by the model, score >= 7, subject-aware crop) -> ffmpeg assembly, captions, cover.
- AI calls go through `create/ai.py`: Claude first (API key, else Claude Code headless on
  Marc's plan via `llm/claude_code.py`), Gemini only when no Claude is set up
  (`create_claude_only`, D117), except the jobs in `llm.create_gemini_jobs` (script, check,
  review, footage; D136), which Gemini does first. `ask(job=, keep=)`: `keep` remembers an
  answer by its question. `clipper ai-check` shows which AI is used and why.
- Settings: `config/default.yaml` -> `llm.create_model`, `create_quick_model`,
  `create_via_claude_plan`, `create_claude_only`, `create_gemini_jobs`.

## Clipping AI
- Moments are judged (rubric scores, opening lines) by Claude when `llm.judge_model` is set and
  Claude is reachable (API key, else Claude Code on Marc's plan), Gemini otherwise; Gemini always
  watches the video (D139). Settings switch: `claude_judge`.
- Cached stages in `data/work/<source>/` carry a `.key` of the settings that made them
  (`pipeline.stage_keys`); a changed campaign redoes them (D139).

## Nothing of the owner in the repo (D145-D148)
- Campaigns are in `<data>/campaigns`, personal settings in `<data>/config.yaml` over `config/default.yaml`
  (and `config.auto.yaml` from Settings under it). Don't commit either. New defaults must be generic.
- Create channels are `data/create/channels/<slug>.json`, made from niche packs (`create/packs.py`); the
  physics pack's prompts must stay byte-identical (`tests/fixtures/*_physics.txt`).
- Platforms: one list in `platforms.py`; `npm run gen:api` writes the page's copy.
- Running elsewhere or hosted: `docs/HOSTING.md`.

## Where things are
- `src/clipper/cli.py` commands; `studio/server.py` + `studio/create_api.py` the web API;
  `studio/web/src/` the page source (built into `studio/static/`, don't read the build).
- `llm/` model backends; `render/` ffmpeg, captions, faces, cover; `campaign/` campaign rules.
- `campaigns/*.yaml` one per campaign; `german-professor.yaml` is Marc's channel.
