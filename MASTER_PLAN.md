# 量化交易系統 — 專案總體規劃書 (MASTER_PLAN)

> 最後更新：2026-07-20
> 定位：全局視角的總覽 + 路線圖。與既有三份文件互補，不取代它們：
> - `HANDOFF.md` = 現況快照與逐輪結案記錄（最細）
> - `AGENTS.md` = 工作紀律、Gate 制、方法論教訓
> - `CLAUDE_2.md` = 架構規範、canonical schema、技術約定
> - 本檔 = 站在最高處看整個專案：做過什麼、為什麼、還剩什麼、往哪走
>
> 建議放進 repo 根目錄，供任何新 session（Claude Code / Codex / 未來的你）
> 快速建立全貌後，再依 COLLAB.md 的順序細讀其他文件。

---

## 1. 專案總覽

### 1.1 目標與定位

多市場量化交易系統（Python），涵蓋加密貨幣（Binance 現貨）與台股
（TWSE/TPEx），採嚴格的工程與研究紀律開發：分層架構、防未來函數、
walk-forward 驗證、事前判準、Gate 制推進。核心哲學：

- **回測可信度優先於回測報酬**——每個數字都要能溯源到可重現的
  凍結資料集與明確假設
- **誠實否證與成功驗證同等有價值**——被否證的路線（籌碼閘門、
  ML 第一輪）正式結案存檔，不重複踩坑
- **規則策略是所有進階方法的 benchmark**——ML/RL 打不贏就不上

### 1.2 七站 Gate 制與目前位置

| Phase | 內容 | 狀態 |
|---|---|---|
| P1 | 垂直切片（資料→策略→回測→風控→broker） | ✅ 完成 |
| P2 | 驗證階段（event-driven 引擎、walk-forward） | ✅ 完成 |
| P3 | 拓寬市場（台股、ETH） | ✅ 核心完成（可再擴充） |
| P4 | ML 訊號層 | ✅ 第一輪完成並誠實否證結案 |
| P5 | RL 倉位層（FinRL） | ⬜ 未開始 |
| P6 | Paper trading + 監控 | ⬜ 未開始（完全空白） |
| P7 | 限額實盤 | ⬜ 未開始 |

跳站規則：Gate 制不可跳站，除非使用者明確承擔風險並記錄於文件。

### 1.3 三條市場線狀態總表

| 線 | 資料 | 策略 | 風控 | E2 驗證 | 正式基準 |
|---|---|---|---|---|---|
| BTC | 25,300 根 1h（凍結） | v2 雙向 | 完整（規則一二三+校正） | ✅ | ✅ 2bp 基準 |
| ETH | 26,000 根 1h | 沿用 BTC | 沿用 BTC（驗證可推廣） | ✅ | 機制層驗證通過 |
| 台股 | 4 檔日K + 12 檔籌碼 | regime 多頭 + v2 放空 | 七環節台股專屬 | ✅ | ✅ 多頭 + 放空整合基準 |

---

## 2. 系統架構

### 2.1 分層架構

```
資料層 → 特徵層 → 訊號層 → 回測層 → 風控層 → 執行層 → [監控層] → [部署層]
data     indicators  strategy   backtest   risk      broker    (P6 未建)   (P6 未建)
```

原則：每層只依賴上一層的輸出格式，跨層不互戳內部；新增資料源寫
adapter，不動下游。

### 2.2 目錄結構與模組職責

