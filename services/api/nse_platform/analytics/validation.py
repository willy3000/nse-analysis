"""Walk-forward calibration of the Trading Opportunity Score.

At each month-end t (after a 1-year warm-up) the score is recomputed for every instrument using only data <= t,
then compared with the forward 21-session return after t. Reported: Spearman rank IC per date, mean IC, IC
t-statistic, share of dates with IC > 0, and the mean forward return of the top vs bottom score tercile.

The result is displayed next to the score so users can see whether it has shown any predictive value in this
data universe. A score with mean IC ~ 0 is a descriptive ranking only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from nse_platform.analytics import indicators as ind_lib
from nse_platform.analytics.scores import SCORE_CALC_VERSION, compute_scores
from nse_platform.analytics.series import adjustment_events, apply_adjustments, load_daily
from nse_platform.analytics.trend import classify
from nse_platform.core.db import session_scope
from nse_platform.models import AnalyticsReport, Instrument
from nse_platform.models.enums import SecurityType

FWD = 21
PRICED = (SecurityType.EQUITY.value, SecurityType.ETF.value, SecurityType.REIT.value, SecurityType.PREFERENCE.value)


def _features_at(adj: pd.DataFrame, ind: pd.DataFrame, pos: int) -> dict[str, Any]:
    c = adj["close"]
    sl_bars, sl_ind = adj.iloc[: pos + 1], ind.iloc[: pos + 1]
    vol = adj["volume"].iloc[max(0, pos - 59) : pos + 1]
    traded = (vol > 0).astype(float).where(vol.notna())
    tv = (adj["close"] * adj["volume"]).iloc[max(0, pos - 19) : pos + 1]
    tf = traded.mean() if traded.notna().sum() >= 40 else np.nan
    hi = ind["high_52w"].iloc[pos]
    trend = classify(sl_bars, sl_ind, tf if np.isfinite(tf) else None).classification.value
    return {
        "return_3m": c.iloc[pos] / c.iloc[pos - 63] - 1 if pos >= 63 else np.nan,
        "trend_class": trend,
        "relative_volume": ind["rel_volume"].iloc[pos],
        "proximity_52w_high": c.iloc[pos] / hi if pd.notna(hi) and hi else np.nan,
        "liquidity": tf * tv.mean() if np.isfinite(tf) and tv.notna().sum() == 20 else np.nan,
    }


def run_score_validation() -> dict[str, Any]:
    reports = {}
    for synthetic in (False, True):
        universe = "synthetic" if synthetic else "real"
        data: dict[int, tuple[pd.DataFrame, pd.DataFrame]] = {}
        with session_scope() as s:
            insts = s.scalars(select(Instrument).where(Instrument.is_synthetic.is_(synthetic),
                                                       Instrument.security_type.in_(PRICED),
                                                       Instrument.primary_provider_id.is_not(None))).all()
            for inst in insts:
                raw = load_daily(s, inst)
                if len(raw) < 300:
                    continue
                adj = apply_adjustments(raw, adjustment_events(s, inst.id))
                data[inst.id] = (adj, ind_lib.compute_all(adj, "1d"))
        if len(data) < 5:
            reports[universe] = {"status": "insufficient_universe", "instruments": len(data)}
            continue
        all_dates = sorted(set().union(*[set(a.index) for a, _ in data.values()]))
        month_ends = pd.Series(all_dates).groupby(pd.Series(all_dates).dt.to_period("M")).max().tolist()
        rows = []
        for t in month_ends[12:]:
            frame, fwd = {}, {}
            for iid, (adj, ind) in data.items():
                if t not in adj.index:
                    continue
                pos = adj.index.get_loc(t)
                if pos < 252 or pos + FWD >= len(adj):
                    continue
                frame[iid] = _features_at(adj, ind, pos)
                fwd[iid] = adj["close"].iloc[pos + FWD] / adj["close"].iloc[pos] - 1
            if len(frame) < 5:
                continue
            fr = pd.DataFrame.from_dict(frame, orient="index")
            r3 = pd.to_numeric(fr["return_3m"], errors="coerce")
            fr["relative_strength_3m"] = r3.rank(pct=True) * 100
            scores = compute_scores(fr)
            sc = pd.Series({i: scores[i]["trading"]["score"] for i in fr.index}, dtype=float)
            fw = pd.Series(fwd)
            ok = sc.notna()
            if ok.sum() < 5:
                continue
            ic = sc[ok].rank().corr(fw[ok].rank())
            q = pd.qcut(sc[ok].rank(method="first"), 3, labels=False)
            rows.append({"date": t.date().isoformat(), "n": int(ok.sum()), "ic": float(ic),
                         "top_tercile_fwd": float(fw[ok][q == 2].mean()),
                         "bottom_tercile_fwd": float(fw[ok][q == 0].mean())})
        df = pd.DataFrame(rows)
        if df.empty:
            reports[universe] = {"status": "insufficient_history"}
            continue
        ic = df["ic"].dropna()
        spread = df["top_tercile_fwd"] - df["bottom_tercile_fwd"]
        tstat = float(ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))) if len(ic) > 2 and ic.std() > 0 else None
        verdict = ("evidence of positive rank correlation" if tstat is not None and tstat > 2 and ic.mean() > 0.03
                   else "no reliable evidence of predictive value - treat as a descriptive ranking")
        payload = {
            "universe": universe, "score": "trading", "forward_sessions": FWD, "dates": int(len(df)),
            "avg_instruments_per_date": float(df["n"].mean()), "mean_ic": float(ic.mean()),
            "ic_t_stat": tstat, "share_ic_positive": float((ic > 0).mean()),
            "mean_top_minus_bottom_tercile": float(spread.mean()), "verdict": verdict,
            "series": df.tail(120).to_dict(orient="records"),
            "method": "Monthly walk-forward: score computed from data <= t; Spearman IC vs next-21-session "
                      "return. Excludes transaction costs. Quality and risk scores are not yet calibrated.",
        }
        with session_scope() as s:
            stmt = insert(AnalyticsReport).values(code="trading_score_calibration", universe=universe,
                                                  calc_version=SCORE_CALC_VERSION, payload=payload)
            s.execute(stmt.on_conflict_do_update(index_elements=["code", "universe"], set_={
                "payload": stmt.excluded.payload, "calc_version": stmt.excluded.calc_version,
                "computed_at": datetime.now(timezone.utc)}))
        reports[universe] = {k: v for k, v in payload.items() if k != "series"}
    return reports
