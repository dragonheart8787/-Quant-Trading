# CLAUDE.md — 量化交易系統開發指引

> 這份文件是給 Claude Code 在此 repo 中工作時的長期參考。修改架構或慣例時，請同步更新本文件。

## 專案是什麼

一套可實作、可維護、可觀測的量化交易系統骨架，支援台股、美股、加密貨幣三個市場（並行開發，無優先順序）。核心原則：**先把工程骨架做穩（資料、回測、風控、執行、監控、部署），再把 ML/RL 放進訊號層或倉位控制層**。不要一開始就讓模型直接全權下單。

分層架構（嚴格遵守，不要跨層耦合）：

```
資料層 → 特徵層 → 訊號層 → 回測層 → 風控層 → 執行層 → 監控層 → 部署層
```

## 專案結構

```
quant-stack/
├─ config/settings.py          # 環境變數、API keys、DSN 統一讀取點
├─ data/
│  ├─ providers/                # 每個資料來源一個檔案：binance.py / finmind.py（台股 FinMind：
│  │                            #   籌碼 4 dataset + fetch_price 日K，Phase 3；官方 TWSE/TPEx
│  │                            #   OpenAPI 的 twse.py 與 us_equity.py 仍規劃中）
│  ├─ storage_sqlite.py         # 研究用本地 K 線 DB（WAL；klines.sqlite[BTCUSDT]已凍結；
│  │                            #   2026-07-15 新增 ETHUSDT 共存同一 DB（PRIMARY KEY 含
│  │                            #   symbol 天然支援多 symbol，未建新 DB，BTC 段未受影響）；
│  │                            #   台股日K 落 klines_tw.sqlite，同 schema、connect(path) 分檔；
│  │                            #   ★收盤價為原始成交價、未除權息還原（65/65事件逐位驗證，
│  │                            #   2026-07-16），還原見 data/adjustment.py，原始檔本身不動★）
│  ├─ adjustment.py             # 台股除權息還原價（2026-07-16 新增，方案C=動態計算為預設）：
│  │                            #   apply_back_adjustment(klines, dividends) 用
│  │                            #   TaiwanStockDividendResult 的 after_price/before_price
│  │                            #   （非 reference_price，純配股事件該欄位不可靠）算累積調整
│  │                            #   因子，錨點=最新bar=1.0；只調 OHLC(+vwap)，volume/turnover
│  │                            #   不動；漲跌停判定等執行語意一律仍用原始價，不用還原價
│  │                            #   ★但書：若規則在真實世界的定義本身建立在除權息調整後
│  │                            #   價格上（如 3.5% 禁空規則的跌幅基準=除權息參考價），
│  │                            #   該規則用還原價才是忠實建模，見 adjustment.py docstring★
│  ├─ storage_adjusted.py       # 還原價凍結快照（方案B，選配；獨立檔 klines_tw_adjusted.sqlite，
│  │                            #   比照 storage_chips.py 隔離理由）：只在需要固定錨點做 fold
│  │                            #   對照時才手動觸發，預設路徑仍是 adjustment.py 動態計算
│  ├─ storage_chips.py          # 台股籌碼 DB（chips_tw.sqlite 獨立檔案，Phase 3；
│  │                            #   與 K 線分檔隔離，合併只在 features.py 做；
│  │                            #   ★2026-07-16 A1 修復：nullable Int64 落地 blob 問題
│  │                            #   （寫入端 .item() 轉原生型別＋65k 列歷史遷移完成）★）
│  ├─ storage_events_tw.py      # 台股公司行動事件快照（events_tw.sqlite 獨立檔，2026-07-16
│  │                            #   A4 技術債輪）：除權息結果+停券日曆落地，隔離 FinMind
│  │                            #   外部依賴；run_ingest_tw_events.py 刷新；台股分析 runner
│  │                            #   一律讀快照不即時打 API（凍結校驗和見 HANDOFF）
│  ├─ storage_postgres.py       # 交易紀錄／報表用 DB（規劃中）
│  └─ cleaning.py               # 統一轉成 canonical schema（K 線 + 台股籌碼兩套）；
│                               #   _sanitize/validate_canonical 2026-07-16 起丟棄／拋錯
│                               #   非正值 O/H/L/C（6446 藥華藥 2016-12-05 全零列事故發現）
├─ indicators/
│  ├─ technical.py              # 向量化技術指標（rolling/ewm，禁止 apply(axis=1)）
│  │                            # 已實作：SMA、Wilder RSI、ATR（因果，禁未來函數）
│  └─ features.py               # 籌碼→價格bar 排他式 as-of 對齊（Phase 3，已實作；
│                               #   防未來函數，見 align_chips_to_bars；不算 alpha 指標）
├─ strategy/
│  ├─ base.py                   # BaseStrategy 抽象層，所有策略必須繼承
│  ├─ ma_rsi.py                 # 規則策略 baseline（long-only，不要動）
│  ├─ ma_rsi_regime.py          # MA/RSI + 因果 regime filter（不要動）
│  ├─ ma_rsi_chip.py            # MA/RSI + 籌碼閘門（Phase 3 訊號層驗證，long-only；
│  │                            #   chip_window=1 當日 foreign_net>0 / N>1 N日淨買超平滑；
│  │                            #   ★chip-gating 2026-07-12 結案：foreign_net 無資訊
│  │                            #   （IC 診斷），程式碼保留、N 不再校正★）
│  └─ ml_signal.py              # ML 訊號包裝（Phase 4 第一輪已實作：已訓練 scaler/LR/τ →
│                               #   {-1,0,+1}，NaN/退化安全空手；★第一輪 2026-07-15 正式
│                               #   結案：v2 續任、不開第二輪，重啟備忘=低頻 label 優先，
│                               #   見 HANDOFF「Phase 4 ML 訊號層第一輪」節★）
├─ backtest/
│  ├─ vector_engine.py          # 向量化回測（訊號必須 shift(1) 防未來函數，不要動）
│  ├─ costs.py                  # 買賣分離交易成本（TradeCosts；台股手續費+證交稅 vs
│  │                            #   加密貨幣對稱 5bps；costs=None 走舊 fee_bps 路徑逐位不變）
│  ├─ event_engine.py           # 事件驅動回測（MarketEvent→SignalEvent→OrderEvent→FillEvent；
│  │                            #   sizing_mode="risk_per_trade" 選配規則一空頭倉位，
│  │                            #   預設 leverage_cap 逐位不變，2026-07-13；★slippage_bps
│  │                            #   預設值 2.0，2026-07-15 使用者拍板為新正式基準（非逐位
│  │                            #   不變！），舊呼叫需明確 pin slippage_bps=0.0 才維持
│  │                            #   歷史行為，見 HANDOFF「滑價敏感度」節★；costs（TradeCosts
│  │                            #   買賣分離費率）2026-07-16 新增，預設 None 逐位不變（沿用
│  │                            #   fee_bps 對稱路徑）；台股專屬 runner 必選傳入，見
│  │                            #   run_e2_tw_baseline.py 與 HANDOFF「台股 E2 化第一輪基準」節；
│  │                            #   熔斷語意在日頻下的塌縮、漲跌停成交假設兩項已知限制的
│  │                            #   簡短提醒見本檔 docstring「成交假設」節，完整量化數字見
│  │                            #   HANDOFF 同節；★第四次解鎖（2026-07-16 台股放空第一輪）：
│  │                            #   short_uptick_rule_drop（3.5%禁空，成交時點檢查、還原價
│  │                            #   基底）＋ lock_up/lock_down（漲跌停全日鎖死，方向感知：
│  │                            #   風險縮減單延後重試、新倉單作廢），預設 None 逐位不變；
│  │                            #   新診斷欄 blocked_fills/deferred_fills★；★第五次解鎖
│  │                            #   （2026-07-16 item 3 強制回補日曆）：short_entry_ban
│  │                            #   （停券窗禁新空）＋ forced_cover_deadline（最後回補日
│  │                            #   強制平倉，reason="calendar_forced_cover"、
│  │                            #   calendar_cover_bars 與價格驅動 liquidation_bars 歸因
│  │                            #   分離；t+1 日曆旗標=事前公告制非未來函數，分類標準見
│  │                            #   AGENTS.md「使用未來已公告資訊的合法性分類」節）★）
│  ├─ ic.py                     # IC 診斷核心（Phase 3；Spearman 主指標、fold 自足切分、
│  │                            #   forward_return 尾端 NaN 不回繞；純分析不碰回測）
│  ├─ liq_calibration.py        # ATR 強平防線校正機制層量測（觸發語意與 overlay/E2 一致；
│  │                            #   trigger_source close/high，high 觸價輪 2026-07-14 結案：
│  │                            #   接受 close-only 簡化）；stratified_forced_liq_n()
│  │                            #   台股 ATR 分層 N（2026-07-16，trailing q95 錨定 12%，
│  │                            #   train 側因果校準；median 錨定第一版 FAIL 教訓見
│  │                            #   AGENTS.md「校正統計量必須對準機制要守的分位」節）
│  └─ report.py                 # 績效指標：年化、Sharpe、MDD、勝率
├─ risk/manager.py              # 風控核心（已擴充支援空頭，見下方說明）
├─ broker/
│  ├─ paper.py                  # 模擬成交（Position.quantity 正負均支援）
│  ├─ live_tw.py                # Shioaji / Fubon Neo
│  ├─ live_ibkr.py              # 美股
│  └─ live_binance.py           # 加密
├─ ai/
│  ├─ ml_train.py               # Phase 4 第一輪已實作：預先註冊 9 欄特徵矩陣＋逐 fold
│  │                            #   LR 訓練（expanding+purge、τ 內部驗證；無調參無早停）；
│  │                            #   樹模型（XGBoost/LightGBM）屬明示的第二輪、未實作
│  ├─ rl_env.py                 # Gymnasium TradingEnv
│  └─ rl_train.py               # PPO/SAC（Stable-Baselines3）
├─ logging/logger.py
├─ monitoring/{metrics.py,alerts.py}   # Prometheus
├─ deployment/{Dockerfile,compose.yaml}
├─ docs/CLOUD_SETUP.md           # 雲端 session 環境重建（2026-08-15；容器 ephemeral，
│                                #   每個新 session 都要重建 venv）
├─ setup_cloud_env.sh            # 上述流程的可執行版（--full 另跑凍結校驗和＋離線測試）
├─ requirements.txt              # ★所有版本含遞移相依一律 pin 死（2026-08-15）★——
│                                #   「凍結資料集、逐位可重現」延伸到依賴層；改動前
│                                #   先讀該檔開頭註解的但書（numpy 無法 3.11/3.14 共用同一 pin）
└─ tests/                        # 風控與回測邏輯必須有測試（目前 379 個，0 warnings）
```

