# Research brief: the market for Clipper, a tool for paid clipping campaigns

## Who is asking and why

I'm building **Clipper** (working name), software for **clippers**: people paid to cut long videos into short vertical clips. The long videos include TV episodes, films, podcasts, streams and creator videos. Clippers post the clips to TikTok, Instagram Reels and YouTube Shorts for **paid clipping campaigns**, such as Whop's Content Rewards and Vyro. Campaigns pay per 1,000 views, usually $0.50–$3. The clipper must follow each campaign's brief (required hashtags, #ad disclosure, length, what to clip) and submit each post's link to get paid.

It works today as a local app on my PC, and I use it for real campaigns. It isn't public yet. The plan is to turn it into a hosted website and only launch once it's the best product in its category. I want an honest, evidence-based view of the market so I know what to build, fix, cut and charge before anyone else sees it.

Be critical. Don't assume my differentiators are real; check whether competitors already do them. Separate evidence (with sources) from your own judgement, and say how confident you are.

---

## What Clipper does today

### Finding and scoring moments
- **Input:**
  - Upload a video, or pick one already on the computer.
  - Or download from a URL, only after checking the video's own license (e.g. Creative Commons from the official channel).
  - Every campaign must record why the user is allowed to clip its footage, or Clipper refuses to run.
- **Transcription:** word-level transcription (Whisper large-v3). An AI proofreads the captions against the audio, and short ranges are re-transcribed when speech is noisy.
- **Moment scoring:**
  - Each candidate moment is scored by an AI rubric: hook, makes sense alone, payoff, emotion, quotability, ending. Two independent prompts are used, with a penalty when they disagree.
  - The rubric is blended with audio energy, text signals and YouTube's "most replayed" data when available.
  - An absolute quality bar decides whether a video has 0, 2 or 10 clips worth making.
  - The campaign's own brief ("only romance moments between these two characters; comedy-only clips get rejected") is part of the scoring.
- **Clip count modes:**
  - Let Clipper decide: every moment above the quality bar, up to the campaign's maximum.
  - Set a number.
  - Hand-pick: type or mark start/end times on a video player.
- **Scores shown to the user:** every clip shows its 0–10 score, its six sub-scores and its rank among all moments in the video.

### Editing
- **Scene-aware cuts** for scripted TV: never cuts inside a scene, detects cuts in dark footage, and trims dead air before the first line and after the last.
- **Reframing to 9:16:**
  - Face detection with per-shot framing, a close-up rule, and keeping everyone in frame for dialogue.
  - Detects screen shares.
  - Fills the screen in the first seconds.
- **Reads the brief's editing rules:** only makes edits the brief allows (e.g. no jump cuts, keep original audio).
- **Podcast tightening:** removes long pauses and filler words, with a loudness check so laughter isn't cut, and adds alternating punch-in zooms to hide the joins.
- **Picture and sound fixes:** brightens very dark footage, skips black first frames, and normalises loudness to −14 LUFS in two passes (unless the brief forbids audio changes).

### Captions and text
- **Burned-in captions:**
  - Word-by-word highlight in sentence case, at most 3 words per page for fast speech, held long enough to read.
  - Placed inside the platforms' safe zones (clear of TikTok/Reels buttons) and moved so they don't cover faces.
