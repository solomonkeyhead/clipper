import * as RadixSwitch from "@radix-ui/react-switch";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { Check, Copy } from "lucide-react";
import { forwardRef, useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn, copyText } from "@/lib/utils";

/* ---------- Button ---------- */

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "icon";

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: Size;
}>(({ variant = "secondary", size = "md", className, ...props }, ref) => (
  <button
    ref={ref}
    // Not a form's submit unless it says so: a helper button inside a form
    // ("Fill in the form") once saved the campaign it was meant to fill.
    type="button"
    className={cn(
      "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-sm font-medium",
      "transition-[background-color,border-color,color,transform] duration-[var(--dur-fast)] ease-standard",
      "active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40",
      size === "sm" && "h-7 px-2.5 text-xs",
      size === "md" && "h-9 px-3.5 text-sm",
      size === "icon" && "size-8 text-sm",
      variant === "primary" && "bg-accent text-accent-fg hover:bg-accent-hover",
      variant === "secondary" && "border border-line bg-surface-2 text-fg hover:border-line-strong hover:bg-surface-3",
      variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-fg",
      variant === "danger" && "border border-line bg-surface-2 text-danger hover:bg-surface-3",
      className,
    )}
    {...props}
  />
));
Button.displayName = "Button";

/* ---------- Tooltip (teaches the shortcut) ---------- */

export function Tip({ label, keys, children, side = "top" }: {
  label: ReactNode; keys?: string; children: ReactNode; side?: "top" | "bottom" | "left" | "right";
}) {
  return (
    <RadixTooltip.Root delayDuration={500}>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={6}
          className="fade-in z-50 flex max-w-72 items-center gap-2 rounded-sm border border-line bg-surface-3 px-2 py-1 text-xs text-fg shadow-2"
        >
          {label}
          {keys && <Kbd>{keys}</Kbd>}
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}

export const Kbd = ({ children, className }: { children: ReactNode; className?: string }) => (
  <kbd className={cn("rounded-[4px] border border-line-strong bg-surface-2 px-1.5 py-px font-mono text-[11px] text-muted", className)}>
    {children}
  </kbd>
);

/* ---------- Copy button with a "Copied" state ---------- */

export function CopyButton({ text, what, label, size = "sm", variant = "secondary", keys, className, tip }: {
  text: string; what: string; label?: string; size?: Size; variant?: Variant; keys?: string; className?: string;
  /** The tooltip, when "Copy <what>" isn't enough (e.g. the text itself). */
  tip?: ReactNode;
}) {
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(false), 1400);
    return () => clearTimeout(t);
  }, [done]);
  const button = (
    <Button
      size={size}
      variant={variant}
      className={className}
      aria-label={label ? undefined : `Copy ${what.toLowerCase()}`}
      onClick={async (e) => {
        e.stopPropagation();
        if (await copyText(text, what)) setDone(true);
      }}
    >
      {done ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
      {label && <span>{done ? "Copied" : label}</span>}
    </Button>
  );
  return <Tip label={tip ?? `Copy ${what.toLowerCase()}`} keys={keys}>{button}</Tip>;
}

/* ---------- Chips and status ---------- */

export const Chip = ({ children, tone = "neutral", className, title }: {
  children: ReactNode; tone?: "neutral" | "accent" | "money" | "warning" | "success" | "danger" | "info";
  className?: string; title?: string;
}) => (
  <span
    title={title}
    className={cn(
      "inline-flex h-6 items-center gap-1 rounded-full border px-2 text-xs font-medium",
      tone === "neutral" && "border-line bg-surface-2 text-muted",
      tone === "accent" && "border-transparent bg-accent-soft text-accent",
      tone === "money" && "border-transparent bg-[color-mix(in_oklch,var(--money)_14%,transparent)] text-money",
      tone === "warning" && "border-transparent bg-[color-mix(in_oklch,var(--warning)_14%,transparent)] text-warning",
      tone === "success" && "border-transparent bg-[color-mix(in_oklch,var(--success)_14%,transparent)] text-success",
      tone === "danger" && "border-transparent bg-[color-mix(in_oklch,var(--danger)_14%,transparent)] text-danger",
      tone === "info" && "border-transparent bg-[color-mix(in_oklch,var(--info)_14%,transparent)] text-info",
      className,
    )}
  >
    {children}
  </span>
);

const STATUS_TONE = { ready: "info", posted: "warning", submitted: "success", skipped: "neutral" } as const;
const STATUS_LABEL = { ready: "Ready to post", posted: "Posted", submitted: "Submitted", skipped: "Skipped" } as const;

export function StatusChip({ status }: { status: string }) {
  const s = (status in STATUS_TONE ? status : "ready") as keyof typeof STATUS_TONE;
  return (
    <Chip tone={STATUS_TONE[s]} className="whitespace-nowrap">
      <span className="size-1.5 shrink-0 rounded-full bg-current" aria-hidden />
      {STATUS_LABEL[s]}
    </Chip>
  );
}

