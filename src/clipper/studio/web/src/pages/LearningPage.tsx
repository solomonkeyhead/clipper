import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { FlaskConical, Lightbulb } from "lucide-react";
import { useCampaigns, useLearning, usePosts, useSetSettings, useSettings, useWhatsWorking, type Learning, type Post, type WhatsWorking } from "@/api/client";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Button, Card, PageHeader, Skeleton, Switch, Tip } from "@/components/ui";
import { cn, formatCount, PLATFORM_NAME } from "@/lib/utils";

const VERDICT: Record<string, { text: string; tone: string }> = {
  strong: { text: "Strong", tone: "text-success" },
  moderate: { text: "Moderate", tone: "text-accent" },
  weak: { text: "Weak", tone: "text-warning" },
  none: { text: "No link yet", tone: "text-warning" },
  backwards: { text: "Backwards", tone: "text-danger" },
  "not enough yet": { text: "Not enough yet", tone: "text-muted" },
};

function Verdict({ label, verdict, rho, n, need, explain }: {
  label: string; verdict: string; rho?: number | null; n: number; need: number; explain: string;
}) {
  const v = VERDICT[verdict] ?? VERDICT["not enough yet"];
  return (
    <Card className="flex flex-col gap-1 p-4">
      <span className="text-xs font-medium text-muted">{label}</span>
      <span className={cn("text-2xl font-semibold", v.tone)}>{v.text}</span>
      <span className="text-xs text-muted">
        {verdict === "not enough yet" ? `${n} of ${need} clips needed`
          : <Tip label={`${explain} Its rank correlation is ${rho?.toFixed(2)}: 1 is a perfect match, 0 no link.`}><span>over {n} clips</span></Tip>}
      </span>
    </Card>
  );
}

type Report = Learning;

/** What Clipper already does about the reasons people give most. */
const ANSWER: Record<string, string> = {
  weak_hook: "Each clip's opening line is now chosen before it's rendered, so it starts where a stranger would stay.",
  bad_ending: "Clips are extended to finish their last thought when it fits the length limit.",
  needs_context: "Moments that need earlier scenes score lower for standalone clarity.",
};

/** The report in plain words: what's working, what isn't, what would help. */
function insights(r: Report): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const top = r.reasons[0];
  if (top && top.count >= 2) {
    out.push(<>Your most common complaint is <b>{top.label.toLowerCase()}</b> ({top.count}×). {ANSWER[top.key] ?? ""}</>);
  }
  const rated = r.bands.filter((b) => b.rated >= 2 && b.liked_pct != null);
  if (rated.length >= 2) {
    const hi = rated[rated.length - 1];
    const rest = rated.slice(0, -1);
    const restPct = Math.round(rest.reduce((s, b) => s + (b.liked_pct ?? 0) * b.rated, 0) / rest.reduce((s, b) => s + b.rated, 0));
    out.push((hi.liked_pct ?? 0) > restPct
      ? <>The score points the right way: you liked <b>{hi.liked_pct}%</b> of the clips it scores {hi.label.toLowerCase()}, against {restPct}% below that.</>
      : <>Clips it scores {hi.label.toLowerCase()} don't land better with you than lower ones yet ({hi.liked_pct}% liked vs {restPct}%).</>);
  }
  const judged = r.dimensions.filter((d) => d.agreement != null);
  if (r.scored_and_rated >= r.min_for_agreement && judged.length) {
    const sorted = [...judged].sort((a, b) => (b.agreement ?? 0) - (a.agreement ?? 0));
    const best = sorted.filter((d) => (d.agreement ?? 0) >= 0.3).slice(0, 2).map((d) => d.label.toLowerCase());
    const none = sorted.filter((d) => Math.abs(d.agreement ?? 0) < 0.1).map((d) => d.label.toLowerCase());
    if (best.length) out.push(<>Of what it scores, <b>{best.join(" and ")}</b> {best.length === 1 ? "matches" : "match"} your ratings best.</>);
    if (none.length) out.push(<>Its read on <b>{none.join(" and ")}</b> has no link to what you like yet.</>);
  }
  if (r.unrated > 0) {
    out.push(<>{r.unrated} clip{r.unrated === 1 ? " has" : "s have"} no rating. Rating them, especially ones you like, is the fastest way to teach it.{" "}
      <Link to="/clips" search={{ rate: true }} className="font-medium text-accent hover:underline">Rate them →</Link></>);
  }
  return out;
}

