/** Formatting helpers. Missing values render as an em dash - never as zero. */

export const DASH = "—";

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function num(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return v.toLocaleString("en-KE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function price(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  const digits = Math.abs(v) < 1 ? 4 : 2;
  return num(v, digits);
}

export function kes(v: number | null | undefined, digits = 2): string {
  return isNum(v) ? `KES ${num(v, digits)}` : DASH;
}

export function compact(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

/** Decimal ratio -> percent string (0.0523 -> "+5.23%"). */
export function pct(v: number | null | undefined, digits = 2, signed = true): string {
  if (!isNum(v)) return DASH;
  const s = (v * 100).toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function tone(v: number | null | undefined): "up" | "down" | "flat" {
  if (!isNum(v) || v === 0) return "flat";
  return v > 0 ? "up" : "down";
}

export function toneClass(v: number | null | undefined): string {
  const t = tone(v);
  return t === "up" ? "text-up" : t === "down" ? "text-down" : "text-muted";
}

export function date(v: string | null | undefined): string {
  if (!v) return DASH;
  const d = new Date(v.length === 10 ? `${v}T00:00:00Z` : v);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

export function datetime(v: string | null | undefined): string {
  if (!v) return DASH;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    timeZone: "Africa/Nairobi" }) + " EAT";
}

export function age(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return DASH;
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export function label(v: string | null | undefined): string {
  if (!v) return DASH;
  return v.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}