## 風控模組說明（risk/manager.py）

### RiskConfig 欄位

| 欄位 | 預設 | 說明 |
|---|---|---|
| `risk_per_trade` | 0.01 | 多頭每筆風險比例 (1%) |
| `max_daily_loss_pct` | 0.03 | 日內熔斷門檻 (3%) |
| `max_leverage` | 1.0 | 向後相容別名；未設 `max_long_leverage` 時沿用 |
| `short_risk_multiplier` | 0.5 | 空頭風險係數（2026-07-14 校正結案維持 0.5：m 網格 0.25~1.0 風險端無訊號、屬風險胃納哲學參數，詳見 HANDOFF「short_risk_multiplier 校正」節） |
| `max_long_leverage` | None→1.0 | 多頭槓桿上限（None 時從 max_leverage 取） |
| `max_gross_leverage` | None→同 long | 預留欄位（目前多空互斥，等同單方向上限） |
| `max_short_leverage` | 屬性 | = max_long_leverage * 0.5 |
| `risk_per_trade_short` | 屬性 | = risk_per_trade * short_risk_multiplier |
| `forced_liq_atr_n` | 3.0 | 空頭強平 ATR 動態防線倍率（2026-07-13 參數化＋歷史校正完成，建議維持現值） |
| `forced_liq_safety_pct` | 0.15 | 空頭強平固定安全網比例（校正輪凍結未動；資料域內從未先於 ATR 線觸發） |

