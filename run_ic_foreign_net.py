"""Phase 3：foreign_net IC 診斷（特徵資訊價值檢驗，純分析不碰回測）。

背景：chip 閘門（外資買超才做多）在長樣本＋真實成本下 9/9 窗全面跑輸
baseline（見 HANDOFF.md「台股資料擴充＋獨立雙窗驗證」）。在決定換特徵／
改連續加權／棄用 chip-gating 之前，先驗證 foreign_net 本身對未來報酬有無
任何統計資訊價值——IC≈0 則後兩個方向都不用做。

設計（使用者 2026-07-12 確認，標準版 fwd return）：
- 主指標 Spearman rank IC（Pearson 參照）。
- 特徵兩版本：raw = as-of foreign_net(T)；s5 = rolling_chip_sum(fn, 5)
  （與 chip_window=5 同一函式）。smoothing 在各窗（H1/H2）內獨立計算，
  兩窗零資料共享。
- fwd_k(T) = close(T+k)/close(T) - 1，k ∈ {1, 5, 10} 交易日（close.shift(-k)，
  尾端 k 根 NaN 不回繞）。標準版，非執行延遲版——本輪是純診斷。
- 3 檔入統計（2330/2603/8069）× H1/H2 獨立雙半窗（切法與 run_wf_tw_chip
  相同：依 bar 數對半）× 每窗 15 個連續不重疊等長子段逐 fold 算 IC；
  fwd 在 fold 段內計算，fold 間零 close 共享。NaN 配對逐對剔除。
- 6446 照既有標注排除（籌碼 2024-01-25 斷尾，僅作 stale 測試樣本）。

判讀紀律：H1/H2 方向不一致 → 誠實回報「落在雜訊內」，不拿單窗下結論；
|mean IC| < ~0.02 且 fold 正負各半 → 視為無資訊。

用法：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ic_foreign_net.py
"""

from __future__ import annotations

import pandas as pd

from backtest.ic import fold_ics, summarize_folds
from data import storage_chips, storage_sqlite
from indicators.features import align_chips_to_bars
from strategy.ma_rsi_chip import rolling_chip_sum

STOCKS = {"2330": "TWSE", "2603": "TWSE", "8069": "TPEx"}  # 6446 排除（斷尾標注）
CHIP_START = "2004-12-01"
CHIP_END = "2026-12-31"
MAX_AGE_DAYS = 7            # 與 run_wf_tw_chip 一致
N_FOLDS = 15
HORIZONS = (1, 5, 10)       # 交易日
SMOOTH_N = 5                # 與 chip_window=5 同值同函式
NO_INFO_ABS_IC = 0.02       # |mean IC| 低於此且 fold 正負各半 → 無資訊


def build_augmented(kconn, cconn, stock_id: str, exchange: str) -> pd.DataFrame:
    """讀真實日K + 真實籌碼 → canonical + foreign_net（與 run_wf_tw_chip 同構）。"""
    price = storage_sqlite.load_klines(kconn, stock_id, "1d", exchange=exchange)
    bars = price.set_index("ts")
    instit = storage_chips.load_chips(
        cconn, "chip_institutional", stock_id=stock_id, start=CHIP_START, end=CHIP_END
    )
    feat = align_chips_to_bars(
        bars, {"instit": instit}, session_tz="Asia/Taipei", max_age_days=MAX_AGE_DAYS
    )
    aug = price.set_index("ts")
    aug["foreign_net"] = feat["foreign_net"]
    return aug.reset_index()


