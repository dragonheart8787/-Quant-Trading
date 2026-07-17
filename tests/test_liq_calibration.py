"""ATR 強平防線校正（機制層量測模組）單元測試。

涵蓋：
  - 空頭區段抽取：與 short_risk_overlay 同慣例（signal.shift(1)、
    進場價 = 區段首根前一收盤）。
  - 逐區段最大不利波幅（% 與 ATR 倍數）與持有到自然翻轉的反事實報酬。
  - 觸發偵測：ATR 線 / 固定安全網哪條先觸發、觸發當下浮虧、
    ATR NaN 整根跳過（對齊 overlay / E2 語意）。
  - 誤殺/保護分類（誤殺 = 觸發但持有到自然翻轉其實是賺的）。
  - 與 apply_short_forced_liquidation 的觸發 bar 一致性交叉驗證。
  - run_calibration 全網格輸出結構。
  - E2 透傳：RiskConfig 新欄位 + 外部 atr_series 即完成參數注入，
    event_engine 不需任何改動。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.liq_calibration import (
    ShortSegment,
    detect_trigger,
    extract_short_segments,
    run_calibration,
    segment_adverse_stats,
    summarize_grid,
)
from backtest.short_risk_overlay import apply_short_forced_liquidation
from risk.manager import RiskConfig, RiskManager


def _series(values) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float))


# ===== 區段抽取 =====

class TestExtractSegments:
    def test_boundaries_and_entry_price(self):
        """shift(1) 語意：signal t 根 -1 → position t+1 根起；進場價 = 首根前一收盤。"""
        signal = pd.Series([0, -1, -1, 0, -1, -1, -1, 0])
        close = _series([100, 101, 102, 103, 104, 105, 106, 107])
        segs = extract_short_segments(signal, close)
        assert len(segs) == 2
        assert (segs[0].start, segs[0].end) == (2, 4)
        assert segs[0].entry_price == pytest.approx(101.0)
        assert (segs[1].start, segs[1].end) == (5, 8)
        assert segs[1].entry_price == pytest.approx(104.0)

    def test_no_short_signal_yields_empty(self):
        signal = pd.Series([0, 1, 1, 0, 1])
        close = _series([100, 101, 102, 103, 104])
        assert extract_short_segments(signal, close) == []

    def test_segment_running_to_series_end(self):
        signal = pd.Series([0, -1, -1, -1])
        close = _series([100, 99, 98, 97])
        segs = extract_short_segments(signal, close)
        assert len(segs) == 1
        assert (segs[0].start, segs[0].end) == (2, 4)  # 尾端未翻轉也算一段


# ===== 逐區段量測 =====

class TestAdverseStats:
    def test_hand_computed_numbers(self):
        # 區段 bars 2..4：entry=100（close[1]），區段內 close=[102, 98, 105]
        close = _series([99, 100, 102, 98, 105, 90])
        atr = _series([np.nan, 1, 1, 2, np.nan, 1])
        seg = ShortSegment(start=2, end=5, entry_price=100.0)
        stats = segment_adverse_stats(seg, close, atr)
        # 最大不利 = (105-100)/100
        assert stats.max_adverse_pct == pytest.approx(0.05)
        # ATR 倍數：(102-100)/1=2、(98-100)/2=-1、bar4 ATR NaN 跳過 → max=2
        assert stats.max_atr_mult == pytest.approx(2.0)
        # 持有到自然翻轉（end-1 = bar4，close=105）：(100-105)/100 = -5%
        assert stats.held_to_end_return == pytest.approx(-0.05)

    def test_all_nan_atr_gives_nan_mult(self):
        close = _series([100, 100, 103, 101])
        atr = _series([np.nan] * 4)
        seg = ShortSegment(start=2, end=4, entry_price=100.0)
        stats = segment_adverse_stats(seg, close, atr)
        assert np.isnan(stats.max_atr_mult)
        assert stats.max_adverse_pct == pytest.approx(0.03)


# ===== 觸發偵測 =====

class TestDetectTrigger:
    def test_atr_line_triggers_first(self):
        # entry=100、ATR=2、N=3 → 防線 106；固定網 115 未到 → line="atr"
        close = _series([100, 100, 103, 106.5, 110, 100])
        atr = _series([2.0] * 6)
        seg = ShortSegment(start=2, end=6, entry_price=100.0)
        out = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out is not None
        assert out.bar == 3
        assert out.line == "atr"
        assert out.loss_at_trigger_pct == pytest.approx(0.065)
        assert out.bars_from_start == 1

    def test_fixed_net_triggers_when_atr_huge(self):
        # ATR=50 → ATR 線 250 永不觸發；116 ≥ 115 → line="fixed"
        close = _series([100, 100, 110, 116, 120])
        atr = _series([50.0] * 5)
        seg = ShortSegment(start=2, end=5, entry_price=100.0)
        out = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out is not None
        assert out.bar == 3
        assert out.line == "fixed"
        assert out.loss_at_trigger_pct == pytest.approx(0.16)

    def test_both_lines_same_bar(self):
        # close=120 同根越過 ATR 線 106 與固定網 115 → line="both"
        close = _series([100, 100, 120])
        atr = _series([2.0] * 3)
        seg = ShortSegment(start=2, end=3, entry_price=100.0)
        out = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out is not None
        assert out.line == "both"

    def test_nan_atr_bar_skipped_entirely(self):
        # bar2 越過固定網但 ATR NaN → 整根跳過（對齊 overlay）；bar3 才觸發
        close = _series([100, 100, 120, 121])
        atr = _series([2.0, 2.0, np.nan, 2.0])
        seg = ShortSegment(start=2, end=4, entry_price=100.0)
        out = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out is not None
        assert out.bar == 3

    def test_no_trigger_within_bounds(self):
        close = _series([100, 100, 104, 103, 99])
        atr = _series([2.0] * 5)
        seg = ShortSegment(start=2, end=5, entry_price=100.0)
        assert detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15) is None

    def test_tighter_n_triggers_earlier(self):
        """同一路徑，N 越小觸發越早（或從不觸發變觸發）。"""
        close = _series([100, 100, 103, 105, 107, 100])
        atr = _series([2.0] * 6)
        seg = ShortSegment(start=2, end=6, entry_price=100.0)
        out_n15 = detect_trigger(seg, close, atr, atr_n=1.5, fixed_pct=0.15)
        out_n3 = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out_n15 is not None and out_n15.bar == 2  # 103 ≥ 100+1.5*2
        assert out_n3 is not None and out_n3.bar == 4    # 107 ≥ 106
        assert out_n15.bar < out_n3.bar


class TestOverlayConsistency:
    """交叉驗證：detect_trigger 的觸發 bar 必須與 overlay 歸零起點逐一相同。"""

    def test_trigger_bars_match_overlay_on_volatile_path(self):
        rng = np.random.default_rng(17)
        n = 400
        rets = rng.normal(0.0, 0.02, size=n)
        for k in (60, 110, 250, 320):
            rets[k] = 0.09
        close = pd.Series(100.0 * np.cumprod(1.0 + rets))

        values = np.zeros(n, dtype=int)
        values[40:90] = -1
        values[100:160] = -1
        values[230:280] = -1
        values[300:360] = -1
        signal = pd.Series(values)

        from indicators.technical import atr as compute_atr

        atr_series = compute_atr(
            pd.DataFrame({"high": close * 1.01, "low": close * 0.99, "close": close}),
            window=14,
        )

        # overlay 的歸零起點
        adj = apply_short_forced_liquidation(signal, close, atr_series)
        shifted = signal.shift(1).fillna(0.0).to_numpy()
        adj_arr = adj.to_numpy()
        overlay_bars: list[int] = []
        in_zeroed = False
        for t in range(n):
            if shifted[t] == -1.0 and adj_arr[t] == 0.0:
                if not in_zeroed:
                    overlay_bars.append(t)
                    in_zeroed = True
            else:
                in_zeroed = False

        segs = extract_short_segments(signal, close)
        mine = [
            out.bar
            for seg in segs
            if (out := detect_trigger(seg, close, atr_series, 3.0, 0.15)) is not None
        ]
        assert len(overlay_bars) > 0
        assert mine == overlay_bars


# ===== 全網格 run_calibration =====

class _FixedSignalStrategy:
    """測試替身：回傳預先給定的訊號序列。"""

    def __init__(self, signal: pd.Series) -> None:
        self._signal = signal

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        return pd.Series(self._signal.to_numpy(), index=data.index, dtype="int64")


def _make_canonical(close: np.ndarray) -> pd.DataFrame:
    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    return pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": open_,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "symbol": ["TEST"] * n,
        }
    )


class TestRunCalibration:
    def _run(self):
        rng = np.random.default_rng(7)
        n = 240
        rets = rng.normal(0.0, 0.02, size=n)
        rets[80] = 0.12  # 段內強力反彈製造觸發
        close = 100.0 * np.cumprod(1.0 + rets)
        data = _make_canonical(close)
        values = np.zeros(n, dtype=int)
        values[70:110] = -1
        values[150:200] = -1
        signal = pd.Series(values)
        strat = _FixedSignalStrategy(signal)
        return run_calibration(
            data, strat, n_splits=3,
            atr_ns=(1.5, 3.0), atr_windows=(7, 14), fixed_pct=0.15,
        )

    def test_output_structure(self):
        segments_df, triggers_df = self._run()
        assert len(segments_df) > 0
        # 每區段 × 每組合各一列
        assert len(triggers_df) == len(segments_df) * 4
        assert set(triggers_df["atr_n"].unique()) == {1.5, 3.0}
        assert set(triggers_df["atr_window"].unique()) == {7, 14}
        for col in ("fold", "seg_id", "triggered", "line",
                    "loss_at_trigger_pct", "false_kill", "protect"):
            assert col in triggers_df.columns
        for col in ("fold", "seg_id", "start_ts", "end_ts", "n_bars",
                    "entry_price", "max_adverse_pct", "held_to_end_return",
                    "max_atr_mult_w7", "max_atr_mult_w14"):
            assert col in segments_df.columns

    def test_false_kill_protect_partition(self):
        """觸發的區段必分類為誤殺或保護（互斥）；未觸發兩者皆 False。"""
        _, triggers_df = self._run()
        trig = triggers_df[triggers_df["triggered"]]
        assert len(trig) > 0  # 測試要有料
        assert (trig["false_kill"] != trig["protect"]).all()
        untrig = triggers_df[~triggers_df["triggered"]]
        assert (~untrig["false_kill"]).all() and (~untrig["protect"]).all()

    def test_summarize_grid_counts(self):
        _, triggers_df = self._run()
        summary = summarize_grid(triggers_df)
        assert len(summary) == 4  # 2 N × 2 window
        row = summary.loc[(summary["atr_n"] == 1.5) & (summary["atr_window"] == 7)].iloc[0]
        sub = triggers_df[(triggers_df["atr_n"] == 1.5) & (triggers_df["atr_window"] == 7)]
        assert row["n_segments"] == len(sub)
        assert row["n_triggered"] == int(sub["triggered"].sum())
        assert row["n_false_kill"] == int(sub["false_kill"].sum())
        assert row["n_protect"] == int(sub["protect"].sum())
        # 浮虧分布欄（觸發當下 unrealized loss %）
        for col in ("loss_at_trigger_med", "loss_at_trigger_max"):
            assert col in summary.columns


# ===== 觸發依據 trigger_source（high 觸價敏感度輪，2026-07-14）=====

class TestTriggerSourceHigh:
    def test_high_crosses_but_close_does_not(self):
        # entry=100、ATR=2、N=3 → 防線 106；close 最高 104 永不觸發，
        # bar3 盤中 high=107 越線 → 只有 high 模式觸發
        close = _series([100, 100, 103, 104, 100])
        high = _series([101, 100, 104, 107, 101])
        atr = _series([2.0] * 5)
        seg = ShortSegment(start=2, end=5, entry_price=100.0)
        assert detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15) is None
        out = detect_trigger(
            seg, close, atr, atr_n=3.0, fixed_pct=0.15, trigger_price=high
        )
        assert out is not None
        assert out.bar == 3
        assert out.line == "atr"
        # 浮虧兩個界：high = 當根最壞上界；觸線價 = 真實強平成交近似
        assert out.loss_at_trigger_pct == pytest.approx(0.07)
        assert out.loss_at_line_pct == pytest.approx(0.06)

    def test_close_mode_reports_line_loss_too(self):
        # close 模式也回報觸線價浮虧（資訊欄）：close=106.5 越線 106
        close = _series([100, 100, 103, 106.5, 110, 100])
        atr = _series([2.0] * 6)
        seg = ShortSegment(start=2, end=6, entry_price=100.0)
        out = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out is not None
        assert out.loss_at_trigger_pct == pytest.approx(0.065)
        assert out.loss_at_line_pct == pytest.approx(0.06)

    def test_fixed_net_line_loss_uses_fixed_level(self):
        # ATR=50 → ATR 線永不觸發；fixed 觸發時觸線價 = entry*(1+0.15)
        close = _series([100, 100, 110, 116, 120])
        atr = _series([50.0] * 5)
        seg = ShortSegment(start=2, end=5, entry_price=100.0)
        out = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out is not None
        assert out.line == "fixed"
        assert out.loss_at_line_pct == pytest.approx(0.15)

    def test_both_lines_line_loss_is_lower_level(self):
        # 同根越過 ATR 線 106 與固定網 115 → 觸線價 = 先碰到的較低線 106
        close = _series([100, 100, 120])
        atr = _series([2.0] * 3)
        seg = ShortSegment(start=2, end=3, entry_price=100.0)
        out = detect_trigger(seg, close, atr, atr_n=3.0, fixed_pct=0.15)
        assert out is not None
        assert out.line == "both"
        assert out.loss_at_line_pct == pytest.approx(0.06)

    def test_nan_atr_bar_skipped_in_high_mode(self):
        # bar2 high 越線但 ATR NaN → 整根跳過（與 close 模式同語意）
        close = _series([100, 100, 100, 100])
        high = _series([100, 100, 120, 121])
        atr = _series([2.0, 2.0, np.nan, 2.0])
        seg = ShortSegment(start=2, end=4, entry_price=100.0)
        out = detect_trigger(
            seg, close, atr, atr_n=3.0, fixed_pct=0.15, trigger_price=high
        )
        assert out is not None
        assert out.bar == 3

    def test_high_dominates_close_property(self):
        """數學保證：high ≥ close ⇒ high 模式觸發集合 ⊇ close 模式，
        且共同觸發的區段 high 觸發 bar ≤ close 觸發 bar。"""
        from indicators.technical import atr as compute_atr

        rng = np.random.default_rng(23)
        n = 600
        rets = rng.normal(0.0, 0.02, size=n)
        for k in (80, 140, 300, 420, 510):
            rets[k] = 0.07
        close = pd.Series(100.0 * np.cumprod(1.0 + rets))
        high = close * (1.0 + np.abs(rng.normal(0.0, 0.01, size=n)))
        low = close * (1.0 - np.abs(rng.normal(0.0, 0.01, size=n)))
        atr_series = compute_atr(
            pd.DataFrame({"high": high, "low": low, "close": close}), window=14
        )

        values = np.zeros(n, dtype=int)
        for s, e in ((60, 130), (135, 200), (280, 350), (400, 460), (490, 560)):
            values[s:e] = -1
        signal = pd.Series(values)

        n_close = n_high = 0
        checked = 0
        for seg in extract_short_segments(signal, close):
            out_c = detect_trigger(seg, close, atr_series, 3.0, 0.15)
            out_h = detect_trigger(
                seg, close, atr_series, 3.0, 0.15, trigger_price=high
            )
            n_close += out_c is not None
            n_high += out_h is not None
            if out_c is not None:
                assert out_h is not None, "close 觸發但 high 未觸發，違反支配性"
                assert out_h.bar <= out_c.bar
                checked += 1
        assert checked > 0  # 測試要有料
        assert n_high >= n_close


class TestRunCalibrationTriggerSource:
    def _make_inputs(self):
        rng = np.random.default_rng(7)
        n = 240
        rets = rng.normal(0.0, 0.02, size=n)
        rets[80] = 0.12
        close = 100.0 * np.cumprod(1.0 + rets)
        data = _make_canonical(close)
        values = np.zeros(n, dtype=int)
        values[70:110] = -1
        values[150:200] = -1
        signal = pd.Series(values)
        return data, _FixedSignalStrategy(signal)

    def test_default_identical_to_explicit_close(self):
        """保護性回歸：預設 ≡ trigger_source='close' 逐位相同。"""
        data, strat = self._make_inputs()
        kwargs = dict(
            n_splits=3, atr_ns=(1.5, 3.0), atr_windows=(7, 14), fixed_pct=0.15
        )
        seg_a, trig_a = run_calibration(data, strat, **kwargs)
        seg_b, trig_b = run_calibration(data, strat, trigger_source="close", **kwargs)
        pd.testing.assert_frame_equal(seg_a, seg_b)
        pd.testing.assert_frame_equal(trig_a, trig_b)

    def test_high_mode_superset_and_columns(self):
        data, strat = self._make_inputs()
        kwargs = dict(
            n_splits=3, atr_ns=(3.0,), atr_windows=(14,), fixed_pct=0.15
        )
        seg_c, trig_c = run_calibration(data, strat, **kwargs)
        seg_h, trig_h = run_calibration(data, strat, trigger_source="high", **kwargs)
        # 觸發集合支配性（彙總層）
        assert int(trig_h["triggered"].sum()) >= int(trig_c["triggered"].sum())
        # high 模式加註盤中安全邊際欄
        assert "max_atr_mult_high_w14" in seg_h.columns
        assert "max_adverse_pct_high" in seg_h.columns
        # 兩模式都有觸線價浮虧欄
        assert "loss_at_line_pct" in trig_c.columns
        assert "loss_at_line_pct" in trig_h.columns

    def test_invalid_trigger_source_raises(self):
        data, strat = self._make_inputs()
        with pytest.raises(ValueError):
            run_calibration(
                data, strat, n_splits=3, atr_ns=(3.0,), atr_windows=(14,),
                trigger_source="open",
            )


# ===== E2 透傳：RiskConfig 新欄位 + 外部 atr_series，event_engine 零改動 =====

class TestE2ParamPassthrough:
    def test_custom_atr_n_changes_liquidation_via_risk_config(self):
        from backtest.event_engine import run_event_backtest

        # entry @100、ATR=2：N=3 防線 106 不觸發；N=2 防線 104 → 104.9 觸發
        close = np.array([100.0, 100.0, 100.0, 104.9, 104.9, 104.9])
        data = _make_canonical(close)
        signal = pd.Series([0, -1, -1, -1, -1, 0])
        atr_series = pd.Series(np.full(len(close), 2.0))

        ev_default = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0), atr_series=atr_series,
        )
        assert ev_default.liquidation_bars == []

        ev_n2 = run_event_backtest(
            data, signal, fee_bps=0.0, fill_mode="signal_close",
            risk=RiskManager(equity=1.0, config=RiskConfig(forced_liq_atr_n=2.0)),
            atr_series=atr_series,
        )
        assert ev_n2.liquidation_bars == [3]
