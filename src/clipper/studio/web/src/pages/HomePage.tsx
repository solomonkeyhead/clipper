import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowRight, CheckCircle2, Inbox, ListChecks, Send, Sparkles, TrendingUp, Wand2 } from "lucide-react";
import { useCampaigns, useClips, useCreate, useHome, usePosts, useSetSettings, useSetTask, useSettings, useUses, type Clip } from "@/api/client";
import { Button, Card, Metric, PageHeader, Skeleton, Sparkline, Tip } from "@/components/ui";
import { useUI } from "@/lib/store";
import { ago, cn, formatCount, formatMoney } from "@/lib/utils";
import { PLATFORM_NAME } from "@/api/platforms.gen";
import { PlatformIcon } from "@/components/PlatformIcon";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Morning." : h < 18 ? "Afternoon." : "Evening.";
}

/** The money, tidy: the estimate (with its trend) beside what has actually been paid, and what
 *  is earned on paper but under a campaign's minimum payout (D99, D102, D118). */
function Earnings({ est, paid, views, posts, trend, locked, lockedPosts, window }: {
  est: number | null; paid: number | null | undefined; views: number; posts: number; trend: number[];
  locked: number; lockedPosts: number; window: Range;
}) {
  const all = window === "all";
  const waiting = all && est !== null ? Math.max(0, est - (paid ?? 0)) : 0;
  return (
    <Link to="/stats" className="block sm:col-span-2" aria-label="Open Stats">
    <Card className="flex h-full flex-col gap-3 p-5 transition-colors hover:border-line-strong hover:bg-surface-2">
      <div className="grid grid-cols-2 gap-4">
        <Tip label="Views ÷ 1,000 × each campaign's rate, for posts that have reached their campaign's minimum payout. Campaigns verify views themselves.">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Estimated earnings{all ? "" : `, last ${window} days`}</span>
            <span className="num text-[clamp(2.2rem,4vw,3.4rem)] leading-none text-fg">{est === null ? "–" : formatMoney(est)}</span>
          </div>
        </Tip>
        <Tip label="The payouts you've recorded on each campaign's page. Only this is money received.">
          <div className="flex flex-col gap-1 border-l border-dashed border-line-strong pl-4">
            <span className="text-xs font-medium text-muted">Actually paid (all time)</span>
            <span className="num text-[clamp(2.2rem,4vw,3.4rem)] leading-none text-money">{formatMoney(paid ?? 0)}</span>
          </div>
        </Tip>
      </div>
      {trend.length >= 2 && <Sparkline values={trend} className="h-10" />}
      <div className="flex flex-col gap-1 border-t border-dashed border-line-strong pt-3 text-sm text-muted">
        <span>
          {est === null ? "Add a campaign's pay rate to see an estimate"
            : <>From <b className="text-fg">{formatCount(views)}</b> views{all ? <> on <b className="text-fg">{posts}</b> posts</> : <> gained in the last {window} days</>}</>}
        </span>
        {waiting > 0 && <span><b className="text-fg">{formatMoney(waiting)}</b> earned, not paid yet{paid == null && ": record payouts on each campaign's page"}</span>}
        {all && locked > 0 && (
          <span><b className="text-warning">{formatMoney(locked)}</b> more on {lockedPosts} post{lockedPosts === 1 ? "" : "s"} still under the campaign's minimum payout (pays nothing until it's reached)</span>
        )}
      </div>
    </Card>
    </Link>
  );
}

type Range = "7" | "30" | "all";
const RANGES: [Range, string][] = [["7", "7 days"], ["30", "30 days"], ["all", "All time"]];

/** Which stretch of time the money and views cover; all time unless chosen. */
function RangeToggle({ value, onChange }: { value: Range; onChange: (r: Range) => void }) {
  return (
    <div className="flex items-center justify-end gap-1" role="radiogroup" aria-label="Time range">
      {RANGES.map(([key, label]) => (
        <button key={key} type="button" role="radio" aria-checked={value === key} onClick={() => onChange(key)}
          className={cn("rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
            value === key ? "bg-accent-soft text-fg" : "text-muted hover:bg-surface-2 hover:text-fg")}>
          {label}
        </button>
      ))}
    </div>
  );
}

