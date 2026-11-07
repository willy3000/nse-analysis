/** Response shapes of the /api/v1 endpoints used by the UI (mirrors services/api routers). */

export type Universe = "real" | "synthetic";
export type FreshnessStatus = "LIVE" | "DELAYED" | "END_OF_DAY" | "HISTORICAL" | "STALE" | "UNAVAILABLE";

export interface Freshness {
  status: FreshnessStatus;
  reason: string | null;
  as_of: string | null;
  age_seconds: number | null;
  latency_class: string | null;
  provider: string | null;
  detail: string;
}

export interface PriceView {
  price: number | null;
  previous_close: number | null;
  volume: number | null;
  source: "quote" | "daily_bar" | null;
  provider: string | null;
  is_synthetic: boolean;
  attribution: string | null;
  last_traded_date: string | null;
  freshness: Freshness;
}

export interface InstrumentRef {
  id: number;
  symbol: string;
  name: string;
  security_type: string;
  sector: string | null;
  currency: string;
  trading_status: string;
  is_synthetic: boolean;
  isin: string | null;
  lot_size: number | null;
}

export interface Snapshot {
  as_of_ts: string;
  session_date: string | null;
  bars_available: number;
  last_close: number | null;
  prev_close: number | null;
  change_pct: number | null;
  volume: number | null;
  avg_volume_20: number | null;
  relative_volume: number | null;
  avg_traded_value_20: number | null;
  trading_frequency_60: number | null;
  rsi_14: number | null;
  return_1w: number | null;
  return_1m: number | null;
  return_3m: number | null;
  return_6m: number | null;
  return_1y: number | null;
  return_ytd: number | null;
  volatility_20: number | null;
  volatility_60: number | null;
  atr_pct_14: number | null;
  max_drawdown_1y: number | null;
  drawdown_from_high: number | null;
  high_52w: number | null;
  low_52w: number | null;
  relative_strength_3m: number | null;
  dividend_yield: number | null;
  pe_ratio: number | null;
  trend_class: string | null;
  trading_score: number | null;
  quality_score: number | null;
  risk_score: number | null;
  calc_version: string;
}

export interface ScoreComponent {
  key: string;
  label: string;
  weight: number;
  description: string;
  raw: number | string | null;
  sub_score: number | null;
  available: boolean;
}
export interface ScoreBlock {
  score: number | null;
  coverage: number;
  components: ScoreComponent[];
  insufficient_data: boolean;
  higher_is: string;
}
export interface Scores {
  calc_version: string;
  trading: ScoreBlock;
  quality: ScoreBlock;
  risk: ScoreBlock;
}

export interface Evidence {
  label: string;
  value: number | string | boolean | null;
  detail?: string;
  stance?: string;
}

export interface Signal {
  id: number;
  category: string;
  signal_type: string;
  direction: "BULLISH" | "BEARISH" | "NEUTRAL";
  timeframe: string;
  bar_ts: string;
  detected_at: string;
  reference_price: number;
  evidence: Evidence[];
  invalidation: { type: string; price: number | null; condition: string };
  risk_factors: string[];
  status: string;
  status_changed_at: string | null;
  expires_at: string;
  is_backfill: boolean;
  calc_version: string;
}

export interface BaseRate {
  signal_type: string;
  timeframe: string;
  direction: string;
  horizon_bars: number;
  n: number;
  mean_return: number;
  median_return: number;
  hit_rate: number;
  invalidation_rate: number;
  baseline_mean_return: number | null;
  baseline_hit_rate: number | null;
  excess_mean_return: number | null;
  t_stat_naive: number | null;
  sufficient_sample: boolean;
}

export interface OpportunityItem extends Signal {
  instrument: InstrumentRef;
  current_price: number | null;
  change_since_signal: number | null;
  risk_score: number | null;
  historical_performance: BaseRate[] | undefined;
}

export interface Paged<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
  universe?: Universe;
}

export interface MoverItem extends InstrumentRef {
  last_close: number | null;
  change_pct: number | null;
  volume: number | null;
  relative_volume: number | null;
  trend_class: string | null;
  trading_score: number | null;
  risk_score: number | null;
  session_date: string | null;
}

export interface Overview {
  universe: Universe;
  is_synthetic: boolean;
  session_date: string | null;
  breadth: { advancing: number; declining: number; unchanged: number; no_trades: number; not_updated: number;
    total: number };
  equal_weight_change: number | null;
  turnover_estimate: number | null;
  turnover_note: string;
  trend_distribution: Record<string, number>;
  strongest_bullish: MoverItem[];
  strongest_bearish: MoverItem[];
  new_opportunities: { id: number; symbol: string; name: string; signal_type: string; category: string;
    direction: string; timeframe: string; bar_ts: string; reference_price: number }[];
  upcoming_events: { symbol: string; action_type: string; title: string | null; ex_date: string | null;
    payment_date: string | null; amount: number | null; currency: string | null }[];
  watchlist_highlights: { watchlist: string; symbol: string; name: string; change_pct: number | null;
    last_close: number | null; trend_class: string | null }[];
  portfolio_count: number;
  freshness_summary: Record<string, number>;
}

export interface Bar {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
  ohlc_available: boolean;
  is_partial: boolean;
}

export interface User {
  id: number;
  email: string;
  display_name: string | null;
  role: string;
}

export interface DataHealth {
  overall: "COMPLETE" | "PARTIAL" | "DEGRADED" | "UNAVAILABLE";
  message: string;
  last_completed_session: string;
  best_available_latency: string | null;
  real_time_available: boolean;
  universes: Record<string, { instruments: number; with_history: number; current: number;
    stale: { symbol: string; last_bar: string }[]; no_history: string[]; suspended: string[] }>;
  open_quality_issues: number;
  recent_failed_runs: { id: number; job: string; status: string; started_at: string; error: string | null }[];
  cache: { configured: boolean; reachable: boolean | null; last_error: string | null };
}
