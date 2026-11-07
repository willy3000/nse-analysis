"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { PageHeader } from "@/components/shell";
import { Badge, Button, Empty, ErrorState, Field, Input, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { datetime, label } from "@/lib/format";
import { useApi } from "@/lib/hooks";

interface Rule { id: number; rule_type: string; symbol: string | null; params: Record<string, unknown>; cooldown_minutes: number;
  is_active: boolean; last_triggered_at: string | null; created_at: string }
interface Alerts { rules: Rule[]; recent_events: { id: number; rule_type: string; triggered_at: string; message: string }[];
  channels: Record<string, boolean>; note: string }
interface Notes { unread: number; items: { id: number; title: string; body: string; link: string | null; created_at: string; read_at: string | null }[] }

const TYPES: Record<string, { params: string[]; symbolOptional?: boolean; help: string }> = {
  PRICE_ABOVE: { params: ["price"], help: "Close crosses above a level (edge-triggered)" },
  PRICE_BELOW: { params: ["price"], help: "Close crosses below a level" },
  RSI_BELOW: { params: ["level"], help: "RSI(14) falls below level, e.g. 30 (oversold)" },
  RSI_ABOVE: { params: ["level"], help: "RSI(14) rises above level, e.g. 70" },
  SMA_CROSS_UP: { params: [], help: "SMA20 crosses above SMA50" },
  SMA_CROSS_DOWN: { params: [], help: "SMA20 crosses below SMA50" },
  VOLUME_SPIKE: { params: ["multiple"], help: "Volume ≥ multiple × 20-session average" },
  SIGNAL_DETECTED: { params: [], symbolOptional: true, help: "Any new opportunity signal (optionally for one symbol)" },
  DIVIDEND_ANNOUNCED: { params: [], symbolOptional: true, help: "New cash dividend on file" },
  ANNOUNCEMENT: { params: [], symbolOptional: true, help: "New news/announcement item" },
  RISK_SCORE_ABOVE: { params: ["level"], help: "Risk score rises above level" },
  FUNDAMENTAL_CHANGE: { params: ["metric", "change"], help: "Metric (e.g. dividend_yield) changes by ≥ change (0.2 = 20%)" },
};

function AlertsInner() {
  const qc = useQueryClient();
  const params = useSearchParams();
  const a = useApi<Alerts>("/alerts");
  const n = useApi<Notes>("/notifications", { limit: 100 });
  const [type, setType] = useState("PRICE_ABOVE");
  const [symbol, setSymbol] = useState(params.get("symbol") ?? "");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [cooldown, setCooldown] = useState("1440");
  const [err, setErr] = useState<unknown>(null);
  const refresh = () => qc.invalidateQueries({ predicate: (q) => ["/alerts", "/notifications"].includes(String(q.queryKey[0])) });
  async function create() {
    setErr(null);
    const p: Record<string, unknown> = {};
    for (const k of TYPES[type]!.params) p[k] = k === "metric" ? vals[k] : Number(vals[k]);
    try {
      await api("/alerts", { method: "POST", body: { rule_type: type, symbol: symbol || null, params: p, cooldown_minutes: Number(cooldown) || 60 } });
      setVals({});
      refresh();
    } catch (e) { setErr(e); }
  }
  return (
    <div className="space-y-3">
      <PageHeader title="Alerts" description={a.data?.note ?? "Rules are evaluated after each analytics run."} />
      <div className="grid gap-3 xl:grid-cols-[22rem_1fr]">
        <Panel title="New alert">
          <div className="space-y-2">
            <Field label="Rule" hint={TYPES[type]!.help}><Select value={type} onChange={(e) => setType(e.target.value)}>
              {Object.keys(TYPES).map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field>
            <Field label={`Symbol${TYPES[type]!.symbolOptional ? " (optional)" : ""}`}><Input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></Field>
            {TYPES[type]!.params.map((k) => <Field key={k} label={label(k)}><Input value={vals[k] ?? ""} inputMode={k === "metric" ? "text" : "decimal"}
              onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></Field>)}
            <Field label="Cooldown (minutes)" hint="Minimum time between notifications for this rule"><Input inputMode="numeric" value={cooldown} onChange={(e) => setCooldown(e.target.value)} /></Field>
            <p className="text-2xs text-muted">Channels: in-app {a.data?.channels.in_app ? "✓" : "✕"} · email {a.data?.channels.email ? "✓" : "not configured"}</p>
            {err ? <ErrorState error={err} /> : null}
            <Button variant="primary" onClick={create}>Create alert</Button>
          </div>
        </Panel>
        <div className="space-y-3">
          <Panel title="Rules" bodyClassName="p-0">
            {a.isLoading ? <Spinner /> : (a.data?.rules ?? []).length === 0 ? <Empty title="No alert rules" /> : (
              <Table><thead><tr><Th>Rule</Th><Th>Symbol</Th><Th>Params</Th><Th>Cooldown</Th><Th>Last triggered</Th><Th>Active</Th><Th /></tr></thead>
                <tbody>{a.data!.rules.map((r) => (
                  <tr key={r.id}><Td>{label(r.rule_type)}</Td><Td>{r.symbol ?? "any"}</Td><Td className="num text-2xs">{JSON.stringify(r.params)}</Td>
                    <Td num>{r.cooldown_minutes}m</Td><Td num>{datetime(r.last_triggered_at)}</Td>
                    <Td><input type="checkbox" aria-label="Active" checked={r.is_active}
                      onChange={() => api(`/alerts/${r.id}?is_active=${!r.is_active}`, { method: "PATCH" }).then(refresh)} /></Td>
                    <Td><Button size="sm" variant="ghost" aria-label="Delete rule" onClick={() => api(`/alerts/${r.id}`, { method: "DELETE" }).then(refresh)}>✕</Button></Td></tr>))}</tbody></Table>
            )}
          </Panel>
          <Panel title={`Notifications (${n.data?.unread ?? 0} unread)`} actions={<Button size="sm" onClick={() => api("/notifications/read", { method: "POST", body: [] }).then(refresh)}>Mark all read</Button>}>
            {(n.data?.items ?? []).length === 0 ? <Empty title="No notifications" /> : (
              <ul className="space-y-2">{n.data!.items.map((x) => (
                <li key={x.id} className="flex items-start gap-2 text-xs">
                  {!x.read_at && <Badge tone="accent">new</Badge>}
                  <div><div className="font-medium">{x.title}</div><div>{x.link ? <Link className="hover:text-accent" href={x.link}>{x.body}</Link> : x.body}</div>
                    <div className="text-2xs text-muted">{datetime(x.created_at)}</div></div>
                </li>))}</ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

export default function AlertsPage() {
  return <Suspense><AlertsInner /></Suspense>;
}
