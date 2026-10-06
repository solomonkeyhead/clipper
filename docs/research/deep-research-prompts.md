# Questions for deep research (2026-10-05)

Each block is a prompt to paste as it is. They are in order of how much the answer would change what gets
built next. Paste the report back into Claude Code and it gets acted on and noted in docs/DECISIONS.md.

Every prompt ends the same way on purpose: dated sources, official docs first, and saying plainly when
something isn't known.

---

## 1. Getting the TikTok app approved

> I run a desktop app ("Clipper") that lets creators connect their own TikTok account to see their own videos'
> stats. It uses TikTok for Developers Login Kit (web platform) with the scopes user.info.basic and video.list,
> through a small web service that holds the app secret and hands the tokens to the user's own computer.
> Research, as of late 2026: (1) exactly what TikTok's app review checks for Login Kit + video.list, and the most
> common rejection reasons with examples from developer forums; (2) whether an "Individual" developer account
> can get these approved for public use or must be an organization; (3) what the demo video must show for an
> app whose interface is a local desktop app and whose website is a GitHub Pages site, and whether a mismatch
> between the website domain and the redirect domain causes rejection; (4) typical review times; (5) whether
> video.list returns view counts for every video, how fresh they are, and the rate limits. Official TikTok
> documentation first, then developer reports with dates. Say clearly when something isn't documented.

## 2. Instagram and Facebook: app review and Business Verification

> For a desktop app that reads a creator's own Instagram Reels stats using "Instagram API with Instagram Login"
> (permissions instagram_business_basic and instagram_business_manage_insights), research as of late 2026:
> (1) what App Review needs for these two permissions, including the screencast; (2) whether Business
> Verification is required, and whether an individual without a registered company can complete it; (3) whether
> Meta accepts an http://localhost or a third-party web-service redirect URI in Live mode; (4) which Reels
> insights exist now (views, reach, saves, shares, average watch time, skip rate) and any deprecated in 2025 or
> 2026; (5) per-account rate limits. Official Meta docs and changelogs first; dated developer reports second.

## 3. Google OAuth verification for YouTube read-only scopes

> A desktop app reads a creator's own YouTube channel stats with the scopes youtube.readonly and
> yt-analytics.readonly, through a web OAuth client. As of late 2026: (1) are these "sensitive" or
> "restricted" scopes, and what verification each needs; (2) is a paid third-party security assessment (CASA)
> required, and what it costs; (3) the unverified-app 100-user cap and the 7-day refresh-token expiry in
> Testing mode; (4) realistic verification timelines and common rejection reasons; (5) what the demo video
> must show. Official Google docs first.

## 4. Why clipping accounts get almost no views on TikTok and YouTube Shorts

> My clipping accounts post the same TV-show clips (a paid clipping campaign) to Instagram Reels, TikTok and
> YouTube Shorts. Instagram gets about 150 views per post; TikTok and YouTube Shorts get close to zero. Research,
> with 2025-2026 sources: (1) how TikTok and YouTube Shorts treat new accounts and reposted or "unoriginal" TV
> footage, including Instagram's April 2026 originality change and YouTube's reused-content and "inauthentic
> content" policies; (2) what edits count as transformative enough for each platform; (3) posting cadence,
> account warm-up and the first-hour signals that decide distribution; (4) whether the same clip on three
> platforms at once hurts any of them; (5) what successful clipping-campaign creators report doing differently.
> Separate platform statements from creator anecdotes, and date everything.

## 5. What makes a short clip hold viewers (for picking and cutting moments)

> I build software that picks moments from long videos (TV episodes, podcasts) and cuts them into 15-60 second
> vertical clips. Summarise the best 2024-2026 evidence (platform creator guides, published retention studies,
> credible creator data) on: the first 1-3 seconds (what kind of opening line or image keeps people), ideal
> length per platform, on-screen text hooks, caption style and size, loops and endings, and cover frames.
> Rank each finding by how strong the evidence is, and say which ones hold for scripted TV clips versus
> podcast clips.

## 6. Official posting APIs, so Clipper can post for the user

> As of late 2026, what does it take to post videos through official APIs for: TikTok Content Posting API
> (audit, the private-only limit for unaudited apps, daily caps), Instagram content publishing (Reels, App
> Review, limits), YouTube Data API uploads (quota cost per upload, the private-only rule for unverified
> projects), X media upload (price)? Also: can a "paid partnership" or branded-content label be set through each
> API? A table per platform with requirements, limits, costs and review times. Official docs first.

## 7. Whop, Vyro and Content Rewards: official APIs for campaigns, submissions and payouts

> For paid clipping campaigns on Whop (Content Rewards), Vyro and similar marketplaces, research as of late
> 2026: is there an official API or webhook to (1) list campaigns a user joined, (2) submit post links,
> (3) read a submission's approval status, and (4) read payouts? Include the docs, auth method, whether
> individual users can get access, and terms that forbid automation. No scraping-based answers.

## 8. A better speech-to-text model for an 8 GB GPU

> I transcribe long English videos locally with faster-whisper large-v3 (float16) on an RTX 2070 Super (8 GB)
> and need accurate word-level timestamps for captions. As of 2026, compare: Whisper large-v3, large-v3-turbo,
> distil-whisper, NVIDIA Parakeet/Canary, and any newer open models. For each: word error rate on
> conversational English, timestamp accuracy, speed and memory on an 8 GB card, Windows support, and licence.
> Recommend one, with the evidence.

## 9. Finding the best moments with AI that watches video

> My app finds the most clip-worthy moments in 20-60 minute videos. It currently sends the transcript to a
> language model to score moments, and sends the video to Gemini to watch them. As of late 2026, research:
> (1) which models can take long video directly, their price per hour of video and their limits; (2) published
> methods for highlight or "viral moment" detection and how well they work; (3) cheap ways to combine
> transcript, audio energy, scene cuts and faces. Cost per hour of video for each approach.

## 10. AI-made educational Shorts and YouTube's monetisation rules

> My own channel posts physics-explainer Shorts: a human voiceover I record myself, AI-written scripts,
> stock footage and AI-drawn chalk diagrams. As of late 2026, what do YouTube's "inauthentic content" (July
> 2025) and reused-content policies, and the YouTube Partner Programme review, say about channels like this?
> What has caused real channels like this to be demonetised, and what keeps them safe? Separate policy text
> from creator reports, and date everything.

## 11. Selling Clipper to other clippers

> I may sell a desktop tool for people who earn money from paid clipping campaigns (Whop Content Rewards,
> Vyro). It reads campaign briefs, picks and cuts moments, checks captions against the rules, and tracks
> views and earnings. Research as of late 2026: competitors (Opus Clip, Vizard, Klap, WhopClipper, Cut.Pro and
> newer ones), their prices and plans, what clippers say they'd pay for, the size of the clipping-campaign
> market, and whether "Clipper" is already a trademark in software. End with a pricing suggestion and the
> evidence behind it.

## 12. Running the sign-in service safely and legally

> I run a small free-tier web service (Render) that passes OAuth tokens for TikTok, Instagram and YouTube
> from the platform to the user's own computer, storing nothing. As of 2026: (1) what privacy policy, terms
> and data-deletion pages each platform requires for this; (2) GDPR and CCPA duties for a service that briefly
> handles tokens; (3) security practices for such a token relay; (4) Render free-tier limits (sleep, hours,
> bandwidth) and better free alternatives. Official sources first.
