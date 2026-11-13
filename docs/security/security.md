# Security

| Control | Implementation |
|---|---|
| Password storage | Argon2id (`argon2-cffi`), rehashed when parameters change; policy: at least 10 characters, mixed case and a digit |
| Login abuse | Lockout for 15 minutes after 5 failures; constant-time check for unknown emails; login rate limit 10/min per IP |
| Sessions | Random 256-bit token in an `HttpOnly; SameSite=Lax` cookie (`Secure` in production). Only the SHA-256 hash is stored. 12 h idle and 14 day absolute expiry. Revoked on logout. |
| CSRF | Double-submit token (`nse_csrf` cookie must equal the `X-CSRF-Token` header) on every state-changing request, including login and registration |
| Authorisation | Every user-owned query filters by `user_id`; other users' objects return 404 (tested). Admin endpoints require `role=admin`. |
| Input validation | Pydantic schemas on all bodies and queries (bounds, enums, known feature names); strategy rules are a fixed whitelist, never evaluated as code |
| SQL injection | SQLAlchemy parameterised queries only; no string-built SQL from user input |
| CORS | Restricted to `CORS_ORIGINS`; the browser normally uses the same-origin proxy |
| Headers | API: nosniff, frame DENY, referrer policy, HSTS in production, `no-store` cache. Web: CSP (`default-src 'self'`, no third-party scripts), permissions policy. |
| Rate limiting | Per IP per minute (Redis-backed when available, otherwise in-process) |
| Audit log | `audit_log` records logins, failures, lockouts, user creation, portfolio changes and transactions |
| Secrets | Environment only (`.env` is git-ignored; `.env.example` has no values). Production refuses to start with the dev session secret or non-secure cookies. |
| Provider safety | The provider HTTP client is GET-only: it cannot call MyStocks trading or brokerage endpoints |
| Synthetic data | Cannot be seeded in production; the API refuses to start if synthetic instruments exist in production |
| Dependencies | CI runs `npm audit` (high severity) and `pip-audit` (report-only) |

## Operational recommendations
- Set `ALLOW_REGISTRATION=false` after creating your account.
- Expose only the web app via HTTPS; keep the API, Postgres and Redis on a private network.
- Rotate `SESSION_SECRET` and the MyStocks key if they are ever exposed (`POST /api-keys/rotate` in the MyStocks portal).
- Back up daily (see [operations](../deployment/operations.md)).

## Known limitations
- There is no multi-factor authentication or password reset by email, because no email channel is configured yet.
- The in-process rate limiter is per API replica; use Redis when running more than one replica.
- The CSP allows `'unsafe-inline'` scripts for the pre-paint theme script; this could be replaced with a nonce.
