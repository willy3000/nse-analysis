"use client";

import Link from "next/link";

import { Change, DirectionBadge, ScoreMeter, SymbolLink, TrendBadge } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, Empty, ErrorState, Panel, Spinner, Stat, Table, Td, Th } from "@/components/ui";
import { compact, date, kes, label, pct, price } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";
import type { MoverItem, Overview } from "@/lib/types";

interface Movers { gainers: MoverItem[]; losers: MoverItem[]; most_active: MoverItem[]; unusual_volume: MoverItem[];
  session_date: string | null; note: string }
interface Indices { items: { code: string; name: string; value: number; change_pct: number | null; date: string }[];
  available: boolean; note: string | null }
interface PortfolioList { items: { id: number; name: string; total_value: number; total_return: number | null;
  unrealized_pnl: number; positions: unknown[] }[] }

function MoverTable({ rows, metric }: { rows: MoverItem[]; metric: "change" | "volume" | "rvol" }) {
  if (rows.length === 0) return <Empty title="Nothing to show" />;
  return (
    <Table>
      <thead><tr><Th>Symbol</Th><Th align="right">Last</Th><Th align="right">Chg</Th>
        <Th align="right">{metric === "rvol" ? "Rel vol" : "Volume"}</Th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className="hover:bg-raised/50">
            <Td><SymbolLink symbol={r.symbol} name={r.name} synthetic={r.is_synthetic} /></Td>
            <Td align="right" num>{price(r.last_close)}</Td>
            <Td align="right"><Change v={r.change_pct} /></Td>
            <Td align="right" num>{metric === "rvol" ? `${r.relative_volume?.toFixed(1) ?? "—"}x` : compact(r.volume)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export default function OverviewPage() {
  const [universe] = useUniverse();
  const q = { universe: universe ?? undefined };
  const ov = useApi<Overview>("/market/overview", q);
  const mv = useApi<Movers>("/market/movers", { ...q, limit: 8 });
  const idx = useApi<Indices>("/market/indices");
  const pf = useApi<PortfolioList>("/portfolio");

  if (ov.isLoading) return <Spinner label="Loading market overview" />;
  if (ov.error) return <ErrorState error={ov.error} onRetry={() => ov.refetch()} />;
  const o = ov.data!;
  const b = o.breadth;
  const traded = b.advancing + b.declining + b.unchanged;

  return (
    <div className="space-y-3">
      <PageHeader title="Market overview"
        description={<>Session {date(o.session_date)} · {o.is_synthetic ? "synthetic universe" : "NSE"} · prices are
          end-of-day or delayed — see each badge for freshness.</>} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Panel className="col-span-2"><div className="grid grid-cols-3 gap-2">
          <Stat label="Advancing" value={<span className="text-up">{b.advancing}</span>} />
          <Stat label="Declining" value={<span className="text-down">{b.declining}</span>} />
          <Stat label="Unchanged" value={b.unchanged} />
        </div>
          <div className="mt-2 flex h-1.5 overflow-hidden rounded bg-raised" aria-label="Market breadth">
            {traded > 0 && <>
              <span className="bg-up" style={{ width: `${(b.advancing / traded) * 100}%` }} />
              <span className="bg-muted/40" style={{ width: `${(b.unchanged / traded) * 100}%` }} />
              <span className="bg-down" style={{ width: `${(b.declining / traded) * 100}%` }} />
            </>}
          </div>
          <p className="mt-1 text-2xs text-muted">{b.no_trades} without trades · {b.not_updated} not updated this session</p>
        </Panel>
        <Panel><Stat label="Equal-weight move" value={<Change v={o.equal_weight_change} />} sub="traded instruments" /></Panel>
        <Panel><Stat label="Turnover (est.)" value={compact(o.turnover_estimate)} sub="close × volume" /></Panel>
        <Panel className="col-span-2">
          <div className="text-2xs uppercase tracking-wide text-muted">Indices</div>
          {idx.data?.available ? idx.data.items.slice(0, 3).map((i) => (
            <div key={i.code} className="flex justify-between text-xs"><span>{i.name}</span>
              <span className="num">{i.value.toFixed(2)} <Change v={i.change_pct} /></span></div>
          )) : <p className="text-2xs text-muted">{idx.data?.note ?? "—"}</p>}
        </Panel>
        <Panel className="col-span-2">
          <div className="text-2xs uppercase tracking-wide text-muted">Data freshness</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {Object.entries(o.freshness_summary).map(([k, v]) => <Badge key={k} tone={k === "STALE" || k === "UNAVAILABLE" ? "down" : k === "HISTORICAL" ? "warn" : "neutral"}>{label(k)} {v}</Badge>)}
          </div>
          <Link href="/data" className="mt-1 block text-2xs text-accent hover:underline">Open Data Centre →</Link>
        </Panel>
      </div>

      <div className="grid gap-3 lg:grid-cols-4">
        <Panel title="Gainers" bodyClassName="p-0">{mv.data ? <MoverTable rows={mv.data.gainers} metric="volume" /> : <Spinner />}</Panel>
        <Panel title="Losers" bodyClassName="p-0">{mv.data ? <MoverTable rows={mv.data.losers} metric="volume" /> : <Spinner />}</Panel>
        <Panel title="Most active (value)" bodyClassName="p-0">{mv.data ? <MoverTable rows={mv.data.most_active} metric="volume" /> : <Spinner />}</Panel>
        <Panel title="Unusual volume (≥2× avg)" bodyClassName="p-0">{mv.data ? <MoverTable rows={mv.data.unusual_volume} metric="rvol" /> : <Spinner />}</Panel>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="Strongest bullish trends" bodyClassName="p-0" actions={<Link href="/trends" className="text-2xs text-accent">All trends</Link>}>
          {o.strongest_bullish.length ? <TrendRows rows={o.strongest_bullish} /> : <Empty title="No strong bullish trends" />}
        </Panel>
        <Panel title="Strongest bearish trends" bodyClassName="p-0">
          {o.strongest_bearish.length ? <TrendRows rows={o.strongest_bearish} /> : <Empty title="No strong bearish trends" />}
        </Panel>
        <Panel title="Trend distribution (daily)">
          <ul className="space-y-1">
            {Object.entries(o.trend_distribution).sort((a, z) => z[1] - a[1]).map(([k, v]) => (
              <li key={k} className="flex items-center justify-between text-xs"><TrendBadge value={k} /><span className="num">{v}</span></li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="New opportunities (7 days)" bodyClassName="p-0" actions={<Link href="/opportunities" className="text-2xs text-accent">All</Link>}>
          {o.new_opportunities.length === 0 ? <Empty title="No new signals" /> : (
            <Table><tbody>
              {o.new_opportunities.map((s) => (
                <tr key={s.id} className="hover:bg-raised/50">
                  <Td><SymbolLink symbol={s.symbol} /></Td>
                  <Td><Link href={`/opportunities?id=${s.id}`} className="hover:text-accent">{label(s.signal_type)}</Link></Td>
                  <Td><DirectionBadge value={s.direction} /></Td>
                  <Td className="text-muted">{s.timeframe}</Td>
                  <Td align="right" num>{price(s.reference_price)}</Td>
                </tr>
              ))}
            </tbody></Table>
          )}
        </Panel>
        <Panel title="Upcoming corporate events" bodyClassName="p-0" actions={<Link href="/news" className="text-2xs text-accent">Calendar</Link>}>
          {o.upcoming_events.length === 0 ? <Empty title="No upcoming events on file" /> : (
            <Table><tbody>
              {o.upcoming_events.map((e, i) => (
                <tr key={i}><Td><SymbolLink symbol={e.symbol} /></Td><Td>{label(e.action_type)}</Td>
                  <Td num>{date(e.ex_date ?? e.payment_date)}</Td>
                  <Td align="right" num>{e.amount != null ? `${e.currency ?? ""} ${e.amount}` : ""}</Td></tr>
              ))}
            </tbody></Table>
          )}
        </Panel>
        <div className="space-y-3">
          <Panel title="Paper portfolios" actions={<Link href="/portfolio" className="text-2xs text-accent">Open</Link>}>
            {pf.data?.items.length ? pf.data.items.slice(0, 3).map((p) => (
              <div key={p.id} className="flex justify-between text-xs"><span>{p.name}</span>
                <span className="num">{kes(p.total_value, 0)} <Change v={p.total_return} /></span></div>
            )) : <p className="text-xs text-muted">No portfolios yet. <Link className="text-accent" href="/portfolio">Create one</Link>.</p>}
          </Panel>
          <Panel title="Watchlist highlights" bodyClassName="p-0" actions={<Link href="/watchlists" className="text-2xs text-accent">Open</Link>}>
            {o.watchlist_highlights.length ? (
              <Table><tbody>{o.watchlist_highlights.map((w, i) => (
                <tr key={i}><Td><SymbolLink symbol={w.symbol} /></Td><Td className="text-muted">{w.watchlist}</Td>
                  <Td align="right" num>{price(w.last_close)}</Td><Td align="right"><Change v={w.change_pct} /></Td></tr>
              ))}</tbody></Table>
            ) : <Empty title="No watchlist items" />}
          </Panel>
        </div>
      </div>
      <p className="text-2xs text-muted">{o.turnover_note} {mv.data?.note} Equal-weight move is an average of
        instrument returns, not an NSE index ({pct(o.equal_weight_change)}).</p>
    </div>
  );
}

function TrendRows({ rows }: { rows: MoverItem[] }) {
  return (
    <Table><tbody>{rows.map((r) => (
      <tr key={r.id} className="hover:bg-raised/50">
        <Td><SymbolLink symbol={r.symbol} name={r.name} synthetic={r.is_synthetic} /></Td>
        <Td align="right"><Change v={r.change_pct} /></Td>
        <Td align="right"><ScoreMeter value={r.trading_score} title="Trading opportunity score" /></Td>
      </tr>
    ))}</tbody></Table>
  );
}