```
├── config/settings.py          # 全部金鑰走環境變數（FINMIND_TOKEN 等）
├── data/
│   ├── providers/
│   │   ├── binance.py          # 公開 REST K 線 + 429/418 exponential backoff
│   │   └── finmind.py          # 台股價格/籌碼/事件，主動節流 + 402 長等待
│   ├── cleaning.py             # canonical 轉換、非正值防呆、融資恆等式驗證
│   ├── adjustment.py           # 還原股價（向後調整，動態計算，方案C）
│   ├── storage_sqlite.py       # K 線落地（WAL + 冪等 upsert）
│   ├── storage_chips.py        # 台股籌碼（獨立 chips_tw.sqlite）
│   ├── storage_adjusted.py     # 還原價凍結快照（方案B，選配）
│   └── storage_events_tw.py    # 除權息 + 停券日曆快照
├── indicators/
│   ├── technical.py            # SMA/RSI/ATR（全部向量化、因果、有 lookahead 測試）
│   └── features.py             # 排他式 as-of join（籌碼日頻 → bar 對齊，age/stale 標注）
├── strategy/
│   ├── ma_rsi.py               # 最原始 long-only baseline【不要動】
│   ├── ma_rsi_regime.py        # regime 過濾多頭（台股多頭基準用）
│   ├── ma_rsi_bidirectional.py # v2 雙向（BTC/ETH/台股放空正式策略）
│   ├── ma_rsi_chip.py          # 籌碼閘門（已否證，保留供參考）
│   └── ml_signal.py            # ML 訊號（P4 第一輪，已否證，保留）
├── backtest/
│   ├── vector_engine.py        # 向量化回測【不要動】
│   ├── event_engine.py         # 事件驅動 E0/E1/E2【不要動；歷經五次範圍性解鎖】
│   ├── walk_forward.py         # TimeSeriesSplit walk-forward
│   ├── regime.py               # trend_up/trend_down/range 分類（R² + Kaufman ER）
│   ├── report.py               # 績效指標（年化/Sharpe/MDD/勝率）
│   ├── costs.py                # TradeCosts（台股買賣不對稱成本）
│   ├── ic.py                   # 資訊係數診斷工具
│   └── liq_calibration.py      # 強平校正 + 台股分層 N 純函式
├── risk/manager.py             # 規則一二三 + 熔斷 + check_forced_liquidation【不要動】
├── broker/paper.py             # 紙上撮合（方向無關倉位、costs 查表、tz-aware ts）
├── ai/ml_train.py              # P4 LR + 交互項訓練管線
├── tests/                      # 379 個測試（全綠、0 warnings）
└── run_*.py                    # 各輪 runner（見 2.4）
```

### 2.3 資料庫檔案（凍結資料集，全部已備份至 GitHub）

| 檔案 | 內容 | 大小 | 凍結狀態 |
|---|---|---|---|
| `data/klines.sqlite` | BTC 25,300 根 + ETH 26,000 根 1h | 7.6 MB | ✅ 校驗和已記錄 |
| `data/klines_tw.sqlite` | 4 檔台股日K（原始價，未還原） | 1.8 MB | ✅ 校驗和已記錄 |
| `data/chips_tw.sqlite` | 12 檔法人/融資融券/外資持股 | 43.7 MB | 已完成 blob 修復遷移 |
| `data/events_tw.sqlite` | 除權息 82 筆 + 停券日曆 98 筆 | 0.04 MB | ✅ 校驗和已記錄 |

台股資料範圍：2330/2603 自 2012-05、8069 自 2005-01、6446 自 2014-03
（籌碼/融券資格自 2023-07-28）。停券日曆 dataset 起點 2015-04。

原則：原始資料永不覆寫（還原價動態計算、哨兵值轉 NULL 在下游做）；
歷史殘留異常列（6446 兩筆）以三步驟協議手動清除並文件記錄。

### 2.4 Runner 清單與滑價 pin 狀態

**pin 在 0bp（歷史快照，勿改）**：`run_atr_calibration.py`、
`run_rule1_sizing.py`、`run_multiplier_calibration.py`、
`run_phase2_event.py`、`run_ml_signal.py`

**使用現行 2bp 正式基準**：`run_slippage_calibration.py` 之後的
所有新 runner（ETH 驗證、台股各輪）

**台股專屬**：`run_e2_tw_baseline.py`（成本必選介面，無預設值）、
`run_tw_short_rules_diag.py`、台股放空整合基準 runner、
`run_ingest_tw_price.py`、`run_ingest_chips.py`、`run_ingest_tw_events.py`

---

## 3. 已完成工作全記錄

### 3.1 BTC 線（最完整）

