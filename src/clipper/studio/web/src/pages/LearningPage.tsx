import { Lightbulb } from "lucide-react";
import { useLearning, useSetSettings, useSettings, type Learning } from "@/api/client";
import { Card, PageHeader, Skeleton, Switch, Tip } from "@/components/ui";
import { cn, formatCount } from "@/lib/utils";

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
        {verdict === "not enough yet" ? `${n} of ${need} clips needed` : <Tip label={explain}><span>ρ = {rho?.toFixed(2)} over {n} clips</span></Tip>}
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
  const rated = r.bands.filter((b) => b.rated >= 2 && b.avg_rating != null);
  if (rated.length >= 2) {
    const hi = rated[rated.length - 1];
    const rest = rated.slice(0, -1);
    const restAvg = rest.reduce((s, b) => s + (b.avg_rating ?? 0) * b.rated, 0) / rest.reduce((s, b) => s + b.rated, 0);
    out.push((hi.avg_rating ?? 0) > restAvg
      ? <>The score points the right way: clips it scores {hi.label.toLowerCase()} average <b>{hi.avg_rating}★</b> from you, against {restAvg.toFixed(1)}★ below that.</>
      : <>Clips it scores {hi.label.toLowerCase()} don't rate better with you than lower ones yet ({hi.avg_rating}★ vs {restAvg.toFixed(1)}★).</>);
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
    out.push(<>{r.unrated} clip{r.unrated === 1 ? " has" : "s have"} no rating. Rating them, especially ones you like, is the fastest way to teach it.</>);
  }
  return out;
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

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs font-medium text-muted">Clips it learns from</span>
          <span className="tabular text-2xl font-semibold">{report.rated}</span>
          <span className="text-xs text-muted">posted, or marked good or not good</span>
        </Card>
        <Verdict label="Score matches your ratings" verdict={report.agreement_verdict} rho={report.agreement}
                 n={report.scored_and_rated} need={report.min_for_agreement}
                 explain="Rank correlation between Clipper's score and your rating: 1 is perfect, 0 is no relation." />
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
            <p className="text-xs text-muted">If it does, higher-scoring clips should get higher ratings and more views.</p>
          </div>
          <table className="tabular w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="px-2 py-1.5 font-medium">Clipper's score</th>
                <th className="px-2 py-1.5 font-medium">Clips</th>
                <th className="px-2 py-1.5 font-medium">Your avg rating</th>
                <th className="px-2 py-1.5 font-medium">Median views</th>
              </tr>
            </thead>
            <tbody>
              {report.bands.map((b) => (
                <tr key={b.label} className="border-t border-line">
                  <td className="px-2 py-2 font-medium">{b.label}</td>
                  <td className="px-2 py-2">{b.clips}</td>
                  <td className="px-2 py-2">{b.avg_rating != null ? <span className="text-money">★ {b.avg_rating}</span> : <span className="text-subtle">–</span>}
                    {b.rated > 0 && <span className="ml-1 text-xs text-subtle">({b.rated})</span>}</td>
                  <td className="px-2 py-2">{b.median_views != null ? formatCount(b.median_views) : <span className="text-subtle">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.rating_vs_views != null && (
            <p className="px-2 text-xs text-muted">
              Your own ratings vs views: ρ = {report.rating_vs_views.toFixed(2)}. {report.rating_vs_views >= 0.3
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
