"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Change, FreshnessBadge, ScoreMeter, SymbolLink, TrendBadge } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Empty, ErrorState, Input, Panel, Spinner, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { date, pct, price } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Freshness, InstrumentRef } from "@/lib/types";

interface Item { id: number; instrument: InstrumentRef; notes: string | null; tags: string[]; target_price: number | null;
  stop_price: number | null; entry_price: number | null; added_at: string; price_when_added: number | null; price: number | null;
  freshness: Freshness; change_since_added: number | null; distance_to_target: number | null; distance_to_stop: number | null;
  change_pct: number | null; trend_class: string | null; trading_score: number | null; risk_score: number | null }
interface WL { id: number; name: string; description: string | null; items: Item[];
  performance: { average_change_since_added: number | null; count_priced: number } }

function EditableNumber({ value, onSave, label: aria }: { value: number | null; onSave: (v: number | null) => void; label: string }) {
  const [v, setV] = useState(value?.toString() ?? "");
  return <Input aria-label={aria} className="h-6 w-20 text-right text-xs" inputMode="decimal" value={v} onChange={(e) => setV(e.target.value)}
    onBlur={() => { const n = v === "" ? null : Number(v); if (n !== value && (n === null || Number.isFinite(n))) onSave(n); }} />;
}

function WatchlistPanel({ w, refresh }: { w: WL; refresh: () => void }) {
  const [sym, setSym] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const patch = (it: Item, body: Record<string, unknown>) => api(`/watchlists/${w.id}/items/${it.id}`, { method: "PATCH", body }).then(refresh).catch(setErr);
  return (
    <Panel title={w.name} subtitle={`${w.items.length} instruments · avg since added ${pct(w.performance.average_change_since_added)}`} bodyClassName="p-0"
      actions={<>
        <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); setErr(null); api(`/watchlists/${w.id}/items`, { method: "POST", body: { symbol: sym } }).then(() => { setSym(""); refresh(); }).catch(setErr); }}>
          <Input aria-label="Add symbol" placeholder="Add symbol" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} className="h-7 w-28 text-xs" />
          <Button size="sm" type="submit" disabled={!sym}>Add</Button>
        </form>
        <Button size="sm" variant="danger" onClick={() => { if (confirm(`Delete watchlist "${w.name}"?`)) api(`/watchlists/${w.id}`, { method: "DELETE" }).then(refresh); }}>Delete</Button>
      </>}>
      {err ? <div className="p-2"><ErrorState error={err} /></div> : null}
      {w.items.length === 0 ? <Empty title="Empty watchlist" /> : (
        <Table>
          <thead><tr><Th>Instrument</Th><Th align="right">Price</Th><Th>Freshness</Th><Th align="right">Day</Th><Th align="right">Since added</Th>
            <Th align="right">Entry ref</Th><Th align="right">Target</Th><Th align="right">Stop</Th><Th>Trend</Th><Th align="right">Risk</Th><Th>Tags / notes</Th><Th /></tr></thead>
          <tbody>{w.items.map((it) => (
            <tr key={it.id} className="align-top">
              <Td><SymbolLink symbol={it.instrument.symbol} name={it.instrument.name} synthetic={it.instrument.is_synthetic} />
                <span className="text-2xs text-muted">added {date(it.added_at)} @ {price(it.price_when_added)}</span></Td>
              <Td align="right" num>{price(it.price)}</Td><Td><FreshnessBadge f={it.freshness} compact /></Td>
              <Td align="right"><Change v={it.change_pct} /></Td><Td align="right"><Change v={it.change_since_added} /></Td>
              <Td align="right"><EditableNumber label="Entry reference price" value={it.entry_price} onSave={(v) => patch(it, { entry_price: v })} /></Td>
              <Td align="right"><EditableNumber label="Target price" value={it.target_price} onSave={(v) => patch(it, { target_price: v })} />
                <div className="num text-2xs text-muted">{pct(it.distance_to_target, 1)}</div></Td>
              <Td align="right"><EditableNumber label="Stop reference" value={it.stop_price} onSave={(v) => patch(it, { stop_price: v })} />
                <div className="num text-2xs text-muted">{pct(it.distance_to_stop, 1)}</div></Td>
              <Td><TrendBadge value={it.trend_class} /></Td><Td align="right"><ScoreMeter value={it.risk_score} inverse /></Td>
              <Td className="max-w-[16rem] whitespace-normal">
                <div className="mb-1 flex flex-wrap gap-1">{it.tags.map((t) => <Badge key={t}>{t}</Badge>)}</div>
                <Input aria-label="Tags (comma separated)" className="mb-1 h-6 text-xs" defaultValue={it.tags.join(", ")} placeholder="tags, comma separated"
                  onBlur={(e) => patch(it, { tags: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
                <textarea aria-label="Notes" className="h-12 w-full rounded border border-line bg-bg p-1 text-xs" defaultValue={it.notes ?? ""}
                  onBlur={(e) => { if (e.target.value !== (it.notes ?? "")) patch(it, { notes: e.target.value }); }} />
              </Td>
              <Td><Button size="sm" variant="ghost" aria-label={`Remove ${it.instrument.symbol}`} onClick={() => api(`/watchlists/${w.id}/items/${it.id}`, { method: "DELETE" }).then(refresh)}>✕</Button></Td>
            </tr>))}</tbody>
        </Table>
      )}
    </Panel>
  );
}

export default function WatchlistsPage() {
  const qc = useQueryClient();
  const q = useApi<WL[]>("/watchlists");
  const [name, setName] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["/watchlists"] });
  return (
    <div className="space-y-3">
      <PageHeader title="Watchlists" description="Track instruments with your own reference levels. Target/stop levels are notes for your research — nothing is ever executed."
        actions={<form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); setErr(null); api("/watchlists", { method: "POST", body: { name } }).then(() => { setName(""); refresh(); }).catch(setErr); }}>
          <Input aria-label="New watchlist name" placeholder="New watchlist" value={name} onChange={(e) => setName(e.target.value)} className="w-44" />
          <Button variant="primary" type="submit" disabled={!name.trim()}>Create</Button></form>} />
      {err ? <ErrorState error={err} /> : null}
      {q.isLoading ? <Spinner /> : q.error ? <ErrorState error={q.error} /> : q.data!.length === 0 ? <Empty title="No watchlists yet" /> :
        <div className="space-y-3">{q.data!.map((w) => <WatchlistPanel key={w.id} w={w} refresh={refresh} />)}</div>}
    </div>
  );
}
