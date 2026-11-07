"use client";

import { LineChart, themeColor } from "@/components/charts";
import { KV } from "@/components/market";
import { PageHeader } from "@/components/shell";
import { Badge, ErrorState, Panel, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { date, label, num, pct } from "@/lib/format";
import { useApi, useMe, useUniverse } from "@/lib/hooks";

interface Methodology {
  calc_version: string; minimum_universe: number; disclaimer: string; trend_points: Record<string, number>;
  scores: Record<string, { label: string; higher_is: string; min_coverage: number; components: { key: string; weight: number; label: string;
    method: string; description: string }[] }>;
  calibration: { universe: string; computed_at: string | null; status?: string; mean_ic?: number; ic_t_stat?: number | null;
    share_ic_positive?: number; mean_top_minus_bottom_tercile?: number; verdict?: string; dates?: number; method?: string;
    avg_instruments_per_date?: number; series?: { date: string; ic: number }[] };
}
interface Fee { code: string; name: string; components: Record<string, number>; effective_from: string; source_notes: string; is_default: boolean }

export default function SettingsPage() {
  const [universe, setUniverse] = useUniverse();
  const me = useMe();
  const m = useApi<Methodology>("/methodology/scores", { universe: universe ?? undefined });
  const fees = useApi<Fee[]>("/fee-schedules");
  const cal = m.data?.calibration;
  return (
    <div className="space-y-3">
      <PageHeader title="Settings & methodology" />
      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="Account">
          <KV k="Email" v={me.data?.email ?? "—"} /><KV k="Role" v={me.data?.role ?? "—"} />
          <div className="mt-3 space-y-1 text-xs">
            <label htmlFor="universe" className="text-muted">Data universe</label>
            <Select id="universe" value={universe ?? ""} onChange={(e) => setUniverse((e.target.value || null) as "real" | "synthetic" | null)} className="w-full">
              <option value="">Automatic (real when available)</option><option value="real">Real market data</option><option value="synthetic">Synthetic development data</option></Select>
            <p className="text-2xs text-muted">Real and synthetic instruments are never mixed in one view or aggregate.</p>
          </div>
        </Panel>
        <Panel title="Fee schedules" className="lg:col-span-2">
          {fees.isLoading ? <Spinner /> : (fees.data ?? []).map((f) => (
            <div key={f.code} className="space-y-1">
              <div className="flex items-center gap-2 text-sm font-semibold">{f.name} {f.is_default && <Badge tone="accent">default</Badge>}</div>
              <div className="grid grid-cols-2 gap-x-6 md:grid-cols-3">{Object.entries(f.components).map(([k, v]) => <KV key={k} k={label(k)} v={pct(v, k.startsWith("brokerage_min") ? 0 : 2, false)} />)}</div>
              <p className="text-2xs text-muted">Effective {date(f.effective_from)}. {f.source_notes}</p>
            </div>))}
        </Panel>
      </div>
      {m.error ? <ErrorState error={m.error} /> : !m.data ? <Spinner /> : (
        <>
          <Panel title={`Score calibration — trading score (${cal?.universe} universe)`}>
            {cal?.mean_ic == null ? <p className="text-xs text-muted">Not computed yet ({label(cal?.status)}). Requires at least 5 instruments with a year of history.</p> : (
              <div className="grid gap-3 lg:grid-cols-[18rem_1fr]">
                <div>
                  <KV k="Verdict" v={<span className="text-right font-sans">{cal.verdict}</span>} />
                  <KV k="Mean rank IC" v={num(cal.mean_ic, 3)} /><KV k="IC t-statistic" v={num(cal.ic_t_stat, 2)} />
                  <KV k="Months with IC > 0" v={pct(cal.share_ic_positive, 0, false)} /><KV k="Top − bottom tercile (21d)" v={pct(cal.mean_top_minus_bottom_tercile, 2)} />
                  <KV k="Months tested" v={cal.dates} /><KV k="Avg instruments" v={num(cal.avg_instruments_per_date, 1)} />
                  <p className="mt-2 text-2xs text-muted">{cal.method} Computed {date(cal.computed_at)}.</p>
                </div>
                <LineChart series={[{ name: "Monthly IC", color: themeColor("--accent"), points: (cal.series ?? []).map((s) => ({ date: s.date, value: s.ic })) }]} height={220} />
              </div>
            )}
          </Panel>
          <div className="grid gap-3 lg:grid-cols-3">
            {Object.entries(m.data.scores).map(([k, s]) => (
              <Panel key={k} title={s.label} bodyClassName="p-0" subtitle={`Higher = ${s.higher_is}. Scored only if ≥${pct(s.min_coverage, 0, false)} of weight is available.`}>
                <Table><thead><tr><Th>Component</Th><Th align="right">Weight</Th><Th>Method</Th></tr></thead>
                  <tbody>{s.components.map((c) => <tr key={c.key} title={c.description}><Td>{c.label}<div className="text-2xs text-muted">{c.description}</div></Td>
                    <Td align="right" num>{pct(c.weight, 0, false)}</Td><Td className="text-2xs text-muted">{c.method}</Td></tr>)}</tbody></Table>
              </Panel>
            ))}
          </div>
          <p className="text-2xs text-muted">Methodology version {m.data.calc_version}. Universe minimum {m.data.minimum_universe} instruments. {m.data.disclaimer}</p>
        </>
      )}
    </div>
  );
}
