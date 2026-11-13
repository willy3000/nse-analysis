"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { Badge, cn, type Tone } from "@/components/ui";
import { age, label, pct, price, toneClass } from "@/lib/format";
import type { BaseRate, Evidence, Freshness, ScoreBlock } from "@/lib/types";

const FRESH_TONE: Record<string, Tone> = {
  LIVE: "up", DELAYED: "info", END_OF_DAY: "neutral", HISTORICAL: "warn", STALE: "down", UNAVAILABLE: "down",
};
const FRESH_LABEL: Record<string, string> = {
  LIVE: "Live", DELAYED: "Delayed", END_OF_DAY: "End of day", HISTORICAL: "Historical", STALE: "Stale",
  UNAVAILABLE: "Unavailable",
};

/** Always-visible provenance label. Delayed or historical data can never render as "Live". */
export function FreshnessBadge({ f, compact }: { f: Freshness | null | undefined; compact?: boolean }) {
  if (!f) return <Badge tone="down">Unavailable</Badge>;
  const title = [f.detail, f.reason ? `Reason: ${label(f.reason)}` : null, f.provider ? `Source: ${f.provider}` : null,
    f.as_of ? `Observed: ${new Date(f.as_of).toUTCString()}` : null].filter(Boolean).join("\n");
  return (
    <Badge tone={FRESH_TONE[f.status] ?? "neutral"} title={title}>
      {FRESH_LABEL[f.status] ?? f.status}
      {!compact && f.status === "DELAYED" && f.age_seconds != null && <span className="font-normal">· {age(f.age_seconds)}</span>}
      {!compact && f.reason && f.status === "STALE" && <span className="font-normal normal-case">· {label(f.reason)}</span>}
    </Badge>
  );
}

const TREND_TONE: Record<string, Tone> = {
  STRONG_BULLISH: "up", EMERGING_BULLISH: "up", BULLISH_PULLBACK: "accent", SIDEWAYS: "neutral",
  WEAKENING_BULLISH: "warn", EMERGING_BEARISH: "down", STRONG_BEARISH: "down", POTENTIAL_REVERSAL: "info",
  INSUFFICIENT_DATA: "neutral",
};

export function TrendBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-muted">—</span>;
  return <Badge tone={TREND_TONE[value] ?? "neutral"}>{label(value)}</Badge>;
}

export function DirectionBadge({ value }: { value: string }) {
  return <Badge tone={value === "BULLISH" ? "up" : value === "BEARISH" ? "down" : "neutral"}>{label(value)}</Badge>;
}

export function Change({ v, className }: { v: number | null | undefined; className?: string }) {
  return <span className={cn("num", toneClass(v), className)}>{pct(v)}</span>;
}

export function SymbolLink({ symbol, name, synthetic }: { symbol: string; name?: string; synthetic?: boolean }) {
  return (
    <Link href={`/instruments/${encodeURIComponent(symbol)}`} className="group flex min-w-0 flex-col">
      <span className="flex items-center gap-1 font-semibold text-fg group-hover:text-accent">
        {symbol}
        {synthetic && <Badge tone="warn" title="Fictitious development instrument" className="px-1 py-0">syn</Badge>}
      </span>
      {name && <span className="max-w-[16rem] truncate text-2xs text-muted">{name}</span>}
    </Link>
  );
}

/** 0-100 score bar. `inverse` colours high values as bad (risk). */
export function ScoreMeter({ value, inverse, title }: { value: number | null | undefined; inverse?: boolean;
  title?: string }) {
  if (value == null) return <span className="text-2xs text-muted" title="Insufficient data">n/a</span>;
  const good = inverse ? 100 - value : value;
  const color = good >= 66 ? "bg-up" : good >= 40 ? "bg-warn" : "bg-down";
  return (
    <span className="inline-flex items-center gap-1.5" title={title}>
      <span className="relative h-1.5 w-12 overflow-hidden rounded bg-raised" aria-hidden>
        <span className={cn("absolute inset-y-0 left-0", color)} style={{ width: `${Math.max(2, value)}%` }} />
      </span>
      <span className="num w-7 text-right">{value.toFixed(0)}</span>
    </span>
  );
}