1. **放空功能**：規則一（風險比例倉位 + short_risk_multiplier=0.5）、
   規則二（多空槓桿分開，空頭 0.5x）、規則三（ATR N×ATR 動態強平 +
   15% 固定安全網雙層防線，取較嚴格者）
2. **雙向策略 v2**：death cross + RSI>30（排除深度超賣）+ trend_down
   regime 過濾。歷經 RSI≥70 時間錯位發現與修正（v1→v2）
3. **A2 多尺度 regime 過濾**：4 個獨立時間窗 + 兩種計算方法交叉驗證
   後判定 ≈ v2、差異在雜訊內，**不採用**（程式碼保留，預設 None）
4. **Event-driven 引擎**：E0（signal_close）→ E1（next_open）→
   E2（完整風控），發現向量化空頭的路徑依賴偏差（雙位數 pp、方向不定）
   與 Δsizing 主導差異（規則二曝險減半是報酬差異最大成分）
5. **ATR 校正**：12 組合 × 3 零重疊窗，維持 N=3.0/window=14
   （事前立場：業界起點值已夠好，除非證據強烈）
6. **multiplier 校正**：4 點網格 {0.25,0.5,0.75,1.0}，P1-P3 事前預期
   精準命中，維持 m=0.5（封頂率分工證據：m=1.0 時 38-45% 封頂 =
   規則一/二角色崩解）
7. **high 觸價敏感度**：+17.5% 觸發增幅但全為「碰線即回」型，
   接受 close-only 簡化
8. **滑價敏感度**：{0,2,5,10,20}bp 網格，5bp 起系統性高估、2bp 零翻轉
   → **正式基準切換至 2bp**（含五支歷史 runner pin 0bp 保護）
9. **Phase 4 ML**：LR + 預註冊交互項（能表達 v2 的 AND 邏輯），
   τ 在全空手/全進場兩極擺盪 = 機率貼 0.5，判準 (c) 攔住 D 窗
   +560.6% 單 fold 幻象（剔除後 +3.8%）→ **否證結案**

### 3.2 ETH 線（推廣驗證）

- 用 BTC 既有 C/D/A calendar 邊界（固定市況、只換資產的控制實驗）
- 五項機制層檢驗全過：v2 方向性同型、ATR 觸發密度 28.1% vs BTC 32%、
  固定網仍為異常備援、multiplier P2 逐位成立、封頂率同量級
- **發現 P1 邊界情況（1/60）**：日內熔斷判定依賴權益路徑、權益路徑
  隨 m 縮放 → 觸發 bar 集合跨 m 不變這個「不變量」只在 BTC 資料域內
  經驗性成立，非數學保證。已記入教訓
- 結論：BTC 拍板結論可推廣至 ETH，不觸發重新校正

### 3.3 台股線（多頭 + 放空七環節）

**資料管線**：
- FinMind 價格/籌碼/事件三類資料源，canonical schema（ts=開盤時間、
  跨市場一致）、還原股價機制（向後調整、動態計算、原始價不動）、
  融資恆等式 26 年 0 違反驗證、哨兵值 -1000000→NULL + 保護性斷言

**已否證路線（完整結案，資料管線保留）**：
- foreign_net IC ≈ 0（甚至弱負），36 格矩陣全數無資訊
- chip-gating 閘門：9/9 長樣本窗跑輸 baseline；N=5 smoothing 換手
  抑制機制有效但無法翻轉整體負貢獻。根因 = 特徵本身無資訊，非包裝方式

**多頭 E2 基準**：ma_rsi_regime + 成本必選介面（三道防線：無預設值
簽名 + 型別斷言 + runner 寫死）+ M1-M3/R1-R2 判準全過。
4 檔年化 mean +3.3%~+15.6%、std 16.7%~43.2%（60 fold-案例）

