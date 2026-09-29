import { useNavigate, useSearch } from "@tanstack/react-router";
import { CheckCircle2, Film, Inbox, PartyPopper, Send, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { useCampaigns, useClips, usePosts, useSetSubmitted, type Clip, type Post } from "@/api/client";
import { ClipGrid } from "@/components/ClipGrid";
import { DeleteButton, DownloadButton, LinkButtons, useStatusWithUndo } from "@/components/clips";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Button, Card, Chip, CopyButton, EmptyState, Kbd, PageHeader, Skeleton } from "@/components/ui";
import { useUI } from "@/lib/store";
import { PLATFORM_NAME, ago, cn, formatCount, formatDuration } from "@/lib/utils";

const FILTERS = [["all", "All"], ["ready", "Ready"], ["posted", "Posted"], ["submitted", "Submitted"], ["skipped", "Skipped"]] as const;
export type ClipFilter = (typeof FILTERS)[number][0];

function useActive() {
  const { data: campaigns = [] } = useCampaigns();
  return new Set(campaigns.filter((c) => !c.archived).map((c) => c.name));
}

/* ---------- Clips: the whole library ---------- */

export function ClipsPage() {
  const search = useSearch({ from: "/clips" });
  const navigate = useNavigate({ from: "/clips" });
  const { data: clips, isLoading } = useClips();
  const { data: campaigns = [] } = useCampaigns();
  const active = useActive();
  const status = search.status ?? "all";
  const campaign = search.campaign ?? "active";
  const list = (clips ?? []).filter((c) =>
    (campaign === "active" ? active.has(c.campaign) : campaign === "all" || c.campaign === campaign) &&
    (status === "all" || c.status === status));
  const count = (s: ClipFilter) => (clips ?? []).filter((c) =>
    (campaign === "active" ? active.has(c.campaign) : campaign === "all" || c.campaign === campaign) &&
    (s === "all" || c.status === s)).length;

  return (
    <div className="fade-in">
      <PageHeader title="Clips" subtitle={<>Every clip you've made. <Kbd>J</Kbd> <Kbd>K</Kbd> to move, <Kbd>Enter</Kbd> to open.</>}
        actions={
          <select value={campaign} aria-label="Campaign"
                  onChange={(e) => void navigate({ search: (s) => ({ ...s, campaign: e.target.value }) })}
                  className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm">
            <option value="active">Active campaigns</option>
            <option value="all">All, including archived</option>
            {campaigns.filter((c) => c.clips > 0).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        } />
      <div className="mb-5 flex flex-wrap gap-1.5" role="tablist">
        {FILTERS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={status === key}
                  onClick={() => void navigate({ search: (s) => ({ ...s, status: key }) })}
                  className={cn("h-8 rounded-full border px-3 text-sm font-medium transition-colors",
                    status === key ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
            {label} <span className="tabular ml-1 opacity-70">{count(key)}</span>
          </button>
        ))}
      </div>
      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="aspect-[9/19]" />)}
        </div>
      ) : list.length ? (
        <ClipGrid clips={list} showCampaign={campaign === "active" || campaign === "all"} />
      ) : (
        <EmptyState icon={<Film className="size-5" />} title="No clips here" body="Try another filter, or run clipper on new footage." />
      )}
    </div>
  );
}

/* ---------- Queue: ready to post ---------- */

function QueueRow({ clip }: { clip: Clip }) {
  const open = useUI((s) => s.setOpenClip);
  const setStatus = useStatusWithUndo();
  return (
    <Card className="flex items-center gap-4 p-3">
      <button onClick={() => open(clip.id)} className="shrink-0" aria-label={`Open ${clip.title}`}>
        <img src={clip.thumb} alt="" loading="lazy" className="aspect-[9/16] w-14 rounded-md bg-black object-cover" />
      </button>
      <div className="min-w-0 flex-1">
        <button onClick={() => open(clip.id)} className="block max-w-full truncate text-left text-sm font-semibold hover:underline">
          {clip.title}
        </button>
        <div className="mt-0.5 truncate text-xs text-muted">{clip.campaign} · {formatDuration(clip.duration_s)} · {clip.source_title}</div>
        {clip.notes && <div className="mt-1 line-clamp-1 text-xs text-warning">{clip.notes}</div>}
      </div>
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
        {clip.file_exists && <DownloadButton clip={clip} />}
        {clip.caption && <CopyButton text={clip.caption} what="Caption" label="Caption" />}
        <Button size="sm" variant="primary" onClick={() => setStatus(clip, "posted")}>
          <Send className="size-3.5" /> Mark posted
        </Button>
        <DeleteButton clip={clip} />
      </div>
    </Card>
  );
}

