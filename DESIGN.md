---
name: Clipper Studio
version: 1
direction: Poster Wall (D215)
source: src/clipper/studio/web/src/styles.css
sidecar: .impeccable/design.json
colors:
  dark:
    bg: "#0C0E12"
    surface-1: "#13161B"
    surface-2: "#1B1F26"
    surface-3: "#262B33"
    border: "rgb(255 255 255 / 0.07)"
    border-strong: "rgb(255 255 255 / 0.15)"
    text: "#ECEDEF"
    text-muted: "#A6ABB4"
    text-subtle: "#8A909A"
    accent: "#FFC229"
    accent-hover: "#FFD15C"
    accent-fg: "#17110A"
    accent-ink: "#FFC229"
    focus-ring: "#ECEDEF"
    money: "#52D08F"
    success: "#D5D9DF"
    warning: "#FF8F57"
    danger: "#FF6464"
    info: "#8DB8FF"
    scrim: "rgb(5 6 8 / 0.62)"
  light:
    bg: "#E8EAEE"
    surface-1: "#F4F5F7"
    surface-2: "#FFFFFF"
    surface-3: "#DCDFE4"
    border: "rgb(16 19 24 / 0.10)"
    border-strong: "rgb(16 19 24 / 0.20)"
    text: "#101318"
    text-muted: "#474D57"
    text-subtle: "#5A616C"
    accent: "#F5B30F"
    accent-hover: "#E6A700"
    accent-fg: "#17110A"
    accent-ink: "#8A5900"
    focus-ring: "#101318"
    money: "#0B7A45"
    success: "#2A2F37"
    warning: "#B4470C"
    danger: "#C42B2B"
    info: "#1F62B8"
    scrim: "rgb(16 19 24 / 0.45)"
typography:
  display: "Sofia Sans Condensed Variable"
  body: "Sofia Sans Variable"
  mono: "JetBrains Mono"
  scale: { xs: 13px, sm: 15px, base: 15px, md: 17px, lg: 21px, xl: 26px, 2xl: 32px, 3xl: 40px }
  body-size: 15px
  body-line-height: 1.45
  page-title: "clamp(2.1rem, 3.6vw, 3.1rem), .cap, 800, upper case, line-height 0.92"
  row-head: "19px, 800, upper case, tracking 0.03em, line-height 1"
  metric: "2.1rem, .num, 800, tabular"
rounded: { sm: 4px, md: 6px, lg: 6px, xl: 8px }
spacing:
  wall-row-gap: 14px
  poster-width: "148px, 164px from sm"
  grid-poster-min: 184px
  page-header-bottom: 32px
  touch-target: 44px
motion:
  durations: { instant: 80ms, fast: 110ms, base: 140ms, enter: 180ms, exit: 120ms, panel: 240ms }
  ease-standard: "cubic-bezier(0.2, 0, 0, 1)"
  ease-decelerate: "cubic-bezier(0.16, 1, 0.3, 1)"
  ease-accelerate: "cubic-bezier(0.3, 0, 0.8, 0.15)"
---

# Clipper Studio design system

Recorded from the shipped build of the Poster Wall redesign (D215). Tokens live in
`src/clipper/studio/web/src/styles.css`; components in `components/ui.tsx` and `components/wall.tsx`.
Where this file and the code disagree, the code is right and this file is out of date.

## 1. Overview

The work is a wall of vertical posters: your clips and Shorts, 9:16, in rows by status, like a streaming
catalog. The page around them is flat and quiet so the artwork carries the colour. The poster in focus lifts,
takes the amber ring and says what a click does, while the rest of its row steps back.

Dark is the default (`:root`); light is the same wall by day (`:root.light`). Both are first class: every token
has a light value, and amber and money change shade by day to keep 4.5:1.

## 2. Colors

**The posters are the only colour.** The ground, surfaces and text are blue-black and cool greys (by day,
cool daylight greys). Surfaces step up by lightness (`surface-1` to `surface-3`), never by borders or shadows.