**放空七環節（全部個別驗證 + 整合驗證）**：
1. 還原價（訊號/指標用還原價、執行語意用原始價，含但書）
2. 不對稱成本必選（買 14.25bps / 賣 44.25bps 含證交稅 + 融券 0.08% 一次性近似）
3. 3.5% 跌幅次日禁空（uptick rule，成交時點檢查、用還原價=交易所定義）
4. 漲跌停鎖死耦合（風險縮減單延後重試、新倉單作廢、pending 防疊倉）
5. ATR 分層 N（trailing_q95 錨定 target 12%、clamp[1.5,5.0]、嚴格因果；
   常態 fold 塌縮達成，regime 位移 fold 殘留誠實記錄）
6. 強制回補日曆（停券窗禁新空 + 死線強制回補、事前公告制 = 合法非因果、
   2015-04 前資料未覆蓋 25/60 fold 完整揭露）
7. 完整風控（熔斷/槓桿/規則二 0.5x）

**整合基準**：七環節全開 vs 單獨開啟逐筆對照（I1 三筆差異全部
drill-down 到可解釋的真實因果鏈）、三項疊加邊界測試釘成永久防線、
同 bar 雙重平倉理由診斷欄位已加。v2 放空 + 多頭 baseline 並排，
不下優劣結論。

---

## 4. 已定案參數與正式基準

### 4.1 參數定案表

| 參數 | 值 | 適用 | 定案依據 |
|---|---|---|---|
| ATR 強平 N / window | 3.0 / 14 | BTC・ETH | 12 組合×3 窗校正，維持現值 |
| 台股分層 N | clamp(12%/trailing_q95, 1.5, 5.0) | 台股放空 | q95 修正輪（median 版已否證） |
| 固定安全網 | 15% | 全市場 | 資料域內從未先觸發；正確值由放空策略輪吸收 |
| short_risk_multiplier | 0.5 | 全市場 | 4 點網格校正，維持現值 |
| 滑價預設 | 2bp | BTC・ETH | 敏感度分析後正式切換（台股未校正=B3） |
| 台股成本 | 14.25/44.25bps 必選 | 台股 | 真實費率+證交稅，融券+0.08% |
| regime_window | 120 | v2 | 長視窗提升穩定性的既有驗證 |
| long_regime_window | None（A2 不採用） | — | 4 窗交叉驗證判定差異在雜訊內 |
| E2 正式 sizing | leverage_cap | 全市場 | 規則一為哲學選擇非技術優劣，r1 路徑保留 |
| 觸發依據 | close-only | 全市場 | high 觸價敏感度後接受簡化 |

### 4.2 正式基準數字（引用時的唯一參照）

**BTC（2bp 正式基準，年化 mean/std）**：
乾淨基準 +0.1%/24.2%｜窗C +110.0%/255.3%｜窗D +317.6%/1197.8%
｜窗A -19.1%/66.7%
（窗D 受單一離群 fold f5 影響大，引用時併看滑價敏感度節說明；
歷史 0bp 數字已 pin 在五支 runner，勿混用）

**台股多頭基準**：4 檔 mean +3.3%~+15.6%、std 16.7%~43.2%（60 fold）

**台股放空整合基準**：完整表在 HANDOFF「台股放空正式基準」節

**判讀鐵則**：彙總 mean 易被單 fold 綁架（D f5、Phase 4 D 窗、滑價輪
窗D 皆為同一模式），任何比較先看逐 fold 分布與剔除最大 fold 後的收斂。

### 4.3 已否證路線（負結果資產）

| 路線 | 否證方式 | 保留物 |
|---|---|---|
| foreign_net 籌碼訊號 | IC 診斷 36 格 ≈0 | 資料管線 + IC 工具 |
| chip-gating 閘門 | 9/9 窗跑輸 + IC 交叉印證 | 程式碼 + N=5 smoothing 機制 |
| A2 多尺度過濾 | 4 獨立窗 + 雙方法一致 ≈v2 | 程式碼（預設關閉） |
| ML LR+交互項 @1h | 四判準 + τ 兩極診斷 | 管線 + 洩漏防治規格 |

重啟條件備忘：ML 若重啟，「換更低頻 label」優先於「換更強模型」
（表達力已被排除為根因）。

---

## 5. 方法論資產（比任何單一結論更值錢的部分）

