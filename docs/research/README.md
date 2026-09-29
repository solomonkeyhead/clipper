# Research

- `competitor-research-prompt.md`: the brief given to a research assistant on 2026-09-29.
- The report it produced ("Clipper Market Research: Paid Clipping Campaign Tools", September
  2026) was pasted into the build conversation. Its conclusions and what was acted on are in
  docs/DECISIONS.md D63.

Main findings kept for reference:
- The gap is the campaign workflow, not the edit: brief-aware selection, pre-post compliance and a
  dispute-ready record together. WhopClipper (pre-launch) pitches the same idea; Cut.Pro bundles a
  clipper with its own campaigns.
- Standard elsewhere, so not selling points: visible virality scores, earnings tracking,
  auto-imported posts, campaign discovery (Apify scrapers aggregate Whop, Vyro and others).
- Top clipper pain: rejection after views accrue; Whop lets brands reject only for failing a written
  requirement. Speed matters: budgets drain first-come-first-served.
- Platform risk: Instagram (April 2026) stops recommending mostly-unoriginal accounts; YouTube's
  reused-content policy is channel-wide even with permission; Vyro bans reposting the same clip on
  one account. Reposting-based A/B tests conflict with these.
- APIs: unaudited TikTok clients post only privately (5 users/day); Meta needs App Review and
  Business Verification; unverified YouTube API projects upload private-only, 100 uploads a day.
- Positioning suggested: "The clipping tool that reads the brief so your clips get approved and
  paid." "Clipper" is a crowded, hard-to-trademark name; no trademark search done yet.
- Build before launch (report's list): campaign footage via Drive/Dropbox as the default permission,
  fast post-link capture, dispute pack, scheduling (after audits), duplicate checks, an
  honest-results screen. Done 2026-09-29: dispute pack, fast link capture.
