import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, BarChart3, ChevronRight } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { keys, setStayed, useCampaignTitle, useCampaigns, useClips, usePosts, type Post } from "@/api/client";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Chip, CopyButton, EmptyState, Metric, PageHeader, Skeleton, Tip } from "@/components/ui";
import { useUI } from "@/lib/store";
import { PLATFORM_NAME, ago, cn, formatCount, formatMoney } from "@/lib/utils";

type Key = "posted" | "views" | "x" | "watch" | "pct" | "stayed" | "skip" | "likes" | "shares" | "saves" | "money";

const COLUMNS: { key: Key; label: string; get: (p: Post) => number | null | undefined; tip?: string }[] = [
  { key: "views", label: "Views", get: (p) => p.views },
  { key: "x", label: "vs median", get: (p) => p.x_median, tip: "Views compared with your median for this platform and campaign" },
  { key: "watch", label: "Avg watch", get: (p) => p.avg_watch_s },
  { key: "pct", label: "% viewed", get: (p) => p.avg_view_pct, tip: "The average share of the video watched (YouTube). Over 100% means people rewatched it" },
  { key: "stayed", label: "Stayed", get: (p) => p.stayed_pct, tip: "YouTube Studio's \"viewed vs swiped away\" for a Short, typed in: no API gives it. The steadiest number for a small channel" },
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
      : na("Needs 3+ posts on this platform in this campaign, with a median of at least 10 views");
    case "watch": return p.avg_watch_s != null ? `${p.avg_watch_s}s` : na(p.platform === "tiktok" ? tiktok : "Not reported yet");
    case "pct": return p.avg_view_pct != null ? `${p.avg_view_pct}%` : na(p.platform === "youtube" ? "Not reported yet" : "Only YouTube reports this");
    case "stayed": return p.platform === "youtube" ? <StayedCell post={p} /> : p.stayed_pct != null ? `${p.stayed_pct}%` : <span className="text-subtle">–</span>;
    case "skip": return p.skip_rate_pct != null ? `${p.skip_rate_pct}%` : na(p.platform === "tiktok" ? tiktok : p.platform === "" ? "Instagram reports this; not in yet" : "Not reported yet");
    case "likes": return formatCount(p.likes);
    case "shares": return formatCount(p.shares);
    case "saves": return p.saves != null ? formatCount(p.saves) : na(p.platform === "tiktok" ? tiktok : "Not reported");
    case "money": return p.est_earnings != null ? <span className="text-money">{formatMoney(p.est_earnings)}</span> : na("No pay rate set for this campaign");
    default: return null;
  }
}

/** The "viewed vs swiped away" box for one YouTube Short (D156): type the percent from YouTube Studio, Enter or leave to save. */
function StayedCell({ post }: { post: Post }) {
  const qc = useQueryClient();
  const save = (raw: string) => {
    const pct = raw.trim() === "" ? null : Number(raw.replace("%", ""));
    if (pct !== null && (Number.isNaN(pct) || pct < 0 || pct > 100)) return toast.error("A percent from 0 to 100");
    if (pct === (post.stayed_pct ?? null)) return;
    void setStayed(post.url, pct).then(() => qc.invalidateQueries({ queryKey: keys.posts }), (e: Error) => toast.error(e.message));
  };
  return (
    <input key={post.stayed_pct ?? "none"} defaultValue={post.stayed_pct ?? ""} placeholder="type %" aria-label="Viewed vs swiped away"
           onClick={(e) => e.stopPropagation()} onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") e.currentTarget.blur(); }}
           onBlur={(e) => save(e.target.value)}
           className="h-6 w-14 rounded-sm border border-line bg-surface-2 px-1 text-right text-xs tabular-nums placeholder:text-subtle focus:border-accent focus:outline-none" />
  );
}

