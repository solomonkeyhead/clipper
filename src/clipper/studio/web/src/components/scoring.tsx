import { Hand, ThumbsDown, ThumbsUp } from "lucide-react";
import { useRateClip, type Clip } from "@/api/client";
import { cn } from "@/lib/utils";
import { Tip } from "./ui";

const RUBRIC_LABELS: Record<string, string> = {
  hook_strength: "Hook", standalone_clarity: "Makes sense alone", payoff: "Payoff",
  emotional_intensity: "Emotion", quotability: "Quotable", ending_completeness: "Ending",
};

const GOOD_REASONS: [string, string][] = [
  ["great_hook", "Great hook"], ["funny", "Funny"], ["emotional", "Emotional"],
  ["good_ending", "Good ending"], ["on_brief", "Right for the campaign"],
];
const BAD_REASONS: [string, string][] = [
  ["weak_hook", "Weak hook"], ["boring", "Boring / slow"], ["bad_ending", "Cut off / bad ending"],
  ["needs_context", "Needs context"], ["off_brief", "Wrong for the campaign"],
  ["bad_framing", "Bad framing"], ["caption_errors", "Caption mistakes"],
];

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
  const reasons = clip.reasons ?? [];
  const set = (rating: number | null, next = reasons) => rate.mutate({ id: clip.id, rating, reasons: next });
  const toggle = (key: string) => {
    const next = reasons.includes(key) ? reasons.filter((r) => r !== key) : [...reasons, key];
    set(clip.rating ?? null, next);
  };
  const chip = ([key, label]: [string, string], tone: "good" | "bad") => (
    <button key={key} onClick={() => toggle(key)} disabled={!clip.rating} aria-pressed={reasons.includes(key)}
      className={cn("h-7 rounded-full border px-2.5 text-xs font-medium transition-colors disabled:opacity-40",
        reasons.includes(key)
          ? tone === "good" ? "border-success/50 bg-[color-mix(in_oklch,var(--success)_14%,transparent)] text-success"
            : "border-warning/50 bg-[color-mix(in_oklch,var(--warning)_14%,transparent)] text-warning"
          : "border-line text-muted hover:text-fg")}>
      {label}
    </button>
  );
  // Good / Not good: two clear choices instead of five stars (D74). Older 1-5
  // ratings still count: 4-5 read as good, 1-2 as not good.
  const verdict = clip.rating == null || clip.rating === 3 ? null : clip.rating >= 4 ? "good" : "bad";
  const choose = (v: "good" | "bad") => set(verdict === v ? null : v === "good" ? 5 : 1, []);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Your verdict">
        {(["good", "bad"] as const).map((v) => (
          <button key={v} role="radio" aria-checked={verdict === v} onClick={() => choose(v)}
            className={cn("flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors",
              verdict === v ? v === "good" ? "border-success/50 bg-[color-mix(in_oklch,var(--success)_14%,transparent)] text-success"
                : "border-warning/50 bg-[color-mix(in_oklch,var(--warning)_14%,transparent)] text-warning"
                : "border-line text-muted hover:text-fg")}>
            {v === "good" ? <><ThumbsUp className="size-4" /> Good</> : <><ThumbsDown className="size-4" /> Not good</>}
          </button>
        ))}
      </div>
      {verdict && (
        <div className="flex flex-wrap gap-1.5">{(verdict === "good" ? GOOD_REASONS : BAD_REASONS).map((r) => chip(r, verdict))}</div>
      )}
      <p className="text-xs text-subtle">
        Optional: clips you post already count as good. Clipper learns your taste from both{verdict ? "; tap reasons that fit" : ""}.
      </p>
    </div>
  );
}
