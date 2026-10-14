import * as RadixSwitch from "@radix-ui/react-switch";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, Check, Copy, Info, MoreHorizontal } from "lucide-react";
import { forwardRef, useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { toast } from "sonner";
import { cn, copyText } from "@/lib/utils";

/* ---------- Button ---------- */

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "icon";

/** A button's look, for a link that acts as one too (D196): the same long class string was copied a dozen times. */
export function buttonClass(variant: Variant = "secondary", size: Size = "md") {
  return cn(
    "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-sm font-semibold",
    "transition-[background-color,border-color,color,transform] duration-[var(--dur-fast)] ease-standard",
    "active:translate-y-px disabled:pointer-events-none disabled:opacity-40",
    // 44px on a touch screen (REDESIGN_NOTES 7): 28px buttons were hard to hit on a phone.
    size === "sm" && "h-8 px-3 text-xs pointer-coarse:h-11 pointer-coarse:px-3.5",
    size === "md" && "h-10 px-4 text-sm pointer-coarse:h-11",
    size === "icon" && "size-9 text-sm pointer-coarse:size-11",
    // A primary that can't go yet isn't lit (D213): dimmed amber still reads as the one thing to do.
    variant === "primary" && "bg-accent text-accent-fg hover:bg-accent-hover disabled:bg-surface-3 disabled:text-subtle disabled:opacity-100",
    // Solid grey slabs, as a catalog's "More info" button (D215): no outline boxes.
    variant === "secondary" && "bg-surface-3 text-fg hover:bg-[color-mix(in_oklch,var(--surface-3),var(--text)_10%)]",
    variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-fg",
    variant === "danger" && "bg-surface-3 text-danger hover:bg-[color-mix(in_oklch,var(--surface-3),var(--danger)_14%)]",
  );
}

/** A click whose action returns a promise (it waits on Clipper) takes no second click until it's done, and
 *  past 100 ms the button's words give way to a spinner at the same size (D192): every click answers at once,
 *  the button pressed is the one that spins, and a double click can't send it twice. */
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: Size;
}>(({ variant = "secondary", size = "md", className, onClick, ...props }, ref) => {
  const [busy, setBusy] = useState<0 | 1 | 2>(0);   // idle, waiting, waiting long enough to show it
  const waiting = useRef(false);   // set at once: a second click can come before the page redraws
  return (
  <button
    ref={ref}
    // Not a form's submit unless it says so: a helper button inside a form
    // ("Fill in the form") once saved the campaign it was meant to fill.
    type="button"
    aria-busy={busy > 0 || undefined}
    data-busy={busy === 2 || undefined}
    onClick={(e) => {
      if (waiting.current) return;
      const result = onClick?.(e) as unknown;
      if (!(result instanceof Promise)) return;
      waiting.current = true;
      setBusy(1);
      const show = setTimeout(() => setBusy(2), 100);
      const done = () => { clearTimeout(show); waiting.current = false; setBusy(0); };
      // An action that handles its own errors never rejects; one that didn't would have failed in silence.
      result.then(done, (err: unknown) => { done(); toast.error(err instanceof Error ? err.message : String(err)); });
    }}
    className={cn(
      busy === 2 && "relative",   // for the spinner; a button placed absolutely keeps its place
      buttonClass(variant, size),
      className,
    )}
    {...props}
  />
  );
});
Button.displayName = "Button";

/* ---------- Tooltip (teaches the shortcut) ---------- */

