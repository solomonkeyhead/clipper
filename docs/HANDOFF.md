# Handoff: where Create stands, and where it's going

Written 2026-10-04 at the end of a long cloud session (D118 to D131), for the next session, which
Marc plans to run locally on his own machine. A local session can do what this one couldn't: build
real videos on his GPU, play real H.264 stock previews, and reach Coverr's and NASA's servers.

## Audit, 2026-10-08 (D168 to D178; read this first)

A pass over the whole app on Marc's machine. Done and pushed, Clipper restarted on it:
- Create's AI is Claude and Gemini only (D188): a slow or timed-out Gemini model is rested, not asked again; a script is written in the background with its steps on the page.
- Edit controls on a built video (D171), old drawings "Redraw all" (D171).
- A tab left open across an update reloads to the new page; a broken page says so with Reload (D172).
- server.py split into accounts_api, campaigns_api, alerts_api, editor_api (D173).
- Build 119 s to 85 s on the ready-made script with no footage; the cover picker 5x faster, for every clip too
  (D174). Each build logs its stage times: read them on a real build with footage.
- A clip job cut off by a restart shows as Stopped (D175); a full disk is said plainly (D176); an unknown setting
  in config.yaml is ignored with a warning instead of failing every job (D177).

**Check on the real machine:** a real build with footage and Marc's voice (look at the "built ... in Ns: sketches,
shots, assembly, cover" log line), the Adjust panel on Shot review, captions and the cover on a real video.
`scripts/ui/walkthrough.py` has steps for D171 but needs Playwright (a cloud session); they were done by hand here.

**Done after the audit (D179):** drawings in parallel processes, long ones in pieces; footage shots on NVENC; the
cover joined without encoding the video again. A build of the ready-made script: 85 s to 46 s. Check a real build's
log line and that the first frames play right on the phone.

**Proposed, not done:** a Pexels key when Pexels gives them (the current log has 165 "no footage good enough" verdicts; more candidates would mean fewer chalk cards).

## Check these first, on the real machine

Everything below passed the unit tests and the headless-browser walkthrough
(`scripts/ui/serve.py` + `scripts/ui/walkthrough.py`), but none of it has run against Marc's real
data, keys or GPU.

1. **A full build** of the ready-made "Why does your voice sound so different on a recording?" script
   (`create/library/voice-on-a-recording.json`). Watch for: the held face drawing (sentences 3 to 5)
   building as each route is mentioned, stock and drawings mixed, no three drawings in a row.
2. **Change parts** on a built video: New footage (the picker, D129), New drawing, Undo, then a
   rebuild that only remakes the changed parts (D125). The parts list must stay open the whole time
   (the bug of D128).
3. **Footage libraries** (D130): Coverr and NASA were only tested against fake responses written from
   their docs. Search for something in the picker and see that both return clips. If one returns
   nothing, compare its real JSON with `stock.coverr()` / `stock.nasa()`.
