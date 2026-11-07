"use client";

import Link from "next/link";
import { use, useMemo, useState } from "react";

import { PriceChart, themeColor, type Level, type Overlay } from "@/components/charts";
import {
  BaseRateTable, Change, DirectionBadge, EvidenceList, FreshnessBadge, KV, ScoreBreakdown, TrendBadge,
} from "@/components/market";
import { Badge, Button, Empty, ErrorState, Panel, Spinner, Table, Tabs, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { compact, date, datetime, label, num, pct, price } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Bar, Evidence, PriceView, Scores, Signal, Snapshot } from "@/lib/types";

interface Detail {
  id: number; symbol: string; name: string; security_type: string; sector: string | null; currency: string;
  trading_status: string; is_synthetic: boolean; isin: string | null; lot_size: number | null; issuer: string | null;
  coupon_rate: number | null; maturity_date: string | null; face_value: number | null;
  price: PriceView; snapshot: Snapshot | null; scores: Scores | null;
  profile: { description: string | null; website: string | null; market_cap: number | null; provider_pe_ratio: number | null;
    fetched_at: string } | null;
  coverage: { provider: string | null; provider_name: string | null; latency_class: string | null; first_bar: string | null;
    last_bar: string | null; daily_bars: number; ohlc_coverage: number | null; volume_coverage: number | null;
    attribution: string | null };
  analytics_applicable: boolean;
}
interface TrendView { classification: string; strength: number | null; evidence: Evidence[]; as_of: string; calc_version: string }
interface Analysis {
  trends: Record<string, TrendView | null>; trend_note: string;
  levels: { timeframe: string; type: "SUPPORT" | "RESISTANCE"; price: number; touches: number; last_touch: string }[];
  signals: Signal[]; instrument_signal_outcomes: { signal_type: string; horizon_bars: number; n: number; mean_forward_return: number }[];
}
interface History { items: Bar[]; adjustments: { ex_date: string; price_factor: number; description: string }[];
  adjustment_policy: string; missing_values_policy: string }
interface Indicators { items: Record<string, number | string | null>[]; calc_version: string | null; note: string }
interface Fundamentals {
  applicable: boolean; applicability_note: string | null; availability: string; note: string | null;
  periods: { period_end: string; period_type: string; published_at: string; currency: string; sources: string[];
    items: Record<string, number> }[];
  metrics: { as_of: string | null; values: Record<string, number | null>; period_end: string | null; calc_version: string | null };
  dividends: { ex_date: string | null; payment_date: string | null; amount: number | null; currency: string | null; status: string | null }[];
}
interface Events {
  corporate_actions: { id: number; type: string; status: string | null; title: string | null; ex_date: string | null;
    effective_date: string | null; payment_date: string | null; amount: number | null; currency: string | null;
    ratio_from: number | null; ratio_to: number | null; source_url: string | null }[];
  news: { id: number; title: string; url: string | null; publisher: string | null; published_at: string | null;
    kind: string; summary: string | null; summary_is_generated: boolean }[];
}

const RANGES = { "6M": 126, "1Y": 252, "3Y": 756, "5Y": 1260, All: 100000 } as const;
const OVERLAYS = [
  { key: "sma_20", label: "SMA20", color: "--info" }, { key: "sma_50", label: "SMA50", color: "--warn" },
  { key: "sma_200", label: "SMA200", color: "--down" }, { key: "ema_20", label: "EMA20", color: "--accent" },
  { key: "bb_upper", label: "BB upper", color: "--muted" }, { key: "bb_lower", label: "BB lower", color: "--muted" },
];
const TF_BARS: Record<string, number> = { "1d": 1, "1w": 5, "1mo": 21 };
const FUND_ROWS: [string, string, "pct" | "num" | "kes"][] = [
  ["revenue", "Revenue", "kes"], ["net_income", "Net income", "kes"], ["eps", "EPS", "num"],
  ["gross_margin", "Gross margin", "pct"], ["operating_margin", "Operating margin", "pct"], ["net_margin", "Net margin", "pct"],
  ["roe", "ROE", "pct"], ["roa", "ROA", "pct"], ["pe_ratio", "P/E", "num"], ["pb_ratio", "P/B", "num"],
  ["ps_ratio", "P/S", "num"], ["ev_ebit", "EV/EBIT", "num"], ["dividend_yield", "Dividend yield", "pct"],
  ["payout_ratio", "Payout ratio", "pct"], ["dividend_years_streak", "Consecutive dividend years", "num"],
  ["dividend_growth_3y", "Dividend growth (3y CAGR)", "pct"], ["debt_to_equity", "Debt / equity", "num"],
  ["current_ratio", "Current ratio", "num"], ["interest_coverage", "Interest coverage", "num"],
  ["operating_cash_flow", "Operating cash flow", "kes"], ["free_cash_flow", "Free cash flow", "kes"],
  ["cash_conversion", "Cash conversion (OCF/NI)", "num"], ["revenue_growth", "Revenue growth", "pct"],
  ["earnings_growth", "Earnings growth", "pct"], ["eps_growth", "EPS growth", "pct"], ["revenue_cagr_3y", "Revenue CAGR (3y)", "pct"],
];