- **On-screen hook line** for the first ~3 seconds: from the brief's approved lines, or AI-written.
- **Compliance:**
  - Required hashtags, required caption text (e.g. #ad) and credit lines; "only these hashtags" mode; forbidden terms; profanity masking.
  - Rotates the brief's approved captions.
- **Long description:** an optional searchable 2–3 sentence description written from the clip's transcript, with no invented facts.

### Automatic quality check
Every clip is checked for black frames, length, captions present and compliance. A failed clip is replaced with the next-best moment.

### Control Center (the web app)
- **Dashboard:**
  - Estimated earnings, views, posts, median views, links waiting to be submitted.
  - A "since you were last here" summary.
  - A "Get started" checklist for new users.
- **Campaigns:**
  - Create one by pasting the brief; AI fills in the form (pay rate, dates, length, hashtags, rules, focus) and leaves out passwords and footage links.
  - Edit, rename, archive.
  - A brief checklist on each campaign's page.
- **New clips:** upload or pick footage, choose a mode, and watch a background job with live progress.
- **Clips library:**
  - Filters: Ready to post, To submit, Submitted, Skipped.
  - Download; copy caption; copy each post's link.
  - Mark submitted (with undo); delete to a 30-day trash with undo.
  - Keyboard shortcuts and a command palette.
- **Stats:**
  - Auto-syncs every 15 minutes from TikTok's and Instagram's official APIs: views, likes, comments, shares, saves, Instagram watch time and 3-second skip rate.
  - Each post is compared with the user's median; estimated earnings per campaign.
  - Snapshots over time.
  - Posts are matched to clips automatically by caption, so post links fill in by themselves.
- **Learning:**
  - The user rates clips 1–5 with reasons (weak hook, needs context, boring…).
  - A page shows whether the score agrees with the user's ratings and with real views.
  - Ratings re-weight the scoring rubric and teach the AI the user's taste.
- **Research:**
  - An AI chat that can read the user's own clips, stats and campaigns, search the web and look up top YouTube Shorts.
  - Daily briefs on niches the user follows: trending topics, hook ideas, top Shorts.
  - Saved hooks and ideas.
  - On the top plan, the chat can propose actions (add hook lines, start a clip job, create a campaign) that run only when the user confirms.
- **Accounts:** several TikTok and Instagram accounts per user.
- **Campaign watcher:** new-campaign emails from Vyro are checked by AI for fit and pushed to the user's phone.

## What's planned

- **A hosted website.** The business pays for AI and compute; users just link accounts and add videos. Planned pricing, by minutes of source video per month:
  - Free: 45 minutes, about 5 clips.
  - Starter: $15 for 300 minutes.
  - Pro: $29 for 900 minutes (research chat that can act, auto-post).
  - Agency: $79 for 3,000 minutes with team seats.
- **Posting through the official APIs** (TikTok, Instagram, YouTube), with an auto-post toggle (off by default), a review queue and TikTok's required disclosure flow.
- **A campaign finder:** campaigns from the watcher shown in the app, public campaign announcements found on the web, and a fit check for any pasted campaign link or brief.
- **Possibly:** submitting post links to Content Rewards and Vyro directly, only if their official APIs or terms allow it.
- **Import footage from Google Drive or Dropbox links**, which is how campaigns usually share footage.
- **More edit types:**
  - Fan-style "edits".
  - Split-screen.
  - Emphasis punch-ins.
  - Laughter detection.
  - Speaker-aware L-cuts.
  - Caption contrast boxes.
  - Re-hook checks mid-clip.
  - Duplicate and watermark checks.
- **A/B testing** of hooks and openings.

---

## What I want you to research

### 1. The competitor landscape
Cover at least these, plus any I've missed that matter in 2025–2026:
- **AI clipping tools:** OpusClip, Vizard, Klap, Submagic, Munch, Quso (vidyo.ai), 2short.ai, Choppity, Spikes Studio, StreamLadder, Eklipse, Riverside Magic Clips, Descript, VEED, Captions, and CapCut's auto-clipping.
- **Scheduling and analytics tools clippers use:** Repurpose.io, Metricool, and similar.
- **The campaign platforms themselves:** Whop Content Rewards, Vyro, Clipping.com / ClipFarm-style platforms, and any others. Do any provide their own clipping, tracking or submission tools?
- **Anything built specifically for clippers:** trackers, submission helpers, Discord bots, spreadsheets people sell.

For each competitor:
- Who it's for.
- Pricing and limits: free tier, credits vs minutes, watermark, export quality.
- Core features: moment selection, reframing, captions, editing, posting and scheduling, analytics.
- How good the output actually is, per reviews.
- Speed.
- What users praise.
- What users complain about.
- Why people cancel.

Use G2, Capterra, Trustpilot, Product Hunt, Reddit (clipping, creator and side-hustle communities), YouTube reviews, X and Discord where public. Quote real complaints with links and dates. Tell me which complaints come up most often across tools.

### 2. What clippers actually need
- How big is the clipping-campaign economy right now: number of campaigns, clippers, payouts, typical CPM rates?
- What do clippers realistically earn, and how many clips and accounts do serious ones run?
- What does their workflow and tool stack look like today, step by step, and where do they lose the most time?
- What are their biggest pain points? For example:
  - Finding good campaigns.
  - Brief compliance and rejected submissions.
  - View verification and payout disputes.
  - Submitting links.
  - Reused or unoriginal content flags and account bans.
  - Shadowbans, duplicate content.
  - Footage access.
  - Tracking earnings across campaigns.
- What makes a campaign reject a clip, and what makes a clip do well in campaigns specifically?

### 3. Are my differentiators real?
Go through Clipper's features above and tell me, with evidence, which ones:
- nobody else offers;
- only a few offer, and how well;
- are standard, so not a selling point.

I especially want the truth on these:
- Campaign-brief-aware scoring and compliance.
- Submission tracking.
- Earnings estimates.
- Auto-matching posts to clips.
- Scores the user can see, and learning from their ratings.
- Scene-aware editing for scripted TV.
- The permission gate on edits.
- The campaign finder.

Then list the features competitors have that Clipper lacks, ranked by how much users care.

### 4. Pricing and business model
- Is my planned pricing right for clippers specifically, who get paid per view and are often price-sensitive?
- What do users say about credit and minute systems, free tiers, watermarks, and annual-only pricing?
- Would per-clip, per-minute or percentage-of-earnings pricing fit this audience better?
- Which features are people willing to pay more for?
- How do comparable tools handle free-tier abuse?

### 5. Platform and legal risk
- **Platform rules** on TikTok, Instagram and YouTube about reposted, clipped or "unoriginal" content, and how they detect it. What gets clipper accounts banned or demonetised, and what reduces that risk?
- **Official APIs for posting and stats** (TikTok Content Posting API audit, Meta App Review, YouTube Data API):
  - What approval takes, how long, and what gets apps rejected.
  - Whether a third-party tool can do this for many users.
- **Campaign platforms' terms** on third-party tools and automation, and whether they offer APIs or partner programs.
- **Copyright and liability** for a tool that processes footage users supply, and what terms, DMCA handling and safeguards similar companies use.

### 6. Positioning and go-to-market
- Where clippers gather online, and who they trust (communities, creators, campaign owners).
- How competitors acquired users. Is partnering with campaign platforms or campaign owners realistic?
- What one-sentence positioning would make Clipper stand out? Give me 3 options with reasoning.
- Is the name "Clipper" a problem (existing trademarks, SEO)? Suggest alternatives if so.

### 7. Threats and what to build next
- What could make this product unnecessary? For example:
  - Campaign platforms building their own clipper.
  - CapCut or TikTok adding it.
  - Platforms cracking down on clipping.
- Given everything above, give me a prioritised list:
  - What to build or fix before launch.
  - What to cut because it adds complexity without value. I'm worried the app is getting bloated.
  - What to save for later.

Explain each call.

### 8. Anything else
Anything else you find that would change how I build, price or launch this, especially things I haven't thought to ask about.

---

## How to report back
- Open with a one-page summary of the most important findings and recommendations.
- Include a comparison table: competitors vs Clipper, covering features, pricing, strengths and weaknesses.
- Include a feature gap matrix: what we have, what they have, and how much users care.
- Cite sources inline with links and dates; prefer 2025–2026 sources and say when something may be out of date.
- Mark each finding as evidence or your judgement, with a confidence level.
- Be direct about weaknesses in my plan.