### RiskManager 主要方法

- `position_size(entry_price, stop_price, side="long")` — 依方向使用對應風險比例與槓桿上限
- `approve_order(quantity, price, today, side="long", current_position_qty=0.0)` — 下單前閘門，加入多空互斥檢查
- `check_forced_liquidation(position_quantity, avg_price, current_price, atr_value) -> bool`
  - 只對空頭（quantity < 0）生效
  - ATR 動態防線：price ≥ avg + N × ATR（N 由 `forced_liq_atr_n` 注入，預設 3.0；
    2026-07-13 歷史校正完成，建議維持現值 N=3/window=14，詳見 HANDOFF.md）
  - 固定安全網：price 超過 avg 的 `forced_liq_safety_pct`（預設 15%）
  - 獨立於 `is_circuit_broken`（日內熔斷）之外

## 開發優先順序

**Phase 編號的唯一正式定義在 `AGENTS.md`「Gate 流程（不可跳站）」**（七站：垂直切片→
事件驅動驗證→拓寬市場→ML→RL→paper→限額實盤）。本節不另行編號，只保留下面的
**市場成熟度檢核表**——與 Phase 正交的另一維度：每個市場（台股／美股／加密）的
pipeline 各自依序通過四個檢核，不編號、不稱 Phase。

市場成熟度檢核表（每市場依序通過，不可跳）：

