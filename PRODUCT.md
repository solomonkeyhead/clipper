# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Marc, the single owner, running Clipper on his own Windows machine from a desktop icon; the page is the Control Center at 127.0.0.1:8765. Target for later: anybody who wants to make short videos, once Create becomes a full video editor (docs/HANDOFF.md). Design for Marc now without locking out first-time creators later.

## Product Purpose
Clipper does two jobs:
1. Clipping: long videos in, ranked vertical clips out, for brand campaigns (Whop/Vyro).
2. Create: original physics Shorts for Marc's own channel (the German Professor), from idea to script, voice timing, sketches, shots, and a finished video.
Success: a finished, postable vertical video with the least manual work, with every automatic step visible and takeable.

## Positioning
Local, single-user studio where automatic is the default and manual control is always available (D120), with nothing manual hidden behind a plan tier.

## Operating Context
FastAPI server plus a React page (source in `src/clipper/studio/web/src/`, built into `studio/static/`). Self-updates from master on start. Renders use the owner's GPU, ffmpeg and local `data/`. Users review builds, edit scripts and pictures, and record or import a voiceover by hand.

## Capabilities and Constraints
- Every step shows what Clipper did and lets the user take over, partly or wholly (`docs/MANUAL_CONTROLS.md`).
- Page changes are checked in a real browser (`scripts/ui/serve.py`, `scripts/ui/walkthrough.py`).
- Plain-words copy: no em dashes, never "genuinely", no "it's not X, it's Y".
- Platforms come from one list (`platforms.py`); `npm run gen:api` writes the page's copy.

## Brand Commitments
The Create channel is the German Professor (@German.Professor, "Marshal" voice). Nothing of the owner is committed to the repo; defaults must be generic (D145-D148).

## Evidence on Hand
`docs/HANDOFF.md`, `docs/DECISIONS.md`, `docs/MANUAL_CONTROLS.md`, and walkthrough screenshots from `scripts/ui/walkthrough.py`. No external testimonials or user research.

## Product Principles
1. Automatic by default, manual always one click away.
2. Show what was done and why before asking for a decision.
3. Never spend the owner's money or AI usage without asking.
4. Generic by default; the owner's specifics live in local data.
5. Verify in a real browser, not by reading code.
