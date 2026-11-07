"use client";

import { Fragment, useState } from "react";

import { EvidenceList, SymbolLink, TrendBadge } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Empty, ErrorState, Panel, Spinner, Table, Tabs, Td, Th } from "@/components/ui";
import { date, label, num } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";
import type { Evidence, InstrumentRef } from "@/lib/types";

interface TrendItem extends InstrumentRef { classification: string; strength: number | null; as_of: string;
  evidence: Evidence[]; other_timeframes: Record<string, string> }
interface Trends { timeframe: string; distribution: Record<string, number>; items: TrendItem[] }

export default function TrendsPage() {
  const [universe] = useUniverse();
  const [tf, setTf] = useState<"1d" | "1w" | "1mo">("1d");
  const [cls, setCls] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const q = useApi<Trends>("/trends", { universe: universe ?? undefined, timeframe: tf, classification: cls ?? undefined });
  const all = useApi<Trends>("/trends", { universe: universe ?? undefined, timeframe: tf });
  return (
    <div className="space-y-3">
      <PageHeader title="Trends" description="Each timeframe is classified independently from its own completed bars; daily, weekly and monthly views can and do disagree."
        actions={<Tabs value={tf} onChange={setTf} options={[{ value: "1d", label: "Daily" }, { value: "1w", label: "Weekly" }, { value: "1mo", label: "Monthly" }]} />} />
      <div className="flex flex-wrap gap-1">
        <button onClick={() => setCls(null)}><Badge tone={cls === null ? "accent" : "neutral"}>All {all.data?.items.length ?? ""}</Badge></button>
        {Object.entries(all.data?.distribution ?? {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
          <button key={k} onClick={() => setCls(k)} aria-pressed={cls === k}><Badge tone={cls === k ? "accent" : "neutral"}>{label(k)} {v}</Badge></button>
        ))}
      </div>
      <Panel bodyClassName="p-0">
        {q.isLoading ? <Spinner /> : q.error ? <div className="p-3"><ErrorState error={q.error} /></div> :
          q.data!.items.length === 0 ? <Empty title="No classifications" /> : (
            <Table>
              <thead><tr><Th>Instrument</Th><Th>{tf} trend</Th><Th align="right">Strength</Th><Th>Other timeframes</Th><Th>As of</Th><Th /></tr></thead>
              <tbody>
                {q.data!.items.map((t) => (
                  <Fragment key={t.id}>
                    <tr className="hover:bg-raised/50">
                      <Td><SymbolLink symbol={t.symbol} name={t.name} synthetic={t.is_synthetic} /></Td>
                      <Td><TrendBadge value={t.classification} /></Td>
                      <Td align="right" num>{num(t.strength, 0)}</Td>
                      <Td><div className="flex gap-1">{Object.entries(t.other_timeframes).map(([k, v]) => (
                        <span key={k} className="flex items-center gap-1 text-2xs text-muted">{k}<TrendBadge value={v} /></span>))}</div></Td>
                      <Td num className="text-muted">{date(t.as_of)}</Td>
                      <Td><button className="text-2xs text-accent" aria-expanded={open === t.id} onClick={() => setOpen(open === t.id ? null : t.id)}>
                        {open === t.id ? "Hide" : "Evidence"}</button></Td>
                    </tr>
                    {open === t.id && (
                      <tr><td colSpan={6} className="border-b border-line bg-raised/40 p-3"><EvidenceList items={t.evidence} /></td></tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </Table>
          )}
      </Panel>
    </div>
  );
}
