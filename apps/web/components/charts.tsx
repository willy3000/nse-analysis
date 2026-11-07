"use client";

import {
  ColorType,
  createChart,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bar } from "@/lib/types";

function cssColor(name: string, alpha = 1): string {
  if (typeof window === "undefined") return "#888";
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v ? `rgba(${v.split(/\s+/).join(",")},${alpha})` : "#888";
}

function baseOptions(height: number) {
  return {
    height,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssColor("--muted"),
      fontSize: 11, fontFamily: "ui-monospace, Consolas, monospace" },
    grid: { vertLines: { color: cssColor("--line", 0.5) }, horzLines: { color: cssColor("--line", 0.5) } },
    rightPriceScale: { borderColor: cssColor("--line") },
    timeScale: { borderColor: cssColor("--line"), timeVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
    autoSize: true,
  };
}

export interface Overlay {
  key: string;
  color: string;
  points: { date: string; value: number | null }[];
}
export interface Level {
  price: number;
  type: "SUPPORT" | "RESISTANCE";
}

/** Candlesticks when OHLC is reported for >= 90% of bars; otherwise a close line (no invented OHLC). */
export function PriceChart({ bars, overlays = [], levels = [], height = 380 }: { bars: Bar[]; overlays?: Overlay[];
  levels?: Level[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart: IChartApi = createChart(ref.current, baseOptions(height));
    const ohlcShare = bars.filter((b) => b.ohlc_available).length / bars.length;
    const up = cssColor("--up");
    const down = cssColor("--down");
    let main: ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;
    if (ohlcShare >= 0.9) {
      const s = chart.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up,
        wickDownColor: down });
      s.setData(bars.map((b) => b.ohlc_available && b.open != null && b.high != null && b.low != null
        ? { time: b.date as Time, open: b.open, high: b.high, low: b.low, close: b.close }
        : { time: b.date as Time }));
      main = s;
    } else {
      const s = chart.addLineSeries({ color: cssColor("--accent"), lineWidth: 2 });
      s.setData(bars.map((b) => ({ time: b.date as Time, value: b.close })));
      main = s;
    }
    const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b, i) => b.volume == null ? { time: b.date as Time } : {
      time: b.date as Time, value: b.volume,
      color: (i > 0 && b.close < (bars[i - 1]?.close ?? b.close)) ? cssColor("--down", 0.45) : cssColor("--up", 0.45),
    }));
    for (const o of overlays) {
      const s = chart.addLineSeries({ color: o.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false });
      s.setData(o.points.map((p) => p.value == null ? { time: p.date as Time } : { time: p.date as Time, value: p.value }));
    }
    for (const lv of levels) {
      main.createPriceLine({ price: lv.price, color: lv.type === "SUPPORT" ? cssColor("--up", 0.6) : cssColor("--down", 0.6),
        lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: lv.type === "SUPPORT" ? "S" : "R" });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, overlays, levels, height]);
  return <div ref={ref} className="w-full" style={{ height }} aria-label="Price chart" role="img" />;
}

export function LineChart({ series, height = 260, percent }: { series: { name: string; color: string;
  points: { date: string; value: number | null }[] }[]; height?: number; percent?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, baseOptions(height));
    for (const s of series) {
      const line = chart.addLineSeries({ color: s.color, lineWidth: 2, title: s.name,
        priceFormat: percent ? { type: "custom", formatter: (v: number) => `${(v * 100).toFixed(1)}%` } : undefined });
      line.setData(s.points.filter((p) => p.value != null).map((p) => ({ time: p.date as Time, value: p.value as number })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, height, percent]);
  return <div ref={ref} className="w-full" style={{ height }} role="img" aria-label="Line chart" />;
}

export function themeColor(name: string, alpha = 1) {
  return cssColor(name, alpha);
}
