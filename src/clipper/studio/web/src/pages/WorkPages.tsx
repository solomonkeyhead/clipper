import { useNavigate, useSearch } from "@tanstack/react-router";
import { Film, Info, PartyPopper } from "lucide-react";
import { useCampaigns, useClips } from "@/api/client";
import { ClipGrid } from "@/components/ClipGrid";
import { Card, EmptyState, Kbd, PageHeader, Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";

// In the order a clip moves through them, opening on what's waiting to be posted; All last (D78).
const FILTERS = [["ready", "Ready to post"], ["posted", "To submit"], ["submitted", "Submitted"], ["skipped", "Skipped"], ["all", "All"]] as const;

const HINTS: Partial<Record<ClipFilter, React.ReactNode>> = {
  ready: <>Download a clip and copy its caption to post it. Once it's live, the next sync finds it and moves it to <b className="text-fg">To submit</b>.</>,
  posted: <>Copy each post's link into the campaign's submission form, then press <b className="text-fg">Mark submitted</b>.</>,
};
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
  const status = search.status ?? "ready";
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
            {campaigns.filter((c) => c.clips > 0).map((c) => <option key={c.name} value={c.name}>{c.title}</option>)}
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
      {HINTS[status] && (
        <Card className="mb-5 flex items-start gap-2.5 border-dashed p-3.5 text-sm text-muted">
          <Info className="mt-0.5 size-4 shrink-0 text-accent" /><p>{HINTS[status]}</p>
        </Card>
      )}
      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="aspect-[9/19]" />)}
        </div>
      ) : list.length ? (
        <ClipGrid clips={list} showCampaign={campaign === "active" || campaign === "all"} />
      ) : status === "posted" ? (
        <EmptyState icon={<PartyPopper className="size-5 text-money" />} title="All caught up" body="Every posted clip is submitted." />
      ) : (
        <EmptyState icon={<Film className="size-5" />} title="No clips here" body="Try another filter, or make clips on the New clips page." />
      )}
    </div>
  );
}

