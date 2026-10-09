import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  AlertTriangle, ArrowLeft, Eye, Loader2, Lock, Pause, Play, Redo2, Scissors, SplitSquareHorizontal, Undo2, Wind, ZoomIn,
} from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  prepareEditor, previewEdit, saveEdit, tightenEdit, useEditor, useJobs, usePeaks, type EditorView, type EditRule,
} from "@/api/client";
import { JobProgress } from "@/components/progress";
import { Button, CaptionTitle, Card, Kbd, Skeleton, Tip } from "@/components/ui";
import {
  clipTime, clock, cutRange, cuts, emptyEdit, endOf, keepRange, kept, length, nextZoom, pieceAt, setFix, setIn,
  setOut, setZoom, shownText, split, startOf, tidy, trim, zoomed, type Edit, type Word,
} from "@/lib/edit";
import { useUI } from "@/lib/store";
import { cn, isTyping } from "@/lib/utils";

/* ---------- loading the video ---------- */

export function EditorPage() {
  const search = useSearch({ from: "/edit" });
  const { data: view, error, isLoading } = useEditor(search);
  if (search.clip === undefined && !(search.source && search.campaign)) {
    return <Message title="Nothing to edit" body={<>Open a clip's editor from the clip, or pick a video under <Link to="/new" className="text-accent hover:underline">New clips → I'll pick them</Link>.</>} />;
  }
  if (error) return <Message title="Can't open the editor" body={(error as Error).message} />;
  if (isLoading || !view) return <div className="flex flex-col gap-4"><Skeleton className="h-12 w-80" /><Skeleton className="h-[60vh]" /></div>;
  if (!view.prepared) return <Preparing view={view} />;
  return <Workbench key={`${view.source_id}-${view.clip_id ?? "new"}`} view={view} />;
}

function Message({ title, body }: { title: string; body: ReactNode }) {
  return (
    <Card className="mx-auto mt-10 flex max-w-lg flex-col items-center gap-3 p-8 text-center">
      <AlertTriangle className="size-6 text-warning" />
      <h1 className="text-lg font-semibold">{title}</h1>
      <p className="text-sm text-muted">{body}</p>
    </Card>
  );
}

/** A video not read yet: read and transcribe it first (a job, with progress). */
function Preparing({ view }: { view: EditorView }) {
  const { data: jobs = [] } = useJobs();
  const asked = useRef(false);
  const job = jobs.find((j) => j.mode === "prepare" && j.source === view.source && (j.status === "running" || j.status === "queued"));
  useEffect(() => {
    if (asked.current || job) return;
    asked.current = true;
    prepareEditor(view.campaign, view.source).catch((e) => toast.error((e as Error).message));
  }, [job, view.campaign, view.source]);
  return (
    <div className="mx-auto mt-10 flex max-w-lg flex-col items-center gap-4 text-center">
      <div className="grid h-[72px] w-[41px] place-items-center rounded-[10px] border-2 border-line-strong"><Loader2 className="size-5 animate-spin text-accent" /></div>
      <CaptionTitle text="Getting it ready" hi="ready" className="text-3xl" />
      <p className="text-sm text-muted">
        Clipper reads and transcribes <b className="text-fg">{view.name}</b> once, so you can cut it by its words. A few minutes for an episode;
        you can leave this page and come back.
      </p>
      {job && (
        <div className="w-full text-left">
          <JobProgress pct={job.status === "queued" ? null : job.pct} size="sm" label="Getting the video ready"
                       stage={job.status === "queued" ? "Waiting for the video before it" : job.stage} started={job.started || null}
                       steps={[{ label: "Reading the video", at: 0 }, { label: "Transcribing", at: 10 }]} />
        </div>
      )}
    </div>
  );
}

/* ---------- the editor ---------- */

type History = { past: Edit[]; now: Edit; future: Edit[] };

const draftKey = (view: EditorView) => `clipper.edit.${view.source_id}.${view.clip_id ?? "new"}`;

const usable = (e: unknown): e is Edit =>
  Boolean(e) && Array.isArray((e as Edit).pieces)
  && (e as Edit).pieces.every((p) => Number.isFinite(p.start) && Number.isFinite(p.end));

function firstEdit(view: EditorView): Edit {
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey(view)) ?? "null");
    if (usable(saved)) return { ...emptyEdit(), ...saved };
  } catch { /* no storage: start from the server's */ }
  return usable(view.edit) ? { ...emptyEdit(), ...view.edit } : emptyEdit(view.hooks[0] ?? "");
}