function fmtFund(v: number | null | undefined, kind: "pct" | "num" | "kes") {
  if (v == null) return <span className="text-muted" title="Not available from any source on file">unavailable</span>;
  return kind === "pct" ? pct(v, 1, false) : kind === "kes" ? compact(v) : num(v, 2);
}

function ChartPanel({ symbol, levels }: { symbol: string; levels: Analysis["levels"] }) {
  const [tf, setTf] = useState<"1d" | "1w" | "1mo">("1d");
  const [range, setRange] = useState<keyof typeof RANGES>("1Y");
  const [adjusted, setAdjusted] = useState(true);
  const [on, setOn] = useState<string[]>(["sma_20", "sma_50"]);
  const [showLevels, setShowLevels] = useState(true);
  const hist = useApi<History>(`/instruments/${symbol}/history`, { timeframe: tf, adjusted });
  const ind = useApi<Indicators>(`/instruments/${symbol}/indicators`, { timeframe: tf, names: OVERLAYS.map((o) => o.key).join(","), limit: 6000 });
  const n = Math.ceil(RANGES[range] / TF_BARS[tf]!);
  const bars = useMemo(() => (hist.data?.items ?? []).slice(-n), [hist.data, n]);
  const overlays: Overlay[] = useMemo(() => {
    if (!adjusted || !ind.data) return [];
    const first = bars[0]?.date;
    const rows = ind.data.items.filter((r) => !first || String(r.date) >= first);
    return OVERLAYS.filter((o) => on.includes(o.key)).map((o) => ({ key: o.key, color: themeColor(o.color, 0.9),
      points: rows.map((r) => ({ date: String(r.date), value: typeof r[o.key] === "number" ? (r[o.key] as number) : null })) }));
  }, [ind.data, on, bars, adjusted]);
  const lv: Level[] = useMemo(() => showLevels && adjusted ? levels.filter((l) => l.timeframe === (tf === "1mo" ? "1w" : tf)).slice(0, 6) : [],
    [levels, showLevels, tf, adjusted]);
  return (
    <Panel title="Price" bodyClassName="p-2" actions={<>
      <Tabs value={tf} onChange={setTf} options={[{ value: "1d", label: "D" }, { value: "1w", label: "W" }, { value: "1mo", label: "M" }]} />
      <Tabs value={range} onChange={setRange} options={Object.keys(RANGES).map((r) => ({ value: r as keyof typeof RANGES, label: r }))} />
      <Tabs value={adjusted ? "adj" : "raw"} onChange={(v) => setAdjusted(v === "adj")} options={[{ value: "adj", label: "Adjusted" }, { value: "raw", label: "Raw" }]} />
    </>}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-2xs">
        {OVERLAYS.map((o) => (
          <label key={o.key} className="flex items-center gap-1">
            <input type="checkbox" checked={on.includes(o.key)} disabled={!adjusted}
              onChange={() => setOn(on.includes(o.key) ? on.filter((x) => x !== o.key) : [...on, o.key])} />
            <span style={{ color: themeColor(o.color) }}>{o.label}</span>
          </label>
        ))}
        <label className="flex items-center gap-1"><input type="checkbox" checked={showLevels} onChange={() => setShowLevels(!showLevels)} disabled={!adjusted} />Support/resistance</label>
        {!adjusted && <span className="text-muted">Overlays are computed on adjusted prices and hidden in raw mode.</span>}
      </div>
      {hist.isLoading ? <Spinner /> : hist.error ? <ErrorState error={hist.error} /> : bars.length === 0 ? <Empty title="No price history" /> : (
        <PriceChart bars={bars} overlays={overlays} levels={lv} />
      )}
      {hist.data && (
        <p className="mt-1 text-2xs text-muted">
          {hist.data.adjustment_policy} {hist.data.missing_values_policy}
          {bars.some((b) => !b.ohlc_available) && " Some bars have close only (provider did not report OHLC); the chart switches to a line when OHLC coverage is under 90%."}
          {bars.at(-1)?.is_partial && " The last bar is an incomplete period."}
          {hist.data.adjustments.length > 0 && ` Adjustments: ${hist.data.adjustments.map((a) => `${date(a.ex_date)} ×${a.price_factor.toFixed(4)}`).join(", ")}.`}
        </p>
      )}
    </Panel>
  );
}

