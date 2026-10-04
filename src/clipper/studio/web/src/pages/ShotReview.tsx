import { useQueryClient } from "@tanstack/react-query";
import { Film, Loader2, Play, RefreshCw, Shapes, Undo2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { createApi, type CreateVideo, type CreateVisual } from "@/api/client";
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

/** A finished video, picture by picture (D125): keep what you like, ask for new footage or a new
 *  drawing where you don't, then rebuild. Only the parts you changed are made again. */
export function ShotReview({ video, seek, onRebuild, rebuilding }: {
  video: CreateVideo; seek: (t: number) => void; onRebuild: () => void; rebuilding: boolean;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const beats = video.script.beats;
  const pending = beats.filter((b) => b.visual.redo).length;

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

  if (!video.shots?.length) return null;
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
                      className="group relative aspect-[9/16] w-14 shrink-0 overflow-hidden rounded-md bg-black">
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
                    <Chip tone="accent">{v.kind === "stock" ? "new footage" : "new drawing"} on the next build</Chip>
                  )}
                </div>
                <p className="line-clamp-2 text-sm">{text}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  <input value={notes[first] ?? ""} onChange={(e) => setNotes((n) => ({ ...n, [first]: e.target.value }))}
                         onKeyDown={(e) => e.stopPropagation()} placeholder="What you'd rather see (optional)" aria-label="What you'd rather see"
                         className="h-8 min-w-48 flex-1 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none" />
                  <Tip label="Another stock clip for this part; the one used now is turned down. Your words, if any, are searched first.">
                    <Button size="sm" variant="secondary" disabled={busy !== null || rebuilding} onClick={() => void ask(first, "footage")}>
                      {busy === `${first}-footage` ? <Loader2 className="size-3.5 animate-spin" /> : <Film className="size-3.5" />} New footage
                    </Button>
                  </Tip>
                  <Tip label="A new chalk drawing for this part. Your words, if any, say what to draw.">
                    <Button size="sm" variant="secondary" disabled={busy !== null || rebuilding} onClick={() => void ask(first, "drawing")}>
                      {busy === `${first}-drawing` ? <Loader2 className="size-3.5 animate-spin" /> : <Shapes className="size-3.5" />} New drawing
                    </Button>
                  </Tip>
                  {v.previous && (
                    <Tip label="Keep what this part had">
                      <Button size="sm" variant="ghost" disabled={busy !== null || rebuilding} onClick={() => void ask(first, "undo")}>
                        <Undo2 className="size-3.5" /> Undo
                      </Button>
                    </Tip>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      <p className={cn("text-xs text-subtle")}>New drawings and footage judging use a little of your AI quota when you rebuild.</p>
    </div>
  );
}