### 5.1 核心紀律（AGENTS.md / COLLAB.md 強制執行）

1. 事前判準：先定義「贏」再跑數字，判準重定義由使用者拍板、不靜默放寬
2. 一輪一變因：隔離歸因，多機制疊加時做 ISO vs FULL 逐筆對照
3. 零重疊多窗：重疊窗（86% 案例）不是獨立證據
4. 機制層/結果層分開判讀：機制層不過先除錯，不解讀報酬
5. TDD：先紅後綠；踩過的坑做成永久回歸測試
6. 固定資料集：凍結 + 校驗和 + runner pin，逐位可重現
7. 交付摘要 + 文件同步義務（HANDOFF/CLAUDE_2/AGENTS 三檔）
8. 跨 agent 協作走 COLLAB.md（四文件必讀、狀態盤點、不要動清單）

### 5.2 教訓清單（歷輪累積，遇同型問題直接套用）

1. Regime 決定勝負，不是參數
2. 訊號時間特性互斥（RSI 即時 vs regime 滯後）→ 檢查時間尺度匹配
3. 小樣本彙總不穩健（5-fold 被 3 小時位移翻轉）→ 先擴樣本再調參
4. 向量化空頭 = 逐根再平衡，路徑依賴偏差雙位數 pp、方向不定
5. Δ 歸因先驗證觸發 bar 集合等同，再歸因到曝險設計（勿誤讀為訊號品質）
6. Sizing 公式本身的曝險縮放可在 0 強平下大幅改變報酬（fold4 錨定案例）
7. 新增預設參數 = 所有「靠省略表達假設」的呼叫端靜默變行為
8. 校正統計量必須對準機制要守的分位（median vs q95），錨錯分位
   單元測試全綠仍結構性失效，只有事前判準能攔
9. 跨市場/頻率沿用參數前，按波動特性子群體分開檢查（大型股 vs 小型股）
10. 跨參數不變量僅在已測資料域內成立（ETH P1 熔斷/sizing 路徑依賴）
11. Trailing 校正雙向盲點：看不見未來 regime 突變，兩個方向都會錯
12. 使用未來已公告資訊的合法性三條件（決策時點真實可得／非價格衍生物／
    重新表述 vs 決策輸入），先例：還原價、停券日曆

### 5.3 執行語意邊界

- 執行語意（能不能成交、漲跌停判定）用**原始價**；訊號/指標用**還原價**
- 但書：若規則在真實世界的定義本身 built on 調整後價格（如 3.5% 禁空），
  用還原價才是精確——判斷準則：先問交易所自己用哪個價格基底

---

## 6. 已知限制與掛帳清單

### 6.1 結構性限制（文件化、不解決，引用數字時的可信度但書）

| 限制 | 量級 | 觸發處理時機 |
|---|---|---|
| 熔斷語意在日頻塌縮 | 保護力弱於 1h | 若改分/時頻台股資料 |
| 漲跌停鎖死成交假設 | 0.06%~0.56% 頻率 | Paper trading 前 |
| 融券資本效率 190% | position_size 高估近一倍 | Paper trading 前（C1） |
| 借券費一次性近似 | 未建時間累積模型 | Paper trading 前（C2） |
| 券源限制 | crisis 尾部（2603 100.75%、8069 125 日案例） | Paper trading 前（C3） |
| NT$20 低消 | 無法建模 | 已接受 |
| 回補日曆 2015-04 前未覆蓋 | 25/60 fold | 已接受（不用假資料補） |
| Trailing 校正 regime 位移殘留 | 2603 f9 殘留 21.3%；5 fold 劣於靜態 N=3 | 逐 bar 自適應輪（未排程） |

### 6.2 掛帳項目（依優先序）

