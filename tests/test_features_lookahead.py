"""Phase 3：indicators/features.py 的防未來函數（lookahead bias）測試套件。

features.py 只做一件事：把日頻台股籌碼安全對齊到價格 bar 時間軸。核心機制是
排他式 as-of join（merge_asof, direction='backward', allow_exact_matches=False）——
交易日 D 的 bar 只能拿到籌碼交易日 T < D（嚴格早於）的最近一筆，永遠拿不到
chip[T]（T 日收盤後 20:00/21:00 才公布）。

本檔七項測試逐一鎖定該機制的一個面向（規格見上個 session 定案）：
  1. 排他式位移/對抗預測：完美同日預測子在正確版被位移、bug 版洩漏
  2. 未來截斷：截斷 C 之後的籌碼不影響 asof<=C 的特徵列（逐位不變）
  3. 盤中廣播：1h bar 下同一日所有 bar 共用同一 asof，D 日 bar 永不帶 chip[D]
  4. 新鮮度/過期：斷尾後 age 超標欄位轉 NaN + _stale 旗標（6446 斷尾情境）
  5. 時區邊界：交易日在 session_tz 當地定義，非 UTC 日
  6. 連續假期跳空：週末 age=3、農曆年式長假 age>3，皆由日曆日差算出
  7. 混合 datetime 解析度：ns bar 軸 × us 籌碼日期不得炸 merge_asof（真實資料回歸）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators import features
from indicators.features import align_chips_to_bars

TZ = "Asia/Taipei"


# --------------------------------------------------------------------------
# 測試資料建構器
# --------------------------------------------------------------------------
def _bars(dates, hour=13, tz=TZ, close=None):
    """單一 symbol 的日頻價格 bar（每日一根，落在當地盤中 hour 時）。"""
    idx = pd.DatetimeIndex([pd.Timestamp(f"{d} {hour:02d}:00:00", tz=tz) for d in dates])
    df = pd.DataFrame(index=idx)
    if close is not None:
        df["close"] = close
    return df


def _hourly_bars(dates, hours, tz=TZ):
    """1h 價格 bar：dates × hours 的笛卡兒積，用來驗證盤中廣播。"""
    stamps = [pd.Timestamp(f"{d} {h:02d}:00:00", tz=tz) for d in dates for h in hours]
    return pd.DataFrame(index=pd.DatetimeIndex(sorted(stamps)))


def _instit_frame(dates, foreign_net, invest_trust_net=None, stock_id="9999", source="finmind"):
    """法人買賣超 canonical（長表）：每 (date, investor_type) 一列。"""
    rows = []
    for i, d in enumerate(dates):
        rows.append({"date": d, "stock_id": stock_id, "investor_type": "Foreign_Investor",
                     "buy": 0, "sell": 0, "net": foreign_net[i], "source": source, "ingested_at": ""})
        if invest_trust_net is not None:
            rows.append({"date": d, "stock_id": stock_id, "investor_type": "Investment_Trust",
                         "buy": 0, "sell": 0, "net": invest_trust_net[i], "source": source,
                         "ingested_at": ""})
    return pd.DataFrame(rows)


def _margin_frame(dates, margin_bal, short_bal, stock_id="9999", source="finmind"):
    """融資融券 canonical（寬表）子集——只放 features 會取用的存量欄。"""
    return pd.DataFrame({
        "date": list(dates),
        "stock_id": stock_id,
        "margin_balance_today": list(margin_bal),
        "short_balance_today": list(short_bal),
        "source": source,
        "ingested_at": "",
    })


# --------------------------------------------------------------------------
# 測試 1：排他式位移 / 對抗式「完美同日預測子」
# --------------------------------------------------------------------------
def test_exclusive_shift_defeats_same_day_predictor():
    """造一個 chip[T] = 當日價格漲跌方向的完美同日預測子。

    斷言目標：
      - 正確版（allow_exact_matches=False）：T 日 bar 拿到 chip[T-1]，與同日漲跌去相關
      - bug 版（allow_exact_matches=True）：T 日 bar 拿到 chip[T]，與同日漲跌完全相關（洩漏）
    """
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-08", periods=10)]
    dirs = np.array([1, -1, 1, 1, -1, 1, -1, -1, 1, -1])
    close = 100 + np.cumsum(dirs)  # sign(diff close) == dirs（i>=1）→ chip 是「當日方向」

    bars = _bars(dates, close=close)
    frame = _instit_frame(dates, foreign_net=dirs)

    # 正確版 = 公開函式（allow_exact_matches=False 已寫死）
    correct = align_chips_to_bars(bars, {"instit": frame}, session_tz=TZ)["foreign_net"].to_numpy()

    # bug 版：走同一套機制但把旗標翻成 True，示範洩漏
    bar_key = features._session_dates(bars.index, TZ)
    daily = features._extract_daily_features(frame)
    feat_cols = [c for c in daily.columns if c != "date"]
    bug = features._align_source(
        bar_key, bars.index, daily, feat_cols, "instit", None, allow_exact_matches=True
    )["foreign_net"].to_numpy()

    # 逐位：正確版每一根 bar 拿到「前一個交易日」的 chip；bug 版拿到「同日」chip
    for i in range(1, len(dates)):
        assert correct[i] == dirs[i - 1], f"row{i} 正確版應拿 chip[T-1]={dirs[i-1]}，得 {correct[i]}"
        assert bug[i] == dirs[i], f"row{i} bug 版應洩漏 chip[T]={dirs[i]}，得 {bug[i]}"
    # 首根 bar 之前無籌碼：正確版 NaN、bug 版洩漏當日
    assert np.isnan(correct[0])
    assert bug[0] == dirs[0]

    # 相關性：bug 版與同日方向完全相關 (1.0)；正確版被位移、相關性 <1（去相關）
    assert np.corrcoef(bug, dirs)[0, 1] == pytest.approx(1.0)
    corr_correct = np.corrcoef(correct[1:], dirs[1:])[0, 1]
    assert abs(corr_correct) < 1.0
    assert abs(corr_correct) < abs(np.corrcoef(bug[1:], dirs[1:])[0, 1])


# --------------------------------------------------------------------------
# 測試 2：未來截斷——截掉 C 之後的籌碼不改變 asof<=C 的特徵列
# --------------------------------------------------------------------------
def test_future_truncation_leaves_past_features_bit_identical():
    """截斷 C 點之後的所有籌碼重算，斷言 asof_date<=C 的價格 bar 特徵列逐位不變。"""
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-08", periods=20)]
    net = np.arange(100, 120)  # 每日唯一值，便於偵測任何位移
    bars = _bars(dates)
    frame = _instit_frame(dates, foreign_net=net)

    cutoff = "2024-01-19"
    truncated = frame[frame["date"] <= cutoff].copy()

    full = align_chips_to_bars(bars, {"instit": frame}, session_tz=TZ)
    trunc = align_chips_to_bars(bars, {"instit": truncated}, session_tz=TZ)

    asof = full["instit_asof_date"]
    mask = asof.notna() & (asof <= cutoff)
    assert mask.any() and (~mask).any(), "測試需同時涵蓋 asof<=C 與 asof>C 的 bar"

    cols = ["foreign_net", "instit_asof_date", "instit_age_days"]
    for col in cols:
        pd.testing.assert_series_equal(
            full.loc[mask, col], trunc.loc[mask, col], check_names=False
        )
    # 反面：截斷確實影響了 asof>C 的列（否則測試沒鑑別力）
    assert not full.loc[~mask, "foreign_net"].equals(trunc.loc[~mask, "foreign_net"])


# --------------------------------------------------------------------------
# 測試 3：盤中廣播——1h bar 同一日共用同一 asof，D 日 bar 永不帶 chip[D]
# --------------------------------------------------------------------------
def test_intraday_bars_broadcast_same_asof_and_never_same_day():
    """1h 價格 bar 下，同一交易日所有 bar 共用同一 asof；D 日 bar 永不帶 chip[D]。

    用寬表（融資融券）順帶覆蓋存量型欄位的抽取路徑。
    """
    chip_dates = ["2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"]
    frame = _margin_frame(chip_dates, margin_bal=[10, 20, 30, 40], short_bal=[1, 2, 3, 4])
    bar_dates = ["2024-01-08", "2024-01-09", "2024-01-10"]
    bars = _hourly_bars(bar_dates, hours=range(10, 14))  # 每日 4 根

    out = align_chips_to_bars(bars, {"margin": frame}, session_tz=TZ)
    local_date = out.index.tz_convert(TZ).strftime("%Y-%m-%d")

    for d in bar_dates:
        grp = out[local_date == d]
        assert len(grp) == 4
        # 同一日所有 bar 的 asof / 特徵值完全一致（廣播）
        assert grp["margin_asof_date"].nunique() == 1
        assert grp["margin_balance_today"].nunique() == 1
        assert grp["short_balance_today"].nunique() == 1
        # D 日 bar 永不帶 chip[D]：asof 嚴格早於當日
        asof = grp["margin_asof_date"].iloc[0]
        assert asof < d, f"{d} 的 bar 拿到同日或未來 chip: {asof}"

    # 具體位移：01-08 → 01-05（跨週末）；01-09 → 01-08；01-10 → 01-09
    def asof_of(d):
        return out.loc[local_date == d, "margin_asof_date"].iloc[0]
    assert asof_of("2024-01-08") == "2024-01-05"
    assert asof_of("2024-01-09") == "2024-01-08"
    assert asof_of("2024-01-10") == "2024-01-09"


# --------------------------------------------------------------------------
# 測試 4：新鮮度/過期——斷尾後 age 超標欄位轉 NaN + _stale（6446 情境）
# --------------------------------------------------------------------------
def test_staleness_flag_and_nan_on_data_gap():
    """插入籌碼斷尾（模擬 6446 藥華藥 2024-01-25 後 institutional 停更），
    斷言 age 超標的列特徵轉 NaN + instit_stale=True，且 asof 凍結在斷尾日；
    未設 max_age_days 時只帶舊值不砍。"""
    chip_dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-15", "2024-01-25")]
    frame = _instit_frame(chip_dates, foreign_net=np.arange(len(chip_dates)))
    # 價格 bar 延續到斷尾日之後（模擬股票續交易、籌碼停更）
    bar_dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-16", "2024-02-20")]
    bars = _bars(bar_dates)

    out = align_chips_to_bars(bars, {"instit": frame}, session_tz=TZ, max_age_days=7)
    local_date = out.index.tz_convert(TZ).strftime("%Y-%m-%d")

    def row(d):
        return out[local_date == d].iloc[0]

    fresh = row("2024-01-30")  # asof=2024-01-25, age=5 <=7 → 新鮮
    assert fresh["instit_age_days"] == 5
    assert not fresh["instit_stale"]
    assert not pd.isna(fresh["foreign_net"])
    assert fresh["instit_asof_date"] == "2024-01-25"

    stale = row("2024-02-12")  # 週一；asof 凍結 2024-01-25, age=18 >7 → 過期
    assert stale["instit_age_days"] == 18
    assert stale["instit_stale"]
    assert pd.isna(stale["foreign_net"])          # 特徵轉 NaN
    assert stale["instit_asof_date"] == "2024-01-25"  # asof / age 保留作誠實揭露

    # 未設 max_age_days：只帶舊值不砍、也不產生 _stale 旗標
    out2 = align_chips_to_bars(bars, {"instit": frame}, session_tz=TZ)
    assert "instit_stale" not in out2.columns
    assert not pd.isna(out2[local_date == "2024-02-12"].iloc[0]["foreign_net"])


# --------------------------------------------------------------------------
# 測試 5：時區邊界——交易日在 session_tz 當地定義，非 UTC 日
# --------------------------------------------------------------------------
def test_session_day_is_local_not_utc():
    """chip[T] 與 T 日 23:00（當地）的盤中 bar，斷言後者看不到 chip[T]；
    並用一根「UTC 前一日、當地隔日」的 bar 證明對齊用當地日曆日而非 UTC 日。"""
    frame = _instit_frame(["2024-03-13", "2024-03-14"], foreign_net=[10, 20])

    # bar A：15:00 UTC = 2024-03-14 23:00 台北（當地日 03-14）→ 不得看到 chip[03-14]
    # bar B：17:00 UTC = 2024-03-15 01:00 台北（UTC 仍是 03-14，但當地日 03-15）→ 應看到 chip[03-14]
    idx = pd.DatetimeIndex([
        pd.Timestamp("2024-03-14 15:00", tz="UTC"),
        pd.Timestamp("2024-03-14 17:00", tz="UTC"),
    ])
    bars = pd.DataFrame(index=idx)

    out = align_chips_to_bars(bars, {"instit": frame}, session_tz="Asia/Taipei")

    # bar A（當地 03-14 23:00）：排他式 → 看到 chip[03-13]，不是 chip[03-14]
    assert out.iloc[0]["instit_asof_date"] == "2024-03-13"
    assert out.iloc[0]["foreign_net"] == 10
    # bar B（當地 03-15）：雖然 UTC 日仍是 03-14，仍看得到 chip[03-14]（用當地日對齊的鐵證）
    assert out.iloc[1]["instit_asof_date"] == "2024-03-14"
    assert out.iloc[1]["foreign_net"] == 20


# --------------------------------------------------------------------------
# 測試 6：連續假期跳空——週末 age=3、長假 age>3，皆由日曆日差算出
# --------------------------------------------------------------------------
def test_holiday_gap_age_days():
    """用週一開盤第一根 bar 驗證 age=3（跳過週六週日拿上週五的籌碼），
    正常交易日 age=1；再用農曆年式連續長假驗證 age 可 >3。"""
    # A. 週末跳空 + 正常交易日
    frame = _instit_frame(
        ["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"],
        foreign_net=[1, 2, 3, 4],
    )
    bars = _bars(["2024-01-05", "2024-01-08", "2024-01-09"])
    out = align_chips_to_bars(bars, {"instit": frame}, session_tz=TZ)
    local = out.index.tz_convert(TZ).strftime("%Y-%m-%d")

    def age(d):
        return out[local == d].iloc[0]["instit_age_days"]

    assert age("2024-01-05") == 1              # 週五 → 週四，正常
    assert age("2024-01-08") == 3              # 週一 → 上週五，跳過六日
    assert age("2024-01-09") == 1              # 週二 → 週一，正常

    # B. 農曆年式連續長假：籌碼停在 02-05，下一根交易 bar 到 02-19（14 個日曆日）
    frame2 = _instit_frame(["2024-02-02", "2024-02-05"], foreign_net=[1, 2])
    bars2 = _bars(["2024-02-05", "2024-02-19"])
    out2 = align_chips_to_bars(bars2, {"instit": frame2}, session_tz=TZ)
    local2 = out2.index.tz_convert(TZ).strftime("%Y-%m-%d")
    long_gap_age = out2[local2 == "2024-02-19"].iloc[0]["instit_age_days"]
    assert long_gap_age == 14, f"長假跳空 age 應為 14 個日曆日，得 {long_gap_age}"
    assert out2[local2 == "2024-02-19"].iloc[0]["instit_asof_date"] == "2024-02-05"


# --------------------------------------------------------------------------
# 測試 7：混合 datetime 解析度（真實資料回歸）——ns bar 軸 × us 籌碼日期
# --------------------------------------------------------------------------
def test_mixed_datetime_resolution_ns_bars_us_chip_dates():
    """回歸：storage_sqlite 以 int64 奈秒往返 → 讀回的 bar 軸是 datetime64[ns]；籌碼
    date 是 ISO 字串、pandas 3.0 的 to_datetime 解析成 datetime64[us]。兩邊 join key
    解析度不符會讓 merge_asof 拋 MergeError——這在真實台股日K（klines_tw.sqlite）×
    真實籌碼（chips_tw.sqlite）端到端對齊時實際踩到（合成 us bar 軸的既有測試漏掉）。
    修正後 features 把兩邊 join key 都釘在 ns，對齊要正常跑且維持排他式語意。"""
    dates = ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    # bar 軸刻意造成 ns 解析度（模擬 storage int64 奈秒往返，非合成 us 時間戳）
    idx = pd.DatetimeIndex([pd.Timestamp(f"{d} 09:00:00", tz=TZ) for d in dates]).as_unit("ns")
    assert str(idx.dtype) == "datetime64[ns, Asia/Taipei]"
    # 前置條件：籌碼 date 經 to_datetime 解析為 us，與 bar 的 ns 不同（否則此測試無意義）
    assert str(pd.to_datetime(pd.Series(dates)).dt.normalize().dtype) == "datetime64[us]"

    bars = pd.DataFrame(index=idx)
    frame = _instit_frame(dates, foreign_net=[10, 20, 30, 40])

    out = align_chips_to_bars(bars, {"instit": frame}, session_tz=TZ)  # 不得拋 MergeError

    local = out.index.tz_convert(TZ).strftime("%Y-%m-%d")
    # 排他式語意仍成立：每根 bar 拿「前一交易日」的 chip
    assert np.isnan(out[local == "2024-01-03"].iloc[0]["foreign_net"])   # 首根之前無更早籌碼
    assert out[local == "2024-01-04"].iloc[0]["foreign_net"] == 10       # ← chip[01-03]
    assert out[local == "2024-01-05"].iloc[0]["foreign_net"] == 20       # ← chip[01-04]
    assert out[local == "2024-01-08"].iloc[0]["foreign_net"] == 30       # ← chip[01-05]（跳週末）
    assert out[local == "2024-01-08"].iloc[0]["instit_age_days"] == 3
