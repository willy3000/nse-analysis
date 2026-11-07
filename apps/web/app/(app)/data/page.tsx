"use client";

import { useState } from "react";

import { PageHeader } from "@/components/shell";
import { Badge, Empty, ErrorState, Panel, Spinner, Stat, Table, Tabs, Td, Th } from "@/components/ui";
import { date, datetime, label, pct } from "@/lib/format";
import { useApi, useUniverse } from "@/lib/hooks";
import type { DataHealth } from "@/lib/types";

interface Provider { code: string; name: string; latency_class: string; delay_minutes: number | null; is_synthetic: boolean; enabled: boolean;
  configured: boolean; status: string; last_success_at: string | null; last_failure_at: string | null; last_error: string | null;
  consecutive_failures: number; latest_market_ts: string | null; rate_limit: { limit: number; remaining: number; reset_at: string } | null;
  instruments: number; attribution: string | null; notes: string | null }
interface Run { id: number; job: string; provider: string | null; status: string; started_at: string; finished_at: string | null;
  requested: number; succeeded: number; failed: number; rows_written: number; error: string | null }
interface Issues { summary: Record<string, number>; items: { id: number; symbol: string | null; provider: string | null; issue_type: string;
  severity: string; observed_ts: string | null; detected_at: string; details: Record<string, unknown> | null }[] }
interface Coverage { items: { symbol: string; name: string; security_type: string; trading_status: string; provider: string | null;
  first_bar: string | null; last_bar: string | null; bars: number; ohlc_share: number | null; zero_volume_share: number | null;
  non_ok_quality_share: number | null; latest_quote: string | null }[] }

const STATUS_TONE: Record<string, "up" | "warn" | "down" | "neutral"> = { HEALTHY: "up", DEGRADED: "warn", DOWN: "down", UNCONFIGURED: "neutral",
  DISABLED: "neutral", UNKNOWN: "neutral" };
const RUN_TONE: Record<string, "up" | "warn" | "down" | "neutral" | "info"> = { succeeded: "up", partial: "warn", failed: "down", skipped: "neutral", running: "info" };

