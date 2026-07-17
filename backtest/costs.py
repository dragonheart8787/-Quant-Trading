"""買賣分離的交易成本模型（多市場並存）。

背景：vector_engine 原本只有單一 `fee_bps` 套用在買賣雙邊（加密貨幣假設）。
台股的成本結構不對稱——手續費買賣皆收（牌告 0.1425%），證交稅**僅賣出**課徵
（普通股現股 0.3%）——所以需要 buy/sell 分離的費率。

設計約束（2026-07-10 使用者確認）：
- 加密貨幣既有路徑**零改動**：不傳 `costs` 時走舊 `fee_bps` 邏輯，行為逐位相同
  （tests/test_costs.py 的保護性回歸測試守住這條線）。
- `fee_discount` 預設 1.0（牌告無折扣，研究階段的保守假設）；電子下單折扣依
  券商/個人而異（常見 2.8折~6折），拿到實際費率再代入參數重跑，不改程式碼。
- 折扣只作用於手續費，證交稅是法定稅率不打折。

已知限制（誠實標注，比照 short_risk_overlay 慣例）：
- **最低手續費（多數券商單筆低消 NT$20）無法建模**——vector_engine 是報酬率制
  （無名目部位金額），算不出低消是否觸發；小額交易的實際成本會被低估。
- ETF 證交稅 0.1%、現股當沖 0.15%（優惠至 2027 年底）未涵蓋；目前僅普通股現股。
- 台股借券賣出（放空）同樣課證交稅，`sell_bps` 對空頭開倉語意碰巧正確，但台股
  目前 long-only，空頭成本結構（借券費等）未建模。

費率語意：Δposition > 0 的 bar 收 buy_bps、Δposition < 0 收 sell_bps
（以持倉變動絕對值 × 費率計成本，與舊制一致）。
"""

from __future__ import annotations

from dataclasses import dataclass

TW_FEE_BPS = 14.25        # 券商手續費牌告上限 0.1425%（買賣各收一次）
TW_SELL_TAX_BPS = 30.0    # 證交稅 0.3%（普通股現股，僅賣出）


@dataclass(frozen=True)
class TradeCosts:
    """單邊費率（基點）。buy_bps 收在持倉增加、sell_bps 收在持倉減少。"""

    buy_bps: float
    sell_bps: float


CRYPTO_DEFAULT = TradeCosts(buy_bps=5.0, sell_bps=5.0)  # 與舊 fee_bps=5.0 等價


def tw_stock_costs(fee_discount: float = 1.0) -> TradeCosts:
    """台股普通股現股成本。折扣僅作用於手續費；證交稅不打折。

    Args:
        fee_discount: 手續費折扣（1.0=牌告無折扣；例：0.6=六折）。
    """
    if not 0.0 < fee_discount <= 1.0:
        raise ValueError("fee_discount 必須在 (0, 1] 之間")
    fee = TW_FEE_BPS * fee_discount
    return TradeCosts(buy_bps=fee, sell_bps=fee + TW_SELL_TAX_BPS)
