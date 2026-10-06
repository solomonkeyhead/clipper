# Clipper

A local app (Windows first; Python + a React page) with two jobs:

1. **Clipping**: long videos in, ranked vertical clips out, for paid clipping campaigns (Whop, Vyro,
   Content Rewards). It reads each campaign's brief, picks the moments, frames and captions them, and
   tracks your posts' views and earnings.
2. **Create**: original Shorts for your own channel, from an idea to a finished video.

## Start

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
clipper doctor     # checks ffmpeg, the GPU and the rest
clipper studio     # the Control Center, at http://127.0.0.1:8765
```

Keys (Gemini, platform apps) go in `.env` (see `.env.example`) or the Settings page. Your campaigns and
settings live in the data folder (`data/` by default), never in the repo.

## Authorized footage only

Every campaign needs a `source_authorization`: the brief, link or permission that covers its footage.
Clipper refuses to clip without one. It reads stats only through each platform's official API and never
scrapes Whop, Vyro or TikTok Studio.

## Docs

| File | What's in it |
|---|---|
| `CLAUDE.md` | How to work in this repo: tests, style, standing rules |
| `docs/HANDOFF.md` | What to check on a real machine, and what's next |
| `docs/DECISIONS.md` | Every design decision, numbered (D1 onwards), newest last |
| `docs/MANUAL_CONTROLS.md` | Steps still automatic-only |
| `docs/HOSTING.md` | Running on another computer or a server |
| `docs/CONNECT_SERVICE.md` | One-click sign-in through the connect service |
| `docs/CAMPAIGN_WATCHER.md` | The email campaign watcher (being replaced by in-app alerts) |
| `docs/BUILD_BRIEF.md`, `docs/PLAN.md`, `docs/VERIFIED.md` | The original brief, plan and measurements (history) |
| `docs/site/` | The public home, privacy and terms pages |
