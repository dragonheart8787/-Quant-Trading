"""把日頻台股籌碼安全對齊到價格 bar 時間軸，產出特徵矩陣。

本模組（特徵層）只做一件事：把日頻籌碼 canonical DataFrame 以「排他式 as-of
join」對齊到呼叫端給定的價格 bar 時間軸上，防止未來函數。不抓資料、不算 alpha
指標、不碰回測（分層架構的資料/特徵層邊界，見 CLAUDE_2.md）。

核心機制（可測可稽核，取代自己寫「日期 +1」的邏輯）：
    pd.merge_asof(direction="backward", allow_exact_matches=False)
allow_exact_matches=False 就是「位移」本身——交易日 D 的 bar 只能拿到籌碼交易日
T < D（嚴格早於）的最近一筆；永遠拿不到 chip[T]，因為 chip[T] 是 T 日收盤後
20:00/21:00 才公布，T 日任何一根 bar 都看不到它。位移變成 merge_asof 的參數，
可測、可稽核。

「交易日」在 session_tz（預設 Asia/Taipei；BTC 情境可傳 UTC）當地時間定義：
先把 tz-aware 的 bar 時間戳轉到當地、取當地日曆日（normalize 到當地午夜），再與
籌碼交易日對齊——這是為什麼把 bar 正規化到當地午夜是必要的：如此 chip[D] 與 D 日
任何一根 bar 的對齊鍵相等，被 allow_exact_matches=False 排除。

公布時點保守簡化：法人 20:00、融資融券/外資持股 21:00 略異，但都在台股 13:30
收盤後，本輪一視同仁用排他式 as-of 處理（對日/1h K 線無影響）。之後若要對齊到
分鐘級 K 線，才需要逐資料集的精確公布時間戳機制，現在不做（未來工作）。

as-of join 天生把最後一筆可用籌碼「帶著走」（等同 ffill）——用 <src>_age_days
（bar 當地日曆日 − asof_date 的天數）標注新鮮度：正常交易日 age=1、跨週末 age=3。
存量變數（融資餘額、外資持股比）帶過來多天是誠實揭露，不強制歸零。設 max_age_days
時超標轉 NaN 並附 <src>_stale 旗標；不設時只標注不砍，交下游決定。
"""

from __future__ import annotations

from typing import Final, Mapping, Optional

import numpy as np
import pandas as pd

# 本輪最小特徵集（YAGNI）——真正的 alpha 特徵工程（動量/z-score/比率）不在此模組。
#
# 流量型：法人買賣超長表 pivot net by investor_type。指定 investor_type 值 → 輸出欄名。
FLOW_INVESTOR_FEATURES: Final = {
    "Foreign_Investor": "foreign_net",
    "Investment_Trust": "invest_trust_net",
}
# 存量型：canonical 寬表直接取用的欄（存在才取）。
STOCK_FEATURE_COLUMNS: Final = [
    "margin_balance_today",
    "short_balance_today",
    "foreign_shares_ratio",
]


def _session_dates(index: pd.DatetimeIndex, session_tz: str) -> np.ndarray:
    """把 tz-aware bar 時間戳化約成「當地交易日」的 tz-naive 午夜時間戳。

    先 tz_convert 到 session_tz、去掉時區保留當地牆鐘時間、再 normalize 到午夜。
    回傳 datetime64[ns] 陣列（與 index 等長、順序一致）。

    顯式 as_unit("ns")：merge_asof 要求左右 join key 解析度一致，但 bar 時間軸的
    解析度取決於呼叫端（例：storage_sqlite 以 int64 奈秒往返 → ns），而籌碼 date
    是 ISO 字串、pandas 3.0 的 to_datetime 解析成 us。兩邊都釘死在 ns 才不會在
    真實資料（混合來源）上因解析度不符炸掉 merge_asof。
    """
    local = index.tz_convert(session_tz)
    naive_midnight = local.tz_localize(None).normalize()
    return naive_midnight.as_unit("ns").to_numpy()


