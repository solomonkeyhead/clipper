import { useNavigate, useSearch } from "@tanstack/react-router";
import { Film, Info, PartyPopper } from "lucide-react";
import { useCampaigns, useClips, type Clip } from "@/api/client";
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

export const inFilter = (c: Clip, f: ClipFilter) => f === "all" || c.status === f;

/** The first filter with anything in it, in the order a clip moves through them. */
export const firstFilter = (clips: Clip[]): ClipFilter =>
  FILTERS.find(([key]) => clips.some((c) => inFilter(c, key)))?.[0] ?? "all";

/** The status tabs over a list of clips, each with its count. */
export function ClipFilters({ clips, value, onChange, hints = false }: {
  clips: Clip[]; value: ClipFilter; onChange: (f: ClipFilter) => void; hints?: boolean;
}) {
  return (
    <>
      <div className="mb-5 flex flex-wrap gap-1.5" role="tablist">
        {FILTERS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={value === key} onClick={() => onChange(key)}
                  className={cn("h-8 rounded-full border px-3 text-sm font-medium transition-colors",
                    value === key ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
            {label} <span className="tabular ml-1 opacity-70">{clips.filter((c) => inFilter(c, key)).length}</span>
          </button>
        ))}
      </div>
      {hints && HINTS[value] && (
        <Card className="mb-5 flex items-start gap-2.5 border-dashed p-3.5 text-sm text-muted">
          <Info className="mt-0.5 size-4 shrink-0 text-accent" /><p>{HINTS[value]}</p>
        </Card>
      )}
    </>
  );
}

/** What a filtered list shows when it's empty. */
export function NoClips({ filter }: { filter: ClipFilter }) {
  return filter === "posted"
    ? <EmptyState icon={<PartyPopper className="size-5 text-money" />} title="All caught up" body="Every posted clip is submitted." />
    : <EmptyState icon={<Film className="size-5" />} title="No clips here" body="Try another filter, or make clips on the New clips page." />;
}

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
  const mine = (clips ?? []).filter((c) =>
    campaign === "active" ? active.has(c.campaign) : campaign === "all" || c.campaign === campaign);
  const list = mine.filter((c) => inFilter(c, status));

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
      <ClipFilters clips={mine} value={status} hints
                   onChange={(key) => void navigate({ search: (s) => ({ ...s, status: key }) })} />
      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="aspect-[9/19]" />)}
        </div>
      ) : list.length ? (
        <ClipGrid clips={list} showCampaign={campaign === "active" || campaign === "all"} />
      ) : <NoClips filter={status} />}
    </div>
  );
}
