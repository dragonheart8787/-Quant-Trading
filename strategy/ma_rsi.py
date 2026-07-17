"""MA + RSI 規則策略 baseline。

進場（做多）：快線 > 慢線（趨勢向上）且 RSI 不過熱（< rsi_overbought）
出場（回空手）：快線 < 慢線（趨勢轉弱）或 RSI 過熱（>= rsi_overbought）

Phase 1 baseline 只做多/空手（訊號取 {0, +1}），不做空。
所有計算向量化，禁止 apply(axis=1)。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indicators.technical import rsi, sma
from strategy.base import BaseStrategy


@dataclass
class MaRsiStrategy(BaseStrategy):
    """雙均線 + RSI 過濾的趨勢跟隨策略。"""

    fast_window: int = 20
    slow_window: int = 60
    rsi_window: int = 14
    rsi_overbought: float = 70.0

    name: str = "ma_rsi"

    def __post_init__(self) -> None:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window 必須小於 slow_window")

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        self._check_input(data)
        close = data["close"]

        fast = sma(close, self.fast_window)
        slow = sma(close, self.slow_window)
        r = rsi(close, self.rsi_window)

        # 多頭條件：快線在慢線之上 且 RSI 未過熱
        long_cond = (fast > slow) & (r < self.rsi_overbought)

        signal = np.where(long_cond, 1, 0)
        # 指標暖機期（NaN）一律視為空手
        warmup = fast.isna() | slow.isna() | r.isna()
        signal = np.where(warmup, 0, signal)

        return pd.Series(signal, index=data.index, name="signal", dtype="int64")
