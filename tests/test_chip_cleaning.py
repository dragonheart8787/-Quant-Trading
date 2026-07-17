"""台股籌碼 canonical 轉換與驗證測試（cleaning.py 籌碼區段）。"""

from __future__ import annotations

import pandas as pd
import pytest

from data.cleaning import (
    CHIP_HOLDING_DISPERSION_COLUMNS,
    CHIP_INSTITUTIONAL_COLUMNS,
    CHIP_MARGIN_COLUMNS,
    CHIP_SHAREHOLDING_COLUMNS,
    check_margin_balance_identity,
    finmind_holding_dispersion_to_canonical,
    finmind_institutional_to_canonical,
    finmind_margin_to_canonical,
    finmind_shareholding_to_canonical,
)


def _raw_institutional():
    raw = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-02", "2024-01-03"],
        "stock_id": ["2330", "2330", "2330"],
        "name": ["Foreign_Investor", "Investment_Trust", "Foreign_Investor"],
        "buy": [1000, 50, 300],
        "sell": [400, 80, 300],
    })
    raw.attrs["source"] = "finmind"
    return raw


def _raw_margin(rows=2):
    base = {
        "date": ["2024-01-02", "2024-01-03"],
        "stock_id": ["2330", "2330"],
        "MarginPurchaseBuy": [100, 50],
        "MarginPurchaseSell": [30, 20],
        "MarginPurchaseCashRepayment": [10, 5],
        "MarginPurchaseYesterdayBalance": [5000, 5060],
        "MarginPurchaseTodayBalance": [5060, 5085],  # 恆等式成立
        "MarginPurchaseLimit": [10000, 10000],
        "ShortSaleBuy": [5, 3],
        "ShortSaleSell": [8, 6],
        "ShortSaleCashRepayment": [0, 0],
        "ShortSaleYesterdayBalance": [200, 203],
        "ShortSaleTodayBalance": [203, 206],
        "ShortSaleLimit": [10000, 10000],
        "OffsetLoanAndShort": [2, 1],
        "Note": ["", ""],
    }
    raw = pd.DataFrame({k: v[:rows] for k, v in base.items()})
    raw.attrs["source"] = "finmind"
    return raw


def test_institutional_to_canonical():
    out = finmind_institutional_to_canonical(_raw_institutional())
    assert list(out.columns) == CHIP_INSTITUTIONAL_COLUMNS
    assert len(out) == 3
    assert out["date"].tolist()[0] == "2024-01-02"
    assert set(out["investor_type"]) == {"Foreign_Investor", "Investment_Trust"}
    # net = buy - sell
    fi = out[(out["date"] == "2024-01-02") & (out["investor_type"] == "Foreign_Investor")]
    assert fi["net"].iloc[0] == 600
    assert out["buy"].dtype == "int64"
    assert (out["source"] == "finmind").all()


def test_institutional_negative_raises():
    raw = _raw_institutional()
    raw.loc[0, "buy"] = -1
    with pytest.raises(ValueError, match="負值"):
        finmind_institutional_to_canonical(raw)


def test_institutional_duplicate_pk_raises():
    raw = _raw_institutional()
    raw.loc[1, "name"] = "Foreign_Investor"  # 與第 0 列同 date+stock+type
    with pytest.raises(ValueError, match="主鍵"):
        finmind_institutional_to_canonical(raw)


def test_institutional_missing_column_raises():
    raw = _raw_institutional().drop(columns=["name"])
    with pytest.raises(ValueError, match="缺少欄位"):
        finmind_institutional_to_canonical(raw)


def test_institutional_empty_returns_empty_canonical():
    out = finmind_institutional_to_canonical(pd.DataFrame())
    assert list(out.columns) == CHIP_INSTITUTIONAL_COLUMNS
    assert out.empty


def test_institutional_nan_rows_dropped_and_counted():
    raw = _raw_institutional()
    raw.loc[2, "buy"] = None
    out = finmind_institutional_to_canonical(raw)
    assert len(out) == 2
    assert out.attrs["dropped_rows"] == 1


def test_margin_to_canonical_mapping():
    out = finmind_margin_to_canonical(_raw_margin())
    assert list(out.columns) == CHIP_MARGIN_COLUMNS
    assert len(out) == 2
    row = out.iloc[0]
    assert row["margin_buy"] == 100
    assert row["margin_balance_today"] == 5060
    assert row["short_balance_yesterday"] == 200
    assert row["offset_loan_and_short"] == 2
    assert out["margin_buy"].dtype == "int64"


def test_margin_missing_column_raises():
    raw = _raw_margin().drop(columns=["ShortSaleLimit"])
    with pytest.raises(ValueError, match="缺少欄位"):
        finmind_margin_to_canonical(raw)


