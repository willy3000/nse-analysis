"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Change, FreshnessBadge, ScoreMeter, SymbolLink, TrendBadge } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Empty, ErrorState, Input, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { compact, date, label, price } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";
import type { InstrumentRef, Paged, PriceView, Snapshot } from "@/lib/types";

type Item = InstrumentRef & { price: PriceView; snapshot: Snapshot | null };
const PAGE = 100;

function InstrumentsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const [universe] = useUniverse();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [type, setType] = useState("");
  const [sector, setSector] = useState("");
  const [sort, setSort] = useState("symbol");
  const [offset, setOffset] = useState(0);
  useEffect(() => { setQ(params.get("q") ?? ""); setOffset(0); }, [params]);
  const sectors = useApi<string[]>("/instruments/sectors");
  const list = useApi<Paged<Item>>("/instruments", { q, security_type: type, sector, sort, universe: universe ?? undefined,
    limit: PAGE, offset });
  return (
    <div className="space-y-3">
      <PageHeader title="Instruments" description="The instrument catalogue is discovered from data providers (never hard-coded). Analytics apply only where they make sense for the security type."
        actions={<>
          <Input aria-label="Filter by symbol or name" placeholder="Symbol or name" value={q} className="w-48"
            onChange={(e) => { setQ(e.target.value); setOffset(0); router.replace(`/instruments?q=${encodeURIComponent(e.target.value)}`); }} />
          <Select aria-label="Security type" value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}>
            <option value="">All types</option>{["EQUITY", "ETF", "REIT", "BOND", "PREFERENCE"].map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select>
          <Select aria-label="Sector" value={sector} onChange={(e) => { setSector(e.target.value); setOffset(0); }}>
            <option value="">All sectors</option>{(sectors.data ?? []).map((s) => <option key={s}>{s}</option>)}</Select>
          <Select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="symbol">Symbol</option><option value="name">Name</option><option value="change">Change</option><option value="trading_score">Trading score</option></Select>
        </>} />
      <Panel bodyClassName="p-0" title={list.data ? `${list.data.total} instruments` : undefined}>
        {list.isLoading ? <Spinner /> : list.error ? <div className="p-3"><ErrorState error={list.error} /></div> :
          list.data!.items.length === 0 ? <Empty title="No instruments">Configure a data provider or import licensed files in the Data Centre.</Empty> : (
            <>
              <Table>
                <thead><tr><Th>Instrument</Th><Th>Type</Th><Th>Sector</Th><Th>Status</Th><Th align="right">Price</Th>
                  <Th>Freshness</Th><Th align="right">Chg</Th><Th align="right">Volume</Th><Th>Last trade</Th><Th>Trend</Th><Th align="right">Trading</Th></tr></thead>
                <tbody>
                  {list.data!.items.map((i) => (
                    <tr key={i.id} className="hover:bg-raised/50">
                      <Td><SymbolLink symbol={i.symbol} name={i.name} synthetic={i.is_synthetic} /></Td>
                      <Td>{label(i.security_type)}</Td><Td className="text-muted">{i.sector ?? "—"}</Td>
                      <Td>{i.trading_status !== "ACTIVE" ? <Badge tone={i.trading_status === "SUSPENDED" ? "down" : "neutral"}>{label(i.trading_status)}</Badge> : <span className="text-muted">Active</span>}</Td>
                      <Td align="right" num>{price(i.price.price)}</Td>
                      <Td><FreshnessBadge f={i.price.freshness} /></Td>
                      <Td align="right"><Change v={i.snapshot?.change_pct} /></Td>
                      <Td align="right" num>{compact(i.snapshot?.volume)}</Td>
                      <Td num className="text-muted">{date(i.price.last_traded_date)}</Td>
                      <Td><TrendBadge value={i.snapshot?.trend_class} /></Td>
                      <Td align="right"><ScoreMeter value={i.snapshot?.trading_score} /></Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <div className="flex items-center justify-end gap-2 p-2 text-xs">
                <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
                <span className="text-muted">{offset + 1}–{Math.min(offset + PAGE, list.data!.total)} of {list.data!.total}</span>
                <Button size="sm" disabled={offset + PAGE >= list.data!.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
              </div>
            </>
          )}
      </Panel>
    </div>
  );
}

export default function InstrumentsPage() {
  return <Suspense><InstrumentsInner /></Suspense>;
}