function TechnicalsTab({ symbol, analysis }: { symbol: string; analysis: Analysis }) {
  const ind = useApi<Indicators>(`/instruments/${symbol}/indicators`, { timeframe: "1d", limit: 1 });
  const last = ind.data?.items.at(-1);
  const groups: [string, [string, string, "num" | "pct"][]][] = [
    ["Moving averages", [["sma_5", "SMA 5", "num"], ["sma_10", "SMA 10", "num"], ["sma_20", "SMA 20", "num"], ["sma_50", "SMA 50", "num"],
      ["sma_100", "SMA 100", "num"], ["sma_200", "SMA 200", "num"], ["ema_9", "EMA 9", "num"], ["ema_12", "EMA 12", "num"],
      ["ema_20", "EMA 20", "num"], ["ema_26", "EMA 26", "num"], ["ema_50", "EMA 50", "num"]]],
    ["Momentum", [["rsi_7", "RSI 7", "num"], ["rsi_14", "RSI 14", "num"], ["macd", "MACD", "num"], ["macd_signal", "MACD signal", "num"],
      ["macd_hist", "MACD hist", "num"], ["roc_10", "ROC 10", "pct"], ["roc_20", "ROC 20", "pct"], ["momentum_10", "Momentum 10", "num"]]],
    ["Volatility", [["bb_upper", "Bollinger upper", "num"], ["bb_mid", "Bollinger mid", "num"], ["bb_lower", "Bollinger lower", "num"],
      ["bb_width", "Band width", "pct"], ["atr_14", "ATR 14", "num"], ["atr_pct_14", "ATR %", "pct"], ["hv_20", "Hist. vol 20", "pct"],
      ["hv_60", "Hist. vol 60", "pct"], ["adx", "ADX 14", "num"]]],
    ["Volume & structure", [["vol_sma_20", "Volume SMA 20", "num"], ["rel_volume", "Relative volume", "num"], ["obv", "OBV", "num"],
      ["close_high_20_prior", "20-bar closing high", "num"], ["close_high_52w_prior", "52-week closing high", "num"],
      ["drawdown", "Drawdown from peak", "pct"]]],
  ];
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <Panel title="Multi-timeframe trend" className="lg:col-span-2">
        <p className="mb-2 text-2xs text-muted">{analysis.trend_note}</p>
        <div className="grid gap-3 md:grid-cols-3">
          {(["1d", "1w", "1mo"] as const).map((tf) => {
            const t = analysis.trends[tf];
            return (
              <div key={tf} className="rounded border border-line p-2">
                <div className="mb-1 flex items-center justify-between"><span className="text-2xs uppercase text-muted">{{ "1d": "Daily", "1w": "Weekly", "1mo": "Monthly" }[tf]}</span>
                  {t ? <TrendBadge value={t.classification} /> : <Badge>n/a</Badge>}</div>
                {t ? <><p className="mb-1 text-2xs text-muted">As of {date(t.as_of)} · strength {num(t.strength, 0)}</p><EvidenceList items={t.evidence} /></> :
                  <p className="text-2xs text-muted">Not enough completed bars.</p>}
              </div>
            );
          })}
        </div>
      </Panel>
      <Panel title="Support & resistance">
        {analysis.levels.length === 0 ? <Empty title="No clustered levels" /> : (
          <Table><thead><tr><Th>TF</Th><Th>Type</Th><Th align="right">Price</Th><Th align="right">Touches</Th></tr></thead>
            <tbody>{analysis.levels.map((l, i) => (
              <tr key={i}><Td>{l.timeframe}</Td><Td><Badge tone={l.type === "SUPPORT" ? "up" : "down"}>{l.type}</Badge></Td>
                <Td align="right" num>{price(l.price)}</Td><Td align="right" num>{l.touches}</Td></tr>))}</tbody></Table>
        )}
        <p className="mt-1 text-2xs text-muted">Clusters of confirmed swing highs/lows (≥2 touches) from the last 250 bars.</p>
      </Panel>
      <Panel title={`Indicators (daily, ${last ? date(String(last.date)) : "—"})`} className="lg:col-span-3">
        {!last ? <Spinner /> : (
          <div className="grid gap-4 md:grid-cols-4">
            {groups.map(([g, rows]) => (
              <div key={g}><h3 className="mb-1 text-2xs font-semibold uppercase text-muted">{g}</h3>
                {rows.map(([k, l, kind]) => {
                  const v = last[k];
                  return <KV key={k} k={l} v={typeof v === "number" ? (kind === "pct" ? pct(v, 2, false) : num(v, Math.abs(v) >= 1000 ? 0 : 2)) : "insufficient data"} />;
                })}
              </div>
            ))}
          </div>
        )}
        <p className="mt-2 text-2xs text-muted">{ind.data?.note} Version {ind.data?.calc_version ?? "—"}.</p>
      </Panel>
    </div>
  );
}