/** A clip's posts as one row (D154): totals where they add up, the best ratio, averages for watch and skip. */
function together(posts: Post[]): Post {
  const known = (f: (p: Post) => number | null | undefined) => posts.map(f).filter((v): v is number => v != null);
  const total = (f: (p: Post) => number | null | undefined) => { const v = known(f); return v.length ? v.reduce((a, b) => a + b, 0) : null; };
  const mean = (f: (p: Post) => number | null | undefined) => { const v = known(f); return v.length ? Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 10) / 10 : null; };
  const best = known((p) => p.x_median);
  return {
    ...posts[0],
    platform: posts.length === 1 ? posts[0].platform : "",
    posted_at: posts.map((p) => p.posted_at ?? "").filter(Boolean).sort()[0] ?? posts[0].posted_at,
    settling: posts.some((p) => p.settling),
    views: total((p) => p.views), x_median: best.length ? Math.max(...best) : null,
    avg_watch_s: mean((p) => p.avg_watch_s), avg_view_pct: mean((p) => p.avg_view_pct), stayed_pct: mean((p) => p.stayed_pct), skip_rate_pct: mean((p) => p.skip_rate_pct),
    likes: total((p) => p.likes), shares: total((p) => p.shares), saves: total((p) => p.saves),
    est_earnings: total((p) => p.est_earnings),
  };
}