type Side = WhatsWorking["comparisons"][number]["rows"][number]["yes"];

function SideCell({ label, side }: { label: string; side: Side }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-xs text-muted">{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span className="num text-xl">{side.median_views != null ? formatCount(side.median_views) : "–"}</span>
        <span className="text-xs text-subtle">{side.posts} post{side.posts === 1 ? "" : "s"}</span>
      </div>
      {side.skip_rate != null && (
        <Tip label="Instagram's skip rate: the share who swipe away in the first 3 seconds. Lower is better.">
          <span className="text-xs text-muted">{side.skip_rate}% swipe away</span>
        </Tip>
      )}
    </div>
  );
}

/** Each change Clipper made, posts with it against posts without, fairly (D105). */
function WhatsWorkingCard() {
  const { data } = useWhatsWorking();
  const { data: campaigns = [] } = useCampaigns();
  const { data: settings } = useSettings();
  const save = useSetSettings();
  const qc = useQueryClient();
  const [showAll, setShowAll] = useState(false);
  if (!data) return <Skeleton className="h-48" />;
  // A comparison with no verdict on any platform yet is one line, not a card of dashes (D154).
  const ready = data.comparisons.filter((c) => c.rows.some((r) => r.ratio != null));
  const waiting = data.comparisons.filter((c) => !ready.includes(c));
  const since = (settings?.tiktok_disclosed_since ?? data.disclosed_since).slice(0, 10);
  const hookGroups = new Map<string, typeof data.hooks>();
  for (const h of data.hooks) {
    const key = `${h.campaign}|${h.platform}`;
    hookGroups.set(key, [...(hookGroups.get(key) ?? []), h]);
  }
  return (
    <Card className="flex flex-col gap-4 p-5">
      <div>
        <h2 className="flex items-center gap-2 text-md font-semibold"><FlaskConical className="size-4 text-accent" /> What's working</h2>
        <p className="mt-0.5 text-sm text-muted">
          Views {data.age_hours} hours after posting, compared within each platform (medians, so one viral post doesn't decide it).
          A verdict needs {data.min_each} posts on each side.
        </p>
      </div>
      {waiting.length > 0 && (
        <p className="text-xs text-muted">
          Waiting for more posts: {waiting.map((c) => c.title).join(", ")}.{" "}
          <button type="button" className="font-medium text-accent hover:underline" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Hide them" : "Show them"}
          </button>
        </p>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {(showAll ? data.comparisons : ready).map((c) => (
          <div key={c.key} className="flex flex-col gap-2 rounded-lg border border-line p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">{c.title}</h3>
              {c.key === "disclosed" && (
                <label className="flex items-center gap-1.5 text-xs text-muted">
                  On since
                  <input type="date" value={since} aria-label="Switch on since"
                         onChange={(e) => e.target.value && save.mutate({ tiktok_disclosed_since: `${e.target.value} 00:00` },
                           { onSuccess: () => void qc.invalidateQueries({ queryKey: ["whats-working"] }) })}
                         className="h-7 rounded-md border border-line bg-surface-2 px-1.5 text-xs" />
                </label>
              )}
            </div>
            {c.rows.length ? c.rows.map((r) => {
              const better = r.ratio != null && r.ratio > 1.2, worse = r.ratio != null && r.ratio < 0.8;
              return (
                <div key={r.platform} className="grid grid-cols-[auto_1fr_1fr] items-center gap-3 border-t border-line pt-2 first-of-type:border-0 first-of-type:pt-0">
                  <Tip label={PLATFORM_NAME[r.platform] ?? r.platform}><span><PlatformIcon platform={r.platform} className="size-4" /></span></Tip>
                  <SideCell label={c.yes_label} side={r.yes} />
                  <SideCell label={c.no_label} side={r.no} />
                  <span className={cn("col-span-3 text-xs font-medium",
                    better ? "text-money" : worse ? "text-warning" : "text-muted")}>{r.verdict}</span>
                </div>
              );
            }) : <p className="text-xs text-subtle">No posts on either side yet.</p>}
          </div>
        ))}
      </div>
      {hookGroups.size > 0 && (
        <div>
          <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">On-screen hooks, best first</h3>
          <div className="grid gap-3 lg:grid-cols-2">
            {[...hookGroups.entries()].map(([key, rows]) => {
              const top = Math.max(1, ...rows.map((r) => r.median_views));
              return (
                <div key={key} className="flex flex-col gap-1.5 rounded-lg border border-line p-3">
                  <span className="flex items-center gap-1.5 text-xs text-muted"><PlatformIcon platform={rows[0].platform} className="size-3.5" />{campaigns.find((c) => c.name === rows[0].campaign)?.title ?? rows[0].campaign} · {PLATFORM_NAME[rows[0].platform] ?? rows[0].platform}</span>
                  {rows.slice(0, 6).map((h) => (
                    <div key={h.hook} className="flex items-center gap-2 text-xs">
                      <span className="min-w-0 flex-1 truncate" title={h.hook}>{h.hook}</span>
                      <span className="h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-surface-3">
                        <span className="block h-full rounded-full bg-accent" style={{ width: `${(h.median_views / top) * 100}%` }} />
                      </span>
                      <span className="tabular w-16 shrink-0 text-right">{formatCount(h.median_views)} <span className="text-subtle">×{h.posts}</span></span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Card>
  );
}

/** Teaching it from nothing, and taking what it learnt with you (D148): a progress line toward the first
 *  learnt weights, and the taste as a file to export, or a file from another install to start from. */
function TastePanel({ have, need }: { have: number; need: number }) {
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const exportIt = async () => {
    const res = await fetch("/api/learning/taste");
    const blob = new Blob([JSON.stringify(await res.json(), null, 2)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "clipper-taste.json" });
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const importIt = async (f: File | undefined) => {
    if (!f) return;
    try {
      const res = await fetch("/api/learning/taste", { method: "PUT", headers: { "Content-Type": "application/json" }, body: await f.text() });
      if (!res.ok) throw new Error((await res.json()).detail ?? "that didn't work");
      toast.success("Starting from that taste", { description: "Your own ratings take over as you rate clips." });
      void qc.invalidateQueries({ queryKey: ["learning"] });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  return (
    <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="text-sm">
        {have < need ? (
          <><b>Teach it your taste:</b> you've rated {have} of {need} clips it needs before its scores start following you.{" "}
            <Link to="/clips" search={{ status: "ready" }} className="font-medium text-accent hover:underline">Rate clips →</Link></>
        ) : <><b>It has learnt from {have} scored clips.</b> Export what it learnt to keep, or to start another install from.</>}
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" onClick={() => void exportIt()}>Export my taste</Button>
        <Button size="sm" variant="secondary" onClick={() => file.current?.click()}>Start from a taste file</Button>
        <input ref={file} type="file" accept="application/json,.json" className="hidden" onChange={(e) => void importIt(e.target.files?.[0])} />
      </div>
    </Card>
  );
}

/* ---------- experiments (D155) ---------- */

type Experiment = { id: string; name: string; a: string; b: string; metric: "pct" | "views" | "watch"; pairs: [number, number][] };

const METRIC: Record<Experiment["metric"], { label: string; get: (p: Post) => number | null | undefined; total?: boolean }> = {
  pct: { label: "% viewed", get: (p) => p.avg_view_pct },
  watch: { label: "average watch time", get: (p) => p.avg_watch_s },
  views: { label: "views", get: (p) => p.views, total: true },
};

/** The chance of at least `wins` of `n` coin flips coming up one way: the sign test, one-sided. */
function signTest(wins: number, n: number): number {
  let p = 0;
  let c = 1;   // n choose k, built up from k = 0
  for (let k = 0; k <= n; k++) {
    if (k >= wins) p += c / 2 ** n;
    c = (c * (n - k)) / (k + 1);
  }
  return p;
}

/** A video's number for a metric: summed for views, averaged over its platforms otherwise. */
function score(posts: Post[], clip: number, metric: Experiment["metric"]): number | null {
  const m = METRIC[metric];
  const v = posts.filter((p) => p.clip === clip).map(m.get).filter((x): x is number => x != null);
  if (!v.length) return null;
  const sum = v.reduce((a, b) => a + b, 0);
  return m.total ? sum : sum / v.length;
}

/** Pairs of videos that differ in one thing, A against B; a side is trusted when the sign test says its
 *  wins are unlikely to be luck (8 of 10). Small channels can't wait for big numbers (the research, D155). */
function Experiments() {
  const { data: settings } = useSettings();
  const save = useSetSettings();
  const { data: posts = [] } = usePosts();
  const [draft, setDraft] = useState({ name: "", a: "", b: "", metric: "pct" as Experiment["metric"] });
  let list: Experiment[] = [];
  try { list = JSON.parse(settings?.experiments ?? "[]"); } catch { list = []; }
  const put = (next: Experiment[]) => save.mutate({ experiments: JSON.stringify(next) }, { onError: (e) => toast.error((e as Error).message) });
  const clips = [...new Map(posts.map((p) => [p.clip, p.clip_title])).entries()];
  const add = () => {
    if (!draft.name.trim() || !draft.a.trim() || !draft.b.trim()) return toast.error("Give it a name and say what A and B are");
    put([...list, { id: String(Date.now()), name: draft.name.trim(), a: draft.a.trim(), b: draft.b.trim(), metric: draft.metric, pairs: [] }]);
    setDraft({ name: "", a: "", b: "", metric: "pct" });
  };
  const field = "h-8 rounded-sm border border-line bg-surface-2 px-2 text-sm focus:border-accent focus:outline-none";
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div>
        <h2 className="flex items-center gap-2 text-md font-semibold"><FlaskConical className="size-4 text-accent" /> Experiments</h2>
        <p className="mt-0.5 text-sm text-muted">
          Change one thing between two videos (A and B), post both, and pair them here. One side has to win about 8 pairs of 10
          before it's more than luck.
        </p>
      </div>
      {list.map((x) => <ExperimentRow key={x.id} x={x} posts={posts} clips={clips}
        onChange={(next) => put(list.map((y) => (y.id === x.id ? next : y)))}
        onRemove={() => put(list.filter((y) => y.id !== x.id))} />)}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted hover:text-fg">New experiment</summary>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input className={cn(field, "w-56")} placeholder="Name, e.g. Ending: loop or send" aria-label="Experiment name"
                 value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <input className={cn(field, "w-40")} placeholder="A, e.g. loop ending" aria-label="What A is"
                 value={draft.a} onChange={(e) => setDraft({ ...draft, a: e.target.value })} />
          <input className={cn(field, "w-40")} placeholder="B, e.g. send-to ending" aria-label="What B is"
                 value={draft.b} onChange={(e) => setDraft({ ...draft, b: e.target.value })} />
          <select className={field} aria-label="Compare by" value={draft.metric}
                  onChange={(e) => setDraft({ ...draft, metric: e.target.value as Experiment["metric"] })}>
            {Object.entries(METRIC).map(([k, m]) => <option key={k} value={k}>by {m.label}</option>)}
          </select>
          <Button size="sm" variant="secondary" onClick={add}>Add</Button>
        </div>
      </details>
    </Card>
  );
}

function ExperimentRow({ x, posts, clips, onChange, onRemove }: {
  x: Experiment; posts: Post[]; clips: [number, string][]; onChange: (x: Experiment) => void; onRemove: () => void;
}) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const title = (id: number) => clips.find(([c]) => c === id)?.[1] ?? `Clip ${id}`;
  const results = x.pairs.map(([ca, cb]) => {
    const va = score(posts, ca, x.metric), vb = score(posts, cb, x.metric);
    return { ca, cb, va, vb, winner: va == null || vb == null || va === vb ? null : va > vb ? "A" : "B" };
  });
  const aWins = results.filter((r) => r.winner === "A").length, bWins = results.filter((r) => r.winner === "B").length;
  const n = aWins + bWins, lead = Math.max(aWins, bWins), leader = aWins >= bWins ? x.a : x.b;
  const p = n ? signTest(lead, n) : 1;
  const fmt = (v: number | null) => (v == null ? "not in yet" : x.metric === "pct" ? `${v.toFixed(1)}%` : x.metric === "watch" ? `${v.toFixed(1)}s` : formatCount(v));
  const pick = "h-8 max-w-56 rounded-sm border border-line bg-surface-2 px-2 text-xs";
  return (
    <div className="rounded-md border border-line p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-sm font-medium">{x.name} <span className="font-normal text-muted">· A: {x.a} · B: {x.b} · by {METRIC[x.metric].label}</span></div>
        <button type="button" className="text-xs text-subtle hover:text-danger" onClick={onRemove}>Remove</button>
      </div>
      <p className={cn("mt-1 text-sm", n && aWins !== bWins && p < 0.06 ? "font-semibold text-success" : "text-muted")}>
        {n === 0 ? "No pairs with numbers yet."
          : aWins === bWins ? `Even so far: ${aWins} each.`
          : p < 0.06 ? `${leader} wins ${lead} of ${n} pairs. That's unlikely to be luck.`
          : `${leader} leads ${lead} of ${n}. Not enough yet to trust.`}
      </p>
      {results.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 text-xs">
          {results.map((r, i) => (
            <li key={i} className="flex flex-wrap items-center gap-x-2">
              <span className={cn(r.winner === "A" && "font-semibold")}>A: {title(r.ca)} ({fmt(r.va)})</span>
              <span className="text-subtle">vs</span>
              <span className={cn(r.winner === "B" && "font-semibold")}>B: {title(r.cb)} ({fmt(r.vb)})</span>
              <button type="button" className="text-subtle hover:text-danger" aria-label="Remove this pair"
                      onClick={() => onChange({ ...x, pairs: x.pairs.filter((_, j) => j !== i) })}>×</button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <select className={pick} value={a} onChange={(e) => setA(e.target.value)} aria-label={`A video for ${x.name}`}>
          <option value="">A video…</option>
          {clips.map(([c, t]) => <option key={c} value={c}>{t}</option>)}
        </select>
        <select className={pick} value={b} onChange={(e) => setB(e.target.value)} aria-label={`B video for ${x.name}`}>
          <option value="">B video…</option>
          {clips.map(([c, t]) => <option key={c} value={c}>{t}</option>)}
        </select>
        <Button size="sm" variant="secondary" disabled={!a || !b || a === b}
                onClick={() => { onChange({ ...x, pairs: [...x.pairs, [Number(a), Number(b)]] }); setA(""); setB(""); }}>Add pair</Button>
      </div>
    </div>
  );
}

export function LearningPage() {
  const { data: report, isLoading } = useLearning();
  const { data: settings } = useSettings();
  const save = useSetSettings();

  if (isLoading || !report) return <div className="flex flex-col gap-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-64" /></div>;
  const learning = settings ? settings.learn_from_feedback === "1" : report.active;
  const toWeights = Math.max(0, report.min_for_weights - report.weights_n);

  return (
    <div className="fade-in flex max-w-5xl flex-col gap-6">
      <PageHeader title="Learning"
        subtitle="Clipper learns which moments to pick from the clips you post, the ones you mark not good, and how many views your posts get. This page shows whether its scores match your taste and your views." />

      <TastePanel have={report.weights_n} need={report.min_for_weights} />

      <Experiments />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs font-medium text-muted">Clips it learns from</span>
          <span className="tabular text-2xl font-semibold">{report.rated}</span>
          <span className="text-xs text-muted">posted, or marked good or not good{report.weights_n < report.rated && `; ${report.weights_n} of them have a Clipper score to learn from`}</span>
        </Card>
        <Verdict label="Score matches your ratings" verdict={report.agreement_verdict} rho={report.agreement}
                 n={report.scored_and_rated} need={report.min_for_agreement}
                 explain="Rank correlation between Clipper's score and your Good / Not good: 1 is perfect, 0 is no relation." />
        <Verdict label="Score matches views" verdict={report.views_verdict} rho={report.views_agreement}
                 n={report.with_views} need={report.min_for_agreement}
                 explain="Rank correlation between Clipper's score and each clip's total views. Views on a new account are noisy." />
        <Card className="flex flex-col gap-2 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted">Learn from my ratings</span>
            <Switch label="Learn from my ratings" checked={learning}
                    onChange={(v) => save.mutate({ learn_from_feedback: v ? "1" : "0" })} />
          </div>
          <span className="text-xs text-muted">
            {learning ? "On: new clips are picked with your taste in mind." : "Off: Clipper scores with its defaults only."}
          </span>
        </Card>
      </div>

      <WhatsWorkingCard />

      {insights(report).length > 0 && (
        <Card className="flex flex-col gap-2 p-5">
          <h2 className="flex items-center gap-2 text-md font-semibold"><Lightbulb className="size-4 text-accent" /> What it's seeing</h2>
          <ul className="flex flex-col gap-1.5 text-sm">
            {insights(report).map((line, i) => (
              <li key={i} className="flex gap-2"><span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />{line}</li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4">
        <Card className="flex flex-col gap-3 p-4">
          <div className="px-2">
            <h2 className="text-md font-semibold">Does the score work?</h2>
            <p className="text-xs text-muted">If it does, you should like more of the higher-scoring clips, and they should get more views.</p>
          </div>
          <table className="tabular w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="px-2 py-1.5 font-medium">Clipper's score</th>
                <th className="px-2 py-1.5 font-medium">Clips</th>
                <th className="px-2 py-1.5 font-medium">You liked</th>
                <th className="px-2 py-1.5 font-medium">Median views</th>
              </tr>
            </thead>
            <tbody>
              {report.bands.map((b) => (
                <tr key={b.label} className="border-t border-line">
                  <td className="px-2 py-2 font-medium">{b.label}</td>
                  <td className="px-2 py-2">{b.clips}</td>
                  <td className="px-2 py-2">
                    {b.liked_pct != null ? (
                      <span className="inline-flex items-center gap-2">
                        <span className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-3">
                          <span className="block h-full rounded-full bg-success" style={{ width: `${b.liked_pct}%` }} />
                        </span>
                        {b.liked_pct}%
                      </span>
                    ) : <span className="text-subtle">–</span>}
                    {b.rated > 0 && <span className="ml-1 text-xs text-subtle">of {b.rated}</span>}</td>
                  <td className="px-2 py-2">{b.median_views != null ? formatCount(b.median_views) : <span className="text-subtle">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.rating_vs_views != null && (
            <p className="px-2 text-xs text-muted">
              <Tip label={`Rank correlation ${report.rating_vs_views.toFixed(2)}: 1 is a perfect match, 0 no link.`}>
                <span>How well your own ratings predict views: <b>{report.rating_vs_views >= 0.5 ? "strongly" : report.rating_vs_views >= 0.3 ? "fairly well" : report.rating_vs_views >= 0.1 ? "weakly" : "not yet"}</b>.</span>
              </Tip>{" "}{report.rating_vs_views >= 0.3
                ? "Your taste predicts views well, so learning from it should help."
                : "Your taste and views don't line up much yet; let views settle."}
            </p>
          )}
        </Card>
      </div>

      <Card className="flex flex-col gap-4 p-5">
        <div>
          <h2 className="text-md font-semibold">What Clipper has learned</h2>
          <p className="mt-0.5 text-sm text-muted">
            The score adds up six parts. Parts that match what you post and what you mark not good count for more.
            {toWeights > 0 ? ` Post or mark ${toWeights} more scored clip${toWeights === 1 ? "" : "s"} to start adjusting.` : ` Based on ${report.weights_n} rated clips.`}
          </p>
        </div>
        <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
          {report.dimensions.map((d) => {
            const up = d.learned - d.default;
            return (
              <div key={d.key} className="flex items-center gap-3 text-sm">
                <span className="w-36 shrink-0">{d.label}</span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.min(100, d.learned * 200)}%` }} />
                </span>
                <span className="tabular w-24 text-right text-xs">
                  {Math.round(d.learned * 100)}%
                  {Math.abs(up) >= 0.005 && <span className={cn("ml-1", up > 0 ? "text-success" : "text-warning")}>
                    {up > 0 ? "▲" : "▼"}{Math.abs(Math.round(up * 100))}
                  </span>}
                </span>
              </div>
            );
          })}
        </div>
        {report.reasons.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Reasons you give most</h3>
            <div className="flex flex-wrap gap-1.5">
              {report.reasons.map((r) => (
                <span key={r.key} className="rounded-full border border-line px-2.5 py-1 text-xs">{r.label} <b className="tabular">{r.count}</b></span>
              ))}
            </div>
          </div>
        )}
        {(report.edit_problems ?? []).length > 0 && (
          <div>
            <h3 className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">Edit problems you've flagged</h3>
            <p className="mb-1.5 text-xs text-subtle">About how a clip was made, not which moment: kept out of your taste so good moments aren't avoided.</p>
            <div className="flex flex-wrap gap-1.5">
              {(report.edit_problems ?? []).map((r) => (
                <span key={r.key} className="rounded-full border border-warning/40 px-2.5 py-1 text-xs">{r.label} <b className="tabular">{r.count}</b></span>
              ))}
            </div>
          </div>
        )}
        {report.taste ? (
          <details>
            <summary className="cursor-pointer text-sm text-muted hover:text-fg">What the scorer is told about your taste</summary>
            <pre className="mt-2 max-h-72 overflow-auto rounded-md border border-line bg-surface-1 p-3 text-xs whitespace-pre-wrap">{report.taste}</pre>
          </details>
        ) : (
          <p className="text-xs text-subtle">After 3 clips posted or marked, Clipper also shows the scorer examples of clips you liked and didn't.</p>
        )}
      </Card>
    </div>
  );
}
