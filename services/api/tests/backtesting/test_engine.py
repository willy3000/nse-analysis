"""Backtest engine behaviour on hand-constructed data (no DB)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from nse_platform.backtesting.costs import FeeModel
from nse_platform.backtesting.engine import InstrumentData, run
from nse_platform.backtesting.spec import BacktestParams, StrategySpec

ZERO_FEES = FeeModel(brokerage_rate=0, vat_on_brokerage=0, nse_levy=0, cma_levy=0, cdsc_levy=0, icf_levy=0,
                     guarantee_fund=0)


def data(close: list[float], opens: list[float] | None = None, volume: list[float] | None = None,
         signal_days: list[int] | None = None, exit_days: list[int] | None = None, **kw: object) -> InstrumentData:
    n = len(close)
    idx = pd.bdate_range("2024-01-01", periods=n)
    raw = pd.DataFrame({"open": opens or close, "high": close, "low": close, "close": close,
                        "volume": volume or [1e6] * n}, index=idx, dtype=float)
    feats = raw.copy()
    feats["sig"] = 0.0
    feats["ex"] = 0.0
    for d in signal_days or []:
        feats.iloc[d, feats.columns.get_loc("sig")] = 1.0
    for d in exit_days or []:
        feats.iloc[d, feats.columns.get_loc("ex")] = 1.0
    feats["roc_60"] = 0.0
    return InstrumentData(1, "TST", raw, feats, **kw)  # type: ignore[arg-type]


def spec(**kw: object) -> StrategySpec:
    # 'volume' > 0 and the custom columns are injected via features; use rel conditions on existing names.
    base = {"entry": [{"left": "close", "op": ">", "right": 0}], "max_holding_bars": 1000,
            "position_sizing": {"method": "fixed_fraction", "fraction": 0.5, "max_positions": 1}}
    base.update(kw)
    return StrategySpec.model_validate(base)


def params(**kw: object) -> BacktestParams:
    base = {"start_date": "2024-01-01", "end_date": "2024-12-31", "initial_capital": 100_000, "slippage_bps": 0}
    base.update(kw)
    return BacktestParams.model_validate(base)


def patch_signals(monkeypatch, d: InstrumentData) -> None:  # noqa: ANN001
    import nse_platform.backtesting.engine as eng

    monkeypatch.setattr(eng, "signals", lambda s, f: (f["sig"] > 0, f["ex"] > 0))


def test_fee_schedule_total():
    fm = FeeModel()
    b = fm.breakdown(100_000)
    assert b["brokerage"] == 1500 and b["vat_on_brokerage"] == 240
    assert b["total"] == pytest.approx(2080.0)  # 1.5% + 16% VAT + 0.34% levies
    assert fm.rate_estimate() == pytest.approx(0.0208)


def test_executes_next_open_not_signal_bar(monkeypatch):
    d = data([10, 10, 10, 12, 12], opens=[10, 10, 11, 12, 12], signal_days=[1])
    patch_signals(monkeypatch, d)
    res = run(spec(), params(), [d], ZERO_FEES)
    eq = res["equity_curve"]
    # Signal at close of day 1, filled at day-2 open (11), never at day-1 close (10).
    qty = 50_000 // 11
    assert eq[2]["cash"] == pytest.approx(100_000 - qty * 11)
    assert res["open_positions"][0]["entry_price"] == 11


def test_fees_and_slippage_reduce_cash(monkeypatch):
    d = data([10] * 6, signal_days=[0], exit_days=[2])
    patch_signals(monkeypatch, d)
    res = run(spec(exit=[{"left": "close", "op": ">", "right": 0}]), params(slippage_bps=100), [d], FeeModel())
    t = res["trades"][0]
    assert t["entry_price"] == pytest.approx(10.1) and t["exit_price"] == pytest.approx(9.9)
    assert t["net_pnl"] < 0 and t["entry_fees"] > 0 and t["exit_fees"] > 0


def test_zero_volume_blocks_fill_and_order_expires(monkeypatch):
    d = data([10] * 10, volume=[1e6, 0, 0, 0, 0, 0, 0, 1e6, 1e6, 1e6], signal_days=[0])
    patch_signals(monkeypatch, d)
    res = run(spec(), params(order_ttl_bars=5), [d], ZERO_FEES)
    assert not res["open_positions"] and res["metrics"]["diagnostics"]["orders_cancelled_no_liquidity"] == 1


def test_participation_cap(monkeypatch):
    d = data([10] * 5, volume=[1000] * 5, signal_days=[0])
    patch_signals(monkeypatch, d)
    res = run(spec(), params(max_participation=0.1), [d], ZERO_FEES)
    assert res["open_positions"][0]["quantity"] == 100  # 10% of 1000 shares


def test_stop_loss_exits_next_session(monkeypatch):
    d = data([10, 10, 8, 8, 8], signal_days=[0])
    patch_signals(monkeypatch, d)
    res = run(spec(stop_loss_pct=0.1), params(), [d], ZERO_FEES)
    t = res["trades"][0]
    assert t["exit_reason"] == "stop_loss" and t["exit_date"] == d.raw.index[3].date()


def test_bonus_issue_adjusts_quantity_and_dividend_credited_net(monkeypatch):
    close = [11, 11, 11, 10, 10, 10, 10]
    d = data(close, signal_days=[0], ratio_events=[(pd.bdate_range("2024-01-01", periods=7)[3].date(), 10, 11)],
             dividends=[(pd.bdate_range("2024-01-01", periods=7)[4].date(), None, 1.0)])
    patch_signals(monkeypatch, d)
    res = run(spec(), params(dividend_tax_rate=0.05), [d], ZERO_FEES)
    pos = res["open_positions"][0]
    qty0 = 50_000 // 11
    assert pos["quantity"] == pytest.approx(qty0 * 11 / 10)
    assert res["metrics"]["diagnostics"]["dividends_received"] == pytest.approx(qty0 * 1.1 * 0.95, abs=0.01)  # diagnostics rounded to cents
    # Value preserved across the bonus (no phantom loss from the raw price drop).
    eq = [e["equity"] for e in res["equity_curve"]]
    assert eq[3] == pytest.approx(eq[2], rel=1e-6)


def test_missing_feature_never_satisfies_condition():
    from nse_platform.backtesting.engine import evaluate
    from nse_platform.backtesting.spec import Condition

    f = pd.DataFrame({"rsi_14": [np.nan, 20.0, 40.0]})
    assert evaluate(Condition(left="rsi_14", op="<", right=30), f).tolist() == [False, True, False]
    assert evaluate(Condition(left="rsi_14", op="crosses_above", right=30), f).tolist() == [False, False, True]


def test_strategy_requires_exit():
    with pytest.raises(ValueError):
        StrategySpec.model_validate({"entry": [{"left": "close", "op": ">", "right": 1}]})


def test_segments_are_chronological(monkeypatch):
    d = data(list(np.linspace(10, 20, 100)), signal_days=[0])
    patch_signals(monkeypatch, d)
    res = run(spec(), params(), [d], ZERO_FEES)
    segs = [e["segment"] for e in res["equity_curve"]]
    first = {s: segs.index(s) for s in ("in_sample", "validation", "out_of_sample")}
    assert first["in_sample"] < first["validation"] < first["out_of_sample"]
    assert date.fromisoformat(res["metrics"]["segment_bounds"]["validation_start"]) < date.fromisoformat(
        res["metrics"]["segment_bounds"]["out_of_sample_start"])
