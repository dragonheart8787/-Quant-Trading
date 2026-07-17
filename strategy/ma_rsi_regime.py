"""MA/RSI + regime filter 策略。

沿用原本的 MA/RSI 訊號（不改動 strategy/ma_rsi.py），但加上一層市場狀態閘門：
  - 當前 regime = trend_up   → 維持原 MA/RSI 訊號
  - 當前 regime = range / trend_down → 強制壓成 0（空手；不做空，因為
    broker/paper.py 目前不支援放空）

**防未來函數**：regime 用「滾動 trailing 視窗」逐根分類——第 t 根的 regime
只看 close[t-regime_window+1 .. t]（含當根、不看未來）。最終訊號仍由
backtest/vector_engine.py 做 shift(1) 才轉成持倉，因此整條鏈不偷看未來。
暖機期（資料不足以分類）一律視為空手。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.regime import classify_regime
from strategy.base import BaseStrategy
from strategy.ma_rsi import MaRsiStrategy


@dataclass
class MaRsiRegimeStrategy(BaseStrategy):
    """MA/RSI 訊號 + 因果 regime filter（只在 trend_up 進場）。"""

    fast_window: int = 20
    slow_window: int = 60
    rsi_window: int = 14
    rsi_overbought: float = 70.0
    regime_window: int = 120  # regime 分類的 trailing 視窗長度（根）

    name: str = "ma_rsi_regime"

    def __post_init__(self) -> None:
        if self.regime_window < 3:
            raise ValueError("regime_window 至少為 3")
        # 內部沿用既有 MA/RSI（其建構子會驗證 fast < slow 等條件）
        self._base = MaRsiStrategy(
            fast_window=self.fast_window,
            slow_window=self.slow_window,
            rsi_window=self.rsi_window,
            rsi_overbought=self.rsi_overbought,
        )

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        self._check_input(data)
        base_signal = self._base.generate_signals(data)

        allow_long = self._rolling_trend_up_mask(data["close"])
        filtered = np.where(allow_long.to_numpy(), base_signal.to_numpy(), 0)
        return pd.Series(filtered, index=data.index, name="signal", dtype="int64")

    def _rolling_trend_up_mask(self, close: pd.Series) -> pd.Series:
        """逐根因果分類：回傳布林序列，True = 當根 regime 為 trend_up。

        第 t 根用 close[t-regime_window+1 .. t]（不含未來）呼叫既有 classify_regime。
        視窗不足（暖機期）一律 False（空手）。
        """
        values = close.to_numpy(dtype=float)
        n = len(values)
        mask = np.zeros(n, dtype=bool)
        w = self.regime_window
        for t in range(w - 1, n):
            window = pd.Series(values[t - w + 1 : t + 1])
            mask[t] = classify_regime(window).label == "trend_up"
        return pd.Series(mask, index=close.index)
