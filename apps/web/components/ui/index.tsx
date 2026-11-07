"use client";

import { clsx, type ClassValue } from "clsx";
import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode,
  type SelectHTMLAttributes } from "react";
import { twMerge } from "tailwind-merge";

import { ApiError } from "@/lib/api";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

type Variant = "primary" | "ghost" | "outline" | "danger";
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: "sm" | "md"; loading?: boolean }>(
  function Button({ className, variant = "outline", size = "md", loading, disabled, children, ...rest }, ref) {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "inline-flex items-center justify-center gap-1.5 rounded border font-medium transition-colors",
          "disabled:cursor-not-allowed disabled:opacity-50",
          size === "sm" ? "h-7 px-2 text-xs" : "h-8 px-3 text-sm",
          variant === "primary" && "border-accent bg-accent text-bg hover:bg-accent/90",
          variant === "outline" && "border-line bg-panel hover:bg-raised",
          variant === "ghost" && "border-transparent hover:bg-raised",
          variant === "danger" && "border-down/40 text-down hover:bg-down/10",
          className,
        )}
        {...rest}
      >
        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
        {children}
      </button>
    );
  },
);

export function Panel({ title, actions, children, className, bodyClassName, subtitle }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
  bodyClassName?: string }) {
  return (
    <section className={cn("rounded-md border border-line bg-panel", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
          <div className="min-w-0">
            {title && <h2 className="truncate text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>}
            {subtitle && <p className="truncate text-2xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
        </header>
      )}
      <div className={cn("p-3", bodyClassName)}>{children}</div>
    </section>
  );
}

const BADGE_TONES = {
  neutral: "border-line text-muted",
  up: "border-up/40 bg-up/10 text-up",
  down: "border-down/40 bg-down/10 text-down",
  warn: "border-warn/40 bg-warn/10 text-warn",
  info: "border-info/40 bg-info/10 text-info",
  accent: "border-accent/40 bg-accent/10 text-accent",
} as const;
export type Tone = keyof typeof BADGE_TONES;

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode;
  className?: string; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px",
      "text-2xs font-semibold uppercase tracking-wide", BADGE_TONES[tone], className)}>
      {children}
    </span>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest }, ref) {
  return <input ref={ref} className={cn("h-8 w-full rounded border border-line bg-bg px-2 text-sm",
    "placeholder:text-muted/70", className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest }, ref) {
  return <select ref={ref} className={cn("h-8 rounded border border-line bg-bg px-2 text-sm", className)} {...rest}>
    {children}</select>;
});

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: ReactNode;
  className?: string }) {
  return (
    <label className={cn("flex flex-col gap-1 text-xs", className)}>
      <span className="font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="text-2xs text-muted">{hint}</span>}
    </label>
  );
}

export function Tabs<T extends string>({ value, onChange, options, className }: { value: T;
  onChange: (v: T) => void; options: { value: T; label: string }[]; className?: string }) {
  return (
    <div role="tablist" className={cn("inline-flex rounded border border-line bg-bg p-0.5", className)}>
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={o.value === value} onClick={() => onChange(o.value)}
          className={cn("rounded px-2 py-0.5 text-xs", o.value === value ? "bg-raised font-semibold text-fg"
            : "text-muted hover:text-fg")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-4 text-xs text-muted">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1 p-6 text-center text-xs text-muted">
      <Inbox className="h-5 w-5" aria-hidden />
      <p className="font-medium text-fg">{title}</p>
      {children && <div className="max-w-md">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded border border-down/40 bg-down/5 p-3 text-xs">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-down" aria-hidden />
      <div className="flex-1">
        <p className="font-semibold text-down">{e ? `${e.code}` : "Error"}</p>
        <p className="text-fg">{e?.message ?? (error instanceof Error ? error.message : "Something went wrong")}</p>
      </div>
      {onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function Stat({ label, value, sub, className, valueClassName }: { label: string; value: ReactNode;
  sub?: ReactNode; className?: string; valueClassName?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="truncate text-2xs uppercase tracking-wide text-muted">{label}</div>
      <div className={cn("num truncate text-base font-semibold", valueClassName)}>{value}</div>
      {sub && <div className="truncate text-2xs text-muted">{sub}</div>}
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  );
}

export function Th({ children, className, onClick, sorted, align = "left" }: { children?: ReactNode;
  className?: string; onClick?: () => void; sorted?: "asc" | "desc" | null; align?: "left" | "right" }) {
  return (
    <th scope="col" aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
      className={cn("sticky top-0 z-[1] whitespace-nowrap border-b border-line bg-panel px-2 py-1.5 font-medium",
        "text-2xs uppercase tracking-wide text-muted", align === "right" ? "text-right" : "text-left",
        onClick && "cursor-pointer select-none hover:text-fg", className)}
      onClick={onClick}>
      {children}{sorted === "asc" ? " ▲" : sorted === "desc" ? " ▼" : ""}
    </th>
  );
}

export function Td({ children, className, align = "left", num: isNum, title }: { children?: ReactNode; className?: string;
  align?: "left" | "right"; num?: boolean; title?: string }) {
  return <td title={title} className={cn("whitespace-nowrap border-b border-line/60 px-2 py-1.5", align === "right" && "text-right",
    isNum && "num", className)}>{children}</td>;
}
