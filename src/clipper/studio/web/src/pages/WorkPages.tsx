import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Film, Info, PartyPopper } from "lucide-react";
import { useCampaigns, useClips, type Clip } from "@/api/client";
import { ClipGrid } from "@/components/ClipGrid";
import { Card, EmptyState, PageHeader, Skeleton, StatusLegend, buttonClass } from "@/components/ui";

// In the order a clip moves through them, opening on what's waiting to be posted; All last (D78).
const FILTERS = [["ready", "Ready to post"], ["posted", "To submit"], ["submitted", "Submitted"], ["skipped", "Skipped"], ["all", "All"]] as const;

const HINTS: Partial<Record<ClipFilter, React.ReactNode>> = {
  ready: <>Download a clip and copy its caption to post it. Once it's live, the next sync finds the post and moves the clip on by itself.</>,
  posted: <>Copy each post's link into the campaign's submission form, then press <b className="text-fg">Mark submitted</b>.</>,
};
export type ClipFilter = (typeof FILTERS)[number][0];

// A campaign that takes no links (your own channel) has nothing "to submit": its posted clips are under All.
export const inFilter = (c: Clip, f: ClipFilter) => f === "all" || (c.status === f && !(f === "posted" && c.submits === false));

/** The first filter with anything in it, in the order a clip moves through them. */
export const firstFilter = (clips: Clip[]): ClipFilter =>
  FILTERS.find(([key]) => clips.some((c) => inFilter(c, key)))?.[0] ?? "all";

/** The status tabs over a list of clips, each with its count. */
export function ClipFilters({ clips, value, onChange, hints = false }: {
  clips: Clip[]; value: ClipFilter; onChange: (f: ClipFilter) => void; hints?: boolean;
}) {
  const waiting = clips.filter((c) => inFilter(c, "posted") && c.submit_at_views).length;
  // The submit steps only where a clip's campaign takes links, and Skipped only once something is (D221): your own
  // channel and a first look had two tabs that could never fill.
  const submits = clips.some((c) => c.submits !== false);
  const shown = FILTERS.filter(([key]) => (submits || (key !== "posted" && key !== "submitted"))
                                          && (key !== "skipped" || value === "skipped" || clips.some((c) => c.status === "skipped")));
  return (
    <>
      <div className="tabs mb-6" role="tablist">
        {shown.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={value === key} onClick={() => onChange(key)} className="tab">
            {label} <span className="tabular ml-1 opacity-70">{clips.filter((c) => inFilter(c, key) && !(key === "posted" && c.submit_at_views)).length}</span>
            {/* Posts still short of the campaign's view minimum are listed here but can't be submitted yet:
                counted apart, as the Dashboard and the Post queue do (D161). */}
            {key === "posted" && waiting > 0 && <span className="tabular ml-1 text-xs opacity-70" title="Posted, waiting for enough views to submit">· {waiting} waiting</span>}
          </button>
        ))}
        <StatusLegend />
      </div>
      {hints && HINTS[value] && (
        <Card className="mb-5 flex items-start gap-2.5 border-dashed p-3.5 text-sm text-muted">
          <Info className="mt-0.5 size-4 shrink-0 text-info" /><p>{HINTS[value]}</p>
        </Card>
      )}
    </>
  );
}

const linkClass = buttonClass("primary");

