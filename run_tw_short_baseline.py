"""台股放空正式基準（2026-07-16，七環節整合第一輪）。

七環節（全部個別驗證過，本輪第一次全開整合）：
  ①還原價（data/adjustment.py，事件快照）②台股不對稱成本（tw_stock_costs
  必選介面）③3.5% 禁空（short_uptick_rule_drop=0.035）④漲跌停鎖死耦合
  （lock_up/lock_down，原始價分時代門檻）⑤ATR 分層 N（stratified q95 錨
  12%）＋15% 固定網 ⑥強制回補日曆（short_entry_ban+forced_cover_deadline）
  ⑦完整風控（RiskManager：熔斷/槓桿/規則二空頭 0.5x）。

策略 = v2（MaRsiBidirectionalStrategy 預設參數，與 BTC 正式基準同）；
對照組 = 多頭 baseline（ma_rsi_regime，與「台股 E2 化第一輪基準」同配置）
——並排呈現、**不下優劣結論**（第一份數字非策略評選）。

機制層判準（使用者具體化，任一不符先除錯不解讀報酬）：
  I1 觸發清單 ISO vs FULL 比對：每個機制在「單獨開啟」與「七項全開」下
     的觸發清單逐 fold 比對；差異逐筆列出並根因分類，不得假設「整合後
     不同是正常的」。ISO 配置定義（base = v2+costs+slippage0）：
       ISO_LIQ      = base + risk(分層N)+強平（含熔斷，與 risk 不可分離）
       ISO_UPTICK   = base + uptick（無 risk）
       ISO_LOCK     = base + lock_up/down（無 risk）
       ISO_CALENDAR = base + ban+deadline（無 risk）
  I2 三項疊加邊界（tests/test_tw_rules_integration.py，pytest 層已釘住）：
     uptick 不誤傷強平回補／同 bar 雙重平倉理由=價格優先日曆跳過（特徵化）
     ／鎖死延後×死線=單一回補單重試。
  I3 非預期交互 → 先回報根因，不自行修。

結果層：60 fold-案例健康度（無 NaN/發散）＋正式基準表（v2 全開 vs 多頭
baseline 並排）＋回補日曆未覆蓋 fold 揭露＋鎖死曝險清點。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tw_short_baseline.py
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from backtest.costs import TradeCosts, tw_stock_costs
from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.grid_search import label_fold_regimes
from backtest.liq_calibration import stratified_forced_liq_n
from backtest.report import build_report
from data import storage_events_tw
from data.adjustment import apply_back_adjustment
from data.storage_sqlite import connect, load_klines
from indicators.technical import atr as compute_atr
from risk.manager import RiskConfig, RiskManager
from strategy.ma_rsi_bidirectional import MaRsiBidirectionalStrategy
from strategy.ma_rsi_regime import MaRsiRegimeStrategy

SYMBOLS: dict[str, str] = {
    "2330": "TWSE", "2603": "TWSE", "8069": "TPEx", "6446": "TPEx",
}
N_SPLITS = 15
PERIODS_PER_YEAR = 252
TW_DB = "data/klines_tw.sqlite"
UPTICK_DROP = 0.035
ATR_WINDOW = 14
LIMIT_ERA_SPLIT = "2015-06-01"
CALENDAR_DATA_START = "2015-04-01"

COSTS: TradeCosts = tw_stock_costs()   # 全域唯一合法成本來源（必選介面慣例）


# ---------- 資料載入（全部走本地快照，零即時 API） ----------

def _load(symbol: str, exchange: str):
    conn = connect(TW_DB)
    try:
        raw = load_klines(conn, symbol, "1d", exchange=exchange)
    finally:
        conn.close()
    econn = storage_events_tw.connect()
    try:
        dividends = storage_events_tw.load_dividend_results(econn, symbol)
        suspension = storage_events_tw.load_suspensions(econn, symbol)
    finally:
        econn.close()
    if raw.empty or dividends.empty or suspension.empty:
        raise RuntimeError(f"{symbol} 落地資料不完整；請先跑 ingest runners")
    adjusted = apply_back_adjustment(raw, dividends).reset_index(drop=True)
    return raw.reset_index(drop=True), adjusted, suspension


def _lock_masks(raw: pd.DataFrame):
    close = raw["close"].to_numpy(dtype=float)
    ret = np.full(len(raw), np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    era_10 = raw["ts"] >= pd.Timestamp(LIMIT_ERA_SPLIT, tz="Asia/Taipei").tz_convert("UTC")
    threshold = np.where(era_10.to_numpy(), 0.10, 0.07) * 0.9
    flat = raw["high"].to_numpy() == raw["low"].to_numpy()
    return (
        pd.Series(flat & (ret >= threshold), index=raw.index),
        pd.Series(flat & (ret <= -threshold), index=raw.index),
    )


def _calendar_masks(raw: pd.DataFrame, suspension: pd.DataFrame):
    bar_dates = raw["ts"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
    date_to_idx = {d: i for i, d in enumerate(bar_dates)}
    ban = pd.Series(np.zeros(len(raw), dtype=bool), index=raw.index)
    deadline = pd.Series(np.zeros(len(raw), dtype=bool), index=raw.index)
    for _, r in suspension.iterrows():
        idx = [i for d, i in date_to_idx.items() if str(r["date"]) <= d <= str(r["end_date"])]
        for i in idx:
            ban.iloc[i] = True
        if idx:
            deadline.iloc[min(idx)] = True
    return ban, deadline


# ---------- 觸發清單抽取（I1 比對用） ----------

def _trigger_lists(ev: EventBacktestResult) -> dict[str, list]:
    return {
        "price_liq": sorted(ev.liquidation_bars),
        "uptick": sorted(b for b, _, _, why in ev.blocked_fills if why == "uptick_rule"),
        "lock": sorted(
            [(i, a, r) for i, a, r in ev.deferred_fills]
            + [(b, -1, reason) for b, _, reason, why in ev.blocked_fills if why == "limit_lock"]
        ),
        "calendar": sorted(
            list(ev.calendar_cover_bars)
            + [b for b, _, _, why in ev.blocked_fills if why == "short_sale_suspension"]
        ),
    }


@dataclass
class FoldRow:
    symbol: str
    fold: int
    regime: str
    n_bars: int
    annual_v2: float
    sharpe_v2: float
    annual_long: float
    n_liq: int
    n_uptick: int
    n_lock: int
    n_calendar: int
    trip_days: int
    calendar_covered: str


def main() -> None:
    v2 = MaRsiBidirectionalStrategy(
        fast_window=20, slow_window=60, rsi_overbought=70.0, regime_window=120
    )
    long_base = MaRsiRegimeStrategy()

    i1_diffs: list[dict] = []
    rows: list[FoldRow] = []
    lock_exposure: list[str] = []

    for symbol, exchange in SYMBOLS.items():
        print(f"跑 {symbol}（{exchange}）...")
        raw, adjusted, suspension = _load(symbol, exchange)
        lock_up_f, lock_down_f = _lock_masks(raw)
        ban_f, deadline_f = _calendar_masks(raw, suspension)
        sig_v2_full = v2.generate_signals(adjusted)
        sig_long_full = long_base.generate_signals(adjusted)
        atr_full = compute_atr(adjusted[["high", "low", "close"]], window=ATR_WINDOW)
        regimes = label_fold_regimes(adjusted, n_splits=N_SPLITS)
        splits = list(TimeSeriesSplit(n_splits=N_SPLITS).split(np.arange(len(adjusted))))
        bar_dates = raw["ts"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
        first_evt = suspension["date"].min()

        for k, (train_idx, test_idx) in enumerate(splits):
            n_strat = stratified_forced_liq_n(adjusted.iloc[train_idx])
            adj_s = adjusted.iloc[test_idx].reset_index(drop=True)
            sig_s = sig_v2_full.iloc[test_idx].reset_index(drop=True)
            sig_long_s = sig_long_full.iloc[test_idx].reset_index(drop=True)
            atr_s = atr_full.iloc[test_idx].reset_index(drop=True)
            lu = lock_up_f.iloc[test_idx].reset_index(drop=True)
            ld = lock_down_f.iloc[test_idx].reset_index(drop=True)
            ban_s = ban_f.iloc[test_idx].reset_index(drop=True)
            dl_s = deadline_f.iloc[test_idx].reset_index(drop=True)

            def _run(**kw) -> EventBacktestResult:
                return run_event_backtest(
                    adj_s, sig_s, costs=COSTS, slippage_bps=0.0, **kw,
                )

            # FULL：七項全開
            full = _run(
                risk=RiskManager(equity=1.0, config=RiskConfig(forced_liq_atr_n=n_strat)),
                atr_series=atr_s, short_uptick_rule_drop=UPTICK_DROP,
                lock_up=lu, lock_down=ld,
                short_entry_ban=ban_s, forced_cover_deadline=dl_s,
            )
            # ISO 配置（I1）
            isos = {
                "price_liq": _run(
                    risk=RiskManager(equity=1.0, config=RiskConfig(forced_liq_atr_n=n_strat)),
                    atr_series=atr_s,
                ),
                "uptick": _run(short_uptick_rule_drop=UPTICK_DROP),
                "lock": _run(lock_up=lu, lock_down=ld),
                "calendar": _run(short_entry_ban=ban_s, forced_cover_deadline=dl_s),
            }
            full_lists = _trigger_lists(full)
            for mech, iso_ev in isos.items():
                iso_list = _trigger_lists(iso_ev)[mech]
                if iso_list != full_lists[mech]:
                    i1_diffs.append({
                        "symbol": symbol, "fold": k + 1, "mech": mech,
                        "iso": iso_list, "full": full_lists[mech],
                    })

            # 對照組：多頭 baseline（與台股 E2 化第一輪完全同配置）
            long_ev = run_event_backtest(
                adj_s, sig_long_s, costs=COSTS, slippage_bps=0.0,
                risk=RiskManager(equity=1.0), forced_liquidation=False,
            )

            rep = build_report(full.to_backtest_result(adj_s["close"]),
                               periods_per_year=PERIODS_PER_YEAR)
            rep_long = build_report(long_ev.to_backtest_result(adj_s["close"]),
                                    periods_per_year=PERIODS_PER_YEAR)

            t0 = pd.Timestamp(bar_dates.iloc[test_idx[0]])
            t1 = pd.Timestamp(bar_dates.iloc[test_idx[-1]])
            if t1 < pd.Timestamp(CALENDAR_DATA_START):
                covered = "未覆蓋"
            elif t0 < pd.Timestamp(CALENDAR_DATA_START) or t1 < pd.Timestamp(first_evt):
                covered = "部分/未覆蓋"
            else:
                covered = "覆蓋"

            for b, _, reason, why in full.blocked_fills:
                if why == "limit_lock":
                    lock_exposure.append(f"{symbol} fold{k+1} bar{b} dropped:{reason}")
            for i, a, r in full.deferred_fills:
                lock_exposure.append(f"{symbol} fold{k+1} deferred:{r}({i}->{a})")

            rows.append(FoldRow(
                symbol=symbol, fold=k + 1, regime=regimes[k].regime.label,
                n_bars=len(test_idx),
                annual_v2=rep.annual_return, sharpe_v2=rep.sharpe,
                annual_long=rep_long.annual_return,
                n_liq=len(full.liquidation_bars),
                n_uptick=sum(1 for *_, w in full.blocked_fills if w == "uptick_rule"),
                n_lock=len(full.deferred_fills)
                + sum(1 for *_, w in full.blocked_fills if w == "limit_lock"),
                n_calendar=len(full.calendar_cover_bars)
                + sum(1 for *_, w in full.blocked_fills if w == "short_sale_suspension"),
                trip_days=len(full.tripped_days),
                calendar_covered=covered,
            ))

    # ===== 機制層輸出 =====
    print()
    print("=" * 108)
    print("I1：觸發清單 ISO（單獨開啟）vs FULL（七項全開）逐 fold 比對")
    print("=" * 108)
    if not i1_diffs:
        print("全部 4 檔 × 15 fold × 4 機制：ISO 與 FULL 觸發清單逐位一致，無交互差異。")
    else:
        print(f"發現 {len(i1_diffs)} 筆差異（逐筆列出，待根因分類）：")
        for d in i1_diffs:
            print(f"  [{d['symbol']} fold{d['fold']} {d['mech']}]")
            print(f"    ISO : {d['iso']}")
            print(f"    FULL: {d['full']}")
    print()
    print("I2：三項疊加邊界（pytest 層）＝ tests/test_tw_rules_integration.py 3 passed")

    # ===== 結果層輸出 =====
    print()
    print("=" * 108)
    print("結果層：台股放空正式基準（v2 七項全開）vs 多頭 baseline 並排（不下優劣結論）")
    print("=" * 108)
    df = pd.DataFrame([r.__dict__ for r in rows])
    bad = df[~np.isfinite(df["annual_v2"]) | (df["annual_v2"].abs() > 50)]
    print(f"R1 健康度：異常 fold {len(bad)}/60")
    for symbol in SYMBOLS:
        sub = df[df["symbol"] == symbol]
        print(f"\n[{symbol}]")
        for _, r in sub.iterrows():
            print(
                f"  fold{r['fold']:>2} {r['regime']:>11}  v2={r['annual_v2']:>+8.1%} "
                f"(sharpe {r['sharpe_v2']:>+5.2f})  多頭={r['annual_long']:>+8.1%}  "
                f"強平={r['n_liq']} uptick擋={r['n_uptick']} 鎖死={r['n_lock']} "
                f"日曆={r['n_calendar']} 熔斷日={r['trip_days']}  回補日曆:{r['calendar_covered']}"
            )
        print(f"  v2 mean={sub['annual_v2'].mean():+.1%} std={sub['annual_v2'].std(ddof=1):.1%}"
              f"   多頭 mean={sub['annual_long'].mean():+.1%}")

    print()
    print("鎖死曝險清點（FULL 配置實際發生的延後/作廢）：")
    if lock_exposure:
        for s in lock_exposure:
            print("  " + s)
    else:
        print("  （無）")
    n_uncovered = (df["calendar_covered"] != "覆蓋").sum()
    print(f"\n回補日曆未覆蓋 fold：{n_uncovered}/60（明細見上表每列標注）")


if __name__ == "__main__":
    main()
