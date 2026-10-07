# Deep research prompt: making the German Professor channel better (2026-10-07)

Paste everything under the line as one prompt. Paste the report back into Claude Code and it will be turned into
changes to the script prompts, the footage and diagram rules, and the build, and noted in docs/DECISIONS.md.

---

I run a faceless YouTube Shorts channel (also posted to TikTok and Instagram Reels) called the German Professor.
It explains everyday physics in 35 to 55 seconds. I want a research report, with sources and dates, on how to make
it much better. Please research widely and tell me plainly where the evidence is weak.

## What the channel is today

- **Format.** One question a stranger would stop scrolling for, such as "Why do your ears pop on mountain roads?" or
  "Why does your voice sound weird on a recording?". The video answers it in 79 to 129 words, spoken at about 2.3
  words a second.
- **Script shape.** A "why does this happen to you?" question first, then a myth-bust or a dry line ("You say
  centrifugal force. I say careful."), then the physics as a short chain of causes, one everyday comparison, and a
  two-beat deadpan ending.
- **Voice.** A deadpan German-professor character. I record the voiceover myself in the same voice every time, so the
  voice is a human recording, not text-to-speech.
- **Pictures.** Stock footage (Pixabay, Pexels, Coverr, NASA) chosen by an AI that scores thumbnails, and
  chalkboard-style diagrams drawn by an AI and animated. Word-by-word captions with the key word highlighted. A cover
  frame. Roughly one new picture every 3 to 5 seconds.
- **Production.** Scripts are written by an AI from a prompt, then fact-checked by a second AI pass. Ideas come from an
  AI planner. Everything is assembled automatically with ffmpeg.
- **Results so far.** Only about ten Shorts, so the numbers are small and noisy. The ones about things a viewer feels
  in their own body (a car turning, pasta at altitude, an elevator, hearing, your voice on a recording) did two to
  five times the views of ones about objects (batteries, torque, seatbelts). The best single Short reached about 230
  views on YouTube; most get under 10. The channel is new and has few subscribers.

## What I want to learn

Answer each section with concrete, testable recommendations, not general advice. Where you can, give a specific
wording, number or rule I could put straight into a script-writing prompt or an editing rule.

### 1. Why people stay or leave in the first seconds
- What the 2024 to 2026 evidence says about the first 1 to 3 seconds of a Short: the kinds of opening line and opening
  image that hold people, and the ones that make them swipe. Separate platform statements, large studies, and
  creator anecdotes.
- How "viewed vs swiped away", average view duration and average percentage viewed are counted on YouTube Shorts,
  TikTok and Reels now, and which of those the recommendation systems appear to reward. Include the 2025 to 2026
  changes to how Shorts views are counted.
- Whether a question opening, a claim opening, a mid-action opening or a visual-only opening works best for
  educational content, and for faceless channels in particular.

### 2. Script structure and wording
- Best-supported structures for 30 to 60 second explainers: hook, open loop, payoff, ending. How long each part should
  be, and how to keep an open loop from feeling like a cheat.
- Ideal length and pace for educational Shorts. Is 79 to 129 words right? Is 2.3 words a second right? What do the
  best educational channels do, and does a shorter or longer video win on each platform?
- How to write for the ear: sentence length, rhythm, repetition, contractions, one idea per sentence, saying numbers,
  avoiding words that are hard to say or hear. Anything known about "curiosity gap" wording, and where it backfires.
- Endings that cause rewatches and loops, comments, saves or shares. Whether a deadpan sign-off helps or hurts.
- How to keep a recurring character voice funny without it becoming a tic across 50 videos.
- How to turn a physics concept into one everyday comparison that people remember. Examples from the best science
  communicators (Veritasium, Kurzgesagt, Vsauce, Minute Physics, Physics Girl, Mark Rober, Simone Giertz style
  short-form work, and TikTok and Shorts science creators with large audiences), and what they do in under a minute.

### 3. Choosing ideas
- What makes a physics question get clicked: the evidence on topic selection, search demand versus browse demand, and
  "felt in your body" versus "objects". How to find questions people actually search for on YouTube and Google.
- A scoring method I could put in an AI planner prompt to rank ideas before writing them.
- How to plan a series so each video leads to the next, and whether recurring formats ("Myth or physics?") help.
- How to avoid ideas that drift into biology or medicine when the channel is meant to be physics.

### 4. Visuals
- What the research and the best creators say about how often the picture should change, and what the picture should
  be (the thing being talked about, the viewer's own situation, a diagram, a close-up, motion).
- Stock footage versus diagrams versus screen recordings versus animation for explainers. When do chalk-style diagrams
  help understanding, and when do they only look pretty? What makes a diagram readable on a phone in under 3 seconds.
- How to judge whether a stock clip fits a sentence, and common failure modes (generic footage, wrong subject,
  watermarks, vertical crop of wide footage, clips that clash in colour or style).
- Colour, contrast, text size and safe zones for the three platforms' interfaces.
- Whether AI-generated video clips are safe and useful for this, and how platforms treat them.

### 5. Editing, captions and sound
- Caption style that works: size, position, font, how many words at once, highlight colour, animation. What the
  evidence says about captions and retention.
- Pacing and cuts, zooms, punch-ins, transitions, and where they help or annoy.
- Music and sound effects: whether to use them under a voiceover, volume levels, and copyright traps on each
  platform. Loudness targets (LUFS) for Shorts, TikTok and Reels.
- Voiceover quality: microphone and room advice for a home recording, pace, breath, and fixing common problems. Whether
  a human recording beats a synthetic voice for retention and for platform rules.
- Looping: ending a Short so it flows back into its start, and whether it measurably helps.

### 6. Titles, covers, descriptions and hashtags
- Titles for Shorts: length, wording, keywords, question versus statement. Whether the title matters much for Shorts
  at all.
- Cover frames and thumbnails for Shorts on each platform.
- Descriptions, pinned comments, hashtags and on-screen text. What actually affects distribution and what is myth.
- Playlists, series naming, channel page, banner and bio for a faceless explainer channel.

### 7. Growth, distribution and platform rules
- How YouTube Shorts, TikTok and Reels recommend a new, small channel in 2026: test audiences, first-hour signals, how
  long a video keeps getting views, how a good video can be found weeks later.
- Posting frequency and timing for a small channel: is one a day better than three a week? What is the evidence?
- Whether posting the same video to all three platforms helps or hurts, and how to adapt each one.
- Comments and community: replying, pinned comments, using comments as the next idea.
- Subscriber conversion from Shorts, and how to move viewers to longer videos if that is worth doing.
- How to read the numbers I get (views, average view duration, percent viewed, swipe-away rate, likes, shares, saves,
  subscribers) and what to change when each is low. A simple decision table.

### 8. Policy and risk
- YouTube's "inauthentic content" and reused-content rules (updated July 2025) and the Partner Programme review, as
  they apply to a channel with a human voiceover, AI-written scripts, stock footage and AI-drawn diagrams. What has
  caused real channels like this to be demonetised, and what keeps them safe. Also the AI-content labelling
  requirements on YouTube, TikTok and Instagram.
- Fact accuracy: how popular science channels get corrected, how to handle simplifications that are not quite true,
  and how to phrase a simplification honestly in 40 seconds.

### 9. Improving the AI prompts themselves
- Published and practitioner advice on prompting a language model to write short spoken scripts: examples versus
  rules, how many examples, how to stop it copying the examples, how to enforce word count and rhythm, how to get
  genuinely funny deadpan lines, and how to stop typical AI habits (stock phrases, rule-of-three lists, "it's not X,
  it's Y", over-explaining, em dashes).
- Whether to write with one pass or several (draft, critique, rewrite), and how to run a critic that scores a script on
  the things in sections 1 and 2 so the weak ones are rewritten before I see them.
- How to build a small checklist or scoring rubric an AI can apply to every script (hook strength, clarity, payoff,
  length, ending, accuracy) and how reliable AI scoring of this kind is versus real audience data.
- How a tiny channel with little data can still learn: simple experiments I can run with 10 to 30 videos, what to
  vary one at a time, and how many videos make a result trustworthy.

## How to answer

1. Start with a one-page summary: the ten changes most likely to improve retention and growth for this channel, in
   order, each with the evidence behind it and how sure you are (strong, moderate, weak, opinion).
2. Then one section per number above, with specific recommendations and example wordings I can reuse.
3. Include a section called "What I should test first": five experiments for my next 20 videos, what to change in each,
   and what result would tell me it worked.
4. Quote or paraphrase sources briefly and give the link and date. Official platform documentation and published
   data first, then reputable creator analyses, then anecdotes labelled as such. Say clearly when something is not
   known or when sources disagree. Do not pad with general social media tips that apply to every channel.
5. Prefer evidence from 2024 to 2026. Mark anything older.
