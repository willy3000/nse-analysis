"""Operator CLI: `nse-cli <command>`. Run `nse-cli --help` for the list."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date

from nse_platform.core.config import get_settings
from nse_platform.core.logging import configure_logging


def _providers(only: str | None):  # noqa: ANN202
    from nse_platform.providers.registry import build_providers

    p = build_providers()
    if only:
        if only not in p:
            sys.exit(f"provider {only!r} not available (enabled providers: {', '.join(p)})")
        p = {only: p[only]}
    return p


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


async def _ingest_all(providers, years: int, replay: bool) -> None:  # noqa: ANN001
    from nse_platform.analytics.pipeline import run_analytics
    from nse_platform.ingestion import jobs

    _print(await jobs.sync_instruments(providers))
    start = date(date.today().year - years, 1, 1)
    _print(await jobs.ingest_history(providers, start=start, incremental=False))
    _print(await jobs.ingest_corporate(providers))
    _print(await asyncio.to_thread(run_analytics, replay))


def main(argv: list[str] | None = None) -> None:
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    ap = argparse.ArgumentParser(prog="nse-cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    p = sub.add_parser("sync-instruments")
    p.add_argument("--provider")
    p = sub.add_parser("backfill", help="full historical daily bar ingestion")
    p.add_argument("--provider")
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--symbols", nargs="*")
    p = sub.add_parser("ingest-eod", help="incremental daily bars")
    p.add_argument("--provider")
    p = sub.add_parser("poll-quotes")
    p.add_argument("--provider")
    p = sub.add_parser("ingest-corporate")
    p.add_argument("--provider")
    p = sub.add_parser("analytics")
    p.add_argument("--replay", action="store_true", help="replay detectors over full history (base rates)")
    sub.add_parser("validate-scores", help="walk-forward calibration of the trading score")
    p = sub.add_parser("seed-synthetic", help="DEV ONLY: load the synthetic universe end to end")
    p.add_argument("--years", type=int, default=11)
    sub.add_parser("purge-synthetic", help="delete all synthetic instruments and their data")
    p = sub.add_parser("import-csv", help="import licensed EOD/fundamental files")
    p.add_argument("kind", choices=["prices", "financials", "corporate-actions", "index"])
    p.add_argument("path")
    p.add_argument("--mapping", help="JSON column mapping file")
    p = sub.add_parser("create-user")
    p.add_argument("email")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--password", help="omit to be prompted")
    args = ap.parse_args(argv)

    if args.cmd == "migrate":
        from alembic.config import Config

        from alembic import command

        command.upgrade(Config("alembic.ini"), "head")
    elif args.cmd == "sync-instruments":
        from nse_platform.ingestion import jobs

        _print(asyncio.run(jobs.sync_instruments(_providers(args.provider))))
    elif args.cmd == "backfill":
        from nse_platform.ingestion import jobs

        _print(asyncio.run(jobs.ingest_history(_providers(args.provider), start=date(date.today().year - args.years,
                                                                                         1, 1),
                                               incremental=False, symbols=args.symbols)))
    elif args.cmd == "ingest-eod":
        from nse_platform.ingestion import jobs

        _print(asyncio.run(jobs.ingest_history(_providers(args.provider))))
    elif args.cmd == "poll-quotes":
        from nse_platform.ingestion import jobs

        _print(asyncio.run(jobs.poll_quotes(_providers(args.provider))))
    elif args.cmd == "ingest-corporate":
        from nse_platform.ingestion import jobs

        _print(asyncio.run(jobs.ingest_corporate(_providers(args.provider))))
    elif args.cmd == "analytics":
        from nse_platform.analytics.pipeline import run_analytics

        _print(run_analytics(replay=args.replay))
    elif args.cmd == "validate-scores":
        from nse_platform.analytics.validation import run_score_validation

        _print(run_score_validation())
    elif args.cmd == "seed-synthetic":
        if s.app_env == "production":
            sys.exit("refusing to seed synthetic data in production (ADR-0007)")
        asyncio.run(_ingest_all(_providers("synthetic"), args.years, replay=True))
    elif args.cmd == "purge-synthetic":
        from nse_platform.ingestion.admin import purge_synthetic

        _print(purge_synthetic())
    elif args.cmd == "import-csv":
        from nse_platform.providers.csv_import import run_import

        _print(run_import(args.kind, args.path, args.mapping))
    elif args.cmd == "create-user":
        import getpass

        from nse_platform.auth.service import create_user
        from nse_platform.core.db import session_scope

        pw = args.password or getpass.getpass("Password: ")
        with session_scope() as db:
            u = create_user(db, args.email, pw, role="admin" if args.admin else "user")
            print(f"created user {u.id} {u.email} role={u.role}")
    logging.shutdown()


if __name__ == "__main__":
    main()
