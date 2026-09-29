import { ArrowDown, ArrowUp, BarChart3 } from "lucide-react";
import { useMemo, useState } from "react";
import { useCampaigns, useClips, usePosts, type Post } from "@/api/client";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Chip, CopyButton, EmptyState, Metric, PageHeader, Skeleton, Tip } from "@/components/ui";
import { useUI } from "@/lib/store";
import { PLATFORM_NAME, ago, cn, formatCount, formatMoney } from "@/lib/utils";

type Key = "posted" | "views" | "x" | "watch" | "skip" | "likes" | "shares" | "saves" | "money";

const COLUMNS: { key: Key; label: string; get: (p: Post) => number | null | undefined; tip?: string }[] = [
  { key: "views", label: "Views", get: (p) => p.views },
  { key: "x", label: "vs median", get: (p) => p.x_median, tip: "Views compared with your median for this platform and campaign" },
  { key: "watch", label: "Avg watch", get: (p) => p.avg_watch_s },
  { key: "skip", label: "Skip 3s", get: (p) => p.skip_rate_pct, tip: "Share of viewers who swiped away in the first 3 seconds (Instagram)" },
  { key: "likes", label: "Likes", get: (p) => p.likes },
  { key: "shares", label: "Shares", get: (p) => p.shares },
  { key: "saves", label: "Saves", get: (p) => p.saves },
  { key: "money", label: "Est. $", get: (p) => p.est_earnings, tip: "Views ÷ 1,000 × the campaign's rate" },
];

function cell(p: Post, key: Key): React.ReactNode {
  const tiktok = "TikTok's API doesn't provide this";
  const na = (why: string) => <Tip label={why}><span className="text-subtle">n/a</span></Tip>;
  switch (key) {
    case "views": return formatCount(p.views);
    case "x": return p.x_median != null
      ? <span className={cn(p.x_median >= 2 ? "font-semibold text-money" : p.x_median < 0.5 ? "text-subtle" : "")}>{p.x_median}×</span>
      : na("Needs 3+ posts on this platform in this campaign");
    case "watch": return p.avg_watch_s != null ? `${p.avg_watch_s}s` : na(p.platform === "tiktok" ? tiktok : "Not reported yet");
    case "skip": return p.skip_rate_pct != null ? `${p.skip_rate_pct}%` : na(p.platform === "tiktok" ? tiktok : "Not reported yet");
    case "likes": return formatCount(p.likes);
    case "shares": return formatCount(p.shares);
    case "saves": return p.saves != null ? formatCount(p.saves) : na(tiktok);
    case "money": return p.est_earnings != null ? <span className="text-money">{formatMoney(p.est_earnings)}</span> : na("No pay rate set for this campaign");
    default: return null;
  }
}