export function ScoreBreakdown({ name, block, inverse }: { name: string; block: ScoreBlock | undefined;
  inverse?: boolean }) {
  if (!block) return null;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">{name}</h3>
        <span className="num text-lg font-semibold">{block.score == null ? "n/a" : block.score.toFixed(0)}</span>
      </div>
      <p className="text-2xs text-muted">Higher = {block.higher_is}. Coverage {(block.coverage * 100).toFixed(0)}% of
        component weight{block.insufficient_data ? " — insufficient data to score" : ""}.</p>
      <ul className="space-y-1">
        {block.components.map((c) => (
          <li key={c.key} className="grid grid-cols-[1fr_auto_auto] items-center gap-2 text-xs" title={c.description}>
            <span className={cn(!c.available && "text-muted line-through decoration-muted/50")}>
              {c.label} <span className="text-2xs text-muted">({(c.weight * 100).toFixed(0)}%)</span>
            </span>
            <span className="num text-2xs text-muted">{typeof c.raw === "number" ? c.raw.toFixed(3) : c.raw ?? "—"}</span>
            <ScoreMeter value={c.sub_score} inverse={inverse} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EvidenceList({ items }: { items: Evidence[] }) {
  return (
    <ul className="space-y-1 text-xs">
      {items.map((e, i) => (
        <li key={i} className="flex gap-2">
          <span className={cn("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", e.stance === "bullish" ? "bg-up"
            : e.stance === "bearish" ? "bg-down" : e.stance === "caution" ? "bg-warn" : "bg-muted")} />
          <span className="flex-1">
            <span className="font-medium">{e.label}</span>
            {e.value !== null && e.value !== undefined && e.value !== "" && (
              <span className="num ml-1 text-muted">{typeof e.value === "number" ? +e.value.toFixed(4) : String(e.value)}</span>
            )}
            {e.detail && <span className="block text-2xs text-muted">{e.detail}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function BaseRateTable({ rates }: { rates: BaseRate[] | undefined }) {
  if (!rates || rates.length === 0)
    return <p className="text-2xs text-muted">No tracked outcomes for this signal type yet.</p>;
  return (
    <div className="space-y-1">
      <table className="w-full text-2xs">
        <thead className="text-muted">
          <tr><th className="text-left font-medium">Horizon</th><th className="text-right font-medium">n</th>
            <th className="text-right font-medium">Median</th><th className="text-right font-medium">Mean</th><th className="text-right font-medium">Baseline</th>
            <th className="text-right font-medium">Hit rate</th></tr>
        </thead>
        <tbody>
          {rates.map((r) => (
            <tr key={r.horizon_bars} className={cn(!r.sufficient_sample && "opacity-50")}>
              <td>{r.horizon_bars} bars</td>
              <td className="num text-right">{r.n}</td>
              <td className={cn("num text-right", toneClass(r.median_return))}>{pct(r.median_return, 1)}</td>
              <td className={cn("num text-right", toneClass(r.mean_return))}>{pct(r.mean_return, 1)}</td>
              <td className="num text-right text-muted">{pct(r.baseline_mean_return, 1)}</td>
              <td className="num text-right">{pct(r.hit_rate, 0, false)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-2xs text-muted">Observed forward returns of past signals vs. all bars (baseline), before
        ~4% round-trip costs. Faded rows have fewer than 30 observations. Not a forecast.</p>
    </div>
  );
}

export function PriceCell({ value, freshness }: { value: number | null | undefined; freshness?: Freshness }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="num">{price(value)}</span>
      {freshness && <FreshnessBadge f={freshness} compact />}
    </span>
  );
}

export function KV({ k, v, className }: { k: ReactNode; v: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 border-b border-line/50 py-1 text-xs", className)}>
      <span className="text-muted">{k}</span>
      <span className="num text-right">{v}</span>
    </div>
  );
}
