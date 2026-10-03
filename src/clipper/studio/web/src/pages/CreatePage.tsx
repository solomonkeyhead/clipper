import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, CheckCircle2, ClipboardCopy, Film, Lightbulb, Loader2, Mic, PenLine, RefreshCw, Shapes, Trash2, Upload,
  Wand2, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { createApi, useClips, useCreate, useCreateAI, type CreateScript, type CreateTopic, type CreateVideo } from "@/api/client";
import { Button, CaptionTitle, Card, Chip, EmptyState, Skeleton, Tip } from "@/components/ui";
import { useUI } from "@/lib/store";
import { cn, copyText } from "@/lib/utils";
import { MyClips } from "./MyClips";

/** Create (D108): original Shorts for your own channel -- idea, script, your voice, built. */
export function CreatePage() {
  const { data, isLoading } = useCreate();
  if (isLoading || !data) return <div className="flex flex-col gap-4"><Skeleton className="h-12 w-80" /><Skeleton className="h-96" /></div>;
  const active = data.videos.find((v) => v.status !== "built")?.id ?? data.videos[0]?.id ?? null;
  return (
    <div className="fade-in flex flex-col gap-6">
      <div>
        <CaptionTitle text="Make a Short" hi="Short" className="text-[clamp(1.9rem,3.2vw,2.6rem)]" />
        <p className="mt-2 text-sm text-muted">
          For <b className="text-fg">{data.channel.name}</b> ({data.channel.handle}). Pick an idea, approve the script, make the voice on
          ElevenLabs, drop it in: Clipper builds the rest.
        </p>
        <AIStrip />
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
        {/* The video in progress comes first on a phone-width window; the ideas wait below it. */}
        <div className="order-2 lg:order-1"><Ideas topics={data.topics} /></div>
        <div className="order-1 flex flex-col gap-4 lg:order-2">
          {data.videos.length === 0 && (
            <EmptyState icon={<Wand2 />} title="No videos yet" body="Pick an idea on the left and press Write it: the script takes about 20 seconds." />
          )}
          {data.videos.map((v) => <VideoCard key={v.id} video={v} open={v.id === active} wps={data.channel.words_per_second} />)}
        </div>
      </div>
    </div>
  );
}

/** Which AI writes and draws, always in view: Claude or Gemini was a guess for two videos (D118). */
function AIStrip() {
  const { data: ai } = useCreateAI();
  if (!ai) return null;
  const first = ai.order[0] ?? "";
  const claude = first.startsWith("claude_code") || first.startsWith("anthropic");
  const miss = Object.entries(ai.misses)[0];
  const text = ai.problem ? ai.problem
    : claude ? `Written and drawn by ${first.split(":").pop()} on your Claude plan.`
    : `Claude isn't set up: ${first.split(":").pop() || "no AI"} will write and draw.`;
  return (
    <p className={cn("mt-1 flex flex-wrap items-center gap-x-2 text-xs", claude && !ai.problem ? "text-muted" : "text-warning")}>
      {claude && !ai.problem ? <CheckCircle2 className="size-3.5 text-success" /> : <AlertTriangle className="size-3.5" />}
      {text}
      {miss && <span className="text-subtle">Last problem: {miss[1]}</span>}
      {ai.spent_usd > 0 && <span className="text-subtle">This session so far: about ${ai.spent_usd.toFixed(2)} at API prices (free on a plan).</span>}
    </p>
  );
}

/* ---------- ideas ---------- */