export function QueuePage() {
  const { data: clips, isLoading } = useClips();
  const active = useActive();
  const ready = (clips ?? []).filter((c) => c.status === "ready" && active.has(c.campaign));
  return (
    <div className="fade-in">
      <PageHeader title="Queue" subtitle="Clips ready to post, oldest first." />
      <Card className="mb-5 flex items-start gap-3 border-dashed p-4 text-sm text-muted">
        <Send className="mt-0.5 size-4 shrink-0 text-accent" />
        <p>
          Posting straight from Clipper is coming next. For now: <b className="text-fg">Download</b> saves the video to upload
          to TikTok or Instagram, <b className="text-fg">Caption</b> copies the caption, then <b className="text-fg">Mark posted</b>.
          Posts also switch to Posted by themselves on the next sync, once they're live with the same caption.
        </p>
      </Card>
      {isLoading ? <Skeleton className="h-64" /> : ready.length ? (
        <div className="flex flex-col gap-2">{ready.map((c) => <QueueRow key={c.id} clip={c} />)}</div>
      ) : (
        <EmptyState icon={<CheckCircle2 className="size-5 text-success" />} title="Nothing waiting to post"
          body="Every clip in your active campaigns is posted or skipped." />
      )}
    </div>
  );
}

/* ---------- Submissions: one link at a time ---------- */

function SubmissionRow({ post, thumb }: { post: Post; thumb?: string }) {
  const setSubmitted = useSetSubmitted();
  const open = useUI((s) => s.setOpenClip);
  const done = Boolean(post.submitted_at);
  const toggle = () => {
    setSubmitted.mutate({ url: post.url, submitted: !done });
    if (!done) {
      toast("Marked submitted", {
        description: post.clip_title, duration: 5000,
        action: { label: "Undo", onClick: () => setSubmitted.mutate({ url: post.url, submitted: false }) },
      });
    }
  };
  return (
    <div className={cn("flex items-center gap-3 rounded-md px-3 py-2.5 hover:bg-surface-2", done && "opacity-60")}>
      <button onClick={() => open(post.clip)} className="shrink-0" aria-label={`Open ${post.clip_title}`}>
        <img src={thumb} alt="" loading="lazy" className="aspect-[9/16] w-8 rounded-[4px] bg-black object-cover" />
      </button>
      <PlatformIcon platform={post.platform} className="text-muted" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{post.clip_title}</div>
        <div className="tabular truncate text-xs text-muted">
          {PLATFORM_NAME[post.platform] ?? post.platform} · {formatCount(post.views)} views · posted {ago(post.posted_at)}
        </div>
      </div>
      <CopyButton text={post.url} what={`${PLATFORM_NAME[post.platform] ?? post.platform} link`} label="Copy link" />
      <Button size="sm" variant={done ? "ghost" : "primary"} onClick={toggle}>
        {done ? <><Undo2 className="size-3.5" /> Unmark</> : <><CheckCircle2 className="size-3.5" /> Mark submitted</>}
      </Button>
    </div>
  );
}

export function SubmissionsPage() {
  const { data: posts, isLoading } = usePosts();
  const { data: clips = [] } = useClips();
  const active = useActive();
  const thumbs = new Map(clips.map((c) => [c.id, c.thumb]));
  const mine = (posts ?? []).filter((p) => active.has(p.campaign));
  const waiting = mine.filter((p) => !p.submitted_at);
  const done = mine.filter((p) => p.submitted_at);
  const byCampaign = new Map<string, Post[]>();
  waiting.forEach((p) => byCampaign.set(p.campaign, [...(byCampaign.get(p.campaign) ?? []), p]));

  return (
    <div className="fade-in">
      <PageHeader title="Submissions" subtitle="Copy each post's link into its campaign's submission form, then tick it off." />
      {isLoading ? <Skeleton className="h-64" /> : (
        <div className="flex flex-col gap-6">
          {waiting.length === 0 ? (
            <EmptyState icon={<PartyPopper className="size-5 text-money" />} title="All caught up"
              body={mine.length ? `All ${mine.length} posted links are submitted.` : "Links appear here once your posts are live."} />
          ) : [...byCampaign].map(([campaign, list]) => (
            <Card key={campaign} className="p-2">
              <div className="flex items-center justify-between px-3 py-2">
                <h2 className="text-sm font-semibold">{campaign}</h2>
                <Chip tone="warning">{list.length} to submit</Chip>
              </div>
              {list.map((p) => <SubmissionRow key={p.url} post={p} thumb={thumbs.get(p.clip)} />)}
            </Card>
          ))}
          {done.length > 0 && (
            <details className="group">
              <summary className="cursor-pointer text-sm text-muted hover:text-fg">
                <Inbox className="mr-1.5 inline size-4" />Submitted ({done.length})
              </summary>
              <Card className="mt-2 p-2">
                {done.map((p) => <SubmissionRow key={p.url} post={p} thumb={thumbs.get(p.clip)} />)}
              </Card>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export { LinkButtons };
