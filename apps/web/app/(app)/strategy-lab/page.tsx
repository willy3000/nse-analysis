"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { LineChart, themeColor } from "@/components/charts";
import { KV } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Empty, ErrorState, Field, Input, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { date, kes, label, num, pct, toneClass } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";

type Cond = { left: string; op: string; right: number | string };
interface Def {
  timeframe: "1d" | "1w"; entry: Cond[]; exit: Cond[]; stop_loss_pct: number | null; take_profit_pct: number | null;
  max_holding_bars: number | null; position_sizing: { method: string; fraction: number; max_positions: number };
  universe: { symbols: string[] | null; security_types: string[]; min_trading_frequency: number; synthetic: boolean };
}
interface Strategy { id: number; name: string; description: string | null; definition: Def; version: number; backtest_runs: number }
interface Features { features: string[]; fundamental_features: string[]; operators: string[]; templates: { name: string; definition: Partial<Def> }[]; notes: string[] }
type Metrics = Record<string, number | string | null | boolean>;
interface RunSummary { id: number; strategy_id: number | null; status: string; created_at: string; error: string | null;
  warnings: string[]; headline: Metrics | null; params: Record<string, unknown> }
interface RunFull extends RunSummary {
  metrics: { overall: Metrics; segments: Record<string, Metrics>; benchmark: Metrics | null; diagnostics: Record<string, number>;
    segment_bounds: Record<string, string>; fee_rate_per_side_estimate: number; open_positions: Record<string, unknown>[] } | null;
  equity_curve: { date: string; equity: number; benchmark: number | null; segment: string }[] | null;
  trades: { symbol: string; segment: string; entry_date: string; entry_price: number; exit_date: string; exit_price: number;
    quantity: number; net_pnl: number; return_pct: number; bars_held: number; exit_reason: string }[];
  data_fingerprint: string | null; calc_version: string | null; strategy_snapshot: Def;
}

const blankDef = (synthetic: boolean): Def => ({
  timeframe: "1d", entry: [{ left: "sma_20", op: "crosses_above", right: "sma_50" }],
  exit: [{ left: "sma_20", op: "crosses_below", right: "sma_50" }], stop_loss_pct: 0.12, take_profit_pct: null,
  max_holding_bars: null, position_sizing: { method: "equal_weight", fraction: 0.2, max_positions: 5 },
  universe: { symbols: null, security_types: ["EQUITY", "ETF", "REIT"], min_trading_frequency: 0.5, synthetic },
});

function CondRow({ c, features, ops, onChange, onRemove }: { c: Cond; features: string[]; ops: string[];
  onChange: (c: Cond) => void; onRemove: () => void }) {
  const rightIsFeature = typeof c.right === "string";
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Select aria-label="Left operand" value={c.left} onChange={(e) => onChange({ ...c, left: e.target.value })}>
        {features.map((f) => <option key={f}>{f}</option>)}</Select>
      <Select aria-label="Operator" value={c.op} onChange={(e) => onChange({ ...c, op: e.target.value })}>
        {ops.map((o) => <option key={o}>{o}</option>)}</Select>
      <Select aria-label="Right operand type" value={rightIsFeature ? "feature" : "value"}
        onChange={(e) => onChange({ ...c, right: e.target.value === "feature" ? "sma_50" : 0 })}>
        <option value="value">value</option><option value="feature">feature</option></Select>
      {rightIsFeature ? (
        <Select aria-label="Right feature" value={c.right as string} onChange={(e) => onChange({ ...c, right: e.target.value })}>
          {features.map((f) => <option key={f}>{f}</option>)}</Select>
      ) : <Input aria-label="Right value" className="w-24" inputMode="decimal" value={String(c.right)}
        onChange={(e) => onChange({ ...c, right: e.target.value === "" ? 0 : Number(e.target.value) })} />}
      <Button size="sm" variant="ghost" onClick={onRemove} aria-label="Remove condition">✕</Button>
    </div>
  );
}