**One amber, only for the next action (D120).** `accent` fills the primary button and draws the ring on the
poster in focus. Nothing else is amber: not tags (`Chip tone="accent"` renders plain `text-fg`), not links
(`.link` is the text colour, underlined), not the nav, not selected toggles. `accent-ink` is amber as words or
an icon on the ground (the input caret); by day it turns deep ochre `#8A5900` so it still reads.
One primary button a page (D211).

**Money has its own green.** `money` is used only for money: the "paid" amount in the top bar
(`AppShell`), the money lines on the Dashboard ("actually paid"), `Metric tone="money"`, and pay rates and
earnings on Campaigns and Stats. "Done" and "good" are not money: `success` is plain strong text with its icon.

**Status words have tones**, as text only: Ready to post `info`, To submit `warning`, Submitted `success`,
Skipped and Waiting for views neutral `text-muted`. `danger` is for destructive words (Delete in a menu,
the danger button's text).

**Keyboard focus** is a 2px `focus-ring` outline (the text colour, offset 2px), not amber. The amber ring
belongs to the poster in focus.

Text contrast: `text-subtle` is 4.5:1 or better on every surface in both themes.

## 3. Typography

- **Sofia Sans Condensed** (`--font-display`), tall and upper case, for titles and row names.
  - Page title: `PageHeader` / `CaptionTitle` with `.cap`: weight 800, upper case, line-height 0.92,
    `clamp(2.1rem, 3.6vw, 3.1rem)`.
  - Row name: `.row-head`: 19px, 800, upper case, tracking 0.03em.
  - `h2`, `h3`: condensed, 700, set in the base layer so a heading's own classes win.
  - Big numbers: `.num`, condensed 800, tabular (scores on posters, metrics, the paid amount).
- **Sofia Sans** (`--font-sans`) for everything else. Body 15px, line-height 1.45, tabular numbers throughout.
  Labels and metadata 13px (`text-xs`), usually semibold.
- **JetBrains Mono** for key caps (`Kbd`) and code.

Ramp: 13, 15, 17, 21, 26, 32, 40px. Metadata is plain text joined by dots ("9.1 · 0:41 · Ready to post").

## 4. Layout and spacing

- App shell: a sidebar nav (active item is a `surface-2` slab with `text-fg`), a 64px top bar with search,
  Ask, the paid money line, the AI status and Sync. On a phone the nav becomes a five-item bottom bar.
- Dashboard: the next clip to post shown large at 9:16 with its title, campaign and the one amber button
  ("Post the next clip"), then rows titled by status: Ready to post (or Also ready to post), To submit, Submitted.
  Under them, "Money and views" as lines of text, not boxes.
- A wall row (`WallRow`): a `.row-head` name, its count in muted tabular text, and "See all" with an arrow at the
  right when there is anything to see. The posters scroll sideways (`.wall-row`: flex, 14px gap, snap one poster
  at a time, no scrollbar). An empty row is one line of text naming the step that fills it.
- The Clips page is the same wall as a grid (`ClipGrid`, `data-wall`): 2 columns on a phone, then
  `auto-fill, minmax(184px, 1fr)`.
- Space separates things, not boxes. Page header has 32px under it.

## 5. Elevation and shape

- Corners are small and exact: 4px on controls (`rounded-sm`), 6px on posters and cards (`rounded-md`),
  3px on the score and length badges on a poster.
- Cards (`Card`) are flat `surface-1` with no border and no shadow. No cards inside cards: what sits inside a
  card is a plain inset or rows of text.
- Shadows are kept for things that float: tooltips (`shadow-2`), the More menu (`shadow-3`), the lifted poster.

## 6. Components

**Button** (`buttonClass`, `Button`): `primary` (amber fill, dark words; when it can't go yet it is grey
`surface-3`, not dim amber), `secondary` (solid grey `surface-3` slab, no outline), `ghost` (muted words, grey on
hover), `danger` (grey slab, red words). Sizes `sm` 32px, `md` 40px, `icon` 36px; all 44px on a touch screen.
A click that returns a promise blocks a second click and shows a spinner at the same size after 100ms (D192).
A link that acts as a button uses `buttonClass`.

**Poster** (`wall.tsx`): `.poster`, 9:16, `surface-2` placeholder, the frame fading in. Score top left in `.num`
and length bottom right, both white on a 70% black badge so they read on any artwork.

**PosterTile**: a button with `data-tile`, the poster, a two-line title (semibold), the campaign in muted text,
and the `.tile-more` line ("Open to post", "Open to submit", "See its views" with an arrow).

**The focus move** (CSS only, `styles.css`):
- The tile in focus is marked `data-focus` (the next one to post, J/K, or the Dashboard's lead clip). It and any
  hovered or keyboard-focused `[data-tile]` lift 3px and take a 2px ring of `bg` then 2px of `accent`, plus `shadow-2`.
- While a tile in a `.wall-row` or `[data-wall]` is hovered or focused, its siblings drop to 50% opacity and the
  default `data-focus` tile gives up its lift. Hover and keyboard win over the default.
- `.tile-more` is always in the layout (opacity 0) so nothing moves; it shows on the tile in focus.
- Reduced motion drops the transitions.
- A built Short waiting to be posted carries `data-focus` on its player in Create the same way.

**Tabs**: underlined words. A `role="tablist"` row with a bottom hairline; each tab 44px tall, semibold 15px,
the current one `text-fg` with a 2px `fg` underline, the rest muted. Counts sit after the word. One `.tabs` / `.tab` class pair (D217): the underline grows from the middle, a hovered tab shows a short grey one. Create's steps
are words with the current one underlined.

**Tags** (`Chip`, `StatusChip`): coloured semibold 13px text, no fill, no border, no pill. A status adds a
6px dot in the current colour.

**Toggles** (filter and reason pickers): square grey slabs, 32px (44px on touch), `surface-3` when on and
muted words when off, with `aria-pressed`.

**Switch**: a 24px track, `surface-3` off and `fg` on, with a hit area reaching to 44px. Neutral, never amber.

**More menu** (`MoreMenu`, `MenuItem`): actions a tile or bar doesn't need in view sit under a ghost button with
the dots icon and the word "More". It opens a `surface-2` panel, 256px wide, `shadow-3`. Each item is an icon and
words in full ("Good: Clipper learns what you like", "Delete (you can undo)"), 40px (44px on touch); a danger
item is red. Picking an item closes it. Used on Clips tiles and Create drafts.

**Metric**: a label in muted 13px over a `.num` value at 2.1rem, set flat on the page, not in a box; money tone
is green. **Sparkline**: grey, or green for money.

**Others**: `Tip` (tooltip that teaches the shortcut, `Kbd` inside, opens on tap for 3s), `EmptyState` (an empty
dashed 9:16 poster beside a title and the step to take), `Skeleton` (shows after 150ms), `BackLink`,
`PageHeader`, the `Mark` (a 9:16 poster with a title bar).

## 7. Touch

Every tap target is 44px on a coarse pointer (`pointer-coarse:`): buttons, menu items, tabs, toggles, See all,
BackLink, top bar items. Switches keep a 24px track with a 44px hit area. A `.link` inside a sentence reaches
10px above and below the line.

## 8. Motion

Durations 80 to 240ms on `ease-standard` and `ease-decelerate`. The poster lift and `.tile-more` use 140ms.
Images fade in over their placeholder. Waiting bars run stripes backwards; a done step pops once. Reduced motion
cuts enter, exit and base to 1ms and stops shimmer, stripes and pops.

## 9. Do and don't

Do:
- Use tokens through the Tailwind names (`bg-surface-2`, `text-muted`, `text-money`); never raw colours in
  components. The exception is the black badges and white words laid over artwork on a poster.
- Keep amber for the one next action per view.
- Put secondary actions under a named More.
- Check both themes and a phone width.

Don't:
- No cards on cards, no boxed metric tiles, no kanban columns.
- No pills for tags, tabs, buttons or toggles. Round shapes stay where the device itself is round: the switch
  track, progress bars and status dots.
- No amber on tags, links, selected toggles, switches or the nav.
- No green for anything but money.
- No icon-only actions that say nothing until hovered; name them, or put them under More.