function FundamentalsTab({ symbol }: { symbol: string }) {
  const f = useApi<Fundamentals>(`/instruments/${symbol}/fundamentals`);
  if (f.isLoading) return <Spinner />;
  if (f.error) return <ErrorState error={f.error} />;
  const d = f.data!;
  if (!d.applicable) return <Empty title="Not applicable">{d.applicability_note}</Empty>;
  const periods = d.periods.slice(-6);
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <Panel title={`Point-in-time metrics (as of ${date(d.metrics.as_of)})`}>
        {d.note && <p className="mb-2 text-xs text-warn">{d.note}</p>}
        {FUND_ROWS.map(([k, l, kind]) => <KV key={k} k={l} v={fmtFund(d.metrics.values[k], kind)} />)}
        <p className="mt-2 text-2xs text-muted">Latest statements: period ending {date(d.metrics.period_end)}. Only figures published
          on or before the as-of date are used. Version {d.metrics.calc_version ?? "—"}.</p>
      </Panel>
      <Panel title="Reported statements (FY)" className="lg:col-span-2" bodyClassName="p-0">
        {periods.length === 0 ? <Empty title="No statements on file">{d.note}</Empty> : (
          <Table>
            <thead><tr><Th>Line item</Th>{periods.map((p) => <Th key={p.period_end} align="right">{p.period_end.slice(0, 4)}</Th>)}</tr></thead>
            <tbody>
              {Array.from(new Set(periods.flatMap((p) => Object.keys(p.items)))).map((li) => (
                <tr key={li}><Td>{label(li)}</Td>{periods.map((p) => <Td key={p.period_end} align="right" num>
                  {p.items[li] == null ? "—" : ["eps", "dps"].includes(li) ? num(p.items[li], 2) : compact(p.items[li])}</Td>)}</tr>
              ))}
              <tr><Td className="text-muted">Published</Td>{periods.map((p) => <Td key={p.period_end} align="right" className="text-muted">{date(p.published_at)}</Td>)}</tr>
              <tr><Td className="text-muted">Source</Td>{periods.map((p) => <Td key={p.period_end} align="right" className="max-w-[10rem] truncate text-2xs text-muted">{p.sources.join("; ")}</Td>)}</tr>
            </tbody>
          </Table>
        )}
      </Panel>
      <Panel title="Dividend history" className="lg:col-span-3" bodyClassName="p-0">
        {d.dividends.length === 0 ? <Empty title="No dividends on file" /> : (
          <Table><thead><tr><Th>Ex-date</Th><Th>Payment</Th><Th align="right">Amount</Th><Th>Status</Th></tr></thead>
            <tbody>{d.dividends.map((v, i) => <tr key={i}><Td num>{date(v.ex_date)}</Td><Td num>{date(v.payment_date)}</Td>
              <Td align="right" num>{v.amount != null ? `${v.currency ?? ""} ${num(v.amount, 2)}` : "—"}</Td><Td>{label(v.status)}</Td></tr>)}</tbody></Table>
        )}
      </Panel>
    </div>
  );
}

