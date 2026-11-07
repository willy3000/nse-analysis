"use client";

import { useState } from "react";

import { SymbolLink } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Empty, ErrorState, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { date, datetime, label, num } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";

interface News { total: number; policy: string; items: { id: number; kind: string; category: string | null; title: string; publisher: string | null;
  url: string | null; summary: string | null; summary_is_generated: boolean; published_at: string | null; ingested_at: string;
  source_reliability: string; symbols: string[]; is_synthetic: boolean }[] }
interface Events { from: string; to: string; items: { symbol: string; name: string; type: string; status: string | null; title: string | null;
  ex_date: string | null; record_date: string | null; payment_date: string | null; effective_date: string | null; amount: number | null;
  currency: string | null; ratio_from: number | null; ratio_to: number | null; source_url: string | null }[] }

export default function NewsPage() {
  const [universe] = useUniverse();
  const [kind, setKind] = useState("");
  const [back, setBack] = useState("90");
  const news = useApi<News>("/news", { universe: universe ?? undefined, kind, limit: 100 });
  const events = useApi<Events>("/events", { universe: universe ?? undefined, days_back: Number(back), days_ahead: 180 });
  return (
    <div className="space-y-3">
      <PageHeader title="News & events" description={news.data?.policy ?? "Market and company news with links to original sources."} />
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="News & announcements" actions={<Select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>{["NEWS", "ANNOUNCEMENT", "RESULTS", "CORPORATE_ACTION", "REGULATORY", "MACRO"].map((k) => <option key={k} value={k}>{label(k)}</option>)}</Select>}>
          {news.isLoading ? <Spinner /> : news.error ? <ErrorState error={news.error} /> : news.data!.items.length === 0 ? <Empty title="No news on file" /> : (
            <ul className="divide-y divide-line/60">{news.data!.items.map((n) => (
              <li key={n.id} className="py-2 text-xs">
                <div className="mb-0.5 flex flex-wrap items-center gap-1">
                  <Badge>{label(n.kind)}</Badge>{n.is_synthetic && <Badge tone="warn">synthetic</Badge>}
                  <span className="text-2xs text-muted">{datetime(n.published_at)} · {n.publisher ?? "Unknown publisher"} · source: {label(n.source_reliability)}</span>
                </div>
                {n.url ? <a href={n.url} target="_blank" rel="noreferrer noopener" className="font-medium hover:text-accent">{n.title}</a> : <span className="font-medium">{n.title}</span>}
                {n.summary && <p className="mt-0.5 text-2xs text-muted">{n.summary_is_generated && <Badge tone="info">AI-generated summary</Badge>} {n.summary}</p>}
                {n.symbols.length > 0 && <div className="mt-1 flex gap-2">{n.symbols.map((s) => <SymbolLink key={s} symbol={s} />)}</div>}
                {!n.url && <p className="text-2xs text-muted">No link to the original article was supplied by the provider.</p>}
              </li>))}</ul>
          )}
        </Panel>
        <Panel title="Corporate events calendar" bodyClassName="p-0" actions={<Select aria-label="Look back" value={back} onChange={(e) => setBack(e.target.value)}>
          <option value="30">Past 30 days</option><option value="90">Past 90 days</option><option value="365">Past year</option><option value="3650">All</option></Select>}>
          {events.isLoading ? <Spinner /> : events.error ? <div className="p-3"><ErrorState error={events.error} /></div> : events.data!.items.length === 0 ? <Empty title="No events in range" /> : (
            <Table className="max-h-[75vh]"><thead><tr><Th>Date</Th><Th>Instrument</Th><Th>Event</Th><Th>Detail</Th><Th>Status</Th></tr></thead>
              <tbody>{events.data!.items.map((e, i) => {
                const upcoming = (e.ex_date ?? e.effective_date ?? "") >= new Date().toISOString().slice(0, 10);
                return (
                  <tr key={i} className={upcoming ? "bg-accent/5" : ""}>
                    <Td num>{date(e.ex_date ?? e.effective_date ?? e.payment_date)}{upcoming && <Badge tone="accent" className="ml-1">upcoming</Badge>}</Td>
                    <Td><SymbolLink symbol={e.symbol} name={e.name} /></Td><Td>{label(e.type)}</Td>
                    <Td className="max-w-xs truncate">{e.amount != null ? `${e.currency ?? ""} ${num(e.amount, 2)} per share · pay ${date(e.payment_date)}` : e.ratio_from ? `${e.ratio_from} → ${e.ratio_to}` : e.title}</Td>
                    <Td>{label(e.status)}</Td></tr>);
              })}</tbody></Table>
          )}
        </Panel>
      </div>
    </div>
  );
}
