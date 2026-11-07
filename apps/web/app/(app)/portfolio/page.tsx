"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { LineChart, themeColor } from "@/components/charts";
import { Change, FreshnessBadge, KV, SymbolLink } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Empty, ErrorState, Field, Input, Panel, Select, Spinner, Stat, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { date, kes, label, num, pct, price, toneClass } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Freshness, InstrumentRef } from "@/lib/types";

interface Position { instrument: InstrumentRef; quantity: number; avg_cost: number; cost_basis: number; price: number | null;
  freshness: Freshness; market_value: number | null; unrealized_pnl: number | null; unrealized_pct: number | null;
  realized_pnl: number; dividends_net: number; weight: number | null }
interface Summary { id: number; name: string; kind: string; cash: number; market_value: number; total_value: number;
  net_contributions: number; total_return: number | null; unrealized_pnl: number; realized_pnl: number; dividends_net: number;
  dividend_tax_withheld: number; fees_paid: number; positions: Position[]; all_positions_priced: boolean;
  allocation: { by_sector: Record<string, number>; by_security_type: Record<string, number>; cash: number | null };
  concentration: { top_position_weight: number | null; hhi: number | null; positions: number; warning: string | null } }
interface Detail extends Summary { transactions: { id: number; txn_type: string; trade_date: string; symbol: string | null;
  quantity: number | null; price: number | null; gross_amount: number; fees: number; tax: number; net_cash_flow: number;
  notes: string | null; fee_breakdown: Record<string, number> | null }[] }
interface Perf { series: { date: string; value: number; twr: number; benchmark: number | null }[]; twr?: number;
  benchmark_return?: number | null; benchmark?: string; method?: string; note?: string }

const today = () => new Date().toISOString().slice(0, 10);

function TxnForm({ p, defaultSymbol, onDone }: { p: Detail; defaultSymbol: string; onDone: () => void }) {
  const [t, setT] = useState({ txn_type: "BUY", trade_date: today(), symbol: defaultSymbol, quantity: "", price: "", amount: "", fees: "", notes: "" });
  const [error, setError] = useState<unknown>(null);
  const [preview, setPreview] = useState<Record<string, number> | null>(null);
  const [busy, setBusy] = useState(false);
  const trade = t.txn_type === "BUY" || t.txn_type === "SELL";
  useEffect(() => {
    const q = Number(t.quantity), pr = Number(t.price);
    if (!trade || !q || !pr || t.fees) { setPreview(null); return; }
    const h = setTimeout(() => api<{ breakdown: Record<string, number> }>("/portfolio/fee-preview", { method: "POST", body: { consideration: q * pr } })
      .then((r) => setPreview(r.breakdown)).catch(() => setPreview(null)), 300);
    return () => clearTimeout(h);
  }, [t.quantity, t.price, t.fees, trade]);
  async function submit() {
    setBusy(true); setError(null);
    try {
      await api("/portfolio/transactions", { method: "POST", body: {
        portfolio_id: p.id, txn_type: t.txn_type, trade_date: t.trade_date, symbol: t.symbol || null,
        quantity: trade ? Number(t.quantity) : null, price: trade ? Number(t.price) : null,
        amount: !trade ? Number(t.amount) : null, fees: t.fees ? Number(t.fees) : null, notes: t.notes || null } });
      setT({ ...t, quantity: "", price: "", amount: "", fees: "", notes: "" });
      onDone();
    } catch (e) { setError(e); }
    setBusy(false);
  }
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <Field label="Type"><Select value={t.txn_type} onChange={(e) => setT({ ...t, txn_type: e.target.value })}>
          {["BUY", "SELL", "DIVIDEND", "DEPOSIT", "WITHDRAWAL", "FEE"].map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
        <Field label="Date"><Input type="date" max={today()} value={t.trade_date} onChange={(e) => setT({ ...t, trade_date: e.target.value })} /></Field>
        {(trade || t.txn_type === "DIVIDEND") && <Field label="Symbol"><Input value={t.symbol} onChange={(e) => setT({ ...t, symbol: e.target.value.toUpperCase() })} /></Field>}
        {trade ? <>
          <Field label="Quantity"><Input inputMode="numeric" value={t.quantity} onChange={(e) => setT({ ...t, quantity: e.target.value })} /></Field>
          <Field label="Price (KES)"><Input inputMode="decimal" value={t.price} onChange={(e) => setT({ ...t, price: e.target.value })} /></Field>
          <Field label="Fees override" hint="Blank = fee schedule"><Input inputMode="decimal" value={t.fees} onChange={(e) => setT({ ...t, fees: e.target.value })} /></Field>
        </> : <Field label={t.txn_type === "DIVIDEND" ? "Gross amount (KES)" : "Amount (KES)"}><Input inputMode="decimal" value={t.amount} onChange={(e) => setT({ ...t, amount: e.target.value })} /></Field>}
        <Field label="Notes" className="col-span-2"><Input value={t.notes} onChange={(e) => setT({ ...t, notes: e.target.value })} /></Field>
      </div>
      {preview && <div className="rounded border border-line bg-raised/50 p-2 text-2xs">
        <div className="mb-1 font-semibold">Estimated costs (fee schedule)</div>
        {Object.entries(preview).map(([k, v]) => <KV key={k} k={label(k)} v={kes(v)} className={k === "total" ? "font-semibold" : ""} />)}
      </div>}
      {t.txn_type === "DIVIDEND" && <p className="text-2xs text-muted">Withholding tax of {pct(0.05, 0, false)} (portfolio setting) is deducted automatically.</p>}
      {error ? <ErrorState error={error} /> : null}
      <Button variant="primary" onClick={submit} loading={busy}>Record simulated transaction</Button>
    </div>
  );
}

