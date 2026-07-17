"""E2 滑價敏感度（slippage_bps）測試（2026-07-15 範圍性解鎖）。

任務定位：壓力測試 event_engine.py 目前「零滑價」簡化假設，不是校正
最優滑價值。slippage_bps 新增前只存在於 docstring 註解，本檔驗證：
  1. 省略參數與顯式傳入正式基準值行為逐位不變（保護性回歸）。
     ★2026-07-15 使用者拍板：正式基準由 0bp 切換為 2bp（見 HANDOFF「滑價
     敏感度」節判決），`slippage_bps` 預設值已改為 2.0——本檔案下方需要
     「零摩擦」比較的測試（機制驗證，非經濟假設）一律明確傳
     `slippage_bps=0.0`，比照既有 `fee_bps=0.0` 慣例，不依賴省略。★
  2. 滑價方向正確：市價單恆不利——BUY 成交價上調、SELL 成交價下調。
  3. 強平單（forced_liquidation）同樣套用滑價，不特殊豁免（強平集中在
     高波動 bar，正是滑價壓力測試最相關的情境）。
  4. 只調整實際成交價，不污染 close/open（訊號生成、mark-to-market、
     ATR、強平判定、approve_order 核可估價全部維持用原始價格）。
  5. 參數驗證：負值拋錯。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.event_engine import run_event_backtest
from broker.paper import Side
from risk.manager import RiskManager


def _make_data(close: np.ndarray, start_ts: str = "2026-01-01") -> pd.DataFrame:
    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    ts = pd.date_range(start_ts, periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": open_,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "symbol": ["TEST"] * n,
        }
    )


def _const_atr(n: int, value: float) -> pd.Series:
    return pd.Series(np.full(n, value))


class TestDefaultIsOfficialBaseline:
    def test_default_slippage_matches_omitted(self):
        """slippage_bps 省略 與 顯式傳現行正式基準值（2.0）結果逐位相同。

        ★2026-07-15 基準切換：預設值本身就是「2bp」，不是「0bp」——這條
        測試鎖住的是「省略參數＝現行正式基準」，不是「省略＝零摩擦」。★
        """
        rng = np.random.default_rng(11)
        close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=200))
        data = _make_data(close)
        signal = pd.Series(rng.choice([-1, 0, 1], size=200))
        atr = _const_atr(200, 1.0)

        ev_omitted = run_event_backtest(
            data, signal, fee_bps=5.0, risk=RiskManager(equity=1.0), atr_series=atr,
        )
        ev_explicit = run_event_backtest(
            data, signal, fee_bps=5.0, risk=RiskManager(equity=1.0), atr_series=atr,
            slippage_bps=2.0,
        )
        assert np.array_equal(
            ev_omitted.equity_curve.to_numpy(), ev_explicit.equity_curve.to_numpy()
        )
        assert np.array_equal(
            ev_omitted.position_qty.to_numpy(), ev_explicit.position_qty.to_numpy()
        )
        fills_omitted = [(f.side, f.price) for f in ev_omitted.fills]
        fills_explicit = [(f.side, f.price) for f in ev_explicit.fills]
        assert fills_omitted == fills_explicit

    def test_slippage_zero_is_frictionless_reference_not_default(self):
        """slippage_bps=0.0 明確傳入時仍可拿到零摩擦結果（機制對照用途保留），
        但這已不是省略時的行為——risk_per_trade sizing 路徑手算案例驗證。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev_slip0 = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0), atr_series=_const_atr(6, 2.0),
            sizing_mode="risk_per_trade", slippage_bps=0.0,
        )
        entry = ev_slip0.fills[0]
        # D=6、budget=0.005 → qty=0.005/6；零摩擦下成交價=決策價 100
        assert entry.quantity == pytest.approx(0.005 / 6.0)
        assert entry.price == pytest.approx(100.0)


