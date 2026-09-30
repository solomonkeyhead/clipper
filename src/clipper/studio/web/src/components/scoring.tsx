import { Hand, Star } from "lucide-react";
import { RATING_WORDS, useRateClip, type Clip } from "@/api/client";
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

export function RatingStars({ rating }: { rating?: number | null }) {
  if (!rating) return null;
  return (
    <Tip label={`You rated it ${rating}/5 (${RATING_WORDS[rating]})`}>
      <span className="tabular flex items-center gap-0.5 text-[11px] font-semibold text-money">
        <Star className="size-3 fill-current" />{rating}
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
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="Your rating">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} role="radio" aria-checked={clip.rating === n}
            onClick={() => set(clip.rating === n ? null : n, clip.rating === n ? [] : reasons)}
            className={cn("flex h-8 items-center gap-1 rounded-md border px-2 text-sm font-medium transition-colors",
              clip.rating && n <= clip.rating ? "border-money/50 text-money" : "border-line text-muted hover:text-fg",
              clip.rating === n && "bg-surface-2")}>
            <Star className={cn("size-4", clip.rating && n <= clip.rating && "fill-current")} />
            <span className="hidden sm:inline">{RATING_WORDS[n]}</span>
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">{GOOD_REASONS.map((r) => chip(r, "good"))}</div>
      <div className="flex flex-wrap gap-1.5">{BAD_REASONS.map((r) => chip(r, "bad"))}</div>
      <p className="text-xs text-subtle">
        {clip.rating ? "Tap reasons that fit (optional)." : "Rate it first, then add reasons if you like."} Clipper learns your taste from these.
      </p>
    </div>
  );
}