- **研究**：資料抓取 → canonical schema → 規則策略 baseline
- **驗證**：向量化回測 → 事件驅動回測 → walk-forward → lookahead bias 檢查
- **模擬**：paper trading → 監控告警 → 斷線重連 → 對帳
- **上線**：小資金白名單 → 日內熔斷 → 人工覆核與回滾方案

兩軸關係：Phase 決定「全專案下一個要建的能力」，檢核表決定「某市場的 pipeline 走到
多成熟」。對映：加密軌的研究＋驗證已在 Phase 1–2 完成；台股／美股軌的研究從 Phase 3
開始；模擬對應 Phase 6、上線對應 Phase 7；ML/RL（Phase 4–5）的產出同樣要重走驗證
檢核才能進模擬。

三個市場模組互相獨立，可並行開發，但都必須走完同一套檢核才能進入下一階段，不可為了上線速度跳過風控或回測驗證。

## 硬性工程規範

- **Canonical schema**：K 線資料一律轉成 `ts, symbol, open, high, low, close, volume, turnover, vwap, exchange, interval, source, ingested_at`。台股籌碼（日/週頻）**另有獨立 canonical schema**（每資料集一張表、date 用 ISO 字串、主鍵含 source，定義在 `data/cleaning.py` 籌碼區段）——頻率/粒度不同不混表，兩條資料流只在 `indicators/features.py` 依時間對齊合併，不在儲存層合併（2026-07-07 定案）。新增資料源時優先寫 provider adapter，不要動下游邏輯。
  - **`ts` 統一代表 bar 的「開盤時間」（open time），跨市場一致，不可混用收盤時間（2026-07-08 定案）**。依據：Binance 現有落地資料的 ts = `open_time`（整點、相鄰 1h bar 差 1.0h 實證，`cleaning.py:to_canonical_from_binance`）。因此台股日K 錨定當地 **09:00 Asia/Taipei 開盤**（存為對應 UTC 奈秒；interval=`"1d"`），未來美股等市場一律沿用開盤語意。**理由**：`ts` 語意在不同 symbol/market 間必須一致，否則跨市場共用邏輯（`vector_engine.py` 的 `shift(1)`、`walk_forward.py` 的 fold 切分等）會靜默處理到語意不一致的時間戳——`validate_canonical` 只檢查型別/遞增/不重複，**抓不到這種語意漂移**，只會在下游行為異常時才被發現。
- **時間序列驗證**：一律用 `TimeSeriesSplit` / walk-forward，禁止隨機 K-fold 交叉驗證（會洩漏未來資料）。
- **回測防未來函數**：向量化回測中 `position = signal.shift(1)`，事件驅動回測中嚴格按時間順序處理事件佇列。ATR 等指標一律因果計算（只使用過去資料）。
- **風控模組必須有單元測試**：日內熔斷、槓桿上限、停損停利、強制平倉邏輯改動時，先跑 `pytest tests/test_risk_manager.py tests/test_risk_manager_short.py`。
- **金鑰管理**：所有 API key、憑證密碼一律從環境變數讀取（`config/settings.py`），絕不寫死在程式碼或 commit 進 repo。
- **重試與斷線**：對外部 API（Binance 429/418、券商連線限制）一律實作 exponential backoff + 重連邏輯，不可裸呼叫後讓例外往上炸。
- **RL/ML 驗證**：訓練結果對 random seed 敏感，任何模型結論需多 seed、多 fold 驗證後才能視為穩定，不可用單次跑出來的結果下結論。

## 三市場資料來源對照

