import { useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";
import { Card, Switch } from "./ui";

const control =
  "w-full rounded-sm border border-line bg-surface-1 px-3 text-sm placeholder:text-subtle " +
  "transition-colors hover:border-line-strong focus:border-accent focus:outline-none";

export function Section({ title, hint, children, id }: {
  title: string; hint?: ReactNode; children: ReactNode; id?: string;
}) {
  return (
    <Card className="flex flex-col gap-4 p-5" id={id}>
      <div>
        <h2 className="text-md font-semibold">{title}</h2>
        {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
      </div>
      {children}
    </Card>
  );
}

/** A labelled control; `hint` sits under the label, `optional` marks what can stay empty. */
export function Field({ label, hint, optional, children, className }: {
  label: string; hint?: ReactNode; optional?: boolean; children: (id: string) => ReactNode; className?: string;
}) {
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-sm font-medium">
        {label}{optional && <span className="ml-1.5 text-xs font-normal text-subtle">optional</span>}
      </label>
      {children(id)}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function TextInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(control, "h-9", className)} />;
}

export function TextArea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(control, "resize-y py-2 leading-relaxed", className)} />;
}

/** A number field that holds `null` when empty. */
export function NumberInput({ value, onChange, prefix, suffix, ...props }: {
  value: number | null | undefined; onChange: (v: number | null) => void; prefix?: string; suffix?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "prefix">) {
  return (
    <div className="relative flex items-center">
      {prefix && <span className="pointer-events-none absolute left-3 text-sm text-muted">{prefix}</span>}
      <input
        {...props}
        type="number"
        inputMode="decimal"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        className={cn(control, "tabular h-9", prefix && "pl-7", suffix && "pr-12")}
      />
      {suffix && <span className="pointer-events-none absolute right-3 text-xs text-muted">{suffix}</span>}
    </div>
  );
}

/** One item per line, edited as text. */
export function LinesInput({ value, onChange, ...props }: {
  value: string[]; onChange: (v: string[]) => void;
} & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange">) {
  return (
    <TextArea {...props} value={value.join("\n")}
              onChange={(e) => onChange(e.target.value.split("\n"))} />
  );
}

export function SwitchRow({ label, hint, checked, onChange }: {
  label: string; hint?: ReactNode; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
      </div>
      <div className="pt-0.5"><Switch label={label} checked={checked} onChange={onChange} /></div>
    </div>
  );
}

/** Pick one of a few options, shown side by side. */
export function Segmented<T extends string>({ value, onChange, options, label }: {
  value: T; onChange: (v: T) => void; options: [T, string, string?][]; label: string;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label={label}>
      {options.map(([key, title, hint]) => (
        <button key={key} type="button" role="radio" aria-checked={value === key} onClick={() => onChange(key)}
          className={cn("flex flex-col items-start gap-0.5 rounded-md border px-3 py-2.5 text-left transition-colors",
            value === key ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-2")}>
          <span className="text-sm font-medium">{title}</span>
          {hint && <span className="text-xs text-muted">{hint}</span>}
        </button>
      ))}
    </div>
  );
}
