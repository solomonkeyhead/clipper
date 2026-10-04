import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowDown, ArrowUp, CheckCircle2, ChevronRight, ClipboardCopy, Film, Lightbulb, Loader2, Mic, PenLine, Plus, RefreshCw, Shapes,
  Trash2, Upload, Wand2, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  createApi, useClips, useCreate, useCreateAI, type CreateScript, type CreateTopic, type CreateVideo, type CreateVisual,
} from "@/api/client";
import { Button, CaptionTitle, Card, Chip, EmptyState, Skeleton, Tip } from "@/components/ui";
import { useUI } from "@/lib/store";
import { cn, copyText } from "@/lib/utils";
import { MyClips } from "./MyClips";
import { ShotReview } from "./ShotReview";

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
          For <b className="text-fg">{data.channel.name}</b> ({data.channel.handle}). Pick an idea or write your own script, approve it, make the voice on
          ElevenLabs, drop it in: Clipper builds the rest. Every step has a manual option.
        </p>
        <AIStrip />
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
        {/* The video in progress comes first on a phone-width window; the ideas wait below it. */}
        <div className="order-2 lg:order-1"><Ideas topics={data.topics} /></div>
        <div className="order-1 flex flex-col gap-4 lg:order-2">
          <YourOwn />
          {data.videos.length === 0 && (
            <EmptyState icon={<Wand2 />} title="No videos yet" body="Pick an idea on the left and press Write it (about 20 seconds), or write your own script." />
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

/** Write the script yourself (D120): paste or type it, and Clipper cuts it into sentences, keeping every
 *  word. Pictures and the physics check are optional help, not a requirement. */
function YourOwn() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [description, setDescription] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [plan, setPlan] = useState(false);
  const [busy, setBusy] = useState(false);
  const count = text.split(/\s+/).filter(Boolean).length;
  const create = async () => {
    setBusy(true);
    try {
      await createApi.own({ title, text, description, hashtags, plan });
      toast.success("Script created", { description: plan ? "Pictures planned and physics checked." : "Choose the pictures yourself, or press Plan pictures." });
      setTitle(""); setText(""); setDescription(""); setHashtags(""); setOpen(false);
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);   // what was typed stays in the box
    } finally {
      setBusy(false);
    }
  };
  const { data: ready = [] } = useQuery({ queryKey: ["create", "ready"], queryFn: createApi.ready, staleTime: 60_000 });
  const start = async (name: string) => {
    setBusy(true);
    try {
      await createApi.useReady(name);
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!open) {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex"><Button variant="secondary" onClick={() => setOpen(true)}><PenLine className="size-4" /> Write your own script</Button></div>
        {ready.length > 0 && (
          <Card className="flex flex-col gap-1 p-3">
            <span className="text-xs font-medium text-muted">Ready-made: written and drawn already</span>
            {ready.map((r) => (
              <div key={r.name} className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{r.title}</div>
                  <div className="line-clamp-1 text-xs text-muted">{r.about}</div>
                </div>
                <Button size="sm" variant="primary" disabled={busy} onClick={() => void start(r.name)}>Use it</Button>
              </div>
            ))}
          </Card>
        )}
      </div>
    );
  }
  const field = "rounded-md border border-line bg-surface-2 px-3 py-2 text-sm focus:border-accent focus:outline-none";
  return (
    <Card className="flex flex-col gap-3 p-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><PenLine className="size-4 text-accent" /> Your own script</h2>
      <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional: the first sentence is used)" aria-label="Title" className={cn(field, "font-semibold")} />
      <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} aria-label="Script"
                placeholder={"Paste or type your script. Each sentence becomes one picture.\nPress Enter to cut it yourself: one line is one sentence."}
                className={cn(field, "resize-y")} />
      <p className={cn("text-xs", count && (count < 70 || count > 130) ? "text-warning" : "text-muted")}>
        {count} words · about {Math.round(count / 2.6)}s read aloud{count > 130 ? " · long for a Short" : count && count < 70 ? " · short for a Short" : ""}
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description for the post (optional)" aria-label="Description" className={cn(field, "resize-none")} />
        <textarea rows={2} value={hashtags} onChange={(e) => setHashtags(e.target.value)} placeholder="#hashtags (optional)" aria-label="Hashtags" className={cn(field, "resize-none")} />
      </div>
      <label className="flex items-start gap-2 text-sm text-muted">
        <input type="checkbox" className="mt-1" checked={plan} onChange={(e) => setPlan(e.target.checked)} />
        <span>Plan the pictures and check the physics for me. Your words are never changed. Leave it off to choose every picture yourself.</span>
      </label>
      <div className="flex items-center gap-2">
        <Button variant="primary" disabled={busy || !text.trim()} onClick={() => void create()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />} {busy ? (plan ? "Planning…" : "Creating…") : "Create"}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </Card>
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
  const qc = useQueryClient();
  const [open, setOpen] = useState(startOpen);
  const [confirm, setConfirm] = useState(false);
  // Opens when it becomes the video to work on; never closes on its own (it shut while in use, D127).
  useEffect(() => { if (startOpen) setOpen(true); }, [startOpen]);
  const s = video.script;
  const building = video.status === "voiced" || video.status === "building";
  /** Delete, from any state, so no video can get stuck on the page (D123). */
  const remove = async () => {
    try {
      const res = await createApi.remove(video.id) as { after_stop?: boolean };
      toast.success(res.after_stop ? "Stopping the build, then deleting it" : "Deleted",
                    { description: "Its files are in the Recycle Bin; a finished video stays in Clips." });
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setConfirm(false);
    }
  };
  return (
    <Card className={cn("flex flex-col", open && "ring-1 ring-line-strong")}>
      <div className="flex items-center gap-2 p-4">
        <button className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-3 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <span className="min-w-0">
            <span className="block truncate font-semibold">{s.title || "Untitled"}</span>
            <span className="text-xs text-muted">{s.beats?.length ?? 0} beats · ~{Math.round(words(s) / wps)}s</span>
          </span>
          <Steps status={video.status} />
        </button>
        {confirm ? (
          <span className="flex shrink-0 items-center gap-1 text-xs">
            <span className="text-muted">{building ? "Stop and delete?" : "Delete?"}</span>
            <Button size="sm" variant="danger" onClick={() => void remove()}>Delete</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>Keep</Button>
          </span>
        ) : (
          <Tip label={building ? "Stop the build and delete this video" : "Delete this video"}>
            <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label="Delete this video" onClick={() => setConfirm(true)}>
              <Trash2 className="size-4" />
            </Button>
          </Tip>
        )}
      </div>
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
        <div className="flex flex-wrap items-center gap-3">
          <p className="flex-1 text-xs text-muted">Timing your voice, finding footage, drawing the diagrams. About a minute; you can leave this page.</p>
          <Tip label="Stops at the next step. A video built before stays as it was.">
            <Button size="sm" variant="ghost" disabled={busy !== null || video.cancelling}
                    onClick={run("cancel", () => createApi.cancel(video.id), "Stopping the build")}>
              {video.cancelling || busy === "cancel" ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
              {video.cancelling ? "Stopping…" : "Cancel"}
            </Button>
          </Tip>
        </div>
      </div>
    );
  }
  if (video.status === "built" || (video.status === "failed" && video.clip_id)) {
    return <Built video={video} wps={wps} busy={busy} onPictures={run("pictures", () => createApi.pictures(video.id), "New pictures planned: building")}
                  onRebuild={run("rebuild", () => createApi.build(video.id), "Rebuilding: only the parts that changed are made again")} onRemove={remove} />;
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
          {video.topic_id !== null && (
            <Tip label="Write a different script for the same idea. Replaces what's here.">
              <Button variant="secondary" disabled={busy !== null} onClick={run("rewrite", () => createApi.rewrite(video.id), "A new take")}>
                {busy === "rewrite" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} {busy === "rewrite" ? "Writing…" : "Another take"}
              </Button>
            </Tip>
          )}
          <Tip label="Claude plans a picture for each sentence you haven't chosen one for, and checks the physics. Your words stay as written.">
            <Button variant="secondary" disabled={busy !== null} onClick={run("plan", () => createApi.plan(video.id), "Pictures planned")}>
              {busy === "plan" ? <Loader2 className="size-4 animate-spin" /> : <Shapes className="size-4" />} {busy === "plan" ? "Planning…" : "Plan pictures"}
            </Button>
          </Tip>
          <Tip label="Claude reads the script for physics mistakes and tells you; it changes nothing.">
            <Button variant="secondary" disabled={busy !== null} onClick={run("check", () => createApi.check(video.id), "Physics checked")}>
              {busy === "check" ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />} {busy === "check" ? "Checking…" : "Check physics"}
            </Button>
          </Tip>
          <Button variant="ghost" className="ml-auto" disabled={busy !== null} onClick={remove}><Trash2 className="size-4" /> Delete</Button>
        </div>
      ) : (
        <>
          <VoiceStep video={video} />
          {video.status === "approved" && (
            <Button variant="ghost" className="self-start" disabled={busy !== null} onClick={run("unapprove", () => createApi.edit(video.id, {}))}>
              <PenLine className="size-4" /> Edit the script again
            </Button>
          )}
        </>
      )}
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