class TestSlippageDirection:
    def test_buy_fill_price_adjusted_up(self):
        """做多進場（BUY）：滑價後成交價 = 原價 × (1 + bps/1e4)。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 1, 1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            slippage_bps=10.0,
        )
        entry = ev.fills[0]
        assert entry.side is Side.BUY
        assert entry.price == pytest.approx(100.0 * 1.001)

    def test_sell_fill_price_adjusted_down(self):
        """做空進場（SELL）：滑價後成交價 = 原價 × (1 − bps/1e4)。"""
        close = np.full(6, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            slippage_bps=10.0,
        )
        entry = ev.fills[0]
        assert entry.side is Side.SELL
        assert entry.price == pytest.approx(100.0 * 0.999)

    def test_higher_slippage_strictly_worse_equity(self):
        """滑價越大、往返一趟（進+出）的權益侵蝕越大（單調）。"""
        close = np.array([100.0, 100.0, 105.0, 105.0, 105.0])
        data = _make_data(close)
        signal = pd.Series([0, 1, 1, 0, 0])
        finals = []
        for bps in (0.0, 5.0, 20.0):
            ev = run_event_backtest(
                data, signal, fee_bps=0.0, fill_mode="signal_close",
                slippage_bps=bps,
            )
            finals.append(ev.equity_curve.iloc[-1])
        assert finals[0] > finals[1] > finals[2]


class TestForcedLiquidationSlippage:
    def test_forced_liq_fill_gets_slippage_no_exemption(self):
        """強平單（BUY 回補空頭）同樣套用滑價，不豁免。"""
        close = np.array([100.0, 100.0, 100.0, 100.0, 109.0, 109.0, 109.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        atr = _const_atr(7, 2.0)  # N=3 × ATR=2 → 觸發距離 6；close=109 超越

        ev_base = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close", slippage_bps=0.0,
            risk=RiskManager(equity=1.0), atr_series=atr,
        )
        ev_slip = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0), atr_series=atr, slippage_bps=50.0,
        )
        assert ev_base.liquidation_bars == ev_slip.liquidation_bars == [4]
        liq_fill_base = [f for f in ev_base.fills if f.side is Side.BUY][-1]
        liq_fill_slip = [f for f in ev_slip.fills if f.side is Side.BUY][-1]
        # 強平回補是 BUY（買貴）：滑價後成交價應更高，侵蝕更大
        assert liq_fill_slip.price == pytest.approx(liq_fill_base.price * 1.005)
        assert ev_slip.equity_curve.iloc[-1] < ev_base.equity_curve.iloc[-1]


class TestMarkAndDecisionPricesUntouched:
    def test_equity_curve_marks_use_unadjusted_close(self):
        """收盤標記市值必須用原始 close，不受滑價影響（只有成交價受影響）。"""
        close = np.full(4, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0])  # 全程無交易，equity 應恆等於初始資金
        ev = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            initial_equity=1.0, slippage_bps=50.0,
        )
        assert np.array_equal(
            ev.equity_curve.to_numpy(), np.full(4, 1.0)
        )

    def test_liquidation_trigger_uses_unadjusted_close(self):
        """強平觸發判斷（check_forced_liquidation）用原始 close，
        觸發 bar 與零滑價時相同——滑價只改變成交價，不改變觸發時機。"""
        close = np.array([100.0, 100.0, 100.0, 100.0, 109.0, 109.0, 109.0])
        data = _make_data(close)
        signal = pd.Series([0, -1, -1, -1, -1, -1, 0])
        atr = _const_atr(7, 2.0)
        ev_base = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0), atr_series=atr,
        )
        ev_slip = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0), atr_series=atr, slippage_bps=100.0,
        )
        assert ev_base.liquidation_bars == ev_slip.liquidation_bars


class TestValidation:
    def test_negative_slippage_raises(self):
        close = np.full(4, 100.0)
        data = _make_data(close)
        signal = pd.Series([0, 0, 0, 0])
        with pytest.raises(ValueError):
            run_event_backtest(data, signal, slippage_bps=-1.0)