export function Tip({ label, keys, children, side = "top" }: {
  label: ReactNode; keys?: string; children: ReactNode; side?: "top" | "bottom" | "left" | "right";
}) {
  // A tap opens it on a touch screen (D200), for a few seconds: a tooltip opened by hover and keyboard only.
  const [open, setOpen] = useState(false);
  const touched = useRef(false);
  useEffect(() => {
    if (!open || !touched.current) return;
    const t = setTimeout(() => setOpen(false), 3000);
    return () => clearTimeout(t);
  }, [open]);
  return (
    <RadixTooltip.Root delayDuration={500} open={open} onOpenChange={setOpen}>
      {/* Focusable (D199): a plain span's explanation (n/a, Estimated, Median) opened by hover only; a button keeps its own. */}
      <RadixTooltip.Trigger asChild tabIndex={0}
                            onPointerDown={(e) => { touched.current = e.pointerType === "touch"; }}
                            onClick={() => { if (touched.current) setOpen(true); }}>{children}</RadixTooltip.Trigger>
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
  <kbd className={cn("rounded-[3px] border border-line-strong px-1.5 py-px font-mono text-[12px] text-muted", className)}>
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

/** A word of metadata (D215): coloured text, no pill. The wall sets "9.1 · 0:41 · Ready to post" as plain words;
 *  a row of rounded chips was the stock dashboard look. */
export const Chip = ({ children, tone = "neutral", className, title }: {
  children: ReactNode; tone?: "neutral" | "accent" | "money" | "warning" | "success" | "danger" | "info";
  className?: string; title?: string;
}) => (
  <span
    title={title}
    className={cn(
      "inline-flex min-h-6 items-center gap-1.5 text-xs font-semibold",
      tone === "neutral" && "text-muted",
      // Not amber: a tag is never the next thing to do (D120).
      tone === "accent" && "text-fg",
      tone === "money" && "text-money",
      tone === "warning" && "text-warning",
      tone === "success" && "text-success",
      tone === "danger" && "text-danger",
      tone === "info" && "text-info",
      className,
    )}
  >
    {children}
  </span>
);

const STATUS_TONE = { ready: "info", posted: "warning", submitted: "success", skipped: "neutral" } as const;
/** The one vocabulary for where a clip is (D143): the chip, the tabs, the cards and the dashboard all say
 *  these words. "To submit" is a clip that is posted but whose link still needs submitting to its
 *  campaign; your own channel has no submitting, so there it stays "Posted". */
export const STATUS_LABEL = { ready: "Ready to post", posted: "To submit", submitted: "Submitted", skipped: "Skipped" } as const;
export const STATUS_MEANING = {
  ready: "Made, and waiting for you to post it on your account.",
  posted: "Posted on your account. Its link still has to be submitted to the campaign.",
  submitted: "Its link has been submitted to the campaign.",
  skipped: "You passed on it. It stays here, and Clipper learns from why.",
} as const;

/** `waiting`: posted, and the brief wants more views before its link is submitted (D196): it said "To submit" while
 *  the tab above it said "To submit 0". */
export function StatusChip({ status, own = false, waiting = false }: { status: string; own?: boolean; waiting?: boolean }) {
  const s = (status in STATUS_TONE ? status : "ready") as keyof typeof STATUS_TONE;
  const wait = waiting && s === "posted" && !own;
  return (
    <Chip tone={wait ? "neutral" : STATUS_TONE[s]} className="whitespace-nowrap"
          title={wait ? "Posted. The brief wants more views on it before its link is submitted." : STATUS_MEANING[s]}>
      <span className="size-1.5 shrink-0 rounded-full bg-current" aria-hidden />
      {wait ? "Waiting for views" : own && s === "posted" ? "Posted" : STATUS_LABEL[s]}
    </Chip>
  );
}

/** "What do these mean?" beside any set of status tabs. */
export function StatusLegend() {
  return (
    <Tip side="bottom" label={
      <span className="flex flex-col gap-1 py-0.5">
        {(Object.keys(STATUS_LABEL) as (keyof typeof STATUS_LABEL)[]).map((k) => (
          <span key={k}><b>{STATUS_LABEL[k]}</b>: {STATUS_MEANING[k]}</span>
        ))}
      </span>
    }>
      <button type="button" aria-label="What the statuses mean" className="grid size-9 place-items-center rounded-sm text-subtle hover:text-fg pointer-coarse:size-11">
        <Info className="size-4" />
      </button>
    </Tip>
  );
}

/** A way back up: the list a detail page belongs to. */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="mb-3 inline-flex min-h-8 items-center gap-1.5 text-sm text-muted hover:text-fg pointer-coarse:min-h-11">
      <ArrowLeft className="size-3.5" /> {children}
    </Link>
  );
}

/* ---------- Surfaces ---------- */

export const Card = ({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("rounded-md bg-surface-1", className)} {...props}>{children}</div>
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
    <div className="fade-in flex flex-col items-start gap-3 py-10 sm:flex-row sm:items-center sm:gap-5">
      {/* An empty poster: a clip waiting to happen. */}
      <div className="grid aspect-[9/16] w-14 shrink-0 place-items-center rounded-md border-2 border-dashed border-line-strong text-muted [&_svg]:size-5">{icon}</div>
      <div className="flex flex-col items-start gap-2">
        <div className="text-md font-semibold">{title}</div>
        {body && <div className="max-w-md text-sm text-muted">{body}</div>}
        {action}
      </div>
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
      // 24px to see, 44px to hit (REDESIGN_NOTES 7): the hit area reaches past the switch.
      className="relative h-6 w-10 shrink-0 rounded-full bg-surface-3 transition-colors duration-[var(--dur-base)] before:absolute before:-inset-2.5 before:content-[''] data-[state=checked]:bg-fg"
    >
      <RadixSwitch.Thumb className="block size-5 translate-x-0.5 rounded-full bg-[var(--text-muted)] shadow-1 transition-transform duration-[var(--dur-base)] ease-standard data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-bg" />
    </RadixSwitch.Root>
  );
}