const blankVisual = (): CreateVisual => ({ kind: "stock", query: "", queries: [], template: "", title: "", labels: [] });
type PictureKind = "auto" | "stock" | "card" | "sketch" | "hold";
const kindOf = (v: CreateVisual): PictureKind =>
  v.hold ? "hold" : !v.manual ? "auto" : v.kind === "stock" ? "stock" : v.template === "card" ? "card" : "sketch";

/** Choosing a sentence's picture yourself (D120): footage by your own search words, a chalk
 *  phrase, or a drawing you describe. "Automatic" hands it back to Clipper. */
function PictureChoice({ visual, emphasis, onChange, canHold }: {
  visual: CreateVisual; emphasis: string; onChange: (v: CreateVisual, emphasis?: string) => void; canHold: boolean;
}) {
  const kind = kindOf(visual);
  const value = kind === "stock" ? (visual.queries?.length ? visual.queries : [visual.query]).filter(Boolean).join(", ")
    : kind === "card" ? visual.title : kind === "sketch" ? visual.idea ?? "" : "";
  const placeholder = kind === "stock" ? "search words, e.g. skull, sound waves" : kind === "card" ? "the phrase to chalk on the board" : "what to draw";
  const pick = (raw: PictureKind) => {
    if (raw === "hold") return onChange({ ...visual, hold: true });
    const next = raw;
    visual = { ...visual, hold: false };
    if (next === "auto") return onChange({ ...visual, manual: false });
    if (next === "stock") return onChange({ ...visual, kind: "stock", manual: true, queries: visual.queries?.length ? visual.queries : visual.query ? [visual.query] : [] });
    if (next === "card") return onChange({ ...visual, kind: "diagram", template: "card", manual: true, title: visual.title || emphasis });
    return onChange({ ...visual, kind: "diagram", template: "sketch", manual: true, idea: visual.idea ?? "", sketch: null });
  };
  const commit = (text: string) => {
    const t = text.trim();
    if (kind === "stock") {
      const q = t.split(",").map((x) => x.trim()).filter(Boolean).slice(0, 3);
      return onChange({ ...visual, queries: q, query: q[0] ?? "" });
    }
    if (kind === "card") return onChange({ ...visual, title: t });
    if (kind === "sketch") return onChange({ ...visual, idea: t, sketch: null });
  };
  const field = "h-8 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <select value={kind} aria-label="Picture" className={field} onChange={(e) => pick(e.target.value as PictureKind)}>
        <option value="auto">Picture: automatic</option>
        <option value="stock">Footage I search for</option>
        <option value="card">Chalk phrase</option>
        <option value="sketch">Drawing I describe</option>
        {(canHold || kind === "hold") && <option value="hold">Keep the drawing above, building on</option>}
      </select>
      {kind !== "auto" && kind !== "hold" && (
        <input key={`${kind}-${value}`} defaultValue={value} placeholder={placeholder} aria-label={placeholder}
               onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") e.currentTarget.blur(); }}
               onBlur={(e) => e.target.value.trim() !== value && commit(e.target.value)}
               className={cn(field, "min-w-48 flex-1")} />
      )}
    </div>
  );
}

