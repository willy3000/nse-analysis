/**
 * Typed client for the FastAPI backend (same-origin via Next rewrites).
 * - Sends cookies; attaches the CSRF double-submit header on mutating requests.
 * - Converts the backend's {"error": {code, message}} envelope into ApiError.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m?.[1] ? decodeURIComponent(m[1]) : undefined;
}

async function ensureCsrf(): Promise<string> {
  let token = readCookie("nse_csrf");
  if (!token) {
    await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
    token = readCookie("nse_csrf");
  }
  if (!token) throw new ApiError(0, "CSRF_UNAVAILABLE", "Could not obtain a CSRF token");
  return token;
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function qs(params?: Query): string {
  if (!params) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function api<T>(path: string, init: { method?: string; body?: unknown; query?: Query } = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["X-CSRF-Token"] = await ensureCsrf();
  }
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}${qs(init.query)}`, {
      method,
      headers,
      credentials: "same-origin",
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "NETWORK", "The API is unreachable. Check that the backend is running.");
  }
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? `HTTP_${res.status}`, err?.message ?? res.statusText, err?.details);
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { error: { code: "BAD_RESPONSE", message: text.slice(0, 200) } };
  }
}
