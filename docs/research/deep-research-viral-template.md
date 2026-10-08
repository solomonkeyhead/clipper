# Deep research prompt: a viral-ready template for the German Professor (2026-10-07)

The second research prompt, after `deep-research-german-professor.md` (whose report became D155). Paste everything
under the line as one prompt. Paste the report back into Claude Code to turn it into changes.

Correction to the first prompt: it said the voice was a human recording. It is ElevenLabs text-to-speech, one fixed
voice ("Marshal"), made from the approved script.

---

I run a faceless YouTube Shorts channel (also posted to TikTok and Instagram Reels) called the German Professor. It
explains everyday physics and other STEM in 37 to 48 seconds. A first research report already gave me rules for
hooks, script length, endings, idea scoring and basic metrics, and I have put those in. I now want a second report on
what still separates my videos from ones that go viral. Please research widely, give sources and dates, and tell me
plainly where the evidence is weak.

## What the channel is today

- **Script.** 85 to 110 words. An opening question of at most 8 words said to the viewer ("Why does the elevator
  make you heavier?"), the first cause within 20 words, a short chain of causes, one everyday comparison, and a
  deadpan two-beat ending. Three shapes in turn (myth-bust, walk-through, one surprising number) and three endings in
  turn (loop back to the start, "send this to...", a one-word question for the comments). Written by an AI, checked
  by a second AI for facts and a third as an editor.
- **Voice.** ElevenLabs text-to-speech, one voice every time: a deadpan, friendly German professor ("Marshal"). Long
  pauses are trimmed to 0.25 s, with 0.6 s kept before the punchline.
- **Pictures.** Stock footage (Pixabay, Pexels, NASA) chosen by an AI, and AI-drawn chalkboard sketches and simple
  diagrams (arrows, graphs, bars, equations) animated as they are spoken. A new picture every 3 to 5 seconds, no
  shot longer than 4.5 s. Every stock shot has the same slow push-in zoom. No drawing in the first 4 seconds; the
  last sentence plays over the opening footage again so the video loops.
- **Text and sound.** Word-by-word captions with one key word highlighted, a 6-word hook on screen for 1.5 s, a
  small logo. Audio normalised to -14 LUFS. No music, no sound effects, no transitions other than hard cuts.
- **Results.** About ten Shorts, mostly under 1,000 views, the best around 1,300. Videos about something felt in
  your own body clearly beat videos about objects.
- **How it is made.** Everything is assembled automatically with ffmpeg, so any editing rule must be something a
  program can apply from the script, the word timings and the pictures: exact numbers matter more than taste words.

## What I want to learn

Answer each section with concrete, testable recommendations. Where you can, give exact numbers (seconds, percent,
pixels, decibels, frames) and the rule for when to apply each technique, so it can be automated.

### 1. A recognizable character and look
- How faceless explainer channels build recognition so a viewer knows whose video it is within a second: a mascot or
  drawn character, a fixed colour palette and board style, a signature opening shot, a recurring prop, a sign-off
  line, a sound logo. Which of these measurably help subscribe rate or return viewers, and which are only branding
  talk.
- Whether a visual character for the Professor (a drawn or animated figure, a pair of hands, a chalk silhouette)
  would help, how often it should appear, and how successful faceless channels do it without it becoming a gimmick.
- How to keep a consistent look across stock footage from many sources (colour grading, a frame or texture, a
  consistent crop), and whether that matters to viewers.
- How a recognizable format and character interact with YouTube's July 2025 rules on inauthentic, repetitive and
  mass-produced content: what counts as a "format" versus a "template".

### 2. Deadpan comedy that lands
- How deadpan science comedy is built in under a minute: the joke shapes that work (understatement, a literal
  reading, a misplaced formal register, a callback, a rule of escalation, an absurd comparison), how many jokes per
  Short, and where they go (hook, middle, ending).
- Examples from creators who do dry or deadpan science or explainer comedy in short form, and what they do
  consistently. Separate measured findings from creator opinion.
- How a recurring character's running jokes and catchphrases stay funny across 50 or 100 videos: how often to use
  them, when to retire them, and how callbacks to earlier videos reward regular viewers.
- Comedy timing with a text-to-speech voice: how punctuation, sentence breaks and pauses in the text change the
  delivery of a punchline, and what is known about making synthetic voices sound funny rather than flat.
- A short list of joke patterns, with an example of each in this channel's voice, that I could give a script writer
  as patterns to follow rather than lines to copy.

### 3. What the top science Shorts actually do (measured)
- Pick 15 to 25 of the most-viewed science, physics and "how things work" Shorts channels of 2024 to 2026 (faceless
  and on-camera, labelled) and describe what their top videos measurably do: opening line type and length, words per
  second, total length, cuts per 10 seconds, how often text appears on screen, the share of footage versus graphics
  versus a face, music or not, sound effects or not, ending type, posting rate.
- What their best videos have in common that their average videos do not.
- Where a tiny faceless channel can realistically compete and where it cannot.
- Say clearly which numbers you measured or found published and which are estimates.

### 4. Editing techniques: picture
For each, say what it does for retention or clarity, when to use it, when it annoys, and the exact settings:
- Zooms: slow push-ins, punch-ins on a key word, zoom-outs for a reveal. How much (percent), how fast, and how often.
  Is the same slow push-in on every shot a problem?
- Pans and Ken Burns moves on still images and drawings; following a subject across the frame.
- Cut timing: cutting on the word, on the beat, or on the breath; the right shot length for an explainer; how often a
  "pattern interrupt" (a sudden change of picture, scale or colour) should come, and what counts as one.
- Transitions: hard cuts versus whip pans, match cuts, zoom transitions and smash cuts. Which help in educational
  Shorts and which look cheap.
- Speed changes: speed ramps, slow motion on the key moment, freeze frames.
- Text on screen beyond captions: labels, numbers, arrows pointing at the footage, circling or highlighting part of
  the picture, a "pop" when a key term appears. Size, position, how long it stays, and how much is too much.
- Captions: animation style (pop, bounce, typewriter), how many words at once, the highlight word's colour and size,
  and any 2025 to 2026 evidence on caption styles and retention.
- Diagrams: animating chalk drawings so they are readable on a phone in under 3 seconds (drawing speed, how many
  elements, building up versus showing at once).
- The first 2 seconds and the last 2 seconds as edits: what to put on screen, how fast, and how to make the loop
  seamless.

### 5. Editing techniques: sound
- Music under a voice: whether it helps educational Shorts, which kinds (tempo, mood, no lyrics), how loud relative
  to the voice (in dB or LUFS), ducking under speech, and when silence works better.
- Sound effects: whooshes on cuts, pops on text, chalk sounds on drawings, risers before a reveal, a hit on the
  punchline, room tone. How many per Short, how loud, and when they turn into noise.
- A sound logo or signature sound for the channel.
- Copyright and licensing traps for music and sound effects on YouTube Shorts, TikTok and Reels in 2026: platform
  libraries, royalty-free libraries that are safe for monetized channels, and the Content ID risks of each. Which
  free sources are safe to use automatically.
- Loudness targets per platform and how music and sound effects change them.

### 6. Each platform, and turning comments into ideas
- What to change for TikTok and Reels instead of posting the same file: length, hook, captions and on-screen text,
  search keywords in the caption and spoken words, trending or original audio, Instagram's trial reels, cover frames,
  and posting time. Which differences are proven and which are folklore.
- How small channels turn comments into the next video: reply-to-comment videos, pinned questions, polls, and series
  built from viewer questions. Whether these measurably help a channel under 1,000 subscribers.
- What the voice being AI text-to-speech means on each platform: disclosure labels, YouTube's monetization review of
  channels with synthetic voices, and anything that has got channels like this demonetised or limited.

## How to answer

1. Start with a one-page summary: the ten changes most likely to make the next videos travel further, in order, each
   with the evidence behind it and how sure you are (strong, moderate, weak, opinion).
2. Then one section per number above, with specific recommendations, exact settings and example wordings I can reuse.
3. End with an "editing spec": a list of rules a program could apply to every video, each written as "when X, do Y,
   with these numbers", for zooms, cuts, text, captions, music and sound effects.
4. Add "What I should test first": five experiments for my next 20 videos (pairs of videos that differ in one thing),
   what to change in each, and what result would tell me it worked.
5. Quote or paraphrase sources briefly with the link and date. Official platform documentation and published data
   first, then reputable creator analyses, then anecdotes labelled as such. Say clearly when something is not known
   or when sources disagree. Do not pad with general social media tips.
6. Prefer evidence from 2024 to 2026. Mark anything older.