export function PostTable({ posts }: { posts: Post[] }) {
  const open = useUI((s) => s.setOpenClip);
  const { data: clips = [] } = useClips();
  const thumbs = useMemo(() => new Map(clips.map((c) => [c.id, c.thumb])), [clips]);
  const [sort, setSort] = useState<{ key: Key; desc: boolean }>({ key: "posted", desc: true });

  const sorted = useMemo(() => {
    const get = sort.key === "posted" ? (p: Post) => p.posted_at ?? ""
      : COLUMNS.find((c) => c.key === sort.key)!.get;
    return [...posts].sort((a, b) => {
      const x = get(a) ?? -1, y = get(b) ?? -1;
      return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1);
    });
  }, [posts, sort]);

  if (!posts.length) {
    return <EmptyState icon={<BarChart3 className="size-5" />} title="No posts yet"
      body="Post a clip with its caption and the next sync picks it up, with its link and numbers." />;
  }
  const header = (key: Key, label: string, tip?: string) => {
    const th = (
      <button onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : true }))}
              className="inline-flex items-center gap-1 whitespace-nowrap hover:text-fg">
        {label}
        {sort.key === key && (sort.desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
      </button>
    );
    return tip ? <Tip label={tip}>{th}</Tip> : th;
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface-1">
      <table className="tabular w-full text-sm">
        <thead className="border-b border-line text-left text-xs text-muted">
          <tr className="h-9">
            <th className="px-3 font-medium">Post</th>
            <th className="px-3 font-medium">{header("posted", "Posted")}</th>
            {COLUMNS.map((c) => <th key={c.key} className="px-3 text-right font-medium">{header(c.key, c.label, c.tip)}</th>)}
            <th className="px-3 font-medium"><span className="sr-only">Link</span></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => (
            <tr key={p.url} className="h-12 border-b border-line last:border-0 hover:bg-surface-2">
              <td className="px-3">
                <button onClick={() => open(p.clip)} className="flex items-center gap-2.5 text-left" title={p.clip_title}>
                  <img src={thumbs.get(p.clip)} alt="" loading="lazy" className="aspect-[9/16] w-6 shrink-0 rounded-[3px] bg-black object-cover" />
                  <PlatformIcon platform={p.platform} className="text-muted" />
                  <span className="block w-[240px] truncate xl:w-[300px]">{p.clip_title}</span>
                </button>
              </td>
              <td className="px-3 whitespace-nowrap text-muted">
                {ago(p.posted_at)}
                {p.settling && <Tip label="Instagram's numbers can arrive up to 48 hours late"><span className="ml-1.5"><Chip tone="warning">settling</Chip></span></Tip>}
              </td>
              {COLUMNS.map((c) => <td key={c.key} className="px-3 text-right whitespace-nowrap">{cell(p, c.key)}</td>)}
              <td className="px-3 text-right">
                <CopyButton text={p.url} what={`${PLATFORM_NAME[p.platform] ?? p.platform} link`} variant="ghost" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StatsPage() {
  const { data: posts, isLoading } = usePosts();
  const { data: campaigns = [] } = useCampaigns();
  const [campaign, setCampaign] = useState("all");
  const list = (posts ?? []).filter((p) => campaign === "all" || p.campaign === campaign);
  const sum = (f: (p: Post) => number | null | undefined) => list.reduce((s, p) => s + (f(p) ?? 0), 0);
  const withWatch = list.filter((p) => p.avg_watch_s != null);
  const withSkip = list.filter((p) => p.skip_rate_pct != null);
  const money = list.filter((p) => p.est_earnings != null);
  const views = list.map((p) => p.views ?? 0).sort((a, b) => a - b);
  const median = views.length ? views[Math.floor(views.length / 2)] : null;

  return (
    <div className="fade-in">
      <PageHeader title="Stats" subtitle="Every post's numbers, refreshed on each sync."
        actions={
          <select value={campaign} onChange={(e) => setCampaign(e.target.value)}
                  className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm" aria-label="Campaign">
            <option value="all">All campaigns</option>
            {campaigns.filter((c) => c.clips > 0).map((c) => <option key={c.name} value={c.name}>{c.title}</option>)}
          </select>
        } />
      {isLoading ? <Skeleton className="h-80" /> : (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <Metric label="Est. earnings" tone="money" value={money.length ? formatMoney(sum((p) => p.est_earnings)) : "–"} />
            <Metric label="Views" value={formatCount(sum((p) => p.views))} sub={`${list.length} posts`} />
            <Metric label="Median views" value={formatCount(median)} />
            <Metric label="Avg watch time" value={withWatch.length ? `${(withWatch.reduce((s, p) => s + (p.avg_watch_s ?? 0), 0) / withWatch.length).toFixed(1)}s` : "–"}
                    sub={withWatch.length ? `from ${withWatch.length} posts that report it` : "TikTok's API doesn't report it"} />
            <Metric label="Skipped in 3s" value={withSkip.length ? `${Math.round(withSkip.reduce((s, p) => s + (p.skip_rate_pct ?? 0), 0) / withSkip.length)}%` : "–"}
                    sub="lower is better" />
          </div>
          <PostTable posts={list} />
        </div>
      )}
    </div>
  );
}
