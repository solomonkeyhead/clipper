# Manual controls: where Clipper is still automatic-only

The rule (D120): automatic is the default, manual is always there. Every step Clipper does for the
user should show what it did and let them take over, in part or whole, without turning anything
else off. Many users want some steps done for them and others done by hand. A step with no manual
option is a gap, and nothing manual sits behind a plan tier.

How to read the tables: **Done** shipped, **Exists** was already there, **Gap** is automatic-only
today. Effort is a rough size (S under a day, M a day or two, L several days). Order within each table
is the order I'd build them.

## Create (original Shorts)

| Step | Automatic today | Manual option | Status | Effort |
|---|---|---|---|---|
| Script | AI writes it from an idea | Write or paste your own; add, remove, move sentences; edit description and hashtags | **Done** | |
| Physics check | Runs on every AI script | On demand only, never changes your words | **Done** | |
| Picture per sentence | AI plans footage or a diagram | Choose footage by your own search words, a chalk phrase, or a drawing you describe; "Plan pictures" only fills the ones you left alone | **Done** | |
| Your own footage | none | Drop clips; place by hand, in order, or by AI; fill gaps by hand or automatically | **Done** (D119) | |
| Stopping a build | Ran to the end | Cancel while it builds (stops at the next step; a finished version stays); Build again on a finished video | **Done** (D122) | |
| How long a drawing stays up | One picture per sentence, unless the plan holds a drawing | "Keep the drawing above, building on" on any sentence after a drawing | **Done** (D124) | |
| Archiving a posted video | Moves to the Archive once posted (found or marked in Clips) | Archive any video by hand; "Back to Create" keeps it out | **Done** (D131) | |
| Who does each AI job | Gemini first for script, check, review and footage; Claude draws (`llm.create_gemini_jobs`, D136) | Switches in Settings per job | Gap (config only) | S |
| Which stock clip | The AI judge picks one of ~6 | "New footage" opens a picker: searches written for the part, both libraries, every clip scored, best first; pick one (or several for a long part), or let Clipper choose | **Done** (D129) | |
| Redo one picture | "New pictures" redoes all of them | On a built video: per part, "New footage" or "New drawing" (with optional words), undo, and a rebuild that keeps every other part as it was | **Done** (D125) | |
| Diagram contents | Templates and sketches are generated | Edit labels, numbers, arrows and colours of a diagram; move or delete a sketch's words | Gap | L |
| Framing of footage | Faces, else the judge's guess | A slider per shot for where the crop sits; push-in on or off | Gap | M |
| Shot lengths and cuts | Cuts land where each sentence starts; footage over 4.5s is split | Nudge a cut earlier or later; set the longest a shot may last | Gap | M |
| Voice timing | Speech recognition times every word | Nudge a sentence's start; fix a misheard word | Gap | M |
| Captions | One style from settings | Pick the style, position or none per video; edit caption text | Gap | M |
| On-screen hook | The writer's 6-word hook (else the title) for 1.5 s (D155) | Its own text per video in the script editor; length and on/off | Partial | S |
| Script shape and ending | Taken in turn per script (D155) | Pick the shape and ending before writing | Gap | S |
| Editor's read of a script | Another AI lists fixes and the script is rewritten once (D155); shown in the notes | Edit the script; skip the editor | Gap | S |
| Pauses in the voice | Pauses over 0.25 s cut, 0.6 s kept before the last sentence (D155) | Off, or the pause lengths | Gap | S |
| Last shot | The opening footage again under the last sentence, so it loops (D155) | Pick the last picture yourself (any manual pick stops it) | Done | - |
| Camera moves and punch-ins | Moves in turn; punch-in on two highlighted words (D156) | Choose the move per shot; turn punch-ins off | Gap | S |
| Character, sign-off, board colours | Channel settings (D156) | Per video on or off | Partial | S |
| Music and chalk sounds | Off; Clipper's own tune or your tracks folder (D156) | On, off or channel default per video | Done | - |
| Cover frame | Best still picked automatically | Scrub and choose the frame | Gap | S |
| Watermark | Channel setting | On/off and corner per video | Gap | S |
| Loudness and music | Always normalised; no music | Normalisation off or a level; an optional music bed with ducking under the voice | Gap | M |
| Which AI writes | Claude plan, else key, else Gemini | A Settings choice (Claude plan, API key, Gemini), model picker, and the "only Claude" switch (`create_claude_only`) | Gap | S |

## Clipping (campaign clips)

| Step | Automatic today | Manual option | Status | Effort |
|---|---|---|---|---|
| Picking the moments | AI ranks candidates | "I'll pick them" and the editor (start, end, split, cut, add pieces) | **Exists** | |
| How many, how long | `candidates` and `selection` config only | Per job: number of clips, shortest and longest | Gap | S |
| Ranking style | Fixed weights (AI 50%, audience heat 20%, audio 15%, text 15%) | Sliders or presets (funniest, most informative, most energetic), and the score gate | Gap | M |
| Promote a runner-up | Only the top picks are kept | See the next-best candidates and add any | Gap | M |
| Tightening (cut ums and pauses) | One button | Accept or reject each proposed cut | Check (the editor returns the list of cuts; confirm each can be refused) | S |
| Framing | Follows faces; screen-share stacked | Choose which speaker to follow, drag the crop per shot, layout on or off | Gap | L |
| Captions | One style from config; word fixes in the editor | Style, position or none per clip | Gap | M |
| On-screen hook | AI line | **Exists** (editor field and suggestions) | **Exists** | |
| Open on the payoff | Global switch | Per clip, in the editor | Gap | S |
| Cover | Global switch | Choose the frame per clip | Gap | S |
| Post caption and hashtags | Generated from the brief | Editable on the clip; the "Plan (preview)" setting gates "writing your own captions" behind a tier. That contradicts the rule above and should come out. | Check | S |
| Brief rules | AI reads a pasted brief | **Exists** (the campaign editor) | **Exists** | |
| Finding campaigns | Automatic discovery | **Exists** ("New campaign" by hand) | **Exists** | |
| Posting | Auto-post switch (not shipped) | Off by default; every post waits for a click | **Exists** | |

## Where I'd start

1. Swap the stock clip per sentence (the footage judge is the weakest automatic step, and a bad pick
   is the most visible thing in a Short).
2. Captions per video and per clip: style, position, off, edit text.
3. Framing override for footage and clips.
4. The AI choice in Settings, with the "only Claude" switch.
5. Cover frame picker.
6. Cut and voice timing nudges.
7. Diagram label editing.

## For anyone adding a feature

Ask three questions before it ships: what does this do automatically, how does the user see what
it did, and how do they do it themselves, or turn it off, without losing the rest? If the answer to
the last is "they can't", add the manual path in the same change or add a row here.