function EventsTab({ symbol }: { symbol: string }) {
  const e = useApi<Events>(`/instruments/${symbol}/events`);
  if (e.isLoading) return <Spinner />;
  if (e.error) return <ErrorState error={e.error} />;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Panel title="Corporate actions" bodyClassName="p-0">
        {e.data!.corporate_actions.length === 0 ? <Empty title="None on file" /> : (
          <Table><thead><tr><Th>Type</Th><Th>Title</Th><Th>Ex / effective</Th><Th align="right">Terms</Th></tr></thead>
            <tbody>{e.data!.corporate_actions.map((c) => (
              <tr key={c.id}><Td>{label(c.type)}</Td><Td className="max-w-xs truncate">{c.source_url ? <a className="text-accent" href={c.source_url} target="_blank" rel="noreferrer">{c.title}</a> : c.title}</Td>
                <Td num>{date(c.ex_date ?? c.effective_date)}</Td>
                <Td align="right" num>{c.amount != null ? `${c.currency ?? ""} ${c.amount}` : c.ratio_from ? `${c.ratio_from}→${c.ratio_to}` : ""}</Td></tr>))}</tbody></Table>
        )}
      </Panel>
      <Panel title="News & announcements">
        {e.data!.news.length === 0 ? <Empty title="No news on file" /> : (
          <ul className="space-y-2">{e.data!.news.map((n) => (
            <li key={n.id} className="text-xs">
              <div className="flex items-center gap-1"><Badge>{label(n.kind)}</Badge><span className="text-2xs text-muted">{datetime(n.published_at)} · {n.publisher ?? "Unknown publisher"}</span></div>
              {n.url ? <a href={n.url} target="_blank" rel="noreferrer" className="font-medium hover:text-accent">{n.title}</a> : <span className="font-medium">{n.title}</span>}
              {n.summary && <p className="text-2xs text-muted">{n.summary_is_generated && <Badge tone="info">AI-generated</Badge>} {n.summary}</p>}
            </li>))}</ul>
        )}
      </Panel>
    </div>
  );
}

function SignalsTab({ analysis }: { analysis: Analysis }) {
  const [sel, setSel] = useState<Signal | null>(analysis.signals[0] ?? null);
  const rates = useApi<{ stats: import("@/lib/types").BaseRate[] }>("/opportunities/base-rates");
  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_24rem]">
      <Panel title="Signals on this instrument (latest 40, incl. historical replay)" bodyClassName="p-0">
        {analysis.signals.length === 0 ? <Empty title="No signals" /> : (
          <Table><thead><tr><Th>Bar</Th><Th>Signal</Th><Th>Dir</Th><Th>TF</Th><Th align="right">Ref</Th><Th>Status</Th><Th>Origin</Th></tr></thead>
            <tbody>{analysis.signals.map((s) => (
              <tr key={s.id} onClick={() => setSel(s)} className={`cursor-pointer hover:bg-raised/50 ${sel?.id === s.id ? "bg-raised" : ""}`}>
                <Td num>{date(s.bar_ts)}</Td><Td>{label(s.signal_type)}</Td><Td><DirectionBadge value={s.direction} /></Td><Td>{s.timeframe}</Td>
                <Td align="right" num>{price(s.reference_price)}</Td><Td>{label(s.status)}</Td>
                <Td className="text-2xs text-muted">{s.is_backfill ? "replay" : "live"}</Td></tr>))}</tbody></Table>
        )}
      </Panel>
      <Panel title="Detail">
        {!sel ? <Empty title="Select a signal" /> : (
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">{label(sel.signal_type)}</h3>
            <EvidenceList items={sel.evidence} />
            <p className="text-xs"><span className="text-muted">Invalidation:</span> {sel.invalidation.condition} {sel.invalidation.price != null && `(${price(sel.invalidation.price)})`}</p>
            <ul className="list-disc pl-4 text-2xs text-muted">{sel.risk_factors.map((r, i) => <li key={i}>{r}</li>)}</ul>
            <h4 className="pt-2 text-2xs font-semibold uppercase text-muted">Universe base rates</h4>
            <BaseRateTable rates={rates.data?.stats.filter((r) => r.signal_type === sel.signal_type && r.timeframe === sel.timeframe)} />
          </div>
        )}
      </Panel>
    </div>
  );
}

