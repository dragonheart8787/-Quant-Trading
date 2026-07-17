"""台股放空第一輪機制層診斷：uptick 規則 + 鎖死耦合 + 分層 N（2026-07-16）。

範圍（使用者定案）：本輪只做兩項——3.5% 跌幅次日禁空規則、ATR 分層＋漲跌停
耦合強平。items 1/4/5（借券費時間累積成本／保證金維持率／券源限制）為
「已知限制、本輪不建模」，文件化見 HANDOFF。item 3（強制回補日曆）另開一輪。

事前判準（動工前登錄）：
  P1 uptick 正確性：blocked_fills 中 reason="uptick_rule" 的每一筆，其成交
     bar 的前一 bar 還原價跌幅必須 <= -3.5% 且成交價 < 前收（獨立重算交叉
     驗證，零例外）。
  P2 鎖死耦合正確性：deferred_fills 與 reason="limit_lock" 的每一筆，都
     必須落在原始價真實全日鎖死 bar 上（分時代門檻：2015-06-01 前 7%／後
     10%），且方向正確（BUY↔漲停、SELL↔跌停）。
  P3 逐位不變：兩參數省略時既有 tests 全過（pytest 層驗證，非本 runner）。
  P4 分層 N（判準 2026-07-16 使用者重新定義，第一版 median 錨定 FAIL 後）：
     新判準 =「trailing_q95 口徑下的典型極端值應留在固定網之下」，**不要求
     全部 bar 塌縮到 0**——任何 trailing 校正都無法預見 train 段之後才發生
     的劇烈 regime 突變（結構性限制，與漲跌停鎖死尾部風險同類：記錄不消除）。
     檢驗方式：(a) 排除劇烈 regime 位移 fold（test_q95 > 1.25×train_q95，
     1.25=15/12 的餘裕倍率）後，大部分 fold 的 >=15% 佔比顯著改善；
     (b) 劇烈位移 fold 單獨列出、具體數字，明確標注「trailing 校正的已知
     殘留風險」，不讓其他 fold 的改善把它平均掉。

訊號：v2 雙向測試訊號（延續 BTC/台股校正輪方法論，非實際部署策略）。
價格：還原價餵引擎（訊號/成交/uptick 判定）；鎖死表用原始價（執行語意）。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tw_short_rules_diag.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.costs import tw_stock_costs
from backtest.event_engine import run_event_backtest
from backtest.liq_calibration import stratified_forced_liq_n
from data import storage_events_tw
from data.adjustment import apply_back_adjustment
from data.storage_sqlite import connect, load_klines
from indicators.technical import atr as compute_atr
from risk.manager import RiskConfig, RiskManager
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy

SYMBOLS: dict[str, str] = {
    "2330": "TWSE",
    "2603": "TWSE",
    "8069": "TPEx",
    "6446": "TPEx",
}
N_SPLITS = 15
TW_DB = "data/klines_tw.sqlite"
UPTICK_DROP = 0.035
ATR_WINDOW = 14
FIXED_PCT = 0.15
# 漲跌幅限制分時代（原始價執行語意）：2015-06-01 起 10%，之前 7%；
# 鎖死偵測門檻 = 0.9 × 限制（容忍升降單位造成的微小偏離）
LIMIT_ERA_SPLIT = "2015-06-01"


def _load(symbol: str, exchange: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = connect(TW_DB)
    try:
        raw = load_klines(conn, symbol, "1d", exchange=exchange)
    finally:
        conn.close()
    econn = storage_events_tw.connect()
    try:
        dividends = storage_events_tw.load_dividend_results(econn, symbol)
    finally:
        econn.close()
    if dividends.empty:
        raise RuntimeError(f"{symbol} 事件快照無資料；請先跑 run_ingest_tw_events.py")
    adjusted = apply_back_adjustment(raw, dividends).reset_index(drop=True)
    return raw.reset_index(drop=True), adjusted


def _lock_masks(raw: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """全日鎖死偵測（原始價）：high==low 且 |close/prev-1| >= 0.9×該時代限制。"""
    close = raw["close"].to_numpy(dtype=float)
    high = raw["high"].to_numpy(dtype=float)
    low = raw["low"].to_numpy(dtype=float)
    ret = np.full(len(raw), np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    era_10pct = raw["ts"] >= pd.Timestamp(LIMIT_ERA_SPLIT, tz="Asia/Taipei").tz_convert("UTC")
    threshold = np.where(era_10pct.to_numpy(), 0.10, 0.07) * 0.9
    flat = high == low
    lock_up = pd.Series(flat & (ret >= threshold), index=raw.index)
    lock_down = pd.Series(flat & (ret <= -threshold), index=raw.index)
    return lock_up, lock_down


def main() -> None:
    strat = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, rsi_overbought=70.0, regime_window=120
    )
    costs = tw_stock_costs()

    p1_rows: list[dict] = []
    p1_violations: list[str] = []
    p2_rows: list[dict] = []
    p2_violations: list[str] = []
    p4_rows: list[dict] = []

    for symbol, exchange in SYMBOLS.items():
        print(f"跑 {symbol}（{exchange}）...")
        raw, adjusted = _load(symbol, exchange)
        lock_up_full, lock_down_full = _lock_masks(raw)
        full_signal = strat.generate_signals(adjusted)
        full_atr = compute_atr(adjusted[["high", "low", "close"]], window=ATR_WINDOW)
        splits = list(TimeSeriesSplit(n_splits=N_SPLITS).split(np.arange(len(adjusted))))

        full_ratio = (full_atr / adjusted["close"])

        for k, (train_idx, test_idx) in enumerate(splits):
            train_slice = adjusted.iloc[train_idx]
            n_strat = stratified_forced_liq_n(train_slice)  # P4：只用 train 側（因果）
            # regime 位移診斷量（q95 口徑；1.25 = 15%/12% 的餘裕倍率）
            train_q95 = float(full_ratio.iloc[train_idx][-252:].dropna().quantile(0.95))
            test_q95 = float(full_ratio.iloc[test_idx].dropna().quantile(0.95))

            adj_slice = adjusted.iloc[test_idx].reset_index(drop=True)
            raw_slice = raw.iloc[test_idx].reset_index(drop=True)
            sig_slice = full_signal.iloc[test_idx].reset_index(drop=True)
            atr_slice = full_atr.iloc[test_idx].reset_index(drop=True)
            lu = lock_up_full.iloc[test_idx].reset_index(drop=True)
            ld = lock_down_full.iloc[test_idx].reset_index(drop=True)

            ev = run_event_backtest(
                adj_slice, sig_slice,
                risk=RiskManager(equity=1.0, config=RiskConfig(forced_liq_atr_n=n_strat)),
                atr_series=atr_slice, costs=costs, slippage_bps=0.0,
                short_uptick_rule_drop=UPTICK_DROP, lock_up=lu, lock_down=ld,
            )

            adj_close = adj_slice["close"].to_numpy()
            adj_open = adj_slice["open"].to_numpy()
            raw_dates = raw_slice["ts"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")

            # ---- P1 交叉驗證：uptick 攔截逐筆重算 ----
            for bar, ts, reason, why in ev.blocked_fills:
                date_str = raw_dates.iloc[bar]
                if why == "uptick_rule":
                    drop = adj_close[bar - 1] / adj_close[bar - 2] - 1.0
                    fill_p = adj_open[bar]  # slippage=0，成交價=開盤參考價
                    ok = (drop <= -UPTICK_DROP) and (fill_p < adj_close[bar - 1])
                    p1_rows.append({
                        "symbol": symbol, "fold": k + 1, "date": date_str,
                        "prev_drop": drop, "fill_vs_prev_close": fill_p / adj_close[bar - 1] - 1.0,
                        "verified": ok,
                    })
                    if not ok:
                        p1_violations.append(f"{symbol} fold{k+1} {date_str}")
                elif why == "limit_lock":
                    # P2：作廢單必須在真實鎖死 bar 上
                    locked = bool(lu.iloc[bar]) or bool(ld.iloc[bar])
                    p2_rows.append({
                        "symbol": symbol, "fold": k + 1, "date": date_str,
                        "kind": f"dropped:{reason}", "on_true_lock": locked,
                    })
                    if not locked:
                        p2_violations.append(f"{symbol} fold{k+1} {date_str} dropped")

            # ---- P2 交叉驗證：延後單原定 bar 必須真實鎖死 ----
            for intended, actual, reason in ev.deferred_fills:
                for b in range(intended, actual):  # 被鎖住的每一根都應為真實鎖死
                    locked = bool(lu.iloc[b]) or bool(ld.iloc[b])
                    p2_rows.append({
                        "symbol": symbol, "fold": k + 1, "date": raw_dates.iloc[b],
                        "kind": f"deferred:{reason}({intended}->{actual})",
                        "on_true_lock": locked,
                    })
                    if not locked:
                        p2_violations.append(f"{symbol} fold{k+1} bar{b} deferred")

            # ---- P4：分層 N 下，測試窗動態線 >= 15% 的 bar 佔比 vs N=3 ----
            ratio = (atr_slice / adj_slice["close"]).dropna()
            p4_rows.append({
                "symbol": symbol, "fold": k + 1, "n_strat": n_strat,
                "q95_shift": test_q95 / train_q95,
                "regime_shift": test_q95 > 1.25 * train_q95,
                "frac_ge15_strat": float((n_strat * ratio >= FIXED_PCT).mean()),
                "frac_ge15_n3": float((3.0 * ratio >= FIXED_PCT).mean()),
            })

    print()
    print("=" * 100)
    print("P1：uptick 攔截逐筆交叉驗證")
    print("=" * 100)
    p1_df = pd.DataFrame(p1_rows)
    if p1_df.empty:
        print("本批資料無 uptick 攔截案例（訊號未在受限日嘗試低於前收放空）")
    else:
        print(p1_df.to_string(index=False, formatters={
            "prev_drop": "{:+.2%}".format, "fill_vs_prev_close": "{:+.2%}".format}))
        print(f"\n共 {len(p1_df)} 筆攔截，全部通過獨立重算驗證：{bool(p1_df['verified'].all())}")
    print(f"P1 判定：{'PASS' if not p1_violations else 'FAIL ' + str(p1_violations)}")

    print()
    print("=" * 100)
    print("P2：鎖死耦合逐筆交叉驗證（作廢單與延後單都必須對應真實鎖死 bar）")
    print("=" * 100)
    p2_df = pd.DataFrame(p2_rows)
    if p2_df.empty:
        print("本批資料無鎖死攔截/延後案例（測試訊號的成交時點未撞上全日鎖死）")
    else:
        print(p2_df.to_string(index=False))
        print(f"\n共 {len(p2_df)} 筆，全部落在真實鎖死 bar：{bool(p2_df['on_true_lock'].all())}")
    print(f"P2 判定：{'PASS' if not p2_violations else 'FAIL ' + str(p2_violations)}")

    print()
    print("=" * 100)
    print("P4：分層 N（trailing q95 錨定 12%）——新判準：常態 fold 顯著改善 + 劇烈位移 fold 單獨列出")
    print("=" * 100)
    p4_df = pd.DataFrame(p4_rows)
    normal = p4_df[~p4_df["regime_shift"]]
    shifted = p4_df[p4_df["regime_shift"]]

    print(f"(a) 常態 fold（test_q95 <= 1.25×train_q95）：{len(normal)}/{len(p4_df)}")
    for symbol in SYMBOLS:
        sub = normal[normal["symbol"] == symbol]
        if sub.empty:
            continue
        print(
            f"    [{symbol}] n={len(sub)}  N 範圍 {sub['n_strat'].min():.2f}~{sub['n_strat'].max():.2f}  "
            f">=15%佔比：分層 q95 mean={sub['frac_ge15_strat'].mean():.2%}  "
            f"vs 固定 N=3 mean={sub['frac_ge15_n3'].mean():.2%}"
        )
    print()
    print(f"(b) ★劇烈 regime 位移 fold（trailing 校正的已知殘留風險，記錄不消除）★：{len(shifted)}/{len(p4_df)}")
    if shifted.empty:
        print("    （本批無）")
    else:
        print(shifted.to_string(index=False, formatters={
            "n_strat": "{:.2f}".format, "q95_shift": "{:.2f}x".format,
            "frac_ge15_strat": "{:.1%}".format, "frac_ge15_n3": "{:.1%}".format}))
    print()
    print("每 fold 明細：")
    print(p4_df.to_string(index=False, formatters={
        "n_strat": "{:.2f}".format, "q95_shift": "{:.2f}x".format,
        "frac_ge15_strat": "{:.1%}".format, "frac_ge15_n3": "{:.1%}".format}))


if __name__ == "__main__":
    main()