export default function DataCentrePage() {
  const [universe] = useUniverse();
  const [tab, setTab] = useState<"providers" | "coverage" | "runs" | "issues">("providers");
  const health = useApi<DataHealth>("/data/health", undefined, { refetchInterval: 30_000 });
  const providers = useApi<Provider[]>("/data/providers", undefined, { refetchInterval: 30_000 });
  const runs = useApi<Run[]>(tab === "runs" ? "/data/runs" : null, { limit: 200 });
  const issues = useApi<Issues>(tab === "issues" ? "/data/issues" : null, { limit: 300 });
  const coverage = useApi<Coverage>(tab === "coverage" ? "/data/coverage" : null, { universe: universe ?? undefined });
  const h = health.data;
  return (
    <div className="space-y-3">
      <PageHeader title="Data Centre" description="What data the platform is running on: provider connectivity, latency, coverage, ingestion runs and data-quality issues." />
      {health.error ? <ErrorState error={health.error} /> : !h ? <Spinner /> : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Panel className="col-span-2"><Stat label="Overall data status" value={<Badge tone={h.overall === "COMPLETE" ? "up" : h.overall === "UNAVAILABLE" ? "down" : "warn"} className="text-xs">{h.overall}</Badge>} sub={h.message} /></Panel>
          <Panel><Stat label="Best real-data latency" value={label(h.best_available_latency)} sub={h.real_time_available ? "real-time available" : "no real-time feed"} /></Panel>
          <Panel><Stat label="Last completed session" value={date(h.last_completed_session)} /></Panel>
          <Panel><Stat label="Open quality issues" value={h.open_quality_issues} sub={`cache: ${h.cache.configured ? (h.cache.reachable ? "redis ok" : "redis unreachable") : "not configured (optional)"}`} /></Panel>
          {Object.entries(h.universes).map(([u, v]) => (
            <Panel key={u} className="col-span-2 md:col-span-5" title={`${u} universe`}>
              <div className="flex flex-wrap gap-4 text-xs">
                <span>{v.instruments} priced instruments</span><span>{v.with_history} with history</span>
                <span className="text-up">{v.current} current</span><span className={v.stale.length ? "text-warn" : ""}>{v.stale.length} stale</span>
                <span>{v.no_history.length} without history</span><span>{v.suspended.length} suspended</span>
              </div>
              {v.stale.length > 0 && <p className="mt-1 text-2xs text-muted">Stale: {v.stale.slice(0, 30).map((s) => `${s.symbol} (${date(s.last_bar)})`).join(", ")}
                {" — "}may be no trades, a provider gap or a feed failure; see coverage.</p>}
            </Panel>
          ))}
        </div>
      )}
      <Tabs value={tab} onChange={setTab} options={[{ value: "providers", label: "Providers" }, { value: "coverage", label: "Coverage" },
        { value: "runs", label: "Ingestion runs" }, { value: "issues", label: "Quality issues" }]} />
      {tab === "providers" && (providers.isLoading ? <Spinner /> : (
        <div className="grid gap-3 lg:grid-cols-2">{(providers.data ?? []).map((p) => (
          <Panel key={p.code} title={p.name} actions={<><Badge tone={STATUS_TONE[p.status] ?? "neutral"}>{p.status}</Badge>
            <Badge tone={p.latency_class === "LIVE" ? "up" : p.latency_class === "DELAYED" ? "info" : "neutral"}>{label(p.latency_class)}{p.delay_minutes ? ` ${p.delay_minutes}m` : ""}</Badge>
            {p.is_synthetic && <Badge tone="warn">synthetic</Badge>}</>}>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <span className="text-muted">Enabled / configured</span><span>{p.enabled ? "yes" : "no"} / {p.configured ? "yes" : "no"}</span>
              <span className="text-muted">Instruments linked</span><span className="num">{p.instruments}</span>
              <span className="text-muted">Last success</span><span>{datetime(p.last_success_at)}</span>
              <span className="text-muted">Last failure</span><span>{datetime(p.last_failure_at)}</span>
              <span className="text-muted">Latest market timestamp</span><span>{datetime(p.latest_market_ts)}</span>
              <span className="text-muted">Rate limit</span><span className="num">{p.rate_limit ? `${p.rate_limit.remaining}/${p.rate_limit.limit} (reset ${datetime(p.rate_limit.reset_at)})` : "—"}</span>
            </div>
            {p.last_error && <p className="mt-2 break-words rounded border border-down/30 bg-down/5 p-1.5 text-2xs text-down">{p.last_error}</p>}
            {p.notes && <p className="mt-2 text-2xs text-muted">{p.notes}</p>}
            {p.attribution && <p className="text-2xs text-muted">Attribution: {p.attribution}</p>}
          </Panel>))}</div>
      ))}
      {tab === "coverage" && (
        <Panel bodyClassName="p-0">{coverage.isLoading ? <Spinner /> : !coverage.data?.items.length ? <Empty title="No instruments" /> : (
          <Table className="max-h-[70vh]"><thead><tr><Th>Symbol</Th><Th>Type</Th><Th>Status</Th><Th>Provider</Th><Th>First bar</Th><Th>Last bar</Th>
            <Th align="right">Bars</Th><Th align="right">OHLC</Th><Th align="right">Zero-vol</Th><Th align="right">Flagged</Th><Th>Latest quote</Th></tr></thead>
            <tbody>{coverage.data.items.map((c) => (
              <tr key={c.symbol}><Td className="font-semibold">{c.symbol}</Td><Td>{label(c.security_type)}</Td><Td>{label(c.trading_status)}</Td><Td>{c.provider ?? "—"}</Td>
                <Td num>{date(c.first_bar)}</Td><Td num>{date(c.last_bar)}</Td><Td align="right" num>{c.bars}</Td>
                <Td align="right" num>{pct(c.ohlc_share, 0, false)}</Td><Td align="right" num>{pct(c.zero_volume_share, 0, false)}</Td>
                <Td align="right" num>{pct(c.non_ok_quality_share, 1, false)}</Td><Td num>{datetime(c.latest_quote)}</Td></tr>))}</tbody></Table>
        )}</Panel>
      )}
      {tab === "runs" && (
        <Panel bodyClassName="p-0">{runs.isLoading ? <Spinner /> : !runs.data?.length ? <Empty title="No runs yet" /> : (
          <Table className="max-h-[70vh]"><thead><tr><Th>#</Th><Th>Job</Th><Th>Provider</Th><Th>Status</Th><Th>Started</Th><Th align="right">Req</Th>
            <Th align="right">OK</Th><Th align="right">Failed</Th><Th align="right">Rows</Th><Th>Error</Th></tr></thead>
            <tbody>{runs.data.map((r) => (
              <tr key={r.id}><Td num>{r.id}</Td><Td>{r.job}</Td><Td>{r.provider ?? "—"}</Td><Td><Badge tone={RUN_TONE[r.status] ?? "neutral"}>{r.status}</Badge></Td>
                <Td num>{datetime(r.started_at)}</Td><Td align="right" num>{r.requested}</Td><Td align="right" num>{r.succeeded}</Td><Td align="right" num>{r.failed}</Td>
                <Td align="right" num>{r.rows_written}</Td><Td className="max-w-md truncate text-2xs text-down" >{r.error ?? ""}</Td></tr>))}</tbody></Table>
        )}</Panel>
      )}
      {tab === "issues" && (
        <Panel bodyClassName="p-0">{issues.isLoading ? <Spinner /> : !issues.data ? null : (
          <>
            <div className="flex flex-wrap gap-1 p-2">{Object.entries(issues.data.summary).map(([k, v]) => <Badge key={k}>{label(k)} {v}</Badge>)}</div>
            {issues.data.items.length === 0 ? <Empty title="No open issues" /> : (
              <Table className="max-h-[65vh]"><thead><tr><Th>Detected</Th><Th>Symbol</Th><Th>Provider</Th><Th>Issue</Th><Th>Severity</Th><Th>Observed</Th><Th>Details</Th></tr></thead>
                <tbody>{issues.data.items.map((i) => (
                  <tr key={i.id}><Td num>{datetime(i.detected_at)}</Td><Td>{i.symbol ?? "—"}</Td><Td>{i.provider ?? "—"}</Td><Td>{label(i.issue_type)}</Td>
                    <Td><Badge tone={i.severity === "error" ? "down" : i.severity === "warn" ? "warn" : "neutral"}>{i.severity}</Badge></Td>
                    <Td num>{date(i.observed_ts)}</Td><Td className="max-w-md truncate text-2xs text-muted">{JSON.stringify(i.details)}</Td></tr>))}</tbody></Table>
            )}
          </>
        )}</Panel>
      )}
    </div>
  );
}