function PipelineColumn({ title, status, clips, tone }: {
  title: string; status: "ready" | "posted" | "submitted"; clips: Clip[]; tone: string;
}) {
  const open = useUI((s) => s.setOpenClip);
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-lg bg-surface-1 p-3">
      <div className="flex items-center justify-between px-1">
        <Link to="/clips" search={{ status }} className="flex items-center gap-2 text-sm font-medium hover:text-accent">
          <span className={cn("size-2 rounded-full", tone)} /> {title}
        </Link>
        <span className="tabular text-xs text-muted">{clips.length}</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {clips.slice(0, 5).map((c) => (
          <button key={c.id} onClick={() => open(c.id)}
                  className="flex items-center gap-2.5 rounded-md p-1.5 text-left hover:bg-surface-2">
            <img src={c.thumb} alt="" loading="lazy" className="aspect-[9/16] w-7 shrink-0 rounded-[4px] bg-black object-cover" />
            <span className="line-clamp-2 text-xs leading-snug">{c.title}</span>
          </button>
        ))}
        {clips.length > 5 && (
          <Link to="/clips" search={{ status }} className="px-1.5 text-xs text-subtle hover:text-accent">+{clips.length - 5} more</Link>
        )}
        {clips.length === 0 && <span className="px-1.5 py-2 text-xs text-subtle">Nothing here</span>}
      </div>
    </div>
  );
}

const USES: { key: string; title: string; body: string }[] = [
  { key: "use_campaigns", title: "Clip footage for campaigns", body: "Brands or creators pay clippers per view (Whop, Vyro, Content Rewards...), or you clip for a client or for your own long videos." },
  { key: "use_create", title: "Make original videos", body: "Create writes a short script, you add the voice, and Clipper builds the video for your own channel." },
  { key: "use_finder", title: "Find campaigns for me", body: "Watch your inbox, Discord or Whop for new campaigns that fit what you clip." },
];

/** The one question asked once: what do you use Clipper for? Parts you don't use stay out of the way (D145). */
function Welcome() {
  const { data: settings } = useSettings();
  const save = useSetSettings();
  const [picked, setPicked] = useState<Record<string, boolean>>({ use_campaigns: true, use_create: false, use_finder: false });
  const [about, setAbout] = useState("");
  const done = () => save.mutate({
    onboarded: "1", alert_profile: about.trim(),
    ...Object.fromEntries(USES.map((u) => [u.key, picked[u.key] ? "1" : "0"])),
  });
  if (!settings) return null;
  return (
    <Card className="flex flex-col gap-4 p-5">
      <div>
        <h2 className="text-md font-semibold">Welcome. What will you use Clipper for?</h2>
        <p className="mt-1 text-sm text-muted">Pick what applies. Anything you leave out stays hidden, and you can change it later in Settings.</p>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {USES.map((u) => (
          <label key={u.key} className={cn("flex cursor-pointer flex-col gap-1.5 rounded-md border p-4", picked[u.key] ? "border-accent bg-accent-soft/50" : "border-line")}>
            <span className="flex items-center gap-2 text-sm font-semibold">
              <input type="checkbox" className="accent-[var(--color-accent)]" checked={picked[u.key]}
                     onChange={(e) => setPicked({ ...picked, [u.key]: e.target.checked })} /> {u.title}
            </span>
            <span className="text-xs text-muted">{u.body}</span>
          </label>
        ))}
      </div>
      {picked.use_finder && (
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">What do you clip? (so only campaigns that fit are shown)</span>
          <textarea value={about} onChange={(e) => setAbout(e.target.value)} rows={2}
                    placeholder="e.g. A TikTok account for TV comedy clips. Not interested in crypto or supplements."
                    className="rounded-md border border-line bg-surface-1 p-3 text-sm outline-none focus:border-accent" />
        </label>
      )}
      <div><Button variant="primary" disabled={save.isPending || !Object.values(picked).some(Boolean)} onClick={done}>Continue <ArrowRight className="size-4" /></Button></div>
    </Card>
  );
}

