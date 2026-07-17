"""策略抽象層。所有策略必須繼承 BaseStrategy。

策略只負責「在每根 K 棒產生目標訊號」，不負責倉位大小、停損、熔斷——
那些是 risk/manager.py 的職責。訊號的 shift(1) 防未來函數由回測引擎處理，
策略本身可以用到「當根收盤」的資訊。

訊號約定（target signal）：
    +1 = 看多/持有多單
     0 = 空手
    -1 = 看空/持有空單（Phase 1 baseline 可只用 +1/0）
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from data.cleaning import validate_canonical


class BaseStrategy(ABC):
    """策略基底類別。"""

    name: str = "base"

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """輸入 canonical schema 的 DataFrame，輸出與其 index 對齊的訊號序列。

        回傳值為 int 序列，取值 {-1, 0, +1}，index 與 data 相同。
        """
        raise NotImplementedError

    def _check_input(self, data: pd.DataFrame) -> None:
        """子類別可呼叫此方法驗證輸入是否為合法 canonical 資料。"""
        validate_canonical(data)
        if "close" not in data.columns:
            raise ValueError("策略需要 close 欄位")
