import { useLearning, useSetSettings, useSettings } from "@/api/client";
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
        subtitle="Clipper learns from the clips you post and the ones you mark not good. This page shows whether its scores match your taste and your views." />

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