function Ideas({ topics }: { topics: CreateTopic[] }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<number | "more" | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["create"] });
  const more = async () => {
    setBusy("more");
    try {
      const { added } = await createApi.ideas(20);
      toast.success(`${added} new idea${added === 1 ? "" : "s"}`);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const write = async (t: CreateTopic) => {
    setBusy(t.id);
    try {
      await createApi.script(t.id);
      await refresh();
    } catch (e) {
      toast.error("Couldn't write it", { description: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card className="flex max-h-[78vh] flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><Lightbulb className="size-4 text-accent" /> Ideas <span className="font-normal text-subtle">{topics.length}</span></h2>
        <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => void more()}>
          {busy === "more" ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />} More ideas
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {topics.length === 0 && <p className="p-4 text-sm text-muted">No ideas left. Press More ideas.</p>}
        {topics.map((t) => (
          <div key={t.id} className="group flex items-start gap-2 border-b border-line px-4 py-3 last:border-0">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{t.question}</div>
              <div className="mt-0.5 text-xs text-muted">{t.angle}</div>
              {t.felt ? <Chip tone="accent" className="mt-1.5 h-5 text-[11px]">you feel this one</Chip> : null}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => void write(t)}>
                {busy === t.id ? <Loader2 className="size-3.5 animate-spin" /> : <PenLine className="size-3.5" />} {busy === t.id ? "Writing…" : "Write it"}
              </Button>
              <Tip label="Not this one">
                <Button size="icon" variant="ghost" className="size-7 opacity-60 group-hover:opacity-100" aria-label="Skip this idea"
                        onClick={() => void createApi.skip(t.id).then(() => qc.invalidateQueries({ queryKey: ["create"] }))}>
                  <X className="size-3.5" />
                </Button>
              </Tip>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

/* ---------- a video, step by step ---------- */

const STEPS = [["Script", ["draft"]], ["Voice", ["approved"]], ["Build", ["voiced", "building", "failed"]], ["Ready", ["built"]]] as const;

function Steps({ status }: { status: CreateVideo["status"] }) {
  const at = STEPS.findIndex(([, s]) => (s as readonly string[]).includes(status));
  return (
    <div className="flex items-center gap-1.5 text-xs">
      {STEPS.map(([label], i) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className={cn("rounded-full px-2 py-0.5 font-semibold",
            i < at || status === "built" ? "bg-accent-soft text-accent" : i === at ? "bg-accent text-accent-fg" : "bg-surface-2 text-subtle",
            status === "failed" && i === at && "bg-[color-mix(in_oklch,var(--danger)_18%,transparent)] text-danger")}>
            {label}
          </span>
          {i < STEPS.length - 1 && <span className="h-px w-3 bg-line-strong" />}
        </span>
      ))}
    </div>
  );
}

function VideoCard({ video, open: startOpen, wps }: { video: CreateVideo; open: boolean; wps: number }) {
  const [open, setOpen] = useState(startOpen);
  useEffect(() => setOpen(startOpen), [startOpen]);
  const s = video.script;
  return (
    <Card className={cn("flex flex-col", open && "ring-1 ring-line-strong")}>
      <button className="flex flex-wrap items-center justify-between gap-3 p-4 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="min-w-0">
          <span className="block truncate font-semibold">{s.title || "Untitled"}</span>
          <span className="text-xs text-muted">{s.beats?.length ?? 0} beats · ~{Math.round(words(s) / wps)}s</span>
        </span>
        <Steps status={video.status} />
      </button>
      {open && <div className="border-t border-line p-4"><Body video={video} wps={wps} /></div>}
    </Card>
  );
}

const words = (s: CreateScript) => (s.beats ?? []).reduce((n, b) => n + b.text.split(/\s+/).filter(Boolean).length, 0);

function Body({ video, wps }: { video: CreateVideo; wps: number }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["create"] });
  const [busy, setBusy] = useState<string | null>(null);
  const run = (label: string, fn: () => Promise<unknown>, done?: string) => async () => {
    setBusy(label);
    try {
      await fn();
      if (done) toast.success(done);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const remove = run("delete", () => createApi.remove(video.id), "Removed from Create");

  if (video.status === "voiced" || video.status === "building") {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex justify-between text-sm"><span>{video.stage ?? "Starting…"}</span><span className="tabular text-muted">{Math.round(video.pct ?? 0)}%</span></div>
        <div className="h-2 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(3, video.pct ?? 0)}%` }} /></div>
        <p className="text-xs text-muted">Timing your voice, finding footage, drawing the diagrams. About a minute; you can leave this page.</p>
      </div>
    );
  }
  if (video.status === "built") {
    return <Built video={video} wps={wps} busy={busy} onPictures={run("pictures", () => createApi.pictures(video.id), "New pictures planned: building")}
                  onRebuild={run("rebuild", () => createApi.build(video.id), "Building again with your clips")} onRemove={remove} />;
  }

  return (
    <div className="flex flex-col gap-4">
      {video.status === "failed" && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-danger/40 p-3 text-sm text-danger">
          <AlertTriangle className="size-4 shrink-0" /> <span className="flex-1">{video.error || "The build failed."}</span>
          {video.voice && <Button size="sm" variant="secondary" onClick={run("build", () => createApi.build(video.id))}>Try again</Button>}
        </div>
      )}
      <CheckNote text={video.check_notes} />
      <ScriptEditor video={video} wps={wps} />
      <MyClips video={video} wps={wps} onRebuild={video.status === "failed" && video.voice ? run("rebuild", () => createApi.build(video.id)) : undefined}
               busyRebuild={busy === "rebuild"} />
      {video.status === "draft" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={busy !== null} onClick={run("approve", () => createApi.approve(video.id))}>
            <CheckCircle2 className="size-4" /> Approve script
          </Button>
          <Button variant="secondary" disabled={busy !== null} onClick={run("rewrite", () => createApi.rewrite(video.id), "A new take")}>
            {busy === "rewrite" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} {busy === "rewrite" ? "Writing…" : "Another take"}
          </Button>
          <Button variant="ghost" className="ml-auto" disabled={busy !== null} onClick={remove}><Trash2 className="size-4" /> Delete</Button>
        </div>
      ) : <VoiceStep video={video} />}
    </div>
  );
}

function CheckNote({ text }: { text: string }) {
  if (!text) return null;
  const clean = text.startsWith("Physics check: no problems");
  return (
    <div className={cn("rounded-md border p-3 text-xs whitespace-pre-line",
      clean ? "border-success/40 text-success" : "border-warning/40 text-warning")}>
      {text}
    </div>
  );
}

function ScriptEditor({ video, wps }: { video: CreateVideo; wps: number }) {
  const qc = useQueryClient();
  const s = video.script;
  const [beats, setBeats] = useState(s.beats);
  const [title, setTitle] = useState(s.title);
  useEffect(() => { setBeats(s.beats); setTitle(s.title); }, [s]);
  const save = async (next: Partial<CreateScript>) => {
    try {
      await createApi.edit(video.id, next);
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const n = words({ ...s, beats });
  const seconds = Math.round(n / wps);
  const locked = video.status !== "draft";
  return (
    <div className="flex flex-col gap-2">
      <input value={title} disabled={locked} onChange={(e) => setTitle(e.target.value)}
             onBlur={() => title !== s.title && void save({ title })} aria-label="Title"
             className="rounded-md border border-line bg-surface-2 px-3 py-2 font-semibold focus:border-accent focus:outline-none disabled:opacity-80" />
      <div className="flex flex-col gap-1.5">
        {beats.map((b, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_200px]">
            <textarea rows={2} value={b.text} disabled={locked} aria-label={`Sentence ${i + 1}`}
                      onChange={(e) => setBeats(beats.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
                      onBlur={() => b.text !== s.beats[i]?.text && void save({ beats })}
                      className="resize-none rounded-md border border-line bg-surface-1 px-3 py-2 text-sm focus:border-accent focus:outline-none disabled:opacity-80" />
            <Picture visual={b.visual} />
          </div>
        ))}
      </div>
      <p className={cn("text-xs", n < 70 || n > 130 ? "text-warning" : "text-muted")}>
        {n} words · about {seconds}s read aloud{n > 130 ? " · long for a Short" : n < 70 ? " · short; Another take may give more" : ""}
      </p>
    </div>
  );
}

function Picture({ visual }: { visual: CreateScript["beats"][number]["visual"] }) {
  if (visual.clip) {
    return (
      <Tip label="Your own clip. The planned picture fills any time it doesn't cover.">
        <span className="flex items-center gap-1.5 self-start rounded-md border border-accent bg-accent-soft px-2 py-1.5 text-xs text-accent">
          <Film className="size-3.5 shrink-0" /><span className="truncate">Your clip</span>
        </span>
      </Tip>
    );
  }
  const diagram = visual.kind === "diagram";
  const tip = diagram ? `${visual.template} diagram${visual.labels.length ? `: ${visual.labels.join(" · ")}` : ""}`
    : `Stock footage: ${(visual.queries?.length ? visual.queries : [visual.query]).join(" / ")}${visual.card ? `. If none fits, "${visual.card}" on the board` : ""}`;
  return (
    <Tip label={tip}>
      <span className={cn("flex items-center gap-1.5 self-start rounded-md border px-2 py-1.5 text-xs",
        diagram ? "border-accent/40 text-accent" : "border-line text-muted")}>
        {diagram ? <Shapes className="size-3.5 shrink-0" /> : <Film className="size-3.5 shrink-0" />}
        <span className="truncate">{diagram ? `Diagram: ${visual.title || visual.template}` : visual.query}</span>
      </span>
    </Tip>
  );
}

/* ---------- the voice, by hand on ElevenLabs ---------- */

function VoiceStep({ video }: { video: CreateVideo }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const text = video.script.beats.map((b) => b.text.trim()).join(" ");
  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    try {
      await createApi.voice(video.id, file);
      toast.success("Got the voice", { description: "Building your Short now: about a minute." });
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-accent/30 bg-accent-soft/40 p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Mic className="size-4 text-accent" /> Make the voice</h3>
      <Step n={1}>
        <span className="flex-1">Copy the script.</span>
        <Button size="sm" variant="primary" onClick={() => void copyText(text, "Script")}><ClipboardCopy className="size-3.5" /> Copy script</Button>
      </Step>
      <Step n={2}>
        <span className="flex-1">On <a href="https://elevenlabs.io/app/speech-synthesis/text-to-speech" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">ElevenLabs</a>, choose <b>Marshal</b>, paste, generate, download.</span>
      </Step>
      <Step n={3}>
        <div
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); void upload(e.dataTransfer.files[0]); }}
          onClick={() => input.current?.click()} role="button" tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
          className={cn("flex w-full cursor-pointer items-center justify-center gap-2 rounded-md border-2 border-dashed px-4 py-6 text-sm",
            over ? "border-accent bg-accent-soft text-fg" : "border-line-strong text-muted hover:border-accent hover:text-fg")}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
          {busy ? "Uploading…" : "Drop the MP3 here, or click to choose it"}
          <input ref={input} type="file" accept="audio/*" className="hidden" onChange={(e) => void upload(e.target.files?.[0])} />
        </div>
      </Step>
    </div>
  );
}

const Step = ({ n, children }: { n: number; children: ReactNode }) => (
  <div className="flex items-center gap-3 text-sm">
    <span className="grid size-6 shrink-0 place-items-center rounded-full bg-accent text-xs font-bold text-accent-fg">{n}</span>
    {children}
  </div>
);

/* ---------- done ---------- */

function Built({ video, wps, busy, onPictures, onRebuild, onRemove }: {
  video: CreateVideo; wps: number; busy: string | null; onPictures: () => void; onRebuild: () => void; onRemove: () => void;
}) {
  const { data: clips = [] } = useClips();
  const clip = clips.find((c) => c.id === video.clip_id);
  return (
    <div className="flex flex-wrap gap-4">
      {clip?.file_exists ? (
        <video key={clip.video} src={clip.video} poster={clip.thumb} controls playsInline className="aspect-[9/16] w-56 rounded-xl bg-black" />
      ) : <div className="grid aspect-[9/16] w-56 place-items-center rounded-xl bg-surface-2 text-sm text-muted">Video not found</div>}
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p className="text-sm">It's in your library under <b>{video.script.title}</b>, with the title, description and hashtags ready to copy.</p>
        {/* Who wrote and drew it, and what didn't work, stay visible after the build (D118). */}
        <CheckNote text={video.check_notes} />
        <div className="flex flex-wrap gap-2">
          {clip && <Button variant="primary" onClick={() => useUI.getState().setOpenClip(clip.id)}>Open to post</Button>}
          <Tip label="Same words and voice: footage and diagrams planned again, then built">
            <Button variant="secondary" disabled={busy !== null} onClick={onPictures}>
              {busy === "pictures" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} {busy === "pictures" ? "Planning…" : "New pictures"}
            </Button>
          </Tip>
          <Button variant="ghost" onClick={onRemove}><Trash2 className="size-4" /> Remove from Create</Button>
        </div>
        <details className="mt-1" open={video.mine?.clips.length > 0 || undefined}>
          <summary className="cursor-pointer text-sm font-medium text-muted hover:text-fg">
            Your own clips{video.mine?.clips.length ? ` (${video.mine.clips.length})` : ""}
          </summary>
          <div className="mt-2"><MyClips video={video} wps={wps} onRebuild={onRebuild} busyRebuild={busy === "rebuild"} /></div>
        </details>
      </div>
    </div>
  );
}