function MetricsTable({ m }: { m: RunFull["metrics"] }) {
  if (!m) return null;
  const cols: [string, Metrics | null][] = [["Overall", m.overall], ["In-sample", m.segments.in_sample ?? null],
    ["Validation", m.segments.validation ?? null], ["Out-of-sample", m.segments.out_of_sample ?? null], ["Benchmark", m.benchmark]];
  const rows: [string, string, "pct" | "num" | "int"][] = [["total_return", "Total return", "pct"], ["cagr", "CAGR", "pct"],
    ["annualized_return", "Annualised return", "pct"], ["volatility", "Volatility", "pct"], ["sharpe", "Sharpe", "num"],
    ["sortino", "Sortino", "num"], ["max_drawdown", "Max drawdown", "pct"], ["trades", "Closed trades", "int"],
    ["win_rate", "Win rate", "pct"], ["avg_win_pct", "Avg win", "pct"], ["avg_loss_pct", "Avg loss", "pct"],
    ["profit_factor", "Profit factor", "num"], ["payoff_ratio", "Win/loss ratio", "num"], ["exposure", "Exposure", "pct"],
    ["avg_bars_held", "Avg bars held", "num"]];
  return (
    <Table>
      <thead><tr><Th>Metric</Th>{cols.map(([n]) => <Th key={n} align="right">{n}</Th>)}</tr></thead>
      <tbody>{rows.map(([k, l, kind]) => (
        <tr key={k}><Td>{l}</Td>{cols.map(([n, v]) => {
          const x = v?.[k];
          return <Td key={n} align="right" num className={kind === "pct" && typeof x === "number" ? toneClass(k === "max_drawdown" ? null : x) : ""}>
            {typeof x !== "number" ? "—" : kind === "pct" ? pct(x, 1) : kind === "int" ? x : num(x, 2)}</Td>;
        })}</tr>))}
        <tr><Td className="text-muted">Period</Td>{cols.map(([n, v]) => <Td key={n} align="right" className="text-2xs text-muted">
          {v?.start ? `${date(String(v.start))} → ${date(String(v.end))}` : "—"}</Td>)}</tr>
      </tbody>
    </Table>
  );
}

function RunView({ id }: { id: number }) {
  const r = useApi<RunFull>(`/backtests/${id}`, undefined, {
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 3000 : false) });
  const curve = useMemo(() => {
    const ec = r.data?.equity_curve ?? [];
    const step = Math.max(1, Math.floor(ec.length / 800));
    const pts = ec.filter((_, i) => i % step === 0 || i === ec.length - 1);
    let peak = 0;
    return {
      eq: [{ name: "Strategy", color: themeColor("--accent"), points: pts.map((p) => ({ date: p.date, value: p.equity })) },
        { name: "Benchmark", color: themeColor("--muted"), points: pts.map((p) => ({ date: p.date, value: p.benchmark })) }],
      dd: [{ name: "Drawdown", color: themeColor("--down"), points: pts.map((p) => { peak = Math.max(peak, p.equity); return { date: p.date, value: p.equity / peak - 1 }; }) }],
    };
  }, [r.data]);
  if (r.isLoading) return <Spinner />;
  if (r.error) return <ErrorState error={r.error} />;
  const d = r.data!;
  if (d.status === "queued" || d.status === "running") return <Spinner label={`Backtest ${d.status} in the worker`} />;
  if (d.status === "failed") return <ErrorState error={new Error(d.error ?? "Backtest failed")} />;
  const m = d.metrics!;
  return (
    <div className="space-y-3">
      {d.warnings.length > 0 && <div className="space-y-1">{d.warnings.map((w, i) => <p key={i} className="rounded border border-warn/40 bg-warn/10 p-2 text-xs text-warn">{w}</p>)}</div>}
      <Panel title="Performance by chronological segment" bodyClassName="p-0"
        subtitle={`Validation from ${date(m.segment_bounds.validation_start)}, out-of-sample from ${date(m.segment_bounds.out_of_sample_start)}. Judge the strategy on out-of-sample results.`}
        actions={<a href={`/api/v1/backtests/${d.id}/report.csv`} className="text-2xs text-accent" download>Download CSV report</a>}>
        <MetricsTable m={m} />
      </Panel>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Equity curve vs benchmark"><LineChart series={curve.eq} /></Panel>
        <Panel title="Drawdown"><LineChart series={curve.dd} percent /></Panel>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="Costs & execution">
          <KV k="Est. cost per side" v={pct(m.fee_rate_per_side_estimate, 2, false)} />
          {Object.entries(m.diagnostics).map(([k, v]) => <KV key={k} k={label(k)} v={typeof v === "number" && k.includes("paid") || k.includes("received") || k.includes("withheld") ? kes(v) : String(v)} />)}
        </Panel>
        <Panel title="Reproducibility" className="lg:col-span-2">
          <KV k="Engine version" v={d.calc_version ?? "—"} />
          <KV k="Data fingerprint" v={<span className="text-2xs">{d.data_fingerprint?.slice(0, 24)}…</span>} />
          <KV k="Execution" v={`${String(d.params.execution)} · slippage ${String(d.params.slippage_bps)} bps · max ${pct(Number(d.params.max_participation), 0, false)} of volume`} />
          <KV k="Open positions at end" v={m.open_positions.length} />
          <p className="mt-1 text-2xs text-muted">Re-running with the same strategy snapshot, parameters and data fingerprint reproduces these results exactly.
            Historical simulation only — past performance does not indicate future results.</p>
        </Panel>
      </div>
      <Panel title={`Trades (${d.trades.length})`} bodyClassName="p-0">
        {d.trades.length === 0 ? <Empty title="No closed trades" /> : (
          <Table className="max-h-96"><thead><tr><Th>Symbol</Th><Th>Segment</Th><Th>Entry</Th><Th align="right">Price</Th><Th>Exit</Th>
            <Th align="right">Price</Th><Th align="right">Qty</Th><Th align="right">Net P&L</Th><Th align="right">Return</Th><Th align="right">Bars</Th><Th>Reason</Th></tr></thead>
            <tbody>{d.trades.map((t, i) => (
              <tr key={i}><Td>{t.symbol}</Td><Td className="text-muted">{label(t.segment)}</Td><Td num>{date(t.entry_date)}</Td><Td align="right" num>{num(t.entry_price, 2)}</Td>
                <Td num>{date(t.exit_date)}</Td><Td align="right" num>{num(t.exit_price, 2)}</Td><Td align="right" num>{t.quantity}</Td>
                <Td align="right" num className={toneClass(t.net_pnl)}>{num(t.net_pnl, 0)}</Td><Td align="right" num className={toneClass(t.return_pct)}>{pct(t.return_pct, 1)}</Td>
                <Td align="right" num>{t.bars_held}</Td><Td>{label(t.exit_reason)}</Td></tr>))}</tbody></Table>
        )}
      </Panel>
    </div>
  );
}