| 市場 | 公開資料 | 即時/交易 SDK | 備註 |
|---|---|---|---|
| 台股 | FinMind（籌碼/日頻整合 API，Phase 3 POC 起用）、TWSE / TPEx / TAIFEX OpenAPI | Shioaji、Fubon Neo | 需 CA 憑證、流量限制（如 Fubon intraday 300/min）；FinMind 註冊 token 600 req/hr、超限回 HTTP 402（非 429），token 走 config/settings.py 環境變數 |
| 美股 | Alpha Vantage | Alpaca、IBKR (TWS/Client Portal) | Alpaca 適合 paper trading 起步 |
| 加密 | Binance REST/WebSocket | Binance Spot（簽名請求） | 注意 429 backoff、418 封 IP、WS 24hr 連線壽命 |

## 技術棧

研究期：`pandas` `NumPy` `SQLAlchemy` (`SQLite`/`PostgreSQL`) `vectorbt`/`backtrader` `scikit-learn` `xgboost`/`lightgbm` `PyTorch` `Gymnasium` `Stable-Baselines3`
實盤期：`Prometheus` + `Grafana` + `Sentry` + `Docker` + `GitHub Actions`

## 雙向策略設計注意事項（ma_rsi_bidirectional.py）

空頭觸發條件（**預設 = v2 單視窗 = 基準**；A2 已定案不採用，程式碼保留可顯式啟用）：
```
death_cross (fast < slow)
AND RSI > rsi_not_oversold (預設 30，排除深度超賣)
AND rolling_trend_down (trailing 120 根，regime_window)
AND [選配 A2] rolling_trend_down (trailing long_regime_window 根，如 300；A2 已定案不採用、300 不再校正)
```
- 因果逐根判斷實作在 `backtest/regime.py`：
  `rolling_trend_down_mask(close, window)` 與 `multi_scale_trend_down_mask(...)`。
- `long_regime_window` 預設 `None`（v2 模式 = 當前基準）；傳 300 啟用 A2。
  A2 程式碼與測試保留。四時間窗驗證（2026-07-06）：A2 ≈ v2 差異在雜訊內、
  「trend_up 恆不劣」已被獨立窗反例推翻、條件化A2 已否決；
  **2026-07-06 使用者定案：A2 不採用**，v2 + 風控近似為基準（詳見 HANDOFF.md）。

**不要改回 RSI >= overbought 條件**：滯後型指標（120根 trend_down）與即時反轉信號
（RSI 超買）時間窗不重疊，fold4 實驗驗證 n_short=0，是結構性設計問題。

**A2 的動機**：fold 層級 trend_up/range 中，rolling 120 根窗口可能識別局部
trend_down 子段，產生逆勢空頭訊號（2026-06-25 執行：fold3 23次、fold5 97次）。

**A2 的已知代價（2026-07-03 實驗，兩面都要看）**：
- 有效消滅 trend_up 段逆勢空頭（上次 fold3 時間窗 23→0）。
- 但誤殺下跌起點的早期空單（上次 fold4 時間窗濾掉 41/130，優勢 +27.9pp→+3.3pp）；
- 長視窗殘留記憶：前段下跌讓後續 range 仍判 trend_down（上次 fold5 時間窗只濾 20%）。
- 整體去留已定案：不採用（2026-07-06 使用者決定），詳見 HANDOFF.md 與 AGENTS.md 教訓。

## 給 Claude Code 的工作慣例

- 修改 `strategy/`、`ai/` 下任何模型邏輯前，先確認 `risk/manager.py` 的限制是否同步適用。
- 新增 broker adapter 時，介面要對齊 `broker/paper.py` 的方法簽名，確保 paper/live 可互換測試。
- 任何牽動下單流程的改動，跑完單元測試後才能視為完成；不要假設「能 import 就是能用」。
- 若新增資料源或模型，請同步更新本文件的「專案結構」與「資料來源對照」表，避免文件與程式碼漂移。
- **不要動**：`strategy/ma_rsi.py`、`backtest/vector_engine.py`、`backtest/event_engine.py`（除非使用者明確解除限制。event_engine 已核可過數次範圍性擴充，預設路徑皆 byte-diff 逐位不變、已鎖回：2026-07-13 sizing_mode 規則一；2026-07-15 slippage_bps；2026-07-16 costs 買賣分離費率（台股 E2 化第一輪，見 HANDOFF「台股 E2 化第一輪基準」節））。
