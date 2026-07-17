"""Phase 3：台股籌碼特徵併入 walk-forward（訊號層驗證，不追 alpha）。

三方對照（2026-07-10 起）：
  baseline = MaRsiStrategy(5/20/14，long-only)               ← 只看 MA/RSI
  chip N=1 = MaRsiChipStrategy(+ 當日 foreign_net>0)          ← 原始閘門
  chip N=5 = MaRsiChipStrategy(chip_window=5)                 ← 5 日淨買超 smoothing
                                                                （起點值★待校正★）
動機：當日閘門逐日翻正負放大換手（2330 base 3 次 vs chip 20 次），台股成本重在
賣出端（證交稅 30bps），換手放大直接侵蝕報酬（8069 chip -50.6pp 年化）。N 日
累加抑制翻轉；但平滑必然讓閘門反應變慢——**及時性代價與換手降低一起呈現**：
  - 「N=1 做多、N=5 空手」bar 數 = 進場延後或被濾
  - 「N=1 空手、N=5 做多」bar 數 = 出場延後（外資當日賣超仍持有）
  - 平滑版「濾:缺失」= 暖機（前 N-1）+ NaN 傳染（單日缺失關閘 N 天）+ 斷尾

★ 過濾成因兩類拆解 ★（避免把資料品質空窗誤讀成策略邏輯效果）：
  decompose_chip_filtering 對 N=5 傳 rolling_chip_sum 序列——
  filtered_foreign_sell = N 日淨賣超（閘門正常生效）；
  filtered_missing_chip = rolling 視窗含 NaN（暖機/缺失傳染/斷尾，非策略判斷）

紀律：日K 年化因子 periods_per_year=252（非預設 8760）；n_splits=3、fold 小、
數字有雜訊——這輪只驗管線，不下策略優劣結論。

成本（2026-07-10 起接真實台股成本）：tw_stock_costs()＝買 14.25bps（牌告手續費
無折扣，保守假設）、賣 44.25bps（手續費+證交稅 30bps）；與舊 5bps 對稱假設
並排對照，看賣出端疊加的侵蝕量級。注意：成本不影響訊號（做多 bar 數兩制相同），
只影響報酬。**限制：券商單筆低消 NT$20 無法建模**（報酬率制引擎無名目金額），
小額交易實際成本會被低估。

用法：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_wf_tw_chip.py
"""

from __future__ import annotations

import pandas as pd

from backtest.costs import tw_stock_costs
from backtest.walk_forward import walk_forward
from data import storage_chips, storage_sqlite
from indicators.features import align_chips_to_bars
from strategy.ma_rsi import MaRsiStrategy
from strategy.ma_rsi_chip import (
    MaRsiChipStrategy,
    decompose_chip_filtering,
    rolling_chip_sum,
)

POC = {
    "2330": "TWSE", "2603": "TWSE", "8069": "TPEx", "6446": "TPEx",
}
# 2026-07-10 資料擴充：價格已抓到「價格∩籌碼交集」全區間（2330/2603 自 2012-05、
# 8069 自 2005-01、6446 自 2014-03），籌碼庫本來就是全歷史 → 讀取視窗放寬到全區間
CHIP_START = "2004-12-01"
CHIP_END = "2026-12-31"
MAX_AGE_DAYS = 7
FAST, SLOW, RSI_W = 5, 20, 14
N_SPLITS = 15               # 比照放空/A2 輪紀律：雙位數獨立 fold
PERIODS_PER_YEAR = 252      # ★ 台股交易日/年；非預設 8760（1h）★
EXCLUDE_FROM_STATS = {"6446"}   # 籌碼 2024-01-25 斷尾，僅作 stale 機制測試樣本，
                                # ★不納入 N=1 vs N=5 的統計判斷★
TW_COSTS = tw_stock_costs()  # 買 14.25 / 賣 44.25 bps（牌告無折扣＋證交稅）
LEGACY_FEE_BPS = 5.0         # 舊制對照（加密貨幣對稱假設）
CHIP_N = 5                   # ★ smoothing 視窗起點，待校正非最終值 ★


