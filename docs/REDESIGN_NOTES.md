# Notes for the Studio redesign

Done in D215 (2026-10-13), except the undo after a delete or a skip (problem 5), which needs the server.

From three `/impeccable critique` runs on 2026-10-13 (scores 28, 26, 27 of 40; snapshots in `.impeccable/critique/`).
These are usability problems the new look has to solve, not style wishes. The bugs are fixed (D214).

## What works and should survive
- One status vocabulary (Ready to post, To submit, Submitted) with meanings in tooltips.
- Money kept honest: estimated earnings apart from "Actually paid", the payout minimum spelled out.
- Automatic with manual takeover in the UI (D120): per-part changes, "everything you don't change stays".
- Busy buttons keep their size, blocked buttons go grey, inline confirms instead of modals, reduced motion respected.
- Keyboard: Ctrl K palette, g-chords, J/K, the one-by-one posting queue.

## What the critiques called generic
- Below the caption headings it is a stock dark SaaS dashboard: rounded cards on near-black, a row of four metric
  tiles, a sparkline, a three-column kanban, pill chips.
- The caption idea (a page set like a clip with captions on) stops at the H1.

## Open problems
1. One highlight colour must mean one thing. Lime still marks the Script status pill, heading words, the "Back to
   ideas" confirm, accent chips and Settings links; sage marks "good" in 8+ places that aren't money.
2. Create buries the work in progress: the New Short composer comes first (the whole first screen on a phone), the
   draft below it. On a finished video "Open to post" is a secondary button among Build again, More and four folds;
   the finished moment has no clear next step.
3. Shot review: each of 7-9 rows carries a text field, New footage, New drawing, camera and Adjust. Choose which part
   first, then how.
4. Draft action bar: Approve, Plan pictures, Check physics again, Undo, plus three icon buttons on the card header.
5. Icon-only card actions (archive, lightbulb for "Back to ideas", trash); no undo after delete or skip.
6. Empty states: Dashboard and Stats show grids of dashes and zeros before there is any data. Dashboard repeats the
   to-do counts in Next up and Pipeline.
7. Phone tap targets: 28px controls on Create and Clips, 20px Settings switches and inline links.
8. The AI-problem pill is hidden under 1024px wide. Dev words leak: "camera: auto", the model id in Create's subtitle,
   "made before the new style".
9. Light mode drops the caption outline, so headings lose their character there.