function ScriptEditor({ video, wps }: { video: CreateVideo; wps: number }) {
  const qc = useQueryClient();
  const s = video.script;
  const [beats, setBeats] = useState(s.beats);
  const [title, setTitle] = useState(s.title);
  const [description, setDescription] = useState(s.description ?? "");
  const [tags, setTags] = useState((s.hashtags ?? []).join(" "));
  useEffect(() => { setBeats(s.beats); setTitle(s.title); setDescription(s.description ?? ""); setTags((s.hashtags ?? []).join(" ")); }, [s]);
  const save = async (next: Partial<CreateScript>) => {
    try {
      await createApi.edit(video.id, next);
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  /** Structural changes (add, remove, move, a picture) save at once; text saves when you leave it. */
  const commit = (next: typeof beats) => { setBeats(next); void save({ beats: next }); };
  const patch = (i: number, change: Partial<(typeof beats)[number]>) => commit(beats.map((x, j) => (j === i ? { ...x, ...change } : x)));
  const move = (i: number, by: number) => {
    const next = [...beats];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    commit(next);
  };
  const n = words({ ...s, beats });
  const seconds = Math.round(n / wps);
  const locked = video.status !== "draft";
  const small = "size-7";
  return (
    <div className="flex flex-col gap-2">
      <input value={title} disabled={locked} onChange={(e) => setTitle(e.target.value)}
             onBlur={() => title !== s.title && void save({ title })} aria-label="Title"
             className="rounded-md border border-line bg-surface-2 px-3 py-2 font-semibold focus:border-accent focus:outline-none disabled:opacity-80" />
      <div className="flex flex-col gap-2">
        {beats.map((b, i) => (
          <div key={i} className={cn("flex flex-col gap-1.5", !locked && "rounded-md border border-line p-2")}>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_200px]">
              <textarea rows={2} value={b.text} disabled={locked} aria-label={`Sentence ${i + 1}`}
                        onChange={(e) => setBeats(beats.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
                        onBlur={() => b.text !== s.beats[i]?.text && void save({ beats })}
                        className="resize-none rounded-md border border-line bg-surface-1 px-3 py-2 text-sm focus:border-accent focus:outline-none disabled:opacity-80" />
              <Picture visual={b.visual} />
            </div>
            {!locked && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <PictureChoice visual={b.visual} emphasis={b.emphasis} canHold={i > 0 && (beats[i - 1].visual.kind === "diagram" || !!beats[i - 1].visual.hold)} onChange={(v, emphasis) => patch(i, { visual: v, ...(emphasis !== undefined ? { emphasis } : {}) })} />
                <Tip label="The word shown highlighted in the captions">
                  <input key={b.emphasis} defaultValue={b.emphasis} placeholder="highlight word" aria-label={`Highlight word, sentence ${i + 1}`}
                         onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") e.currentTarget.blur(); }}
                         onBlur={(e) => e.target.value.trim() !== b.emphasis && patch(i, { emphasis: e.target.value.trim() })}
                         className="h-8 w-32 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none" />
                </Tip>
                <span className="ml-auto flex items-center">
                  <Tip label="Move up"><Button size="icon" variant="ghost" className={small} aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp className="size-3.5" /></Button></Tip>
                  <Tip label="Move down"><Button size="icon" variant="ghost" className={small} aria-label="Move down" disabled={i === beats.length - 1} onClick={() => move(i, 1)}><ArrowDown className="size-3.5" /></Button></Tip>
                  <Tip label="Add a sentence below"><Button size="icon" variant="ghost" className={small} aria-label="Add a sentence below"
                    onClick={() => commit([...beats.slice(0, i + 1), { text: "New sentence.", emphasis: "", visual: blankVisual() }, ...beats.slice(i + 1)])}><Plus className="size-3.5" /></Button></Tip>
                  <Tip label="Remove this sentence"><Button size="icon" variant="ghost" className={small} aria-label="Remove this sentence" disabled={beats.length <= 1}
                    onClick={() => commit(beats.filter((_, j) => j !== i))}><X className="size-3.5" /></Button></Tip>
                </span>
              </div>
            )}
          </div>
        ))}
      </div>
      <p className={cn("text-xs", n < 70 || n > 130 ? "text-warning" : "text-muted")}>
        {n} words · about {seconds}s read aloud{n > 130 ? " · long for a Short" : n < 70 ? " · short for a Short" : ""}
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <textarea rows={2} value={description} disabled={locked} aria-label="Description" placeholder="Description for the post"
                  onChange={(e) => setDescription(e.target.value)}
                  onBlur={() => description !== (s.description ?? "") && void save({ description })}
                  className="resize-none rounded-md border border-line bg-surface-1 px-3 py-2 text-sm focus:border-accent focus:outline-none disabled:opacity-80" />
        <textarea rows={2} value={tags} disabled={locked} aria-label="Hashtags" placeholder="#hashtags, up to 5"
                  onChange={(e) => setTags(e.target.value)}
                  onBlur={() => tags !== (s.hashtags ?? []).join(" ") && void save({ hashtags: tags.split(/[\s,]+/).filter(Boolean) })}
                  className="resize-none rounded-md border border-line bg-surface-1 px-3 py-2 text-sm focus:border-accent focus:outline-none disabled:opacity-80" />
      </div>
    </div>
  );
}

function Picture({ visual }: { visual: CreateScript["beats"][number]["visual"] }) {
  if (visual.hold && !visual.clip) {
    return (
      <Tip label="The drawing above stays on screen through this sentence, its parts arriving as they're said.">
        <span className="flex items-center gap-1.5 self-start rounded-md border border-accent/40 px-2 py-1.5 text-xs text-accent">
          <Shapes className="size-3.5 shrink-0" /><span className="truncate">Same drawing, continued</span>
        </span>
      </Tip>
    );
  }
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

/** Which folds are open, per video, kept outside the components: the panel holding them is
 *  swapped out while a video builds, and a fold must come back as the user left it (D127). */
const folds = new Map<string, boolean>();

/** A section that opens and closes on a click, and stays as the user left it. */
function Fold({ id, title, startOpen = false, children }: { id: string; title: string; startOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(() => folds.get(id) ?? startOpen);
  const toggle = () => setOpen((o) => { folds.set(id, !o); return !o; });
  return (
    <section className="mt-1">
      <button type="button" onClick={toggle} aria-expanded={open}
              className="flex items-center gap-1.5 text-sm font-medium text-muted hover:text-fg">
        <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} /> {title}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </section>
  );
}

function Built({ video, wps, busy, onPictures, onRebuild, onRemove }: {
  video: CreateVideo; wps: number; busy: string | null; onPictures: () => void; onRebuild: () => void; onRemove: () => void;
}) {
  const { data: clips = [] } = useClips();
  const clip = clips.find((c) => c.id === video.clip_id);
  const player = useRef<HTMLVideoElement>(null);
  const seek = (t: number) => {
    const el = player.current;
    if (!el) return;
    el.currentTime = t;
    void el.play().catch(() => undefined);
  };
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-4">
        {clip?.file_exists ? (
          <video ref={player} key={clip.video} src={clip.video} poster={clip.thumb} controls playsInline className="aspect-[9/16] w-56 self-start rounded-xl bg-black" />
        ) : <div className="grid aspect-[9/16] w-56 place-items-center rounded-xl bg-surface-2 text-sm text-muted">Video not found</div>}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          {video.error && (
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-danger/40 p-3 text-sm text-danger">
              <AlertTriangle className="size-4 shrink-0" />
              <span className="flex-1">The last rebuild didn't finish: {video.error.replace(/\.?\s*$/, ".")} The video here is the one built before, unchanged.</span>
              <Button size="sm" variant="secondary" disabled={busy !== null} onClick={onRebuild}>Try again</Button>
            </div>
          )}
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
            <Tip label="Same words, voice and pictures, built again; only parts you asked to change, or that had no footage, are made again">
              <Button variant="secondary" disabled={busy !== null} onClick={onRebuild}>
                {busy === "rebuild" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} Build again
              </Button>
            </Tip>
            <Button variant="ghost" onClick={onRemove}><Trash2 className="size-4" /> Remove from Create</Button>
          </div>
        </div>
      </div>
      <Fold id={`review-${video.id}`} title="Change parts you don't like" startOpen>
        <ShotReview video={video} seek={seek} onRebuild={onRebuild} rebuilding={busy === "rebuild"} />
      </Fold>
      <Fold id={`clips-${video.id}`} title={`Your own clips${video.mine?.clips.length ? ` (${video.mine.clips.length})` : ""}`}
              startOpen={(video.mine?.clips.length ?? 0) > 0}>
        <MyClips video={video} wps={wps} onRebuild={onRebuild} busyRebuild={busy === "rebuild"} />
      </Fold>
    </div>
  );
}
