import { Link, useNavigate } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { ArrowRight, CheckCircle2, Inbox, ListChecks, TrendingUp, Wand2 } from "lucide-react";
import { useCampaignTitle, useCampaigns, useClips, useCreate, useHome, useHosted, usePosts, useSetTask, type Clip } from "@/api/client";
import { Button, Card, Skeleton, Tip, buttonClass } from "@/components/ui";
import { Poster, PosterTile, WallRow } from "@/components/wall";
import { useUI } from "@/lib/store";
import { ago, cn, formatCount, formatDuration, formatMoney } from "@/lib/utils";
import { PLATFORM_NAME } from "@/api/platforms.gen";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Morning." : h < 18 ? "Afternoon." : "Evening.";
}

/** The money and views as lines of text (D215): the estimate, what has actually been paid in its own colour, what is
 *  earned on paper but under a campaign's minimum payout (D99, D102, D118), then the views. Four boxes of big numbers
 *  and a trend line read as a dashboard template; the wall above is the page. */
function Numbers({ est, paid, views, posts, locked, lockedPosts, window, medians }: {
  est: number | null; paid: number | null | undefined; views: number; posts: number;
  locked: number; lockedPosts: number; window: Range; medians: { platform: string; median: number }[];
}) {
  const all = window === "all";
  const waiting = all && est !== null ? Math.max(0, est - (paid ?? 0)) : 0;
  const dots = (parts: ReactNode[]) => parts.filter(Boolean).flatMap((part, i) => (i ? [<span key={`d${i}`} aria-hidden className="text-subtle">·</span>, part] : [part]));
  return (
    <div className="flex flex-col gap-2 text-sm text-muted">
      <p className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        {dots([
          <Tip key="est" label="Views ÷ 1,000 × each campaign's rate, for posts that have reached their campaign's minimum payout. Campaigns verify views themselves.">
            <Link to="/stats" className="hover:text-fg">
              {est === null ? "Add a campaign's pay rate to see an estimate"
                : <><b className="num text-lg text-fg">{formatMoney(est)}</b> estimated earnings{all ? "" : `, last ${window} days`}</>}
            </Link>
          </Tip>,
          <Tip key="paid" label="The payouts you've recorded on each campaign's page. Only this is money received.">
            <Link to="/stats" className="hover:text-fg"><b className="num text-lg text-money">{paid == null ? "–" : formatMoney(paid)}</b> actually paid (all time)</Link>
          </Tip>,
          waiting > 0 && <span key="wait"><b className="tabular text-fg">{formatMoney(waiting)}</b> estimated, not paid yet{paid == null && ": record payouts on each campaign's page"}</span>,
        ])}
      </p>
      {all && locked > 0 && (
        <p>An estimated <b className="tabular text-warning">{formatMoney(locked)}</b> more on {lockedPosts} post{lockedPosts === 1 ? "" : "s"} still under the campaign's minimum payout (pays nothing until it's reached)</p>
      )}
      <p className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        {dots([
          <Tip key="views" label="Total views on your posts at the end of each day, from the syncs">
            <Link to="/stats" search={{ sort: "views" }} className="hover:text-fg">
              <b className="tabular text-fg">{formatCount(views)}</b> views{all ? <> on <b className="tabular text-fg">{posts}</b> posts, every campaign, archived too, since the first sync</> : <> gained in the last {window} days, every campaign</>}
            </Link>
          </Tip>,
          medians.length > 0 && (
            <Tip key="med" label="The middle post on each platform: half your posts there get at least this many views.">
              <Link to="/stats" search={{ sort: "views" }} className="hover:text-fg">
                Median views per post: {medians.map((r, i) => <span key={r.platform}>{i ? ", " : ""}{PLATFORM_NAME[r.platform] ?? r.platform} <b className="tabular text-fg">{formatCount(r.median)}</b></span>)}
              </Link>
            </Tip>
          ),
        ])}
      </p>
    </div>
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
          className={cn("h-8 rounded-sm px-3 text-xs font-semibold transition-colors pointer-coarse:h-11",
            value === key ? "bg-surface-3 text-fg" : "text-muted hover:bg-surface-2 hover:text-fg")}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** What an empty column says, and the step that fills it (D209): "Nothing here" left you nowhere to go. */
const EMPTY: Record<"ready" | "posted" | "submitted", [string, string?, string?]> = {
  ready: ["Nothing to post.", "/new", "Make clips"],
  posted: ["Nothing to submit: post a clip first."],
  submitted: ["None submitted yet."],
};

/** The next clip to post, in focus (D215): the poster large, what it is, and the one button. It is the first clip
 *  of the post queue, so the button opens the queue on it. */
function NextUp({ clip, label, run }: { clip: Clip; label: string; run: () => void }) {
  const open = useUI((s) => s.setOpenClip);
  const title = useCampaignTitle();
  const why = clip.reasons?.[0];
  return (
    <section className="grid items-center gap-x-10 gap-y-5 sm:grid-cols-[minmax(0,12rem)_1fr] lg:grid-cols-[13.5rem_1fr]">
      <button type="button" data-tile data-focus onClick={() => open(clip.id)} aria-label={`Open ${clip.title || "the clip"}`}
              className="w-40 outline-none sm:w-full">
        <Poster clip={clip} />
      </button>
      <div className="flex min-w-0 flex-col items-start gap-4">
        <h2 className="cap max-w-[20ch] text-[clamp(2rem,4vw,3.5rem)] break-words">{clip.title || clip.hook || `Clip ${clip.id}`}</h2>
        <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted">
          {[
            clip.score != null && <span key="s"><b className="text-fg">{clip.score.toFixed(1)}</b> score</span>,
            clip.duration_s ? <span key="d" className="tabular">{formatDuration(clip.duration_s)}</span> : null,
            <span key="c">{title(clip.campaign)}</span>,
            clip.source_title ? <span key="f" className="truncate">{clip.source_title}</span> : null,
          ].filter(Boolean).flatMap((part, i) => (i ? [<span key={`dot${i}`} aria-hidden>·</span>, part] : [part]))}
        </p>
        {why && <p className="max-w-[62ch] text-sm text-muted">{why}</p>}
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={run}>{label} <ArrowRight className="size-4" /></Button>
          <Button variant="secondary" onClick={() => open(clip.id)}>Open</Button>
        </div>
      </div>
    </section>
  );
}

/** Where today's Short stands and the one thing to do next, with a button to it (D118). */
function ChannelCard({ quiet = false }: { quiet?: boolean }) {
  const { data } = useCreate();
  const hosted = useHosted();
  if (!data) return null;
  const video = data.videos.find((v) => v.status !== "built");
  const last = data.videos[0];
  const [text, action]: [string, string] = !video
    ? [last ? `Last Short: “${last.script.title || "Untitled"}”. Ready for the next one?` : "No Short yet.", "Make a Short"]
    : video.status === "draft" ? [`“${video.script.title || "Untitled"}” is written: read it and approve it.`, "Open the script"]
    : video.status === "approved" ? [`“${video.script.title}” is approved: ${hosted ? "record or upload your voice" : "make the voice on ElevenLabs and drop it in"}.`, "Add the voice"]
    : video.status === "failed" ? [`“${video.script.title}” didn't build: ${video.error || "see why"}.`, "See why"]
    : [`“${video.script.title}” is building${video.pct != null ? `: ${Math.round(video.pct)}%` : ""}.`, "Watch it"];
  return (
    <Card className="flex flex-wrap items-center gap-3 px-4 py-3">
      <Wand2 className="size-5 shrink-0 text-muted" />
      <div className="min-w-0 flex-1 basis-56">
        <div className="text-xs font-semibold text-muted">{data.channel.name}</div>
        <div className="text-sm">{text}</div>
      </div>
      {/* Grey unless it is the next thing to do (D209, D215): one amber button on the page. */}
      <Link to="/create" className={buttonClass(quiet ? "secondary" : "primary")}>
        {action} <ArrowRight className="size-3.5" />
      </Link>
    </Card>
  );
}

// Three steps that fit anyone (D221): "Add a campaign" assumed clipping, and a Short's own campaign file ticked it.
const STEPS: { key: string; title: string; body: string; to: string; action: string; alt?: [string, string] }[] = [
  { key: "ai", title: "Add your free Gemini key", body: "Clipper uses it to write scripts, watch footage and write captions. Takes a minute.",
    to: "/settings", action: "Add key" },
  { key: "clips", title: "Make your first video", body: "Make a Short from your own idea, or clip the footage of a campaign you've joined.",
    to: "/create", action: "Make a Short", alt: ["/new", "Clip footage"] },
  { key: "accounts", title: "Connect your accounts", body: "So Clipper can find your posts and count their views.",
    to: "/accounts", action: "Connect" },
];

/** Setting up, as one list (D209): the next step is open with its button, the rest are a line each, and the list goes
 *  when all are done. Its first step asked what you'd use and hid the rest (D145); everything shows since D216. */
function Setup({ done }: { done: Record<string, boolean> }) {
  // Hosted, the AI is the site's (D229): there is no key to add.
  const hosted = useHosted();
  const steps = hosted === false ? STEPS : STEPS.filter((s) => s.key !== "ai");
  const isDone = (key: string) => Boolean(done[key]);
  const next = steps.find((s) => !isDone(s.key));
  if (!next) return null;
  const count = steps.filter((s) => isDone(s.key)).length;
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-md font-semibold">Set up Clipper</h2>
        <span className="tabular text-xs text-muted">{count} of {steps.length} done</span>
      </div>
      <ol className="flex flex-col divide-y divide-line">
        {steps.map((step, i) => {
          const finished = isDone(step.key);
          const open = step === next;
          return (
            <li key={step.key} className="flex flex-col gap-3 py-3 first:pt-0 last:pb-0">
              <div className="flex items-center gap-3">
                {finished ? <CheckCircle2 className="size-5 shrink-0 text-success" aria-label="Done" />
                  : <span className={cn("grid size-5 shrink-0 place-items-center rounded-full text-[12px] font-bold", open ? "bg-fg text-bg" : "bg-surface-3")}>{i + 1}</span>}
                <span className={cn("flex-1 text-sm", open ? "font-semibold" : finished ? "text-muted line-through" : "text-muted")}>{step.title}</span>
                {!finished && !open && <Link to={step.to} className="inline-flex min-h-8 items-center text-xs font-semibold text-muted hover:text-fg pointer-coarse:min-h-11">{step.action}</Link>}
              </div>
              {open && (
                <div className="flex flex-col gap-3 pl-8">
                  <p className="text-sm text-muted">{step.body}</p>
                  <span className="flex flex-wrap gap-2">
                    <Link to={step.to} hash={step.key === "ai" ? "ai" : undefined} className={cn(buttonClass("primary"), "w-fit")}>
                      {step.action} <ArrowRight className="size-3.5" />
                    </Link>
                    {step.alt && <Link to={step.alt[0]} className={cn(buttonClass("secondary"), "w-fit")}>{step.alt[1]}</Link>}
                  </span>
                </div>
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
  const firstRun = STEPS.some((s) => !home.first_run[s.key as keyof typeof home.first_run]);
  // Submitting links and money belong to paid campaigns (D221): your own channel alone has neither.
  const paid = campaigns.some((c) => !c.archived && !c.own_channel);
  const submits = campaigns.some((c) => !c.archived && c.submits !== false);
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
  const channelBusy = create?.videos.find((v) => v.status !== "built");
  const next: { label: string; run: () => void } | null = firstRun ? null
    : toPost.length ? { label: `Post the next clip (${toPost.length})`, run: () => void navigate({ to: "/post", search: { step: "post" } }) }
    : by("posted").length ? { label: `Submit the next clip (${by("posted").length})`, run: () => void navigate({ to: "/post", search: { step: "submit" } }) }
    : due.length ? { label: "Do the next brief task", run: () => open(due[0].post.clip) }
    : channelBusy ? { label: `Your Short: ${channelBusy.status === "draft" ? "approve the script" : channelBusy.status === "approved" ? "add the voice" : "see it"}`, run: () => void navigate({ to: "/create" }) }
    : paid ? { label: "Make more clips", run: () => void navigate({ to: "/new" }) }
    : { label: "Make a Short", run: () => void navigate({ to: "/create" }) };
  // This week, in a line: what you posted and what it brought.
  const weekAgo = new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10);
  const weekPosts = posts.filter((p) => (p.posted_at ?? "") >= weekAgo).length;
  const weekViews = gained(days.slice(-8));
  const week = weekPosts || weekViews
    ? `This week: ${weekPosts} post${weekPosts === 1 ? "" : "s"} up, +${formatCount(weekViews)} views.` : "";

  // The first clip of the post queue, shown large with its button (D215); the queue takes the same order.
  const hero = !firstRun ? toPost.find((c) => c.file_exists) : undefined;
  const nextIsChannel = !!next && next.label.startsWith("Your Short");
  const restReady = by("ready").filter((c) => c !== hero);
  // Nothing has synced yet (REDESIGN_NOTES 6): one line, not four boxes of dashes and zeros.
  const noNumbers = m.posts === 0 && !m.views && m.est_earnings === null && m.paid_usd == null;

  return (
    <div className="fade-in flex flex-col gap-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="cap text-[clamp(1.7rem,2.6vw,2.3rem)]">
            {!firstRun && toPost.length ? `${greeting()} ${toPost.length} clip${toPost.length === 1 ? "" : "s"} to post` : greeting()}
          </h1>
          <p className="mt-2 text-sm text-muted">
            {firstRun ? "Welcome to Clipper. A few steps and you're ready." : `${week ? `${week} ` : ""}Here's where your clips stand.`}
          </p>
        </div>
        {/* The one button: on the poster when there is a clip to post, up here for every other next step. */}
        {next && !hero && <Button variant="primary" size="md" onClick={next.run}>{next.label} <ArrowRight className="size-4" /></Button>}
      </div>
      {firstRun && <Setup done={home.first_run} />}
      <ChannelCard quiet={firstRun || !nextIsChannel} />
      {home.first_run.clips && <>
      {hero && next && <NextUp clip={hero} label={next.label} run={next.run} />}

      {(due.length > 0 || waitingViews.length > 0) && (
        <section className="flex flex-col gap-1">
          <h2 className="row-head mb-2">Next up</h2>
          {waitingViews.length > 0 && (
            <Link to="/clips" search={{ status: "posted" }} className="flex min-h-11 items-center gap-3 rounded-sm px-2 text-muted hover:bg-surface-1">
              <Inbox className="size-4" />
              <span className="flex-1 text-sm">{waitingViews.length} posted, waiting for enough views to submit</span>
              <ArrowRight className="size-4 text-subtle" />
            </Link>
          )}
          {due.slice(0, 3).map(({ post, task }) => (
            <div key={`${post.url}-${task.views}`} className="flex min-h-11 items-center gap-3 rounded-sm px-2 hover:bg-surface-1">
              <ListChecks className="size-4 shrink-0 text-warning" />
              <button type="button" className="min-w-0 flex-1 py-1.5 text-left text-sm" onClick={() => open(post.clip)}
                      title={task.task}>
                <span className="line-clamp-1 font-semibold">{post.clip_title}</span>
                <span className="line-clamp-1 text-xs text-muted">Passed {formatCount(task.views)} views: {task.task}</span>
              </button>
              <Button size="sm" variant="secondary" onClick={() => setTask.mutateAsync({ url: post.url, views: task.views, done: true })}>
                Done
              </Button>
            </div>
          ))}
          {due.length > 3 && <span className="px-2 text-xs text-subtle">+{due.length - 3} more brief tasks</span>}
        </section>
      )}
      {!hero && m.ready === 0 && by("posted").length === 0 && !waitingViews.length && !due.length && (
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <CheckCircle2 className="size-4 text-success" /> All caught up.
          <Link to={paid ? "/new" : "/create"} className="inline-flex min-h-8 items-center gap-1 font-semibold text-fg hover:underline">
            {paid ? "Make more clips" : "Make a Short"} <ArrowRight className="size-3.5" />
          </Link>
        </p>
      )}

      <div className="flex flex-col gap-8">
        {!(hero && restReady.length === 0) && (
          <WallRow title={hero ? "Also ready to post" : "Ready to post"} count={restReady.length} to="/clips" search={{ status: "ready" }}
                   empty={<EmptyLine status="ready" />}>
            {restReady.map((c) => <PosterTile key={c.id} clip={c} />)}
          </WallRow>
        )}
        {submits && <WallRow title="To submit" count={by("posted").length} to="/clips" search={{ status: "posted" }} empty={<EmptyLine status="posted" />}>
          {by("posted").map((c) => <PosterTile key={c.id} clip={c} />)}
        </WallRow>}
        {submits && <WallRow title="Submitted" count={by("submitted").length} to="/clips" search={{ status: "submitted" }} empty={<EmptyLine status="submitted" />}>
          {by("submitted").map((c) => <PosterTile key={c.id} clip={c} />)}
        </WallRow>}
      </div>

      <section className="flex flex-col gap-4 border-t border-line pt-6">
        {noNumbers ? (
          <p className="text-sm text-muted">{paid ? "Views and money" : "Views"} show here once your first posts have synced.</p>
        ) : <>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="row-head">Money and views</h2>
          <RangeToggle value={range} onChange={setRange} />
        </div>
        <Numbers est={earnedShown} paid={m.paid_usd} views={viewsShown} posts={m.posts} window={range}
                 locked={m.locked_usd ?? 0} lockedPosts={m.locked_posts ?? 0} medians={medians} />
        {since && (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-muted">
            <span className="font-semibold text-fg">Since you were last here</span>
            <Link to="/stats" search={{ sort: "views" }} className="hover:text-fg">
              <b className={cn("tabular", "text-fg")}>+{formatCount(since.views_gained)}</b> views since {ago(since.since)}
            </Link>
            <Link to="/stats" className="hover:text-fg"><b className="tabular text-fg">{since.new_posts}</b> new posts</Link>
            {since.top_mover ? (
              <button type="button" onClick={() => open(since.top_mover!.clip)} className="inline-flex min-w-0 items-center gap-1.5 hover:text-fg">
                <TrendingUp className="size-4 text-muted" /> Top mover: <span className="max-w-[24ch] truncate">{since.top_mover.clip_title}</span>
                <b className="tabular text-fg">+{formatCount(since.top_mover_gain)} views</b>
              </button>
            ) : <span>No movers yet</span>}
          </div>
        )}
        </>}
      </section>
      </>}
    </div>
  );
}

function EmptyLine({ status }: { status: "ready" | "posted" | "submitted" }) {
  const [empty, to, action] = EMPTY[status];
  return (
    <span className="flex flex-wrap items-center gap-2">
      {empty}
      {to && <Link to={to} className="inline-flex min-h-8 items-center gap-1 font-semibold text-fg hover:underline">{action} <ArrowRight className="size-3.5" /></Link>}
    </span>
  );
}