export function PostTable({ posts, initialSort, showCampaign = false }: { posts: Post[]; initialSort?: string; showCampaign?: boolean }) {
  const open = useUI((s) => s.setOpenClip);
  const { data: clips = [] } = useClips();
  const campaignTitle = useCampaignTitle();
  const thumbs = useMemo(() => new Map(clips.map((c) => [c.id, c.thumb])), [clips]);
  const start = (COLUMNS.some((c) => c.key === initialSort) ? initialSort : "posted") as Key;
  const [sort, setSort] = useState<{ key: Key; desc: boolean }>({ key: start, desc: true });
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // One row per clip, its platforms folded under it: a clip on three platforms was three rows (D154).
  const groups = useMemo(() => {
    const by = new Map<number, Post[]>();
    for (const p of posts) by.set(p.clip, [...(by.get(p.clip) ?? []), p]);
    const get = sort.key === "posted" ? (p: Post) => p.posted_at ?? ""
      : COLUMNS.find((c) => c.key === sort.key)!.get;
    return [...by.values()].map((list) => ({ row: together(list), posts: list })).sort((a, b) => {
      const x = get(a.row) ?? -1, y = get(b.row) ?? -1;
      return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1);
    });
  }, [posts, sort]);

  if (!posts.length) {
    return <EmptyState icon={<BarChart3 className="size-5" />} title="No posts yet"
      body="Post a clip with its caption and the next sync picks it up, with its link and numbers."
      action={<Link to="/clips" search={{ status: "ready" }} className="inline-flex h-9 items-center rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">See clips ready to post</Link>} />;
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
  const toggle = (clip: number) => setExpanded((s) => {
    const n = new Set(s);
    if (n.has(clip)) n.delete(clip); else n.add(clip);
    return n;
  });
  const platformLink = (p: Post) => (
    <Tip label={`${PLATFORM_NAME[p.platform] ?? p.platform}${p.account ? ` @${p.account}` : ""}: open the post`}>
      <a href={p.url} target="_blank" rel="noopener noreferrer" className="text-muted hover:text-fg"><PlatformIcon platform={p.platform} /></a>
    </Tip>
  );

  return (
    // `relative`: the header's screen-reader label is positioned, and without it
    // escaped the scroll box and widened the whole page on a phone.
    <div className="relative overflow-x-auto rounded-lg border border-line bg-surface-1">
      <table className="tabular w-full text-sm">
        <thead className="border-b border-line text-left text-xs text-muted">
          <tr className="h-9">
            <th className="px-2 font-medium">Clip</th>
            <th className="px-2 font-medium">{header("posted", "Posted")}</th>
            {COLUMNS.map((c) => <th key={c.key} className="px-2 text-right font-medium">{header(c.key, c.label, c.tip)}</th>)}
            <th className="px-2 font-medium"><span className="sr-only">Link</span></th>
          </tr>
        </thead>
        <tbody>
          {groups.map(({ row, posts: list }) => {
            const many = list.length > 1;
            const isOpen = expanded.has(row.clip);
            return (
              <Fragment key={row.clip}>
                <tr className="h-12 border-b border-line last:border-0 hover:bg-surface-2">
                  <td className="px-2">
                    <div className="flex items-center gap-2">
                      <button onClick={() => open(row.clip)} className="flex min-w-0 items-center gap-2 text-left" title={row.clip_title}>
                        <img src={thumbs.get(row.clip)} alt="" loading="lazy" className="aspect-[9/16] w-6 shrink-0 rounded-[3px] bg-black object-cover" />
                        <span className="block w-[180px] truncate 2xl:w-[280px]">{row.clip_title}</span>
                      </button>
                      {many ? (
                        <button type="button" onClick={() => toggle(row.clip)} aria-expanded={isOpen}
                                className="flex items-center gap-1 rounded-sm px-1 text-muted hover:bg-surface-3 hover:text-fg" title="Each platform's numbers">
                          {list.map((p) => <PlatformIcon key={p.url} platform={p.platform} className="size-3.5" />)}
                          <ChevronRight className={cn("size-3.5 transition-transform", isOpen && "rotate-90")} />
                        </button>
                      ) : platformLink(list[0])}
                      {showCampaign && (
                        <Link to="/campaigns/$name" params={{ name: row.campaign }} className="hidden max-w-28 truncate text-xs text-muted hover:text-accent 2xl:block">
                          {campaignTitle(row.campaign)}
                        </Link>
                      )}
                    </div>
                  </td>
                  <td className="px-2 whitespace-nowrap text-muted">
                    {ago(row.posted_at)}
                    {row.settling && <Tip label="Instagram's and YouTube's numbers can arrive up to 48 hours late"><span className="ml-1.5"><Chip tone="warning">settling</Chip></span></Tip>}
                  </td>
                  {COLUMNS.map((c) => {
                    // The box to type YouTube's "stayed" in, on the clip's own row too: it hid in the folded rows (D161).
                    const yt = c.key === "stayed" && many ? list.find((p) => p.platform === "youtube") : undefined;
                    return <td key={c.key} className="px-2 text-right whitespace-nowrap">{yt ? <StayedCell post={yt} /> : cell(row, c.key)}</td>;
                  })}
                  <td className="px-2 text-right">
                    {!many && <CopyButton text={row.url} what={`${PLATFORM_NAME[row.platform] ?? row.platform} link`} variant="ghost" />}
                  </td>
                </tr>
                {many && isOpen && list.map((p) => (
                  <tr key={p.url} className="h-10 border-b border-line bg-surface-2/40 text-xs">
                    <td className="py-1 pr-2 pl-10">
                      <span className="flex items-center gap-2 text-muted">
                        {platformLink(p)} {PLATFORM_NAME[p.platform] ?? p.platform}{p.account ? ` @${p.account}` : ""}
                      </span>
                    </td>
                    <td className="px-2 whitespace-nowrap text-muted">{ago(p.posted_at)}</td>
                    {COLUMNS.map((c) => <td key={c.key} className="px-2 text-right whitespace-nowrap">{cell(p, c.key)}</td>)}
                    <td className="px-2 text-right">
                      <CopyButton text={p.url} what={`${PLATFORM_NAME[p.platform] ?? p.platform} link`} variant="ghost" />
                    </td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Each platform's posts, views and median side by side: where views come from, and where they don't. */
function ByPlatform({ posts }: { posts: Post[] }) {
  const groups = new Map<string, Post[]>();
  for (const p of posts) groups.set(p.platform, [...(groups.get(p.platform) ?? []), p]);
  if (groups.size < 2) return null;
  const rows = [...groups.entries()].map(([platform, list]) => {
    const views = list.map((p) => p.views ?? 0).sort((a, b) => a - b);
    return { platform, posts: list.length, total: views.reduce((s, v) => s + v, 0),
             median: views[Math.floor(views.length / 2)], zero: views.filter((v) => v === 0).length };
  }).sort((a, b) => b.total - a.total);
  const top = Math.max(1, ...rows.map((r) => r.total));
  return (
    <div className="rounded-lg border border-line bg-surface-1 p-4">
      <h2 className="mb-3 text-sm font-semibold">By platform</h2>
      <div className="flex flex-col gap-2.5">
        {rows.map((r) => (
          <div key={r.platform} className="grid grid-cols-[120px_1fr_auto] items-center gap-3 text-sm">
            <Link to="/accounts" className="flex items-center gap-2 hover:text-accent" title="Open Accounts">
              <PlatformIcon platform={r.platform} />{PLATFORM_NAME[r.platform] ?? r.platform}
            </Link>
            <span className="h-2 overflow-hidden rounded-full bg-surface-3">
              <span className="block h-full rounded-full bg-accent" style={{ width: `${(r.total / top) * 100}%` }} />
            </span>
            <span className="tabular text-right text-xs text-muted">
              <b className="text-fg">{formatCount(r.total)}</b> views · {r.posts} post{r.posts === 1 ? "" : "s"} · median {formatCount(r.median)}
              {r.zero > 0 && r.zero === r.posts && <span className="ml-1.5 text-warning">all at 0</span>}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function StatsPage() {
  const { data: posts, isLoading } = usePosts();
  const { data: campaigns = [] } = useCampaigns();
  const search = useSearch({ from: "/stats" });
  const navigate = useNavigate({ from: "/stats" });
  const campaign = search.campaign ?? "all";
  const [platform, setPlatform] = useState("all");
  const platforms = [...new Set((posts ?? []).map((p) => p.platform))];
  const setCampaign = (value: string) => void navigate({ search: (s) => ({ ...s, campaign: value === "all" ? undefined : value }) });
  const list = (posts ?? []).filter((p) => (campaign === "all" || p.campaign === campaign) && (platform === "all" || p.platform === platform));
  const sum = (f: (p: Post) => number | null | undefined) => list.reduce((s, p) => s + (f(p) ?? 0), 0);
  const withWatch = list.filter((p) => p.avg_watch_s != null);
  const withSkip = list.filter((p) => p.skip_rate_pct != null);
  const money = list.filter((p) => p.est_earnings != null);
  const views = list.map((p) => p.views ?? 0).sort((a, b) => a - b);
  const median = views.length ? views[Math.floor(views.length / 2)] : null;

  return (
    <div className="fade-in">
      <PageHeader title="Stats" subtitle="Every post's numbers, refreshed on each sync."
        actions={<div className="flex flex-wrap gap-2">
          {platforms.length > 1 && (
            <select value={platform} onChange={(e) => setPlatform(e.target.value)}
                    className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm" aria-label="Platform">
              <option value="all">All platforms</option>
              {platforms.map((p) => <option key={p} value={p}>{PLATFORM_NAME[p] ?? p}</option>)}
            </select>
          )}
          <select value={campaign} onChange={(e) => setCampaign(e.target.value)}
                  className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm" aria-label="Campaign">
            <option value="all">All campaigns</option>
            {campaigns.filter((c) => c.clips > 0).map((c) => <option key={c.name} value={c.name}>{c.title}</option>)}
          </select>
        </div>} />
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
          <ByPlatform posts={list} />
          <PostTable key={search.sort ?? "posted"} posts={list} initialSort={search.sort} showCampaign={campaign === "all"} />
        </div>
      )}
    </div>
  );
}
