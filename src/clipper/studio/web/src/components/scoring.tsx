import { Hand, ThumbsDown, ThumbsUp } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { markNotGood, useRateClip, useReasons, type Clip } from "@/api/client";
import { cn } from "@/lib/utils";
import { Tip } from "./ui";

const RUBRIC_LABELS: Record<string, string> = {
  hook_strength: "Hook", standalone_clarity: "Makes sense alone", payoff: "Payoff",
  emotional_intensity: "Emotion", quotability: "Quotable", ending_completeness: "Ending",
};

const scoreTone = (score: number) =>
  score >= 8 ? "text-success" : score >= 6.5 ? "text-accent" : score >= 5.5 ? "text-fg" : "text-warning";

/** Clipper's 0-10 score, or "Hand-picked" for a clip cut from chosen times. */
export function ScoreBadge({ clip }: { clip: Clip }) {
  if (clip.picked_by === "hand") {
    return (
      <Tip label="Hand-picked: cut from times you chose, so Clipper didn't score it">
        <span className="text-subtle" aria-label="Hand-picked"><Hand className="size-3.5" /></span>
      </Tip>
    );
  }
  if (clip.score == null) return null;
  return (
    <Tip label={`Clipper's score out of 10${clip.pool_rank && clip.pool ? `, #${clip.pool_rank} of ${clip.pool} moments in the video` : ""}`}>
      <span className={cn("tabular rounded-[4px] bg-surface-2 px-1.5 py-px text-[11px] font-semibold", scoreTone(clip.score))}>
        {clip.score.toFixed(1)}
      </span>
    </Tip>
  );
}

/** Your verdict on a card: thumbs up (4-5, or posted) or down (1-2). */
export function RatingMark({ rating }: { rating?: number | null }) {
  if (!rating || rating === 3) return null;
  const good = rating >= 4;
  return (
    <Tip label={good ? "You liked it" : "You marked it not good"}>
      <span className={cn("flex items-center", good ? "text-success" : "text-warning")}>
        {good ? <ThumbsUp className="size-3.5" /> : <ThumbsDown className="size-3.5" />}
      </span>
    </Tip>
  );
}

export function ScoreBreakdown({ clip }: { clip: Clip }) {
  if (clip.picked_by === "hand") {
    return <p className="text-sm text-muted">You picked this moment's times, so Clipper didn't score it. Your rating still teaches it what you like.</p>;
  }
  if (clip.score == null) {
    return <p className="text-sm text-muted">No score kept for this clip (it was made before scores were saved, or its scoring files are gone).</p>;
  }
  const dims = Object.entries(RUBRIC_LABELS).filter(([key]) => clip.rubric[key] != null);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-3">
        <span className={cn("tabular text-3xl font-semibold", scoreTone(clip.score))}>{clip.score.toFixed(1)}</span>
        <span className="text-sm text-muted">
          out of 10{clip.pool_rank && clip.pool ? <> · #{clip.pool_rank} of {clip.pool} moments in this video</> : null}
        </span>
      </div>
      {clip.watched != null && (
        <div className="rounded-md bg-surface-2/60 p-2.5 text-xs">
          <div className="text-muted">
            Read: <span className="tabular font-medium text-fg">{clip.read?.toFixed(1) ?? "?"}</span> · Watched:{" "}
            <span className="tabular font-medium text-fg">{clip.watched.toFixed(1)}</span>
            {clip.visual_payoff != null && <> · the picture adds <span className="tabular font-medium text-fg">{clip.visual_payoff}/10</span></>}
          </div>
          {clip.sees && <p className="mt-1">What the AI saw: {clip.sees}</p>}
        </div>
      )}
      {dims.length > 0 && (
        <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
          {dims.map(([key, label]) => (
            <div key={key} className="flex items-center gap-2 text-xs">
              <dt className="w-32 shrink-0 text-muted">{label}</dt>
              <dd className="flex flex-1 items-center gap-2">
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full rounded-full bg-accent" style={{ width: `${clip.rubric[key] * 10}%` }} />
                </span>
                <span className="tabular w-7 text-right font-medium">{clip.rubric[key].toFixed(1)}</span>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function RatingPanel({ clip }: { clip: Clip }) {
  const rate = useRateClip();
  const qc = useQueryClient();
  // What worked, what didn't in the moment, and what didn't in the edit (learn/feedback.py, D86).
  const { data: groups = [] } = useReasons();
  const good = new Set(groups.filter((g) => g.tone === "good").flatMap((g) => g.reasons.map((r) => r.key)));
  const reasons = clip.reasons ?? [];
  // Good / Not good: two clear choices instead of five stars (D74). Older 1-5
  // ratings still count: 4-5 read as good, 1-2 as not good.
  const verdict = clip.rating == null || clip.rating === 3 ? null : clip.rating >= 4 ? "good" : "bad";
  const set = (rating: number | null, next: string[]) => rate.mutate({ id: clip.id, rating, reasons: next });
  const notGood = async (next: string[]) => {
    // On a ready clip, Not good also skips it, the same as the card's button.
    await markNotGood(clip.id, undefined, next);
    void qc.invalidateQueries({ queryKey: ["clips"] });
    toast("Marked not good", { description: "Skipped. Tap what worked too: Clipper keeps those.", duration: 4000 });
  };
  const choose = (v: "good" | "bad") => {
    if (verdict === v) return set(null, []);
    if (v === "bad" && clip.status === "ready") return void notGood(reasons);
    set(v === "good" ? 5 : 1, reasons);
  };
  const toggle = (key: string) => {
    const next = reasons.includes(key) ? reasons.filter((r) => r !== key) : [...reasons, key];
    // A reason on an unrated clip rates it: a skipped clip as not good, otherwise by the reason.
    const rating = clip.rating ?? (clip.status === "skipped" || !good.has(key) ? 1 : 5);
    set(rating, next);
  };
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Your verdict">
        {(["good", "bad"] as const).map((v) => (
          <button key={v} role="radio" aria-checked={verdict === v} onClick={() => choose(v)}
            className={cn("flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors",
              verdict === v ? v === "good" ? "border-success/50 bg-[color-mix(in_oklch,var(--success)_14%,transparent)] text-success"
                : "border-warning/50 bg-[color-mix(in_oklch,var(--warning)_14%,transparent)] text-warning"
                : "border-line text-muted hover:text-fg")}>
            {v === "good" ? <><ThumbsUp className="size-4" /> Good</> : <><ThumbsDown className="size-4" /> Not good</>}
          </button>
        ))}
        <span className="text-xs text-subtle">
          {clip.status === "ready" ? "Not good skips it and teaches Clipper."
            : clip.status === "skipped" ? "Skipped clips can have good parts too: tap what worked."
            : "Posting it already counts as good."}
        </span>
      </div>
      {(verdict || reasons.length > 0) && <div className="flex flex-col gap-1.5">
        {groups.map((g) => (
          <div key={g.label} className="flex flex-wrap items-center gap-1.5">
            <span className="w-24 shrink-0 text-xs text-muted">{g.label}</span>
            {g.reasons.map(({ key, label }) => (
              <button key={key} onClick={() => toggle(key)} aria-pressed={reasons.includes(key)}
                className={cn("h-7 rounded-full border px-2.5 text-xs font-medium transition-colors",
                  reasons.includes(key)
                    ? g.tone === "good" ? "border-success/50 bg-[color-mix(in_oklch,var(--success)_14%,transparent)] text-success"
                      : "border-warning/50 bg-[color-mix(in_oklch,var(--warning)_14%,transparent)] text-warning"
                    : "border-line text-muted hover:text-fg")}>
                {label}
              </button>
            ))}
          </div>
        ))}
        <p className="text-xs text-subtle">
          Tap any that fit, good and bad. Edit problems are fixed, not learnt: a good moment with bad framing still
          counts as a good moment.
        </p>
      </div>}
    </div>
  );
}