export default function InstrumentPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol: raw } = use(params);
  const symbol = decodeURIComponent(raw);
  const [tab, setTab] = useState<"overview" | "technicals" | "fundamentals" | "events" | "signals">("overview");
  const d = useApi<Detail>(`/instruments/${encodeURIComponent(symbol)}`);
  const a = useApi<Analysis>(`/instruments/${encodeURIComponent(symbol)}/analysis`);
  const [watchMsg, setWatchMsg] = useState<string | null>(null);
  if (d.isLoading) return <Spinner label={`Loading ${symbol}`} />;
  if (d.error) return <ErrorState error={d.error} onRetry={() => d.refetch()} />;
  const i = d.data!;
  const s = i.snapshot;

  async function addToWatchlist() {
    try {
      let lists = await api<{ id: number; name: string }[]>("/watchlists");
      if (lists.length === 0) lists = [await api<{ id: number; name: string }>("/watchlists", { method: "POST", body: { name: "My watchlist" } })];
      await api(`/watchlists/${lists[0]!.id}/items`, { method: "POST", body: { symbol: i.symbol } });
      setWatchMsg(`Added to "${lists[0]!.name}"`);
    } catch (e) {
      setWatchMsg(e instanceof Error ? e.message : "Failed");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight">{i.symbol}</h1>
            <Badge>{label(i.security_type)}</Badge>
            {i.sector && <Badge>{i.sector}</Badge>}
            {i.trading_status !== "ACTIVE" && <Badge tone="down">{label(i.trading_status)}</Badge>}
            {i.is_synthetic && <Badge tone="warn">Synthetic — not a real security</Badge>}
          </div>
          <p className="text-sm text-muted">{i.name}{i.isin ? ` · ISIN ${i.isin}` : ""}{i.lot_size ? ` · lot ${i.lot_size}` : ""}</p>
        </div>
        <div className="text-right">
          <div className="flex items-center justify-end gap-2">
            <span className="num text-2xl font-semibold">{price(i.price.price)}</span>
            <span className="text-xs text-muted">{i.currency}</span>
            <Change v={s?.change_pct} className="text-sm" />
          </div>
          <div className="flex items-center justify-end gap-2 text-2xs text-muted">
            <FreshnessBadge f={i.price.freshness} />
            <span>{i.price.source === "quote" ? "Quote" : "Daily close"} · {datetime(i.price.freshness.as_of)}</span>
          </div>
          {i.price.last_traded_date && <div className="text-2xs text-muted">Last session with trades: {date(i.price.last_traded_date)}</div>}
          <div className="mt-1 flex justify-end gap-1">
            <Button size="sm" onClick={addToWatchlist}>+ Watchlist</Button>
            <Link href={`/alerts?symbol=${i.symbol}`}><Button size="sm">Create alert</Button></Link>
            <Link href={`/portfolio?symbol=${i.symbol}`}><Button size="sm">Paper trade</Button></Link>
          </div>
          {watchMsg && <p className="text-2xs text-muted">{watchMsg}</p>}
        </div>
      </div>

      <Tabs value={tab} onChange={setTab} options={[{ value: "overview", label: "Overview" }, { value: "technicals", label: "Technicals" },
        { value: "fundamentals", label: "Fundamentals" }, { value: "events", label: "Events & news" }, { value: "signals", label: "Signals" }]} />

      {tab === "overview" && (
        <div className="grid gap-3 xl:grid-cols-[1fr_22rem]">
          <div className="space-y-3">
            <ChartPanel symbol={i.symbol} levels={a.data?.levels ?? []} />
            {!i.analytics_applicable && <Panel><p className="text-xs">Technical and equity analytics are not applied to {label(i.security_type)} instruments.
              {i.coupon_rate != null && ` Coupon ${pct(i.coupon_rate, 2, false)}, maturity ${date(i.maturity_date)}, face value ${num(i.face_value, 0)}.`}</p></Panel>}
            <div className="grid gap-3 md:grid-cols-3">
              <Panel title="Performance">
                {[["1 week", s?.return_1w], ["1 month", s?.return_1m], ["3 months", s?.return_3m], ["6 months", s?.return_6m],
                  ["1 year", s?.return_1y], ["Year to date", s?.return_ytd]].map(([k, v]) => <KV key={k as string} k={k} v={<Change v={v as number | null} />} />)}
                <p className="mt-1 text-2xs text-muted">Split-adjusted price returns; dividends excluded.</p>
              </Panel>
              <Panel title="Risk">
                <KV k="Volatility (20 / 60)" v={`${pct(s?.volatility_20, 0, false)} / ${pct(s?.volatility_60, 0, false)}`} />
                <KV k="ATR % (14)" v={pct(s?.atr_pct_14, 2, false)} />
                <KV k="Max drawdown (1y)" v={pct(s?.max_drawdown_1y, 1)} />
                <KV k="From 52w high" v={pct(s?.drawdown_from_high, 1)} />
                <KV k="52w range" v={`${price(s?.low_52w)} – ${price(s?.high_52w)}`} />
                <KV k="RSI (14)" v={num(s?.rsi_14, 1)} />
              </Panel>
              <Panel title="Liquidity & valuation">
                <KV k="Volume (last)" v={compact(s?.volume)} />
                <KV k="Avg volume (20)" v={compact(s?.avg_volume_20)} />
                <KV k="Relative volume" v={num(s?.relative_volume, 2)} />
                <KV k="Avg traded value (20)" v={compact(s?.avg_traded_value_20)} />
                <KV k="Sessions with trades (60)" v={pct(s?.trading_frequency_60, 0, false)} />
                <KV k="P/E (computed)" v={num(s?.pe_ratio, 1)} />
                <KV k="Dividend yield (TTM)" v={pct(s?.dividend_yield, 2, false)} />
                <KV k="Market cap (provider)" v={compact(i.profile?.market_cap)} />
              </Panel>
            </div>
            {i.profile?.description && <Panel title="Profile"><p className="text-xs leading-relaxed">{i.profile.description}</p>
              {i.profile.website && <a href={i.profile.website} target="_blank" rel="noreferrer" className="text-2xs text-accent">{i.profile.website}</a>}</Panel>}
          </div>
          <div className="space-y-3">
            <Panel title="Scores">
              {i.scores ? <div className="space-y-4">
                <ScoreBreakdown name="Trading opportunity" block={i.scores.trading} />
                <ScoreBreakdown name="Investment quality" block={i.scores.quality} />
                <ScoreBreakdown name="Risk" block={i.scores.risk} inverse />
                <p className="text-2xs text-muted">Cross-sectional rankings within the data universe, not forecasts. See
                  <Link className="text-accent" href="/settings"> methodology & calibration</Link>.</p>
              </div> : <Empty title="Not scored">Scores need a computed snapshot and a universe of at least 5 instruments.</Empty>}
            </Panel>
            <Panel title="Data coverage">
              <KV k="Provider" v={i.coverage.provider_name ?? "—"} />
              <KV k="Latency class" v={label(i.coverage.latency_class)} />
              <KV k="History" v={`${date(i.coverage.first_bar)} → ${date(i.coverage.last_bar)}`} />
              <KV k="Daily bars" v={i.coverage.daily_bars} />
              <KV k="OHLC reported" v={pct(i.coverage.ohlc_coverage, 0, false)} />
              <KV k="Volume reported" v={pct(i.coverage.volume_coverage, 0, false)} />
              {i.coverage.attribution && <p className="mt-1 text-2xs text-muted">{i.coverage.attribution}</p>}
            </Panel>
          </div>
        </div>
      )}
      {tab === "technicals" && (a.data ? <TechnicalsTab symbol={i.symbol} analysis={a.data} /> : <Spinner />)}
      {tab === "fundamentals" && <FundamentalsTab symbol={i.symbol} />}
      {tab === "events" && <EventsTab symbol={i.symbol} />}
      {tab === "signals" && (a.data ? <SignalsTab analysis={a.data} /> : <Spinner />)}
    </div>
  );
}
