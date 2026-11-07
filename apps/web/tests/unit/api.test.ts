import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, qs } from "@/lib/api";

afterEach(() => { vi.restoreAllMocks(); document.cookie = "nse_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT"; });

describe("api client", () => {
  it("builds query strings without empty values", () => {
    expect(qs({ a: 1, b: undefined, c: "", d: null, e: "x y" })).toBe("?a=1&e=x+y");
    expect(qs({})).toBe("");
  });
  it("parses the error envelope into ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "Instrument not found" } }), { status: 404 })));
    await expect(api("/instruments/NOPE")).rejects.toMatchObject({ status: 404, code: "NOT_FOUND", message: "Instrument not found" });
  });
  it("sends the CSRF header on mutations", async () => {
    document.cookie = "nse_csrf=tok123";
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api("/watchlists", { method: "POST", body: { name: "x" } });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
  });
  it("reports network failures clearly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    const err = await api("/market/status").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("NETWORK");
  });
});
