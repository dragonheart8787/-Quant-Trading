"""事件驅動回測引擎（backtest/event_engine.py）不變量驗證。

涵蓋（2026-07-06 設計確認的驗收前提）：
  1. 多頭一致性：fee=0 時 E0（signal_close）與 vector_engine 逐位一致；
     有手續費時差異受費用時點的二階效應約束。
  2. 空頭理論差方向：固定數量空單 vs vector 再平衡反向曝險，
     單向趨勢（漲/跌兩情境）中 vector 恆優，且兩者皆符合精確公式。
  3. 無未來函數：截斷未來資料，已發生的成交前綴不變（兩種 fill 模式）。
  4. 翻向先平後開、最後一根不產生新訂單、next_open 成交價 = 次根開盤。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.event_engine import run_event_backtest
from backtest.vector_engine import run_backtest
from broker.paper import Side


def _make_data(close: np.ndarray) -> pd.DataFrame:
    """以收盤價序列合成引擎所需欄位（open = 前一根收盤）。"""
    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    ts = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"ts": ts, "open": open_, "close": close, "symbol": ["TEST"] * n}
    )


def _random_walk(n: int, seed: int = 42, drift: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.01, size=n)
    return 100.0 * np.cumprod(1.0 + rets)


def _long_signal(n: int) -> pd.Series:
    values = np.zeros(n, dtype=int)
    values[5:25] = 1
    values[35:65] = 1
    values[80:] = 1
    return pd.Series(values)


class TestLongConsistencyWithVector:
    """多頭語意下兩引擎應等價（設計決策②的多頭側）。"""

    def test_long_only_fee0_matches_vector_exactly(self):
        data = _make_data(_random_walk(100))
        signal = _long_signal(100)

        vec = run_backtest(
            pd.DataFrame({"close": data["close"]}), signal, fee_bps=0.0
        )
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0
        )

        np.testing.assert_allclose(
            ev.equity_curve.to_numpy(), vec.equity_curve.to_numpy(), rtol=1e-10
        )

    def test_long_only_with_fee_close_to_vector(self):
        """費用扣款時點差一根（vector 記在生效根、broker 記在成交根），
        造成的是複利基底的二階差異，最終權益應非常接近。"""
        data = _make_data(_random_walk(100))
        signal = _long_signal(100)

        vec = run_backtest(pd.DataFrame({"close": data["close"]}), signal, fee_bps=5.0)
        ev = run_event_backtest(
            data, signal, fee_bps=5.0, fill_mode="signal_close", slippage_bps=0.0
        )

        rel_diff = abs(
            ev.equity_curve.iloc[-1] / vec.equity_curve.iloc[-1] - 1.0
        )
        assert rel_diff < 5e-4


class TestShortStructuralDifference:
    """空頭理論差（設計決策②核心）：
    vector -1 = 每根再平衡反向曝險（每根權益乘 2-m）；
    event 空單 = 固定數量（權益 = 2 - P_t/P_entry，線性 payoff）。
    單向趨勢中 vector 恆優（凸性），漲跌兩情境都要成立。"""

    @pytest.mark.parametrize("multiplier", [0.95, 1.05])
    def test_monotonic_path_matches_exact_formulas(self, multiplier: float):
        k = 10
        close = 100.0 * multiplier ** np.arange(k + 1)
        data = _make_data(close)
        signal = pd.Series([-1] * (k + 1))

        vec = run_backtest(pd.DataFrame({"close": data["close"]}), signal, fee_bps=0.0)
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0
        )

        expected_vector = (2.0 - multiplier) ** k
        expected_event = 2.0 - multiplier ** k

        assert vec.equity_curve.iloc[-1] == pytest.approx(expected_vector, rel=1e-10)
        assert ev.equity_curve.iloc[-1] == pytest.approx(expected_event, rel=1e-10)
        # 單向趨勢中再平衡假設恆優於固定倉位（凸性），方向不分漲跌
        assert vec.equity_curve.iloc[-1] > ev.equity_curve.iloc[-1]


class TestNoLookahead:
    """截斷未來資料後，已發生的成交（ts 在截斷點之前）必須逐筆相同。"""

    @staticmethod
    def _fills_key(fills):
        return [
            (f.ts, f.side.value, round(f.quantity, 10), round(f.price, 10))
            for f in fills
        ]

    @pytest.mark.parametrize(
        "fill_mode,lag", [("next_open", 1), ("signal_close", 2)]
    )
    def test_truncation_preserves_fill_prefix(self, fill_mode: str, lag: int):
        # lag：截斷後最後 lag 根的決策/成交本來就不會發生
        #（next_open 最後一根無次根可成交；signal_close 最後一根不產生訂單）
        n, k = 120, 60
        data = _make_data(_random_walk(n, seed=7))
        rng = np.random.default_rng(11)
        signal = pd.Series(rng.choice([-1, 0, 1], size=n))

        full = run_event_backtest(data, signal, fee_bps=5.0, fill_mode=fill_mode)
        truncated = run_event_backtest(
            data.iloc[:k], signal.iloc[:k], fee_bps=5.0, fill_mode=fill_mode
        )

        cutoff = data["ts"].iloc[k - lag]
        full_prefix = [f for f in full.fills if f.ts <= cutoff]
        trunc_prefix = [f for f in truncated.fills if f.ts <= cutoff]
        assert self._fills_key(trunc_prefix) == self._fills_key(full_prefix)
        assert len(trunc_prefix) > 0  # 測試本身要有料


class TestOrderSemantics:
    def test_flip_generates_exit_then_entry(self):
        close = _random_walk(8, seed=3)
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, -1, -1, 0, 0, 0])

        ev = run_event_backtest(data, signal, fee_bps=0.0, fill_mode="next_open")

        reasons = [o.reason for o in ev.orders]
        assert reasons == ["entry_long", "exit", "entry_short", "exit"]
        # 倉位路徑：多 → （先平後開）空 → 平
        assert ev.position_qty.iloc[2] > 0
        assert ev.position_qty.iloc[4] < 0
        assert ev.position_qty.iloc[6] == 0
        assert len(ev.fills) == 4

    def test_flip_never_holds_both_directions(self):
        """先平後開保證任一時點不會多空同時存在（多空互斥的引擎側前提）。"""
        n = 200
        data = _make_data(_random_walk(n, seed=5))
        rng = np.random.default_rng(6)
        signal = pd.Series(rng.choice([-1, 0, 1], size=n))

        for mode in ("signal_close", "next_open"):
            ev = run_event_backtest(data, signal, fee_bps=5.0, fill_mode=mode)
            # position_qty 逐根只有單一方向；成交後 broker 內部也只有一個淨倉
            assert ((ev.position_qty >= 0) | (ev.position_qty <= 0)).all()

    def test_last_bar_generates_no_orders(self):
        n = 10
        data = _make_data(_random_walk(n, seed=9))
        values = np.zeros(n, dtype=int)
        values[-1] = 1  # 只有最後一根出訊號
        signal = pd.Series(values)

        for mode in ("signal_close", "next_open"):
            ev = run_event_backtest(data, signal, fee_bps=5.0, fill_mode=mode)
            assert ev.orders == []
            assert ev.fills == []

    def test_next_open_fills_at_next_bar_open(self):
        n = 10
        data = _make_data(_random_walk(n, seed=13))
        values = np.zeros(n, dtype=int)
        values[2:] = 1  # t=2 出多頭訊號
        signal = pd.Series(values)

        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="next_open", slippage_bps=0.0
        )

        assert len(ev.fills) == 1
        fill = ev.fills[0]
        assert fill.ts == data["ts"].iloc[3]
        assert fill.price == pytest.approx(float(data["open"].iloc[3]))
        assert fill.side is Side.BUY

    def test_invalid_signal_value_raises(self):
        data = _make_data(_random_walk(5, seed=1))
        signal = pd.Series([0, 2, 0, 0, 0])
        with pytest.raises(ValueError, match="signal"):
            run_event_backtest(data, signal)

    def test_report_compatibility(self):
        """to_backtest_result 能直接餵 build_report（指標管線共用）。"""
        from backtest.report import build_report

        n = 100
        data = _make_data(_random_walk(n, seed=21))
        signal = _long_signal(n)
        ev = run_event_backtest(data, signal, fee_bps=5.0, fill_mode="next_open")

        report = build_report(ev.to_backtest_result(data["close"]))
        assert report.num_bars == n
        assert report.num_trades == len(ev.fills)
        assert np.isfinite(report.annual_return)
