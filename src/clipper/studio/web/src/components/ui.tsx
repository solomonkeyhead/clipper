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

export function CopyButton({ text, what, label, size = "sm", variant = "secondary", keys, className }: {
  text: string; what: string; label?: string; size?: Size; variant?: Variant; keys?: string; className?: string;
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
  return <Tip label={`Copy ${what.toLowerCase()}`} keys={keys}>{button}</Tip>;
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
    <div className="fade-in flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-line px-6 py-14 text-center">
      <div className="grid size-11 place-items-center rounded-full bg-surface-2 text-muted">{icon}</div>
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
      <RadixSwitch.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow-1 transition-transform duration-[var(--dur-base)] ease-standard data-[state=checked]:translate-x-[18px]" />
    </RadixSwitch.Root>
  );
}

export function PageHeader({ title, subtitle, actions }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Metric({ label, value, hint, tone, sub }: {
  label: string; value: ReactNode; hint?: string; tone?: "money"; sub?: ReactNode;
}) {
  const body = (
    <Card className="flex flex-col gap-1 p-4">
      <span className="text-xs font-medium text-muted">{label}</span>
      <span className={cn("tabular text-2xl font-semibold", tone === "money" && "text-money")}>{value}</span>
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </Card>
  );
  return hint ? <Tip label={hint}>{body}</Tip> : body;
}