function PortfolioView({ id, defaultSymbol }: { id: number; defaultSymbol: string }) {
  const qc = useQueryClient();
  const d = useApi<Detail>(`/portfolio/${id}`);
  const perf = useApi<Perf>(`/portfolio/${id}/performance`);
  const refresh = () => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("/portfolio") });
  const series = useMemo(() => perf.data?.series.length ? [
    { name: "Portfolio (TWR)", color: themeColor("--accent"), points: perf.data.series.map((s) => ({ date: s.date, value: s.twr })) },
    { name: "Benchmark", color: themeColor("--muted"), points: perf.data.series.map((s) => ({ date: s.date, value: s.benchmark })) },
  ] : [], [perf.data]);
  if (d.isLoading) return <Spinner />;
  if (d.error) return <ErrorState error={d.error} />;
  const p = d.data!;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Panel><Stat label="Total value" value={kes(p.total_value, 0)} sub={p.all_positions_priced ? undefined : "some positions unpriced"} /></Panel>
        <Panel><Stat label="Cash" value={kes(p.cash, 0)} /></Panel>
        <Panel><Stat label="Total return" value={<Change v={p.total_return} />} sub="vs net contributions" /></Panel>
        <Panel><Stat label="Unrealised P&L" value={<span className={toneClass(p.unrealized_pnl)}>{num(p.unrealized_pnl, 0)}</span>} /></Panel>
        <Panel><Stat label="Realised P&L" value={<span className={toneClass(p.realized_pnl)}>{num(p.realized_pnl, 0)}</span>} /></Panel>
        <Panel><Stat label="Dividends (net)" value={num(p.dividends_net, 0)} sub={`WHT ${num(p.dividend_tax_withheld, 0)}`} /></Panel>
        <Panel><Stat label="Fees paid" value={num(p.fees_paid, 0)} /></Panel>
      </div>
      <div className="grid gap-3 xl:grid-cols-[1fr_22rem]">
        <div className="space-y-3">
          <Panel title="Positions" bodyClassName="p-0">
            {p.positions.length === 0 ? <Empty title="No open positions" /> : (
              <Table><thead><tr><Th>Instrument</Th><Th align="right">Qty</Th><Th align="right">Avg cost</Th><Th align="right">Price</Th><Th>Freshness</Th>
                <Th align="right">Value</Th><Th align="right">Unrealised</Th><Th align="right">Weight</Th></tr></thead>
                <tbody>{p.positions.map((x) => (
                  <tr key={x.instrument.id}><Td><SymbolLink symbol={x.instrument.symbol} name={x.instrument.name} synthetic={x.instrument.is_synthetic} /></Td>
                    <Td align="right" num>{num(x.quantity, 0)}</Td><Td align="right" num>{price(x.avg_cost)}</Td><Td align="right" num>{price(x.price)}</Td>
                    <Td><FreshnessBadge f={x.freshness} compact /></Td><Td align="right" num>{num(x.market_value, 0)}</Td>
                    <Td align="right" num className={toneClass(x.unrealized_pnl)}>{num(x.unrealized_pnl, 0)} ({pct(x.unrealized_pct, 1)})</Td>
                    <Td align="right" num>{pct(x.weight, 1, false)}</Td></tr>))}</tbody></Table>
            )}
          </Panel>
          <Panel title="Performance (time-weighted) vs benchmark">
            {perf.isLoading ? <Spinner /> : series.length ? <>
              <LineChart series={series} percent />
              <p className="mt-1 text-2xs text-muted">{perf.data?.method} Benchmark: {perf.data?.benchmark}. Portfolio {pct(perf.data?.twr)}, benchmark {pct(perf.data?.benchmark_return ?? null)}.</p>
            </> : <Empty title="No performance history yet" />}
          </Panel>
          <Panel title="Transactions" bodyClassName="p-0">
            {p.transactions.length === 0 ? <Empty title="No transactions" /> : (
              <Table className="max-h-80"><thead><tr><Th>Date</Th><Th>Type</Th><Th>Symbol</Th><Th align="right">Qty</Th><Th align="right">Price</Th>
                <Th align="right">Gross</Th><Th align="right">Fees</Th><Th align="right">Tax</Th><Th align="right">Cash effect</Th><Th /></tr></thead>
                <tbody>{p.transactions.map((t) => (
                  <tr key={t.id}><Td num>{date(t.trade_date)}</Td><Td>{label(t.txn_type)}</Td><Td>{t.symbol ?? ""}</Td>
                    <Td align="right" num>{t.quantity ?? ""}</Td><Td align="right" num>{t.price != null ? price(t.price) : ""}</Td>
                    <Td align="right" num>{num(t.gross_amount, 2)}</Td><Td align="right" num title={t.fee_breakdown ? Object.entries(t.fee_breakdown).map(([k, v]) => `${k}: ${v}`).join("\n") : undefined}>{num(t.fees, 2)}</Td>
                    <Td align="right" num>{num(t.tax, 2)}</Td><Td align="right" num className={toneClass(t.net_cash_flow)}>{num(t.net_cash_flow, 2)}</Td>
                    <Td><Button size="sm" variant="ghost" aria-label="Delete transaction" onClick={() => api(`/portfolio/transactions/${t.id}`, { method: "DELETE" }).then(refresh).catch((e) => alert(e.message))}>✕</Button></Td></tr>))}</tbody></Table>
            )}
          </Panel>
        </div>
        <div className="space-y-3">
          <Panel title="Simulated transaction"><TxnForm p={p} defaultSymbol={defaultSymbol} onDone={refresh} /></Panel>
          <Panel title="Allocation & concentration">
            {Object.entries(p.allocation.by_sector).map(([k, v]) => <KV key={k} k={k} v={pct(v, 1, false)} />)}
            <KV k="Cash" v={pct(p.allocation.cash, 1, false)} />
            <div className="mt-2" />
            {Object.entries(p.allocation.by_security_type).map(([k, v]) => <KV key={k} k={label(k)} v={pct(v, 1, false)} />)}
            <KV k="Largest position" v={pct(p.concentration.top_position_weight, 1, false)} />
            <KV k="HHI (concentration)" v={num(p.concentration.hhi, 3)} />
            {p.concentration.warning && <p className="mt-1 text-2xs text-warn">{p.concentration.warning}</p>}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function PortfolioInner() {
  const qc = useQueryClient();
  const params = useSearchParams();
  const list = useApi<{ items: Summary[]; disclaimer: string }>("/portfolio");
  const [sel, setSel] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [cash, setCash] = useState("1000000");
  const [error, setError] = useState<unknown>(null);
  const current = sel ?? list.data?.items[0]?.id ?? null;
  async function create() {
    setError(null);
    try {
      const p = await api<Summary>("/portfolio", { method: "POST", body: { name, initial_cash: Number(cash) || 0 } });
      setName(""); setSel(p.id);
      qc.invalidateQueries({ queryKey: ["/portfolio"] });
    } catch (e) { setError(e); }
  }
  return (
    <div className="space-y-3">
      <PageHeader title="Paper portfolio" description={list.data?.disclaimer ?? "Simulated research portfolios."}
        actions={<>
          {(list.data?.items ?? []).length > 0 && <Select aria-label="Portfolio" value={current ?? ""} onChange={(e) => setSel(Number(e.target.value))}>
            {list.data!.items.map((p) => <option key={p.id} value={p.id}>{p.name} ({label(p.kind)})</option>)}</Select>}
          <Input aria-label="New portfolio name" placeholder="New portfolio name" value={name} onChange={(e) => setName(e.target.value)} className="w-44" />
          <Input aria-label="Initial cash" inputMode="numeric" value={cash} onChange={(e) => setCash(e.target.value)} className="w-28" />
          <Button variant="primary" disabled={!name.trim()} onClick={create}>Create</Button>
        </>} />
      {error ? <ErrorState error={error} /> : null}
      <p className="text-2xs text-muted"><Badge tone="info">Simulation</Badge> Trades are recorded at the price you enter; fees follow the Kenyan
        fee schedule unless overridden. Cash cannot go negative and you cannot sell more than you hold on the trade date.</p>
      {list.isLoading ? <Spinner /> : current ? <PortfolioView id={current} defaultSymbol={params.get("symbol") ?? ""} /> :
        <Empty title="No portfolios yet">Create a paper portfolio to simulate purchases, sales and dividends.</Empty>}
    </div>
  );
}

export default function PortfolioPage() {
  return <Suspense><PortfolioInner /></Suspense>;
}
