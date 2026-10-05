import { useQueryClient } from "@tanstack/react-query";
import { Check, Film, Loader2, Play, RefreshCw, Search, Shapes, Undo2, Wand2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { createApi, useCreate, type CreateVideo, type CreateVisual, type FootageOffer } from "@/api/client";
import { Button, Chip, Tip } from "@/components/ui";
import { cn } from "@/lib/utils";

/** What a picture is, in a few words. */
function describe(v: CreateVisual): string {
  if (v.clip) return "Your own clip";
  if (v.kind === "stock") {
    const tags = (v.picked?.[0] as { tags?: string } | undefined)?.tags;
    return tags ? `Footage: ${tags.split(/[,\s]+/).slice(0, 4).join(" ")}` : `Footage: ${v.queries?.[0] || v.query}`;
  }
  if (v.template === "card") return `Chalk phrase: ${v.title}`;
  return v.template === "sketch" ? "Drawing" : `Diagram: ${v.title || v.template}`;
}

/** How well the footage model thinks a clip fits, in words. */
function fit(score: number | null): [string, string] {
  if (score === null) return ["not scored", "text-subtle"];
  if (score >= 7) return [`good match ${score}/10`, "text-success"];
  if (score >= 4) return [`loose ${score}/10`, "text-warning"];
  return [`poor ${score}/10`, "text-danger"];
}

/** A candidate's thumbnail that plays the clip while the pointer is over it (D130). The video is
 *  only loaded on the first hover, small and muted; on a touch screen, a press plays it. */
function Preview({ thumb, src }: { thumb: string; src: string }) {
  const video = useRef<HTMLVideoElement>(null);
  const [live, setLive] = useState(false);
  const start = () => {
    if (!src) return;
    setLive(true);
    requestAnimationFrame(() => void video.current?.play().catch(() => undefined));
  };
  const stop = () => {
    const el = video.current;
    if (el) { el.pause(); el.currentTime = 0; }
  };
  return (
    <span className="absolute inset-0" onMouseEnter={start} onMouseLeave={stop} onTouchStart={start} onTouchEnd={stop}
          data-testid="preview">
      <img src={thumb} alt="" loading="lazy" className="size-full object-cover" />
      {live && (
        <video ref={video} src={src} muted loop playsInline preload="auto" poster={thumb}
               className="absolute inset-0 size-full object-cover" />
      )}
      {src && !live && <Play className="absolute inset-0 m-auto size-5 text-white/80 drop-shadow" />}
    </span>
  );
}

/** Choosing a part's footage yourself (D129): searches written for it, both libraries, every clip
 *  scored and shown best first; the clip it has now and clips used elsewhere aren't offered. */
function FootagePicker({ video, beat, wish, setWish, onClose, onAuto }: {
  video: CreateVideo; beat: number; wish: string; setWish: (w: string) => void; onClose: () => void; onAuto: () => void;
}) {
  const qc = useQueryClient();
  const [offer, setOffer] = useState<FootageOffer | null>(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const find = async () => {
    setSearching(true);
    setError("");
    setChosen([]);
    try {
      setOffer(await createApi.footage(video.id, { beat, wish }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSearching(false);
    }
  };
  useEffect(() => { void find(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (id: string) => setChosen((c) => c.includes(id) ? c.filter((x) => x !== id)
    : c.length >= (offer?.clips ?? 1) ? [...c.slice(1), id] : [...c, id]);
  const use = async () => {
    setSaving(true);
    try {
      await createApi.footageUse(video.id, { beat, ids: chosen });
      await qc.invalidateQueries({ queryKey: ["create"] });
      onClose();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="mt-1 flex flex-col gap-2 rounded-md border border-line bg-surface-1 p-3" data-testid="footage-picker">
      <div className="flex flex-wrap items-center gap-1.5">
        <input value={wish} onChange={(e) => setWish(e.target.value)} placeholder="What you'd like to see (optional)"
               onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") void find(); }} aria-label="What you'd like to see"
               className="h-8 min-w-48 flex-1 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none" />
        <Button size="sm" variant="secondary" disabled={searching} onClick={() => void find()}>
          {searching ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />} Search again
        </Button>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close"><X className="size-3.5" /></Button>
      </div>
      {searching && <p className="flex items-center gap-2 text-xs text-muted"><Loader2 className="size-3.5 animate-spin" /> Writing searches, looking in both libraries and scoring what comes back…</p>}
      {error && <p className="text-xs text-danger">{error}</p>}
      {offer && !searching && (
        <>
          <p className="text-xs text-muted">
            Searched: {offer.searches.join(" · ")}. {offer.clips > 1 ? `This part plays ${offer.seconds.toFixed(0)}s: pick up to ${offer.clips} clips, in the order they should play.` : "Pick one."}
          </p>
          {offer.candidates.length === 0 ? (
            <p className="text-xs text-warning">Nothing new came back. Say what you'd like to see and search again.</p>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2">
              {offer.candidates.map((c) => {
                const n = chosen.indexOf(c.id);
                const [label, tone] = fit(c.score);
                return (
                  <button key={c.id} type="button" onClick={() => toggle(c.id)} aria-pressed={n >= 0} title={c.tags}
                          className={cn("flex flex-col gap-1 rounded-md border p-1 text-left", n >= 0 ? "border-accent bg-accent-soft" : "border-line hover:border-line-strong")}>
                    <span className="relative block aspect-[9/16] w-full overflow-hidden rounded-sm bg-black">
                      <Preview thumb={`/api/create/stock-thumb/${encodeURIComponent(c.id)}`} src={c.preview} />
                      <span className="absolute bottom-1 left-1 rounded-sm bg-black/70 px-1 text-[10px] text-white/80">
                        {c.source === "pexels" ? "Pexels" : c.source === "coverr" ? "Coverr" : c.source === "nasa" ? "NASA" : "Pixabay"} · {Math.round(c.duration)}s
                      </span>
                      {n >= 0 && <span className="absolute top-1 right-1 grid size-5 place-items-center rounded-full bg-accent text-[11px] font-bold text-accent-fg">{offer.clips > 1 ? n + 1 : <Check className="size-3" />}</span>}
                    </span>
                    <span className={cn("text-[11px] font-medium", tone)}>{label}</span>
                    <span className="line-clamp-2 text-[11px] text-muted">{c.tags || c.query}</span>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" disabled={!chosen.length || saving} onClick={() => void use()}>
          {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />} Use {chosen.length > 1 ? `these ${chosen.length}` : "this one"}
        </Button>
        <Tip label="Clipper picks the footage itself when you rebuild (only a good match is used)">
          <Button size="sm" variant="ghost" onClick={onAuto}><Wand2 className="size-3.5" /> Let Clipper choose</Button>
        </Tip>
      </div>
    </div>
  );
}

/** A finished video, picture by picture (D125): keep what you like, ask for new footage or a new
 *  drawing where you don't, then rebuild. Only the parts you changed are made again. */
export function ShotReview({ video, seek, onRebuild, rebuilding }: {
  video: CreateVideo; seek: (t: number) => void; onRebuild: () => void; rebuilding: boolean;
}) {
  const qc = useQueryClient();
  const drawings = useCreate().data?.channel.drawings !== false;   // a footage-only channel draws nothing (D146)
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [picking, setPicking] = useState<number | null>(null);
  const beats = video.script.beats;
  const pending = beats.filter((b) => b.visual.redo).length;
  // Built before Clipper kept its footage (D126): the next build picks every part's footage again.
  const unsaved = (video.shots ?? []).some((shot) => {
    const v = beats[shot.beats[0] - 1]?.visual;
    return v && v.kind === "stock" && !v.clip && !v.redo && !(v.picked?.length);
  });

  const ask = async (beat: number, want: "footage" | "drawing" | "undo") => {
    setBusy(`${beat}-${want}`);
    try {
      await createApi.redo(video.id, { beat, want, note: want === "undo" ? "" : notes[beat] ?? "" });
      setNotes((n) => ({ ...n, [beat]: "" }));
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  // Never an empty space where the list was (D128): say why there is no list.
  if (!video.shots?.length) {
    return (
      <p className={cn("text-xs", video.problem ? "rounded-md border border-danger/40 p-2.5 text-danger" : "text-muted")}>
        {video.problem ?? "The parts are listed here once the video has been built with its voice."}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <p className="flex-1 text-xs text-muted">
          Everything you don't change stays exactly as it is. Ask for new footage or a new drawing where you don't like a
          part (add a few words to say what you'd rather see), then rebuild.
        </p>
        <Button variant="primary" disabled={!pending || rebuilding} onClick={onRebuild}>
          {rebuilding ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {pending ? `Rebuild with ${pending} change${pending === 1 ? "" : "s"}` : "No changes yet"}
        </Button>
      </div>
      {unsaved && (
        <p className="rounded-md border border-warning/40 p-2.5 text-xs text-warning">
          This video was built before Clipper kept its footage, so the next build picks footage again for every footage
          part, not just the ones you change. From then on, everything you don't change stays.
        </p>
      )}
      <ol className="flex flex-col divide-y divide-line">
        {video.shots.map((shot) => {
          const first = shot.beats[0];
          const v = beats[first - 1]?.visual;
          if (!v) return null;
          const text = shot.beats.map((n) => beats[n - 1]?.text).join(" ");
          const mid = (shot.start + shot.end) / 2;
          return (
            <li key={first} className="flex gap-3 py-3">
              <button type="button" onClick={() => seek(shot.start)} aria-label="Play from here"
                      className="group relative aspect-[9/16] w-14 shrink-0 self-start overflow-hidden rounded-md bg-black">
                <img src={`/api/create/videos/${video.id}/still?t=${mid.toFixed(2)}&v=${video.clip_id}-${video.updated_at ?? ""}`}
                     alt="" loading="lazy" className="size-full object-cover" onError={(e) => { e.currentTarget.style.opacity = "0"; }} />
                <Play className="absolute inset-0 m-auto size-5 text-white opacity-0 drop-shadow group-hover:opacity-100" />
              </button>
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="tabular">{shot.start.toFixed(1)}s</span>
                  <span>{shot.beats.length > 1 ? `Sentences ${first} to ${shot.beats[shot.beats.length - 1]}` : `Sentence ${first}`}</span>
                  <span className="text-fg">{describe(v)}</span>
                  {v.redo && (
                    <Chip tone="accent">{v.kind !== "stock" ? "new drawing" : v.picked?.length ? "your footage" : "new footage"} on the next build</Chip>
                  )}
                </div>
                {v.notice && <p className="text-xs text-warning">{v.notice}</p>}
                <p className="line-clamp-2 text-sm">{text}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  {picking !== first && (
                    <input value={notes[first] ?? ""} onChange={(e) => setNotes((n) => ({ ...n, [first]: e.target.value }))}
                           onKeyDown={(e) => e.stopPropagation()} placeholder="What you'd rather see (optional)" aria-label="What you'd rather see"
                           className="h-8 min-w-48 flex-1 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none" />
                  )}
                  <Tip label="See footage for this part, scored, best first, and pick what to use. The clip it has now isn't offered.">
                    <Button size="sm" variant={picking === first ? "primary" : "secondary"} disabled={busy !== null || rebuilding}
                            onClick={() => setPicking((p) => (p === first ? null : first))}>
                      {busy === `${first}-footage` ? <Loader2 className="size-3.5 animate-spin" /> : <Film className="size-3.5" />} New footage
                    </Button>
                  </Tip>
                  {drawings && <Tip label="A new chalk drawing for this part. Your words, if any, say what to draw.">
                    <Button size="sm" variant="secondary" disabled={busy !== null || rebuilding} onClick={() => void ask(first, "drawing")}>
                      {busy === `${first}-drawing` ? <Loader2 className="size-3.5 animate-spin" /> : <Shapes className="size-3.5" />} New drawing
                    </Button>
                  </Tip>}
                  {v.previous && (
                    <Tip label="Keep what this part had">
                      <Button size="sm" variant="ghost" disabled={busy !== null || rebuilding} onClick={() => void ask(first, "undo")}>
                        <Undo2 className="size-3.5" /> Undo
                      </Button>
                    </Tip>
                  )}
                </div>
                {picking === first && (
                  <FootagePicker video={video} beat={first} wish={notes[first] ?? ""}
                                 setWish={(w) => setNotes((n) => ({ ...n, [first]: w }))}
                                 onClose={() => setPicking(null)}
                                 onAuto={() => { setPicking(null); void ask(first, "footage"); }} />
                )}
              </div>
            </li>
          );
        })}
      </ol>
      <p className={cn("text-xs text-subtle")}>New drawings and footage judging use a little of your AI quota when you rebuild.</p>
    </div>
  );
}