/** Where today's Short stands and the one thing to do next, with a button to it (D118). */
function ChannelCard() {
  const { data } = useCreate();
  if (!data) return null;
  const video = data.videos.find((v) => v.status !== "built");
  const last = data.videos[0];
  const [text, action]: [string, string] = !video
    ? [last ? `Last Short: “${last.script.title || "Untitled"}”. Ready for the next one?` : "No Short yet.", "Make a Short"]
    : video.status === "draft" ? [`“${video.script.title || "Untitled"}” is written: read it and approve it.`, "Open the script"]
    : video.status === "approved" ? [`“${video.script.title}” is approved: make the voice on ElevenLabs and drop it in.`, "Add the voice"]
    : video.status === "failed" ? [`“${video.script.title}” didn't build: ${video.error || "see why"}.`, "See why"]
    : [`“${video.script.title}” is building${video.pct != null ? `: ${Math.round(video.pct)}%` : ""}.`, "Watch it"];
  return (
    <Card className="flex flex-wrap items-center gap-3 p-4">
      <Wand2 className="size-5 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium text-muted">{data.channel.name}</div>
        <div className="text-sm">{text}</div>
      </div>
      <Link to="/create" className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">
        {action} <ArrowRight className="size-3.5" />
      </Link>
    </Card>
  );
}

const STEPS: { key: string; title: string; body: string; to: string; action: string }[] = [
  { key: "ai", title: "Add your free Gemini key", body: "Clipping uses it to find the best moments and write captions. Takes a minute. (Create uses your Claude plan instead.)",
    to: "/settings", action: "Add key" },
  { key: "campaign", title: "Add a campaign", body: "Paste the brief of a campaign you've joined; Clipper fills in its rules.",
    to: "/campaigns/new", action: "Add campaign" },
  { key: "clips", title: "Make your first clips", body: "Drop in the campaign's footage and Clipper cuts, frames and captions it.",
    to: "/new", action: "Make clips" },
  { key: "accounts", title: "Connect TikTok or Instagram", body: "So Clipper can track views, earnings and your posts' links.",
    to: "/accounts", action: "Connect" },
];

