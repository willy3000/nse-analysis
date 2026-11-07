# ADR-0005: Server-side sessions with HttpOnly cookies + CSRF double-submit

Status: Accepted (2026-11-07)

## Decision
- **Passwords:** hashed with Argon2id (`argon2-cffi` defaults).
- **Sessions:** login creates a random 256-bit token. Only its SHA-256 hash is stored in `user_sessions`, so a database leak does not expose live sessions.
  - The token is sent as an `HttpOnly; SameSite=Lax` cookie, with `Secure` in production.
  - Sessions have an idle timeout and an absolute timeout.
- **CSRF:** state-changing requests (POST/PUT/PATCH/DELETE) must send an `X-CSRF-Token` header equal to the readable `nse_csrf` cookie (double-submit).
- **Same origin:** the Next.js app proxies `/api/*` to FastAPI (Next rewrites), so the browser talks to a single origin. CORS is restricted to `CORS_ORIGINS`.
- **Authorisation:** every user-owned table (portfolios, watchlists, alerts, strategies, backtests, notifications, saved scans) carries `user_id`.
  - Repository functions always filter by the authenticated user.
  - Requests for another user's objects return 404 rather than 403, so existence is not leaked.
  - `/api/v1/admin/*` requires `role=admin`.
- **Rate limiting:** fixed-window counters per client IP and route class, in Redis when present and in-process otherwise. Login has a strict limit.

## Rejected
JWTs in localStorage. They can be exfiltrated through XSS and cannot be revoked without a denylist.
