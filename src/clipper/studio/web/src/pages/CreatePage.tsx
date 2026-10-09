import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle, Archive, ArchiveRestore, ArrowDown, ArrowUp, CheckCircle2, ChevronRight, ClipboardCopy, Film, Lightbulb, Loader2, Mic, PenLine, Plus, RefreshCw, Shapes,
  ExternalLink, Trash2, Upload, Wand2, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { toast } from "sonner";
import {
  createApi, useClips, useCreate, useCreateAI, type CreatePost, type CreateScript, type CreateTopic, type CreateVideo, type CreateVisual,
} from "@/api/client";
import { Button, CaptionTitle, Card, Chip, EmptyState, Skeleton, Tip } from "@/components/ui";
import { useUI } from "@/lib/store";
import { cn, copyText } from "@/lib/utils";
import { ChannelBar, NewChannel } from "./ChannelPanel";
import { MyClips } from "./MyClips";
import { CAMERAS, ShotReview } from "./ShotReview";

/** Create (D108): original Shorts for your own channel -- idea, script, your voice, built. */
/** "physics" on a physics channel, "facts" on any other: what the fact check is called in the buttons (D146). */
function useFactWord() {
  const { data } = useCreate();
  return data?.channel.check_name === "Physics check" ? "physics" : "facts";
}

export function CreatePage() {
  const { data, isLoading } = useCreate();
  if (isLoading || !data) return <div className="flex flex-col gap-4"><Skeleton className="h-12 w-80" /><Skeleton className="h-96" /></div>;
  // The first time: make a channel before anything else (D146).
  if (data.channels.length === 0) {
    return (
      <div className="fade-in flex max-w-3xl flex-col gap-6">
        <CaptionTitle text="Make a Short" hi="Short" className="text-[clamp(1.9rem,3.2vw,2.6rem)]" />
        <NewChannel data={data} onDone={() => undefined} first />
      </div>
    );
  }
  // Posted videos move to the archive (D131): the list above it is what's still being worked on.
  const work = data.videos.filter((v) => !v.archived);
  const archived = data.videos.filter((v) => v.archived);
  const active = work.find((v) => v.status !== "built")?.id ?? work[0]?.id ?? null;
  return (
    <div className="fade-in flex flex-col gap-6">
      <div>
        <CaptionTitle text="Make a Short" hi="Short" className="text-[clamp(1.9rem,3.2vw,2.6rem)]" />
        <p className="mt-2 text-sm text-muted">
          For <b className="text-fg">{data.channel.name}</b>{data.channel.handle && ` (${data.channel.handle})`}. Pick an idea or write your own script, approve it,
          add your voice: Clipper builds the rest. Every step has a manual option.
        </p>
        <AIStrip />
        <div className="mt-3"><ChannelBar data={data} /></div>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 lg:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
        {/* The video in progress comes first on a phone-width window; the ideas wait below it. */}
        <div className="order-2 lg:order-1"><Ideas topics={data.topics} /></div>
        <div className="order-1 flex flex-col gap-4 lg:order-2">
          <YourOwn wps={data.channel.words_per_second} />
          {work.length === 0 && (
            archived.length ? <p className="text-sm text-muted">Nothing in progress: everything you've made is in the Archive below.</p>
              : <EmptyState icon={<Wand2 />} title="No videos yet" body="Pick an idea on the left and press Write it (about 20 seconds), or write your own script." />
          )}
          {work.map((v) => <VideoCard key={v.id} video={v} open={v.id === active} wps={data.channel.words_per_second} />)}
          {archived.length > 0 && <ArchiveList videos={archived} wps={data.channel.words_per_second} />}
        </div>
      </div>
    </div>
  );
}

/** The archive (D131): posted videos, out of the way but whole. Each opens as it always did, to
 *  watch, copy, change or rebuild, and "Back to Create" returns it to the list above. */
function ArchiveList({ videos, wps }: { videos: CreateVideo[]; wps: number }) {
  const [open, setOpen] = useState(() => folds.get("archive") ?? false);
  const toggle = () => setOpen((o) => { folds.set("archive", !o); return !o; });
  return (
    <section className="flex flex-col gap-3">
      <button type="button" onClick={toggle} aria-expanded={open}
              className="flex items-center gap-2 self-start text-sm font-semibold text-muted hover:text-fg">
        <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        <Archive className="size-4" /> Archive <span className="font-normal text-subtle">{videos.length}</span>
      </button>
      {open && (
        <>
          <p className="-mt-1 text-xs text-muted">Videos move here once they're posted (found on your channel, or marked posted in Clips). You can also archive any video yourself.</p>
          {videos.map((v) => <VideoCard key={v.id} video={v} open={false} wps={wps} />)}
        </>
      )}
    </section>
  );
}