def build_augmented(kconn, cconn, stock_id: str, exchange: str) -> pd.DataFrame:
    """讀真實日K + 真實籌碼，用 align_chips_to_bars 併出 foreign_net 欄，
    回傳「canonical + foreign_net」的 augmented DataFrame（ts 為欄、RangeIndex）。"""
    price = storage_sqlite.load_klines(kconn, stock_id, "1d", exchange=exchange)
    bars = price.set_index("ts")  # tz-aware UTC index

    instit = storage_chips.load_chips(
        cconn, "chip_institutional", stock_id=stock_id, start=CHIP_START, end=CHIP_END
    )
    feat = align_chips_to_bars(
        bars, {"instit": instit}, session_tz="Asia/Taipei", max_age_days=MAX_AGE_DAYS
    )

    aug = price.set_index("ts")
    aug["foreign_net"] = feat["foreign_net"]        # 依 ts index join（用鍵不用位置）
    aug["instit_asof_date"] = feat["instit_asof_date"]
    return aug.reset_index()


def signal_transitions(sig: pd.Series) -> int:
    """訊號變動次數（進場+出場），近似換手。"""
    prev = sig.shift(1).fillna(0)
    return int((sig - prev).abs().gt(0).sum())


def main() -> None:
    kconn = storage_sqlite.connect("data/klines_tw.sqlite")
    cconn = storage_chips.connect()
    try:
        augmented = {}
        signals = {}          # (stock_id, variant) -> pd.Series
        strategies = {
            "base": lambda: MaRsiStrategy(FAST, SLOW, RSI_W),
            "chip1": lambda: MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=1),
            "chip5": lambda: MaRsiChipStrategy(FAST, SLOW, RSI_W, chip_window=CHIP_N),
        }
        for stock_id, exchange in POC.items():
            aug = build_augmented(kconn, cconn, stock_id, exchange)
            augmented[stock_id] = aug
            for variant, mk in strategies.items():
                signals[(stock_id, variant)] = mk().generate_signals(aug)

        print("=== [A] 訊號與換手三方對照（baseline / chip N=1 / chip N=5）===")
        rows_a = []
        for stock_id, exchange in POC.items():
            aug = augmented[stock_id]
            asof_last = (aug["instit_asof_date"].dropna().iloc[-1]
                         if aug["instit_asof_date"].notna().any() else "—")
            rows_a.append({
                "stock": f"{stock_id}/{exchange}",
                "bars": len(aug),
                "base_long": int((signals[(stock_id, "base")] == 1).sum()),
                "N1_long": int((signals[(stock_id, "chip1")] == 1).sum()),
                "N5_long": int((signals[(stock_id, "chip5")] == 1).sum()),
                "base_換手": signal_transitions(signals[(stock_id, "base")]),
                "N1_換手": signal_transitions(signals[(stock_id, "chip1")]),
                "N5_換手": signal_transitions(signals[(stock_id, "chip5")]),
                "籌碼末日": asof_last,
            })
        print(pd.DataFrame(rows_a).to_string(index=False))

        print(f"\n=== [B] 平滑版（N={CHIP_N}）過濾成因拆解（對 rolling sum 序列分類）===")
        print("  恆等式：base_long = N5_long + 濾:N日淨賣超 + 濾:缺失(暖機/NaN傳染/斷尾)")
        rows_b = []
        for stock_id, exchange in POC.items():
            aug = augmented[stock_id]
            dec5 = decompose_chip_filtering(
                signals[(stock_id, "base")],
                rolling_chip_sum(aug["foreign_net"], CHIP_N),
            )
            rows_b.append({
                "stock": f"{stock_id}/{exchange}",
                "base_long": dec5["baseline_long"],
                "N5_long": dec5["kept_long"],
                "濾:N日淨賣超": dec5["filtered_foreign_sell"],
                "濾:缺失": dec5["filtered_missing_chip"],
            })
        print(pd.DataFrame(rows_b).to_string(index=False))

        print("\n=== [C] 及時性代價（平滑不是免費的：與換手降低同等份量呈現）===")
        print("  進場延後/被濾 = N=1 做多、N=5 空手；出場延後 = N=1 空手、N=5 做多")
        rows_c = []
        for stock_id, exchange in POC.items():
            c1 = signals[(stock_id, "chip1")].to_numpy()
            c5 = signals[(stock_id, "chip5")].to_numpy()
            rows_c.append({
                "stock": f"{stock_id}/{exchange}",
                "進場延後/被濾(bar)": int(((c1 == 1) & (c5 == 0)).sum()),
                "出場延後(bar)": int(((c1 == 0) & (c5 == 1)).sum()),
            })
        print(pd.DataFrame(rows_c).to_string(index=False))

        print(f"\n=== [D] 全窗＋獨立雙半窗 × {N_SPLITS}-fold 三方對照（真實台股成本）===")
        print(f"    參數：MA {FAST}/{SLOW}、RSI{RSI_W}、n_splits={N_SPLITS}、periods_per_year={PERIODS_PER_YEAR}")
        print("    H1/H2 = 每檔依 bar 數對半切的兩個完全獨立時間窗（零重疊，市場世代不同）")
        print("    侵蝕 = annual@真實成本 − annual@5bps（mean）；★6446 僅作 stale 測試，不入統計★")
        print("    ※ 券商低消 NT$20 無法建模（報酬率制引擎），小額交易成本被低估。")
        wf_stats = {}   # (stock_id, wname, variant) -> dict
        rows_d = []
        for stock_id, exchange in POC.items():
            aug = augmented[stock_id]
            n = len(aug)
            windows = {"full": aug, "H1": aug.iloc[: n // 2], "H2": aug.iloc[n // 2 :]}
            for wname, sub_raw in windows.items():
                sub = sub_raw.reset_index(drop=True)
                lo = sub["ts"].iloc[0].tz_convert("Asia/Taipei").strftime("%Y-%m")
                hi = sub["ts"].iloc[-1].tz_convert("Asia/Taipei").strftime("%Y-%m")
                for variant, mk in strategies.items():
                    wf5 = walk_forward(sub, mk(), n_splits=N_SPLITS,
                                       fee_bps=LEGACY_FEE_BPS, periods_per_year=PERIODS_PER_YEAR)
                    wftw = walk_forward(sub, mk(), n_splits=N_SPLITS,
                                        costs=TW_COSTS, periods_per_year=PERIODS_PER_YEAR)
                    a5 = wf5.metrics_mean["annual_return"]
                    atw = wftw.metrics_mean["annual_return"]
                    stat = {
                        "annual_tw": atw, "std_tw": wftw.metrics_std["annual_return"],
                        "sharpe_tw": wftw.metrics_mean["sharpe"],
                        "erosion_pp": (atw - a5) * 100,
                        "turnover": signal_transitions(mk().generate_signals(sub)),
                    }
                    wf_stats[(stock_id, wname, variant)] = stat
                    mark = " ★不入統計" if stock_id in EXCLUDE_FROM_STATS else ""
                    rows_d.append({
                        "stock": f"{stock_id}{mark}",
                        "window": f"{wname}({lo}→{hi})",
                        "bars": len(sub),
                        "variant": variant,
                        "換手": stat["turnover"],
                        "annual@真實": f"{atw:+.1%}",
                        "std": f"{stat['std_tw']:.1%}",
                        "sharpe": f"{stat['sharpe_tw']:+.2f}",
                        "侵蝕(pp)": f"{stat['erosion_pp']:+.1f}",
                    })
        print(pd.DataFrame(rows_d).to_string(index=False))

        print("\n=== [E] 核心問題：N=5 − N=1 差異在兩個獨立半窗方向是否一致 ===")
        print("    Δ換手<0 = 平滑抑制換手；Δ侵蝕>0 = 平滑縮小成本侵蝕；Δannual = 淨效果")
        rows_e = []
        for stock_id, exchange in POC.items():
            for wname in ("H1", "H2", "full"):
                s1 = wf_stats[(stock_id, wname, "chip1")]
                s5 = wf_stats[(stock_id, wname, "chip5")]
                mark = " ★不入統計" if stock_id in EXCLUDE_FROM_STATS else ""
                rows_e.append({
                    "stock": f"{stock_id}{mark}",
                    "window": wname,
                    "Δ換手(N5-N1)": s5["turnover"] - s1["turnover"],
                    "Δ侵蝕(pp)": f"{s5['erosion_pp'] - s1['erosion_pp']:+.1f}",
                    "Δannual@真實(pp)": f"{(s5['annual_tw'] - s1['annual_tw']) * 100:+.1f}",
                })
        print(pd.DataFrame(rows_e).to_string(index=False))
    finally:
        kconn.close()
        cconn.close()


if __name__ == "__main__":
    main()
