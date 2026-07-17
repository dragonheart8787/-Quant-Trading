"""ML 訊號包裝（Phase 4 第一輪：L2 邏輯回歸 + 預先註冊交互項）。

單一職責：持有「單一 fold 已訓練完成」的 (scaler, model, τ)，把特徵矩陣
轉成 {-1, 0, +1} 逐 bar 訊號。訓練與 fold 編排在 ai/ml_train.py；本類別
不抓資料、不訓練、不碰回測。

訊號規則（τ = 中性帶，由訓練集尾端內部驗證選出）：
  p_up > 0.5 + τ → +1
  p_up < 0.5 - τ → -1
  其餘（含特徵任一 NaN 的列、退化模型）→ 0（安全空手，比照 chip NaN 語意）

E2 介面對齊：輸出未 shift 的目標訊號，與 v2 相同餵進
backtest/event_engine.run_event_backtest；sizing/風控/成本全部沿用基準設定，
確保 Δ(ML−v2) 只反映訊號品質。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


@dataclass
class MlLogisticSignal:
    """單一 fold 的已訓練 ML 訊號產生器。

    scaler / model 為 None 表示退化模型（訓練樣本不足或單一類別），
    predict_signal 恆回 0（全空手），不拋錯——資料不足時誠實停止進場。
    """

    feature_columns: Sequence[str]
    scaler: Optional[StandardScaler]
    model: Optional[LogisticRegression]
    tau: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.tau < 0.5:
            raise ValueError("tau 必須介於 [0, 0.5)")

    @property
    def is_degenerate(self) -> bool:
        return self.scaler is None or self.model is None

    def predict_proba_up(self, features: pd.DataFrame) -> pd.Series:
        """逐列 P(下一根上漲)；特徵任一 NaN 的列與退化模型回 NaN。"""
        cols = list(self.feature_columns)
        missing = [c for c in cols if c not in features.columns]
        if missing:
            raise ValueError(f"features 缺少欄位: {missing}")
        proba = pd.Series(np.nan, index=features.index, name="proba_up")
        if self.is_degenerate:
            return proba
        x = features[cols]
        valid = x.notna().all(axis=1)
        if valid.any():
            z = self.scaler.transform(x.loc[valid].to_numpy(dtype=float))
            proba.loc[valid] = self.model.predict_proba(z)[:, 1]
        return proba

    def predict_signal(self, features: pd.DataFrame) -> pd.Series:
        """{-1, 0, +1} 目標訊號（未 shift；事件引擎時序即防未來機制）。"""
        proba = self.predict_proba_up(features)
        signal = np.where(
            proba > 0.5 + self.tau, 1, np.where(proba < 0.5 - self.tau, -1, 0)
        )
        return pd.Series(signal, index=features.index, name="signal", dtype="int64")
