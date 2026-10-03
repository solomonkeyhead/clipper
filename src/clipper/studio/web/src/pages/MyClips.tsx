import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Film, Loader2, RefreshCw, Sparkles, Trash2, Upload, Wand2 } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { createApi, type ClipFill, type CreateVideo, type MineClip } from "@/api/client";
import { Button, Chip, Switch, Tip } from "@/components/ui";
import { cn } from "@/lib/utils";

const FILL_LABEL: Record<ClipFill, string> = {
  auto: "Fill automatically", planned: "Planned footage or diagram", loop: "Repeat the clip", slow: "Slow it down", hold: "Hold the last picture",
};
const FILL_HINT = "When a clip is shorter than its sentence";
const select = "h-8 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none disabled:opacity-60";

const seconds = (n: number) => `${n.toFixed(1)}s`;
/** "2", "2 and 3", "2 to 5": the sentences a clip is on. */
const span = (used: number[]) => (used.length === 1 ? `${used[0]}` : used.length === 2 ? `${used[0]} and ${used[1]}` : `${used[0]} to ${used[used.length - 1]}`);

/** The video's own clips (D119): drop them in, let Clipper place them (or place them by hand), and
 *  anything the clips don't cover is filled with the planned footage and diagrams. */
export function MyClips({ video, wps, onRebuild, busyRebuild }: {
  video: CreateVideo; wps: number; onRebuild?: () => void; busyRebuild?: boolean;
}) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["create"] });
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [strict, setStrict] = useState(false);
  const { mine } = video;
  const beats = video.script.beats;
  const clips = mine?.clips ?? [];
  const byId = new Map(clips.map((c) => [c.id, c]));
  const lengths = beats.map((b) => Math.max(1.2, b.text.split(/\s+/).filter(Boolean).length / wps));
  const total = lengths.reduce((a, b) => a + b, 0);
  const covered = beats.reduce((n, b, i) => {
    const c = b.visual.clip ? byId.get(b.visual.clip) : undefined;
    return n + (c ? Math.min(lengths[i], c.duration) : 0);
  }, 0);
  const placed = beats.some((b) => b.visual.clip);

  const guard = async (label: string, fn: () => Promise<unknown>, done?: (r: never) => string | undefined) => {
    setWorking(label);
    try {
      const r = await fn();
      const message = done?.(r as never);
      if (message) toast.success(message);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setWorking(null);
    }
  };

  const upload = async (files: FileList | File[] | null | undefined) => {
    const list = Array.from(files ?? []);
    for (const [k, file] of list.entries()) {
      setAdding(`Adding ${file.name}${list.length > 1 ? ` (${k + 1} of ${list.length})` : ""}…`);
      try {
        await createApi.clipAdd(video.id, file);
        await refresh();
      } catch (e) {
        toast.error((e as Error).message);   // one bad file never stops the rest
      }
    }
    setAdding(null);
  };

  const busy = working !== null || adding !== null;
  const row = (i: number) => {
    const b = beats[i];
    const v = b.visual;
    const clip = v.clip ? byId.get(v.clip) : undefined;
    const set = (patch: Partial<{ clip: string; start: number | null; fill: ClipFill }>) =>
      void guard("placement", () => createApi.placement(video.id, {
        beat: i + 1, clip: v.clip ?? "", start: v.clip_start ?? null, fill: (v.fill ?? "auto") as ClipFill, ...patch }));
    return (
      <div key={i} className="grid items-center gap-2 py-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0 text-sm">
          <span className="tabular mr-2 text-xs text-subtle">{i + 1}</span>
          <span className="line-clamp-2 sm:line-clamp-1" title={b.text}>{b.text}</span>
          <span className="text-xs text-subtle"> about {seconds(lengths[i])}
            {clip && clip.duration < lengths[i] - 0.05 && <span className="text-warning"> · clip is {seconds(clip.duration)}</span>}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <select className={select} value={v.clip ?? ""} disabled={busy} aria-label={`Clip for sentence ${i + 1}`}
                  onChange={(e) => set({ clip: e.target.value, start: null })}>
            <option value="">Planned {v.kind === "diagram" ? "diagram" : "footage"}</option>
            {clips.filter((c) => !c.missing).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {clip && (
            <>
              <Tip label="Seconds into the clip to start from. Empty: carry on from where the last sentence using it stopped.">
                <input key={`${v.clip}-${v.clip_start ?? "auto"}`} type="number" min={0} step={0.1} disabled={busy} aria-label="Start second"
                       defaultValue={v.clip_start ?? ""} placeholder="auto"
                       onKeyDown={(e) => e.stopPropagation()}
                       onBlur={(e) => {
                         const raw = e.target.value.trim();
                         const next = raw === "" ? null : Number(raw);
                         if (next !== (v.clip_start ?? null)) set({ start: next });
                       }}
                       className={cn(select, "w-20")} />
              </Tip>
              <select className={select} value={v.fill ?? "auto"} disabled={busy} aria-label={FILL_HINT} title={FILL_HINT}
                      onChange={(e) => set({ fill: e.target.value as ClipFill })}>
                {(Object.keys(FILL_LABEL) as ClipFill[]).map((f) => <option key={f} value={f}>{FILL_LABEL[f]}</option>)}
              </select>
            </>
          )}
        </div>
      </div>
    );
  };

  const thumb = (c: MineClip) => (
    <a href={`/api/create/videos/${video.id}/clips/${c.id}/file`} target="_blank" rel="noopener noreferrer"
       className="block aspect-video w-24 shrink-0 overflow-hidden rounded-md bg-black" aria-label={`Play ${c.name}`}>
      <img src={`/api/create/videos/${video.id}/clips/${c.id}/thumb`} alt="" loading="lazy" className="size-full object-cover"
           onError={(e) => { e.currentTarget.style.display = "none"; }} />
    </a>
  );

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold"><Film className="size-4 text-accent" /> Your own clips</h3>
        <p className="mt-0.5 text-xs text-muted">
          Drop in clips you like. Clipper puts each on the sentence it fits, cuts it to the voice, and fills any time
          they don't cover with the planned footage and diagrams. Their sound isn't used, only your voice.
        </p>
      </div>

      <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
           onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy) void upload(e.dataTransfer.files); }}
           onClick={() => !busy && input.current?.click()} role="button" tabIndex={0}
           onKeyDown={(e) => e.key === "Enter" && !busy && input.current?.click()}
           className={cn("flex cursor-pointer items-center justify-center gap-2 rounded-md border-2 border-dashed px-4 py-4 text-sm",
             over ? "border-accent bg-accent-soft text-fg" : "border-line-strong text-muted hover:border-accent hover:text-fg",
             busy && "cursor-wait opacity-70")}>
        {adding ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
        {adding ?? "Drop video clips here, or click to choose them"}
        <input ref={input} type="file" accept="video/*,.mkv,.mov,.m4v,.avi" multiple className="hidden"
               onChange={(e) => { void upload(e.target.files); e.target.value = ""; }} />
      </div>

      {clips.length > 0 && (
        <>
          <ul className="flex flex-col gap-2">
            {clips.map((c) => (
              <li key={c.id} className="flex items-center gap-3">
                {thumb(c)}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium" title={c.name}>{c.name}</div>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                    <span className="tabular">{seconds(c.duration)} · {c.width}×{c.height}</span>
                    {c.missing && <Chip tone="danger">file missing</Chip>}
                    {c.low_res && <Chip tone="warning" title="Under 720p: it may look soft filling a phone screen">low resolution</Chip>}
                    {c.used.length ? <Chip tone="accent">sentence{c.used.length > 1 ? "s" : ""} {span(c.used)}</Chip> : <Chip>not placed</Chip>}
                  </div>
                </div>
                <Tip label="Remove this clip">
                  <Button size="icon" variant="ghost" aria-label={`Remove ${c.name}`} disabled={busy}
                          onClick={() => void guard("remove", () => createApi.clipRemove(video.id, c.id))}>
                    <Trash2 className="size-4" />
                  </Button>
                </Tip>
              </li>
            ))}
          </ul>

          <div className="flex flex-wrap items-center gap-2">
            <Tip label="Claude looks at your clips and puts each on the sentence it fits. Uses a little of your Claude plan.">
              <Button variant="primary" disabled={busy} onClick={() => void guard("ai", () => createApi.place(video.id, "ai", strict), (r: { note: string }) => r.note)}>
                {working === "ai" ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />} {working === "ai" ? "Looking at them…" : "Place them for me"}
              </Button>
            </Tip>
            <Tip label="Free, no AI: the clips in the order you added them, spread across the video.">
              <Button variant="secondary" disabled={busy} onClick={() => void guard("order", () => createApi.place(video.id, "order"), (r: { note: string }) => r.note)}>
                <Wand2 className="size-4" /> In order
              </Button>
            </Tip>
            {placed && (
              <Button variant="ghost" disabled={busy} onClick={() => void guard("clear", () => createApi.place(video.id, "clear"), (r: { note: string }) => r.note)}>
                Clear
              </Button>
            )}
            <label className="ml-auto flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={strict} onChange={(e) => setStrict(e.target.checked)} /> Only where they fit
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3 text-sm">
            <label className="flex items-center gap-2 text-muted">
              <Switch label="Place automatically when I build" checked={mine.auto}
                      onChange={(v) => void guard("settings", () => createApi.clipSettings(video.id, { auto: v }))} />
              Place them for me when I build, if I haven't
            </label>
            <label className="flex items-center gap-2 text-xs text-muted">
              {FILL_HINT}
              <select className={select} value={mine.fill} disabled={busy} aria-label={FILL_HINT}
                      onChange={(e) => void guard("settings", () => createApi.clipSettings(video.id, { fill: e.target.value as ClipFill }))}>
                {(Object.keys(FILL_LABEL) as ClipFill[]).map((f) => <option key={f} value={f}>{FILL_LABEL[f]}</option>)}
              </select>
            </label>
          </div>

          <p className={cn("text-xs", placed ? "text-muted" : "text-subtle")}>
            {placed
              ? <>Your clips cover about <b className="text-fg">{seconds(Math.min(covered, total))}</b> of about <b className="text-fg">{seconds(total)}</b>. The rest uses the planned footage and diagrams.</>
              : mine.auto ? "Nothing is placed yet: they'll be placed automatically when the video is built."
                : "Nothing is placed, and automatic placing is off: your clips won't be used until you place them."}
          </p>

          <details className="group">
            <summary className="cursor-pointer text-xs font-medium text-muted hover:text-fg">
              Where each clip goes, sentence by sentence
            </summary>
            <div className="mt-1 divide-y divide-line">{beats.map((_, i) => row(i))}</div>
          </details>

          {clips.some((c) => c.missing) && (
            <p className="flex items-center gap-1.5 text-xs text-warning"><AlertTriangle className="size-3.5" /> A clip's file is missing; its sentences use their planned picture.</p>
          )}
        </>
      )}

      {onRebuild && clips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <Button variant="secondary" disabled={busy || busyRebuild} onClick={onRebuild}>
            {busyRebuild ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} Rebuild with these clips
          </Button>
          <span className="text-xs text-muted">Same words and voice; takes about a minute.</span>
        </div>
      )}
      {!onRebuild && !video.voice && clips.length > 0 && (
        <p className="text-xs text-subtle">They're used when you drop the voice in.</p>
      )}
    </div>
  );
}
