import { describe, expect, it } from "vitest";

import { compact, DASH, kes, num, pct, price, tone } from "@/lib/format";

describe("format", () => {
  it("renders missing values as a dash, never zero", () => {
    for (const v of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(num(v as number)).toBe(DASH);
      expect(pct(v as number)).toBe(DASH);
      expect(price(v as number)).toBe(DASH);
      expect(kes(v as number)).toBe(DASH);
      expect(compact(v as number)).toBe(DASH);
    }
  });
  it("formats ratios as signed percentages", () => {
    expect(pct(0.0523)).toBe("+5.23%");
    expect(pct(-0.1)).toBe("-10.00%");
    expect(pct(0.05, 1, false)).toBe("5.0%");
    expect(pct(0)).toBe("0.00%");
  });
  it("uses more precision for sub-shilling prices", () => {
    expect(price(0.4567)).toBe("0.4567");
    expect(price(34.5)).toBe("34.50");
  });
  it("classifies tone", () => {
    expect(tone(0.01)).toBe("up");
    expect(tone(-0.01)).toBe("down");
    expect(tone(0)).toBe("flat");
    expect(tone(null)).toBe("flat");
  });
  it("compacts large numbers", () => {
    expect(compact(1_220_000_000_000)).toBe("1220.00B");
    expect(compact(3_773_465)).toBe("3.77M");
  });
});