/** Clipper's mark: a 9:16 poster with its title bar (D215; the lime caption bar of D102 went with the old look). */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 18 32" className={cn("h-7 w-auto shrink-0", className)} aria-hidden>
      <rect x="1.25" y="1.25" width="15.5" height="29.5" rx="2.5" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <rect x="4" y="19" width="10" height="3.2" rx="0.6" fill="currentColor" />
      <rect x="4" y="24" width="6" height="2.2" rx="0.6" fill="currentColor" opacity="0.55" />
    </svg>
  );
}

/** A page's title in the condensed face, upper case (D215). One highlight colour, only for the next action (D120), so no word of it is lit. */
export function CaptionTitle({ text, as: Tag = "h1", className }: {
  text: ReactNode; as?: "h1" | "h2"; className?: string;
}) {
  return <Tag className={cn("cap text-balance", className)}>{text}</Tag>;
}

export function PageHeader({ title, subtitle, actions }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <CaptionTitle text={title} className="text-[clamp(2.1rem,3.6vw,3.1rem)]" />
        {subtitle && <p className="mt-2 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A small line of how a number moved, no axes: the shape, not the values. Grey, or money's sage for money (D212). */
export function Sparkline({ values, className, color = "var(--text-muted)" }: { values: number[]; className?: string; color?: string }) {
  if (values.length < 2) return null;
  const W = 120, H = 32, lo = Math.min(...values), hi = Math.max(...values);
  const x = (i: number) => (i / (values.length - 1)) * W;
  const y = (v: number) => H - 2 - ((v - lo) / Math.max(1, hi - lo)) * (H - 4);
  const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={cn("h-8 w-full overflow-visible", className)} aria-hidden>
      <path d={`${line}L${W},${H}L0,${H}Z`} fill={color} opacity="0.12" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.75" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** A number and its label, set flat on the page (D215): the row of four boxed metric tiles was the stock dashboard. */
export function Metric({ label, value, hint, tone, sub, trend, to, search }: {
  label: string; value: ReactNode; hint?: string; tone?: "money"; sub?: ReactNode; trend?: number[];
  /** Makes it a link to where the number comes from (D143). */
  to?: string; search?: Record<string, string>;
}) {
  const card = (
    <div className={cn("flex h-full flex-col gap-1 rounded-md py-1", to && "group")}>
      <span className="text-xs font-semibold text-muted group-hover:text-fg">{label}</span>
      <span className={cn("num text-[2.1rem] leading-none", tone === "money" && "text-money")}>{value}</span>
      {trend && <Sparkline values={trend} className="mt-1" />}
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </div>
  );
  const body = to ? <Link to={to} search={search} className="block rounded-md">{card}</Link> : card;
  return hint ? <Tip label={hint}>{body}</Tip> : body;
}

/** A "More" menu of named actions (D215): the actions a card or a bar doesn't need in view, each in words. Icon-only
 *  buttons (archive, a lightbulb for "Back to ideas", a bin) said nothing until hovered (REDESIGN_NOTES 5). A
 *  `<details>`, so it opens with a tap, Enter or Space, and an item that is picked closes it. */
export function MoreMenu({ label = "More", children, align = "left", size = "md", className }: {
  label?: string; children: ReactNode; align?: "left" | "right"; size?: "sm" | "md"; className?: string;
}) {
  return (
    <details className={cn("relative", className)}
             onClick={(e) => { if ((e.target as HTMLElement).closest("[data-menu-item]")) e.currentTarget.removeAttribute("open"); }}>
      <summary className={cn(buttonClass("ghost", size), "cursor-pointer list-none [&::-webkit-details-marker]:hidden")}>
        <MoreHorizontal className="size-4" /> {label}
      </summary>
      <div className={cn("absolute z-30 mt-1 flex w-64 flex-col rounded-md bg-surface-2 p-1 text-sm shadow-3", align === "right" ? "right-0" : "left-0")}>
        {children}
      </div>
    </details>
  );
}

export function MenuItem({ onClick, disabled, danger, children }: {
  onClick: () => void; disabled?: boolean; danger?: boolean; children: ReactNode;
}) {
  return (
    <button type="button" data-menu-item disabled={disabled} onClick={onClick}
            className={cn("flex min-h-10 items-center gap-2.5 rounded-sm px-3 text-left font-semibold hover:bg-surface-3 disabled:opacity-50 pointer-coarse:min-h-11 [&_svg]:size-4 [&_svg]:shrink-0",
              danger ? "text-danger" : "text-fg")}>
      {children}
    </button>
  );
}