/* ---------- Surfaces ---------- */

export const Card = ({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("rounded-lg border border-line bg-surface-1 shadow-1", className)} {...props}>{children}</div>
);

/** Only shows after 150 ms, so fast loads never flash a skeleton. */
export function Skeleton({ className }: { className?: string }) {
  const [show, setShow] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setShow(true), 150);
    return () => clearTimeout(t);
  }, []);
  return <div className={cn(show ? "skeleton" : "", className)} aria-hidden />;
}

export function EmptyState({ icon, title, body, action }: {
  icon: ReactNode; title: string; body?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="fade-in flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line-strong px-6 py-14 text-center">
      {/* The empty 9:16 frame: a clip waiting to happen. */}
      <div className="grid h-[72px] w-[41px] place-items-center rounded-[10px] border-2 border-line-strong text-muted [&_svg]:size-5">{icon}</div>
      <div className="text-md font-semibold">{title}</div>
      {body && <div className="max-w-md text-sm text-muted">{body}</div>}
      {action}
    </div>
  );
}

export function Switch({ checked, onChange, label, id }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; id?: string;
}) {
  return (
    <RadixSwitch.Root
      id={id}
      checked={checked}
      onCheckedChange={onChange}
      aria-label={label}
      className="relative h-5 w-9 shrink-0 rounded-full bg-surface-3 transition-colors duration-[var(--dur-base)] data-[state=checked]:bg-accent"
    >
      <RadixSwitch.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow-1 transition-transform duration-[var(--dur-base)] ease-standard data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-accent-fg" />
    </RadixSwitch.Root>
  );
}

/** Clipper's mark: a 9:16 clip with its caption on, the lime bar being the
 *  word spoken right now (D102). */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 18 32" className={cn("h-7 w-auto shrink-0", className)} aria-hidden>
      <rect x="1.25" y="1.25" width="15.5" height="29.5" rx="4" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <rect x="4" y="17.5" width="10" height="3.5" rx="1" fill="var(--accent)" />
      <rect x="5" y="23" width="6" height="2.2" rx="0.8" fill="currentColor" />
    </svg>
  );
}

/**
 * A heading set like a clip's caption: heavy, outlined, words popping in one
 * by one, with `hi` (a word or phrase in it) lit in lime. A title that isn't
 * plain text is shown as it is.
 */
export function CaptionTitle({ text, hi, as: Tag = "h1", className }: {
  text: ReactNode; hi?: string; as?: "h1" | "h2"; className?: string;
}) {
  if (typeof text !== "string") return <Tag className={cn("cap", className)}>{text}</Tag>;
  const at = hi ? text.toLowerCase().indexOf(hi.toLowerCase()) : -1;
  const words = text.split(/\s+/).filter(Boolean);
  let pos = 0;
  return (
    <Tag className={cn("cap", className)} aria-label={text}>
      {words.map((word, i) => {
        const start = text.indexOf(word, pos);
        pos = start + word.length;
        const lit = at >= 0 && start >= at && start < at + (hi?.length ?? 0);
        return (
          <span key={i} aria-hidden>
            <span className={cn("cap-word", lit && "cap-hi")} style={{ "--i": i } as React.CSSProperties}>{word}</span>
            {i < words.length - 1 && " "}
          </span>
        );
      })}
    </Tag>
  );
}

export function PageHeader({ title, hi, subtitle, actions }: {
  title: ReactNode; hi?: string; subtitle?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <CaptionTitle text={title} hi={hi} className="text-[clamp(1.9rem,3.2vw,2.6rem)]" />
        {subtitle && <p className="mt-2 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A small line of how a number moved, lime, no axes: the shape, not the values. */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return null;
  const W = 120, H = 32, lo = Math.min(...values), hi = Math.max(...values);
  const x = (i: number) => (i / (values.length - 1)) * W;
  const y = (v: number) => H - 2 - ((v - lo) / Math.max(1, hi - lo)) * (H - 4);
  const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={cn("h-8 w-full overflow-visible", className)} aria-hidden>
      <path d={`${line}L${W},${H}L0,${H}Z`} fill="var(--accent)" opacity="0.12" />
      <path d={line} fill="none" stroke="var(--accent)" strokeWidth="1.75" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Metric({ label, value, hint, tone, sub, trend }: {
  label: string; value: ReactNode; hint?: string; tone?: "money"; sub?: ReactNode; trend?: number[];
}) {
  const body = (
    <Card className="flex flex-col gap-1 p-4">
      <span className="text-xs font-medium text-muted">{label}</span>
      <span className={cn("num text-[1.9rem] leading-tight", tone === "money" && "text-money")}>{value}</span>
      {trend && <Sparkline values={trend} className="mt-1" />}
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </Card>
  );
  return hint ? <Tip label={hint}>{body}</Tip> : body;
}