def test_shareholding_to_canonical():
    raw = pd.DataFrame({
        "date": ["2024-01-02"],
        "stock_id": ["2330"],
        "stock_name": ["台積電"],  # 多餘欄位，不應進 canonical
        "ForeignInvestmentShares": [20_000_000_000],
        "ForeignInvestmentRemainingShares": [5_000_000_000],
        "ForeignInvestmentSharesRatio": [77.0],
        "ForeignInvestmentRemainRatio": [23.0],
        "ForeignInvestmentUpperLimitRatio": [100.0],
        "ChineseInvestmentUpperLimitRatio": [0.0],
        "NumberOfSharesIssued": [25_930_000_000],
    })
    raw.attrs["source"] = "finmind"
    out = finmind_shareholding_to_canonical(raw)
    assert list(out.columns) == CHIP_SHAREHOLDING_COLUMNS
    assert "stock_name" not in out.columns
    assert out["foreign_shares_ratio"].dtype == "float64"
    assert out["foreign_shares"].iloc[0] == 20_000_000_000


def test_holding_dispersion_to_canonical():
    raw = pd.DataFrame({
        "date": ["2024-01-05", "2024-01-05"],
        "stock_id": ["2330", "2330"],
        "HoldingSharesLevel": ["1-999", "1,000-5,000"],
        "people": [500000, 300000],
        "percent": [1.5, 2.5],
        "unit": [100000000, 200000000],
    })
    raw.attrs["source"] = "finmind"
    out = finmind_holding_dispersion_to_canonical(raw)
    assert list(out.columns) == CHIP_HOLDING_DISPERSION_COLUMNS
    assert len(out) == 2
    assert set(out["holding_level"]) == {"1-999", "1,000-5,000"}


def test_margin_identity_all_pass():
    canonical = finmind_margin_to_canonical(_raw_margin())
    violations = check_margin_balance_identity(canonical)
    assert violations.empty


def test_check_identity_diagnostic_detects_violation():
    # 直接建 canonical 形狀（繞過 to_canonical 的強制驗證），單測診斷函式本身
    canonical = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "margin_balance_yesterday": [5000, 5060],
        "margin_buy": [100, 50],
        "margin_sell": [30, 20],
        "margin_cash_repayment": [10, 5],
        "margin_balance_today": [5060, 9999],  # 第 2 列打破恆等式
    })
    violations = check_margin_balance_identity(canonical)
    assert len(violations) == 1
    assert violations["date"].iloc[0] == "2024-01-03"
    assert violations["residual"].iloc[0] == 9999 - 5085  # 實際 - 應然


def test_margin_to_canonical_raises_on_identity_violation():
    # 恆等式已納入驗證（使用者決定）：源頭資料破恆等式時不靜默入庫，直接拋錯
    raw = _raw_margin()
    raw.loc[1, "MarginPurchaseTodayBalance"] = 9999
    with pytest.raises(ValueError, match="恆等式違反"):
        finmind_margin_to_canonical(raw)


def test_margin_identity_empty_input():
    canonical = finmind_margin_to_canonical(pd.DataFrame())
    assert check_margin_balance_identity(canonical).empty


# ---- 哨兵值處理與保護性斷言（2026-07-07 使用者決定）----

def test_margin_sentinel_converted_to_null():
    # TPEx 早期 -1000000 哨兵值 → NULL（僅 limit / offset 類欄位）
    raw = _raw_margin()
    raw.loc[0, "ShortSaleLimit"] = -1_000_000
    raw.loc[0, "OffsetLoanAndShort"] = -1_000_000
    raw.loc[0, "MarginPurchaseLimit"] = -1_000_000
    out = finmind_margin_to_canonical(raw)
    r0 = out[out["date"] == "2024-01-02"].iloc[0]
    assert pd.isna(r0["short_limit"])
    assert pd.isna(r0["offset_loan_and_short"])
    assert pd.isna(r0["margin_limit"])
    # 未含哨兵的另一列不受影響
    r1 = out[out["date"] == "2024-01-03"].iloc[0]
    assert r1["short_limit"] == 10000
    # 核心買賣/餘額欄位不會因哨兵轉換被動到
    assert r0["margin_balance_today"] == 5060


def test_margin_unknown_negative_in_core_column_raises():
    # 保護性斷言：核心欄位出現負值（非已知哨兵可解釋）→ 明確拋錯，不靜默略過
    raw = _raw_margin()
    raw.loc[0, "MarginPurchaseBuy"] = -5
    with pytest.raises(ValueError, match="未知負值"):
        finmind_margin_to_canonical(raw)


def test_margin_known_sentinel_in_unexpected_column_raises():
    # 已知哨兵值但出現在非預期（核心）欄位 → 仍要拋錯，代表出現新的資料樣態
    raw = _raw_margin()
    raw.loc[0, "ShortSaleSell"] = -1_000_000
    with pytest.raises(ValueError, match="未知負值"):
        finmind_margin_to_canonical(raw)


def test_margin_new_sentinel_value_raises():
    # 若 FinMind 換了新哨兵值（例如 -999999），非負斷言必須攔下來讓我們知道
    raw = _raw_margin()
    raw.loc[0, "ShortSaleLimit"] = -999_999
    with pytest.raises(ValueError, match="未知負值"):
        finmind_margin_to_canonical(raw)