const PLATFORMS: Record<string, string> = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", x: "X" };

/** Where a video is posted, with its views and a link to each post (D131). */
function PostedLine({ video }: { video: CreateVideo }) {
  if (!video.posted) return null;
  const posts = video.posts ?? [];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <CheckCircle2 className="size-4 text-success" />
      {posts.length === 0 ? <span>Marked posted in Clips.</span> : posts.map((p: CreatePost) => (
        <a key={p.url} href={p.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-accent hover:underline">
          {PLATFORMS[p.platform] ?? p.platform}{p.views != null ? ` · ${p.views.toLocaleString()} views` : ""}
          <ExternalLink className="size-3.5" />
        </a>
      ))}
      {!video.archived && <span className="text-xs text-muted">You brought it back from the Archive, so it stays here until you archive it.</span>}
    </div>
  );
}

/** While a build waits on an overloaded Gemini, say so and offer Claude for the footage step (D154): a
 *  build stuck at one number for minutes looked dead. Changing it takes effect on the next AI call. */
function GeminiStruggling() {
  const { data: ai } = useCreateAI();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const miss = Object.entries(ai?.misses ?? {}).find(([k]) => k.startsWith("gemini"));
  if (!ai || !miss) return null;
  const claude = ai.order.some((b) => b.startsWith("claude_code"));
  const until = /until (\d\d:\d\d)/.exec(miss[1])?.[1];   // a day's quota says when it's back (D168)
  const useClaude = async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/ai-jobs", { method: "PUT", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ job: "footage", choice: "claude_plan" }) });
      if (!res.ok) throw new Error("Couldn't change it");
      toast.success("Claude picks the footage from now on. Change it back in Settings, Who does what.");
      void qc.invalidateQueries({ queryKey: ["ai-jobs"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md bg-[color-mix(in_oklch,var(--warning)_10%,transparent)] px-3 py-2 text-xs text-warning">
      <AlertTriangle className="size-3.5" />
      <span className="flex-1">{/quota/i.test(miss[1])
        ? `Gemini's free quota is used up${until ? ` until ${until}` : " for now"}, so the other free models answer, more slowly. It hasn't stopped.`
        : "Gemini is overloaded and Clipper is asking the next model, so this is slow. It hasn't stopped."}</span>
      {claude && (
        <Tip label="Uses your Claude plan's usage for the rest of this build and later ones.">
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => void useClaude()}>
            {busy && <Loader2 className="size-3.5 animate-spin" />} Use Claude for footage
          </Button>
        </Tip>
      )}
    </div>
  );
}

function minutesAgo(seconds: number | undefined) {
  const m = Math.round((seconds ?? 0) / 60);
  return m < 1 ? "just now" : `${m} min ago`;
}

/** Each AI provider by its own name: a miss on Mistral or NVIDIA was labelled "Claude had a problem" (D171). */
const PROVIDER: Record<string, string> = { gemini: "Gemini", claude_code: "Claude", anthropic: "Claude", mistral: "Mistral",
  nvidia: "NVIDIA", groq: "Groq", openrouter: "OpenRouter", ollama: "Ollama" };

/** Which AI does what, always in view: Claude or Gemini was a guess for two videos (D118). */
function AIStrip() {
  const { data: ai } = useCreateAI();
  if (!ai) return null;
  const first = ai.order[0] ?? "";
  const claude = first.startsWith("claude_code") || first.startsWith("anthropic");
  const miss = Object.entries(ai.misses)[0];
  const text = ai.problem ? ai.problem
    : claude ? `Drawings by ${first.split(":").pop()} ${first.startsWith("anthropic") ? `on your paid Claude API key (it does only: ${ai.paid_api_jobs.join(", ")})` : "on your Claude plan"}.${ai.gemini_jobs.length ? ` Gemini does ${ai.gemini_jobs.join(", ")}.` : ""}`
    : `Claude isn't set up: ${first.split(":").pop() || "no AI"} will write and draw.`;
  return (
    <p className={cn("mt-1 flex flex-wrap items-center gap-x-2 text-xs", claude && !ai.problem ? "text-muted" : "text-warning")}>
      {claude && !ai.problem ? <CheckCircle2 className="size-3.5 text-success" /> : <AlertTriangle className="size-3.5" />}
      {text}
      {miss && <span className="text-subtle">{PROVIDER[miss[0].split(":")[0]] ?? miss[0].split(":")[0]} had a problem {minutesAgo(ai.missed_ago_s[miss[0]])}: {miss[1]}</span>}
    </p>
  );
}

/** Write the script yourself (D120): paste or type it, and Clipper cuts it into sentences, keeping every
 *  word. Pictures and the physics check are optional help, not a requirement. */
function YourOwn({ wps }: { wps: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [description, setDescription] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [plan, setPlan] = useState(false);
  const fact = useFactWord();
  const [busy, setBusy] = useState(false);
  const count = text.split(/\s+/).filter(Boolean).length;
  const create = async () => {
    setBusy(true);
    try {
      await createApi.own({ title, text, description, hashtags, plan });
      toast.success("Script created", { description: plan ? `Pictures planned and ${fact} checked.` : "Choose the pictures yourself, or press Plan pictures." });
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
            {ready.map((r) => {
              // What's been made from it already (D131): posted ones are in the Archive.
              const posted = r.made.filter((m) => m.archived).length;
              const going = r.made.length - posted;
              return (
                <div key={r.name} className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{r.title}</span>
                      {posted > 0 && <Chip tone="success" className="h-5 shrink-0 text-[11px]">in the Archive</Chip>}
                      {going > 0 && <Chip className="h-5 shrink-0 text-[11px]">{going === 1 ? "made, below" : `${going} made, below`}</Chip>}
                    </div>
                    <div className="line-clamp-1 text-xs text-muted">{r.about}</div>
                  </div>
                  <Button size="sm" variant={r.made.length ? "secondary" : "primary"} disabled={busy} onClick={() => void start(r.name)}>
                    {r.made.length ? "Make again" : "Use it"}
                  </Button>
                </div>
              );
            })}
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
        {count} words · about {Math.round(count / wps)}s read aloud{count > 130 ? " · long for a Short" : count && count < 70 ? " · short for a Short" : ""}
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description for the post (optional)" aria-label="Description" className={cn(field, "resize-none")} />
        <textarea rows={2} value={hashtags} onChange={(e) => setHashtags(e.target.value)} placeholder="#hashtags (optional)" aria-label="Hashtags" className={cn(field, "resize-none")} />
      </div>
      <label className="flex items-start gap-2 text-sm text-muted">
        <input type="checkbox" className="mt-1" checked={plan} onChange={(e) => setPlan(e.target.checked)} />
        <span>Plan the pictures and check the {fact} for me. Your words are never changed. Leave it off to choose every picture yourself.</span>
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
  const [steer, setSteer] = useState("");
  const [find, setFind] = useState("");
  const words = find.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = words.length ? topics.filter((t) => words.every((w) => `${t.question} ${t.angle} ${t.series ?? ""}`.toLowerCase().includes(w))) : topics;
  const refresh = () => qc.invalidateQueries({ queryKey: ["create"] });
  const more = async () => {
    setBusy("more");
    try {
      const { added } = await createApi.ideas(20, steer);
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
      <div className="border-b border-line px-4 py-2">
        <input value={steer} onChange={(e) => setSteer(e.target.value)} aria-label="Steer the next ideas"
               onKeyDown={(e) => { if (e.key === "Enter" && busy === null) void more(); }}
               placeholder="Steer the next batch, e.g. only light and sound, no biology"
               className="h-8 w-full rounded-sm border border-line bg-surface-2 px-2.5 text-xs outline-none placeholder:text-subtle focus:border-accent" />
        <p className="mt-1 text-[11px] text-subtle">Lasting guidance is under Channel settings. Skipping an idea teaches it what to avoid.</p>
      </div>
      {(topics.length > 10 || find) && (
        <div className="border-b border-line px-4 py-2">
          <input value={find} onChange={(e) => setFind(e.target.value)} aria-label="Search the ideas" type="search"
                 placeholder={`Search ${topics.length} ideas`}
                 className="h-8 w-full rounded-sm border border-line bg-surface-2 px-2.5 text-xs outline-none placeholder:text-subtle focus:border-accent" />
        </div>
      )}
      <div className="flex-1 overflow-y-auto">
        {topics.length === 0 && <p className="p-4 text-sm text-muted">No ideas to pick from. Press More ideas for some.</p>}
        {topics.length > 0 && shown.length === 0 && <p className="p-4 text-sm text-muted">No idea matches “{find}”.</p>}
        {shown.map((t) => (
          <div key={t.id} className="group flex items-start gap-2 border-b border-line px-4 py-3 last:border-0">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{t.question}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
                <span>{t.angle}</span>
                {/* The planner's score of 21 and series (D155); clicking a series shows only its ideas. */}
                {t.score != null && <Tip label="The planner's score out of 21: felt, common, surprise, one mechanism, showable, searched, fits the channel"><span className="tabular-nums text-subtle">{t.score}/21</span></Tip>}
                {t.series && <button type="button" onClick={() => setFind(t.series ?? "")} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] hover:text-fg">{t.series}</button>}
              </div>
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
  // Remembered per video, so a card moving into or out of the Archive stays as it was (D131).
  const [open, setOpenState] = useState(() => folds.get(`card-${video.id}`) ?? startOpen);
  const setOpen = (next: boolean | ((o: boolean) => boolean)) =>
    setOpenState((o) => { const v = typeof next === "function" ? next(o) : next; folds.set(`card-${video.id}`, v); return v; });
  const [confirm, setConfirm] = useState(false);
  // Opens when it becomes the video to work on; never closes on its own (it shut while in use, D127).
  useEffect(() => { if (startOpen) setOpen(true); }, [startOpen]);
  const s = video.script;
  const building = video.status === "voiced" || video.status === "building";
  /** Delete, from any state, so no video can get stuck on the page (D123). */
  const archive = async (on: boolean) => {
    try {
      await createApi.archive(video.id, on);
      toast.success(on ? "Moved to the Archive" : "Back in Create", { description: on ? "Open the Archive at the bottom of the list to find it." : undefined });
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const remove = async (withClip = false) => {
    try {
      const res = await createApi.remove(video.id, withClip) as { after_stop?: boolean };
      toast.success(res.after_stop ? "Stopping the build, then deleting it" : "Deleted",
                    { description: withClip ? "Its files are in the Recycle Bin and its clip is in Clips' trash." : "Its files are in the Recycle Bin; a finished video stays in Clips." });
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
            <span className="text-xs text-muted">
              {s.beats?.length ?? 0} beats · ~{Math.round(words(s) / wps)}s
              {video.archived && video.archived_at ? ` · archived ${video.archived_at.slice(0, 10)}` : ""}
            </span>
          </span>
          <Steps status={video.status} />
        </button>
        {confirm ? (
          <span className="flex shrink-0 items-center gap-1 text-xs">
            <span className="text-muted">{building ? "Stop and delete?" : "Delete?"}</span>
            {video.clip_id ? (
              <>
                <Button size="sm" variant="danger" onClick={() => void remove(true)}>Delete and its clip</Button>
                <Button size="sm" variant="secondary" onClick={() => void remove(false)}>Keep the clip</Button>
              </>
            ) : (
              <Button size="sm" variant="danger" onClick={() => void remove()}>Delete</Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>Keep</Button>
          </span>
        ) : (
          <>
          {!building && (
            <Tip label={video.archived ? "Back to Create: out of the Archive, into the list above" : "Move to the Archive (posted videos go there by themselves)"}>
              <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label={video.archived ? "Back to Create" : "Move to the Archive"}
                      onClick={() => void archive(!video.archived)}>
                {video.archived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
              </Button>
            </Tip>
          )}
          <Tip label={building ? "Stop the build and delete this video" : "Delete this video"}>
            <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label="Delete this video" onClick={() => setConfirm(true)}>
              <Trash2 className="size-4" />
            </Button>
          </Tip>
          </>
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
  const fact = useFactWord();
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState("");                     // the owner's note for Another take (D153)
  const again = async () => { await createApi.rewrite(video.id, note); setNote(""); };   // a note is for one take
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
        <GeminiStruggling />
        <div className="flex flex-wrap items-center gap-3">
          <p className="flex-1 text-xs text-muted">Timing your voice, finding footage, drawing the diagrams. You can leave this page.</p>
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
      {video.status === "draft" && video.topic_id !== null && (
        <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Tell it what to change for the next take"
               onKeyDown={(e) => { if (e.key === "Enter" && busy === null) void run("rewrite", () => again(), "A new take")(); }}
               placeholder="Steer Another take: shorter, funnier opening, use a kitchen example, less jargon…"
               className="h-9 w-full rounded-sm border border-line bg-surface-2 px-3 text-sm outline-none placeholder:text-subtle focus:border-accent" />
      )}
      {video.status === "draft" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={busy !== null} onClick={run("approve", () => createApi.approve(video.id))}>
            <CheckCircle2 className="size-4" /> Approve script
          </Button>
          {video.topic_id !== null && (
            <Tip label="Write a different script for the same idea, following your note if you typed one. Replaces what's here.">
              <Button variant="secondary" disabled={busy !== null} onClick={run("rewrite", () => again(), "A new take")}>
                {busy === "rewrite" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} {busy === "rewrite" ? "Writing…" : "Another take"}
              </Button>
            </Tip>
          )}
          <Tip label={`The AI plans a picture for each sentence you haven't chosen one for, and checks the ${fact}. Your words stay as written.`}>
            <Button variant="secondary" disabled={busy !== null} onClick={run("plan", () => createApi.plan(video.id), "Pictures planned")}>
              {busy === "plan" ? <Loader2 className="size-4 animate-spin" /> : <Shapes className="size-4" />} {busy === "plan" ? "Planning…" : "Plan pictures"}
            </Button>
          </Tip>
          <Tip label={`The AI reads the script for ${fact === "physics" ? "physics mistakes" : "factual mistakes"} and tells you; it changes nothing.`}>
            <Button variant="secondary" disabled={busy !== null} onClick={run("check", () => createApi.check(video.id), "Checked")}>
              {busy === "check" ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />} {busy === "check" ? "Checking…" : `Check ${fact}`}
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
  const clean = /check: no problems/.test(text.split("\n")[0]);
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
  const drawings = useCreate().data?.channel.drawings !== false;   // a footage-only channel draws nothing (D146)
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
        {(drawings || kind === "sketch") && <option value="sketch">Drawing I describe</option>}
        {drawings && (canHold || kind === "hold") && <option value="hold">Keep the drawing above, building on</option>}
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

/** The endings scripts take in turn (create/script.ENDINGS, D155), in the page's words. */
const ENDING_NAME: Record<string, string> = { loop: "a loop back to the start", send: "a \"send this to\" line", poll: "a one-word question" };

function ScriptEditor({ video, wps }: { video: CreateVideo; wps: number }) {
  const poses = useCreate().data?.channel.poses ?? [];
  const qc = useQueryClient();
  const s = video.script;
  const [beats, setBeats] = useState(s.beats);
  const [title, setTitle] = useState(s.title);
  const [hook, setHook] = useState(s.hook ?? "");
  const [description, setDescription] = useState(s.description ?? "");
  const [tags, setTags] = useState((s.hashtags ?? []).join(" "));
  useEffect(() => { setBeats(s.beats); setTitle(s.title); setHook(s.hook ?? ""); setDescription(s.description ?? ""); setTags((s.hashtags ?? []).join(" ")); }, [s]);
  const hookWords = hook.split(/\s+/).filter(Boolean).length;
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
      {/* The words on screen for the first second and a half (D155); empty shows the title. */}
      <div className="flex flex-wrap items-center gap-2">
        <input value={hook} disabled={locked} onChange={(e) => setHook(e.target.value)} placeholder={`On-screen hook (empty: the title)`}
               onBlur={() => hook !== (s.hook ?? "") && void save({ hook: hook.trim() })} aria-label="On-screen hook"
               className="h-8 min-w-0 flex-1 rounded-sm border border-line bg-surface-2 px-2.5 text-sm focus:border-accent focus:outline-none disabled:opacity-80" />
        <span className={cn("text-xs", hookWords > 6 ? "text-warning" : "text-subtle")}>{hookWords} of 6 words on screen</span>
        {s.series && <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">{s.series}</span>}
      </div>
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
                {b.visual.kind === "stock" && !b.visual.hold && (
                  <Tip label="How the camera moves on this sentence's footage. Automatic takes the moves in turn.">
                    <select aria-label={`Camera, sentence ${i + 1}`} value={b.visual.camera ?? ""}
                            onChange={(e) => patch(i, { visual: { ...b.visual, camera: e.target.value } })}
                            className="h-8 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none">
                      {CAMERAS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </Tip>
                )}
                {poses.length > 0 && i < beats.length - 1 && (
                  <Tip label="The presenter's pose while this sentence is said (about 2 seconds, bottom left)">
                    <select aria-label={`Pose, sentence ${i + 1}`} value={b.pose ?? ""} onChange={(e) => patch(i, { pose: e.target.value })}
                            className="h-8 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none">
                      <option value="">no pose</option>
                      {poses.map((p) => <option key={p} value={p}>{p}</option>)}
                    </select>
                  </Tip>
                )}
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
        {s.ending && <span className="text-subtle"> · ends with {ENDING_NAME[s.ending] ?? s.ending}</span>}
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

/* ---------- the voice: a file, recorded here, or none (D146) ---------- */

function VoiceStep({ video }: { video: CreateVideo }) {
  const qc = useQueryClient();
  const { data: create } = useCreate();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [way, setWay] = useState<"file" | "record" | "none">("file");
  const text = video.script.beats.map((b) => b.text.trim()).join(" ");
  const how = create?.channel.voice ?? "";
  // For ElevenLabs v3 (D156): a sentence a line, and a [pause] before the punchline so the deadpan beat lands.
  const beats = video.script.beats.map((b) => b.text.trim());
  const eleven = [...beats.slice(0, -1), ...(beats.length > 1 ? ["[pause]"] : []), ...beats.slice(-1)].join("\n");
  const gotIt = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await work();
      toast.success(done, { description: "Building your Short now: about a minute." });
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const upload = (file: File | undefined) => file && gotIt(() => createApi.voice(video.id, file), "Got the voice");
  const ways: [typeof way, string][] = [["file", "I have the audio"], ["record", "Record it here"], ["none", "No voice"]];
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-accent/30 bg-accent-soft/40 p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Mic className="size-4 text-accent" /> Add the voice</h3>
      <div className="flex flex-wrap gap-1.5" role="tablist">
        {ways.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={way === key} onClick={() => setWay(key)}
                  className={cn("h-8 rounded-full border px-3 text-sm font-medium", way === key ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
            {label}
          </button>
        ))}
      </div>
      {way === "file" && <>
        <Step n={1}>
          <span className="flex-1">Copy the script.</span>
          <Button size="sm" variant="primary" onClick={() => void copyText(text, "Script")}><ClipboardCopy className="size-3.5" /> Copy script</Button>
          {/elevenlabs/i.test(how) && (
            <Tip label="A sentence a line and a [pause] before the punchline, for Eleven v3. Try stability 0.55 to 0.65, similarity 0.75, style 0 to 0.1, and make the punchline 2 or 3 times to keep the flattest.">
              <Button size="sm" variant="secondary" onClick={() => void copyText(eleven, "Script for ElevenLabs")}><ClipboardCopy className="size-3.5" /> For ElevenLabs</Button>
            </Tip>
          )}
        </Step>
        <Step n={2}>
          <span className="flex-1">Make the voice your way{how ? <>: <b>{how}</b></> : ""}. Any audio file works (MP3, WAV, M4A...).</span>
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
            {busy ? "Uploading…" : "Drop the audio here, or click to choose it"}
            <input ref={input} type="file" accept="audio/*" className="hidden" onChange={(e) => void upload(e.target.files?.[0])} />
          </div>
        </Step>
      </>}
      {way === "record" && <Recorder text={text} busy={busy} onUse={(file) => void upload(file)} />}
      {way === "none" && (
        <div className="flex flex-col gap-2 text-sm">
          <p className="text-muted">The video is the pictures with the words as captions, timed at about {create?.channel.words_per_second ?? 2.3} words a second, over a silent track. Add music or a voice-over in the app you post from.</p>
          <Button variant="primary" className="self-start" disabled={busy}
                  onClick={() => void gotIt(() => createApi.noVoice(video.id), "Building without a voice")}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <PenLine className="size-4" />} Build it with no voice
          </Button>
        </div>
      )}
    </div>
  );
}

/** Record the voice in the page (D146): the script to read, a record button, and a play-back before it's used. */
function Recorder({ text, busy, onUse }: { text: string; busy: boolean; onUse: (file: File) => void }) {
  const [recording, setRecording] = useState(false);
  const [take, setTake] = useState<Blob | null>(null);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const start = async () => {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(stream);
      chunks.current = [];
      r.ondataavailable = (e) => chunks.current.push(e.data);
      r.onstop = () => { stream.getTracks().forEach((t) => t.stop()); setTake(new Blob(chunks.current, { type: r.mimeType || "audio/webm" })); };
      r.start();
      recorder.current = r;
      setTake(null);
      setRecording(true);
    } catch {
      setError("Clipper can't use the microphone. Allow it for this page in your browser, or upload an audio file instead.");
    }
  };
  const stop = () => { recorder.current?.stop(); setRecording(false); };
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="max-h-40 overflow-auto rounded-md border border-line bg-surface-1 p-3 whitespace-pre-wrap">{text}</p>
      <div className="flex flex-wrap items-center gap-2">
        {!recording
          ? <Button variant="primary" onClick={() => void start()}><Mic className="size-4" /> {take ? "Record again" : "Start recording"}</Button>
          : <Button variant="danger" onClick={stop}><span className="size-2.5 animate-pulse rounded-full bg-danger" /> Stop</Button>}
        {take && !recording && <audio controls src={URL.createObjectURL(take)} className="h-9" />}
        {take && !recording && (
          <Button variant="primary" disabled={busy} onClick={() => onUse(new File([take], "recording.webm", { type: take.type }))}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : null} Use this recording
          </Button>
        )}
      </div>
      {error && <p className="text-warning">{error}</p>}
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

/** Music and chalk sounds for this one video (D156), so two videos can differ for an experiment; used on Build again. */
function SoundChoice({ video }: { video: CreateVideo }) {
  const qc = useQueryClient();
  const { data: create } = useCreate();
  const pick = (key: "music" | "sfx", label: string) => {
    const mine = video.script[key];
    const usual = create?.channel[key] ? "on" : "off";
    return (
      <label className="flex items-center gap-1.5">
        {label}
        <select value={mine == null ? "" : mine ? "on" : "off"} aria-label={label} disabled={video.status === "building"}
                className="h-7 rounded-sm border border-line bg-surface-2 px-1.5 text-xs"
                onChange={(e) => void createApi.sound(video.id, { [key]: e.target.value === "" ? null : e.target.value === "on" })
                  .then(() => qc.invalidateQueries({ queryKey: ["create"] }), (err: Error) => toast.error(err.message))}>
          <option value="">Channel ({usual})</option><option value="on">On</option><option value="off">Off</option>
        </select>
      </label>
    );
  };
  const style = (key: "zooms" | "cut_ins", label: string) => (
    <label className="flex items-center gap-1.5">
      <input type="checkbox" className="accent-[var(--color-accent)]" checked={video.script[key] ?? true}
             disabled={video.status === "building"}
             onChange={(e) => void createApi.editStyle(video.id, { [key]: e.target.checked })
               .then(() => qc.invalidateQueries({ queryKey: ["create"] }), (err: Error) => toast.error(err.message))} />
      {label}
    </label>
  );
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
      {pick("music", "Music")}{pick("sfx", "Chalk sounds")}
      {style("zooms", "Zoom-ins")}{style("cut_ins", "Cut-ins on the Professor")}
      <label className="flex items-center gap-1.5">
        Transitions
        <select value={video.script.transitions ?? "auto"} aria-label="Transitions" disabled={video.status === "building"}
                className="h-7 rounded-sm border border-line bg-surface-2 px-1.5 text-xs"
                onChange={(e) => void createApi.editStyle(video.id, { transitions: e.target.value })
                  .then(() => qc.invalidateQueries({ queryKey: ["create"] }), (err: Error) => toast.error(err.message))}>
          <option value="auto">Automatic</option><option value="cut">Plain cuts</option>
          <option value="whip">Whip</option><option value="zoom">Zoom</option>
        </select>
      </label>
      <span className="text-subtle">Changes show on Build again.</span>
    </div>
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
          <PostedLine video={video} />
          <p className="text-sm">It's in your library under <b>{video.script.title}</b>, with the title, description and hashtags ready to copy.</p>
          {/* Who wrote and drew it, and what didn't work, stay visible after the build (D118). */}
          <CheckNote text={video.check_notes} />
          <div className="flex flex-wrap gap-2">
            {clip && <Button variant="primary" onClick={() => useUI.getState().setOpenClip(clip.id)}>Open to post</Button>}
            {/* Where this video goes next, and where its numbers are (D143). */}
            {clip && (clip.posts.length > 0 || video.posted) && (
              <Link to="/stats" search={{ campaign: clip.campaign }}
                    className="inline-flex h-9 items-center rounded-sm border border-line bg-surface-2 px-3.5 text-sm font-medium hover:bg-surface-3">
                See its stats
              </Link>
            )}
            {clip && (
              <Link to="/clips" search={{ campaign: clip.campaign, status: "all" }}
                    className="inline-flex h-9 items-center rounded-sm px-3 text-sm font-medium text-muted hover:bg-surface-2 hover:text-fg">
                All your Shorts in Clips
              </Link>
            )}
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
          <SoundChoice video={video} />
          <CoverChoice video={video} player={player} />
        </div>
      </div>
      <Fold id={`review-${video.id}`} title="Change parts you don't like" startOpen>
        <ShotReview video={video} seek={seek} onRebuild={onRebuild} rebuilding={busy === "rebuild"} />
      </Fold>
      <Fold id={`captions-${video.id}`} title={`Captions${video.script.captions === false ? " (off)" : ""}`}>
        <CaptionsEditor video={video} />
      </Fold>
      <Fold id={`clips-${video.id}`} title={`Your own clips${video.mine?.clips.length ? ` (${video.mine.clips.length})` : ""}`}
              startOpen={(video.mine?.clips.length ?? 0) > 0}>
        <MyClips video={video} wps={wps} onRebuild={onRebuild} busyRebuild={busy === "rebuild"} />
      </Fold>
    </div>
  );
}

/** The cover (D171): the best still, picked by the build, or the frame the player is on. Used on Build again. */
function CoverChoice({ video, player }: { video: CreateVideo; player: RefObject<HTMLVideoElement | null> }) {
  const qc = useQueryClient();
  const at = video.script.cover;
  const set = (cover: number | null) => void createApi.editStyle(video.id, { cover })
    .then(() => { void qc.invalidateQueries({ queryKey: ["create"] });
                  toast.success(cover === null ? "Cover: the best still" : `Cover: the frame at ${cover.toFixed(1)}s`,
                                { description: "Build again to use it." }); },
          (err: Error) => toast.error(err.message));
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      <span>Cover: {at == null ? "the best still, picked automatically" : `the frame at ${at.toFixed(1)}s`}</span>
      <Tip label="Pause the video on the frame you want, then press this">
        <Button size="sm" variant="secondary" disabled={video.status === "building"}
                onClick={() => set(Math.round((player.current?.currentTime ?? 0) * 10) / 10)}>Use this frame</Button>
      </Tip>
      {at != null && <Button size="sm" variant="ghost" onClick={() => set(null)}>Automatic</Button>}
    </div>
  );
}

/** How the captions look, per video (D180). */
const CAPTION_SIZES: [number, string][] = [[0.8, "size: smaller"], [1, "size: as made"], [1.15, "size: bigger"], [1.3, "size: biggest"]];
const CAPTION_PLACES: [number, string][] = [[-0.12, "place: over the picture"], [0, "place: as made"], [0.08, "place: lower"], [0.16, "place: lowest"]];
const CAPTION_COLOURS: [string, string][] = [["", "said word: channel's colour"], ["#ffd23f", "said word: yellow"], ["#ffffff", "said word: white"],
  ["#5be37d", "said word: green"], ["#4fd2ff", "said word: blue"], ["#ff6fae", "said word: pink"], ["#ff9a3c", "said word: orange"]];

/** Captions for this video (D171): on or off, and any sentence's caption written by hand (its words shown in
 *  place of the spoken ones, spread over the time it's said). Saved as you leave the box; used on Build again. */
function CaptionsEditor({ video }: { video: CreateVideo }) {
  const qc = useQueryClient();
  const beats = video.script.beats;
  const [draft, setDraft] = useState<Record<number, string>>({});
  const save = (k: number) => {
    const text = (draft[k] ?? beats[k].caption ?? "").trim();
    if (text === (beats[k].caption ?? "")) return;
    void createApi.part(video.id, { beat: k + 1, caption: text })
      .then(() => { void qc.invalidateQueries({ queryKey: ["create"] });
                    toast.success(text ? `Caption ${k + 1} written` : `Caption ${k + 1} back to the spoken words`, { description: "Build again to see it." }); },
            (err: Error) => toast.error(err.message));
  };
  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="accent-[var(--color-accent)]" checked={video.script.captions ?? true}
               disabled={video.status === "building"}
               onChange={(e) => void createApi.editStyle(video.id, { captions: e.target.checked })
                 .then(() => qc.invalidateQueries({ queryKey: ["create"] }), (err: Error) => toast.error(err.message))} />
        Show captions <span className="text-xs text-muted">(changes show on Build again)</span>
      </label>
      {(video.script.captions ?? true) && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            {([["caption_size", "Caption size", video.script.caption_size ?? 1, CAPTION_SIZES],
               ["caption_shift", "Caption place", video.script.caption_shift ?? 0, CAPTION_PLACES],
               ["caption_colour", "Said word colour", video.script.caption_colour ?? "", CAPTION_COLOURS]] as const).map(([key, label, now, options]) => (
              <select key={key} aria-label={label} disabled={video.status === "building"} value={String(now)}
                      className="h-7 rounded-sm border border-line bg-surface-2 px-1.5 text-xs"
                      onChange={(e) => void createApi.editStyle(video.id, { [key]: key === "caption_colour" ? e.target.value : Number(e.target.value) })
                        .then(() => qc.invalidateQueries({ queryKey: ["create"] }), (err: Error) => toast.error(err.message))}>
                {!options.some(([k]) => String(k) === String(now)) && <option value={String(now)}>{label}: set by hand</option>}
                {options.map(([k, name]) => <option key={k} value={String(k)}>{name}</option>)}
              </select>
            ))}
          </div>
          <p className="text-xs text-muted">Each caption shows the spoken words. Write over one to show other words for that sentence; leave it empty for the spoken ones.</p>
          <ol className="flex flex-col gap-1.5">
            {beats.map((b, k) => (
              <li key={k} className="flex items-center gap-2">
                <span className="tabular w-5 shrink-0 text-right text-xs text-subtle">{k + 1}</span>
                <input value={draft[k] ?? b.caption ?? ""} placeholder={b.text} aria-label={`Caption for sentence ${k + 1}`}
                       onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))} onBlur={() => save(k)}
                       onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                       className="h-8 min-w-0 flex-1 rounded-sm border border-line bg-surface-2 px-2 text-sm placeholder:text-subtle focus:border-accent focus:outline-none" />
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
