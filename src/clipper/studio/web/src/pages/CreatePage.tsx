import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle, Archive, ArchiveRestore, ArrowDown, ArrowUp, CheckCircle2, ChevronRight, ClipboardCopy, Film, Lightbulb, Loader2, Mic, PenLine, Plus, RefreshCw, Shapes,
  ExternalLink, MoreHorizontal, Redo2, Trash2, Undo2, Upload, Wand2, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { toast } from "sonner";
import {
  createApi, useClips, useCreate, useCreateAI, type CreatePost, type CreateScript, type CreateTopic, type CreateVideo, type CreateView, type CreateVisual,
} from "@/api/client";
import { JobProgress, type Step } from "@/components/progress";
import { Button, CaptionTitle, Card, Chip, EmptyState, Skeleton, Tip, buttonClass } from "@/components/ui";
import { useUI } from "@/lib/store";
import { PLATFORM_NAME, cn, copyText, formatCount } from "@/lib/utils";
import { ChannelBar, NewChannel } from "./ChannelPanel";
import { MyClips } from "./MyClips";
import { CAMERAS, ShotReview } from "./ShotReview";

/** Create (D108): original Shorts for your own channel -- idea, script, your voice, built. */
/** "physics" on a physics channel, "facts" on any other: what the fact check is called in the buttons (D146). */
function useFactWord() {
  const { data } = useCreate();
  return data?.channel.check_name === "Physics check" ? "physics" : "facts";
}

/** A toast when a script is written or a Short is built (D196): both run for minutes in the background, and the page
 *  said nothing if you were looking elsewhere on it. */
function useDoneToasts(videos: CreateVideo[] | undefined) {
  const seen = useRef<Map<number, string> | null>(null);
  useEffect(() => {
    if (!videos) return;
    const before = seen.current;
    seen.current = new Map(videos.map((v) => [v.id, v.status]));
    if (!before) return;   // the first load: nothing has finished yet
    for (const v of videos) {
      const was = before.get(v.id);
      const title = v.script.title || "Your Short";
      if (was === "writing" && v.status === "draft") toast.success("Script written", { description: title });
      if ((was === "building" || was === "voiced") && v.status === "built") toast.success("Short built", { description: title });
      if (was === "building" && v.status === "failed") toast.error("The build stopped", { description: v.error || title });
    }
  }, [videos]);
}

export function CreatePage() {
  const { data, isLoading } = useCreate();
  useDoneToasts(data?.videos);
  if (isLoading || !data) return <CreateSkeleton />;
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
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 xl:grid-cols-[minmax(300px,360px)_minmax(0,1fr)]">
        {/* The video in progress comes first below a wide window; the ideas wait below it. Two columns only from xl
            and the ideas held in view there (D206): at lg the script got a 460px column, and beside a long open
            video the ideas column ended in a screen of nothing. */}
        <div className="order-2 xl:sticky xl:top-4 xl:order-1 xl:max-h-[calc(100dvh-8rem)] xl:overflow-y-auto xl:pr-1"><Ideas topics={data.topics} /></div>
        <div className="order-1 flex flex-col gap-4 xl:order-2">
          <NewShort wps={data.channel.words_per_second} />
          {work.length === 0 && (
            archived.length ? <p className="text-sm text-muted">Nothing in progress: everything you've made is in the Archive below.</p>
              : <EmptyState icon={<Wand2 />} title="No videos yet" body="Pick an idea on the left and press Write it (a minute or two), or write your own script." />
          )}
          {work.map((v) => <VideoCard key={v.id} video={v} open={v.id === active} wps={data.channel.words_per_second} />)}
          {archived.length > 0 && <ArchiveList videos={archived} wps={data.channel.words_per_second} />}
        </div>
      </div>
    </div>
  );
}

/** The page's own shape while it loads (D189): the heading, the ideas on the left, the videos on the right, so
 *  nothing jumps when they arrive. */
function CreateSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2"><Skeleton className="h-10 w-72" /><Skeleton className="h-4 w-full max-w-xl" /><Skeleton className="h-4 w-56" /></div>
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 xl:grid-cols-[minmax(300px,360px)_minmax(0,1fr)]">
        <div className="order-2 flex flex-col gap-2 xl:order-1"><Skeleton className="h-12" />{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16" />)}</div>
        <div className="order-1 flex flex-col gap-4 xl:order-2"><Skeleton className="h-10 w-48" /><Skeleton className="h-28" /><Skeleton className="h-72" /></div>
      </div>
    </div>
  );
}

/** The archive (D131): posted videos, out of the way but whole. Each opens as it always did, to
 *  watch, copy, change or rebuild, and "Back to Create" returns it to the list above. */