def main() -> None:
    kconn = storage_sqlite.connect("data/klines_tw.sqlite")
    cconn = storage_chips.connect()
    try:
        rows = []
        for stock_id, exchange in STOCKS.items():
            aug = build_augmented(kconn, cconn, stock_id, exchange)
            n = len(aug)
            windows = {"H1": aug.iloc[: n // 2], "H2": aug.iloc[n // 2 :]}
            for wname, sub_raw in windows.items():
                sub = sub_raw.reset_index(drop=True)
                lo = sub["ts"].iloc[0].tz_convert("Asia/Taipei").strftime("%Y-%m")
                hi = sub["ts"].iloc[-1].tz_convert("Asia/Taipei").strftime("%Y-%m")
                # smoothing 在窗內獨立計算（H1/H2 零資料共享）；
                # 窗內相鄰 fold 有至多 N-1 根因果 rolling 重疊，屬過去資料非洩漏
                feats = {
                    "raw": sub["foreign_net"],
                    "s5": rolling_chip_sum(sub["foreign_net"], SMOOTH_N),
                }
                for fname, feature in feats.items():
                    for k in HORIZONS:
                        s = summarize_folds(
                            fold_ics(feature, sub["close"], k=k, n_folds=N_FOLDS)
                        )
                        rows.append({
                            "stock": f"{stock_id}/{exchange}",
                            "window": f"{wname}({lo}→{hi})",
                            "wname": wname,
                            "feature": fname,
                            "k": k,
                            "mean_IC": s["mean_spearman"],
                            "std_IC": s["std_spearman"],
                            "pos_frac": s["pos_frac_spearman"],
                            "mean_pearson": s["mean_pearson"],
                            "n_valid/fold": s["mean_n_valid"],
                            "folds_valid": s["n_folds_valid"],
                        })
        df = pd.DataFrame(rows)

        print("=== [A] 36 格完整矩陣：Spearman rank IC（mean±std over 15 folds）===")
        print(f"    特徵：raw = as-of foreign_net(T)；s5 = rolling {SMOOTH_N} 日累加")
        print(f"    fwd_k = close(T+k)/close(T)-1（標準版）；6446 排除；Pearson 僅參照")
        out = df.copy()
        out["mean_IC"] = out["mean_IC"].map(lambda v: f"{v:+.4f}")
        out["std_IC"] = out["std_IC"].map(lambda v: f"{v:.4f}")
        out["pos_frac"] = out["pos_frac"].map(lambda v: f"{v:.0%}")
        out["mean_pearson"] = out["mean_pearson"].map(lambda v: f"{v:+.4f}")
        out["n_valid/fold"] = out["n_valid/fold"].map(lambda v: f"{v:.0f}")
        print(out.drop(columns=["wname"]).to_string(index=False))

        print("\n=== [B] H1/H2 跨窗一致性判讀（每檔 × 特徵 × k）===")
        print(f"    紀律：方向不一致=雜訊；|mean IC|<{NO_INFO_ABS_IC} 且 fold 正負各半=無資訊")
        rows_b = []
        for stock in df["stock"].unique():
            for fname in ("raw", "s5"):
                for k in HORIZONS:
                    h1 = df[(df["stock"] == stock) & (df["feature"] == fname)
                            & (df["k"] == k) & (df["wname"] == "H1")].iloc[0]
                    h2 = df[(df["stock"] == stock) & (df["feature"] == fname)
                            & (df["k"] == k) & (df["wname"] == "H2")].iloc[0]
                    same_sign = (h1["mean_IC"] > 0) == (h2["mean_IC"] > 0)
                    both_material = (abs(h1["mean_IC"]) >= NO_INFO_ABS_IC
                                     and abs(h2["mean_IC"]) >= NO_INFO_ABS_IC)
                    if same_sign and both_material:
                        verdict = ("跨窗一致正IC" if h1["mean_IC"] > 0
                                   else "跨窗一致負IC")
                    elif same_sign:
                        verdict = "同號但量級<0.02（弱）"
                    else:
                        verdict = "方向不一致（雜訊）"
                    rows_b.append({
                        "stock": stock, "feature": fname, "k": k,
                        "H1 mean_IC": f"{h1['mean_IC']:+.4f}",
                        "H2 mean_IC": f"{h2['mean_IC']:+.4f}",
                        "H1 pos%": f"{h1['pos_frac']:.0%}",
                        "H2 pos%": f"{h2['pos_frac']:.0%}",
                        "判讀": verdict,
                    })
        print(pd.DataFrame(rows_b).to_string(index=False))

        print("\n=== [C] 彙總：特徵版本 × horizon 的整體 mean IC（3 檔 × 2 窗平均）===")
        piv = df.pivot_table(index="feature", columns="k", values="mean_IC",
                             aggfunc="mean")
        print(piv.map(lambda v: f"{v:+.4f}").to_string())
        print("\n（判讀屬事實呈現；策略方向的決策屬使用者。）")
    finally:
        kconn.close()
        cconn.close()


if __name__ == "__main__":
    main()