4. **Hover preview** plays in Chrome or Edge (the test browser here can't play H.264).
5. **The archive** (D131): mark a built video posted in Clips, open Create, and it should be in the
   Archive. "Back to Create" should keep it out.

## Smaller things offered and not done

Done since: per-part way in, cut nudges, drawing size and place, drawing words, captions off and written per
sentence, the cover frame (D171); the plan gate on post captions is gone (D169). Still open in
`docs/MANUAL_CONTROLS.md`: caption style and position, framing override for footage, diagram arrows and colours,
the longest a shot may last, script shape and ending before writing, skipping the editor's read.

## The big idea: Create for anybody, as a full video editor

Marc's words: "eventually, i want the create thing to be for anybody and not just for me. i want it to
be a full blown video editor."

Today Create is built around one channel and one kind of video. Opening it up means:

**1. Any channel, not only the German Professor.**
- `create/channel.py` holds one profile (persona, script rules, voice, words per second, example
  scripts), with the German Professor's as the default. It needs several profiles, made by the user:
  their niche, their tone, a few of their own scripts to imitate, and which campaign the videos are
  filed under.
- The prompts assume physics: topic ideas (`topics.py`), the physics check (`script.py`), the
  diagram templates (`diagrams.py`: forces, waves and so on) and the drawing prompt (`sketch.py`).
  The check should become a "fact check for this niche", optional, and the templates a library the
  profile picks from. Chalk drawings suit explainers; other niches may want none.
- The voice is made by hand on ElevenLabs and dropped in. Keep that path, and add others: record in
  the page, upload any audio, or no voice at all (text and music only). Never clone a voice
  (standing rule).

**2. A real editor, not only a pipeline.**
Today the user steers the automatic steps (pick a picture per sentence, swap footage per part,
rebuild). A full editor means seeing and moving everything directly:
- A **timeline**: the voice track, one row of shots, captions, music. Drag a cut, trim a shot,
  reorder, split, delete. The shot cache (`build._kept`, keyed by each shot's inputs) already makes
  "change one shot, rebuild fast" work, which a timeline needs.
- **Any media in**: the user's own clips (D119 started this), images, screen recordings, more stock.
- **Text and captions** as editable layers: style, position, timing, per video.
- **Audio**: a music bed with ducking under the voice, levels, normalisation on or off.
- **Framing** per shot: where the 9:16 crop sits, push-in on or off.
- **Live preview** in the page without a full render, then one render at the end. (Today every
  change is an ffmpeg build.)
- **Undo/redo across the whole edit**, and versions to go back to.
- **Export**: sizes for Shorts, TikTok, Reels, and 16:9.
- The automatic way stays the default (D120): "make me a draft" fills the timeline, and every piece
  of it can then be taken over by hand.

**3. For other people, not one person's machine.**
Clipper is a local Windows app for one user, with keys in a `.env`. Other users means: accounts and
each user's own keys or a shared plan, storage per user, renders on their machine or a server's GPU,
and a first-run setup that needs no terminal. `studio/plans.py` already sketches plan tiers; the rule
that nothing manual sits behind a tier still holds.

Suggested order: channel profiles first (smallest change, opens Create to other niches), then the
timeline over the existing build, then live preview, then the multi-user work.

## Rules that carry over

All of `CLAUDE.md`'s standing rules, especially: commit straight to master; ask before spending
Marc's money or Claude usage; deletions to the Recycle Bin; check page changes in a real browser and
extend the walkthrough (D127); every design change gets a D-number at the end of `docs/DECISIONS.md`.

## Local review, 2026-10-04 (first session on Marc's machine; read this before the list above)

Done and pushed (D132, `8359cbf`; settings `0f1f389`): tests (all pass) and ruff clean; `clipper doctor` 16/16
(NVENC, RTX 2070 SUPER); nothing changes under a running build; NASA downloads "large" not "orig";
a clip used twice in a row carries on. Coverr and NASA real replies match `stock.coverr()` / `stock.nasa()`.
`.claude/settings.json` now denies only secrets (`.env`, `.env.*`, `data/*/accounts/**`, `youtube/app.json`,
`*.key`, `*.pem`, `token*.json`); it only limits the Read tool, not Bash.

**Not run yet (each calls the AI, ask Marc first; footage judging is Gemini first, so it costs little Claude):**
a full build of the ready-made script, the footage picker, the hover preview, New drawing, Undo, rebuild.
Video 12 is the earlier build of that script, already in the Archive (never posted: it was archived at
20:14 on Oct 3, the minute the archive code arrived; ask Marc whether he marked it posted).

**Resolved in D133 (Marc's calls, same day): 1, 4, 5, 6, 8.** 2 and 7 were explained to Marc and await his answer;
3: Pexels isn't giving out keys right now. Marc's own list of Create issues: asked for, see below.

**Open concerns, as first written:**
1. 9 Create clips sit in Clips as "ready" (8 are old test builds of two questions). Propose: Recycle
   Bin the 8, keep video 12's, and ask "also remove the clip?" when a video is removed from Create.
2. An automatic archive never undoes itself when the post goes away.
3. No `PEXELS_API_KEY` (free; the code calls it the best for people footage).
4. NASA runs on every footage search (about 5 requests); suggest opt-in or space topics only.
5. Every build sends scratch files to the Recycle Bin (stock folder is 526 MB): delete scratch directly?
6. The footage prompts and physics check assume physics: blocks "Create for anybody".
7. (Done, D134.) The script writer now gets a schema without the app-only Visual fields.
8. Small: filter Coverr's `is_ai_generated`; the first idea in the list duplicates the ready-made script.

**Speed ideas:** done in D179 (parallel drawings, NVENC footage shots, the cover without a second encode).

**Marc's own list of Create issues is still to come** (he waits to tell the better model). Ask for it first.

**Tools:** Ponytail is installed (user plugin; `/ponytail-gain` to check its numbers). Graphify was tried on
`src/` and judged not worth it; its leftover files are gone, and the tool itself can go with
`graphify uninstall` and `uv tool uninstall graphifyy`.

**Cloud session:** it kept pushing to master (last at 20:23 local, dropping the deny list). If still open,
Marc should close it: selfupdate pulls everything it pushes.
