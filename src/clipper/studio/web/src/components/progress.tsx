import { Check, Loader2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/* Waiting, designed (D189). A long wait shows what's being done (the steps, done and to come), how long it has
   taken and about how long is left; the bar never sits still between real updates, but creeps forward a few
   percent, slower and slower, and never past the next real milestone. Stripes run backwards inside the fill
   (a moving bar reads as faster), a dot marks the track's end, and screen readers hear each new step. */

const reduced = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The shown percent: the real one, crept towards `cap` between updates (3% of the gap a second). */
function useCreep(pct: number, cap: number) {
  const [shown, setShown] = useState(pct);
  useEffect(() => setShown((s) => (pct < s - 15 ? pct : Math.max(s, pct))), [pct]);   // a new run starts low again
  useEffect(() => {
    if (reduced()) return;
    const t = setInterval(() => setShown((s) => (s < cap ? s + (cap - s) * 0.03 : s)), 1000);
    return () => clearInterval(t);
  }, [cap]);
  return Math.max(shown, pct);
}

/** Seconds now, ticking once a second while shown. */
function useNow() {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

export const clock = (s: number) => {
  s = Math.max(0, Math.floor(s));
  return s >= 3600 ? `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`
    : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/** About how long is left, from the pace so far; nothing until there's a pace to go by. */
export function timeLeft(pct: number, elapsed: number): string | null {
  if (pct < 15 || pct >= 100 || elapsed < 20) return null;
  const left = (elapsed * (100 - pct)) / pct;
  if (left > 3600) return null;
  return left < 60 ? "under a minute left" : `about ${Math.ceil(left / 60)} min left`;
}

/* The tab's title says the progress while you're in another tab (the page is still open). */
const titles = new Map<symbol, string>();
let baseTitle = "";
function showTitle() {
  if (!baseTitle) baseTitle = document.title;
  const first = titles.values().next().value;
  document.title = first ? `${first} · ${baseTitle}` : baseTitle;
}
function useTitle(text: string | null) {
  const key = useRef(Symbol("title")).current;
  useEffect(() => {
    if (text) titles.set(key, text); else titles.delete(key);
    showTitle();
  }, [key, text]);
  useEffect(() => () => { titles.delete(key); showTitle(); }, [key]);
}

/** A progress bar: `value` 0-100, or null when how far along can't be known (a sliding band). */
export function ProgressBar({ value, label, valueText, size = "md", live = true, className }: {
  value: number | null; label: string; valueText?: string; size?: "sm" | "md"; live?: boolean; className?: string;
}) {
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
         aria-valuenow={value === null ? undefined : Math.round(value)} aria-valuetext={valueText}
         className={cn("relative overflow-hidden rounded-full bg-surface-3", size === "md" ? "h-2" : "h-1.5", className)}>
      {value === null ? (
        <div className="bar-indeterminate absolute inset-y-0 w-1/3 rounded-full bg-accent" />
      ) : (
        <div className={cn("relative h-full overflow-hidden rounded-full bg-accent transition-[width] duration-1000 ease-decelerate",
                           live && value < 100 && "bar-ribs")}
             style={{ width: `${Math.max(3, Math.min(100, value))}%` }} />
      )}
      {/* The end of the track, visible on any surface (the track itself is faint). */}
      <span className="absolute right-[2px] top-1/2 size-1 -translate-y-1/2 rounded-full bg-accent" aria-hidden />
    </div>
  );
}

export type Step = { label: string; at: number };

/** A long job's wait: the live stage, the bar, time so far and left, and (when given) its steps. */
export function JobProgress({ pct, stage, started, steps, label, size = "md", children }: {
  pct: number | null; stage: string | null; started?: number | null; steps?: Step[]; label: string;
  size?: "sm" | "md"; children?: ReactNode;
}) {
  const real = pct ?? 0;
  const cur = steps ? steps.reduce((c, s, i) => (s.at <= real ? i : c), 0) : -1;
  const next = steps?.[cur + 1]?.at ?? 100;
  const shown = useCreep(real, Math.max(real, Math.min(next - 1, real + 8, 99)));
  const now = useNow();
  const elapsed = started ? now - started : 0;
  const left = timeLeft(real, elapsed);
  const said = stage || "Starting…";
  useTitle(pct === null ? null : `${Math.floor(shown)}%`);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 truncate">{said}</span>
        <span className="tabular shrink-0 text-muted">{pct === null ? "" : `${Math.floor(shown)}%`}</span>
      </div>
      <ProgressBar value={pct === null ? null : shown} label={label} valueText={said} size={size} />
      {started ? (
        <div className="tabular flex justify-between gap-3 text-xs text-subtle">
          <span>{clock(elapsed)} so far</span>
          {left && <span>{left}</span>}
        </div>
      ) : null}
      {/* Screen readers hear each new step, not every percent. */}
      <span className="sr-only" role="status" aria-live="polite">{steps ? steps[cur]?.label : said}</span>
      {steps && (
        <ol className="mt-1 flex flex-col gap-1 text-xs" aria-label="Steps">
          {steps.map((s, i) => (
            <li key={s.label} className={cn("flex items-center gap-2",
                                             i < cur ? "text-muted" : i === cur ? "font-medium text-fg" : "text-subtle")}>
              {i < cur ? <Check className="pop-in size-3.5 shrink-0 text-success" aria-label="done" />
                : i === cur ? <Loader2 className="size-3.5 shrink-0 animate-spin text-accent" aria-label="now" />
                : <span className="mx-[3px] size-2 shrink-0 rounded-full border border-line-strong" aria-label="to come" />}
              <span className="min-w-0 truncate">{i === cur && stage ? stage : s.label}</span>
            </li>
          ))}
        </ol>
      )}
      {children}
    </div>
  );
}

/** "12s" since this appeared, after a few seconds: a short wait that's running long says it's still going. */
export function Waited({ after = 3, className }: { after?: number; className?: string }) {
  const start = useRef(Date.now() / 1000).current;
  const now = useNow();
  const s = now - start;
  return s < after ? null : <span className={cn("tabular text-subtle", className)}>{s < 60 ? `${Math.floor(s)}s` : clock(s)}</span>;
}

/** An image that fades in once it has arrived, over its box's placeholder colour, instead of popping in. */
export function FadeImg({ className, onLoad, onError, ...props }: React.ImgHTMLAttributes<HTMLImageElement>) {
  const [loaded, setLoaded] = useState(false);
  return (
    <img loading="lazy" decoding="async" alt="" {...props}
         className={cn("img-in", loaded && "loaded", className)}
         ref={(el) => { if (el?.complete && el.naturalWidth && !loaded) setLoaded(true); }}
         onLoad={(e) => { setLoaded(true); onLoad?.(e); }}
         onError={(e) => { setLoaded(true); onError?.(e); }} />
  );
}