/** What a filtered list shows when it's empty: what puts a clip here, and a link to do it (D143). */
export function NoClips({ filter, campaign }: { filter: ClipFilter; campaign?: string }) {
  const make = <Link to="/new" search={{ campaign }} className={linkClass}>Make clips</Link>;
  if (filter === "posted") {
    return <EmptyState icon={<PartyPopper className="size-5" />} title="Nothing to submit" body="A posted clip waits here until you submit its link to its campaign."
      action={<Link to="/clips" search={{ status: "ready" }} className={linkClass}>See what's ready to post</Link>} />;
  }
  if (filter === "ready") {
    return <EmptyState icon={<Film className="size-5" />} title="Nothing waiting to be posted"
      body="Clips you haven't posted yet land here. Make more from a campaign's footage, or write a Short for your own channel."
      action={<span className="flex flex-wrap justify-center gap-2">{make}<Link to="/create" className={buttonClass("secondary")}>Make a Short</Link></span>} />;
  }
  if (filter === "submitted") {
    return <EmptyState icon={<Film className="size-5" />} title="Nothing submitted yet"
      body="Once you've pasted a post's link into its campaign, mark the clip submitted and it moves here."
      action={<Link to="/clips" search={{ status: "posted" }} className={linkClass}>See links to submit</Link>} />;
  }
  if (filter === "skipped") {
    return <EmptyState icon={<Film className="size-5" />} title="Nothing skipped"
      body="Clips you skip or mark not good are kept here, and Clipper learns from them." />;
  }
  return <EmptyState icon={<Film className="size-5" />} title="No clips yet" body="Give Clipper a campaign's footage and the clips land here." action={make} />;
}

function useActive() {
  const { data: campaigns = [] } = useCampaigns();
  return new Set(campaigns.filter((c) => !c.archived).map((c) => c.name));
}

/* ---------- Clips: the whole library ---------- */

export function ClipsPage() {
  const search = useSearch({ from: "/clips" });
  const navigate = useNavigate({ from: "/clips" });
  const { data: clips, isLoading: clipsLoading } = useClips();
  const { data: campaigns = [], isLoading: campaignsLoading } = useCampaigns();
  // Both, or "active campaigns" is empty for a moment and every tab reads 0 over a blank page (D198).
  const isLoading = clipsLoading || campaignsLoading;
  const active = useActive();
  const campaign = search.campaign ?? "active";
  const mine = (clips ?? []).filter((c) =>
    campaign === "active" ? active.has(c.campaign) : campaign === "all" || c.campaign === campaign);
  // Ready when it has anything (D78), else the first tab that does (D196): it opened on an empty Ready with 45 clips
  // in the other tabs.
  const status = search.status ?? (mine.some((c) => inFilter(c, "ready")) ? "ready" : firstFilter(mine));
  // From Learning's "Rate them" (D154): every clip with no thumbs up or down yet, whatever its status.
  const list = search.rate ? (clips ?? []).filter((c) => c.rating == null) : mine.filter((c) => inFilter(c, status));

  return (
    <div className="fade-in">
      <PageHeader title="Clips" subtitle="Every clip you've made."
        actions={<>
          {(status === "ready" || status === "posted") && list.length > 0 && (
            <Link to="/post" search={{ step: status === "ready" ? "post" : "submit", campaign: campaign !== "active" && campaign !== "all" ? campaign : undefined }}
                  className={linkClass}>Post one by one</Link>
          )}
          <select value={campaign} aria-label="Campaign"
                  onChange={(e) => void navigate({ search: (s) => ({ ...s, campaign: e.target.value }) })}
                  className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm">
            <option value="active">Active campaigns</option>
            <option value="all">All, including archived</option>
            {campaigns.filter((c) => c.clips > 0).map((c) => <option key={c.name} value={c.name}>{c.title}</option>)}
          </select>
        </>} />
      {search.rate ? (
        <Card className="mb-5 flex flex-wrap items-center gap-2.5 border-dashed p-3.5 text-sm text-muted">
          <Info className="size-4 shrink-0 text-muted" />
          <p className="flex-1">{list.length} clip{list.length === 1 ? "" : "s"} with no rating. Thumbs up or down on each teaches Clipper your taste.</p>
          <Link to="/clips" search={{ status }} className="font-medium link">Back to all clips</Link>
        </Card>
      ) : (
        <ClipFilters clips={mine} value={status} hints
                     onChange={(key) => void navigate({ search: (s) => ({ ...s, status: key }) })} />
      )}
      {isLoading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(200px,1fr))] sm:gap-4">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="aspect-[9/19]" />)}
        </div>
      ) : list.length ? (
        <ClipGrid clips={list} showCampaign={campaign === "active" || campaign === "all"} />
      ) : <NoClips filter={status} campaign={campaign !== "active" && campaign !== "all" ? campaign : undefined} />}
    </div>
  );
}
