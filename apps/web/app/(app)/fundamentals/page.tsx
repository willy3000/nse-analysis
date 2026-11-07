"use client";

import { useMemo, useState } from "react";

import { ScoreMeter, SymbolLink } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Empty, ErrorState, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { compact, date, num, pct } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";
import type { InstrumentRef } from "@/lib/types";

interface Row extends InstrumentRef { as_of: string; period_end: string | null; metrics: Record<string, number | null>;
  quality_score: number | null; has_statements: boolean }

const COLS: [string, string, "pct" | "num" | "big"][] = [
  ["pe_ratio", "P/E", "num"], ["pb_ratio", "P/B", "num"], ["dividend_yield", "Yield", "pct"], ["payout_ratio", "Payout", "pct"],
  ["dividend_years_streak", "Div yrs", "num"], ["roe", "ROE", "pct"], ["net_margin", "Net margin", "pct"],
  ["revenue_growth", "Rev growth", "pct"], ["earnings_growth", "EPS/NI growth", "pct"], ["debt_to_equity", "D/E", "num"],
  ["market_cap", "Mkt cap", "big"],
];

export default function FundamentalsPage() {
  const [universe] = useUniverse();
  const [sort, setSort] = useState("dividend_yield");
  const [onlyStatements, setOnly] = useState(false);
  const q = useApi<{ items: Row[]; note: string }>("/fundamentals/summary", { universe: universe ?? undefined });
  const rows = useMemo(() => {
    const r = (q.data?.items ?? []).filter((x) => !onlyStatements || x.has_statements);
    return [...r].sort((a, b) => {
      const va = sort === "quality_score" ? a.quality_score : a.metrics[sort];
      const vb = sort === "quality_score" ? b.quality_score : b.metrics[sort];
      if (va == null) return 1;
      if (vb == null) return -1;
      return sort === "pe_ratio" || sort === "pb_ratio" || sort === "debt_to_equity" ? va - vb : vb - va;
    });
  }, [q.data, sort, onlyStatements]);
  const withStatements = (q.data?.items ?? []).filter((x) => x.has_statements).length;
  return (
    <div className="space-y-3">
      <PageHeader title="Fundamentals" description="Point-in-time valuation, profitability, balance-sheet and dividend metrics. Values are never back-filled: if no published statement is on file, the metric is shown as unavailable."
        actions={<>
          <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={onlyStatements} onChange={() => setOnly(!onlyStatements)} />Only with statements</label>
          <Select aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value)}>
            {[["dividend_yield", "Dividend yield"], ["pe_ratio", "Lowest P/E"], ["roe", "ROE"], ["quality_score", "Quality score"],
              ["earnings_growth", "Earnings growth"], ["debt_to_equity", "Lowest D/E"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select></>} />
      {q.data && withStatements === 0 && (
        <Panel><p className="text-xs"><Badge tone="warn">Statements unavailable</Badge> The connected provider does not supply financial statements,
          so profitability, balance-sheet and P/E metrics are unavailable. Dividend metrics use the provider&apos;s dividend history where present.
          Import published statements with <code className="num">nse-cli import-csv financials</code> (see docs/data-sources/csv-import.md).</p></Panel>
      )}
      <Panel bodyClassName="p-0">
        {q.isLoading ? <Spinner /> : q.error ? <div className="p-3"><ErrorState error={q.error} /></div> : rows.length === 0 ? <Empty title="No fundamentals computed yet" /> : (
          <Table className="max-h-[78vh]">
            <thead><tr><Th>Instrument</Th><Th>Period</Th>{COLS.map(([k, l]) => <Th key={k} align="right" onClick={() => setSort(k)} sorted={sort === k ? "desc" : null}>{l}</Th>)}
              <Th align="right" onClick={() => setSort("quality_score")} sorted={sort === "quality_score" ? "desc" : null}>Quality</Th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} className="hover:bg-raised/50">
                <Td><SymbolLink symbol={r.symbol} name={r.name} synthetic={r.is_synthetic} /></Td>
                <Td className="text-muted" num>{r.period_end ? date(r.period_end) : "no statements"}</Td>
                {COLS.map(([k, , kind]) => {
                  const v = r.metrics[k];
                  return <Td key={k} align="right" num className={v == null ? "text-muted/60" : ""}>
                    {v == null ? "—" : kind === "pct" ? pct(v, 1, false) : kind === "big" ? compact(v) : num(v, k === "dividend_years_streak" ? 0 : 2)}</Td>;
                })}
                <Td align="right"><ScoreMeter value={r.quality_score} /></Td>
              </tr>))}</tbody>
          </Table>
        )}
      </Panel>
      <p className="text-2xs text-muted">{q.data?.note}</p>
    </div>
  );
}
