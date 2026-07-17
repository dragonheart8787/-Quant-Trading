"""Phase 4 ML 訊號層第一輪判定跑批：LR+交互項 vs v2（E2 配對對照）。

★ 事前註冊規格（2026-07-15 使用者核可；本檔為一次定案跑批，不做績效導向
  重跑或調整——正確性修 bug 不限次數、看過結果後改特徵/參數 = 明示新一輪）★

判定基準：E2 事件驅動引擎（fill=next_open、fee=5bps、RiskManager 全預設、
sizing_mode="leverage_cap" 現行正式基準）下的 v2；ML 只替換訊號層，
sizing/風控/成本/成交全同 → Δ(ML−v2) 只反映訊號品質（向量化空頭失真教訓）。

特徵：ai/ml_train.py 預先註冊 9 欄（基礎 5＋v2 規則 AND 結構逐條映射的交互 4，
零裁量零試跑）。模型：L2 邏輯回歸固定 C=1.0/lbfgs（無調參、無早停）。
label：k=1 forward return 方向（D1）。訓練：expanding 全歷史 + purge k 根（D2）。
τ：訓練集尾端 20% 內部驗證（D3）。

★ 判準（事前註冊；預設立場 = v2 勝）★
  比較軸 = 逐 fold 配對 Δ = annual(E2_ML) − annual(E2_v2)
  (a) 跨窗方向一致：C/D/A 三零重疊窗 Δmean 全 > 0；乾淨基準（與窗A重疊，
      僅定錨展示不入判準）無強反例。
  (b) 量級：每窗 Δmean ≥ 1×SE（std(Δ)/√15）；三窗合併 45 fold 配對 t ≥ 2
      （D4）且逐 fold 勝率 ≥ 60%。
  (c) 非單 fold 主導：每窗剔除 Δ 最大 fold 後 Δmean 仍 > 0。
  (d) 歸因檢查：附曝險/換手拆解（多空 bar 數、成交筆數）——Δ 若主要來自
      換手成本或曝險方向分佈而非選 bar 品質，如實標注。
  另依 D2：n_train < 2000 的 fold 標 thin，額外回報「排除 thin fold 後」
  (a)(b)(c) 是否改變。
  檢定力邊界（事前接受）：本設計只能偵測「大且一致」的改善（約 6~15pp
  年化量級）；小於此的真實改善與雜訊不可區分，依判準判「不值得」。

執行：
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ml_signal.py --stage smoke
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ml_signal.py --stage e2
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from ai.ml_train import (
    LABEL_K,
    THIN_TRAIN_FLOOR,
    build_feature_matrix,
    fit_fold,
    make_label,
)
from backtest.event_engine import EventBacktestResult, run_event_backtest
from backtest.ic import forward_return
from backtest.report import build_report
from indicators.technical import atr as compute_atr
from risk.manager import RiskManager
from run_atr_calibration import CLEAN_BASELINE, FEE_BPS, WINDOWS, _load, _v2_strategy

FULL_RANGE = ("2023-08-14 00:00", "2026-07-04 00:00")  # 凍結 DB 全區間（25300 根）


@dataclass
class FoldRow:
    """單 fold 配對結果與診斷。"""

    fold: int
    a_v2: float
    a_ml: float
    n_train: int
    thin: bool
    tau: float
    v2_long: int
    v2_short: int
    v2_fills: int
    ml_long: int
    ml_short: int
    ml_fills: int

    @property
    def delta(self) -> float:
        return self.a_ml - self.a_v2


def _bars(ev: EventBacktestResult) -> tuple[int, int]:
    qty = ev.position_qty.to_numpy()
    return int((qty > 0).sum()), int((qty < 0).sum())


def _run_range(
    name: str,
    start: str,
    end: str,
    n_splits: int,
    full_ts: pd.Index,
    features: pd.DataFrame,
    label: pd.Series,
    fwd: pd.Series,
) -> list[FoldRow]:
    data = _load(start, end)
    offset = int(full_ts.get_indexer([data["ts"].iloc[0]])[0])
    if offset < 0 or not (full_ts[offset : offset + len(data)] == pd.Index(data["ts"])).all():
        raise RuntimeError(f"[{name}] 窗資料與全域凍結資料對不上（ts 映射失敗）")

    v2_signal = _v2_strategy().generate_signals(data)
    win_atr = compute_atr(data[["high", "low", "close"]], window=14)

    print(f"  [{name}] {start} → {end}（{len(data)} 根，{n_splits}-fold，全域偏移 {offset}）")
    hdr = (f"    {'fold':>4} | {'E2_v2':>8} {'E2_ML':>8} {'Δ(ML-v2)':>9} | "
           f"{'n_train':>7} {'thin':>4} {'τ':>5} | "
           f"{'v2多/空bar':>10} {'成交':>4} | {'ML多/空bar':>10} {'成交':>4}")
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))

    rows: list[FoldRow] = []
    splits = TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data)))
    for k, (_, test_idx) in enumerate(splits, start=1):
        td = data.iloc[test_idx].reset_index(drop=True)
        sig_v2 = v2_signal.iloc[test_idx].reset_index(drop=True)
        atr_s = win_atr.iloc[test_idx].reset_index(drop=True)

        g0 = offset + int(test_idx[0])
        fit = fit_fold(features, label, fwd, test_start_pos=g0)
        sig_ml = (
            fit.strategy.predict_signal(features.iloc[g0 : g0 + len(test_idx)])
            .reset_index(drop=True)
        )

        def _e2(sig: pd.Series) -> EventBacktestResult:
            # slippage_bps=0.0 pin：保留本輪（2026-07-15 結案）數字逐位可
            # 重現性，不隨新預設值（2.0，同日稍後基準切換拍板）變動。
            return run_event_backtest(
                td, sig, fee_bps=FEE_BPS, fill_mode="next_open", slippage_bps=0.0,
                risk=RiskManager(equity=1.0), atr_series=atr_s,
                sizing_mode="leverage_cap",
            )

        ev_v2, ev_ml = _e2(sig_v2), _e2(sig_ml)
        a_v2 = build_report(ev_v2.to_backtest_result(td["close"])).annual_return
        a_ml = build_report(ev_ml.to_backtest_result(td["close"])).annual_return
        v2_l, v2_s = _bars(ev_v2)
        ml_l, ml_s = _bars(ev_ml)
        row = FoldRow(
            fold=k, a_v2=a_v2, a_ml=a_ml,
            n_train=fit.n_train, thin=fit.thin, tau=fit.tau,
            v2_long=v2_l, v2_short=v2_s, v2_fills=len(ev_v2.fills),
            ml_long=ml_l, ml_short=ml_s, ml_fills=len(ev_ml.fills),
        )
        rows.append(row)
        print(
            f"    {k:>4} | {a_v2:>+8.1%} {a_ml:>+8.1%} {row.delta:>+9.1%} | "
            f"{row.n_train:>7d} {'★' if row.thin else '':>4} {row.tau:>5.2f} | "
            f"{v2_l:>4d}/{v2_s:<5d} {row.v2_fills:>4d} | {ml_l:>4d}/{ml_s:<5d} {row.ml_fills:>4d}"
        )

    d = np.array([r.delta for r in rows])
    print(
        f"    mean: v2 {np.mean([r.a_v2 for r in rows]):+.1%} / "
        f"ML {np.mean([r.a_ml for r in rows]):+.1%}   "
        f"Δmean {d.mean():+.1%}  std(Δ) {d.std(ddof=1):.1%}  "
        f"SE {d.std(ddof=1) / np.sqrt(len(d)):.1%}  勝率 {(d > 0).mean():.0%}"
    )
    print()
    return rows


def _criteria(per_window: dict[str, list[FoldRow]], tag: str) -> None:
    """判準 (a)(b)(c) 逐條評估（僅 C/D/A 入判準）。"""
    print(f"  ── 判準評估（{tag}）──")
    means, oks_b_window, oks_c = {}, {}, {}
    all_d: list[float] = []
    for win, rows in per_window.items():
        d = np.array([r.delta for r in rows])
        if len(d) < 3:
            print(f"    窗{win}: fold 數不足（{len(d)}），無法評估")
            means[win] = float("nan")
            continue
        mean, std = d.mean(), d.std(ddof=1)
        se = std / np.sqrt(len(d))
        loo = np.delete(d, d.argmax()).mean()  # 剔除對 ML 最有利的 fold
        means[win] = mean
        oks_b_window[win] = mean >= se
        oks_c[win] = loo > 0
        all_d.extend(d.tolist())
        print(
            f"    窗{win}: Δmean {mean:+.1%}  SE {se:.1%}  t {mean / se if se > 0 else float('nan'):+.2f}  "
            f"剔最大fold後 {loo:+.1%}  勝率 {(d > 0).mean():.0%}  (n={len(d)})"
        )
    d_all = np.array(all_d)
    pooled_t = d_all.mean() / (d_all.std(ddof=1) / np.sqrt(len(d_all))) if len(d_all) > 1 else float("nan")
    win_rate = (d_all > 0).mean() if len(d_all) else float("nan")

    ok_a = all(np.isfinite(m) and m > 0 for m in means.values())
    ok_b = all(oks_b_window.values()) and pooled_t >= 2.0 and win_rate >= 0.60
    ok_c = all(oks_c.values())
    print(f"    合併: n={len(d_all)}  Δmean {d_all.mean():+.1%}  t {pooled_t:+.2f}  勝率 {win_rate:.0%}")
    print(f"    (a) 三窗 Δmean 全 > 0: {'PASS' if ok_a else 'FAIL'}  "
          f"({', '.join(f'{w} {m:+.1%}' for w, m in means.items())})")
    print(f"    (b) 每窗 mean≥SE 且合併 t≥2 且勝率≥60%: {'PASS' if ok_b else 'FAIL'}")
    print(f"    (c) 每窗剔除最有利 fold 後仍 > 0: {'PASS' if ok_c else 'FAIL'}")
    verdict = ok_a and ok_b and ok_c
    print(f"    ⇒ {tag}判定: {'ML 勝出（仍需 (d) 歸因檢查佐證）' if verdict else 'v2 續任（預設立場）'}")
    print()


def stage_e2() -> None:
    print("=" * 112)
    print("★ Phase 4 第一輪判定跑批：E2 配對 Δ(ML−v2)；一次定案，不做績效導向重跑 ★")
    print("  E2 = next_open / fee 5bps / RiskManager 預設 / leverage_cap；ML 只換訊號層")
    print("=" * 112)

    print("  載入凍結全歷史並建特徵矩陣（一次）…")
    full = _load(*FULL_RANGE)
    features = build_feature_matrix(full)
    label = make_label(full["close"])
    fwd = forward_return(full["close"], LABEL_K)
    full_ts = pd.Index(full["ts"])
    print(f"  全歷史 {len(full)} 根；特徵可用列 {int(features.notna().all(axis=1).sum())}\n")

    start, end, n_splits = CLEAN_BASELINE
    _run_range("乾淨基準（定錨展示，與窗A重疊、不入判準）", start, end, n_splits,
               full_ts, features, label, fwd)

    per_window: dict[str, list[FoldRow]] = {}
    for name, (w_start, w_end) in WINDOWS.items():
        per_window[name] = _run_range(f"窗{name}", w_start, w_end, 15,
                                      full_ts, features, label, fwd)

    _criteria(per_window, tag="全 fold ")
    thin_excluded = {w: [r for r in rows if not r.thin] for w, rows in per_window.items()}
    n_thin = sum(len(rows) - len(thin_excluded[w]) for w, rows in per_window.items())
    print(f"  thin fold（n_train < {THIN_TRAIN_FLOOR}）共 {n_thin} 個，排除後複驗（D2 決策）：")
    _criteria(thin_excluded, tag="排除 thin ")

    print("  判準 (d) 歸因參考（彙總；判讀寫在交付報告）：")
    for win, rows in per_window.items():
        print(
            f"    窗{win}: v2 多/空bar {sum(r.v2_long for r in rows)}/{sum(r.v2_short for r in rows)} "
            f"成交 {sum(r.v2_fills for r in rows)}  |  "
            f"ML 多/空bar {sum(r.ml_long for r in rows)}/{sum(r.ml_short for r in rows)} "
            f"成交 {sum(r.ml_fills for r in rows)}  |  "
            f"τ 分佈 {sorted(set(r.tau for r in rows))}"
        )


def stage_smoke() -> None:
    """管線煙霧測試：只驗「跑得動、訊號域正確、E2 可吃」，數字不作任何判讀。

    為省時在乾淨基準窗內就地建特徵（僅 smoke 用近似；判定跑批一律用全歷史）。
    """
    print("=" * 80)
    print("smoke：管線機制驗證（數字不判讀、不入任何結論）")
    print("=" * 80)
    start, end, n_splits = CLEAN_BASELINE
    data = _load(start, end)
    features = build_feature_matrix(data)
    label = make_label(data["close"])
    fwd = forward_return(data["close"], LABEL_K)

    _, test_idx = list(TimeSeriesSplit(n_splits=n_splits).split(np.arange(len(data))))[-1]
    g0 = int(test_idx[0])
    fit = fit_fold(features, label, fwd, test_start_pos=g0)
    sig = fit.strategy.predict_signal(features.iloc[g0 : g0 + len(test_idx)])
    dist = sig.value_counts().to_dict()
    print(f"  fold(last): n_train={fit.n_train} thin={fit.thin} τ={fit.tau} "
          f"degenerate={fit.degenerate}")
    print(f"  訊號分佈: {dist}（域 ⊆ {{-1,0,1}}: {set(sig.unique()) <= {-1, 0, 1}}）")

    td = data.iloc[test_idx].reset_index(drop=True)
    atr_s = compute_atr(data[["high", "low", "close"]], 14).iloc[test_idx].reset_index(drop=True)
    ev = run_event_backtest(
        td, sig.reset_index(drop=True), fee_bps=FEE_BPS, fill_mode="next_open",
        slippage_bps=0.0,  # pin 0bp：smoke 僅驗機制，與判定跑批基準無關
        risk=RiskManager(equity=1.0), atr_series=atr_s, sizing_mode="leverage_cap",
    )
    print(f"  E2 可執行: fills={len(ev.fills)} 強平={len(ev.liquidation_bars)} "
          f"權益尾值={ev.equity_curve.iloc[-1]:.4f}")
    print("  smoke PASS（機制層）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("smoke", "e2"), default="smoke")
    args = parser.parse_args()
    if args.stage == "smoke":
        stage_smoke()
    else:
        stage_e2()


if __name__ == "__main__":
    main()