function ArchiveList({ videos, wps }: { videos: CreateVideo[]; wps: number }) {
  const [open, setOpen] = useState(() => folds.get("archive") ?? false);
  const toggle = () => setOpen((o) => { folds.set("archive", !o); return !o; });
  // What worked (D200): each video's views on every platform together, and the most viewed first on request.
  const [byViews, setByViews] = useState(false);
  const views = (v: CreateVideo) => (v.posts ?? []).reduce((n, p) => n + (p.views ?? 0), 0);
  const total = videos.reduce((n, v) => n + views(v), 0);
  const shown = byViews ? [...videos].sort((a, b) => views(b) - views(a)) : videos;
  return (
    <section className="flex flex-col gap-3">
      <button type="button" onClick={toggle} aria-expanded={open}
              className="flex items-center gap-2 self-start text-sm font-semibold text-muted hover:text-fg">
        <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        <Archive className="size-4" /> Archive <span className="font-normal text-subtle">{videos.length}{total > 0 && ` · ${formatCount(total)} views`}</span>
      </button>
      {open && (
        <>
          <p className="-mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
            <span>Videos move here once they're posted (found on your channel, or marked posted in Clips). You can also archive any video yourself.</span>
            {total > 0 && (
              <button type="button" aria-pressed={byViews} onClick={() => setByViews((b) => !b)}
                      className={cn("rounded-sm border px-2 py-0.5", byViews ? "border-line-strong bg-surface-3 text-fg" : "border-line hover:text-fg")}>
                Most viewed first
              </button>
            )}
          </p>
          {shown.map((v) => <VideoCard key={v.id} video={v} open={false} wps={wps} />)}
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
/** What writing and building do, in order, at the percent each starts (D189): the page lists them, done and to
 *  come, so a long wait shows the work. The percents are the ones `write_checked` and the build report. */
const writeSteps = (fact: string): Step[] => [
  { label: "Writing the script", at: 0 }, { label: `The editor's read and the ${fact} check`, at: 30 },
  { label: "Planning the pictures", at: 70 },
];
const buildSteps = (drawings: boolean): Step[] => [
  { label: "Listening to the voice", at: 0 }, ...(drawings ? [{ label: "Drawing the sketches", at: 4 }] : []),
  { label: "Choosing footage", at: 8 }, { label: "Making the shots", at: 10 },
  { label: "Putting it together", at: 75 }, { label: "Choosing the cover", at: 90 },
];

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
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => useClaude()}>
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
const PROVIDER: Record<string, string> = { gemini: "Gemini", claude_code: "Claude", anthropic: "Claude",
  ollama: "Ollama" };

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

/** One place to start a Short (D200): your own idea for the AI to write, your own words, or a ready-made script.
 *  The ideas the planner offers are the list beside it. Before this there were three cards with near-identical names. */
function NewShort({ wps }: { wps: number }) {
  const [way, setWay] = useState<"idea" | "words" | "ready">("idea");
  const { data: ready = [] } = useQuery({ queryKey: ["create", "ready"], queryFn: createApi.ready, staleTime: 60_000 });
  const ways: ["idea" | "words" | "ready", string, string][] = [
    ["idea", "My idea, AI writes it", "Type the question"], ["words", "My own words", "Paste your script"],
    ...(ready.length > 0 ? [["ready", "Ready-made", "Written and drawn already"] as ["ready", string, string]] : []),
  ];
  return (
    <Card className="flex flex-col gap-3 p-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><Plus className="size-4 text-muted" /> New Short</h2>
      <div className={cn("grid gap-2", ways.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")} role="radiogroup" aria-label="How to start a Short">
        {ways.map(([key, title, hint]) => (
          <button key={key} type="button" role="radio" aria-checked={way === key} onClick={() => setWay(key)}
                  className={cn("flex flex-col items-start gap-0.5 rounded-md border px-3 py-2 text-left transition-colors",
                    way === key ? "border-fg/50 bg-surface-2" : "border-line hover:bg-surface-2")}>
            <span className="text-sm font-medium">{title}</span>
            <span className="text-xs text-muted">{hint}</span>
          </button>
        ))}
      </div>
      {way === "idea" && <MyIdea />}
      {way === "words" && <OwnWords wps={wps} />}
      {way === "ready" && <ReadyMade ready={ready} />}
      <p className="text-xs text-subtle">Or press Write it on one of the ideas.</p>
    </Card>
  );
}

/** Your own idea: onto the board, and written at once (D191). */
function MyIdea() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [mine, setMine] = useState("");
  const [mineAngle, setMineAngle] = useState("");
  const writeMine = async () => {
    setBusy(true);
    try {
      const { id } = await createApi.ownIdea(mine, mineAngle);
      await createApi.script(id);
      setMine(""); setMineAngle("");
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error("Couldn't write it", { description: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const field = "h-9 w-full rounded-sm border border-line bg-surface-2 px-2.5 text-sm outline-none placeholder:text-subtle focus:border-accent";
  return (
    <div className="flex flex-col gap-2">
      <input value={mine} onChange={(e) => setMine(e.target.value)} aria-label="Your own idea"
             onKeyDown={(e) => { if (e.key === "Enter" && !busy && mine.trim().length >= 8) void writeMine(); }}
             placeholder="e.g. Why does a spoon flip your reflection?" className={field} />
      <input value={mineAngle} onChange={(e) => setMineAngle(e.target.value)} aria-label="Angle (optional)"
             placeholder="Angle or what to cover (optional)" className={field} />
      {/* Lime only once it can go (D207): an empty form's dimmed lime button competed with the draft's Approve. */}
      <Button variant={mine.trim().length < 8 ? "secondary" : "primary"} className="self-start" disabled={busy || mine.trim().length < 8} onClick={() => writeMine()}>
        {busy ? <Loader2 className="size-4 animate-spin" /> : <PenLine className="size-4" />} {busy ? "Writing…" : "AI writes it"}
      </Button>
    </div>
  );
}

/** Write the script yourself (D120): paste or type it, and Clipper cuts it into sentences, keeping every
 *  word. Pictures and the physics check are optional help, not a requirement. */
function OwnWords({ wps }: { wps: number }) {
  const qc = useQueryClient();
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
      setTitle(""); setText(""); setDescription(""); setHashtags("");
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);   // what was typed stays in the box
    } finally {
      setBusy(false);
    }
  };
  const field = "rounded-md border border-line bg-surface-2 px-3 py-2 text-sm focus:border-accent focus:outline-none";
  return (
    <div className="flex flex-col gap-3">
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
      <Button variant={text.trim() ? "primary" : "secondary"} className="self-start" disabled={busy || !text.trim()} onClick={() => create()}>
        {busy ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />} {busy ? (plan ? "Planning…" : "Creating…") : "Use my own words"}
      </Button>
    </div>
  );
}

/** The scripts that ship written and drawn already, and what's been made from each. */
function ReadyMade({ ready }: { ready: { name: string; title: string; about: string; made: { archived?: boolean }[] }[] }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
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
  return (
    <div className="flex flex-col gap-2">
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
            <Button size="sm" variant={r.made.length ? "secondary" : "primary"} disabled={busy} onClick={() => start(r.name)}>
              {r.made.length ? "Make again" : "Use it"}
            </Button>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- ideas ---------- */

function Ideas({ topics }: { topics: CreateTopic[] }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<number | "more" | null>(null);
  const [steer, setSteer] = useState("");
  const [steering, setSteering] = useState(false);   // the steer box, behind a chevron by More ideas (D199)
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
  /** Gone from the list as it's clicked (D192); back if the server says no, or on Undo (D196). */
  const skip = (t: CreateTopic) => {
    qc.setQueryData<CreateView>(["create"], (d) => d && { ...d, topics: d.topics.filter((x) => x.id !== t.id) });
    toast("Idea skipped", { description: t.question, duration: 5000,
                            action: { label: "Undo", onClick: () => void createApi.skip(t.id, true).finally(refresh) } });
    return createApi.skip(t.id).finally(refresh);
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
        <h2 className="flex items-center gap-2 text-sm font-semibold"><Lightbulb className="size-4 text-muted" /> Ideas <span className="font-normal text-subtle">{topics.length}</span></h2>
        <span className="flex items-center gap-0.5">
          <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => more()}>
            {busy === "more" ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />} More ideas
          </Button>
          <Tip label="Steer the next batch">
            <Button size="icon" variant="ghost" className="size-7" aria-label="Steer the next batch" aria-expanded={steering || !!steer}
                    onClick={() => setSteering((v) => !v)}>
              <ChevronRight className={cn("size-3.5 transition-transform", (steering || steer) && "rotate-90")} />
            </Button>
          </Tip>
        </span>
      </div>
      {(steering || steer) && <div className="border-b border-line px-4 py-2">
        <input value={steer} onChange={(e) => setSteer(e.target.value)} aria-label="Steer the next ideas" autoFocus
               onKeyDown={(e) => { if (e.key === "Enter" && busy === null) void more(); }}
               placeholder="Steer the next batch, e.g. only light and sound, no biology"
               className="h-8 w-full rounded-sm border border-line bg-surface-2 px-2.5 text-xs outline-none placeholder:text-subtle focus:border-accent" />
        <p className="mt-1 text-[11px] text-subtle">Lasting guidance is under Channel settings. Skipping an idea teaches it what to avoid.</p>
      </div>}
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
              {/* Lime only on the row you're on (D199): 27 lime buttons drowned the one that matters. */}
              <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => write(t)} aria-label={`Write it: ${t.question}`}
                      className="group-hover:border-transparent group-hover:bg-accent group-hover:text-accent-fg group-focus-within:border-transparent group-focus-within:bg-accent group-focus-within:text-accent-fg">
                {busy === t.id ? <Loader2 className="size-3.5 animate-spin" /> : <PenLine className="size-3.5" />} {busy === t.id ? "Writing…" : "Write it"}
              </Button>
              <Tip label="Not this one">
                <Button size="icon" variant="ghost" className="size-7 opacity-60 group-hover:opacity-100 focus-visible:opacity-100" aria-label={`Skip: ${t.question}`}
                        onClick={() => skip(t)}>
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

const STEPS = [["Script", ["writing", "draft"]], ["Voice", ["approved"]], ["Build", ["voiced", "building", "failed"]], ["Ready", ["built"]]] as const;

function Steps({ status }: { status: CreateVideo["status"] }) {
  const at = STEPS.findIndex(([, s]) => (s as readonly string[]).includes(status));
  return (
    <div className="flex items-center gap-1.5 text-xs">
      {STEPS.map(([label], i) => (
        <span key={label} className="flex items-center gap-1.5">
          <span aria-current={i === at && status !== "built" ? "step" : undefined}
                className={cn("rounded-full px-2 py-0.5 font-semibold",
            i < at || status === "built" ? "bg-surface-2 text-muted" : i === at ? "bg-accent text-accent-fg" : "text-subtle",
            status === "failed" && i === at && "bg-[color-mix(in_oklch,var(--danger)_18%,transparent)] text-danger")}>
            {label}{(i < at || status === "built") && <span className="sr-only"> (done)</span>}
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
  const [confirm, setConfirm] = useState<"delete" | "ideas" | null>(null);
  // Opens when it becomes the video to work on; never closes on its own (it shut while in use, D127).
  useEffect(() => { if (startOpen) setOpen(true); }, [startOpen]);
  const s = video.script;
  const building = video.status === "voiced" || video.status === "building";
  const busyNow = building || video.status === "writing";
  /** Delete, from any state, so no video can get stuck on the page (D123). */
  const archive = async (on: boolean) => {
    // Moved as it's clicked (D192); the refresh after puts it back if the server said no.
    qc.setQueryData<CreateView>(["create"], (d) => d && { ...d, videos: d.videos.map((x) => x.id === video.id ? { ...x, archived: on } : x) });
    try {
      await createApi.archive(video.id, on);
      toast.success(on ? "Moved to the Archive" : "Back in Create", { description: on ? "Open the Archive at the bottom of the list to find it." : undefined });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      await qc.invalidateQueries({ queryKey: ["create"] });
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
      setConfirm(null);
    }
  };
  const toIdeas = async () => {
    try {
      await createApi.toIdeas(video.id);
      toast.success("Back on the idea board", { description: video.clip_id ? "Its files are in the Recycle Bin; the finished video stays in Clips." : "Its files are in the Recycle Bin." });
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setConfirm(null);
    }
  };
  return (
    <Card className={cn("flex flex-col", open && "ring-1 ring-line-strong")}>
      <div className="flex items-center gap-2 p-4">
        <button className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-3 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <span className="min-w-0">
            <span className="line-clamp-2 font-semibold">{s.title || "Untitled"}</span>
            <span className="text-xs text-muted">
              {video.status === "writing" ? "Being written…" : `${s.beats?.length ?? 0} sentences · ~${Math.round(words(s) / wps)}s${s.length === "short" ? " · short take" : ""}`}
              {video.archived && video.archived_at ? ` · archived ${video.archived_at.slice(0, 10)}` : ""}
            </span>
          </span>
          {/* Built: its picture and views (D199); four lit steps on every finished card said nothing. */}
          {video.status === "built" && video.clip_id ? (
            <span className="flex shrink-0 items-center gap-2.5 text-xs text-muted">
              <span className="tabular">
                {video.posts?.length ? video.posts.map((p) => `${PLATFORM_NAME[p.platform] ?? p.platform} ${p.views == null ? "–" : formatCount(p.views)}`).join(" · ")
                  : video.posted ? "Posted" : "Not posted yet"}
              </span>
              {/* A poster you can read (D206): at 48px tall it was a strip. */}
              <img src={`/thumb/${video.clip_id}`} alt="" loading="lazy" className="aspect-[9/16] h-20 rounded-md bg-black object-cover ring-1 ring-line" />
            </span>
          ) : <Steps status={video.status} />}
        </button>
        {confirm === "ideas" ? (
          <span className="flex shrink-0 items-center gap-1 text-xs">
            <span className="text-muted">Back to ideas? This video goes to the Recycle Bin.</span>
            <Button size="sm" variant="primary" onClick={() => toIdeas()}>Back to ideas</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>Keep</Button>
          </span>
        ) : confirm ? (
          <span className="flex shrink-0 items-center gap-1 text-xs">
            <span className="text-muted">{building ? "Stop and delete?" : "Delete?"}</span>
            {video.clip_id ? (
              <>
                <Button size="sm" variant="danger" onClick={() => remove(true)}>Delete and its clip</Button>
                <Button size="sm" variant="secondary" onClick={() => remove(false)}>Keep the clip</Button>
              </>
            ) : (
              <Button size="sm" variant="danger" onClick={() => remove()}>Delete</Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>Keep</Button>
          </span>
        ) : (
          <>
          {!building && (
            <Tip label={video.archived ? "Back to Create: out of the Archive, into the list above" : "Move to the Archive (posted videos go there by themselves)"}>
              <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label={`${video.archived ? "Back to Create" : "Move to the Archive"}: ${s.title || "Untitled"}`}
                      onClick={() => archive(!video.archived)}>
                {video.archived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
              </Button>
            </Tip>
          )}
          {!busyNow && (
            <Tip label="Back to the idea board: the idea comes back to write again later, this video goes">
              <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label={`Back to ideas: ${s.title || "Untitled"}`} onClick={() => setConfirm("ideas")}>
                <Lightbulb className="size-4" />
              </Button>
            </Tip>
          )}
          <Tip label={building ? "Stop the build and delete this video" : "Delete this video"}>
            <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label={`Delete this video: ${s.title || "Untitled"}`} onClick={() => setConfirm("delete")}>
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
  const drawings = useCreate().data?.channel.drawings !== false;
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
  /** The script before the last take, edit or picture plan (D193), and back again (D196): an undone take isn't lost. */
  const undo = (video.undo || video.redo) && (
    <span className="flex items-center gap-1">
      {video.undo && (
        <Tip label={`Back to the script as it was before ${video.undo === "your edit" ? "your last edit" : `the last ${video.undo}`}. Press again to go further back.`}>
          <Button variant="ghost" disabled={busy !== null} onClick={run("undo", () => createApi.undo(video.id), `Undone: ${video.undo}`)}>
            <Undo2 className="size-4" /> Undo
          </Button>
        </Tip>
      )}
      {video.redo && (
        <Tip label="Forward again, to the script you undid">
          <Button variant="ghost" disabled={busy !== null} onClick={run("forward", () => createApi.forward(video.id), "Redone")}>
            <Redo2 className="size-4" /> Redo
          </Button>
        </Tip>
      )}
    </span>
  );

  if (video.status === "writing") {   // written in the background, step by step (D188)
    return (
      <JobProgress pct={video.pct ?? 0} stage={video.stage} started={video.started} label="Writing the script"
                   steps={writeSteps(fact)}>
        <GeminiStruggling />
        <p className="text-xs text-muted">Usually a minute or two. You can leave this page.</p>
      </JobProgress>
    );
  }
  if (video.status === "voiced" || video.status === "building") {
    return (
      <JobProgress pct={video.status === "voiced" && video.pct === null ? null : video.pct ?? 0} stage={video.stage ?? (video.status === "voiced" ? "Waiting for the build before it" : null)}
                   started={video.started} label="Building the Short" steps={buildSteps(drawings)}>
        <GeminiStruggling />
        <div className="flex flex-wrap items-center gap-3">
          <p className="flex-1 text-xs text-muted">You can leave this page: it carries on.</p>
          <Tip label="Stops at the next step. A video built before stays as it was.">
            <Button size="sm" variant="ghost" disabled={busy !== null || video.cancelling}
                    onClick={run("cancel", () => createApi.cancel(video.id), "Stopping the build")}>
              {video.cancelling || busy === "cancel" ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
              {video.cancelling ? "Stopping…" : "Cancel"}
            </Button>
          </Tip>
        </div>
      </JobProgress>
    );
  }
  if (video.status === "built" || (video.status === "failed" && video.clip_id)) {
    return <Built video={video} wps={wps} busy={busy} onPictures={run("pictures", () => createApi.pictures(video.id), "New pictures planned: building")}
                  onRebuild={run("rebuild", () => createApi.build(video.id), "Rebuilding: only the parts that changed are made again")} />;
  }

  const checked = isChecked(video.check_notes);
  const checkButton = (
    <Tip label={`The AI reads the script for ${fact === "physics" ? "physics mistakes" : "factual mistakes"} and tells you; it changes nothing.`}>
      <Button variant={checked ? "secondary" : "primary"} disabled={busy !== null} onClick={run("check", () => createApi.check(video.id), "Checked")}>
        {busy === "check" ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />} {busy === "check" ? "Checking…" : checked ? `Check ${fact} again` : `Check ${fact}`}
      </Button>
    </Tip>
  );
  return (
    <div className="flex flex-col gap-4">
      {video.status === "draft" && video.error && (
        <div className="flex items-center gap-3 rounded-md border border-danger/40 p-3 text-sm text-danger">
          <AlertTriangle className="size-4 shrink-0" /> <span className="flex-1">{video.error}</span>
        </div>
      )}
      {video.status === "failed" && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-danger/40 p-3 text-sm text-danger">
          <AlertTriangle className="size-4 shrink-0" /> <span className="flex-1">{video.error || "The build failed."}</span>
          {video.voice && <Button size="sm" variant="secondary" onClick={run("build", () => createApi.build(video.id))}>Try again</Button>}
        </div>
      )}
      <CheckNote text={video.check_notes} />
      {video.status === "draft" && (video.takes ?? 0) > 0 && <Takes video={video} />}
      <ScriptEditor video={video} wps={wps} />
      {/* Folded (D199): the whole drop card sat between the script and Approve. */}
      <Fold id={`clips-${video.id}`} title={`Your own clips (optional)${video.mine?.clips.length ? `: ${video.mine.clips.length}` : ""}`}
            startOpen={(video.mine?.clips.length ?? 0) > 0 || video.status === "failed"}>
        <MyClips video={video} wps={wps} onRebuild={video.status === "failed" && video.voice ? run("rebuild", () => createApi.build(video.id)) : undefined}
                 busyRebuild={busy === "rebuild"} />
      </Fold>
      {video.status === "draft" && (
        // Held in view at the card's foot (D199): Approve sat below a long script; above a phone's tabs.
        <div className="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 -mx-4 -mb-4 flex flex-col gap-2 rounded-b-lg border-t border-line bg-surface-1/95 px-4 py-3 backdrop-blur md:bottom-0">
      {video.topic_id !== null && (
        <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Tell it what to change for the next take"
               onKeyDown={(e) => { if (e.key === "Enter" && busy === null) void run("rewrite", () => again(), "A new take")(); }}
               placeholder="Steer Another take: shorter, funnier opening, use a kitchen example, less jargon…"
               className="h-9 w-full rounded-sm border border-line bg-surface-2 px-3 text-sm outline-none placeholder:text-subtle focus:border-accent" />
      )}
        {/* Which step this is and what Approve does (D208): three equal buttons gave no order, and nothing said
            Approve locks the words. The check goes first until the script has one; it isn't run by itself, it uses AI. */}
        <p className="text-xs text-muted">
          <b className="font-medium text-fg">Step 1 of 4, the script.</b>{" "}
          {checked ? "Approve locks the words so you can record them in step 2; Edit the script again unlocks them."
            : `Check the ${fact} first, then approve: that locks the words so you can record them in step 2.`}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {!checked && checkButton}
          <Button variant={checked ? "primary" : "secondary"} disabled={busy !== null} onClick={run("approve", () => createApi.approve(video.id))}>
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
          {checked && checkButton}
          {/* Deleting is the card's own trash button, which asks first (D196): this one didn't. */}
          <span className="ml-auto flex items-center gap-2">{undo}</span>
        </div>
        </div>
      )}
      {video.status !== "draft" && (
        <>
          <VoiceStep video={video} />
          {video.status === "approved" && (
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="ghost" disabled={busy !== null} onClick={run("unapprove", () => createApi.edit(video.id, {}))}>
                <PenLine className="size-4" /> Edit the script again
              </Button>
              {undo}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** The earlier takes beside the one in place, the lines that differ marked, and any one back (D199): Undo only went
 *  back one step at a time without showing where to. */
function Takes({ video }: { video: CreateVideo }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [pick, setPick] = useState<number | null>(null);
  const { data: takes = [] } = useQuery({ queryKey: ["takes", video.id, video.takes], enabled: open,
                                          queryFn: () => createApi.takes(video.id) });
  const now = video.script.beats.map((b) => b.text);
  const shown = takes.find((t) => t.index === pick) ?? takes[takes.length - 1];
  const use = async (k: number) => {
    try {
      await createApi.useTake(video.id, k);
      toast.success("That take is back", { description: "The one it replaced is in Earlier takes, and Undo brings it back." });
      setPick(null);
      await qc.invalidateQueries({ queryKey: ["create"] });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const lines = (list: string[], other: string[]) => (
    <ol className="flex flex-col gap-1 text-sm">
      {list.map((l, i) => <li key={i} className={cn("rounded-sm px-1.5", !other.includes(l) && "bg-surface-3")}>{l}</li>)}
    </ol>
  );
  return (
    <section>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
              className="flex items-center gap-1.5 text-sm font-medium text-muted hover:text-fg">
        <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} /> Earlier takes ({video.takes})
      </button>
      {open && shown && (
        <div className="mt-2 flex flex-col gap-3 rounded-md border border-line p-3">
          <div className="flex flex-wrap gap-1.5">
            {takes.map((t) => (
              <button key={t.index} type="button" onClick={() => setPick(t.index)} aria-pressed={t.index === shown.index}
                      className={cn("h-7 rounded-sm border px-2.5 text-xs", t.index === shown.index ? "border-line-strong bg-surface-3 text-fg" : "border-line text-muted hover:text-fg")}>
                {t.index + 1}: {t.why}{t.at ? ` · ${t.at.slice(11, 16)}` : ""}
              </button>
            ))}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-muted">Take {shown.index + 1}: {shown.title}</span>
              {lines(shown.lines, now)}
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-muted">Now: {video.script.title}</span>
              {lines(now, shown.lines)}
            </div>
          </div>
          <Button size="sm" variant="secondary" className="self-start" onClick={() => use(shown.index)}>Use take {shown.index + 1}</Button>
        </div>
      )}
    </section>
  );
}

/** Whether the script carries a check's verdict (D208), from the AI's writing or Check: a script written by hand or
 *  restored without one starts with Check as its next step; a ready-made one was checked when it was made. */
const isChecked = (notes: string) => /no problems|fixed|still unsure|^- |checked by|^a ready-made script/im.test(notes);

/** Lines about how a script was made, not about the script: folded under the verdict (D196). */
const MADE = /^(written by|checked by|drawn by|\(edited by hand|restored from|.*didn't answer|.*out of its quota)/i;

/** The checks' verdict in one line, coloured by what it says, with the findings and how it was made folded under
 *  it (D196): a fixed script was a wall of orange, a restored one orange for no reason. */
function CheckNote({ text }: { text: string }) {
  if (!text.trim()) return null;
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const made = lines.filter((l) => MADE.test(l));
  const rest = lines.filter((l) => !MADE.test(l));
  const open = rest.some((l) => /still unsure|^- /.test(l)) && !rest.some((l) => /fixed/i.test(l));
  const fixed = rest.some((l) => /fixed/i.test(l));
  const verdict = open ? "Worth a look: the checks found something" : fixed ? "Checked: problems found and fixed in the rewrite"
    : rest.some((l) => /no problems/i.test(l)) ? "Checked: no problems found" : rest[0] ?? "How it was made";
  return (
    <details className={cn("rounded-md border px-3 py-2 text-xs",
      open ? "border-warning/40 text-warning" : "border-success/40 text-success")}>
      <summary className="cursor-pointer font-medium">{verdict}</summary>
      {rest.length > 0 && <p className="mt-2 whitespace-pre-line text-fg">{rest.join("\n")}</p>}
      {made.length > 0 && <p className="mt-2 whitespace-pre-line text-subtle">{made.join("\n")}</p>}
    </details>
  );
}

const blankVisual = (): CreateVisual => ({ kind: "stock", query: "", queries: [], template: "", title: "", labels: [] });
type PictureKind = "auto" | "stock" | "card" | "sketch" | "hold";
const kindOf = (v: CreateVisual): PictureKind =>
  v.hold ? "hold" : !v.manual ? "auto" : v.kind === "stock" ? "stock" : v.template === "card" ? "card" : "sketch";

/** Choosing a sentence's picture yourself (D120): footage by your own search words, a chalk
 *  phrase, or a drawing you describe. "Automatic" hands it back to Clipper. */
function PictureChoice({ visual, emphasis, onChange, canHold, canBuild }: {
  visual: CreateVisual; emphasis: string; onChange: (v: CreateVisual, emphasis?: string) => void; canHold: boolean; canBuild: boolean;
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
        {drawings && (canHold || kind === "hold") && <option value="hold">Keep the drawing above and add to it</option>}
      </select>
      {kind !== "auto" && kind !== "hold" && (
        <input key={`${kind}-${value}`} defaultValue={value} placeholder={placeholder} aria-label={placeholder}
               onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") e.currentTarget.blur(); }}
               onBlur={(e) => e.target.value.trim() !== value && commit(e.target.value)}
               className={cn(field, "min-w-48 flex-1")} />
      )}
      {kind === "sketch" && canBuild && (
        <Tip label="Starts from the drawing an earlier sentence made, with its marks already on the board, and draws only what this sentence adds. For when footage came in between.">
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" className="accent-[var(--color-accent)]" checked={!!visual.builds}
                   onChange={(e) => onChange({ ...visual, builds: e.target.checked, sketch: null })} />
            Goes on with an earlier drawing
          </label>
        </Tip>
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
    setOpen(i + by);
  };
  // One sentence's controls at a time (D205): nine rows of six controls each was a wall; the words and the
  // picture stay in view for every sentence, its picture, highlight, camera, pose and order open on click or Tab.
  const [open, setOpen] = useState<number | null>(null);
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
          <div key={i} onFocus={() => setOpen(i)} onClick={() => setOpen(i)} data-open={!locked && open === i}
               className={cn("flex flex-col gap-1.5", !locked && "rounded-md border p-2",
                             !locked && (open === i ? "border-line bg-surface-2/40" : "border-transparent"))}>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_200px]">
              <textarea rows={!locked && open === i ? 3 : 2} value={b.text} disabled={locked} aria-label={`Sentence ${i + 1}`}
                        onChange={(e) => setBeats(beats.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
                        onBlur={() => b.text !== s.beats[i]?.text && void save({ beats })}
                        className="resize-none rounded-md border border-line bg-surface-1 px-3 py-2 text-sm focus:border-accent focus:outline-none disabled:opacity-80" />
              <Picture visual={b.visual} />
            </div>
            {!locked && open === i && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <PictureChoice visual={b.visual} emphasis={b.emphasis} canBuild={beats.slice(0, i).some((x) => x.visual.kind === "diagram")} canHold={i > 0 && (beats[i - 1].visual.kind === "diagram" || !!beats[i - 1].visual.hold)} onChange={(v, emphasis) => patch(i, { visual: v, ...(emphasis !== undefined ? { emphasis } : {}) })} />
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
                  <Tip label="Move up"><Button size="icon" variant="ghost" className={small} aria-label="Move up" disabled={i === 0} onClick={(e) => { e.stopPropagation(); move(i, -1); }}><ArrowUp className="size-3.5" /></Button></Tip>
                  <Tip label="Move down"><Button size="icon" variant="ghost" className={small} aria-label="Move down" disabled={i === beats.length - 1} onClick={(e) => { e.stopPropagation(); move(i, 1); }}><ArrowDown className="size-3.5" /></Button></Tip>
                  <Tip label="Add a sentence below"><Button size="icon" variant="ghost" className={small} aria-label="Add a sentence below"
                    onClick={(e) => { e.stopPropagation(); commit([...beats.slice(0, i + 1), { text: "New sentence.", emphasis: "", visual: blankVisual() }, ...beats.slice(i + 1)]); setOpen(i + 1); }}><Plus className="size-3.5" /></Button></Tip>
                  <Tip label="Remove this sentence"><Button size="icon" variant="ghost" className={small} aria-label="Remove this sentence" disabled={beats.length <= 1}
                    onClick={(e) => { e.stopPropagation(); commit(beats.filter((_, j) => j !== i)); setOpen(null); }}><X className="size-3.5" /></Button></Tip>
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
      {/* Folded (D205): the post's words aren't the script's, and sat open under every draft. */}
      <Fold id={`post-${video.id}`} title="Description and hashtags">
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
      </Fold>
    </div>
  );
}

function Picture({ visual }: { visual: CreateScript["beats"][number]["visual"] }) {
  if (visual.hold && !visual.clip) {
    return (
      <Tip label="The drawing above stays on screen through this sentence, its parts arriving as they're said.">
        <span className="flex max-w-full items-center gap-1.5 self-start rounded-full bg-surface-2 px-2.5 py-1 text-xs text-muted">
          <Shapes className="size-3.5 shrink-0" /><span className="truncate">Keeps the drawing above</span>
        </span>
      </Tip>
    );
  }
  if (visual.clip) {
    return (
      <Tip label="Your own clip. The planned picture fills any time it doesn't cover.">
        <span className="flex max-w-full items-center gap-1.5 self-start rounded-full bg-surface-2 px-2.5 py-1 text-xs text-fg">
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
      <span className="flex max-w-full items-center gap-1.5 self-start rounded-full bg-surface-2 px-2.5 py-1 text-xs text-muted">
        {diagram ? <Shapes className="size-3.5 shrink-0" /> : <Film className="size-3.5 shrink-0" />}
        <span className="truncate">{diagram ? `Diagram: ${visual.title || visual.template}` : visual.query}</span>
      </span>
    </Tip>
  );
}

/* ---------- the voice: a file, recorded here, or none (D146) ---------- */

function VoiceStep({ video, title = "Step 2 of 4: add the voice" }: { video: CreateVideo; title?: string }) {
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
  // The punchline is the second to last sentence on a loop (D200): the last one runs back into the start.
  const loop = video.script.loop ?? video.script.ending === "loop";
  const at = loop && beats.length >= 3 ? beats.length - 2 : beats.length - 1;
  const eleven = [...beats.slice(0, at), ...(beats.length > 1 ? ["[pause]"] : []), ...beats.slice(at)].join("\n");
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
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Mic className="size-4 text-accent" /> {title}</h3>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="How you give the voice">
        {ways.map(([key, label]) => (
          <button key={key} type="button" role="radio" aria-checked={way === key} onClick={() => setWay(key)}
                  className={cn("h-8 rounded-sm border px-3 text-sm font-medium", way === key ? "border-line-strong bg-surface-3 text-fg" : "border-line text-muted hover:text-fg")}>
            {label}
          </button>
        ))}
      </div>
      {way === "file" && <>
        <Step n={1}>
          <span className="flex-1">Copy the script.</span>
          <Button size="sm" variant="primary" onClick={() => copyText(text, "Script")}><ClipboardCopy className="size-3.5" /> Copy script</Button>
          {/elevenlabs/i.test(how) && (
            <Tip label="A sentence a line and a [pause] before the punchline, for Eleven v3. Try stability 0.55 to 0.65, similarity 0.75, style 0 to 0.1, and make the punchline 2 or 3 times to keep the flattest.">
              <Button size="sm" variant="secondary" onClick={() => copyText(eleven, "Script for ElevenLabs")}><ClipboardCopy className="size-3.5" /> For ElevenLabs</Button>
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
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
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
                  onClick={() => gotIt(() => createApi.noVoice(video.id), "Building without a voice")}>
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
  const [secs, setSecs] = useState(0);
  // One link to the take, freed when it's replaced (D196): a new one each render leaked the recording.
  const takeUrl = useMemo(() => (take ? URL.createObjectURL(take) : ""), [take]);
  useEffect(() => () => { if (takeUrl) URL.revokeObjectURL(takeUrl); }, [takeUrl]);
  useEffect(() => {
    if (!recording) return;
    setSecs(0);
    const t = setInterval(() => setSecs((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [recording]);
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
          ? <Button variant="primary" onClick={() => start()}><Mic className="size-4" /> {take ? "Record again" : "Start recording"}</Button>
          : <Button variant="danger" onClick={stop}><span className="size-2.5 animate-pulse rounded-full bg-danger" /> Stop · {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")}</Button>}
        {take && !recording && <audio controls src={takeUrl} className="h-9" />}
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
    <span className="grid size-6 shrink-0 place-items-center rounded-full bg-surface-3 text-xs font-bold text-fg">{n}</span>
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

/** Music and sound effects for this one video (D156, D197), so two videos can differ for an experiment; used on Build again. */
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
  const style = (key: "zooms" | "cut_ins" | "loop", label: string, usual = true) => (
    <label className="flex items-center gap-1.5">
      <input type="checkbox" className="accent-[var(--color-accent)]" checked={video.script[key] ?? usual}
             disabled={video.status === "building"}
             onChange={(e) => void createApi.editStyle(video.id, { [key]: e.target.checked })
               .then(() => qc.invalidateQueries({ queryKey: ["create"] }), (err: Error) => toast.error(err.message))} />
      {label}
    </label>
  );
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
      {pick("music", "Music")}{pick("sfx", "Sound effects")}
      {style("zooms", "Zoom-ins")}{style("cut_ins", "Cut-ins on the presenter")}
      <Tip label="The last second and a half plays the opening clip up to its first frame, the hook comes back and the presenter ends as he starts, so the video runs straight into itself">
        <span>{style("loop", "Seamless loop", video.script.ending === "loop")}</span>
      </Tip>
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
      <Tip label="Opening and punchline: he slides away after the first sentence and comes back for the punchline, so the drawings carry the middle. Try it on every other video and compare how long viewers stay">
        <label className="flex items-center gap-1.5">
          Presenter
          <select value={video.script.on_screen ?? "all"} aria-label="Presenter" disabled={video.status === "building"}
                  className="h-7 rounded-sm border border-line bg-surface-2 px-1.5 text-xs"
                  onChange={(e) => void createApi.editStyle(video.id, { on_screen: e.target.value })
                    .then(() => qc.invalidateQueries({ queryKey: ["create"] }), (err: Error) => toast.error(err.message))}>
            <option value="all">Whole video</option><option value="ends">Opening and punchline</option>
          </select>
        </label>
      </Tip>
      <span className="text-subtle">Changes show on Build again.</span>
    </div>
  );
}

function Built({ video, wps, busy, onPictures, onRebuild }: {
  video: CreateVideo; wps: number; busy: string | null; onPictures: () => void; onRebuild: () => void;
}) {
  const { data: clips = [] } = useClips();
  const clip = clips.find((c) => c.id === video.clip_id);
  const player = useRef<HTMLVideoElement>(null);
  const [replan, setReplan] = useState(false);   // New pictures asks first (D199): one click replanned every part
  const [now, setNow] = useState<number | null>(null);   // where the player is: its part lights up (D199)
  const [rewording, setRewording] = useState(false);
  const qc = useQueryClient();
  /** Back to a draft (D199): approved, then draft, as "Edit the script again" does from approved. The build stays in Clips. */
  const reword = async () => {
    try {
      await createApi.edit(video.id, {});
      await createApi.edit(video.id, {});
      if (video.archived) await createApi.archive(video.id, false);   // a draft belongs in Create, not the Archive
      await qc.invalidateQueries({ queryKey: ["create"] });
      toast.success("Back to the script", { description: "Change the words, approve them and record the voice again. The last build stays in Clips." });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const shots = video.shots ?? [];
  const total = shots.length ? shots[shots.length - 1].end : 0;
  const seek = (t: number) => {
    const el = player.current;
    if (!el) return;
    el.currentTime = t;
    void el.play().catch(() => undefined);
  };
  return (
    <div className="flex flex-wrap gap-4 lg:flex-nowrap lg:items-start">
      {/* The player stays in view beside the parts on a wide screen (D199): it scrolled away while you went through
          10 to 15 of them, and "Play from here" started a player you couldn't see. */}
      <div className="flex w-56 shrink-0 flex-col gap-2 self-start lg:sticky lg:top-4 xl:w-72">
        {clip?.file_exists ? (
          <video ref={player} key={clip.video} src={clip.video} poster={clip.thumb} controls playsInline className="aspect-[9/16] w-full rounded-xl bg-black"
                 onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)} />
        ) : <div className="grid aspect-[9/16] w-full place-items-center rounded-xl bg-surface-2 text-sm text-muted">Video not found</div>}
        {shots.length > 1 && total > 0 && (
          <div className="flex h-2 w-full gap-px overflow-hidden rounded-full">
            {shots.map((shot) => (
              <button key={shot.beats[0]} type="button" onClick={() => seek(shot.start)} aria-label={`Play from ${shot.start.toFixed(1)}s`}
                      style={{ width: `${((shot.end - shot.start) / total) * 100}%` }}
                      className={cn("h-full bg-surface-3 hover:bg-line-strong",
                        now != null && shot.start <= now && now < shot.end && "bg-accent hover:bg-accent")} />
            ))}
          </div>
        )}
      </div>
      {/* At least 16rem: narrower, it drops under the player instead of one word a line (D196). */}
      <div className="flex min-w-64 flex-1 flex-col gap-4">
        <div className="flex flex-col gap-2">
          {video.error && (
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-danger/40 p-3 text-sm text-danger">
              <AlertTriangle className="size-4 shrink-0" />
              <span className="flex-1">The last rebuild didn't finish: {video.error.replace(/\.?\s*$/, ".")} The video here is the one built before, unchanged.</span>
              <Button size="sm" variant="secondary" disabled={busy !== null} onClick={onRebuild}>Try again</Button>
            </div>
          )}
          <PostedLine video={video} />
          {/* Who wrote and drew it, and what didn't work, stay visible after the build (D118). */}
          <CheckNote text={video.check_notes} />
          {/* One main button and one other (D199); the rest under More: six equal buttons sat over the parts. */}
          <div className="flex flex-wrap items-center gap-2">
            {clip && <Button variant="primary" onClick={() => useUI.getState().setOpenClip(clip.id)}>Open to post</Button>}
            <Tip label="Same words, voice and pictures, built again; only parts you asked to change, or that had no footage, are made again">
              <Button variant="secondary" disabled={busy !== null} onClick={onRebuild}>
                {busy === "rebuild" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} Build again
                {(video.unbuilt?.length ?? 0) > 0 && <span className="text-xs text-muted">· {video.unbuilt!.length} not built yet</span>}
              </Button>
            </Tip>
            <details className="relative">
              <summary className={cn(buttonClass("ghost"), "cursor-pointer list-none [&::-webkit-details-marker]:hidden")}>
                <MoreHorizontal className="size-4" /> More
              </summary>
              <div className="absolute left-0 z-20 mt-1 flex w-60 flex-col rounded-md border border-line bg-surface-1 p-1 text-sm shadow-3">
                {/* Where this video goes next, and where its numbers are (D143). */}
                {clip && (clip.posts.length > 0 || video.posted) && (
                  <Link to="/stats" search={{ campaign: clip.campaign }} className="rounded-sm px-3 py-2 hover:bg-surface-2">See its stats</Link>
                )}
                {clip && (
                  <Link to="/clips" search={{ campaign: clip.campaign, status: "all" }} className="rounded-sm px-3 py-2 hover:bg-surface-2">All your Shorts in Clips</Link>
                )}
                <button type="button" disabled={busy !== null}
                        onClick={(e) => { setReplan(true); e.currentTarget.closest("details")?.removeAttribute("open"); }}
                        className="rounded-sm px-3 py-2 text-left hover:bg-surface-2 disabled:opacity-50">New pictures for every part…</button>
              </div>
            </details>
          </div>
          {replan && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-line p-3 text-sm">
              <span className="flex-1 text-muted">Plans every picture you haven't chosen yourself again and builds it; same words and voice. Uses AI.</span>
              <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => { setReplan(false); onPictures(); }}>
                {busy === "pictures" ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />} New pictures
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setReplan(false)}>Keep these</Button>
            </div>
          )}
        </div>
      {/* Folded beside Captions (D199): five controls and the cover row sat between the buttons and the parts. */}
      <Fold id={`look-${video.id}`} title="Look and sound">
        <div className="flex flex-col gap-3">
          <SoundChoice video={video} />
          <CoverChoice video={video} player={player} />
        </div>
      </Fold>
      <Fold id={`review-${video.id}`} title="Change parts you don't like" startOpen>
        <ShotReview video={video} seek={seek} onRebuild={onRebuild} rebuilding={busy === "rebuild"} now={now} />
      </Fold>
      <Fold id={`captions-${video.id}`} title={`Captions${video.script.captions === false ? " (off)" : ""}`}>
        <CaptionsEditor video={video} />
      </Fold>
      <Fold id={`clips-${video.id}`} title={`Your own clips${video.mine?.clips.length ? ` (${video.mine.clips.length})` : ""}`}
              startOpen={(video.mine?.clips.length ?? 0) > 0}>
        <MyClips video={video} wps={wps} onRebuild={onRebuild} busyRebuild={busy === "rebuild"} />
      </Fold>
      {/* A built video was a dead end for its words and voice (D199), though the server takes both. */}
      <Fold id={`words-${video.id}`} title="Words and voice">
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted">{video.script.beats.map((b) => b.text.trim()).join(" ")}</p>
          {rewording ? (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-line p-3 text-sm">
              <span className="flex-1 text-muted">Change the words? You'll record the voice again. This build stays in Clips.</span>
              <Button size="sm" variant="primary" onClick={() => reword()}><PenLine className="size-3.5" /> Change the words</Button>
              <Button size="sm" variant="ghost" onClick={() => setRewording(false)}>Keep them</Button>
            </div>
          ) : (
            <Button variant="secondary" className="self-start" disabled={busy !== null} onClick={() => setRewording(true)}>
              <PenLine className="size-4" /> Change the words
            </Button>
          )}
          <VoiceStep video={video} title="Replace the voice: same words, built again" />
        </div>
      </Fold>
      {/* One Build bar (D200): every change not built yet, with the button, held in view at the foot while you work
          down the parts; the other rebuild buttons sat wherever you had made the change. */}
      {(video.unbuilt?.length ?? 0) > 0 && (
        <div className="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 -mx-1 flex flex-wrap items-center gap-2 rounded-md border border-line-strong bg-surface-1/95 px-3 py-2 text-sm shadow-2 backdrop-blur md:bottom-2">
          <span className="min-w-0 flex-1 text-muted">
            <b className="text-fg">{video.unbuilt!.length} change{video.unbuilt!.length === 1 ? "" : "s"} not built yet</b>: {video.unbuilt!.slice(0, 4).join(", ")}{video.unbuilt!.length > 4 ? "…" : ""}
          </span>
          <Button variant="primary" disabled={busy !== null} onClick={onRebuild}>
            {busy === "rebuild" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} Build again
          </Button>
        </div>
      )}
      </div>
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
