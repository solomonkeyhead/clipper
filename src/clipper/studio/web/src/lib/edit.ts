/**
 * The editor's edit, and everything you can do to it (D103). Pure functions:
 * each takes an edit and returns a new one, so undo is just a list of edits.
 * Mirrors editing.py, which tidies and checks it again before rendering.
 */

export type Piece = { start: number; end: number; zoom: number };
export type Fix = { at: number; text: string };
export type Edit = { pieces: Piece[]; hook: string; fixes: Fix[] };
export type Word = { start: number; end: number; text: string };

export const ZOOMS = [1, 1.1, 1.2] as const;
const MIN_PIECE = 0.3;
const TOUCH = 0.02;

export const emptyEdit = (hook = ""): Edit => ({ pieces: [], hook, fixes: [] });

export const length = (e: Edit) => e.pieces.reduce((s, p) => s + (p.end - p.start), 0);
export const startOf = (e: Edit) => e.pieces[0]?.start ?? 0;
export const endOf = (e: Edit) => e.pieces[e.pieces.length - 1]?.end ?? 0;

/** Places something was taken out of the clip (a zoom change between touching pieces isn't one). */
export const cuts = (e: Edit) =>
  e.pieces.slice(1).filter((p, i) => p.start - e.pieces[i].end > TOUCH).length;
export const zoomed = (e: Edit) => e.pieces.some((p) => p.zoom !== 1);

export const pieceAt = (e: Edit, t: number) => e.pieces.findIndex((p) => t >= p.start && t < p.end);

/** In order, overlaps merged, slips dropped: the shape editing.py expects. */
export function tidy(e: Edit, duration: number): Edit {
  const out: Piece[] = [];
  for (const p of [...e.pieces].sort((a, b) => a.start - b.start)) {
    let start = Math.max(0, p.start);
    const end = Math.min(duration, p.end);
    if (end - start < MIN_PIECE) continue;
    const last = out[out.length - 1];
    if (last && start <= last.end + TOUCH && p.zoom === last.zoom) {
      last.end = Math.max(last.end, end);
      continue;
    }
    if (last && start < last.end) start = last.end;
    if (end - start >= MIN_PIECE) out.push({ start, end, zoom: p.zoom });
  }
  return { ...e, pieces: out };
}

/** The clip starts at `t` (I): pieces before it go; with nothing kept yet, a first 20 s. */
export function setIn(e: Edit, t: number, duration: number): Edit {
  if (!e.pieces.length) return tidy({ ...e, pieces: [{ start: t, end: Math.min(duration, t + 20), zoom: 1 }] }, duration);
  const pieces = e.pieces.filter((p) => p.end > t + MIN_PIECE).map((p, i) => (i === 0 ? { ...p, start: t } : p));
  return tidy({ ...e, pieces: pieces.length ? pieces : [{ start: t, end: Math.min(duration, t + 20), zoom: 1 }] }, duration);
}

/** The clip ends at `t` (O). */
export function setOut(e: Edit, t: number, duration: number): Edit {
  if (!e.pieces.length) return tidy({ ...e, pieces: [{ start: Math.max(0, t - 20), end: t, zoom: 1 }] }, duration);
  const pieces = e.pieces.filter((p) => p.start < t - MIN_PIECE);
  if (!pieces.length) return e;
  pieces[pieces.length - 1] = { ...pieces[pieces.length - 1], end: t };
  return tidy({ ...e, pieces }, duration);
}

/** Split the piece under `t` in two (S): each half can then be zoomed or trimmed alone. */
export function split(e: Edit, t: number): Edit {
  const i = pieceAt(e, t);
  if (i < 0) return e;
  const p = e.pieces[i];
  if (t - p.start < MIN_PIECE || p.end - t < MIN_PIECE) return e;
  return { ...e, pieces: [...e.pieces.slice(0, i), { ...p, end: t }, { ...p, start: t }, ...e.pieces.slice(i + 1)] };
}

/** Take `a`-`b` out of the clip (cut words, delete a piece). */
export function cutRange(e: Edit, a: number, b: number, duration: number): Edit {
  const pieces: Piece[] = [];
  for (const p of e.pieces) {
    if (b <= p.start || a >= p.end) pieces.push(p);
    else {
      if (a > p.start) pieces.push({ ...p, end: a });
      if (b < p.end) pieces.push({ ...p, start: b });
    }
  }
  return tidy({ ...e, pieces }, duration);
}

/** Put `a`-`b` back in, or make it the whole clip (`only`). */
export function keepRange(e: Edit, a: number, b: number, duration: number, only = false): Edit {
  if (only) return tidy({ ...e, pieces: [{ start: a, end: b, zoom: 1 }] }, duration);
  // Inside a gap: fill it at the zoom of the piece before it.
  const before = [...e.pieces].reverse().find((p) => p.start <= a);
  return tidy({ ...e, pieces: [...cutRange(e, a, b, duration).pieces, { start: a, end: b, zoom: before?.zoom ?? 1 }] }, duration);
}

/** Move a piece's edge (Q / W, or dragging a handle), never past its neighbours. */
export function trim(e: Edit, i: number, edge: "start" | "end", t: number, duration: number): Edit {
  const p = e.pieces[i];
  if (!p) return e;
  const low = edge === "start" ? (e.pieces[i - 1]?.end ?? 0) : p.start + MIN_PIECE;
  const high = edge === "start" ? p.end - MIN_PIECE : (e.pieces[i + 1]?.start ?? duration);
  const at = Math.min(high, Math.max(low, t));
  const pieces = e.pieces.map((q, j) => (j === i ? { ...q, [edge]: at } : q));
  return { ...e, pieces };
}

export function setZoom(e: Edit, i: number, zoom: number): Edit {
  return { ...e, pieces: e.pieces.map((p, j) => (j === i ? { ...p, zoom } : p)) };
}

export const nextZoom = (z: number) => ZOOMS[(ZOOMS.indexOf(z as (typeof ZOOMS)[number]) + 1) % ZOOMS.length];

/** A word's caption text, with any fix applied ("" hides it). */
export function shownText(e: Edit, w: Word): string {
  const fix = e.fixes.find((f) => Math.abs(f.at - w.start) < 0.01);
  return fix ? fix.text : w.text;
}

export function setFix(e: Edit, w: Word, text: string): Edit {
  const rest = e.fixes.filter((f) => Math.abs(f.at - w.start) >= 0.01);
  return { ...e, fixes: text === w.text ? rest : [...rest, { at: w.start, text }] };
}

/** Where `t` (source) lands on the finished clip's own clock, or null if it's cut. */
export function clipTime(e: Edit, t: number): number | null {
  let before = 0;
  for (const p of e.pieces) {
    if (t >= p.start && t < p.end) return before + (t - p.start);
    before += p.end - p.start;
  }
  return null;
}

export const kept = (e: Edit, w: Word) => pieceAt(e, (w.start + w.end) / 2) >= 0;

export function clock(seconds: number, tenths = false): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  const sec = tenths ? (s % 60).toFixed(1).padStart(4, "0") : String(Math.floor(s % 60)).padStart(2, "0");
  return h ? `${h}:${String(m % 60).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
