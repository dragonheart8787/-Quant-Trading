"""把各資料來源的原始格式統一轉成 canonical schema。

Canonical schema（硬性規範）：
    ts, symbol, open, high, low, close, volume, turnover, vwap,
    exchange, interval, source, ingested_at

新增資料源時優先在 data/providers/ 寫 adapter，再在這裡加對應轉換函式，
不要動下游（策略/回測/風控）邏輯。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

import pandas as pd

CANONICAL_COLUMNS: Final = [
    "ts",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "vwap",
    "exchange",
    "interval",
    "source",
    "ingested_at",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_canonical_from_binance(raw: pd.DataFrame) -> pd.DataFrame:
    """把 binance.fetch_klines 的原始 DataFrame 轉成 canonical schema。

    - ts: 用 open_time（K 棒開盤時間，UTC）
    - turnover: 用 Binance 的 quote_asset_volume（以報價幣計價的成交額）
    - vwap: turnover / volume（成交量加權均價的近似）
    """
    if raw.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    symbol = raw.attrs.get("symbol", "UNKNOWN")
    interval = raw.attrs.get("interval", "1h")
    source = raw.attrs.get("source", "binance")
    exchange = raw.attrs.get("exchange", "BINANCE")

    out = pd.DataFrame()
    out["ts"] = pd.to_datetime(raw["open_time"].astype("int64"), unit="ms", utc=True)

    numeric_cols = ["open", "high", "low", "close", "volume", "quote_asset_volume"]
    for col in numeric_cols:
        raw_num = pd.to_numeric(raw[col], errors="coerce")
        if col == "quote_asset_volume":
            out["turnover"] = raw_num
        else:
            out[col] = raw_num

    out["symbol"] = symbol
    # vwap 近似：成交額 / 成交量，volume=0 時給 NaN 避免除零
    out["vwap"] = (out["turnover"] / out["volume"]).where(out["volume"] > 0)
    out["exchange"] = exchange
    out["interval"] = interval
    out["source"] = source
    out["ingested_at"] = _utc_now()

    cleaned = _sanitize(out)
    return cleaned[CANONICAL_COLUMNS].reset_index(drop=True)


def _sanitize(df: pd.DataFrame) -> pd.DataFrame:
    """共用清洗：去重、排序、丟掉 OHLC 缺值或非正值的列。

    非正值防線（2026-07-16 新增，6446 藥華藥 2016-12-05 真實資料事故：OHLCV
    全部等於 0.0，前後交易日正常，屬資料源缺漏列，非除權息或真實鎖死）——
    真實交易日的收盤價不可能 <= 0，一律視為無效列整列丟棄，比照既有『已知
    情況丟棄，非預期殘留才拋錯』紀律（拋錯防線見 validate_canonical）。
    """
    df = df.dropna(subset=["open", "high", "low", "close"])
    non_positive = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    df = df[~non_positive]
    df = df.drop_duplicates(subset=["ts", "symbol", "interval"])
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def validate_canonical(df: pd.DataFrame) -> None:
    """驗證 DataFrame 符合 canonical schema，不符合就丟 ValueError。"""
    missing = set(CANONICAL_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Canonical schema 缺少欄位: {sorted(missing)}")
    if not pd.api.types.is_datetime64_any_dtype(df["ts"]):
        raise ValueError("ts 欄位必須是 datetime 型別")
    if df["ts"].duplicated().any():
        raise ValueError("ts 欄位有重複值")
    if not df["ts"].is_monotonic_increasing:
        raise ValueError("ts 欄位必須遞增排序")
    # 保護性斷言：即使繞過 _sanitize（例如新資料源轉換函式忘記處理），
    # 任何非正值的 O/H/L/C 一律在這道最終關卡攔下，不靜默放行進 DB。
    price_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if price_cols and (df[price_cols] <= 0).any().any():
        bad = df[(df[price_cols] <= 0).any(axis=1)].head(3)
        raise ValueError(f"O/H/L/C 出現非正值，樣本:\n{bad}")


# 台股日K 交易時段開盤（Asia/Taipei 09:00；固定 +08:00，無 DST，不會有
# 模糊/不存在時刻）。ts 統一代表 bar 開盤時間，見 CLAUDE_2.md 硬性規範。
_TW_SESSION_TZ: Final = "Asia/Taipei"
_TW_OPEN_HOUR: Final = 9


def finmind_price_to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """FinMind TaiwanStockPrice（台股日K）原始 DataFrame → K 線 canonical schema。

    - ts: 交易日 09:00 Asia/Taipei 開盤時間，存為對應 UTC（跨市場 ts 統一為開盤語意，
      見 CLAUDE_2.md「Canonical schema」）。interval 固定 "1d"。
    - high ← max、low ← min（FinMind 用 max/min 命名）。
    - turnover ← Trading_money（成交金額）。**注意 Trading_turnover 是成交筆數、
      不是成交金額**，不進 canonical（比照 binance 丟棄 num_trades）。spread（漲跌）同丟。
    - volume ← Trading_Volume（股數）。vwap = turnover/volume（近似，volume=0 給 NaN）。
    - exchange 由 raw.attrs["exchange"] 提供（TWSE/TPEx；provider fetch_price 帶入）。
    - symbol ← raw.attrs["stock_id"]（fetch_dataset 帶入），退回讀第一列 stock_id 欄。
    """
    if raw.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    _require_columns(
        raw,
        {"date", "stock_id", "open", "max", "min", "close", "Trading_Volume", "Trading_money"},
        "台股日K",
    )

    symbol = raw.attrs.get("stock_id") or str(raw["stock_id"].iloc[0])
    exchange = raw.attrs.get("exchange", "TWSE")
    source = raw.attrs.get("source", "finmind")

    out = pd.DataFrame()
    local_open = pd.to_datetime(raw["date"]) + pd.Timedelta(hours=_TW_OPEN_HOUR)
    out["ts"] = local_open.dt.tz_localize(_TW_SESSION_TZ).dt.tz_convert("UTC")
    out["open"] = pd.to_numeric(raw["open"], errors="coerce")
    out["high"] = pd.to_numeric(raw["max"], errors="coerce")
    out["low"] = pd.to_numeric(raw["min"], errors="coerce")
    out["close"] = pd.to_numeric(raw["close"], errors="coerce")
    out["volume"] = pd.to_numeric(raw["Trading_Volume"], errors="coerce")
    out["turnover"] = pd.to_numeric(raw["Trading_money"], errors="coerce")
    out["symbol"] = str(symbol)
    out["vwap"] = (out["turnover"] / out["volume"]).where(out["volume"] > 0)
    out["exchange"] = exchange
    out["interval"] = "1d"
    out["source"] = source
    out["ingested_at"] = _utc_now()

    cleaned = _sanitize(out)
    return cleaned[CANONICAL_COLUMNS].reset_index(drop=True)


# ======================================================================
# 台股籌碼 canonical schema（Phase 3，獨立於上面的 K 線 schema）
#
# 籌碼資料是「交易日」概念（日/週頻、無盤中時間點），date 用 ISO 字串
# （YYYY-MM-DD，台北交易日）而非 K 線的 tz-aware ts，避免時區歧義。
# 與 K 線時間軸的對齊在 indicators/features.py 做，不在儲存層合併。
#
# 欄名紀律：FinMind 的 CamelCase 原欄名 → snake_case，映射表集中在
# _FINMIND_*_MAP，原始欄名不外洩到下游。
# ======================================================================

CHIP_INSTITUTIONAL_COLUMNS: Final = [
    "date", "stock_id", "investor_type", "buy", "sell", "net", "source", "ingested_at",
]
CHIP_MARGIN_COLUMNS: Final = [
    "date", "stock_id",
    "margin_buy", "margin_sell", "margin_cash_repayment",
    "margin_balance_yesterday", "margin_balance_today", "margin_limit",
    "short_buy", "short_sell", "short_cash_repayment",
    "short_balance_yesterday", "short_balance_today", "short_limit",
    "offset_loan_and_short", "note", "source", "ingested_at",
]
CHIP_SHAREHOLDING_COLUMNS: Final = [
    "date", "stock_id",
    "foreign_shares", "foreign_remaining_shares",
    "foreign_shares_ratio", "foreign_remain_ratio",
    "foreign_upper_limit_ratio", "chinese_upper_limit_ratio",
    "number_of_shares_issued", "source", "ingested_at",
]
CHIP_HOLDING_DISPERSION_COLUMNS: Final = [
    "date", "stock_id", "holding_level", "people", "percent", "unit",
    "source", "ingested_at",
]

_FINMIND_MARGIN_MAP: Final = {
    "MarginPurchaseBuy": "margin_buy",
    "MarginPurchaseSell": "margin_sell",
    "MarginPurchaseCashRepayment": "margin_cash_repayment",
    "MarginPurchaseYesterdayBalance": "margin_balance_yesterday",
    "MarginPurchaseTodayBalance": "margin_balance_today",
    "MarginPurchaseLimit": "margin_limit",
    "ShortSaleBuy": "short_buy",
    "ShortSaleSell": "short_sell",
    "ShortSaleCashRepayment": "short_cash_repayment",
    "ShortSaleYesterdayBalance": "short_balance_yesterday",
    "ShortSaleTodayBalance": "short_balance_today",
    "ShortSaleLimit": "short_limit",
    "OffsetLoanAndShort": "offset_loan_and_short",
    "Note": "note",
}
_FINMIND_SHAREHOLDING_MAP: Final = {
    "ForeignInvestmentShares": "foreign_shares",
    "ForeignInvestmentRemainingShares": "foreign_remaining_shares",
    "ForeignInvestmentSharesRatio": "foreign_shares_ratio",
    "ForeignInvestmentRemainRatio": "foreign_remain_ratio",
    "ForeignInvestmentUpperLimitRatio": "foreign_upper_limit_ratio",
    "ChineseInvestmentUpperLimitRatio": "chinese_upper_limit_ratio",
    "NumberOfSharesIssued": "number_of_shares_issued",
}
_FINMIND_DISPERSION_MAP: Final = {
    "HoldingSharesLevel": "holding_level",
    "people": "people",
    "percent": "percent",
    "unit": "unit",
}


def _require_columns(raw: pd.DataFrame, needed: set, what: str) -> None:
    missing = needed - set(raw.columns)
    if missing:
        raise ValueError(f"{what} 原始資料缺少欄位: {sorted(missing)}")


def _chip_common(raw: pd.DataFrame, out: pd.DataFrame) -> pd.DataFrame:
    """共用欄位：date 正規化為 YYYY-MM-DD、stock_id 轉字串、source/ingested_at。"""
    out["date"] = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")
    out["stock_id"] = raw["stock_id"].astype(str)
    out["source"] = raw.attrs.get("source", "finmind")
    out["ingested_at"] = _utc_now().isoformat()
    return out


def _drop_na_and_cast(
    out: pd.DataFrame, int_cols: list[str], float_cols: list[str] | None = None
) -> pd.DataFrame:
    """必要數值欄有 NaN 的列丟棄（比照 K 線 _sanitize 精神），其餘轉型。

    丟棄筆數記在 df.attrs['dropped_rows']，ingest 端負責回報，不靜默。
    """
    float_cols = float_cols or []
    before = len(out)
    out = out.dropna(subset=int_cols + float_cols)
    out.attrs["dropped_rows"] = before - len(out)
    for col in int_cols:
        out[col] = pd.to_numeric(out[col]).astype("int64")
    for col in float_cols:
        out[col] = pd.to_numeric(out[col]).astype("float64")
    return out


def _validate_chip(df: pd.DataFrame, columns: list, pk: list, non_negative: list) -> None:
    """籌碼共通驗證：欄位齊全、主鍵無重複、數量欄非負。不符合丟 ValueError。"""
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"籌碼 canonical 缺少欄位: {sorted(missing)}")
    if df.duplicated(subset=pk).any():
        dup = df[df.duplicated(subset=pk, keep=False)].head(4)
        raise ValueError(f"籌碼主鍵 {pk} 有重複列，樣本:\n{dup}")
    for col in non_negative:
        if (df[col] < 0).any():
            raise ValueError(f"欄位 {col} 出現負值")


def finmind_institutional_to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """三大法人買賣超（長表）→ chip_institutional canonical。

    investor_type 直接沿用 FinMind 的 name 值（如 Foreign_Investor /
    Investment_Trust / Dealer_self ...），POC 首抓時列 distinct 核對。
    """
    if raw.empty:
        return pd.DataFrame(columns=CHIP_INSTITUTIONAL_COLUMNS)
    _require_columns(raw, {"date", "stock_id", "name", "buy", "sell"}, "法人買賣超")

    out = pd.DataFrame()
    out = _chip_common(raw, out)
    out["investor_type"] = raw["name"].astype(str)
    out["buy"] = pd.to_numeric(raw["buy"], errors="coerce")
    out["sell"] = pd.to_numeric(raw["sell"], errors="coerce")
    out = _drop_na_and_cast(out, ["buy", "sell"])
    out["net"] = out["buy"] - out["sell"]

    out = out[CHIP_INSTITUTIONAL_COLUMNS].sort_values(["date", "investor_type"]).reset_index(drop=True)
    _validate_chip(
        out, CHIP_INSTITUTIONAL_COLUMNS,
        pk=["date", "stock_id", "investor_type", "source"],
        non_negative=["buy", "sell"],
    )
    return out


# TPEx 早期資料的「無此資料」哨兵值（POC 2026-07-07 實測：僅出現在
# ShortSaleLimit 2003~2005-10 與 OffsetLoanAndShort ~2008-09）
_FINMIND_MARGIN_SENTINEL: Final = -1_000_000
# 允許 NULL 的欄位（哨兵轉換目標；核心買賣/餘額欄不在此列）
_MARGIN_NULLABLE_COLS: Final = ["margin_limit", "short_limit", "offset_loan_and_short"]


def finmind_margin_to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """融資融券 → chip_margin canonical（寬表，欄名映射見 _FINMIND_MARGIN_MAP）。

    兩道資料品質防線（2026-07-07 使用者決定）：
    1. 哨兵值 -1000000 → NULL（僅限 _MARGIN_NULLABLE_COLS）。轉換後任何欄位
       殘留負值一律拋錯——可能是新哨兵值、或已知哨兵出現在非預期欄位，
       不靜默略過。
    2. 融資恆等式 balance_today = yesterday + buy - sell - cash_repayment
       納入驗證（2330 全歷史 6,274 列 0 違反的實測基準），違反即拋錯。
    """
    if raw.empty:
        return pd.DataFrame(columns=CHIP_MARGIN_COLUMNS)
    numeric_raw = [c for c in _FINMIND_MARGIN_MAP if c != "Note"]
    _require_columns(raw, set(numeric_raw) | {"date", "stock_id"}, "融資融券")

    out = pd.DataFrame()
    out = _chip_common(raw, out)
    for src, dst in _FINMIND_MARGIN_MAP.items():
        if src == "Note":
            out[dst] = raw[src].astype(str) if src in raw.columns else ""
        else:
            out[dst] = pd.to_numeric(raw[src], errors="coerce")
    numeric_cols = [dst for src, dst in _FINMIND_MARGIN_MAP.items() if src != "Note"]

    # 防線 1a：已知哨兵 → NULL（只在實測出現過的欄位類做轉換）
    for col in _MARGIN_NULLABLE_COLS:
        out[col] = out[col].mask(out[col] == _FINMIND_MARGIN_SENTINEL)

    # 防線 1b：保護性斷言——哨兵轉換後不得殘留任何負值
    neg_report = []
    for col in numeric_cols:
        neg_mask = out[col].notna() & (out[col] < 0)
        if neg_mask.any():
            first = out[neg_mask].iloc[0]
            neg_report.append(
                f"{col}: {int(neg_mask.sum())} 筆（樣本 {first['date']} 值 {first[col]}）"
            )
    if neg_report:
        raise ValueError(
            "融資融券出現未知負值——非已知哨兵 -1000000 可解釋（可能有新哨兵值，"
            "或已知哨兵出現在非預期欄位），需人工確認後再決定處理方式: "
            + "; ".join(neg_report)
        )

    core_int_cols = [c for c in numeric_cols if c not in _MARGIN_NULLABLE_COLS]
    out = _drop_na_and_cast(out, core_int_cols)
    for col in _MARGIN_NULLABLE_COLS:
        out[col] = out[col].astype("Int64")  # nullable 整數；NULL = 源頭無資料

    out = out[CHIP_MARGIN_COLUMNS].sort_values("date").reset_index(drop=True)

    # 防線 2：融資恆等式（check_margin_balance_identity 同時保留作獨立診斷用）
    violations = check_margin_balance_identity(out)
    if not violations.empty:
        sample = violations.head(3)[["date", "residual"]].to_string(index=False)
        raise ValueError(
            f"融資恆等式違反 {len(violations)} 筆（實測基準為全歷史 0 違反，"
            f"資料源可能異常）:\n{sample}"
        )

    _validate_chip(
        out, CHIP_MARGIN_COLUMNS,
        pk=["date", "stock_id", "source"],
        non_negative=core_int_cols,
    )
    return out


def finmind_shareholding_to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """外資持股表 → chip_foreign_shareholding canonical（多餘欄位如 stock_name 不進 canonical）。"""
    if raw.empty:
        return pd.DataFrame(columns=CHIP_SHAREHOLDING_COLUMNS)
    _require_columns(raw, set(_FINMIND_SHAREHOLDING_MAP) | {"date", "stock_id"}, "外資持股")

    out = pd.DataFrame()
    out = _chip_common(raw, out)
    for src, dst in _FINMIND_SHAREHOLDING_MAP.items():
        out[dst] = pd.to_numeric(raw[src], errors="coerce")
    int_cols = ["foreign_shares", "foreign_remaining_shares", "number_of_shares_issued"]
    float_cols = [
        "foreign_shares_ratio", "foreign_remain_ratio",
        "foreign_upper_limit_ratio", "chinese_upper_limit_ratio",
    ]
    out = _drop_na_and_cast(out, int_cols, float_cols)

    out = out[CHIP_SHAREHOLDING_COLUMNS].sort_values("date").reset_index(drop=True)
    _validate_chip(
        out, CHIP_SHAREHOLDING_COLUMNS,
        pk=["date", "stock_id", "source"],
        non_negative=int_cols + float_cols,
    )
    return out


def finmind_holding_dispersion_to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """集保股權分散表（週頻）→ chip_holding_dispersion canonical。"""
    if raw.empty:
        return pd.DataFrame(columns=CHIP_HOLDING_DISPERSION_COLUMNS)
    _require_columns(raw, set(_FINMIND_DISPERSION_MAP) | {"date", "stock_id"}, "股權分散")

    out = pd.DataFrame()
    out = _chip_common(raw, out)
    out["holding_level"] = raw["HoldingSharesLevel"].astype(str)
    out["people"] = pd.to_numeric(raw["people"], errors="coerce")
    out["percent"] = pd.to_numeric(raw["percent"], errors="coerce")
    out["unit"] = pd.to_numeric(raw["unit"], errors="coerce")
    out = _drop_na_and_cast(out, ["people", "unit"], ["percent"])

    out = out[CHIP_HOLDING_DISPERSION_COLUMNS].sort_values(["date", "holding_level"]).reset_index(drop=True)
    _validate_chip(
        out, CHIP_HOLDING_DISPERSION_COLUMNS,
        pk=["date", "stock_id", "holding_level", "source"],
        non_negative=["people", "percent", "unit"],
    )
    return out


def check_margin_balance_identity(margin: pd.DataFrame) -> pd.DataFrame:
    """檢查融資恆等式 balance_today == balance_yesterday + buy - sell - cash_repayment
    （同列內檢查，無跨列相依）。回傳違反列（含 residual 欄 = 左式 - 右式），
    全部成立時回傳空表。

    POC 實測（2026-07-07）：2330 全歷史 6,274 列（2001→2026）0 違反——
    「張數」恆等式不受除權息影響。據此使用者決定納入
    finmind_margin_to_canonical 的驗證（違反即拋錯）；本函式保留供獨立診斷。
    """
    if margin.empty:
        return margin.copy()
    expected = (
        margin["margin_balance_yesterday"]
        + margin["margin_buy"]
        - margin["margin_sell"]
        - margin["margin_cash_repayment"]
    )
    residual = margin["margin_balance_today"] - expected
    violations = margin[residual != 0].copy()
    violations["residual"] = residual[residual != 0]
    return violations.reset_index(drop=True)