function Workbench({ view }: { view: EditorView }) {
  const navigate = useNavigate();
  const duration = view.duration;
  const words = view.words as Word[];
  const rules = useMemo(() => Object.fromEntries(view.rules.map((r) => [r.key, r])) as Record<string, EditRule>, [view.rules]);
  const [hist, setHist] = useState<History>(() => ({ past: [], now: firstEdit(view), future: [] }));
  const edit = hist.now;
  // `replace` updates the current step (a drag in progress) instead of adding one to undo.
  const commit = useCallback((next: Edit, replace = false) =>
    setHist((h) => (next === h.now ? h : replace ? { ...h, now: next }
      : { past: [...h.past.slice(-99), h.now], now: next, future: [] })), []);
  const undo = () => setHist((h) => (h.past.length ? { past: h.past.slice(0, -1), now: h.past[h.past.length - 1], future: [h.now, ...h.future] } : h));
  const redo = () => setHist((h) => (h.future.length ? { past: [...h.past, h.now], now: h.future[0], future: h.future.slice(1) } : h));

  // An unsaved edit survives a reload or a wander off the page.
  const restored = useRef(false);
  useEffect(() => {
    try { localStorage.setItem(draftKey(view), JSON.stringify(edit)); } catch { /* fine without */ }
  }, [edit, view]);
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    try {
      if (localStorage.getItem(draftKey(view)) && view.edit && JSON.stringify(view.edit) !== JSON.stringify(edit)) {
        toast("Picked up where you left off", {
          description: "Your unsaved edit is back.", duration: 6000,
          action: { label: "Start over", onClick: () => commit(view.edit as unknown as Edit) },
        });
      }
    } catch { /* fine */ }
  }, [view, edit, commit]);

  /* playback */
  const video = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(() => (edit.pieces.length ? startOf(edit) : 0));
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const following = useRef(-1); // the piece being played through, or -1 for the raw source
  const seek = useCallback((to: number) => {
    const at = Math.min(duration, Math.max(0, to));
    if (video.current) video.current.currentTime = at;
    setT(at);
  }, [duration]);
  const play = useCallback(() => {
    const v = video.current;
    if (!v) return;
    // Inside the clip it plays the clip, skipping what's cut; outside, the raw video.
    let i = pieceAt(edit, v.currentTime);
    if (i < 0 && edit.pieces.length && v.currentTime >= startOf(edit) && v.currentTime < endOf(edit)) {
      i = edit.pieces.findIndex((p) => p.start > v.currentTime);
      if (i >= 0) v.currentTime = edit.pieces[i].start;
    }
    following.current = i;
    v.playbackRate = rate;
    void v.play();
    setPlaying(true);
  }, [edit, rate]);
  const pause = useCallback(() => { video.current?.pause(); setPlaying(false); }, []);
  // The light copy loads after the page: put it where the playhead is.
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const place = () => { if (Math.abs(v.currentTime - t) > 0.05) v.currentTime = t; };
    if (v.readyState >= 1) place(); else v.addEventListener("loadedmetadata", place, { once: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.proxy_ready]);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const v = video.current;
      if (!v) return;
      const i = following.current;
      if (i >= 0 && edit.pieces[i] && v.currentTime >= edit.pieces[i].end - 0.02) {
        const next = edit.pieces[i + 1];
        if (next) { v.currentTime = next.start; following.current = i + 1; }
        else { v.pause(); setPlaying(false); setT(edit.pieces[i].end); return; }
      }
      if (v.ended || v.paused) { setPlaying(false); setT(v.currentTime); return; }
      setT(v.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, edit]);

  /* selection */
  const [piece, setPiece] = useState<number | null>(null);
  const [range, setRange] = useState<[number, number] | null>(null); // word indices, inclusive
  const current = piece ?? (pieceAt(edit, t) >= 0 ? pieceAt(edit, t) : null);

  /* what the brief allows */
  const blocked = (key: string) => rules[key]?.blocked ?? false;
  const why = (key: string) => rules[key]?.why ?? "";
  const problems = useMemo(() => {
    const out: string[] = [];
    if (!edit.pieces.length) return ["Set where the clip starts (I) and ends (O)"];
    if (cuts(edit) && blocked("internal_cuts")) out.push(`No cuts inside the clip: ${why("internal_cuts")}`);
    if (zoomed(edit) && blocked("visual_effects")) out.push(`No zooms: ${why("visual_effects")}`);
    if (edit.hook && blocked("added_text")) out.push(`No on-screen text: ${why("added_text")}`);
    const len = length(edit);
    if (view.min_seconds && len < view.min_seconds) out.push(`${Math.round(len)}s long; the campaign wants at least ${view.min_seconds}s`);
    if (view.max_seconds && len > view.max_seconds) out.push(`${Math.round(len)}s long; the campaign allows at most ${view.max_seconds}s`);
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edit, view]);

  /* actions */
  const act = {
    in: () => commit(setIn(edit, t, duration)),
    out: () => commit(setOut(edit, t, duration)),
    split: () => commit(split(edit, t)),
    zoom: () => {
      if (current === null || blocked("visual_effects")) return;
      commit(setZoom(edit, current, nextZoom(edit.pieces[current].zoom)));
    },
    trimStart: () => current !== null && commit(trim(edit, current, "start", t, duration)),
    trimEnd: () => current !== null && commit(trim(edit, current, "end", t, duration)),
    remove: () => {
      if (range) {
        const [a, b] = range;
        commit(cutRange(edit, words[a].start - 0.04, words[b].end + 0.04, duration));
        setRange(null);
      } else if (piece !== null && edit.pieces[piece]) {
        commit(cutRange(edit, edit.pieces[piece].start, edit.pieces[piece].end, duration));
        setPiece(null);
      }
    },
  };

  /* preview and save */
  const [preview, setPreview] = useState<{ url: string; of: Edit } | null>(null);
  const [tab, setTab] = useState<"source" | "preview">("source");
  const [busy, setBusy] = useState<"preview" | "save" | "tighten" | null>(null);
  const body = () => ({ source_id: view.source_id, campaign: view.campaign, clip: view.clip_id, edit: tidy(edit, duration) });
  const makePreview = async () => {
    if (problems.length || busy) return;
    setBusy("preview");
    pause();
    try {
      const made = await previewEdit(body());
      setPreview({ url: `${made.url}?v=${Date.now()}`, of: edit });
      setTab("preview");
    } catch (e) {
      toast.error("Couldn't make the preview", { description: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };
  // Pauses and fillers cut by Clipper's podcast rules, as ordinary cuts (Ctrl Z puts them back).
  const tighten = async () => {
    if (!edit.pieces.length || blocked("internal_cuts") || busy) return;
    setBusy("tighten");
    try {
      const done = await tightenEdit(body());
      if (!done.cuts.length) toast("Nothing to tighten", { description: "No pause or filler long enough to cut." });
      else {
        commit({ ...(done.edit as Edit), hook: edit.hook, fixes: edit.fixes });
        toast.success(`Cut ${done.cuts.length} pause${done.cuts.length === 1 ? "" : "s"} and filler${done.cuts.length === 1 ? "" : "s"} (${done.removed.toFixed(1)}s)`,
          { description: "They're ordinary cuts: drag their edges, double-click to put one back, or Ctrl Z." });
      }
    } catch (e) {
      toast.error("Couldn't tighten", { description: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };
  const save = async () => {
    if (problems.length || busy) return;
    setBusy("save");
    try {
      await saveEdit(body());
      try { localStorage.removeItem(draftKey(view)); } catch { /* fine */ }
      if (view.clip_id != null) {
        toast.success("Saving your edit", { description: "The clip is rendered again in the background; the old version goes to the Recycle Bin." });
        useUI.getState().setOpenClip(view.clip_id);
        void navigate({ to: "/clips" });
      } else {
        toast.success("Making the clip", { description: "It'll be in Clips when it's done. Keep cutting: you can make more from this video." });
      }
    } catch (e) {
      toast.error("Couldn't save", { description: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  /* keys: the editor's own, ahead of the app's single-key shortcuts */
  const frame = 1 / (view.fps || 30);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || document.querySelector("[role=dialog][data-state=open]")) return;
      const k = e.key.toLowerCase();
      const ctrl = e.ctrlKey || e.metaKey;
      const run = (fn: () => void) => { e.preventDefault(); e.stopImmediatePropagation(); fn(); };
      if (ctrl && k === "z") return run(e.shiftKey ? redo : undo);
      if (ctrl && k === "y") return run(redo);
      if (ctrl && k === "b") return run(act.split);
      if (ctrl || e.altKey) return;
      const keys: Record<string, () => void> = {
        " ": () => (playing ? pause() : play()),
        k: pause,
        l: () => { if (playing) { const r = rate >= 2 ? 1 : rate + 0.5; setRate(r); if (video.current) video.current.playbackRate = r; } else play(); },
        j: () => seek(t - 3),
        arrowleft: () => seek(t - (e.shiftKey ? 1 : frame)),
        arrowright: () => seek(t + (e.shiftKey ? 1 : frame)),
        home: () => seek(startOf(edit)),
        end: () => seek(endOf(edit)),
        i: act.in, o: act.out, s: act.split, z: act.zoom, q: act.trimStart, w: act.trimEnd, t: () => void tighten(),
        delete: act.remove, backspace: act.remove,
        p: () => void makePreview(),
        escape: () => { setPiece(null); setRange(null); },
      };
      const fn = keys[k];
      if (fn) run(fn);
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  });

  const len = length(edit);
  const at = clipTime(edit, t);
  // A posted clip's video is what's live: look and preview, but no saving over it.
  const live = Boolean(view.clip_status && view.clip_status !== "ready" && view.clip_status !== "skipped");
  const stale = preview && JSON.stringify(preview.of) !== JSON.stringify(edit);
  const zoomNow = (() => { const i = pieceAt(edit, t); return i >= 0 ? edit.pieces[i].zoom : 1; })();

  return (
    <div className="fade-in flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <button onClick={() => window.history.back()} className="mb-2 flex items-center gap-1 text-xs text-muted hover:text-fg">
            <ArrowLeft className="size-3.5" /> Back
          </button>
          <CaptionTitle text={view.clip_id != null ? "Edit the clip" : "Cut a clip"} hi="clip" className="text-[clamp(1.6rem,2.6vw,2.2rem)]" />
          <p className="mt-1 truncate text-sm text-muted">{view.name}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tip label="Undo" keys="Ctrl Z"><Button size="icon" variant="ghost" aria-label="Undo" disabled={!hist.past.length} onClick={undo}><Undo2 className="size-4" /></Button></Tip>
          <Tip label="Redo" keys="Ctrl Shift Z"><Button size="icon" variant="ghost" aria-label="Redo" disabled={!hist.future.length} onClick={redo}><Redo2 className="size-4" /></Button></Tip>
          <Tip label="A quick render of exactly what the clip will be: framing, captions, hook" keys="P">
            <Button variant="secondary" disabled={Boolean(problems.length) || busy !== null} onClick={() => void makePreview()}>
              {busy === "preview" ? <Loader2 className="size-4 animate-spin" /> : <Eye className="size-4" />} {busy === "preview" ? "Rendering…" : "Preview"}
            </Button>
          </Tip>
          <Button variant="primary" disabled={Boolean(problems.length) || busy !== null || live} onClick={() => void save()}>
            {busy === "save" ? <Loader2 className="size-4 animate-spin" /> : <Scissors className="size-4" />}
            {view.clip_id != null ? "Save changes" : "Make clip"}
          </Button>
        </div>
      </div>

      {live && (
        <p className="flex items-center gap-2 rounded-md border border-warning/40 px-3 py-2 text-sm text-warning">
          <Lock className="size-4" /> This clip is posted, so its video is what's live. You can look, but not save changes.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(240px,300px)_minmax(0,1fr)_minmax(240px,280px)]">
        <Transcript words={words} edit={edit} range={range} setRange={setRange} seek={seek} t={t}
                    commit={commit} duration={duration} canFix={!blocked("captions")} />

        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex gap-1 self-start rounded-md bg-surface-2 p-1 text-sm">
            <button className={cn("rounded-[7px] px-3 py-1 font-medium", tab === "source" ? "bg-surface-1 text-fg shadow-1" : "text-muted")} onClick={() => setTab("source")}>Edit</button>
            <button disabled={!preview} className={cn("rounded-[7px] px-3 py-1 font-medium disabled:opacity-40", tab === "preview" ? "bg-surface-1 text-fg shadow-1" : "text-muted")}
                    onClick={() => setTab("preview")}>Preview{stale ? " (old)" : ""}</button>
          </div>
          <div className={cn(tab !== "source" && "hidden")}>
            <Stage view={view} video={video} zoom={zoomNow} edit={edit} words={words} t={t} inClip={at !== null}
                   onEnded={() => setPlaying(false)} onSeeked={(to) => !playing && setT(to)} />
          </div>
          {tab === "preview" && preview && (
            <div className="flex flex-col items-center gap-2">
              <video key={preview.url} src={preview.url} controls autoPlay playsInline className="aspect-[9/16] max-h-[62vh] rounded-xl bg-black" />
              {stale && <p className="text-xs text-warning">You've changed the edit since. Press <Kbd>P</Kbd> for a new preview.</p>}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="icon" variant="primary" aria-label={playing ? "Pause" : "Play"} onClick={() => (playing ? pause() : play())}>
              {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
            </Button>
            <span className="tabular text-sm"><b>{clock(t, true)}</b><span className="text-muted"> / {clock(duration)}</span></span>
            {rate !== 1 && <span className="tabular text-xs text-accent">{rate}×</span>}
            <span className="tabular text-xs text-muted">{at !== null ? `clip ${clock(at, true)}` : "outside the clip"}</span>
            <span className="ml-auto flex flex-wrap gap-1.5">
              <Tool label="Start here" keys="I" onClick={act.in}>Start</Tool>
              <Tool label="End here" keys="O" onClick={act.out}>End</Tool>
              <Tool label="Split at the playhead" keys="S" onClick={act.split}><SplitSquareHorizontal className="size-3.5" /> Split</Tool>
              <Tool label={blocked("internal_cuts") ? `No cuts inside the clip: ${why("internal_cuts")}` : "Cut dead air and filler words, like Clipper does on podcasts"} keys="T"
                    onClick={() => void tighten()} disabled={blocked("internal_cuts") || !edit.pieces.length || busy !== null}>
                {busy === "tighten" ? <Loader2 className="size-3.5 animate-spin" /> : <Wind className="size-3.5" />} Tighten
              </Tool>
              <Tool label={blocked("visual_effects") ? `No zooms: ${why("visual_effects")}` : "Punch in on this piece: none, 1.1×, 1.2×"} keys="Z"
                    onClick={act.zoom} disabled={blocked("visual_effects") || current === null}>
                <ZoomIn className="size-3.5" /> {current !== null && edit.pieces[current]?.zoom !== 1 ? `${edit.pieces[current].zoom}×` : "Zoom"}
              </Tool>
            </span>
          </div>
        </div>

        <Panel view={view} edit={edit} commit={commit} len={len} problems={problems} rules={view.rules} blocked={blocked} why={why} />
      </div>

      <Timeline view={view} edit={edit} t={t} seek={seek} commit={commit} piece={piece} setPiece={setPiece} words={words} playing={playing} />
      <p className="text-xs text-subtle">
        <Kbd>Space</Kbd> play · <Kbd>J</Kbd><Kbd>K</Kbd><Kbd>L</Kbd> back, stop, faster · <Kbd>←</Kbd><Kbd>→</Kbd> a frame (Shift: a second) ·
        {" "}<Kbd>I</Kbd><Kbd>O</Kbd> start, end · <Kbd>S</Kbd> split · <Kbd>Q</Kbd><Kbd>W</Kbd> trim to the playhead · <Kbd>Z</Kbd> zoom · <Kbd>T</Kbd> tighten ·
        {" "}<Kbd>Del</Kbd> cut what's selected · drag across words to select them · <Kbd>P</Kbd> preview
      </p>
    </div>
  );
}

function Tool({ label, keys, onClick, disabled, children }: { label: string; keys: string; onClick: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <Tip label={label} keys={keys}>
      <span><Button size="sm" variant="secondary" onClick={onClick} disabled={disabled}>{children}</Button></span>
    </Tip>
  );
}

/* ---------- the picture ---------- */

/** The source, with the 9:16 frame Clipper will cut and the captions as they'll run. */
function Stage({ view, video, zoom, edit, words, t, inClip, onEnded, onSeeked }: {
  view: EditorView; video: React.RefObject<HTMLVideoElement | null>; zoom: number; edit: Edit; words: Word[]; t: number; inClip: boolean;
  onEnded: () => void; onSeeked: (t: number) => void;
}) {
  // Three or four kept words around the playhead, the spoken one lit: roughly the caption.
  const line = useMemo(() => {
    if (!inClip) return [];
    const i = words.findIndex((w) => w.end > t);
    if (i < 0 || words[i].start - t > 1.2) return [];
    const from = Math.max(0, i - (i % 3));
    return words.slice(from, from + 3).filter((w) => kept(edit, w)).map((w) => ({ text: shownText(edit, w), now: w.start <= t && t < w.end + 0.05 }));
  }, [words, t, edit, inClip]);
  const hookUp = inClip && edit.hook && (clipTime(edit, t) ?? 99) < 3;
  if (!view.proxy_ready) {
    return (
      <div className="grid aspect-video place-items-center rounded-xl border border-dashed border-line-strong text-center text-sm text-muted">
        <span className="flex flex-col items-center gap-2"><Loader2 className="size-5 animate-spin text-accent" />Making a light copy to scrub through (about a minute)…</span>
      </div>
    );
  }
  return (
    <div className="relative aspect-video overflow-hidden rounded-xl bg-black">
      <video ref={video} src={`/api/editor/${view.source_id}/proxy`} preload="auto" playsInline onEnded={onEnded}
             onSeeked={(e) => onSeeked(e.currentTarget.currentTime)}
             className="absolute inset-0 size-full object-contain transition-transform duration-150"
             style={{ transform: `scale(${zoom})`, transformOrigin: "50% 35%" }} />
      {/* The vertical frame, with the rest dimmed: Clipper moves it to the faces when it renders. */}
      <div className="pointer-events-none absolute inset-y-0 left-1/2 aspect-[9/16] -translate-x-1/2 rounded-md border-2 border-white/80 shadow-[0_0_0_9999px_rgb(0_0_0/0.5)]">
        {hookUp && <div className="cap absolute inset-x-[6%] top-[12%] text-center text-[clamp(0.7rem,1.6vw,1.05rem)] text-white">{edit.hook}</div>}
        {line.length > 0 && (
          <div className="cap absolute inset-x-[6%] top-[55%] text-center text-[clamp(0.8rem,1.9vw,1.3rem)] text-white">
            {line.map((w, i) => <span key={i} className={cn(w.now && "text-accent")}>{w.text} </span>)}
          </div>
        )}
      </div>
      {!inClip && <span className="absolute top-2 left-2 rounded-md bg-black/70 px-2 py-0.5 text-xs text-white/80">Outside the clip</span>}
      <span className="absolute right-2 bottom-2 rounded-md bg-black/70 px-2 py-0.5 text-[11px] text-white/70">Framing follows faces when it renders · Preview shows it exactly</span>
    </div>
  );
}

/* ---------- the words ---------- */

const Transcript = memo(function Transcript({ words, edit, range, setRange, seek, t, commit, duration, canFix }: {
  words: Word[]; edit: Edit; range: [number, number] | null; setRange: (r: [number, number] | null) => void;
  seek: (t: number) => void; t: number; commit: (e: Edit) => void; duration: number; canFix: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<number | null>(null);
  const [fixing, setFixing] = useState<number | null>(null);
  // Paragraphs at long pauses, each with its time.
  const paras = useMemo(() => {
    const out: number[][] = [];
    words.forEach((w, i) => {
      const gap = i ? w.start - words[i - 1].end : 99;
      const ended = i ? /[.?!]$/.test(words[i - 1].text) : true;
      if (gap > 1.5 || (ended && gap > 0.7) || !out.length) out.push([i]);
      else out[out.length - 1].push(i);
    });
    return out;
  }, [words]);
  // The word being spoken, lit and kept in view; done on the page, not by re-rendering thousands of words.
  const now = useMemo(() => {
    let lo = 0, hi = words.length - 1, found = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (words[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1; }
    return found;
  }, [words, t]);
  useEffect(() => {
    const el = box.current?.querySelector<HTMLElement>(`[data-i="${now}"]`);
    box.current?.querySelectorAll(".word-now").forEach((n) => n.classList.remove("word-now"));
    if (el) {
      el.classList.add("word-now");
      const top = el.offsetTop; // the box is the offset parent
      const view = box.current;
      if (view && (top < view.scrollTop + 24 || top > view.scrollTop + view.clientHeight - 48)) {
        // A short hop glides; a long jump (a seek across the episode) lands at once.
        const far = Math.abs(top - view.scrollTop) > view.clientHeight * 2;
        view.scrollTo({ top: top - view.clientHeight / 3, behavior: far ? "auto" : "smooth" });
      }
    }
  }, [now]);
  const [a, b] = range ?? [-1, -1];
  const lo = Math.min(a, b), hi = Math.max(a, b);
  const sel = range ? [words[lo], words[hi]] : null;

  return (
    <Card className="flex max-h-[68vh] min-h-72 flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <h2 className="text-sm font-semibold">Words</h2>
        {sel ? (
          <span className="flex flex-wrap items-center gap-1">
            <Button size="sm" variant="primary" onClick={() => { commit(keepRange(edit, sel[0].start - 0.08, sel[1].end + 0.15, duration, true)); setRange(null); }}>Keep only these</Button>
            <Button size="sm" variant="secondary" onClick={() => { commit(keepRange(edit, sel[0].start - 0.04, sel[1].end + 0.04, duration)); setRange(null); }}>Add</Button>
            <Button size="sm" variant="secondary" onClick={() => { commit(cutRange(edit, sel[0].start - 0.04, sel[1].end + 0.04, duration)); setRange(null); }}>Cut <Kbd>Del</Kbd></Button>
          </span>
        ) : <span className="text-xs text-subtle">Drag across words to pick them</span>}
      </div>
      <div ref={box} className="relative flex-1 overflow-y-auto px-3 py-2 text-[15px] leading-7 select-none"
           onMouseUp={() => { drag.current = null; }} onMouseLeave={() => { drag.current = null; }}>
        {paras.map((ids) => (
          <p key={ids[0]} className="mb-2">
            <button className="tabular mr-1.5 align-middle text-[11px] text-subtle hover:text-accent" onClick={() => seek(words[ids[0]].start)}>{clock(words[ids[0]].start)}</button>
            {ids.map((i) => {
              const w = words[i];
              const text = shownText(edit, w);
              const inside = edit.pieces.length > 0 && w.start >= startOf(edit) - 0.05 && w.end <= endOf(edit) + 0.05;
              const isKept = kept(edit, w);
              const picked = range !== null && i >= lo && i <= hi;
              if (fixing === i) {
                return (
                  <input key={i} autoFocus defaultValue={text} aria-label="Fix this word"
                         className="mx-0.5 w-24 rounded border border-accent bg-surface-2 px-1 text-sm"
                         onBlur={(e) => { commit(setFix(edit, w, e.target.value.trim())); setFixing(null); }}
                         onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setFixing(null); }} />
                );
              }
              return (
                <span key={i} data-i={i}
                      onMouseDown={(e) => { drag.current = i; setRange(e.shiftKey && range ? [range[0], i] : [i, i]); seek(w.start); }}
                      onMouseEnter={() => drag.current !== null && setRange([drag.current, i])}
                      onDoubleClick={() => canFix && setFixing(i)}
                      title={canFix ? "Double-click to fix the caption" : undefined}
                      className={cn("cursor-pointer rounded-[4px] px-[1px] transition-colors [&.word-now]:bg-accent [&.word-now]:text-accent-fg",
                        isKept ? "text-fg" : inside ? "text-subtle line-through" : "text-muted",
                        picked && "bg-accent-soft outline outline-1 outline-accent/50",
                        text !== w.text && "underline decoration-accent decoration-dotted underline-offset-4")}>
                  {text || <s className="text-subtle">{w.text}</s>}{" "}
                </span>
              );
            })}
          </p>
        ))}
      </div>
    </Card>
  );
});

/* ---------- the clip's settings ---------- */

function Panel({ view, edit, commit, len, problems, rules, blocked, why }: {
  view: EditorView; edit: Edit; commit: (e: Edit) => void; len: number; problems: string[]; rules: EditRule[];
  blocked: (k: string) => boolean; why: (k: string) => string;
}) {
  const lo = view.min_seconds ?? 0, hi = view.max_seconds ?? Infinity;
  const ok = len >= lo && len <= hi;
  const shown = rules.filter((r) => ["internal_cuts", "visual_effects", "added_text", "captions", "overlays"].includes(r.key));
  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-col gap-1 p-4">
        <span className="text-xs text-muted">Clip length</span>
        <span className={cn("num text-4xl leading-none", edit.pieces.length ? (ok ? "text-money" : "text-warning") : "text-subtle")}>{clock(len, true)}</span>
        <span className="text-xs text-muted">
          {view.min_seconds || view.max_seconds ? `The campaign wants ${view.min_seconds ?? 0}–${view.max_seconds ?? "∞"}s` : "No length rule"}
          {cuts(edit) ? ` · ${cuts(edit)} cut${cuts(edit) === 1 ? "" : "s"}` : ""}
        </span>
      </Card>
      {problems.length > 0 && (
        <div className="flex flex-col gap-1 rounded-md border border-warning/40 bg-[color-mix(in_oklch,var(--warning)_8%,transparent)] p-3 text-xs text-warning">
          {problems.map((p) => <span key={p} className="flex gap-1.5"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{p}</span>)}
        </div>
      )}
      <Card className="flex flex-col gap-2 p-4">
        <label htmlFor="hook" className="flex items-center justify-between text-xs font-medium text-muted">
          On-screen hook {blocked("added_text") && <Tip label={why("added_text")}><Lock className="size-3.5" /></Tip>}
        </label>
        <textarea id="hook" rows={2} value={edit.hook} disabled={blocked("added_text")} maxLength={120}
                  placeholder="The line at the top for the first seconds"
                  onChange={(e) => commit({ ...edit, hook: e.target.value })}
                  className="resize-none rounded-md border border-line bg-surface-2 px-2.5 py-2 text-sm focus:border-accent focus:outline-none disabled:opacity-50" />
        {view.hooks.length > 0 && !blocked("added_text") && (
          <div className="flex flex-col gap-1">
            <span className="text-[11px] text-subtle">The brief's lines</span>
            {view.hooks.slice(0, 6).map((h) => (
              <button key={h} onClick={() => commit({ ...edit, hook: h })}
                      className={cn("truncate rounded-md border px-2 py-1 text-left text-xs", edit.hook === h ? "border-accent text-accent" : "border-line text-muted hover:text-fg")}>
                {h}
              </button>
            ))}
          </div>
        )}
      </Card>
      <Card className="flex flex-col gap-1.5 p-4">
        <span className="text-xs font-medium text-muted">What this campaign allows</span>
        {shown.map((r) => (
          <Tip key={r.key} label={r.blocked ? r.why : r.allowed ? "Clipper does this by itself too" : `Yours to do: Clipper doesn't by itself (${r.why})`}>
            <span className="flex items-center justify-between gap-2 text-xs">
              <span className={cn(r.blocked && "text-subtle line-through")}>{r.label}</span>
              <span className={cn("font-medium", r.blocked ? "text-danger" : "text-success")}>{r.blocked ? "Not allowed" : "Allowed"}</span>
            </span>
          </Tip>
        ))}
      </Card>
    </div>
  );
}

/* ---------- the timeline ---------- */

const PX_PER_TICK = 70;
const STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

function Timeline({ view, edit, t, seek, commit, piece, setPiece, words, playing }: {
  view: EditorView; edit: Edit; t: number; seek: (t: number) => void; commit: (e: Edit, replace?: boolean) => void;
  piece: number | null; setPiece: (i: number | null) => void; words: Word[]; playing: boolean;
}) {
  const duration = view.duration;
  const { data: peaks = [] } = usePeaks(view.source_id);
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(800);
  const [win, setWin] = useState(() => {
    if (edit.pieces.length) {
      const span = Math.min(duration, endOf(edit) - startOf(edit) + 20);
      return { from: Math.max(0, startOf(edit) - 10), span };
    }
    return { from: 0, span: Math.min(duration, 60) };
  });
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const x = (s: number) => ((s - win.from) / win.span) * width;
  const at = (px: number) => win.from + (px / width) * win.span;
  // Keep the playhead in view while it plays.
  useEffect(() => {
    if (playing && (t > win.from + win.span * 0.92 || t < win.from)) setWin((w) => ({ ...w, from: Math.max(0, Math.min(duration - w.span, t - w.span * 0.15)) }));
  }, [t, playing, win, duration]);

  // The waveform, drawn when the view or the sound changes.
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = 64 * dpr;
    const g = c.getContext("2d");
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, width, 64);
    g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--text-subtle") || "#888";
    for (let px = 0; px < width; px += 2) {
      const a = Math.floor(at(px) * 20), b = Math.max(a + 1, Math.ceil(at(px + 2) * 20));
      let top = 0;
      for (let i = a; i < b && i < peaks.length; i++) if (i >= 0 && peaks[i] > top) top = peaks[i];
      const h = Math.max(1, (top / 100) * 28);
      g.fillRect(px, 32 - h, 1.4, h * 2);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peaks, width, win]);

  const step = STEPS.find((s) => (s / win.span) * width >= PX_PER_TICK) ?? 600;
  const ticks: number[] = [];
  for (let s = Math.ceil(win.from / step) * step; s <= win.from + win.span; s += step) ticks.push(s);
  const visibleWords = win.span <= 45 ? words.filter((w) => w.end >= win.from && w.start <= win.from + win.span) : [];

  // Seeking by dragging on the ruler or the waveform; trimming by dragging a piece's edge.
  const dragging = useRef<null | { kind: "seek" } | { kind: "trim"; i: number; edge: "start" | "end"; base: Edit; moved: boolean }>(null);
  const snap = (s: number) => {
    const near = (o: number) => Math.abs(x(o) - x(s)) < 7;
    if (near(t)) return t;
    for (const w of words) {
      if (near(w.start)) return w.start - 0.04;
      if (near(w.end)) return w.end + 0.04;
    }
    return s;
  };
  const pointer = (e: React.PointerEvent) => at(e.clientX - (box.current?.getBoundingClientRect().left ?? 0));
  const onMove = (e: React.PointerEvent) => {
    const d = dragging.current;
    if (!d) return;
    if (d.kind === "seek") seek(pointer(e));
    else {
      // One undo step per drag: the first move adds it, the rest update it.
      commit(trim(d.base, d.i, d.edge, snap(pointer(e)), duration), d.moved);
      d.moved = true;
    }
  };
  const onWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      const anchor = pointer(e as unknown as React.PointerEvent);
      setWin((w) => {
        const span = Math.min(duration, Math.max(4, w.span * (e.deltaY > 0 ? 1.25 : 0.8)));
        const from = Math.max(0, Math.min(duration - span, anchor - ((anchor - w.from) / w.span) * span));
        return { from, span };
      });
    } else {
      const delta = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) / width * win.span;
      setWin((w) => ({ ...w, from: Math.max(0, Math.min(duration - w.span, w.from + delta)) }));
    }
  };
  const gaps = edit.pieces.slice(1).map((p, i) => ({ start: edit.pieces[i].end, end: p.start })).filter((g) => g.end - g.start > 0.02);

  return (
    <Card className="flex flex-col gap-1 p-3">
      <div className="flex items-center justify-between gap-2 text-xs text-muted">
        <span>Timeline · {clock(win.from)}–{clock(win.from + win.span)}</span>
        <span className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => setWin({ from: Math.max(0, t - win.span / 2), span: Math.max(4, win.span * 0.6) })}>Zoom in</Button>
          <Button size="sm" variant="ghost" onClick={() => setWin((w) => ({ from: Math.max(0, w.from - w.span * 0.3), span: Math.min(duration, w.span * 1.6) }))}>Zoom out</Button>
          {edit.pieces.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => {
              const span = Math.min(duration, endOf(edit) - startOf(edit) + 8);
              setWin({ from: Math.max(0, startOf(edit) - 4), span });
            }}>Fit the clip</Button>
          )}
          <span className="hidden text-subtle sm:inline">Ctrl + scroll to zoom</span>
        </span>
      </div>
      <div ref={box} className="relative touch-none select-none" onWheel={onWheel}
           onPointerMove={onMove} onPointerUp={() => { dragging.current = null; }} onPointerLeave={() => { dragging.current = null; }}>
        {/* ruler */}
        <div className="relative h-5 cursor-pointer border-b border-line"
             onPointerDown={(e) => { dragging.current = { kind: "seek" }; seek(pointer(e)); }}>
          {ticks.map((s) => (
            <span key={s} className="tabular absolute top-0 border-l border-line-strong pl-1 text-[10px] text-subtle" style={{ left: x(s) }}>{clock(s)}</span>
          ))}
        </div>
        {/* words, when close enough to read */}
        {visibleWords.length > 0 && (
          <div className="relative h-5 overflow-hidden">
            {visibleWords.map((w) => (
              <span key={w.start} className={cn("absolute top-0.5 truncate text-[11px]", kept(edit, w) ? "text-fg" : "text-subtle")}
                    style={{ left: x(w.start), width: Math.max(4, x(w.end) - x(w.start)) }}>{shownText(edit, w)}</span>
            ))}
          </div>
        )}
        {/* the sound, with the clip's pieces over it */}
        <div className="relative h-16 cursor-pointer" onPointerDown={(e) => { if (e.target === e.currentTarget || e.target === canvas.current) { dragging.current = { kind: "seek" }; seek(pointer(e)); setPiece(null); } }}>
          <canvas ref={canvas} className="pointer-events-none absolute inset-0 h-16 w-full" />
          {gaps.map((g) => (
            <Tip key={g.start} label="Cut out. Double-click to put it back">
              <div className="absolute inset-y-1 cursor-pointer rounded-sm bg-[repeating-linear-gradient(135deg,transparent_0_5px,rgb(255_107_90/0.25)_5px_7px)]"
                   style={{ left: x(g.start), width: Math.max(2, x(g.end) - x(g.start)) }}
                   onDoubleClick={() => commit(keepRange(edit, g.start, g.end, duration))} />
            </Tip>
          ))}
          {edit.pieces.map((p, i) => (
            <div key={`${p.start}-${i}`}
                 className={cn("group absolute inset-y-1 rounded-md border-2 bg-accent/15",
                   piece === i ? "border-accent" : "border-accent/60 hover:border-accent")}
                 style={{ left: x(p.start), width: Math.max(3, x(p.end) - x(p.start)) }}
                 onPointerDown={(e) => { if (e.target === e.currentTarget) { setPiece(i); dragging.current = { kind: "seek" }; seek(pointer(e)); } }}>
              {p.zoom !== 1 && <span className="absolute top-0.5 left-1.5 rounded bg-accent px-1 text-[10px] font-bold text-accent-fg">{p.zoom}×</span>}
              {(["start", "end"] as const).map((edge) => (
                <span key={edge} aria-label={`Drag the ${edge}`}
                      className={cn("absolute inset-y-0 w-2 cursor-ew-resize rounded-sm bg-accent/0 group-hover:bg-accent/70", edge === "start" ? "-left-1" : "-right-1")}
                      onPointerDown={(e) => { e.stopPropagation(); (e.target as HTMLElement).setPointerCapture?.(e.pointerId); setPiece(i); dragging.current = { kind: "trim", i, edge, base: edit, moved: false }; }} />
              ))}
            </div>
          ))}
        </div>
        {/* the playhead */}
        <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-accent" style={{ left: x(t) }}>
          <span className="absolute -top-1 -left-[5px] size-3 rotate-45 rounded-[2px] bg-accent" />
        </div>
      </div>
    </Card>
  );
}
