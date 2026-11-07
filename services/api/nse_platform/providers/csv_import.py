"""Import licensed files (e.g. NSE Data Services EOD Excel/CSV) and published financial statements.

Formats are documented in docs/data-sources/csv-import.md. A JSON mapping file adapts any vendor layout:
  {"columns": {"date": "Trade Date", "symbol": "Code", "close": "Day Price", ...},
   "date_format": "%d-%b-%y", "provider": "nse_csv", "exchange": "NSE"}
Blank / "-" cells are treated as missing (never zero). All rows pass the standard validation pipeline.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from nse_platform.core.db import session_scope
from nse_platform.ingestion import store
from nse_platform.ingestion.validation import validate_bars
from nse_platform.models import IndexHistory, Instrument, MarketIndex
from nse_platform.models.enums import CorporateActionType, SecurityType, Timeframe, TradingStatus
from nse_platform.providers.base import BarRecord, CorporateActionRecord, FinancialItemRecord, InstrumentRecord
from nse_platform.providers.parsing import day_start_utc, parse_date

UNIT_MULTIPLIERS = {"": 1, "1": 1, "units": 1, "thousands": 1e3, "kes'000": 1e3, "000": 1e3, "'000": 1e3,
                    "millions": 1e6, "kes'm": 1e6, "kes m": 1e6, "m": 1e6, "billions": 1e9, "bn": 1e9}
REQUIRED = {
    "prices": ["date", "symbol", "close"],
    "financials": ["symbol", "period_end", "statement_type", "line_item", "value", "published_at", "source"],
    "corporate-actions": ["symbol", "action_type"],
    "index": ["date", "index_code", "close"],
}


def _load(path: str, kind: str, mapping_path: str | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    mapping: dict[str, Any] = json.loads(Path(mapping_path).read_text()) if mapping_path else {}
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(p, dtype=str)
    else:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
    cols = mapping.get("columns", {})
    df = df.rename(columns={v: k for k, v in cols.items()})
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED[kind] if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing}; got {list(df.columns)}")
    df = df.apply(lambda s: s.str.strip() if s.dtype == object else s)
    df = df.replace({"": None, "-": None, "--": None, "N/A": None, "n/a": None})
    return df, mapping


def _num(v: Any, thousands: str = ",") -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).replace(thousands, "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _date(v: Any, fmt: str | None) -> date | None:
    if v is None:
        return None
    if fmt:
        try:
            return pd.to_datetime(v, format=fmt).date()
        except (ValueError, TypeError):
            return None
    return parse_date(v) or (pd.to_datetime(v, dayfirst=True, errors="coerce").date()
                             if pd.notna(pd.to_datetime(v, dayfirst=True, errors="coerce")) else None)


def run_import(kind: str, path: str, mapping_path: str | None = None) -> dict[str, Any]:
    df, mapping = _load(path, kind, mapping_path)
    fmt = mapping.get("date_format")
    provider_code = mapping.get("provider", "nse_csv")
    result: dict[str, Any] = {"kind": kind, "rows": len(df), "file": str(path)}
    with session_scope() as s:
        prow = store.provider_row(s, provider_code)
        run = store.start_run(s, f"csv_import:{kind}", prow)
        issues: list[store.Issue] = []
        try:
            if kind == "prices":
                _import_prices(s, prow, run, df, fmt, mapping, result, issues)
            elif kind == "financials":
                _import_financials(s, df, fmt, result)
            elif kind == "corporate-actions":
                _import_actions(s, prow, df, fmt, result)
            elif kind == "index":
                _import_index(s, prow, df, fmt, result)
            run.items_requested = len(df)
            run.items_succeeded = result.get("accepted", len(df))
            run.items_failed = result.get("rejected", 0)
            run.rows_written = result.get("written", 0)
            run.details = {k: v for k, v in result.items() if k != "file"}
            store.record_issues(s, issues, prow, run, result.pop("_symbol_map", None))
            store.finish_run(s, run)
        except Exception as exc:
            store.finish_run(s, run, f"{type(exc).__name__}: {exc}")
            raise
    result.pop("_symbol_map", None)
    return result


def _ensure_instruments(s, prow, symbols: dict[str, dict[str, Any]], exchange: str) -> dict[str, int]:  # noqa: ANN001
    recs = [InstrumentRecord(
        provider_symbol=sym, symbol=sym, name=meta.get("name") or sym,
        security_type=SecurityType(meta["security_type"]) if meta.get("security_type") else SecurityType.EQUITY,
        exchange_code=exchange, currency=meta.get("currency") or "KES", sector=meta.get("sector"),
        isin=meta.get("isin"), trading_status=TradingStatus.UNKNOWN,
    ) for sym, meta in symbols.items()]
    store.sync_instruments(s, prow, recs)
    return store.identifier_map(s, prow)


def _import_prices(s, prow, run, df, fmt, mapping, result, issues) -> None:  # noqa: ANN001
    meta_cols = [c for c in ("name", "sector", "security_type", "isin", "currency") if c in df.columns]
    symbols = {sym: (g[meta_cols].iloc[0].to_dict() if meta_cols else {}) for sym, g in df.groupby("symbol")}
    sym_map = _ensure_instruments(s, prow, symbols, mapping.get("exchange", "NSE"))
    result["_symbol_map"] = sym_map
    accepted = rejected = written = 0
    for sym, g in df.groupby("symbol"):
        bars = []
        for _, r in g.iterrows():
            d = _date(r["date"], fmt)
            close = _num(r.get("close"))
            if d is None or close is None:
                rejected += 1
                issues.append(store.Issue("CSV_ROW_UNPARSEABLE", "warn", sym, None, {"row": r.to_dict()}))
                continue
            o, h, lo = (_num(r.get(k)) for k in ("open", "high", "low"))
            vol = _num(r.get("volume"))
            bars.append(BarRecord(
                provider_symbol=sym, timeframe=Timeframe.D1, ts=day_start_utc(d), close=close, open=o, high=h, low=lo,
                volume=int(vol) if vol is not None else None, turnover=_num(r.get("turnover")),
                trades_count=int(_num(r.get("trades"))) if _num(r.get("trades")) is not None else None,
                ohlc_available=all(x is not None for x in (o, h, lo)), volume_available=vol is not None,
            ))
        iid = sym_map[sym]
        prev = store.last_close_before(s, iid, prow.id, "1d", min(b.ts for b in bars)) if bars else None
        validated, iss = validate_bars(bars, prev)
        issues.extend(iss)
        w, _ = store.store_bars(s, prow, iid, "KES", validated, run)
        accepted += len(validated)
        rejected += len(bars) - len(validated)
        written += w
    result.update(accepted=accepted, rejected=rejected, written=written, instruments=len(sym_map))


def _instrument_ids(s, symbols) -> dict[str, int]:  # noqa: ANN001
    rows = s.execute(select(Instrument.symbol, Instrument.id).where(Instrument.symbol.in_(list(symbols)))).all()
    return {r[0]: r[1] for r in rows}


def _import_financials(s, df, fmt, result) -> None:  # noqa: ANN001
    ids = _instrument_ids(s, set(df["symbol"]))
    unknown = sorted(set(df["symbol"]) - set(ids))
    written = rejected = 0
    by_inst: dict[int, list[FinancialItemRecord]] = {}
    for _, r in df.iterrows():
        iid = ids.get(r["symbol"])
        val = _num(r["value"])
        pe, pub = _date(r["period_end"], fmt), _date(r["published_at"], fmt)
        unit = str(r.get("unit") or "").strip().lower()
        if iid is None or val is None or pe is None or pub is None or unit not in UNIT_MULTIPLIERS:
            rejected += 1
            continue
        if pub < pe:
            rejected += 1  # published before the period ended: impossible, reject
            continue
        by_inst.setdefault(iid, []).append(FinancialItemRecord(
            provider_symbol=r["symbol"], period_end=pe, period_type=(r.get("period_type") or "FY").upper(),
            statement_type=r["statement_type"].upper(), line_item=r["line_item"].strip().lower(),
            value=val * UNIT_MULTIPLIERS[unit] if r["line_item"].strip().lower() not in ("eps", "dps") else val,
            currency=(r.get("currency") or "KES").upper(), published_at=pub, source=r["source"],
            reported_unit=r.get("unit"), source_url=r.get("source_url"),
        ))
    for iid, recs in by_inst.items():
        written += store.store_financials(s, iid, recs)
    result.update(accepted=written, rejected=rejected, written=written, unknown_symbols=unknown)


def _import_actions(s, prow, df, fmt, result) -> None:  # noqa: ANN001
    ids = _instrument_ids(s, set(df["symbol"]))
    written = rejected = 0
    for i, r in df.iterrows():
        iid = ids.get(r["symbol"])
        try:
            atype = CorporateActionType(r["action_type"].upper())
        except ValueError:
            atype = None
        if iid is None or atype is None:
            rejected += 1
            continue
        ex = _date(r.get("ex_date"), fmt)
        rec = CorporateActionRecord(
            external_id=r.get("external_id") or f"csv:{r['symbol']}:{atype.value}:{ex or i}",
            provider_symbol=r["symbol"], action_type=atype, status=r.get("status"), title=r.get("title"),
            ex_date=ex, record_date=_date(r.get("record_date"), fmt), payment_date=_date(r.get("payment_date"), fmt),
            effective_date=_date(r.get("effective_date"), fmt), cash_amount=_num(r.get("cash_amount")),
            currency=r.get("currency"), ratio_from=_num(r.get("ratio_from")), ratio_to=_num(r.get("ratio_to")),
            source_url=r.get("source_url"),
        )
        written += store.store_corporate_actions(s, prow, iid, [rec])
    result.update(accepted=written, rejected=rejected, written=written)


def _import_index(s, prow, df, fmt, result) -> None:  # noqa: ANN001
    ex = store.exchange_row(s, "NSE")
    written = rejected = 0
    for code, g in df.groupby("index_code"):
        name = g["index_name"].iloc[0] if "index_name" in g.columns and g["index_name"].iloc[0] else code
        s.execute(insert(MarketIndex).values(code=code, name=name, exchange_id=ex.id).on_conflict_do_nothing(
            index_elements=["code"]))
        idx_id = s.scalar(select(MarketIndex.id).where(MarketIndex.code == code))
        rows = []
        for _, r in g.iterrows():
            d, c = _date(r["date"], fmt), _num(r["close"])
            if d is None or c is None or c <= 0:
                rejected += 1
                continue
            rows.append({"index_id": idx_id, "provider_id": prow.id, "ts": day_start_utc(d), "session_date": d,
                         "open": _num(r.get("open")), "high": _num(r.get("high")), "low": _num(r.get("low")),
                         "close": c})
        if rows:
            stmt = insert(IndexHistory).values(rows)
            s.execute(stmt.on_conflict_do_update(index_elements=["index_id", "provider_id", "ts"], set_={
                k: stmt.excluded[k] for k in ("open", "high", "low", "close", "session_date")}))
            written += len(rows)
    result.update(accepted=written, rejected=rejected, written=written)
