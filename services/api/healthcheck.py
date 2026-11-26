"""Container healthcheck: GET /health/live on whichever port and loopback stack this process got.

Run by the image's HEALTHCHECK (and so by docker-compose). It deliberately hits /health/live, which
returns a static response and touches neither the database nor Redis - an unreachable database must not
make the container look dead.

Both loopback addresses are tried because the bind interface is configurable: with BIND_HOST=:: (needed
for Railway's private network) the IPv4 loopback may not answer, and with the 0.0.0.0 default the IPv6 one
does not. Exit 0 if either answers 200.
"""

from __future__ import annotations

import os
import sys
import urllib.request

TIMEOUT = 4.0


def main() -> int:
    port = os.environ.get("PORT", "8000")
    errors: list[str] = []
    for host in ("127.0.0.1", "[::1]"):
        url = f"http://{host}:{port}/health/live"
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:  # noqa: S310 - fixed loopback URL
                if r.status == 200:
                    return 0
                errors.append(f"{url} -> {r.status}")
        except Exception as exc:  # noqa: BLE001 - any failure just means "try the other stack"
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")
    print("; ".join(errors), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
