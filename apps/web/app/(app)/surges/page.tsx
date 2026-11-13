"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Change, EvidenceList, KV, SymbolLink } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Empty, ErrorState, Field, Input, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { date, kes, label, num, pct, price, toneClass } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";
import type { OpportunityItem } from "@/lib/types";

type Pnl = { exit_price: number; net_pnl: number; return_on_cash: number };
interface Sheet {
  signal: { id: number; type: string; timeframe: string; bar_ts: string; reference_price: number; status: string;
    evidence: { label: string; value: number | string | null; detail?: string }[] };
  instrument: { symbol: string; name: string; is_synthetic: boolean; lot_size: number; trading_status: string };
  position: { shares: number; entry_price: number; consideration: number; buy_fees: Record<string, number>;
    total_cash_needed: number; breakeven_price: number; breakeven_move: number; fee_schedule: string; entry_basis: string };
  stop: { price: number | null; condition: string | null; loss_if_hit: Pnl | null };
  liquidity: { avg_daily_volume_20: number | null; avg_traded_value_20: number | null; max_shares_per_session_at_10pct: number | null;
    sessions_to_fill: number | null; trading_frequency_60: number | null; warning: string | null };
  scenarios: { horizon_sessions: number; n: number; this_stock_n: number; share_profitable_after_costs: number;
    share_positive: number; returns: Record<string, number>; pnl: Record<string, Pnl>; this_stock_median: number | null }[];
  analogue_signal_type: string;
  trend_continuation: { assumption: string; return: number; pnl: Pnl; historical_frequency: number | null; historical_n: number;
    warning: string } | null;
  data_quality: string[]; checklist: string[]; risk_score: number | null; disclaimer: string;
}

const SCEN: [string, string][] = [["bad_p10", "Bad (1 in 10 did worse)"], ["weak_p25", "Weak"], ["typical_median", "Typical (median)"],
  ["good_p75", "Good"], ["great_p90", "Great (1 in 10 did better)"]];

