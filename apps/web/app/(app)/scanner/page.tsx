"use client";

import { useMemo, useState } from "react";

import { Change, ScoreMeter, SymbolLink, TrendBadge } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Button, Empty, ErrorState, Field, Input, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { compact, label, num, pct, price } from "@/lib/format";
import { useApi, useApiMutation, useUniverse } from "@/lib/hooks";
import type { InstrumentRef, Paged, Snapshot } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Row = InstrumentRef & Snapshot;
type Filters = Record<string, string | string[] | boolean | number | null | undefined>;

const TRENDS = ["STRONG_BULLISH", "EMERGING_BULLISH", "BULLISH_PULLBACK", "SIDEWAYS", "WEAKENING_BULLISH",
  "EMERGING_BEARISH", "STRONG_BEARISH", "POTENTIAL_REVERSAL", "INSUFFICIENT_DATA"];
const NUMERIC: { key: string; label: string; pctInput?: boolean }[] = [
  { key: "price_min", label: "Price ≥" }, { key: "price_max", label: "Price ≤" },
  { key: "volume_min", label: "Volume ≥" }, { key: "relative_volume_min", label: "Rel. volume ≥" },
  { key: "change_min", label: "Change ≥ %", pctInput: true }, { key: "change_max", label: "Change ≤ %", pctInput: true },
  { key: "rsi_min", label: "RSI ≥" }, { key: "rsi_max", label: "RSI ≤" },
  { key: "volatility_max", label: "Volatility ≤ %", pctInput: true },
  { key: "dividend_yield_min", label: "Div. yield ≥ %", pctInput: true },
  { key: "trading_score_min", label: "Trading score ≥" }, { key: "quality_score_min", label: "Quality score ≥" },
  { key: "risk_score_max", label: "Risk score ≤" },
  { key: "trading_frequency_min", label: "Trading freq ≥ %", pctInput: true },
];
const COLUMNS: { key: keyof Row; label: string; fmt: (r: Row) => React.ReactNode; sortable?: boolean }[] = [
  { key: "last_close", label: "Last", fmt: (r) => price(r.last_close), sortable: true },
  { key: "change_pct", label: "Chg", fmt: (r) => <Change v={r.change_pct} />, sortable: true },
  { key: "volume", label: "Volume", fmt: (r) => compact(r.volume), sortable: true },
  { key: "relative_volume", label: "RVol", fmt: (r) => num(r.relative_volume, 2), sortable: true },
  { key: "rsi_14", label: "RSI", fmt: (r) => num(r.rsi_14, 0), sortable: true },
  { key: "return_3m", label: "3M", fmt: (r) => <Change v={r.return_3m} />, sortable: true },
  { key: "return_1y", label: "1Y", fmt: (r) => <Change v={r.return_1y} />, sortable: true },
  { key: "volatility_60", label: "Vol 60", fmt: (r) => pct(r.volatility_60, 0, false), sortable: true },
  { key: "dividend_yield", label: "Yield", fmt: (r) => pct(r.dividend_yield, 1, false), sortable: true },
  { key: "pe_ratio", label: "P/E", fmt: (r) => num(r.pe_ratio, 1), sortable: true },
  { key: "trend_class", label: "Trend", fmt: (r) => <TrendBadge value={r.trend_class} /> },
  { key: "trading_score", label: "Trading", fmt: (r) => <ScoreMeter value={r.trading_score} />, sortable: true },
  { key: "quality_score", label: "Quality", fmt: (r) => <ScoreMeter value={r.quality_score} />, sortable: true },
  { key: "risk_score", label: "Risk", fmt: (r) => <ScoreMeter value={r.risk_score} inverse />, sortable: true },
];

function toBody(f: Filters, sort: string, desc: boolean) {
  const body: Record<string, unknown> = { sort, descending: desc };
  for (const n of NUMERIC) {
    const v = f[n.key];
    if (v === undefined || v === null || v === "") continue;
    const x = Number(v);
    if (Number.isFinite(x)) body[n.key] = n.pctInput ? x / 100 : x;
  }
  for (const k of ["security_types", "sectors", "trend_classes"]) {
    const v = f[k];
    if (Array.isArray(v) && v.length) body[k] = v;
  }
  return body;
}