| 項 | 內容 | 狀態 | 觸發條件 |
|---|---|---|---|
| B3 | 台股滑價校正 | 未開始 | 台股數字要對外引用前 |
| B2 | 回補死線主動避開 | 量測已備（6 案例、費用 0.0149） | 需要優化執行成本時 |
| — | 台股股票池擴大（4→更多檔） | 未開始 | 台股結論要一般化前 |
| — | ML 低頻 label 第二輪 | 備忘已記 | 想重啟 ML 時 |
| — | TaiwanStockPriceLimit 付費層 | 查證完（免費層鎖） | 需要精確漲跌停價時 |
| — | 逐 bar 自適應 N | 未排程 | 分層 N 殘留風險不可接受時 |
| D1/D2 | regime 迴圈效能 / engine 參數密度 | 純效能/整潔 | 變成實際瓶頸時 |

---

## 7. 未來路線圖

### 7.1 近期可選（中型任務，單輪可完成）

1. **B3 台股滑價校正**——比照 BTC 滑價輪方法論，台股日K 的合理滑價
   量級需獨立查證（與 BTC 1h 完全不同）
2. **台股股票池擴大**——4 檔的結論不足以一般化；chips_tw 已有 12 檔
   籌碼資料，價格補齊即可。注意：擴池會引入產業/市值異質性，比照
   ETH 輪「固定市況只換資產」的控制實驗設計
3. **台股放空策略層設計**——目前放空用的是 BTC 校正的 v2 訊號參數，
   台股專屬的空頭進場邏輯（含 regime 視窗是否需要日頻重校）從未設計過。
   注意：regime/ATR 校正輪已確認 window=14「日」與「小時」經濟意義不同
4. **B2 主動避開優化**——量測資料已就緒，評估「省確定費用 vs 放棄
   不確定損益」

### 7.2 Phase 5：RL 倉位層（FinRL）

**開工前必答的前置問題**（依 P4 教訓，不答就開工=重蹈覆轍）：
- **特徵從哪來**：1h 價格衍生特徵已被 P4 證明榨乾。選項：更低頻、
  跨市場特徵、橫截面多標的。這是策略設計決策，需使用者拍板
- **環境包裝**：FinRL 內建 env 的成交/成本假設與本專案 E2 不相容，
  需自建 Gymnasium env 以 event_engine 為底層（工程量不小）
- **Benchmark 鐵則**：RL 必須在 E2 真實假設下打贏 v2+風控才算有價值；
  RL 輸出必須過 approve_order，永不繞過風控層
- **多 seed 預算**：RL 訓練成本是規則策略驗證的百倍級，RTX 5070 Ti
  的實驗預算要先規劃
- **Action space**：discrete {-1,0,1} 對齊現有訊號設計起步，continuous
  倉位控制留到驗證有價值後

### 7.3 Phase 6：Paper Trading + 監控（目前完全空白的一整塊）

需要從零建立：
- `logging/logger.py`、`monitoring/metrics.py`（Prometheus：order
  latency、equity、drawdown gauge）、`monitoring/alerts.py`
- `deployment/`（Dockerfile、compose）
- 即時資料流：斷線重連、backoff、資料缺口處理
- 對帳機制：paper 成交 vs 預期成交
- **雙軌資料庫**：凍結研究庫（現有）+ 滾動更新實盤庫——凍結庫隨
  時間與現實脫節是刻意取捨，進 P6 時必須設計雙軌
- 台股若進 P6：C1-C6 全部限制需逐項重新評估建模必要性
- 熔斷語意：若上 1h/即時頻率，日內熔斷恢復真實保護力

### 7.4 Phase 7：限額實盤

- 前置：P6 穩定運行 + 使用者明確的風險預算決策
- 台股實盤另需：券商 API（Shioaji 等）、信用交易資格、實際借券成本
- 加密實盤另需：Binance 私有 API、實際資金費率（若做合約）

### 7.5 長期選項（有興趣但未排程）

- **美股/SOXL**：零基礎（無 provider/schema 驗證/成本模型），槓桿 ETF
  的每日再平衡結構會放大路徑依賴問題，工程量 ≈ 重走一次台股 POC
- **五檔/order book 深度**：資料類型與 broker 成交模型都要重做，
  1h/日頻策略下邊際價值低，留到接近實盤 + 頻率提高時