export default function StrategyLabPage() {
  const qc = useQueryClient();
  const [universe] = useUniverse();
  const meta = useApi<Features>("/strategies/features");
  const strategies = useApi<Strategy[]>("/strategies");
  const runs = useApi<RunSummary[]>("/backtests", undefined, { refetchInterval: 5000 });
  const [def, setDef] = useState<Def>(blankDef(universe === "synthetic"));
  const [name, setName] = useState("My strategy");
  const [editing, setEditing] = useState<number | null>(null);
  const [params, setParams] = useState({ start_date: "2018-01-01", end_date: new Date().toISOString().slice(0, 10), initial_capital: 1_000_000,
    slippage_bps: 25, max_participation: 0.1, execution: "next_open", include_dividends: true, dividend_tax_rate: 0.05,
    risk_free_rate: 0, fee_schedule_code: "KE_EQUITY_2026" });
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDef((d) => ({ ...d, universe: { ...d.universe, synthetic: universe === "synthetic" } })), [universe]);

  const features = meta.data?.features ?? [];
  const ops = meta.data?.operators ?? [];
  const updCond = (k: "entry" | "exit", i: number, c: Cond) => setDef({ ...def, [k]: def[k].map((x, j) => (j === i ? c : x)) });

  async function save(): Promise<number | null> {
    setError(null);
    try {
      const body = { name, description: null, definition: def };
      const s = editing ? await api<Strategy>(`/strategies/${editing}`, { method: "PUT", body })
        : await api<Strategy>("/strategies", { method: "POST", body });
      setEditing(s.id);
      await qc.invalidateQueries({ queryKey: ["/strategies"] });
      return s.id;
    } catch (e) { setError(e); return null; }
  }
  async function run() {
    setBusy(true);
    const id = await save();
    if (id) {
      try {
        const r = await api<{ id: number }>("/backtests", { method: "POST", body: { strategy_id: id, params } });
        setSelectedRun(r.id);
        await qc.invalidateQueries({ queryKey: ["/backtests"] });
      } catch (e) { setError(e); }
    }
    setBusy(false);
  }

  return (
    <div className="space-y-3">
      <PageHeader title="Strategy lab" description="Define rule-based strategies and test them on history with NSE transaction costs, slippage, liquidity limits and corporate actions. Results are split chronologically into in-sample, validation and out-of-sample periods." />
      <div className="grid gap-3 xl:grid-cols-[30rem_1fr]">
        <div className="space-y-3">
          <Panel title="Saved strategies" bodyClassName="p-0">
            {(strategies.data ?? []).length === 0 ? <Empty title="No saved strategies" /> : (
              <Table><tbody>{strategies.data!.map((s) => (
                <tr key={s.id} className={`cursor-pointer hover:bg-raised/50 ${editing === s.id ? "bg-raised" : ""}`}
                  onClick={() => { setEditing(s.id); setName(s.name); setDef({ ...blankDef(false), ...s.definition }); }}>
                  <Td>{s.name}</Td><Td className="text-muted">v{s.version}</Td><Td align="right" className="text-muted">{s.backtest_runs} runs</Td>
                  <Td align="right"><Button size="sm" variant="ghost" aria-label={`Delete ${s.name}`} onClick={async (e) => {
                    e.stopPropagation(); await api(`/strategies/${s.id}`, { method: "DELETE" }); if (editing === s.id) setEditing(null);
                    qc.invalidateQueries({ queryKey: ["/strategies"] }); }}>✕</Button></Td></tr>))}</tbody></Table>
            )}
          </Panel>
          <Panel title={editing ? `Edit strategy #${editing}` : "New strategy"} actions={
            <Select aria-label="Template" value="" onChange={(e) => { const t = meta.data?.templates[Number(e.target.value)]; if (t) { setDef({ ...blankDef(universe === "synthetic"), ...t.definition } as Def); setName(t.name); setEditing(null); } }}>
              <option value="">Templates…</option>{(meta.data?.templates ?? []).map((t, i) => <option key={i} value={i}>{t.name}</option>)}</Select>}>
            <div className="space-y-3">
              <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Timeframe"><Select value={def.timeframe} onChange={(e) => setDef({ ...def, timeframe: e.target.value as "1d" | "1w" })}>
                  <option value="1d">Daily</option><option value="1w">Weekly</option></Select></Field>
                <Field label="Min. trading frequency" hint="Share of sessions with trades"><Input inputMode="decimal" value={def.universe.min_trading_frequency}
                  onChange={(e) => setDef({ ...def, universe: { ...def.universe, min_trading_frequency: Number(e.target.value) || 0 } })} /></Field>
              </div>
              <fieldset className="space-y-1"><legend className="text-xs font-medium text-muted">Entry — all conditions must hold</legend>
                {def.entry.map((c, i) => <CondRow key={i} c={c} features={features} ops={ops} onChange={(x) => updCond("entry", i, x)}
                  onRemove={() => setDef({ ...def, entry: def.entry.filter((_, j) => j !== i) })} />)}
                <Button size="sm" onClick={() => setDef({ ...def, entry: [...def.entry, { left: "rsi_14", op: "<", right: 30 }] })}>+ Entry condition</Button>
              </fieldset>
              <fieldset className="space-y-1"><legend className="text-xs font-medium text-muted">Exit — any condition triggers</legend>
                {def.exit.map((c, i) => <CondRow key={i} c={c} features={features} ops={ops} onChange={(x) => updCond("exit", i, x)}
                  onRemove={() => setDef({ ...def, exit: def.exit.filter((_, j) => j !== i) })} />)}
                <Button size="sm" onClick={() => setDef({ ...def, exit: [...def.exit, { left: "rsi_14", op: ">", right: 70 }] })}>+ Exit condition</Button>
              </fieldset>
              <div className="grid grid-cols-3 gap-2">
                <Field label="Stop loss %"><Input inputMode="decimal" value={def.stop_loss_pct != null ? def.stop_loss_pct * 100 : ""}
                  onChange={(e) => setDef({ ...def, stop_loss_pct: e.target.value ? Number(e.target.value) / 100 : null })} /></Field>
                <Field label="Take profit %"><Input inputMode="decimal" value={def.take_profit_pct != null ? def.take_profit_pct * 100 : ""}
                  onChange={(e) => setDef({ ...def, take_profit_pct: e.target.value ? Number(e.target.value) / 100 : null })} /></Field>
                <Field label="Max holding bars"><Input inputMode="numeric" value={def.max_holding_bars ?? ""}
                  onChange={(e) => setDef({ ...def, max_holding_bars: e.target.value ? Number(e.target.value) : null })} /></Field>
                <Field label="Sizing"><Select value={def.position_sizing.method} onChange={(e) => setDef({ ...def, position_sizing: { ...def.position_sizing, method: e.target.value } })}>
                  <option value="equal_weight">Equal weight</option><option value="fixed_fraction">Fixed fraction</option></Select></Field>
                <Field label="Fraction"><Input inputMode="decimal" value={def.position_sizing.fraction}
                  onChange={(e) => setDef({ ...def, position_sizing: { ...def.position_sizing, fraction: Number(e.target.value) || 0.1 } })} /></Field>
                <Field label="Max positions"><Input inputMode="numeric" value={def.position_sizing.max_positions}
                  onChange={(e) => setDef({ ...def, position_sizing: { ...def.position_sizing, max_positions: Number(e.target.value) || 1 } })} /></Field>
              </div>
              <Field label="Symbols (optional, comma separated)"><Input value={def.universe.symbols?.join(",") ?? ""}
                onChange={(e) => setDef({ ...def, universe: { ...def.universe, symbols: e.target.value ? e.target.value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean) : null } })} /></Field>
              <p className="text-2xs text-muted">Universe: {def.universe.synthetic ? "synthetic development data" : "real market data"} (follows the universe selector).</p>
            </div>
          </Panel>
          <Panel title="Backtest parameters">
            <div className="grid grid-cols-2 gap-2">
              <Field label="Start"><Input type="date" value={params.start_date} onChange={(e) => setParams({ ...params, start_date: e.target.value })} /></Field>
              <Field label="End"><Input type="date" value={params.end_date} onChange={(e) => setParams({ ...params, end_date: e.target.value })} /></Field>
              <Field label="Initial capital (KES)"><Input inputMode="numeric" value={params.initial_capital} onChange={(e) => setParams({ ...params, initial_capital: Number(e.target.value) || 0 })} /></Field>
              <Field label="Slippage (bps)"><Input inputMode="numeric" value={params.slippage_bps} onChange={(e) => setParams({ ...params, slippage_bps: Number(e.target.value) || 0 })} /></Field>
              <Field label="Max % of session volume"><Input inputMode="decimal" value={params.max_participation * 100} onChange={(e) => setParams({ ...params, max_participation: (Number(e.target.value) || 1) / 100 })} /></Field>
              <Field label="Execution"><Select value={params.execution} onChange={(e) => setParams({ ...params, execution: e.target.value })}>
                <option value="next_open">Next session open</option><option value="next_close">Next session close</option></Select></Field>
              <Field label="Dividend WHT %"><Input inputMode="decimal" value={params.dividend_tax_rate * 100} onChange={(e) => setParams({ ...params, dividend_tax_rate: (Number(e.target.value) || 0) / 100 })} /></Field>
              <Field label="Risk-free rate %"><Input inputMode="decimal" value={params.risk_free_rate * 100} onChange={(e) => setParams({ ...params, risk_free_rate: (Number(e.target.value) || 0) / 100 })} /></Field>
            </div>
            <label className="mt-2 flex items-center gap-1 text-xs"><input type="checkbox" checked={params.include_dividends} onChange={() => setParams({ ...params, include_dividends: !params.include_dividends })} />Credit cash dividends (net of WHT)</label>
            <ul className="mt-2 list-disc space-y-0.5 pl-4 text-2xs text-muted">{(meta.data?.notes ?? []).map((n, i) => <li key={i}>{n}</li>)}</ul>
            {error ? <div className="mt-2"><ErrorState error={error} /></div> : null}
            <div className="mt-2 flex gap-2"><Button onClick={save}>Save</Button><Button variant="primary" onClick={run} loading={busy}>Save & run backtest</Button></div>
          </Panel>
        </div>
        <div className="space-y-3">
          <Panel title="Runs — compare" bodyClassName="p-0">
            {(runs.data ?? []).length === 0 ? <Empty title="No backtests yet" /> : (
              <Table className="max-h-64"><thead><tr><Th>#</Th><Th>Strategy</Th><Th>Status</Th><Th>Created</Th><Th align="right">Return</Th><Th align="right">CAGR</Th>
                <Th align="right">Sharpe</Th><Th align="right">Max DD</Th><Th align="right">Trades</Th></tr></thead>
                <tbody>{runs.data!.map((r) => (
                  <tr key={r.id} onClick={() => setSelectedRun(r.id)} className={`cursor-pointer hover:bg-raised/50 ${selectedRun === r.id ? "bg-raised" : ""}`}>
                    <Td num>{r.id}</Td><Td>{strategies.data?.find((s) => s.id === r.strategy_id)?.name ?? "ad hoc"}</Td>
                    <Td><Badge tone={r.status === "succeeded" ? "up" : r.status === "failed" ? "down" : "info"}>{r.status}</Badge></Td>
                    <Td num className="text-muted">{date(r.created_at)}</Td>
                    <Td align="right" num className={toneClass(r.headline?.total_return as number)}>{pct(r.headline?.total_return as number, 1)}</Td>
                    <Td align="right" num>{pct(r.headline?.cagr as number, 1)}</Td><Td align="right" num>{num(r.headline?.sharpe as number, 2)}</Td>
                    <Td align="right" num>{pct(r.headline?.max_drawdown as number, 1)}</Td><Td align="right" num>{String(r.headline?.trades ?? "—")}</Td></tr>))}</tbody></Table>
            )}
          </Panel>
          {selectedRun ? <RunView id={selectedRun} /> : <Empty title="Select or run a backtest" />}
        </div>
      </div>
    </div>
  );
}
