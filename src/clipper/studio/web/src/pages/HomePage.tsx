import { Link } from "@tanstack/react-router";
import { ArrowRight, CheckCircle2, Inbox, Send, Sparkles, TrendingUp } from "lucide-react";
import { useCampaigns, useClips, useHome, type Clip } from "@/api/client";
import { Card, Metric, PageHeader, Skeleton } from "@/components/ui";
import { useUI } from "@/lib/store";
import { ago, cn, formatCount, formatMoney } from "@/lib/utils";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function PipelineColumn({ title, clips, tone }: { title: string; clips: Clip[]; tone: string }) {
  const open = useUI((s) => s.setOpenClip);
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-lg bg-surface-1 p-3">
      <div className="flex items-center justify-between px-1">
        <span className="flex items-center gap-2 text-sm font-medium">
          <span className={cn("size-2 rounded-full", tone)} /> {title}
        </span>
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
        {clips.length > 5 && <span className="px-1.5 text-xs text-subtle">+{clips.length - 5} more</span>}
        {clips.length === 0 && <span className="px-1.5 py-2 text-xs text-subtle">Nothing here</span>}
      </div>
    </div>
  );
}

const STEPS: { key: string; title: string; body: string; to: string; action: string }[] = [
  { key: "ai", title: "Add your free AI key", body: "Clipper uses it to find the best moments and write captions. Takes a minute.",
    to: "/settings", action: "Add key" },
  { key: "campaign", title: "Add a campaign", body: "Paste the brief of a campaign you've joined; Clipper fills in its rules.",
    to: "/campaigns/new", action: "Add campaign" },
  { key: "clips", title: "Make your first clips", body: "Drop in the campaign's footage and Clipper cuts, frames and captions it.",
    to: "/new", action: "Make clips" },
  { key: "accounts", title: "Connect TikTok or Instagram", body: "So Clipper can track views, earnings and your posts' links.",
    to: "/accounts", action: "Connect" },
];

function GetStarted({ done }: { done: Record<string, boolean> }) {
  const next = STEPS.find((s) => !done[s.key]);
  const count = STEPS.filter((s) => done[s.key]).length;
  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-md font-semibold">Get started</h2>
        <span className="tabular text-xs text-muted">{count} of {STEPS.length} done</span>
      </div>
      <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {STEPS.map((step, i) => {
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
  const { data: campaigns = [] } = useCampaigns();
  const archived = new Set(campaigns.filter((c) => c.archived).map((c) => c.name));

  if (!home) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-24" />)}</div>
        <Skeleton className="h-40" />
      </div>
    );
  }
  const m = home.metrics;
  const since = home.since;
  const firstRun = Object.values(home.first_run).some((done) => !done);
  const active = clips.filter((c) => c.status !== "skipped" && !archived.has(c.campaign));
  const by = (s: string) => active.filter((c) => c.status === s);

  return (
    <div className="fade-in flex flex-col gap-8">
      <PageHeader title={greeting()} subtitle={firstRun ? "Welcome to Clipper. Four steps and you're clipping." : "Here's where your clips stand."} />
      {firstRun && <GetStarted done={home.first_run} />}
      {home.first_run.clips && <>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Metric label="Est. earnings" tone="money" value={formatMoney(m.est_earnings)}
                hint="Estimate: views ÷ 1,000 × each campaign's rate. Campaigns verify views themselves."
                sub={m.est_earnings === null ? "Add a campaign's pay rate" : "active campaigns"} />
        <Metric label="Views" value={formatCount(m.views)} sub="active campaigns" />
        <Metric label="Posts" value={m.posts} sub={`${m.ready} clips ready to post`} />
        <Metric label="Median views / post" value={formatCount(m.median_views)} />
        <Metric label="To submit" value={m.to_submit} sub={m.to_submit ? "links waiting" : "all caught up"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="flex flex-col gap-4 p-5 lg:col-span-2">
          <h2 className="flex items-center gap-2 text-md font-semibold">
            <Sparkles className="size-4 text-accent" /> Since you were last here
          </h2>
          {since ? (
            <div className="grid gap-4 sm:grid-cols-3">
              <div>
                <div className="tabular text-2xl font-semibold text-money">+{formatCount(since.views_gained)}</div>
                <div className="text-xs text-muted">views since {ago(since.since)}</div>
              </div>
              <div>
                <div className="tabular text-2xl font-semibold">{since.new_posts}</div>
                <div className="text-xs text-muted">new posts</div>
              </div>
              <div className="min-w-0">
                {since.top_mover ? (
                  <>
                    <div className="flex items-center gap-1.5 text-sm font-medium">
                      <TrendingUp className="size-4 text-money" /> Top mover
                    </div>
                    <div className="truncate text-xs text-muted">{since.top_mover.clip_title}</div>
                    <div className="tabular text-xs text-money">+{formatCount(since.top_mover_gain)} views</div>
                  </>
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
          {m.to_submit > 0 && (
            <Link to="/clips" search={{ status: "posted" }} className="flex items-center gap-3 rounded-md p-2 hover:bg-surface-2">
              <Inbox className="size-4 text-warning" />
              <span className="flex-1 text-sm">{m.to_submit} link{m.to_submit === 1 ? "" : "s"} to submit</span>
              <ArrowRight className="size-4 text-subtle" />
            </Link>
          )}
          {m.ready === 0 && m.to_submit === 0 && (
            <p className="flex items-center gap-2 p-2 text-sm text-muted">
              <CheckCircle2 className="size-4 text-success" /> All caught up.
            </p>
          )}
        </Card>
      </div>

      <section>
        <h2 className="mb-3 text-md font-semibold">Pipeline</h2>
        <div className="grid gap-3 md:grid-cols-3">
          <PipelineColumn title="Ready to post" clips={by("ready")} tone="bg-info" />
          <PipelineColumn title="Posted" clips={by("posted")} tone="bg-warning" />
          <PipelineColumn title="Submitted" clips={by("submitted")} tone="bg-success" />
        </div>
      </section>
      </>}
    </div>
  );
}