function GetStarted({ done }: { done: Record<string, boolean> }) {
  const uses = useUses();
  // Only the steps that match what you said you'd use (D145).
  const STEPS_SHOWN = STEPS.filter((s) => s.key === "ai" || s.key === "accounts" || uses.campaigns);
  const next = STEPS_SHOWN.find((s) => !done[s.key]);
  const count = STEPS_SHOWN.filter((s) => done[s.key]).length;
  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-md font-semibold">Get started</h2>
        <span className="tabular text-xs text-muted">{count} of {STEPS_SHOWN.length} done</span>
      </div>
      <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {STEPS_SHOWN.map((step, i) => {
          const isDone = Boolean(done[step.key]);
          const isNext = step === next;
          return (
            <li key={step.key} className={cn("flex flex-col gap-2 rounded-md border p-4",
              isNext ? "border-accent bg-accent-soft/50" : "border-line", isDone && "opacity-60")}>
              <div className="flex items-center gap-2">
                {isDone ? <CheckCircle2 className="size-5 text-success" />
                  : <span className="grid size-5 place-items-center rounded-full bg-surface-3 text-[11px] font-bold">{i + 1}</span>}
                <span className={cn("text-sm font-semibold", isDone && "line-through")}>{step.title}</span>
              </div>
              <p className="flex-1 text-xs text-muted">{step.body}</p>
              {!isDone && (
                <Link to={step.to}
                      className={cn("inline-flex h-8 w-fit items-center gap-1.5 rounded-sm px-3 text-sm font-medium",
                        isNext ? "bg-accent text-accent-fg hover:bg-accent-hover" : "border border-line bg-surface-2 hover:bg-surface-3")}>
                  {step.action} <ArrowRight className="size-3.5" />
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

export function DashboardPage() {
  const { data: home } = useHome();
  const { data: clips = [] } = useClips();
  const { data: posts = [] } = usePosts();
  const setTask = useSetTask();
  const [range, setRange] = useState<Range>("all");
  const open = useUI((s) => s.setOpenClip);
  const navigate = useNavigate();
  const { data: campaigns = [] } = useCampaigns();
  const { data: create } = useCreate();
  const uses = useUses();
  const archived = new Set(campaigns.filter((c) => c.archived).map((c) => c.name));

  if (!home) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
        <Skeleton className="h-40" />
      </div>
    );
  }
  const m = home.metrics;
  // All time starts at the first day the syncs saw any views (earlier days are "no data", not zero);
  // 7 or 30 days start that many days back, and show what was gained since.
  const days = m.views_by_day ?? [];
  const earnedDays = m.earned_by_day ?? [];
  const from = range === "all" ? Math.max(0, days.findIndex((v) => v > 0)) : Math.max(0, days.length - 1 - Number(range));
  const trend = days.slice(from);
  const earnedTrend = earnedDays.slice(from);
  const gained = (xs: number[]) => (xs.length ? Math.max(0, xs[xs.length - 1] - xs[0]) : 0);
  const viewsShown = range === "all" ? m.views : gained(trend);
  const earnedShown = range === "all" ? m.est_earnings : m.est_earnings === null ? null : gained(earnedTrend);
  const since = home.since;
  // The brief's view-milestone tasks reached and not done (D98), oldest post first.
  const due = posts.flatMap((post) => (post.tasks ?? []).filter((t) => !t.done).map((task) => ({ post, task })))
    .sort((a, b) => (a.post.posted_at ?? "").localeCompare(b.post.posted_at ?? ""));
  const firstRun = Object.values(home.first_run).some((done) => !done);
  const active = clips.filter((c) => c.status !== "skipped" && !archived.has(c.campaign));
  // To submit: not your own channel's, and not still short of the views the brief wants first (D154).
  const by = (s: string) => active.filter((c) => c.status === s && !(s === "posted" && (c.submits === false || c.submit_at_views)));
  const waitingViews = active.filter((c) => c.status === "posted" && c.submit_at_views);
  // A clip that repeats one already up isn't counted as one to post (D154).
  const toPost = by("ready").filter((c) => !c.duplicates?.length);
  // Each platform's median, side by side: one median across them all read "1" (D154).
  const medians = Object.entries(posts.reduce<Record<string, number[]>>((acc, p) => {
    (acc[p.platform] ??= []).push(p.views ?? 0);
    return acc;
  }, {})).map(([platform, views]) => {
    const sorted = [...views].sort((a, b) => a - b);
    return { platform, posts: sorted.length, median: sorted[Math.floor(sorted.length / 2)] };
  }).sort((a, b) => b.median - a.median);

  // The one thing to do next (D143): the first clip to post, else the first link to submit, else a
  // brief task, else the channel's next step, else more clips. One button, so opening the app has a next move.
  const channelBusy = uses.create ? create?.videos.find((v) => v.status !== "built") : undefined;
  const next: { label: string; run: () => void } | null = firstRun ? null
    : toPost.length ? { label: `Post the next clip (${toPost.length})`, run: () => void navigate({ to: "/post", search: { step: "post" } }) }
    : by("posted").length ? { label: `Submit the next clip (${by("posted").length})`, run: () => void navigate({ to: "/post", search: { step: "submit" } }) }
    : due.length ? { label: "Do the next brief task", run: () => open(due[0].post.clip) }
    : channelBusy ? { label: `Your Short: ${channelBusy.status === "draft" ? "approve the script" : channelBusy.status === "approved" ? "add the voice" : "see it"}`, run: () => void navigate({ to: "/create" }) }
    : uses.campaigns ? { label: "Make more clips", run: () => void navigate({ to: "/new" }) }
    : { label: "Make a Short", run: () => void navigate({ to: "/create" }) };
  // This week, in a line: what you posted and what it brought.
  const weekAgo = new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10);
  const weekPosts = posts.filter((p) => (p.posted_at ?? "") >= weekAgo).length;
  const weekViews = gained(days.slice(-8));
  const week = weekPosts || weekViews
    ? `This week: ${weekPosts} post${weekPosts === 1 ? "" : "s"} up, +${formatCount(weekViews)} views.` : "";

  return (
    <div className="fade-in flex flex-col gap-8">
      <PageHeader
        title={!firstRun && toPost.length ? `${greeting()} ${toPost.length} clip${toPost.length === 1 ? "" : "s"} to post` : greeting()}
        hi={`${toPost.length} clip`}
        subtitle={firstRun ? "Welcome to Clipper. Four steps and you're clipping." : `${week ? `${week} ` : ""}Here's where your clips stand.`}
        actions={next && <Button variant="primary" size="md" onClick={next.run}>{next.label} <ArrowRight className="size-4" /></Button>} />
      {uses.loaded && !uses.onboarded && <Welcome />}
      {uses.create && <ChannelCard />}
      {firstRun && uses.onboarded && <GetStarted done={home.first_run} />}
      {home.first_run.clips && <>

      <RangeToggle value={range} onChange={setRange} />
      <div className="-mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Earnings est={earnedShown} paid={m.paid_usd} views={viewsShown} posts={m.posts} window={range}
                  locked={m.locked_usd ?? 0} lockedPosts={m.locked_posts ?? 0} trend={earnedTrend} />
        <Metric label={range === "all" ? "Views" : `Views, last ${range} days`} value={formatCount(viewsShown)}
                trend={trend.length >= 2 ? trend : undefined}
                hint="Total views on your posts at the end of each day, from the syncs"
                to="/stats" search={{ sort: "views" }}
                sub={range === "all" ? "every campaign, archived too, since the first sync" : "gained, every campaign"} />
        <Tip label="The middle post on each platform: half your posts there get at least this many views.">
          <Link to="/stats" search={{ sort: "views" }} className="block rounded-lg">
            <Card className="flex h-full flex-col gap-2 p-4 transition-colors hover:border-line-strong hover:bg-surface-2">
              <span className="text-xs font-medium text-muted">Median views per post</span>
              {medians.length === 0 && <span className="num text-[1.9rem] leading-tight">–</span>}
              {medians.map((r) => (
                <div key={r.platform} className="flex items-center gap-2 text-sm">
                  <PlatformIcon platform={r.platform} className="size-4 text-muted" />
                  <span className="flex-1 text-muted">{PLATFORM_NAME[r.platform] ?? r.platform}</span>
                  <span className="num text-lg">{formatCount(r.median)}</span>
                </div>
              ))}
            </Card>
          </Link>
        </Tip>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="flex flex-col gap-4 p-5 lg:col-span-2">
          <h2 className="flex items-center gap-2 text-md font-semibold">
            <Sparkles className="size-4 text-accent" /> Since you were last here
          </h2>
          {since ? (
            <div className="grid gap-4 sm:grid-cols-3">
              <Link to="/stats" search={{ sort: "views" }} className="rounded-md hover:bg-surface-2">
                <div className={cn("tabular text-2xl font-semibold", since.views_gained > 0 ? "text-money" : "text-muted")}>+{formatCount(since.views_gained)}</div>
                <div className="text-xs text-muted">views since {ago(since.since)}</div>
              </Link>
              <Link to="/stats" className="rounded-md hover:bg-surface-2">
                <div className="tabular text-2xl font-semibold">{since.new_posts}</div>
                <div className="text-xs text-muted">new posts</div>
              </Link>
              <div className="min-w-0">
                {since.top_mover ? (
                  <button type="button" onClick={() => open(since.top_mover!.clip)} className="block w-full min-w-0 rounded-md text-left hover:bg-surface-2">
                    <div className="flex items-center gap-1.5 text-sm font-medium">
                      <TrendingUp className="size-4 text-money" /> Top mover
                    </div>
                    <div className="truncate text-xs text-muted">{since.top_mover.clip_title}</div>
                    <div className="tabular text-xs text-money">+{formatCount(since.top_mover_gain)} views</div>
                  </button>
                ) : <div className="text-sm text-muted">No movers yet</div>}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted">From your next visit, this shows the views and posts that came in while you were away.</p>
          )}
        </Card>

        <Card className="flex flex-col gap-2 p-5">
          <h2 className="mb-1 text-md font-semibold">Next up</h2>
          {m.ready > 0 && (
            <Link to="/clips" search={{ status: "ready" }} className="flex items-center gap-3 rounded-md p-2 hover:bg-surface-2">
              <Send className="size-4 text-accent" />
              <span className="flex-1 text-sm">{m.ready} clip{m.ready === 1 ? "" : "s"} ready to post</span>
              <ArrowRight className="size-4 text-subtle" />
            </Link>
          )}
          {by("posted").length > 0 && (
            <Link to="/clips" search={{ status: "posted" }} className="flex items-center gap-3 rounded-md p-2 hover:bg-surface-2">
              <Inbox className="size-4 text-warning" />
              <span className="flex-1 text-sm">{by("posted").length} clip{by("posted").length === 1 ? "" : "s"} to submit</span>
              <ArrowRight className="size-4 text-subtle" />
            </Link>
          )}
          {waitingViews.length > 0 && (
            <Link to="/clips" search={{ status: "posted" }} className="flex items-center gap-3 rounded-md p-2 text-muted hover:bg-surface-2">
              <Inbox className="size-4" />
              <span className="flex-1 text-sm">{waitingViews.length} posted, waiting for enough views to submit</span>
              <ArrowRight className="size-4 text-subtle" />
            </Link>
          )}
          {due.slice(0, 3).map(({ post, task }) => (
            <div key={`${post.url}-${task.views}`} className="flex items-start gap-3 rounded-md p-2 hover:bg-surface-2">
              <ListChecks className="mt-0.5 size-4 shrink-0 text-warning" />
              <button type="button" className="min-w-0 flex-1 text-left text-sm" onClick={() => open(post.clip)}
                      title={task.task}>
                <span className="line-clamp-1">{post.clip_title}</span>
                <span className="line-clamp-1 text-xs text-muted">Passed {formatCount(task.views)} views: {task.task}</span>
              </button>
              <Button size="sm" variant="ghost" onClick={() => setTask.mutate({ url: post.url, views: task.views, done: true })}>
                Done
              </Button>
            </div>
          ))}
          {due.length > 3 && <span className="px-2 text-xs text-subtle">+{due.length - 3} more brief tasks</span>}
          {m.ready === 0 && by("posted").length === 0 && !waitingViews.length && !due.length && (
            <p className="flex items-center gap-2 p-2 text-sm text-muted">
              <CheckCircle2 className="size-4 text-success" /> All caught up.
            </p>
          )}
        </Card>
      </div>

      <section>
        <h2 className="mb-3 text-md font-semibold">Pipeline</h2>
        <div className="grid gap-3 md:grid-cols-3">
          <PipelineColumn title="Ready to post" status="ready" clips={by("ready")} tone="bg-info" />
          <PipelineColumn title="To submit" status="posted" clips={by("posted")} tone="bg-warning" />
          <PipelineColumn title="Submitted" status="submitted" clips={by("submitted")} tone="bg-success" />
        </div>
      </section>
      </>}
    </div>
  );
}
