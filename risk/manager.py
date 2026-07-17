"""風控核心模組。

Phase 1 範圍：
  1. 固定風險比例倉位計算（fixed fractional position sizing）
  2. 日內熔斷（intraday circuit breaker）：當日累積虧損達門檻即停止當日交易
  3. 多空槓桿上限分開設定 + 空頭單筆風險比例（short_risk_multiplier）
  4. 空頭強制平倉 check_forced_liquidation（ATR動態防線 + 15%固定安全網）
     獨立於日內熔斷之外，兩者分開實作分開測試。

設計原則：風控是「下單前的最後一道閘門」。任何上層下單邏輯都必須先過
RiskManager.approve_order 才能送到 broker。
熔斷與槓桿上限的數值不可在未經使用者確認下被程式自動調整。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

# ATR 動態防線預設值（業界常見起點；2026-07-13 起改由 RiskConfig 欄位注入，
# 此二常數僅作為 dataclass 預設值的單一定義點）
_FORCED_LIQ_ATR_N: float = 3.0
_FORCED_LIQ_FIXED_PCT: float = 0.15  # 固定安全網：空頭虧損超過進場名目 15% 強制平倉


class OrderDecision(Enum):
    APPROVED = "approved"
    REJECTED_CIRCUIT_BREAKER = "rejected_circuit_breaker"
    REJECTED_LEVERAGE = "rejected_leverage"
    REJECTED_INVALID = "rejected_invalid"
    REJECTED_CONFLICTING_POSITION = "rejected_conflicting_position"


@dataclass
class RiskConfig:
    """風控參數。預設為保守值；修改熔斷/槓桿上限需使用者明確確認。

    向後相容說明：
      - `max_leverage` 保留，未設 `max_long_leverage` 時自動沿用其值。
      - `max_short_leverage` 為唯讀 property = max_long_leverage * 0.5。
      - `max_gross_leverage` 預留欄位，目前多空互斥，值同 max_long_leverage。
    """

    risk_per_trade: float = 0.01           # 多頭每筆風險比例（1%）
    max_daily_loss_pct: float = 0.03       # 日內熔斷門檻（3%）
    max_leverage: float = 1.0              # 向後相容別名；未設 max_long_leverage 時沿用
    short_risk_multiplier: float = 0.5     # 空頭風險比例 = risk_per_trade * this
    max_long_leverage: Optional[float] = field(default=None)   # None → 自動從 max_leverage 取值
    max_gross_leverage: Optional[float] = field(default=None)  # 預留：未來多空並存用
    # 空頭強制平倉雙層防線（規則三）。預設 = 原模組常數，行為向後相容。
    forced_liq_atr_n: float = _FORCED_LIQ_ATR_N          # ATR 動態防線倍率
    forced_liq_safety_pct: float = _FORCED_LIQ_FIXED_PCT  # 固定安全網比例

    @property
    def max_short_leverage(self) -> float:
        """空頭槓桿上限 = 多頭的 0.5 倍。"""
        return self.max_long_leverage * 0.5  # type: ignore[operator]

    @property
    def risk_per_trade_short(self) -> float:
        """空頭每筆風險比例。"""
        return self.risk_per_trade * self.short_risk_multiplier

    def __post_init__(self) -> None:
        if self.max_long_leverage is None:
            self.max_long_leverage = self.max_leverage
        if self.max_gross_leverage is None:
            self.max_gross_leverage = self.max_long_leverage

        if not 0 < self.risk_per_trade < 1:
            raise ValueError("risk_per_trade 必須介於 0 與 1 之間")
        if not 0 < self.max_daily_loss_pct < 1:
            raise ValueError("max_daily_loss_pct 必須介於 0 與 1 之間")
        if self.max_long_leverage <= 0:
            raise ValueError("max_long_leverage 必須為正")
        if not 0 < self.short_risk_multiplier <= 1:
            raise ValueError("short_risk_multiplier 必須介於 0 與 1 之間（含 1）")
        if self.forced_liq_atr_n <= 0:
            raise ValueError("forced_liq_atr_n 必須為正")
        if not 0 < self.forced_liq_safety_pct < 1:
            raise ValueError("forced_liq_safety_pct 必須介於 0 與 1 之間")


@dataclass
class PositionSizing:
    quantity: float        # 建議下單數量（以標的單位計）
    risk_amount: float     # 本筆實際投入的風險金額
    notional: float        # 名目金額（quantity * entry_price）


class RiskManager:
    """有狀態的風控閘門：追蹤當日盈虧並負責倉位計算與下單核可。"""

    def __init__(self, equity: float, config: RiskConfig | None = None) -> None:
        if equity <= 0:
            raise ValueError("初始 equity 必須為正")
        self.config = config or RiskConfig()
        self._equity = float(equity)
        self._day: date | None = None
        self._day_start_equity: float = float(equity)
        self._tripped = False  # 當日是否已熔斷

    # ----- 帳戶狀態 -----
    @property
    def equity(self) -> float:
        return self._equity

    @property
    def is_circuit_broken(self) -> bool:
        return self._tripped

    def _roll_day(self, today: date) -> None:
        """跨日重置：新的一天解除熔斷並重設當日基準淨值。"""
        if self._day != today:
            self._day = today
            self._day_start_equity = self._equity
            self._tripped = False

    def update_equity(self, new_equity: float, today: date) -> None:
        """更新帳戶淨值（成交/標記市價後呼叫），並檢查是否觸發日內熔斷。"""
        self._roll_day(today)
        self._equity = float(new_equity)
        daily_pnl_pct = (self._equity - self._day_start_equity) / self._day_start_equity
        if daily_pnl_pct <= -self.config.max_daily_loss_pct:
            self._tripped = True

    # ----- 倉位計算 -----
    def position_size(
        self,
        entry_price: float,
        stop_price: float,
        side: str = "long",
    ) -> PositionSizing:
        """固定風險比例倉位（多空分開風險比例與槓桿上限）。

            risk_amount = equity * risk_per_trade  (多頭)
                        = equity * risk_per_trade_short  (空頭)
            quantity    = risk_amount / |entry - stop|

        並以對應 max_leverage 限制名目金額上限（超過則縮量）。
        """
        if entry_price <= 0:
            raise ValueError("entry_price 必須為正")
        per_unit_risk = abs(entry_price - stop_price)
        if per_unit_risk <= 0:
            raise ValueError("entry_price 與 stop_price 不可相同（風險為零）")

        if side == "short":
            risk_ratio = self.config.risk_per_trade_short
            max_lev = self.config.max_short_leverage
        else:
            risk_ratio = self.config.risk_per_trade
            max_lev = self.config.max_long_leverage  # type: ignore[assignment]

        risk_amount = self._equity * risk_ratio
        quantity = risk_amount / per_unit_risk

        max_notional = self._equity * max_lev
        notional = quantity * entry_price
        if notional > max_notional:
            quantity = max_notional / entry_price
            notional = quantity * entry_price
            risk_amount = quantity * per_unit_risk

        return PositionSizing(quantity=quantity, risk_amount=risk_amount, notional=notional)

    # ----- 下單核可 -----
    def approve_order(
        self,
        quantity: float,
        price: float,
        today: date,
        side: str = "long",
        current_position_qty: float = 0.0,
    ) -> OrderDecision:
        """下單前最後閘門。回傳核可結果。

        新增檢查：
        - 多空互斥：已有多頭倉位不可開空頭（反之亦然）。
        - 依 side 套用對應槓桿上限（多頭 max_long_leverage，空頭 max_short_leverage）。
        """
        self._roll_day(today)

        if quantity <= 0 or price <= 0:
            return OrderDecision.REJECTED_INVALID
        if self._tripped:
            return OrderDecision.REJECTED_CIRCUIT_BREAKER

        # 多空互斥檢查
        if side == "long" and current_position_qty < 0:
            return OrderDecision.REJECTED_CONFLICTING_POSITION
        if side == "short" and current_position_qty > 0:
            return OrderDecision.REJECTED_CONFLICTING_POSITION

        # 槓桿上限（依方向選用）
        if side == "short":
            max_lev = self.config.max_short_leverage
        else:
            max_lev = self.config.max_long_leverage  # type: ignore[assignment]

        notional = quantity * price
        if notional > self._equity * max_lev + 1e-9:
            return OrderDecision.REJECTED_LEVERAGE

        return OrderDecision.APPROVED

    # ----- 空頭強制平倉（獨立於日內熔斷之外） -----
    def check_forced_liquidation(
        self,
        position_quantity: float,
        avg_price: float,
        current_price: float,
        atr_value: float,
    ) -> bool:
        """空頭倉位強制平倉檢查（獨立於 is_circuit_broken / 日內熔斷機制之外）。

        只對空頭（position_quantity < 0）生效。雙層防線取較嚴格者觸發：

        1. ATR 動態防線：current_price >= avg_price + N * ATR
           N 由 config.forced_liq_atr_n 注入（預設 3.0，業界常見起點）。
        2. 固定安全網：(current_price - avg_price) / avg_price >= forced_liq_safety_pct
           （預設 15%）防止 ATR 異常（資料缺漏/極端值）時的最後防線。

        日內熔斷 = 帳戶整體當日虧損達門檻 → 當日停止交易。
        強制平倉 = 單一空頭倉位虧損過大 → 只平掉那一倉，兩者獨立互不取代。
        """
        if position_quantity >= 0:
            return False  # 只對空頭倉位生效

        # ATR 動態防線
        atr_trigger_price = avg_price + self.config.forced_liq_atr_n * atr_value
        atr_breached = current_price >= atr_trigger_price

        # 固定安全網：空頭虧損 = (current_price - avg_price) / avg_price
        fixed_breached = (
            (current_price - avg_price) / avg_price >= self.config.forced_liq_safety_pct
        )

        return atr_breached or fixed_breached