def _extract_daily_features(frame: pd.DataFrame) -> pd.DataFrame:
    """把單一來源的籌碼 canonical frame 化約成「每交易日一列」的特徵表。

    回傳含 'date'（原 ISO 字串）+ 特徵欄。長表（有 investor_type）pivot net；
    寬表則取用 STOCK_FEATURE_COLUMNS 中出現的欄。空表回傳只有 'date' 欄的空表。
    """
    if frame.empty:
        return pd.DataFrame(columns=["date"])

    if "investor_type" in frame.columns:
        # 法人長表 → pivot net by investor_type（只保留本輪關心的 investor_type）
        sub = frame[frame["investor_type"].isin(FLOW_INVESTOR_FEATURES)]
        if sub.empty:
            return pd.DataFrame(columns=["date"])
        wide = sub.pivot_table(
            index="date", columns="investor_type", values="net", aggfunc="first"
        ).rename(columns=FLOW_INVESTOR_FEATURES)
        daily = wide.reset_index()
        daily.columns.name = None
    else:
        feat_cols = [c for c in STOCK_FEATURE_COLUMNS if c in frame.columns]
        daily = frame[["date", *feat_cols]].copy()

    # 一交易日一列（防禦：若同 date 多列取第一筆，多來源合併不在本輪範圍）
    return daily.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def _align_source(
    bar_key: np.ndarray,
    bar_index: pd.Index,
    daily: pd.DataFrame,
    feature_cols: list[str],
    prefix: str,
    max_age_days: Optional[int],
    allow_exact_matches: bool = False,
) -> pd.DataFrame:
    """把單一籌碼來源用排他式 as-of join 對齊到 bar_key，回傳該來源的特徵欄。

    輸出欄：feature_cols + <prefix>_asof_date（採用的籌碼交易日 ISO 字串）
    + <prefix>_age_days（新鮮度，Int64）；設 max_age_days 時另加 <prefix>_stale。

    allow_exact_matches 僅供 lookahead 測試翻成 True 示範洩漏；公開路徑一律 False。
    """
    asof_col = f"{prefix}_asof_date"
    age_col = f"{prefix}_age_days"
    stale_col = f"{prefix}_stale"
    n = len(bar_index)
    part = pd.DataFrame(index=bar_index)

    if daily.empty or not feature_cols:
        for col in feature_cols:
            part[col] = np.nan
        part[asof_col] = pd.array([pd.NA] * n, dtype="string")
        part[age_col] = pd.array([pd.NA] * n, dtype="Int64")
        if max_age_days is not None:
            part[stale_col] = np.zeros(n, dtype=bool)
        return part

    # 右表：籌碼交易日 normalize 成 tz-naive 午夜當對齊鍵；另存 _asof_dt 帶出被採用的日期
    # as_unit("ns")：與 bar_key（_session_dates 已釘 ns）解析度對齊，否則 ISO 字串在
    # pandas 3.0 解析為 us，merge_asof 會因左右 key 解析度不符拋 MergeError。
    right = daily.copy()
    right["_key"] = pd.to_datetime(right["date"]).dt.normalize().dt.as_unit("ns")
    right["_asof_dt"] = right["_key"]
    right = right.sort_values("_key").reset_index(drop=True)

    left = pd.DataFrame({"_key": bar_key})
    merged = pd.merge_asof(
        left,
        right[["_key", "_asof_dt", *feature_cols]],
        on="_key",
        direction="backward",
        allow_exact_matches=allow_exact_matches,
    )  # merge_asof 保留 left 順序，故 merged 逐列對應 bar（RangeIndex 定位安全）

    asof_dt = merged["_asof_dt"]
    bar_dt = pd.Series(bar_key)
    age = (bar_dt - asof_dt).dt.days.astype("Int64")
    asof_str = asof_dt.dt.strftime("%Y-%m-%d").where(asof_dt.notna())

    stale = None
    if max_age_days is not None:
        stale = (age.fillna(-1) > max_age_days).to_numpy()  # NaN(無籌碼) 不算過期

    for col in feature_cols:
        vals = merged[col].astype("float64").to_numpy()
        if stale is not None:
            vals = np.where(stale, np.nan, vals)
        part[col] = vals
    part[asof_col] = pd.array(asof_str.to_numpy(dtype=object), dtype="string")
    part[age_col] = age.array
    if stale is not None:
        part[stale_col] = stale
    return part


def align_chips_to_bars(
    price_bars: pd.DataFrame,
    chip_frames: Mapping[str, pd.DataFrame],
    session_tz: str = "Asia/Taipei",
    max_age_days: Optional[int] = None,
) -> pd.DataFrame:
    """把多個日頻籌碼來源安全對齊到單一 symbol 的價格 bar 時間軸。

    參數
    ----
    price_bars: 單一 symbol 的價格 DataFrame，index 為 tz-aware DatetimeIndex
                （頻率不限，1h 或日皆可）；此 index 即主時間軸。
    chip_frames: {來源名稱: 籌碼 canonical DataFrame}，同一 symbol，每個含 date 欄
                （ISO 交易日 T）。名稱成為輸出欄的 <src> 前綴。
    session_tz: 交易日所屬時區（預設 Asia/Taipei；BTC 情境可傳 UTC）。
    max_age_days: 選配的過期硬上限；超標的來源特徵轉 NaN 並附 <src>_stale 旗標。

    回傳
    ----
    與 price_bars.index 逐列對齊的特徵矩陣（index 即 price_bars.index）：籌碼特徵欄
    + 每來源的 <src>_asof_date（實際採用的籌碼交易日）與 <src>_age_days（新鮮度）。
    首個籌碼日之前的 bar，其籌碼欄與 age 皆為 NaN。
    """
    index = price_bars.index
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError("price_bars.index 必須是 DatetimeIndex")
    if index.tz is None:
        raise ValueError("price_bars.index 必須是 tz-aware（籌碼交易日在當地時間定義）")
    if len(index) and not index.is_monotonic_increasing:
        raise ValueError("price_bars.index 必須遞增排序（merge_asof 需要）")

    bar_key = _session_dates(index, session_tz)
    out = pd.DataFrame(index=index)
    for name, frame in chip_frames.items():
        daily = _extract_daily_features(frame)
        feature_cols = [c for c in daily.columns if c != "date"]
        part = _align_source(bar_key, index, daily, feature_cols, name, max_age_days)
        for col in part.columns:
            out[col] = part[col].array  # 逐位賦值（避免 index 對齊/重複標籤的陷阱）
    return out