function SurgeSheet({ id }: { id: number }) {
  const [shares, setShares] = useState("");
  const [budget, setBudget] = useState("100000");
  const [mode, setMode] = useState<"budget" | "shares">("budget");
  const q = useApi<Sheet>(`/opportunities/${id}/sheet`, mode === "shares" ? { shares: Number(shares) || undefined } : { budget: Number(budget) || undefined });
  const [h, setH] = useState(5);
  if (q.isLoading) return <Spinner label="Building surge sheet" />;
  if (q.error) return <ErrorState error={q.error} />;
  const s = q.data!;
  const sc = s.scenarios.find((x) => x.horizon_sessions === h) ?? s.scenarios[0];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{s.instrument.symbol}</h2>
            <Badge tone="accent">{label(s.signal.type)}</Badge>{s.instrument.is_synthetic && <Badge tone="warn">synthetic demo</Badge>}</div>
          <p className="text-xs text-muted">{s.instrument.name} · signal {date(s.signal.bar_ts)} at {price(s.signal.reference_price)}</p>
        </div>
        <Link href={`/instruments/${s.instrument.symbol}`}><Button size="sm">Open research page</Button></Link>
      </div>

      <Panel title="1 · Your position">
        <div className="mb-2 flex flex-wrap items-end gap-2">
          <Select aria-label="Size by" value={mode} onChange={(e) => setMode(e.target.value as "budget" | "shares")}>
            <option value="budget">Size by budget (KES)</option><option value="shares">Size by shares</option></Select>
          {mode === "budget" ? <Field label="Budget (KES)"><Input inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} className="w-36" /></Field>
            : <Field label="Shares"><Input inputMode="numeric" value={shares} onChange={(e) => setShares(e.target.value)} className="w-36" /></Field>}
        </div>
        <div className="grid gap-x-6 md:grid-cols-2">
          <KV k="Shares" v={num(s.position.shares, 0)} /><KV k="Entry price (latest close)" v={price(s.position.entry_price)} />
          <KV k="Consideration" v={kes(s.position.consideration)} /><KV k="Buy fees" v={kes(s.position.buy_fees.total)} />
          <KV k="Total cash needed" v={kes(s.position.total_cash_needed)} />
          <KV k="Break-even price (incl. sell fees)" v={`${price(s.position.breakeven_price)} (${pct(s.position.breakeven_move, 1)})`} />
        </div>
      </Panel>

      <Panel title="2 · What happened after similar surges" subtitle={`Past ${label(s.analogue_signal_type)} signals in this data universe, applied to your ${num(s.position.shares, 0)} shares after all fees`}
        actions={<Select aria-label="Holding period" value={h} onChange={(e) => setH(Number(e.target.value))}>
          {s.scenarios.map((x) => <option key={x.horizon_sessions} value={x.horizon_sessions}>Hold {x.horizon_sessions} session{x.horizon_sessions > 1 ? "s" : ""}</option>)}</Select>}>
        {!sc ? <Empty title="No history yet">Base rates appear once past signals of this type have completed their holding periods.</Empty> : (
          <>
            <Table><thead><tr><Th>Scenario</Th><Th align="right">Price move</Th><Th align="right">Exit price</Th><Th align="right">Your net P&L</Th><Th align="right">On cash</Th></tr></thead>
              <tbody>{SCEN.map(([k, l]) => {
                const p = sc.pnl[k];
                if (!p) return null;
                return (<tr key={k}><Td>{l}</Td><Td align="right" num className={toneClass(sc.returns[k])}>{pct(sc.returns[k], 1)}</Td>
                  <Td align="right" num>{price(p.exit_price)}</Td><Td align="right" num className={`font-semibold ${toneClass(p.net_pnl)}`}>{kes(p.net_pnl, 0)}</Td>
                  <Td align="right" num className={toneClass(p.return_on_cash)}>{pct(p.return_on_cash, 1)}</Td></tr>);
              })}</tbody></Table>
            <div className="mt-2 grid gap-x-6 md:grid-cols-3">
              <KV k="Past cases (n)" v={`${sc.n}${sc.this_stock_n ? ` (${sc.this_stock_n} on this stock)` : ""}`} />
              <KV k="Profitable after costs" v={pct(sc.share_profitable_after_costs, 0, false)} />
              <KV k="This stock's median" v={pct(sc.this_stock_median, 1)} />
            </div>
            {sc.n < 30 && <p className="mt-1 text-2xs text-warn">Fewer than 30 past cases: treat these numbers as anecdotal.</p>}
          </>
        )}
        {s.trend_continuation && (
          <div className="mt-3 rounded border border-info/40 bg-info/5 p-2 text-xs">
            <div className="font-semibold">If the current trend continued (hypothetical)</div>
            <p>{s.trend_continuation.assumption} → {pct(s.trend_continuation.return, 1)}, net <span className={`num font-semibold ${toneClass(s.trend_continuation.pnl.net_pnl)}`}>{kes(s.trend_continuation.pnl.net_pnl, 0)}</span>.</p>
            <p className="text-2xs text-muted">{s.trend_continuation.warning} After past surges this happened in{" "}
              {s.trend_continuation.historical_frequency == null ? "— (no history)" : `${pct(s.trend_continuation.historical_frequency, 0, false)} of ${s.trend_continuation.historical_n} cases`}.</p>
          </div>
        )}
      </Panel>

      <div className="grid gap-3 md:grid-cols-2">
        <Panel title="3 · Exit if wrong">
          <KV k="Stop / invalidation" v={price(s.stop.price)} />
          <p className="text-2xs text-muted">{s.stop.condition}</p>
          <KV k="Loss if stopped (after fees)" v={s.stop.loss_if_hit ? <span className="text-down">{kes(s.stop.loss_if_hit.net_pnl, 0)} ({pct(s.stop.loss_if_hit.return_on_cash, 1)})</span> : "—"} />
          <p className="mt-1 text-2xs text-muted">Gaps can make losses larger than the stop suggests.</p>
        </Panel>
        <Panel title="4 · Can you actually trade it?">
          <KV k="Avg daily volume (20)" v={num(s.liquidity.avg_daily_volume_20, 0)} />
          <KV k="Avg daily value (20)" v={kes(s.liquidity.avg_traded_value_20, 0)} />
          <KV k="Max shares/session (10% of volume)" v={num(s.liquidity.max_shares_per_session_at_10pct, 0)} />
          <KV k="Sessions needed to fill" v={s.liquidity.sessions_to_fill ?? "—"} />
          <KV k="Sessions with trades (60)" v={pct(s.liquidity.trading_frequency_60, 0, false)} />
          {s.liquidity.warning && <p className="mt-1 text-2xs text-warn">{s.liquidity.warning}</p>}
        </Panel>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Panel title="Why it was flagged"><EvidenceList items={s.signal.evidence} /></Panel>
        <Panel title="Before you act">
          <ul className="list-disc space-y-1 pl-4 text-xs">{s.checklist.map((c, i) => <li key={i}>{c}</li>)}</ul>
          {s.data_quality.length > 0 && <div className="mt-2 rounded border border-warn/40 bg-warn/10 p-2 text-2xs text-warn">
            <div className="font-semibold">Data-quality flags</div>{s.data_quality.map((d, i) => <div key={i}>{d}</div>)}</div>}
        </Panel>
      </div>
      <p className="text-2xs text-muted">{s.disclaimer}</p>
    </div>
  );
}