export default function ScannerPage() {
  const [universe] = useUniverse();
  const [draft, setDraft] = useState<Filters>({});
  const [applied, setApplied] = useState<Filters>({});
  const [sort, setSort] = useState("trading_score");
  const [desc, setDesc] = useState(true);
  const [name, setName] = useState("");
  const sectors = useApi<string[]>("/instruments/sectors");
  const saved = useApi<{ id: number; name: string; filters: Record<string, unknown> }[]>("/scanner/saved");
  const save = useApiMutation<{ name: string; filters: unknown }>("POST", "/scanner/saved", ["/scanner/saved"]);
  const del = useApiMutation<{ id: number }>("DELETE", (b) => `/scanner/saved/${b.id}`, ["/scanner/saved"]);
  const body = useMemo(() => toBody(applied, sort, desc), [applied, sort, desc]);
  const res = useQuery({
    queryKey: ["/scanner/results", body, universe],
    queryFn: () => api<Paged<Row>>("/scanner/results", { method: "POST", body, query: { universe: universe ?? undefined, limit: 500 } }),
  });

  const set = (k: string, v: Filters[string]) => setDraft((d) => ({ ...d, [k]: v }));
  const toggleIn = (k: string, v: string) => {
    const cur = (draft[k] as string[] | undefined) ?? [];
    set(k, cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]);
  };
  const loadSaved = (f: Record<string, unknown>) => {
    const d: Filters = {};
    for (const n of NUMERIC) if (f[n.key] != null) d[n.key] = String(n.pctInput ? Number(f[n.key]) * 100 : f[n.key]);
    for (const k of ["security_types", "sectors", "trend_classes"]) if (Array.isArray(f[k])) d[k] = f[k] as string[];
    setDraft(d);
    setApplied(d);
    if (typeof f.sort === "string") setSort(f.sort);
    if (typeof f.descending === "boolean") setDesc(f.descending);
  };

  return (
    <div className="space-y-3">
      <PageHeader title="Market scanner" description="Filter the whole universe on precomputed analytics. Instruments with a missing value for a filtered metric are excluded, not treated as zero." />
      <div className="grid gap-3 xl:grid-cols-[18rem_1fr]">
        <Panel title="Filters" actions={<Button size="sm" variant="ghost" onClick={() => { setDraft({}); setApplied({}); }}>Reset</Button>}>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); setApplied(draft); }}>
            <div className="grid grid-cols-2 gap-2">
              {NUMERIC.map((n) => (
                <Field key={n.key} label={n.label}>
                  <Input inputMode="decimal" value={(draft[n.key] as string) ?? ""} onChange={(e) => set(n.key, e.target.value)} />
                </Field>
              ))}
            </div>
            <fieldset><legend className="mb-1 text-xs font-medium text-muted">Security type</legend>
              <div className="flex flex-wrap gap-2 text-xs">
                {["EQUITY", "ETF", "REIT", "PREFERENCE"].map((t) => (
                  <label key={t} className="flex items-center gap-1"><input type="checkbox"
                    checked={((draft.security_types as string[]) ?? []).includes(t)} onChange={() => toggleIn("security_types", t)} />{label(t)}</label>
                ))}
              </div>
            </fieldset>
            <fieldset><legend className="mb-1 text-xs font-medium text-muted">Trend (daily)</legend>
              <div className="flex flex-col gap-0.5 text-xs">
                {TRENDS.map((t) => (
                  <label key={t} className="flex items-center gap-1"><input type="checkbox"
                    checked={((draft.trend_classes as string[]) ?? []).includes(t)} onChange={() => toggleIn("trend_classes", t)} />{label(t)}</label>
                ))}
              </div>
            </fieldset>
            <fieldset><legend className="mb-1 text-xs font-medium text-muted">Sector</legend>
              <div className="flex max-h-32 flex-col gap-0.5 overflow-y-auto text-xs">
                {(sectors.data ?? []).map((s) => (
                  <label key={s} className="flex items-center gap-1"><input type="checkbox"
                    checked={((draft.sectors as string[]) ?? []).includes(s)} onChange={() => toggleIn("sectors", s)} />{s}</label>
                ))}
              </div>
            </fieldset>
            <Button type="submit" variant="primary" className="w-full">Apply filters</Button>
          </form>
          <div className="mt-4 space-y-2 border-t border-line pt-3">
            <div className="flex gap-1">
              <Input placeholder="Save as…" value={name} onChange={(e) => setName(e.target.value)} aria-label="Saved scan name" />
              <Button size="md" disabled={!name.trim()} loading={save.isPending}
                onClick={() => save.mutate({ name: name.trim(), filters: toBody(draft, sort, desc) }, { onSuccess: () => setName("") })}>Save</Button>
            </div>
            {save.error && <ErrorState error={save.error} />}
            <ul className="space-y-1 text-xs">
              {(saved.data ?? []).map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-1">
                  <button className="truncate text-left text-accent hover:underline" onClick={() => loadSaved(s.filters)}>{s.name}</button>
                  <Button size="sm" variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => del.mutate({ id: s.id })}>✕</Button>
                </li>
              ))}
            </ul>
          </div>
        </Panel>
        <Panel title={res.data ? `${res.data.total} instruments` : "Results"} bodyClassName="p-0"
          actions={<Select aria-label="Sort direction" className="h-7 text-xs" value={desc ? "desc" : "asc"} onChange={(e) => setDesc(e.target.value === "desc")}>
            <option value="desc">Descending</option><option value="asc">Ascending</option></Select>}>
          {res.isLoading ? <Spinner /> : res.error ? <div className="p-3"><ErrorState error={res.error} /></div> :
            res.data!.items.length === 0 ? <Empty title="No instruments match these filters" /> : (
              <Table className="max-h-[75vh]">
                <thead><tr><Th>Instrument</Th>
                  {COLUMNS.map((c) => <Th key={c.key} align="right" sorted={sort === c.key ? (desc ? "desc" : "asc") : null}
                    onClick={c.sortable ? () => { if (sort === c.key) setDesc(!desc); else { setSort(c.key); setDesc(true); } } : undefined}>{c.label}</Th>)}
                </tr></thead>
                <tbody>
                  {res.data!.items.map((r) => (
                    <tr key={r.id} className="hover:bg-raised/50">
                      <Td><SymbolLink symbol={r.symbol} name={r.name} synthetic={r.is_synthetic} /></Td>
                      {COLUMNS.map((c) => <Td key={c.key} align="right" num>{c.fmt(r)}</Td>)}
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
        </Panel>
      </div>
    </div>
  );
}
