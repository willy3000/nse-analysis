import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BaseRateTable, FreshnessBadge, ScoreMeter } from "@/components/market";
import type { Freshness } from "@/lib/types";

const f = (status: Freshness["status"], extra: Partial<Freshness> = {}): Freshness => ({
  status, reason: null, as_of: "2026-11-06T12:00:00Z", age_seconds: 1200, latency_class: "DELAYED",
  provider: "mystocks", detail: "Delayed by at least 15 minutes.", ...extra,
});

describe("FreshnessBadge", () => {
  it("labels delayed data as delayed, never live", () => {
    render(<FreshnessBadge f={f("DELAYED")} />);
    expect(screen.getByText(/Delayed/)).toBeInTheDocument();
    expect(screen.queryByText(/Live/)).toBeNull();
  });
  it("shows the stale reason", () => {
    render(<FreshnessBadge f={f("STALE", { reason: "FEED_FAILURE" })} />);
    expect(screen.getByText(/Feed failure/)).toBeInTheDocument();
  });
  it("treats a missing freshness object as unavailable", () => {
    render(<FreshnessBadge f={null} />);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });
  it("labels historical data", () => {
    render(<FreshnessBadge f={f("HISTORICAL", { latency_class: "HISTORICAL" })} />);
    expect(screen.getByText("Historical")).toBeInTheDocument();
  });
});

describe("ScoreMeter", () => {
  it("shows n/a for missing scores instead of zero", () => {
    render(<ScoreMeter value={null} />);
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });
  it("renders the score", () => {
    render(<ScoreMeter value={72.4} />);
    expect(screen.getByText("72")).toBeInTheDocument();
  });
});

describe("BaseRateTable", () => {
  it("explains when no outcomes exist", () => {
    render(<BaseRateTable rates={[]} />);
    expect(screen.getByText(/No tracked outcomes/)).toBeInTheDocument();
  });
  it("shows sample sizes and baseline", () => {
    render(<BaseRateTable rates={[{ signal_type: "X", timeframe: "1d", direction: "BULLISH", horizon_bars: 20, n: 12, mean_return: 0.02,
      median_return: 0.01, hit_rate: 0.55, invalidation_rate: 0.3, baseline_mean_return: 0.005, baseline_hit_rate: 0.5,
      excess_mean_return: 0.015, t_stat_naive: 1.1, sufficient_sample: false }]} />);
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText(/fewer than 30 observations/)).toBeInTheDocument();
  });
});
