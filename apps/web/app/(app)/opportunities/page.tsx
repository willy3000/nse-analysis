"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { BaseRateTable, Change, DirectionBadge, EvidenceList, ScoreMeter, SymbolLink } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Empty, ErrorState, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { date, label, price } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";
import type { OpportunityItem, Paged } from "@/lib/types";

const CATEGORIES = ["", "MOMENTUM", "BREAKOUT", "REVERSAL", "PULLBACK", "VALUE", "DIVIDEND", "RISK"];

function daysSince(iso: string) {
  return Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 86_400_000));
}

function Detail({ s }: { s: OpportunityItem }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <SymbolLink symbol={s.instrument.symbol} name={s.instrument.name} synthetic={s.instrument.is_synthetic} />
        <DirectionBadge value={s.direction} /><Badge>{s.timeframe}</Badge><Badge tone="accent">{label(s.category)}</Badge>
        <Badge tone={s.status === "ACTIVE" ? "up" : "neutral"}>{label(s.status)}</Badge>
      </div>
      <h3 className="text-sm font-semibold">{label(s.signal_type)}</h3>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div><div className="text-2xs text-muted">Reference price</div><div className="num">{price(s.reference_price)}</div></div>
        <div><div className="text-2xs text-muted">Current</div><div className="num">{price(s.current_price)} <Change v={s.change_since_signal} /></div></div>
        <div><div className="text-2xs text-muted">Detected on bar</div><div className="num">{date(s.bar_ts)}</div></div>
      </div>
      <section><h4 className="mb-1 text-2xs font-semibold uppercase tracking-wide text-muted">Supporting evidence</h4>
        <EvidenceList items={s.evidence} /></section>
      <section className="rounded border border-line bg-raised/50 p-2 text-xs">
        <h4 className="text-2xs font-semibold uppercase tracking-wide text-muted">Invalidation</h4>
        <p>{s.invalidation.condition}{s.invalidation.price != null && <span className="num"> ({price(s.invalidation.price)})</span>}</p>
        <p className="text-2xs text-muted">Expires {date(s.expires_at)}.</p>
      </section>
      <section><h4 className="mb-1 text-2xs font-semibold uppercase tracking-wide text-muted">Risk factors</h4>
        <ul className="list-disc space-y-0.5 pl-4 text-xs">{s.risk_factors.map((r, i) => <li key={i}>{r}</li>)}</ul>
        <div className="mt-1 text-xs">Risk score: <ScoreMeter value={s.risk_score} inverse /></div></section>
      <section><h4 className="mb-1 text-2xs font-semibold uppercase tracking-wide text-muted">Historical behaviour of this signal type</h4>
        <BaseRateTable rates={s.historical_performance} /></section>
      <p className="text-2xs text-muted">Rule version {s.calc_version}. Signals describe conditions in historical data; they are
        not recommendations and carry no guarantee.</p>
    </div>
  );
}

function OpportunitiesInner() {
  const [universe] = useUniverse();
  const params = useSearchParams();
  const router = useRouter();
  const [category, setCategory] = useState("");
  const [direction, setDirection] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [status, setStatus] = useState("ACTIVE");
  const list = useApi<Paged<OpportunityItem>>("/opportunities", { universe: universe ?? undefined, category,
    direction, timeframe, status, limit: 300 });
  const selectedId = Number(params.get("id")) || null;
  const selected = list.data?.items.find((s) => s.id === selectedId) ?? list.data?.items[0];

  return (
    <div className="space-y-3">
      <PageHeader title="Opportunities" description="Rule-based detections across the universe. Each shows its evidence, invalidation level, risk factors and measured base rates — no probabilities are invented."
        actions={<>
          <Select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c ? label(c) : "All categories"}</option>)}</Select>
          <Select aria-label="Direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="">All directions</option><option value="BULLISH">Bullish</option><option value="BEARISH">Bearish</option><option value="NEUTRAL">Neutral</option></Select>
          <Select aria-label="Timeframe" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            <option value="">All timeframes</option><option value="1d">Daily</option><option value="1w">Weekly</option></Select>
          <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="ACTIVE">Active</option><option value="INVALIDATED">Invalidated</option><option value="EXPIRED">Expired</option><option value="ALL">All</option></Select>
        </>} />
      {list.isLoading ? <Spinner /> : list.error ? <ErrorState error={list.error} /> : (
        <div className="grid gap-3 xl:grid-cols-[1fr_26rem]">
          <Panel title={`${list.data!.total} signals`} bodyClassName="p-0">
            {list.data!.items.length === 0 ? <Empty title="No signals match">Signals appear after each analytics run on new data.</Empty> : (
              <Table className="max-h-[75vh]">
                <thead><tr><Th>Instrument</Th><Th>Signal</Th><Th>Dir</Th><Th>TF</Th><Th align="right">Ref</Th>
                  <Th align="right">Since</Th><Th align="right">Age</Th><Th align="right">Risk</Th><Th align="right">20-bar hist.</Th></tr></thead>
                <tbody>
                  {list.data!.items.map((s) => {
                    const h20 = s.historical_performance?.find((r) => r.horizon_bars === 20);
                    return (
                      <tr key={s.id} onClick={() => router.replace(`/opportunities?id=${s.id}`)}
                        className={`cursor-pointer hover:bg-raised/50 ${selected?.id === s.id ? "bg-raised" : ""}`}>
                        <Td><SymbolLink symbol={s.instrument.symbol} name={s.instrument.name} synthetic={s.instrument.is_synthetic} /></Td>
                        <Td>{label(s.signal_type)}</Td><Td><DirectionBadge value={s.direction} /></Td><Td>{s.timeframe}</Td>
                        <Td align="right" num>{price(s.reference_price)}</Td>
                        <Td align="right"><Change v={s.change_since_signal} /></Td>
                        <Td align="right" num>{daysSince(s.bar_ts)}d</Td>
                        <Td align="right"><ScoreMeter value={s.risk_score} inverse /></Td>
                        <Td align="right" num className={h20 && !h20.sufficient_sample ? "opacity-50" : ""}>
                          {h20 ? `${(h20.mean_return * 100).toFixed(1)}% (n=${h20.n})` : "—"}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            )}
          </Panel>
          <Panel title="Signal detail">{selected ? <Detail s={selected} /> : <Empty title="Select a signal" />}</Panel>
        </div>
      )}
    </div>
  );
}

export default function OpportunitiesPage() {
  return <Suspense><OpportunitiesInner /></Suspense>;
}