function SurgesInner() {
  const [universe] = useUniverse();
  const params = useSearchParams();
  const router = useRouter();
  const [days, setDays] = useState("30");
  const q = useApi<{ items: OpportunityItem[] }>("/surges", { universe: universe ?? undefined, days: Number(days) }, { refetchInterval: 60_000 });
  const sel = Number(params.get("id")) || q.data?.items[0]?.id || null;
  useEffect(() => { if (!params.get("id") && q.data?.items[0]) router.replace(`/surges?id=${q.data.items[0].id}`); }, [q.data, params, router]);
  return (
    <div className="space-y-3">
      <PageHeader title="Surge Radar"
        description="Flags unusual price-and-volume surges as they happen (intraday from 15-minute delayed quotes, and at each end-of-day run) and setups that often precede breakouts. Flagged stocks are added to your 'Surge Watch (auto)' watchlist. Nothing here predicts a surge before it starts."
        actions={<Select aria-label="Period" value={days} onChange={(e) => setDays(e.target.value)}>
          <option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></Select>} />
      <div className="grid gap-3 xl:grid-cols-[24rem_1fr]">
        <Panel title="Flagged" bodyClassName="p-0">
          {q.isLoading ? <Spinner /> : q.error ? <div className="p-3"><ErrorState error={q.error} /></div> : q.data!.items.length === 0 ? <Empty title="No surges in this period" /> : (
            <Table><thead><tr><Th>Instrument</Th><Th>Type</Th><Th>Date</Th><Th align="right">Since</Th></tr></thead>
              <tbody>{q.data!.items.map((s) => (
                <tr key={s.id} onClick={() => router.replace(`/surges?id=${s.id}`)} className={`cursor-pointer hover:bg-raised/50 ${sel === s.id ? "bg-raised" : ""}`}>
                  <Td><SymbolLink symbol={s.instrument.symbol} name={s.instrument.name} synthetic={s.instrument.is_synthetic} /></Td>
                  <Td><Badge tone={s.signal_type === "PRE_BREAKOUT_SQUEEZE" ? "info" : "accent"}>{label(s.signal_type)}</Badge></Td>
                  <Td num>{date(s.bar_ts)}{s.is_backfill && <span className="block text-2xs text-muted" title="Found by replaying history with only the data available that day">replay</span>}</Td><Td align="right"><Change v={s.change_since_signal} /></Td></tr>))}</tbody></Table>
          )}
        </Panel>
        <div>{sel ? <SurgeSheet id={sel} /> : <Empty title="Select a flagged stock" />}</div>
      </div>
    </div>
  );
}

export default function SurgesPage() {
  return <Suspense><SurgesInner /></Suspense>;
}