- **橫截面多標的框架**：方法論大轉向（排序選股 vs 時間序列預測），
  接近開新子專案，但若 ML 重啟這可能是特徵貧乏問題的正解

---

## 8. 還原與接續指南

### 8.1 環境還原（送修回來 / 換電腦）

**雲端 session（2026-08-15 起的現行環境，Linux）**：容器 ephemeral，
每個新 session 都要重建 venv，已固定成一支腳本：

```
git clone https://github.com/dragonheart8787/-Quant-Trading
cd -Quant-Trading
./setup_cloud_env.sh          # 建 venv + 裝 pin 死的相依 + 跑全套測試
./setup_cloud_env.sh --full   # 上述 + 凍結資料庫校驗和 + 離線（封 socket）測試
```

**本機（Windows，送修回來後）**：

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest tests/ -q   # 應見 379 passed, 0 warnings
```

**跑測試不需要任何 API token**（2026-08-15 以封鎖 socket 重跑全套實證，
非讀碼推論）——`FINMIND_TOKEN` 只有資料抓取 runner 才需要，Binance 公開
端點不需金鑰。`requirements.txt` 的版本（含遞移相依）已全數 pin 死，
理由與 numpy 跨版本但書見該檔開頭註解；完整驗證過程見 HANDOFF.md
「雲端環境基準」節，重建細節見 `docs/CLOUD_SETUP.md`。

資料庫都在 repo 裡，**不需要重新 ingest**（重新 ingest 會破壞逐位
重現性）。備份 commit：27229f38…（112 檔案，四資料庫 byte 級一致）。

### 8.2 新 session 上下文重建（標準開場）

```
1. 依序讀 MASTER_PLAN.md（本檔，先看全貌）→ COLLAB.md → AGENTS.md
   → CLAUDE_2.md → HANDOFF.md（五份，順序以 COLLAB.md 的必讀清單為準）
2. 環境：雲端用 .venv/bin/python（先跑 ./setup_cloud_env.sh 重建）；
   本機用 .venv\Scripts\python.exe
3. 跑全套測試，實際數字與 HANDOFF 記錄比對，不一致先回報
4. 若被要求「繼續上次工作」，先做狀態盤點（文件與程式碼可能因
   中斷而不同步），不要假設
```

### 8.3 遇到問題時的判斷順序

1. 先查 AGENTS.md 教訓清單有沒有同型先例（§5.2）
2. 數字對不上 → 先確認滑價 pin 狀態（0bp 歷史 vs 2bp 現行）與
   資料集凍結範圍，再懷疑程式碼
3. 「看起來像未來函數」→ 套用合法性三條件（§5.2 第 12 條）
4. 任何跨市場/跨頻率沿用 → 按子群體波動特性分開檢查（§5.2 第 9 條）

---

## 附錄：不要動清單（跨 agent 共同遵守）

`strategy/ma_rsi.py`、`backtest/vector_engine.py`、
`backtest/event_engine.py`、`risk/manager.py`

解除限制是**當次任務範圍**，不是永久生效。歷次範圍性解鎖記錄見
HANDOFF.md 各輪結案節。`event_engine.py` 已歷經五次，依時序為：

1. `sizing_mode`（2026-07-13，規則一 sizing 接入 E2；預設 leverage_cap 逐位不變）
2. `slippage_bps`（2026-07-15，滑價敏感度→正式基準切換至 2bp）
3. `costs`（2026-07-16，台股 E2 化第一輪，買賣不對稱成本；預設 None 逐位不變）
4. `short_uptick_rule_drop` + `lock_up`/`lock_down`（2026-07-16，台股放空第一輪）
5. `short_entry_ban` + `forced_cover_deadline`（2026-07-16，強制回補日曆）

注意：`trigger_source`（high 觸價敏感度輪，2026-07-14）**不是** event_engine
的解鎖——該輪改的是 `backtest/liq_calibration.py`，HANDOFF 明載
「event_engine.py 未動（不要動清單未破）」，且使用者拍板時明確決定
「不解鎖 event_engine.py 做報酬層驗證」。
