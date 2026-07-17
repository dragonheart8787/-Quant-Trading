"""Paper broker：模擬成交。

介面對齊未來的 live broker（live_binance.py 等），確保 paper/live 可互換測試。
Phase 1 採市價即時成交模型：以傳入的成交價立即全額成交，並扣手續費。

成本模型（2026-07-16 範圍性擴充，台股 E2 化第一輪）：預設 `fee_bps` 對稱費率
（加密貨幣既有假設）；選填 `costs: TradeCosts` 啟用買賣分離費率（台股手續費+
證交稅不對稱結構，見 backtest/costs.py）。`costs=None` 時完全沿用舊 `fee_bps`
邏輯，逐位不變（tests/test_paper_broker_costs.py 保護性回歸驗證）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from backtest.costs import TradeCosts


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Fill:
    symbol: str
    side: Side
    quantity: float
    price: float
    fee: float
    ts: datetime


@dataclass
class Position:
    quantity: float = 0.0       # 正=多單，負=空單
    avg_price: float = 0.0


class PaperBroker:
    """記憶體內的模擬券商，追蹤現金、持倉與成交紀錄。"""

    def __init__(
        self, cash: float, fee_bps: float = 5.0, costs: Optional[TradeCosts] = None
    ) -> None:
        if cash < 0:
            raise ValueError("初始 cash 不可為負")
        self._cash = float(cash)
        self.fee_rate = fee_bps / 10_000.0
        self._costs = costs
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []

    def _fee_rate_for(self, side: Side) -> float:
        """costs 給定時依方向查表（BUY→buy_bps、SELL→sell_bps），否則沿用
        `fee_rate` 對稱費率（costs=None 的既有路徑，逐位不變）。"""
        if self._costs is None:
            return self.fee_rate
        bps = self._costs.buy_bps if side is Side.BUY else self._costs.sell_bps
        return bps / 10_000.0

    @property
    def cash(self) -> float:
        return self._cash

    def get_position(self, symbol: str) -> Position:
        return self.positions.get(symbol, Position())

    def market_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        price: float,
        ts: datetime | None = None,
    ) -> Fill:
        """送出市價單，立即以 `price` 全額成交。"""
        if quantity <= 0:
            raise ValueError("quantity 必須為正")
        if price <= 0:
            raise ValueError("price 必須為正")

        notional = quantity * price
        fee = notional * self._fee_rate_for(side)
        signed_qty = quantity if side is Side.BUY else -quantity

        if side is Side.BUY:
            self._cash -= notional + fee
        else:
            self._cash += notional - fee

        self._apply_to_position(symbol, signed_qty, price)

        fill = Fill(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            fee=fee,
            ts=ts or datetime.now(timezone.utc),
        )
        self.fills.append(fill)
        return fill

    def _apply_to_position(self, symbol: str, signed_qty: float, price: float) -> None:
        pos = self.positions.get(symbol, Position())
        new_qty = pos.quantity + signed_qty

        if pos.quantity == 0 or (pos.quantity > 0) == (signed_qty > 0):
            # 開倉或加碼：更新加權平均價
            if new_qty != 0:
                pos.avg_price = (
                    pos.avg_price * abs(pos.quantity) + price * abs(signed_qty)
                ) / abs(new_qty)
        else:
            # 反向：減倉/平倉，均價不變；若翻倉則以新價為均價
            if (new_qty > 0) != (pos.quantity > 0) and new_qty != 0:
                pos.avg_price = price
        pos.quantity = new_qty
        if abs(pos.quantity) < 1e-12:
            pos.quantity = 0.0
            pos.avg_price = 0.0
        self.positions[symbol] = pos

    def equity(self, mark_prices: dict[str, float]) -> float:
        """以標記市價計算總權益（現金 + 持倉市值）。"""
        value = self._cash
        for symbol, pos in self.positions.items():
            mark = mark_prices.get(symbol, pos.avg_price)
            value += pos.quantity * mark
        return value
