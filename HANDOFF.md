# HANDOFF — 量化交易專案目前狀況

> 給接手的 Claude / 開發者：讀完這份就能立刻接續工作。最後更新：2026-07-16。
> 規則文件：`AGENTS.md`（工作紀律）、`CLAUDE_2.md`（架構，**注意檔名是 CLAUDE_2.md 不是 CLAUDE.md**）。

## 一句話現況
Phase 1 垂直切片完成；空頭風控與雙向策略已實作；K 線已落地 `data/klines.sqlite`
（WAL，**25300 根 ≈ 34.6 個月，2023-08-14 → 2026-07-03**）；**策略預設 = v2 單視窗
（當前基準），A2 為選配**；15-fold × 四時間窗驗證完成（A/B 錯位但重疊 86%；C/D
完全獨立）：**A2 ≈ v2 彙總差異在雜訊內；先前「A2 在 trend_up fold 恆不劣於 v2」
的模式已被獨立窗反例推翻（窗C fold9 -44.5pp），條件化A2 已否決不實作**
（見「15-fold 擴大驗證」與「獨立時間窗驗證」節）；**A2 已定案不採用
（2026-07-06 使用者決定；程式碼與測試保留、預設 None 不變，此假設不再驗證）**。
**C（Phase 2 事件驅動）第一階段完成**：E0/E1/E2 + 兩層驗收 + 完整跑批與
Δ歸因總表（156 tests；強平觸發 bar 與 overlay 10/10 一致）。**新引用紀律：
策略價值評估一律以 E2 數字為準**——向量版空頭數字量級由曝險假設主導
（sizing 平均|Δ|~22pp > 倉位模型 7~11pp > 熔斷本批 0，見「Phase 2」節）。
**Phase 2 已於 2026-07-07 正式結案**（DoD 四項通過：156 tests、
run_phase2_event.py 跑批逐位重現；Phase 編號同日統一為七站制，唯一定義見
AGENTS.md「Gate 流程」節）。**A2 的 E2 複查同日結案棄做**：向量化（15-fold ×
4 個獨立時間窗）與事件驅動 E2（5-fold）兩種獨立計算方法交叉驗證結論一致
（v2 略優），A2 程式碼與測試保留供未來參考，不再是待辦。
**目前在 Phase 3（拓寬市場：台股籌碼資料）進行中**，POC 先接 FinMind，
之後再評估是否換 TWSE/TPEx 官方 OpenAPI。**籌碼 POC 已結案（195 tests、四項
決定落地、12 檔真實資料落 chips_tw.sqlite）；indicators/features.py 日頻籌碼
排他式 as-of 對齊已完成（2026-07-08，6 項 lookahead 測試、真實資料驗證）**。
**台股日K（TWSE/TPEx 價格）POC 已接上（2026-07-08）：finmind.fetch_price →
TaiwanStockPrice、cleaning.finmind_price_to_canonical → K 線 canonical（ts=09:00
台北開盤、interval="1d"）、4 檔落 data/klines_tw.sqlite；價格＋籌碼兩流第一次
真實端到端對齊，6446「價格續、籌碼斷尾」分歧點在真實資料上重現。過程修掉一個
features.py 的 ns/us datetime 解析度 bug（見 Phase 3 節「台股日K」）。**
**籌碼特徵併入 walk-forward 一輪已完成（2026-07-08，訊號層驗證，不追 alpha）：
strategy/ma_rsi_chip.py（MA/RSI + 單一閘門 foreign_net>0）× baseline 對照，
過濾成因拆兩類（外資賣超 vs 籌碼缺失/stale），6446 全 24 根 baseline 做多因籌碼
缺失被濾（非策略判斷）——見 Phase 3 節「walk-forward」。**
**台股真實交易成本已接入（2026-07-10）：backtest/costs.py 買賣分離費率（買
14.25bps 牌告無折扣／賣 44.25bps 含證交稅 30bps）；vector_engine 經使用者解除
限制做最小擴充（costs=None 走舊路徑**逐位不變**，5-fold 乾淨基準 diff 逐字節相同
＋雙訊號保護性回歸測試守住加密貨幣路徑）；chip 閘門放大換手（2330：3→20 次），
賣出端證交稅疊加下 chip 版侵蝕遠大於 baseline（-39.6/-50.6pp 年化）；NT$20 低消
無法建模（報酬率制引擎限制）——見 Phase 3 節「台股真實交易成本」。**
**N 日淨買超 smoothing 已實作（2026-07-10，chip_window=5 起點值只跑一次不掃
參數；後因方向結案不再校正）；台股資料已擴充至交集全區間（15,161 根日K，籌碼庫本就全歷史
不需重抓）並完成 15-fold × 獨立雙半窗驗證：N=5 的換手抑制與侵蝕縮小 6/6 窗
方向一致（機制穩健）；但★chip 閘門本身不論 N 在長樣本＋真實成本下 9/9 窗全面
跑輸 baseline★——閘門現行形式無 alpha，N=5 只是修復 N=1 的傷害；2024Q1 小樣本
的樂觀印象全數不成立。6446 標注僅作 stale 測試不入統計。見 Phase 3 節「台股
資料擴充＋獨立雙窗驗證」。**
**foreign_net IC 診斷已完成（2026-07-12）：36 格矩陣（3 檔 × H1/H2 × raw/s5 ×
k∈{1,5,10}）★沒有任何一格顯示跨窗一致的正 IC★；唯一跨窗一致的訊號是弱負 IC
（raw k=1 六窗全負，|IC|≈0.02~0.05）——「外資買超→未來報酬」在現行特徵形式下
無正向資訊價值，與 chip 閘門 9/9 窗跑輸 baseline 互相印證。橫向 horizon 拉長
（k=5/10）不增加資訊、只放大雜訊。見 Phase 3 節「foreign_net IC 診斷」。
237 tests。**
**★ Phase 3 台股籌碼特徵已於 2026-07-12 正式結案（使用者決定）★：現行
chip-gating 設計此路不通（根因 = 特徵無資訊，非成本或參數問題）；資料管線、
方法論、程式碼與測試全部保留供未來新特徵構想重啟用。完整推論鏈見「Phase 3
台股籌碼特徵：正式結案」節。**
**★ ATR 強平防線校正已完成機制層＋結果層（2026-07-13，等使用者拍板）★：
RiskConfig 新增 forced_liq_atr_n/forced_liq_safety_pct（預設 3.0/0.15，行為
逐位不變）；event_engine 實測零改動即完成透傳（不要動清單未破）。三個零重疊
8000 根窗（C/D/A）× 15-fold、165 個空頭區段、12 組合網格：固定網 15% 全程
未先觸發；現值 N=3/14 誤殺近零（0/0/1）；收緊 2.0/21 三窗一致 +2.4~2.7pp
但遠小於 fold 雜訊（std 75%~1325%）；放寬 4.0/14 三窗一致變差。
★已定案：維持現值 N=3.0/window=14（2026-07-13 使用者拍板）★，量級與限制
見「ATR 強平防線校正」節。**
**★規則一（risk_per_trade 比例倉位）已接入 E2（2026-07-13，五決策點使用者
核可）★：event_engine 經範圍性解鎖新增 sizing_mode（預設 "leverage_cap"
逐位不變，byte-diff 驗證）；"risk_per_trade" = 空頭用 N×ATR 停損算
position_size()（只接空頭=選項A、NaN fallback 槓桿上限、multiplier 0.5 不動）。
Δ規則一對照（乾淨基準+C/D/A）：空頭曝險 0.5x→平均 0.12~0.42x、觸發 bar
集合逐 fold 相同、強平損失/預算 0.33x~1.94x（≤2x）、規則二封頂僅 ~5%
進場生效；★fold4 無強平但 +27.5%→-1.7%——高波動賺錢段被波動縮倉吃掉
參與度，是規則一本質權衡非誤殺★。★基準已定案不切換（2026-07-13 使用者
拍板）：維持 leverage_cap，哲學選擇留待未來、r1 現成路徑保留★。268 tests。
下一步等使用者指示方向（Phase 4 ML 特徵前置討論或其他掛帳項），不自動開工。**
**★high 觸價敏感度已結案（2026-07-14 使用者拍板：接受 close-only 簡化）★：
機制層壓力測試（liq_calibration 加 trigger_source，event_engine.py 未動；
268→277 tests）：觸發 57→67（+17.5%，超過 10% 門檻）但 9 筆獨立新增全屬
「碰線即回」型（反事實逐筆損益差 ≤1.74pp 未年化、盤中最壞浮虧上界 3.40%，
無深水炸彈）、★fold4 仍 0 觸發（安全邊際 +0.54~+2.89×ATR）★、固定網 15%
仍全程未先觸發——close-only 為足夠近似，不做報酬層驗證。細節與判決理由見
「high 觸價敏感度」節。掛帳僅剩 short_risk_multiplier=0.5 校正（r1 路徑）
→ 亦已結案，見下段。**
**★short_risk_multiplier 校正已結案（2026-07-14 使用者拍板：維持 m=0.5）★：
網格 m∈{0.25,0.5,0.75,1.0} × 乾淨基準＋C/D/A（277→284 tests；
run_multiplier_calibration.py 一鍵重現）。機制層事前預期 P1~P3 精準命中：
觸發 bar 集合跨 m 不變（57 筆強平在每個 m 下相同）、未封頂損失/預算比率
跨 m 逐位不變（155 比較對最大偏差 2e-16）→ ★風險端無校正訊號，m 只是
報酬端＝絕對風險端的同一個線性係數★；報酬端小 m 四範圍一致略優
（+0.7~+2.9pp）但深陷 fold std 且被單 fold 主導；m=1.0 使規則二封頂率
3~7%→38~45%，規則一/二分工崩解。m 是風險胃納哲學參數非資料可判定，
維持 0.5。fold4 線性兌換全鏈條見「short_risk_multiplier 校正」節。
★至此兩個掛帳任務（high 觸價、multiplier 校正）全數結案，僅剩低優先
掛帳（滑價敏感度、固定網 15% 校正、utcnow deprecation、多 symbol）★。**
**★Phase 4 ML 訊號層第一輪已判定（2026-07-15，一次定案跑批）：LR＋v2規則
交互項 vs v2 的 E2 配對對照，事前判準 (a)(b)(c) 全數 FAIL（排除 thin fold
複驗同）→ ★v2 續任（預設立場）：在現有資料量/1h 頻率/現有特徵集下，ML
非線性組合未顯示可偵測的優勢★。ML 行為兩極（τ=0.15 全空手 27/45 fold、
τ=0 高換手），Δ 主成分是參與度與換手成本非選 bar 品質；D f5 單 fold
+8356pp 主導彙總、判準 (c) 正確攔下。284→293 tests；管線/測試/runner
保留（run_ml_signal.py 一鍵重現）。★同日正式結案（使用者拍板：不開第二輪
樹模型/低頻 label）★——診斷為與 foreign_net IC 否證同型的乾淨否證：問題
不在模型表達力（LR+交互項已能表達 v2 的 AND），在特徵×頻率組合的資訊
含量已被 v2 簡單規則消化殆盡。重啟備忘 = 更低頻 label 優先於更強模型
（明確新方向新規格，不動工）。見「Phase 4 ML 訊號層第一輪」節。**
**★E2 正式基準已切換為 2bp 滑價（2026-07-15 使用者拍板）★：
`event_engine.py` 的 `slippage_bps` 預設值由 0.0 改為 2.0（BTCUSDT 真實
流動性量級，2bp 在全部四個範圍零翻轉、fold4 錨點維持穩健）。五支已結案
runner（ATR校正/規則一sizing/multiplier校正/Phase 2/Phase 4）已明確 pin
`slippage_bps=0.0` 保住歷史數字逐位可重現；18 個測試補上同一 pin 恢復
全綠（303 tests）。新乾淨基準+C/D/A 對照表（省略參數即自動套用）：
乾淨 +0.1%/24.2%、C +110.0%/255.3%、D +317.6%/1197.8%、A -19.1%/66.7%
（年化 mean/std）——這是往後所有新任務的正式參照，見「滑價敏感度」節
「基準切換執行」小節完整細節。**
**★多 symbol 擴充方向A（ETHUSDT）驗證完成（2026-07-15）：v2/ATR N=3.14/
multiplier m=0.5 可推廣至 ETH，非 BTC 特例★**——機制層（ATR 觸發密度
28.1% 同量級 BTC 32%、固定網仍 0 次先觸發、multiplier P2 逐位成立）無
退化；v2 方向性剔除單 fold 主導效應後四範圍與 BTC 同型。唯一真實偏差：
P1（觸發bar集合跨m不變）在窗D fold9 出現熔斷-sizing 路徑依賴的邊界互動
（1/60 發生率，已根因分析非 bug）。詳見「多 symbol 擴充（方向A：
ETHUSDT）」節。台股 E2 事件驅動驗證為下一個既定計畫（排在此輪之後）。**
**★台股放空第一輪已完成（2026-07-16）★**：groundwork 六項查證（借券費/
平盤限制/強制回補/維持率/券源/ATR分層，三項有自有資料真實案例交叉驗證）
→ 範圍定案兩項實作：①3.5% 跌幅次日禁空規則（event_engine 第四次範圍性
解鎖 `short_uptick_rule_drop`，成交時點檢查、還原價基底＝「執行語意用
原始價」的但書案例）；②漲跌停鎖死耦合（`lock_up`/`lock_down`，方向感知：
風險縮減單延後重試、新倉單作廢）＋ ATR 分層 N（`stratified_forced_liq_n`，
trailing **q95** 錨定 12%——★第一版 median 錨定 9% 經事前判準 P4 FAIL、
根因=「median 回答平常水位、機制要守極端水位」的統計量錯位，使用者拍板
換 q95＋重新定義判準；新判準下常態 fold（44/60）塌縮達成（8069
17.65%→0.86%），劇烈 regime 位移 fold（16/60，最壞 2603 fold9 殘留
21.3%）記錄為 trailing 校正的已知結構性殘留風險、不消除★）。items
1/4/5 已知限制文件化（資本效率 190% 但書等）。334→**356 tests**。詳見
「台股放空第一輪」節與 AGENTS.md「校正統計量必須對準機制要守的分位」
新教訓。**
**★item 3 強制回補日曆已完成（2026-07-16）★**：資料存在性查證找到
FinMind 免費層 `TaiwanStockMarginShortSaleSuspension`（事前公告制，
51/52 與除權息日交叉驗證）→ 第五次範圍性解鎖 `short_entry_ban`（停券窗
禁新空）＋ `forced_cover_deadline`（死線強制回補，`calendar_forced_cover`
/`calendar_cover_bars` 與價格驅動強平歸因分離）。C1~C5 全過（6 筆日曆
回補全部準時、零窗內進空、97 個停券窗 96 個恰 4 交易日+唯一例外=凱米
颱風休市真實事件）；**未覆蓋 fold 完整揭露 25/60**（2015-04 dataset
起點前+6446 融券資格 2023-07 起）。t+1 日曆旗標=本專案第二個合法非因果
例外，通用分類標準已建立（AGENTS.md「使用未來已公告資訊的合法性分類」）。
主動避開量測：6 案例、來回費用合計 0.0149，屬未來優化輪決策輸入。
356→**368 tests**。詳見「台股強制回補日曆」節。**
**★技術債清理輪已完成（2026-07-16，14 項盤點後使用者排序 A4→A3→A2→A1→
B1→B4）★**：A4 事件快照機制（`data/storage_events_tw.py`+
`run_ingest_tw_events.py`，四支台股 runner 改讀本地快照、切換後輸出逐位
一致——隔離 FinMind 外部依賴，與 utcnow「環境可能被迫改變」同類風險）；
A3 台股可重現性保護補到與 BTC 同等（klines_tw+events_tw 凍結校驗和，見
「台股固定資料集原則」節）；A2 HANDOFF 底部待辦清單同步（台股放空設計
條目更新為已完成）；A1 chip_margin blob 修復（寫入端 .item() 轉原生型別
＋歷史 194,516 個欄位值遷移、定點驗證 PASS、負值零殘留＋補上「讀回型別
正確」防護測試——當時 roundtrip 測試只驗 NULL 語意的測試縫隙）；B1 固定網
15% 定調關閉（改標「由台股放空策略輪吸收」）；B4 TaiwanStockPriceLimit
查證=**鎖付費層**（免費層 HTTP 400 實測，與 TaiwanStockPriceAdj 同一道
牆；內容 date/reference_price/limit_up/limit_down 自 2000 年起，記入
付費層升級選項，維持現行分時代啟發式門檻）。B2 主動避開/B3 台股滑價
維持現狀；C1~C6/D1~D2 已知限制確認現況描述無誤、零異動。368→**376
tests**。**
**★台股放空正式基準已完成（2026-07-16，七環節整合第一輪）★**：機制層
——I1 觸發清單 ISO vs FULL 比對發現 3 筆差異、全部 drill-down 到具體
因果鏈（價格強平先於日曆回補×1；日曆回補→低價再進場→反彈觸發新強平
×2），零機制誤傷、全屬合法部位路徑交互；I2 三項疊加邊界 pytest 釘住
（uptick 不誤傷強平回補／同 bar 雙重平倉理由=價格優先日曆跳過（特徵化
屬性已呈報）／鎖死×死線=單一回補單重試）。結果層——R1 0/60 異常；
v2 全開 vs 多頭 baseline 並排：2330 +18.7%/2603 +17.9%/8069 +9.4%/
6446 -4.5%（std 36~54%，高雜訊比與既有結論同型；不下優劣結論）。
376→**379 tests**。Runner `run_tw_short_baseline.py`（全凍結快照，
逐位可重現）。詳見「台股放空正式基準」節。**
**★台股 E2 化前置查證＋股價還原機制已完成（2026-07-16）★**：查證確認
`klines_tw.sqlite` 收盤價為原始成交價、未除權息還原（65/65 事件逐位驗證，
全歷史範圍受汙染，2603 2023-06-30 單日 -45.2% 為最極端案例）；另查證
日內熔斷在日頻下保護範圍塌縮成「唯一同根自我攔截」、放空結構落差清單
（借券費/平盤限制/強制回補/維持率/券源限制 5 項缺概念）、漲跌停鎖死
歷史頻率 0.06%~0.56%（連台積電都在 2025-04 關稅事件踩過）、costs.py
不對稱成本確認零耦合 event_engine.py。**股價還原方案已 TDD 實作完成
（325 tests，+22）**：`data/adjustment.py::apply_back_adjustment()` 動態
計算還原價（方案C，預設路徑，不落地覆蓋原始資料）+ `data/storage_adjusted.py`
凍結快照工具（方案B，選配，獨立檔 `klines_tw_adjusted.sqlite`）；`data/cleaning.py`
新增非正值 O/H/L/C 防線（6446 藥華藥 2016-12-05 全零列事故發現，_sanitize
丟棄+validate_canonical 保護性拋錯兩層防線）；該筆既有殘留列已於使用者
明確授權後手動 DELETE 清除（精準定位 1 筆 → 刪除 → 驗證鄰近日資料完整 →
325 tests 不受影響，與「未來 re-ingest 自動排除」的常駐防線是分開的兩件
事，見「台股股價還原機制」節）。chip-gating 結案節已加註「不重跑」限制與理由，不受此次
發現影響其結論。詳見「台股股價還原機制」節。**
**★台股 E2 化第一輪（多頭 baseline）已完成（2026-07-16）★**：
`strategy/ma_rsi_regime.py` + 台股真實不對稱成本（必選介面：函式簽名無
預設值+型別斷言+全域唯一 costs 常數，三道防線已實測驗證）+ 還原價全程
+ 4 檔股票各自 15-fold（60 個 fold-案例）。事前判準 M1（成本不對稱生效）/
M2（熔斷觸發密度 43/60，非零非全觸發）/R1（0 個 NaN/發散值）/R2（trend_up
多頭曝險佔比 38.5% vs 非 trend_up 12.3%）**全數 PASS**；M3 漲跌停曝險
診斷完整揭露（8/60 決策bar 曝險、1/60 實際成交曝險，皆對應可查證真實
事件如 2025-04 關稅衝擊）。4 檔 mean 年化 +3.3%~+15.6%、std 16.7%~43.2%
（單 symbol 雜訊比高，與加密貨幣 1h 尺度既有結論同型）。`event_engine.py`
範圍性解鎖新增 `costs` 參數（預設 None 逐位不變，保護 BTC/ETH 既有呼叫端）；
`broker/paper.py` 同步支援買賣分離費率。325→**334 tests**。詳見「台股 E2
化第一輪基準」節。**這輪不下策略優劣結論，純粹是台股 E2 執行層第一份
基準數字，供未來放空/籌碼重啟/ML 引用對照**——這些是下一步既定方向。
**★台股 regime/ATR 參數校正已結案（2026-07-16，維持現值不變更）★**：
P2（59/60 觸發bar集合一致，1 例熔斷擋單近似位移已根因分析非 bug）、P3
（regime_window=120 在 {60,120,180,252} 掃描中無孤立斷點，已結案基準
不需重看）通過；**P1 有重大結構性發現（未變更參數，保留給未來放空設計）**：
N=3 動態線對 2330 設計意圖完好（≥15% 僅 0.23% bar），對 TPEx 小型股結構性
失效（8069 有 19.52% 交易日動態線在固定網之上），真實危機期（2008 金融
海嘯、2015 全球股災）固定網反而先觸發、兩層防線角色反轉——台股放空設計
啟動時 N=3/w=14 不可直接沿用。見「台股 regime/ATR 參數校正」節。
**★雲端環境基準已驗證接受（2026-08-15）★**：本機送修，改用 Claude Code on the
web 雲端 session（Linux, **Python 3.11.15**，本機為 3.14.6）。四項驗證全 PASS：
凍結校驗和逐位相同、**379 passed/0 warnings**（與記錄一致）、**封鎖 socket 後
重跑全套仍 379 passed**（證明免 FinMind token、全部讀凍結 DB，非讀碼推論）、
`run_tw_short_baseline.py` 在 3.11.15/numpy 2.4.6 與 3.14/numpy 2.5.2 兩組環境
**輸出逐位元完全相同**（版本差異實測零影響；殘留限制：驗到 3.14.0rc2 非精確
3.14.6，已記錄接受）。順帶修掉 `requirements.txt` 漏列 scikit-learn（乾淨環境
7 個測試模組 collection error），並依使用者決定把全部版本 pin 死。見
「雲端環境基準」節與 `docs/CLOUD_SETUP.md`。

## 環境（2026-07-03 重大變更；2026-08-15 起本機送修，改用雲端 session）
> **目前實際工作環境是雲端 sandbox（Linux, Python 3.11.15），不是下面的 Windows 本機。**
> 見「雲端環境基準」節。本節保留為本機環境的記錄，本機修好後回歸時仍適用。

- Windows 11，PowerShell 為主（也有 bash）。
- **系統 Python 3.13 已消失**（2026-07-03 重開機後只剩 3.11 與 3.14，且皆為裸環境）。
  現在用專案內 venv：`.venv\`（Python 3.14.6 + pandas 3.0.3 + numpy 2.5 +
  requests + pytest + scikit-learn 1.9）。**所有指令改用 `.venv\Scripts\python.exe`**。
- 沒有 git（非 repo）。沒有 Docker/Postgres/Prometheus（Phase 1 刻意不做）。
- Binance 公開 REST 可連（不需金鑰）。**注意：此環境的系統時鐘在 2026 年，抓到的「最新」K 線是 2026 年資料，非錯誤。**
- 中文 print 在 console 會亂碼，跑腳本請加：`PYTHONUTF8=1 PYTHONIOENCODING=utf-8`

## 雲端環境基準（2026-08-15，★已驗證接受，新 session 直接引用不需重查★）

**背景**：本機電腦送修，改用 Claude Code on the web 的雲端 session 工作。容器是
**ephemeral** 的——每個新 session 都是重新 clone 的乾淨 repo，`.venv` 不保留。
本節記錄該輪完整驗證過程，等級比照其他「已知限制但已驗證接受」條目，讓之後
任何 session 不必重走一次查證。重建流程見 `docs/CLOUD_SETUP.md` / `./setup_cloud_env.sh`。

### 環境對照

| | 本機（送修前） | 雲端 sandbox |
|---|---|---|
| OS | Windows 11 | Linux（容器） |
| Python | 3.14.6 | **3.11.15**（預設；另有 3.10/3.12/3.13，無任何 3.14） |
| numpy | 2.5 | **2.4.6** |
| pandas | 3.0.3 | **3.0.5** |
| scikit-learn | 1.9 | 1.9.0 |
| git | 無（非 repo） | 有（repo，走 branch+PR） |

**★已定案：雲端一律用 sandbox 預設 3.11.15★**（2026-08-15 使用者拍板）。理由：
版本可信度問題已由下述逐位對照解決，剩下的純粹是操作便利性——uv 索引在此容器
只到 3.14.0rc2（RC 版是不必要的風險），apt 的 3.14.3 屬全域安裝、在 ephemeral
容器裡每個 session 都要重來，兩者都不划算。

### 驗證項目與結果（全部 PASS）

**1. Repo 完整性**：commit `27229f3`、tracked 112 檔；四個資料庫大小吻合
（chips_tw 43.7MB / klines 7.6MB / klines_tw 1.8MB / events_tw 0.04MB）。
逐項核對本檔「台股固定資料集原則」的凍結校驗和，**全部逐位相同**：
klines_tw 四檔 COUNT/SUM(close)/max_ts、events_tw dividend_result 與
short_sale_suspension、klines BTCUSDT 25300 + ETHUSDT 26000。
`chip_holding_dispersion` 0 列 = 本檔既有記載的「被 sponsor 擋、緩做」，非缺漏。

**2. 全套測試**：**379 passed, 0 warnings**，與本檔記錄的 379 完全一致，零差異需排查。

**3. 免 token 是「證明」不是「推論」**：沒有靠讀程式碼判斷，而是用 sitecustomize
把 `socket.socket.connect` / `connect_ex` / `create_connection` / `getaddrinfo`
全部替換成拋例外，再跑一次全套 → **379 passed**。任何需要即時打 API 的測試在
這個設定下都會直接炸開、不可能靜默通過。環境本身也未設 `FINMIND_TOKEN`。
`run_tw_short_baseline.py` 亦在同樣離線條件下完整跑完。
**結論：全套測試與台股 runner 都只讀已落地的凍結資料庫，雲端 session 不需任何 API token。**

**4. Python 版本差異的影響 = 實測零（position-level diff，不是推論）**：
另建第二個 venv（uv，Python 3.14.0rc2 + numpy 2.5.2，放在容器暫存區、不動系統全域）：
- 全套測試 → 379 passed
- 更強的證據：`run_tw_short_baseline.py`（全凍結快照、逐位可重現）在
  **3.11.15/numpy 2.4.6** 與 **3.14/numpy 2.5.2** 兩組環境各跑一次 →
  **輸出逐位元完全相同（`diff` 零差異）**，四檔 mean 亦與本檔「台股放空正式基準」
  記錄逐位吻合：2330 +18.7%（std 35.6%）／2603 +17.9%（54.4%）／
  8069 +9.4%（53.9%）／6446 -4.5%（44.3%）。

**5. ★雲端網路政策：資料抓取類 runner 在此環境「不能跑」★**（順帶查證，重要）：
這個容器的對外連線走 agent proxy，且**政策層直接擋掉本專案的兩個資料源**——
`api.binance.com` 與 `api.finmindtrade.com` 皆回 `CONNECT tunnel failed, 403`
（proxy status 的 `recentRelayFailures` 明確記為 `connect_rejected`／
「policy denial」）；`pypi.org`／`files.pythonhosted.org`（在 noProxy 允許清單內）
與 `github.com` 正常，所以 pip 安裝與 git push 不受影響。

推論與影響：
- **分析／回測類 runner 全部可跑**（一律讀凍結 DB，本輪已實測
  `run_tw_short_baseline.py` 完整跑完）。
- **資料抓取類 runner 在雲端一律不可用**：`run_ingest.py`（Binance）、
  `run_ingest_chips.py`／`run_ingest_tw_price.py`／`run_ingest_tw_events.py`
  （FinMind）都會在連線階段就失敗。**任何需要擴充/刷新資料集的任務，在本機
  修好之前不要排進雲端 session**，或需先請使用者調整環境網路政策。
- 附帶效果：這也讓「凍結資料集原則」在雲端被環境**強制**執行——不可能發生
  某個 runner 偷偷即時抓資料導致數字漂移。

**已知限制（誠實揭露）**：驗到的是 3.14.0rc2，**不是**本機精確的 3.14.6——
uv 在此容器的索引只到 rc2，apt 只到 3.14.3。故嚴格說法是「3.14 系列與 3.11.15
在本專案的凍結資料集上逐位相同」，不是「3.14.6 逐位相同」。考量到差異若存在
應在 numpy/pandas 層而非 CPython patch 版本層（本輪 numpy 已跨 2.4.6↔2.5.2 兩個
minor 仍逐位相同），此殘留風險判定為可接受、記錄不消除。

### 順帶發現的落差（已修）

**`requirements.txt` 漏列 `scikit-learn`**。本機 venv 早已裝好所以從未暴露；
乾淨環境照舊檔安裝後，7 個測試模組直接 collection error（`test_costs` /
`test_event_engine_risk` / `test_grid_search` / `test_liq_calibration` /
`test_ml_signal` / `test_short_risk_overlay` / `test_walk_forward`，
根因都是 `backtest/walk_forward.py` import `sklearn.model_selection.TimeSeriesSplit`）。
屬 `COLLAB.md` 的「文件與程式碼漂移」類。

**處置（2026-08-15 使用者拍板）**：不只補漏，而是把 `requirements.txt` 全部版本
（含遞移相依）**一律 pin 死**，理由與但書寫在該檔開頭註解。這是把「凍結資料集、
逐位可重現」延伸到依賴管理層——先例是 pandas 3.0 的 `to_datetime` 解析度行為
造成的 ns/us 靜默 bug（見 Phase 3 節「台股日K」）。
**numpy 無法兩邊共用同一 pin**（2.4.6 無 cp314 wheel、2.5.x 不支援 3.11）：
本檔 pin 以雲端基準為準，本機裝不起來時應本機另存對應 pin 組合並重跑逐位對照，
**不是**把 requirements.txt 改回範圍寫法。

## 怎麼跑

**雲端 session（目前的工作環境）**：容器 ephemeral，每次都要先重建 venv：
```bash
./setup_cloud_env.sh          # 建 venv + 裝 pin 死的相依 + 跑全套測試
./setup_cloud_env.sh --full   # 上述 + 凍結資料庫校驗和 + 離線（封 socket）測試
```
之後指令一律用 `.venv/bin/python`（下面的範例是本機 Windows 路徑，
雲端把 `.venv/Scripts/python.exe` 換成 `.venv/bin/python`）。
細節見 `docs/CLOUD_SETUP.md` 與上方「雲端環境基準」節。

```bash
# 全部單元測試（應 379 passed，0 warnings）
.venv/Scripts/python.exe -m pytest tests/ -q

# 端到端流程 + 績效報表（抓真實 BTCUSDT 1h）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_phase1.py

# 驗證階段：walk-forward 5 fold + lookahead 對照
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_validation.py

# 診斷：每 fold 市場狀態 + 3x3 參數網格
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_diagnosis.py

# regime filter 改良：baseline vs filtered 並排比較
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_diagnosis_regime.py

# K 線落地（upsert 冪等；--from-csv 可匯入既有 canonical CSV）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ingest.py

# 雙向策略：long-only / v2(單視窗) / A2(多尺度) × 無風控/風控近似 全對照
# 資料來源優先序：data/klines.sqlite（固定資料集）→ KLINE_CACHE_CSV → 即時抓取
# 環境變數：N_SPLITS（fold 數，預設 5）、KLINE_START / KLINE_END（時間窗裁切，UTC）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_diagnosis_bidir.py

# Phase 2 事件驅動對照：向量 vs E0(事件/訊號根收盤) vs E1(事件/次根開盤)，無風控
# 預設 = 乾淨基準窗（5-fold 3000 根）；同樣吃 N_SPLITS / KLINE_START / KLINE_END
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_phase2_event.py

# Phase 3：FinMind 台股籌碼 ingest + POC 檢查（預設 12 檔 × 4 資料集全歷史）
# FINMIND_MIN_INTERVAL 調節流間隔（預設 6.5s；少量請求可調 2）；token 可選（FINMIND_TOKEN）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 FINMIND_MIN_INTERVAL=2 .venv/Scripts/python.exe run_ingest_chips.py

# Phase 3：台股日K ingest（每檔起始日=價格∩籌碼交集起點→最新）→ klines_tw.sqlite
# + 真實日K×真實籌碼 as-of 對齊 demo（demo 展示視窗固定 2024Q1）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 FINMIND_MIN_INTERVAL=2 .venv/Scripts/python.exe run_ingest_tw_price.py

# Phase 3：籌碼閘門三方對照（baseline / chip N=1 當日 / chip N=5 平滑，4 檔全歷史）
# 全窗＋獨立雙半窗 × 15-fold；真實台股成本＋5bps 侵蝕對照＋及時性代價拆解
# 6446 標注僅作 stale 測試不入統計；N=5 只跑此一值不掃參數
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_wf_tw_chip.py

# Phase 3：foreign_net IC 診斷（3 檔 × H1/H2 獨立雙窗 × 15-fold，36 格矩陣）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ic_foreign_net.py

# ATR 強平防線校正：機制層全 12 組合（三個零重疊窗 C/D/A × 15-fold）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_atr_calibration.py --stage mech
# 結果層 E2（入圍組合；2026-07-13 實跑用 "3.0:14,2.0:21,4.0:14"）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_atr_calibration.py --stage e2 --candidates "3.0:14,2.0:21,4.0:14"

# high 觸價敏感度（close-only 簡化假設壓力測試；N=3.0/w=14 凍結，2026-07-14 結案）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_atr_calibration.py --stage high

# Δ規則一歸因：E2 槓桿上限全倉 vs 風險比例倉位（乾淨基準 + C/D/A，逐fold診斷）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_rule1_sizing.py

# short_risk_multiplier 校正（m 網格 4 點 × 乾淨基準+C/D/A；2026-07-14 結案維持 0.5）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_multiplier_calibration.py

# Phase 4 ML 訊號層第一輪（2026-07-15 判定完成：v2 續任）
# --stage smoke = 管線機制煙霧測試（窗內建特徵近似，數字不判讀）
# --stage e2   = 一次定案判定跑批（全歷史特徵 + 乾淨定錨 + C/D/A 判準）
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_ml_signal.py --stage e2

# 滑價敏感度壓力測試（2026-07-15 量測完成，觸發「另開基準討論」出口）
# 網格 {0,2,5,10,20}bp；乾淨基準+fold4追蹤、C/D/A 三窗、量級+強度雙層輸出
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_slippage_calibration.py
```

**台股固定資料集原則（2026-07-16 起，A4/A3 技術債輪建立）**：台股分析一律讀
本地落地資料，**不即時打 FinMind**（外部依賴：API 改版/收費/配額變動即斷
重現鏈，TaiwanStockPriceAdj 付費牆為先例）——K 線讀 `data/klines_tw.sqlite`、
除權息與停券日曆讀 `data/events_tw.sqlite`（`run_ingest_tw_events.py` 刷新，
upsert 冪等；快照切換後 run_tw_calendar_cover_diag 輸出逐位一致已驗證）。
**凍結校驗和（2026-07-16 快照，重跑 ingest 前後應核對）**：
- klines_tw.sqlite（symbol / COUNT / SUM(close) / min~max ts ns）：
  2330 / 3471 / 1565048.600000；2603 / 3464 / 241254.020000；
  6446 / 3007 / 811043.340000（含兩筆授權刪除的異常列後）；
  8069 / 5217 / 371298.270000；全部 max ts = 1783558800000000000（2026-07-09）
- events_tw.sqlite dividend_result（stock_id / COUNT / SUM(before)+SUM(after)）：
  2330 / 45 / 46236.270000；2603 / 18 / 2429.560000；6446 / 5 / 6361.590000；
  8069 / 14 / 2558.370000
- events_tw.sqlite short_sale_suspension（stock_id / COUNT / 範圍）：
  2330 / 45 / 2015-04-01~2026-06-05；2603 / 24；6446 / 6；8069 / 23
  （8069 max 2026-07-14 含進行中停券窗）

**固定資料集原則（2026-07-03 起）**：所有 fold 對照一律讀 `data/klines.sqlite`
（BTCUSDT 1h **25300 根，2023-08-14 04:00 → 2026-07-03 07:00 UTC，零缺口**；
2026-07-06 用 endTime 錨定回補 2023-08-14→2025-06-19 段，寫入前後對原凍結區間
做 COUNT/SUM(close) 校驗和，PASS 未被改動）。要擴充資料再跑 `run_ingest.py --limit N`
（upsert 冪等；注意它只支援「從最新往回抓」，回補更早歷史需 endTime 錨定），
但**跨版本比較必須註明資料集時間範圍**。歷史結果的精確重現：
- 5-fold 乾淨基準（3000 根）：`N_SPLITS=5 KLINE_START="2026-02-28 07:00" KLINE_END="2026-07-03 06:00"`
- 15-fold 窗A：`N_SPLITS=15 KLINE_START="2025-06-19 04:00" KLINE_END="2026-05-18 11:00"`
- 15-fold 窗B：`N_SPLITS=15 KLINE_START="2025-08-04 00:00" KLINE_END="2026-07-03 07:00"`
- 15-fold 窗C：`N_SPLITS=15 KLINE_START="2023-08-22 12:00" KLINE_END="2024-07-20 19:00"`
- 15-fold 窗D：`N_SPLITS=15 KLINE_START="2024-07-20 20:00" KLINE_END="2025-06-19 03:00"`

## 目前檔案結構（已實作）
```
量化交易/
├─ AGENTS.md, CLAUDE_2.md, HANDOFF.md, SHORT_SELLING_TASK.md, requirements.txt
├─ config/settings.py              # 環境變數讀取點（API key 一律從 env；含 FINMIND_TOKEN）
├─ data/
│  ├─ providers/binance.py         # Binance 公開 K 線，含 429/418 backoff + 分頁
│  ├─ providers/finmind.py         # 台股 FinMind adapter：籌碼 4 dataset + fetch_price
│  │                               #   （TaiwanStockPrice 日K）；主動節流 + 402 長等待
│  ├─ cleaning.py                  # 轉 canonical schema + validate（K 線[含台股日K] + 籌碼兩套）
│  ├─ storage_sqlite.py            # K 線落地（WAL；upsert 冪等；ts 以 int64 奈秒存）
│  ├─ storage_chips.py             # 台股籌碼落地（Phase 3；獨立 DB、四張表、PK 含 source）
│  ├─ klines.sqlite                # 落地資料（25300 根，2023-08-14 04:00→2026-07-03 07:00 UTC，凍結）
│  ├─ klines_tw.sqlite             # 台股日K（2026-07-10 擴充至價格∩籌碼交集全區間：
│  │                               #   2330/2603 自 2012-05、8069 自 2005-01、6446 自 2014-03，
│  │                               #   皆至 2026-07-09，共 15,161 根，interval=1d）
│  └─ chips_tw.sqlite              # 台股籌碼（POC 起用；日更活躍，與凍結的 klines 分檔）
├─ indicators/
│  ├─ technical.py                 # 向量化 SMA / Wilder RSI / ATR（因果，禁未來函數）
│  └─ features.py                  # Phase 3：籌碼→價格bar 排他式 as-of 對齊（防未來函數，
│                                  #   align_chips_to_bars；不算 alpha 指標、不抓資料）
├─ strategy/
│  ├─ base.py                      # BaseStrategy 抽象層
│  ├─ ma_rsi.py                    # MA/RSI baseline（long-only：訊號 {0,1}）★不要動★
│  ├─ ma_rsi_regime.py             # MA/RSI + 因果 regime filter ★不要動★
│  ├─ ma_rsi_bidirectional.py      # 雙向策略（預設 long_regime_window=None = v2 單視窗基準；
│  │                               #   顯式傳 300 啟用 A2 多尺度過濾，選配）
│  ├─ ma_rsi_chip.py               # MA/RSI + 單一籌碼閘門 foreign_net>0（Phase 3 訊號層驗證，
│  │                               #   long-only，不動 ma_rsi.py；見「Phase 3」節「walk-forward」）
│  └─ ml_signal.py                 # Phase 4：ML 訊號包裝（已訓練 scaler/LR/τ → {-1,0,+1}；
│                                  #   NaN 特徵列/退化模型 → 安全空手）
├─ backtest/
│  ├─ vector_engine.py             # 向量化回測，position=signal.shift(1) ★不要動★
│  │                               #   （2026-07-10 經使用者明確解除限制做過一次最小擴充：
│  │                               #   選用參數 costs 買賣分離費率；costs=None 走舊路徑逐位不變）
│  ├─ costs.py                     # 買賣分離交易成本（TradeCosts / tw_stock_costs / CRYPTO_DEFAULT；
│  │                               #   台股牌告14.25bps+賣出證交稅30bps，fee_discount 預設 1.0）
│  ├─ event_engine.py              # 事件驅動回測（Phase 2：E0/E1 + E2 風控整合完成，
│  │                               #   兩層驗收通過，見「Phase 2」節；2026-07-13 經使用者
│  │                               #   核可範圍性解鎖加 sizing_mode 規則一（預設路徑逐位
│  │                               #   不變）；2026-07-15 再度範圍性解鎖加 slippage_bps，
│  │                               #   ★預設值即為 2.0（新正式基準，非逐位不變）★——
│  │                               #   舊呼叫需明確 pin slippage_bps=0.0 才維持歷史行為，
│  │                               #   見 HANDOFF「滑價敏感度」節「基準切換執行」小節）
│  ├─ ic.py                        # Phase 3：IC 診斷核心（forward_return / compute_ic /
│  │                               #   fold_ics fold 自足切分 / summarize_folds；純分析）
│  ├─ report.py                    # 年化/Sharpe/MDD/勝率（1h 年化因子=8760）
│  ├─ walk_forward.py              # sklearn TimeSeriesSplit 多 fold
│  ├─ regime.py                    # 市場狀態分類 + rolling_trend_down_mask /
│  │                               #   multi_scale_trend_down_mask（A2 因果逐根判斷）
│  ├─ short_risk_overlay.py        # 空頭強平近似 overlay（ATR*3 + 15%，向量化）
│  ├─ liq_calibration.py           # ATR 強平防線校正機制層量測（區段抽取/觸發偵測/
│  │                               #   誤殺-保護權衡表；觸發語意與 overlay/E2 逐位一致；
│  │                               #   trigger_source="close"|"high"（high 觸價輪 2026-07-14，
│  │                               #   預設 close 逐位不變、觸線浮虧與盤中安全邊際欄）
│  └─ grid_search.py               # 參數網格 + fold regime 標註
├─ risk/manager.py                 # 風控核心（已擴充空頭規則，見下方）
├─ broker/paper.py                 # paper 模擬成交（Position.quantity 正負均支援）
├─ ai/ml_train.py                  # Phase 4：特徵矩陣（預先註冊 9 欄）＋逐 fold LR 訓練
│                                  #   （expanding+purge、τ 內部驗證、退化安全空手）
├─ tests/                          # 303 個測試全通過（0 warnings）
│  ├─ test_risk_manager.py         # 原有風控測試（倉位/熔斷/槓桿）
│  ├─ test_risk_manager_short.py   # 空頭規則一/二/三 + forced_liq 參數化（6 tests，
│  │                               #   含預設行為逐位不變的保護性回歸 sweep）
│  ├─ test_paper_broker.py         # 原有 broker 測試
│  ├─ test_paper_broker_short.py   # 新增：空頭倉位邊界案例
│  ├─ test_indicators_and_cleaning.py  # 含 ATR 基本測試
│  ├─ test_lookahead_bias.py       # 含 ATR 無未來函數驗證
│  ├─ test_vector_engine.py, test_walk_forward.py, test_regime.py
│  ├─ test_grid_search.py, test_ma_rsi_regime.py
│  ├─ test_ma_rsi_bidirectional.py # 雙向策略不變量驗證（14 tests）
│  ├─ test_event_engine.py         # 新增：事件引擎不變量（12 tests，多頭一致性/
│  │                               #   空頭理論差/無未來函數/翻向語意）
│  ├─ test_event_engine_risk.py    # 新增：E2 風控語意（9 tests，強平/熔斷/sizing/
│  │                               #   觸發bar與overlay一致 property）
│  ├─ test_finmind_provider.py     # Phase 3：mock HTTP 測 402/5xx/token 路徑（10 tests）
│  ├─ test_chip_cleaning.py        # Phase 3：籌碼 canonical 轉換＋恆等式檢查（13 tests）
│  ├─ test_chip_storage.py         # Phase 3：籌碼 DB 冪等 upsert/讀回（10 tests）
│  ├─ test_features_lookahead.py   # Phase 3：features.py 排他式 as-of 防未來函數（7 tests，
│  │                               #   含 ns/us 解析度混合回歸）
│  ├─ test_tw_price.py             # Phase 3：台股日K provider/cleaning/storage（8 tests）
│  ├─ test_ma_rsi_chip.py          # Phase 3：籌碼閘門策略 + 過濾成因拆解 + chip_window
│  │                               #   N日平滑（12 tests，含 N=1≡現行為回歸、rolling 因果性）
│  ├─ test_costs.py                # 買賣分離成本（5 tests，含對稱費率≡舊fee_bps逐位相同
│  │                               #   的保護性回歸、非對稱落正確方向、walk_forward 透傳）
│  ├─ test_ic.py                   # Phase 3：IC 診斷（10 tests，shift 方向手工數字/
│  │                               #   合成預測力 IC≈+1/反向對照 IC≈0/NaN 逐對剔除/
│  │                               #   fold 自足不跨界/彙總統計）
│  ├─ test_liq_calibration.py      # ATR 校正機制層（25 tests：區段抽取/觸發偵測/
│  │                               #   NaN跳過/誤殺分類/與 overlay 觸發bar一致性/
│  │                               #   E2 透傳零改動驗證/trigger_source high 觸價
│  │                               #   ——預設≡close 逐位回歸＋high≥close 支配性 property）
│  ├─ test_event_engine_sizing.py  # 規則一 sizing（9 tests：預設模式逐位不變/手算
│  │                               #   數量/規則二封頂/多頭不變/NaN fallback/決策bar
│  │                               #   ATR 防未來/強平損失≈預算/參數驗證）
│  ├─ test_multiplier_sizing.py    # multiplier 校正（7 tests：未封頂數量∝m/封頂數量
│  │                               #   跨m等值/觸發bar跨m不變/未封頂強平損失∝m/
│  │                               #   runner budget 參數化＝預設逐位回歸）
│  ├─ test_ml_signal.py            # Phase 4：ML 洩漏防治與不變量（9 tests：特徵因果性/
│  │                               #   purge 邊界/測試期零洩漏/訊號域+NaN 空手/τ 單調/
│  │                               #   LR+交互項可表達 AND/確定性/單類別退化）
│  ├─ test_event_engine_slippage.py # 滑價敏感度（9 tests：預設0逐位不變/買貴賣賤方向/
│  │                               #   強平單同樣套用不豁免/mark與觸發判定不受影響/負值拋錯）
│  └─ __init__.py
├─ run_phase1.py, run_validation.py, run_diagnosis.py, run_diagnosis_regime.py
├─ run_ingest.py                   # K 線落地（Binance 抓取或 CSV 匯入 → klines.sqlite）
├─ run_diagnosis_bidir.py          # long-only / v2 / A2 × 無風控/風控近似 全對照
├─ run_phase2_event.py             # Phase 2：向量 vs 事件引擎 E0/E1 對照 + fold4 拆解
├─ run_ingest_chips.py             # Phase 3：FinMind 籌碼 ingest + POC 首日檢查
├─ run_ingest_tw_price.py          # Phase 3：台股日K ingest → klines_tw.sqlite + 端到端籌碼對齊 demo
├─ run_wf_tw_chip.py               # Phase 3：籌碼特徵併入 walk-forward（baseline vs chip-gated，
│                                  #   4 檔 × 2024Q1，僅驗管線不下策略優劣結論）
├─ run_ic_foreign_net.py           # Phase 3：foreign_net IC 診斷（36 格矩陣 + 跨窗判讀）
├─ run_atr_calibration.py          # ATR 強平防線校正跑批（--stage mech 機制層全網格 /
│                                  #   --stage e2 結果層入圍組合對照 / --stage high
│                                  #   close vs high 觸價對照，2026-07-14）
├─ run_rule1_sizing.py             # Δ規則一歸因（E2 cap vs r1，逐fold 曝險/封頂/
│                                  #   強平損失預算比診斷）
├─ run_multiplier_calibration.py   # short_risk_multiplier 校正（m 網格 4 點 ×
│                                  #   乾淨+C/D/A；P1~P4 事前預期逐條對照；
│                                  #   2026-07-14 結案維持 0.5）
├─ run_ml_signal.py                # Phase 4：ML vs v2 E2 配對判定跑批（--stage smoke/e2；
│                                  #   事前判準寫 docstring；2026-07-15 判定 v2 續任）
└─ run_slippage_calibration.py     # 滑價敏感度壓力測試（{0,2,5,10,20}bp × 乾淨+C/D/A；
                                   #   量級+強度雙層+換手拆解固定呈現；2026-07-15 觸發
                                   #   「另開基準討論」出口，見 HANDOFF「滑價敏感度」節）
```
**尚未建立**（CLAUDE_2.md 有列但屬後續 phase）：`data/storage_postgres.py`、其他 market adapter（twse/us_equity）、live broker、`ai/rl_env.py`／`ai/rl_train.py`、`monitoring/`、`deployment/`。

## risk/manager.py 空頭擴充摘要（2026-06-25）

### 新增 RiskConfig 欄位
- `short_risk_multiplier: float = 0.5`（空頭風險係數）
- `max_long_leverage: Optional[float] = None`（None → 自動從 max_leverage 取）
- `max_gross_leverage: Optional[float] = None`（預留，目前多空互斥）
- 屬性：`max_short_leverage = max_long_leverage * 0.5`
- 屬性：`risk_per_trade_short = risk_per_trade * short_risk_multiplier`
- **向後相容**：`max_leverage` 欄位保留，原有測試無需修改

### 新增 OrderDecision
- `REJECTED_CONFLICTING_POSITION`（多空互斥拒絕）

### 修改方法簽名（向後相容，新參數皆有預設值）
- `position_size(entry_price, stop_price, side="long")`
- `approve_order(quantity, price, today, side="long", current_position_qty=0.0)`

### 新增方法
- `check_forced_liquidation(position_quantity, avg_price, current_price, atr_value) -> bool`
  - 只對空頭倉位（quantity < 0）生效
  - ATR 動態防線：N=3, window=14（2026-07-13 已用歷史資料校正完成，
    建議維持現值、等使用者拍板；見「ATR 強平防線校正」節）
  - 固定安全網：虧損超過 15% 必觸發
  - 與 is_circuit_broken（日內熔斷）**完全獨立**

### forced_liq 參數化（2026-07-13，ATR 校正輪）
- `RiskConfig.forced_liq_atr_n: float = 3.0`（ATR 動態防線倍率）
- `RiskConfig.forced_liq_safety_pct: float = 0.15`（固定安全網比例）
- `check_forced_liquidation` 改讀 config 欄位，預設值 = 原模組常數，
  **行為逐位不變**（保護性回歸 sweep 測試背書）；`__post_init__` 驗證
  N>0、0<pct<1。
- **event_engine 透傳實測零改動**：E2 的 N 經 `RiskManager(config=...)` 注入、
  window 經既有 `atr_series` 參數注入（全窗計算後切片），
  `backtest/event_engine.py` 一行未改，**不要動清單未破**。

## 關鍵設計與紀律（接手前務必知道）
- **防未來函數**：回測一律 `position = signal.shift(1)`。ATR 等指標因果計算（shift(1)取prev_close，rolling向後看）。
- **canonical schema**：`ts,symbol,open,high,low,close,volume,turnover,vwap,exchange,interval,source,ingested_at`。新增資料源寫 provider adapter，別動下游。
- **向量化**：禁止 `DataFrame.apply(axis=1)`，用 rolling/ewm/NumPy。
- **時間序列驗證**：只用 TimeSeriesSplit / walk-forward，禁止隨機 K-fold。
- **風控**：`risk/manager.py` 的熔斷/槓桿/ATR-N 數值不可在未經使用者確認下自動改。任何下單路徑要過風控。
- **金鑰**：一律走 `config/settings.py` 從環境變數讀，不寫死。
- **Gate 制**：未完成前一 Phase 的 DoD 不得開始下一 Phase；要跳站先提風險並取得使用者明確同意。
- **★不要動★**：`strategy/ma_rsi.py`、`backtest/vector_engine.py`、`backtest/event_engine.py`。

## 已完成的分析結論（重要，別重跑得出又忘了）
1. **MA/RSI baseline walk-forward 很不穩定**：5 fold 的 Sharpe 在 −5 ~ +5 劇烈擺盪，跨 fold std≈3.8。
2. **根因 = 市場狀態，不是參數過擬合（結論 b）**：3×3 參數網格全部 std 落在 3.68~5.66，換參數救不了；表現由 fold 屬於 trend_up/range/trend_down 決定。
3. **regime filter 的效果**：只在 trend_up 進場後跨 fold std 下降，但平均報酬被壓到接近零——用「空手避險」換穩定，沒把策略變賺錢。下跌段獲利需要**放空**。
4. fold4（trend_down）年化虧損由 −60.9% 收斂到 −19.0%；評估 regime 策略應看「年化+回撤」而非只看 Sharpe（近空手時 Sharpe 失真）。

## 雙向策略設計決策紀錄（方法論教訓，2026-06-25）

### 時間錯位問題（v1 失效原因）
- v1 空頭條件：`death_cross AND RSI >= 70 AND trend_down(rolling 120根)`
- 現象：fold4（trend_down）n_short = 0，策略等同 long-only
- 根本原因：訊號時間特性互斥
  - `trend_down` 確認需要 120 根滯後（窗口才能確認趨勢）
  - `RSI >= 70`（超買）是即時翻轉信號，在趨勢確立前就發生
  - 兩者時間窗不重疊：trend_down 確立時，RSI 早已從高位下行至低水位
  - 這不是參數問題，無法靠調整 regime_window 解決（縮短窗口會違背「視窗加大更穩定」的既有結論）

### 修正邏輯（v2）
- 新空頭條件：`death_cross AND RSI > rsi_not_oversold(30) AND trend_down`
- 語意：「趨勢確認向下（120根）+ 死亡交叉 + RSI 不在深度超賣（避免追空底部）」
- 這與 trend_down 滯後性完全相容：趨勢確立時 RSI 通常在 30~70 波動

### 三欄對照結果（v2，BTCUSDT 1h 3000根，2026-02-20→2026-06-25）

> **數字隨每次執行略有差異**：每次抓最新 3000 根，fold 邊界因此會移動。
> 以下為 2026-06-25 某次執行結果，相對差距（pp）比絕對數字可靠。

| fold | regime | long-only | 無風控 | 風控近似 | n_short | 說明 |
|------|--------|-----------|--------|----------|---------|------|
| 1 | trend_down | -54.7% | -81.2% | -73.9% | 78 | 風控輕微改善但仍差 |
| 2 | trend_up | +170.9% | +170.9% | +170.9% | 0 | 正確壓制，無影響 |
| 3 | trend_up | +123.7% | +57.4% | **+80.1%** | 23 | 風控回收 +22.7pp（仍差 -43.6pp）|
| **4** | **trend_down** | **-49.4%** | **-7.5%** | **-7.5%** | **138** | **+41.9pp，風控無影響（正確）** |
| 5 | range | -2.9% | -11.2% | -11.2% | 97 | 風控未觸發（price 未反彈 15%/3*ATR）|

**風控近似 vs 無風控的關鍵差異**：
- fold3：+22.7pp 回收（部分逆勢空頭提早強平），但仍 -43.6pp vs long-only
- fold4：0pp 差異（空頭在 trend_down 中正確獲利，沒有觸發強平條件）
- fold5：0pp 差異（range 市場漲幅不夠大，未到強平門檻）

**核心矛盾（持續存在）**：fold3/fold5 問題根源是 rolling 尺度（120根）與 fold 尺度（500根）不同步，
局部 trend_down 在整體 trend_up/range 中出現，產生逆勢空頭。
風控可以截斷「已嚴重虧損」的空頭，但不能阻止「進場就錯」的空頭發生。

### 2026-07-03 任務1驗證：上次 fold5 風控未觸發 = 正常，不是 bug

問題：上次 fold5（range，97 空bar）「風控近似」與「無風控」報酬完全相同（-11.2%）。
驗證方式：用 2026-07-03 快取資料重建上次 fold5 時間窗（06-04→06-25，掃描 24 個
截點候選，n_short=96~98 與上次 97 吻合），加計數儀器統計每個空頭區段。

結論：**全部截點下強平觸發次數 = 0，且非邏輯漏判**：
- 空頭區段內最大反彈只有 +0.86% ~ +2.90%（固定安全網門檻 15%，差一個數量級）
- 最大 ATR 倍數 +2.05 ~ +2.37（門檻 3.0，接近但未達）
- 交叉驗證：同一套邏輯在 2026-07-03 資料的 fold1/2/5 有觸發（1/1/2 次），
  且「觸發=0 的 fold 報酬與無風控完全相等」——機制正常，報酬相等 ⟺ 觸發為 0。

### 2026-07-03 A2 多尺度 regime 一致性過濾：實作完成，結果好壞參半

實作：空頭觸發需 120 根（regime_window）與 300 根（long_regime_window，
當時標注待校正；2026-07-06 A2 定案不採用後不再校正）雙視窗同判 trend_down。
多頭不動。
`long_regime_window=None` = v2 單視窗對照模式。

**當前 fold 結構的三欄對照**（BTCUSDT 1h 3000根，02-28→07-03，快取固定資料集；
fold 邊界與上次不同，regime 標籤已漂移）：

| fold | regime | long-only | v2無風控 | v2風控 | A2無風控 | A2風控 | v2空bar | A2空bar |
|---|---|---|---|---|---|---|---|---|
| 1 | range | -3.2% | -50.7% | -31.3% | -8.7% | -8.7% | 69 | 33 |
| 2 | trend_up | +14.1% | -19.7% | -8.2% | +14.1% | +14.1% | 23 | 0 |
| 3 | range | +41.6% | +69.4% | +69.4% | +7.4% | +15.5% | 68 | 42 |
| 4 | trend_down | -38.9% | +17.2% | +17.2% | +26.5% | +26.5% | 140 | 125 |
| 5 | trend_down | +11.2% | -20.8% | +2.2% | -53.4% | -39.8% | 84 | 47 |

mean/std（年化）：long-only +4.9%/29.4%；v2風控 +9.9%/37.7%；A2風控 +1.5%/26.4%。
A2 降低跨 fold std，但平均報酬也降。

**對上次 fold 時間窗的驗收**（重建近似，簽名層 + 該窗回測）：

| 上次時間窗 | long-only | v2無風控 | A2無風控 | v2空bar | A2空bar | 判定 |
|---|---|---|---|---|---|---|
| old fold3（trend_up）| +99.1% | +40.5% | +99.1% | 23 | 0 | ✅ 逆勢空單 100% 濾除，落差歸零 |
| old fold4（trend_down）| -48.8% | -20.9% | -45.5% | 130 | 89 | ❗誤殺：濾掉 41 根多為下跌起點的賺錢空單，+27.9pp 優勢縮至 +3.3pp |
| old fold5（range）| -2.9% | +8.2% | +25.7% | 98 | 78 | ⚠️ n_short 只降 20%（未大幅下降）；報酬反而改善 |

（注意：重建窗的絕對數字與上次執行不同——資料起點、視窗邊界各差數小時，
年化放大了小差異；相對比較才有意義。）

**結論與教訓（完整版見 AGENTS.md）**：
1. A2 確實消滅「短期下跌 + 長期上漲」的逆勢空頭（old fold3、本次 fold1/2）。
2. 但長視窗加深滯後，兩種代價已實證：
   - 誤殺下跌起點的早期空單（old fold4）——過濾不是免費的；
   - 長視窗殘留記憶：前段下跌讓後續 range 仍判 trend_down（old fold5 只濾 20%），
     且留下的空單偏「趨勢尾端接近反轉」（本次 fold5 A2 比 v2 更差 -32.6pp）。
3. 300 是起點值；**A2 預設開啟是依任務規格實作，最終去留/校正等使用者決定**。
   **（歷史記錄；2026-07-06 已定案：A2 不採用、300 不再校正，見「獨立時間窗
   驗證」節。）**

### 2026-07-03 落地資料乾淨基準（★之後所有比較以這張表為準★）

> 上面兩張表（快取資料 + 重建窗）是落地前的過渡驗證，數字含漂移雜訊，僅供
> 方向性參考。本節數字來自凍結的 `data/klines.sqlite`（02-28 07:00 → 07-03 06:00
> UTC，3000 根），可精確複驗：`run_diagnosis_bidir.py` 讀 DB 重跑即得逐位相同結果。
>
> **2026-07-06 fold5 修正**：本表最初版本的 fold5 與現行 DB 差 ~0.3-0.7pp——
> 07-03 初次落地（3000 根）時窗尾最後一根（07-03 06:00）抓取當下**尚未完結**，
> 同日稍後擴充到 9100 根時冪等 upsert 把它替換成完整 K 線。僅 fold5（含該根）
> 受影響，fold1-4 與所有空bar數逐位不變，相對結論不變。下表已更新為現行 DB
> 的可重現數字（教訓見 AGENTS.md「未完成 K 線與資料凍結」）。

| fold | regime | long-only | v2無風控 | v2風控 | A2無風控 | A2風控 | v2空bar | A2空bar |
|---|---|---|---|---|---|---|---|---|
| 1 | range | -11.2% | -55.0% | -37.3% | -16.2% | -16.2% | 66 | 33 |
| 2 | trend_up | +30.4% | -8.2% | +5.0% | +30.4% | +30.4% | 23 | 0 |
| 3 | range | +18.1% | +41.3% | +41.3% | -10.4% | -3.7% | 68 | 42 |
| 4 | trend_down | -12.8% | +67.3% | +67.3% | +80.7% | +80.7% | 140 | 125 |
| 5 | trend_down | +12.7% | -19.8% | +3.6% | -52.8% | -39.0% | 84 | 47 |

mean/std（年化）：long-only +7.4%/18.9%；v2風控 **+16.0%/40.0%**；A2風控 +10.4%/46.6%。

**乾淨基準下的 A2 評估（與快取版的差異要注意）**：
- 每 fold 方向與快取版一致：fold1/2 A2 大幅改善（+38.8pp/+38.6pp）、fold4 無誤殺
  且略優（+13.3pp）、fold3/fold5 明顯惡化（-51.7pp/-33.2pp）。
- **但彙總結論反轉**：快取版顯示「A2 降 std」；乾淨基準下 A2風控 的 mean（+10.5% vs
  +16.1%）與 std（46.5% vs 39.9%）**皆劣於 v2風控**——3 小時的資料漂移就足以翻轉
  彙總層結論，5-fold 彙總統計在此資料量下極不穩健，勿以單一資料集彙總數字定案。
- fold3 現象值得記錄：range 內的 rolling trend_down 空單**不一定是逆勢虧損單**
  （此 fold v2 空單賺 +23.2pp，被 A2 濾掉反而變差）——「range 內假性 trend_down」
  同時包含賺錢與虧錢子段，A2 的 AND 過濾無法區分兩者。
- **A2 去留未定案（使用者指示；此為 2026-07-03 當時狀態，2026-07-06 已定案
  不採用，見「獨立時間窗驗證」節）**。當前績效基準 = v2 + 風控近似。策略 dataclass
  預設已改回 `long_regime_window=None`（= v2，2026-07-03 任務1）；跑 A2 對照需顯式傳 300。

### 15-fold 擴大驗證（2026-07-03 執行；2026-07-06 依下述指令重跑，數字逐位重現）

目的（任務3：驗證「驗證方法本身」）：把資料從 3000 根擴到 8000 根、fold 從 5 擴到
15，並用兩個錯位約 6.5 週的時間窗各跑一次，檢查 v2/A2 的相對結論是否還會因資料
位移翻轉。單一 symbol（BTCUSDT），刻意不加幣種以隔離「樣本量」單一變因。
重現指令見「怎麼跑」節的 15-fold 窗A / 窗B（讀凍結 DB，結果確定性）。

**彙總 mean/std（年化，15 fold）**：

| 窗 | 範圍 | long-only | v2風控 | A2風控 | A2-v2 |
|---|---|---|---|---|---|
| A | 2025-06-19 04:00 → 2026-05-18 11:00 | -2.7%/92.4% | -1.3%/87.8% | +7.7%/103.0% | **+9.0pp** |
| B | 2025-08-04 00:00 → 2026-07-03 07:00 | +9.6%/84.2% | +12.3%/104.0% | +11.9%/89.6% | **-0.4pp** |

**同 regime 分組的 A2-v2（無風控，組內 mean±std，年化 pp）**：

| regime | 窗A | 窗B | 跨窗符號 |
|---|---|---|---|
| trend_up | +78.4%±129.6%（n=3） | +25.1%±20.1%（n=2） | **恆 ≥ 0（5/5 fold）** |
| trend_down | +27.3%±29.3%（n=5） | +3.8%±26.7%（n=5） | 同號但幅度不穩 |
| range | -5.1%±24.3%（n=7） | +7.0%±71.7%（n=8） | **翻轉（純雜訊）** |

**結論（完整教訓見 AGENTS.md「彙總統計的檢定力邊界」）**：
1. A2-v2 彙總差異在兩窗間仍翻轉符號（+9.0pp / -0.4pp），但兩窗一致支持
   「**A2 ≈ v2，差異落在雜訊內**」——fold 數變多讓「無法區分」本身變穩定，
   這與 5-fold 時代「結論隨 3 小時位移翻轉」是質的不同。
2. 唯一跨窗符號穩定的模式：**trend_up fold 中 A2-v2 恆 ≥ 0**（長視窗把上漲段
   的逆勢空單全數濾除，兩窗 5 個 trend_up fold 無一例外）。
   **（2026-07-06 更新：此模式已被完全獨立時間窗的反例推翻，且 A/B 重疊分析
   顯示這 5 個 fold 實際只有 3 個獨立樣本——見下節「獨立時間窗驗證」。）**
3. range fold 的 A2 效果兩窗符號相反，純屬雜訊；trend_down 組同號但組內擺盪大。
4. 同 regime 組內 std 約 20~130pp 年化：**1h 尺度上此策略族的訊號雜訊比本來就
   高**——「還是大幅擺盪」正是任務3預期要回答的問題，答案是「資料量翻倍後彙總
   層級穩了，但 fold 層級的擺盪是訊號本質，不是樣本量問題」。這是有效結論。

### 獨立時間窗驗證（2026-07-06）：trend_up「恆≥0」模式被反例推翻，條件化A2 否決

**動機**：上節發現 trend_up fold 中 A2-v2 恆 ≥ 0（5/5），考慮實作「條件化A2」
（只在長視窗判 trend_up 時擋空單）。但實作前先驗證兩件事（使用者指示）：
(1) 窗A/B 是否重疊、5 個 trend_up fold 有幾個真正獨立；(2) 模式在完全獨立的
新時間窗是否再現。

**重疊分析結果（使用者的懷疑正確）**：
- 窗A 與窗B 整體重疊 **6900/8000 根 = 86.2%**（只是 fold 邊界錯位，不是獨立樣本）。
- 5 個 trend_up fold 中：A9↔B7 重疊 80%（同一段 2025-12下旬~2026-01中）、
  A14↔B12 重疊 80%（同一段 2026-04）、僅 A12（2026-02下旬~03中）獨立。
- **實際獨立 trend_up 樣本只有 3 段，其中 2 段被兩窗各計一次**——「5 個 fold
  無一例外」的表面樣本數高估了證據力。

**新窗設計**：回補歷史後取兩個與 A/B、也彼此**零重疊**的 8000 根窗（見「固定
資料集原則」的窗C/D 指令）。窗C = 2023-08-22 12:00 → 2024-07-20 19:00；
窗D = 2024-07-20 20:00 → 2025-06-19 03:00。單一 symbol 不變。

**結果：出現明確反例**。新增 8 個 trend_up fold（C:4、D:4，全部獨立）中 7 個
A2-v2 ≥ 0，但 **窗C fold9（2024-02-26→03-17，BTC 破前高的 trend_up 段）
A2-v2 = -44.5pp**：v2 的 28 根空單 bar 相對 long-only **賺 +44.5pp**（抓到急漲
中的深回檔），被 A2 長視窗全數濾除後優勢歸零。這與乾淨基準 fold3（range 內
賺錢空單被 A2 誤殺）同型：**「上漲/盤整大勢中的假性 trend_down 空單」同時包含
賺錢與虧錢子段，長視窗過濾無法區分**，trend_up 段也不例外。

**彙總對照（年化 mean/std，15 fold；2023-2025 大多頭段數字絕對值極大，只看相對）**：
- 窗C：long-only +151.0%/297.5%；v2風控 +131.4%/278.4%；A2風控 +139.8%/299.7%（A2-v2 +8.4pp）
- 窗D：long-only +388.5%/1374.5%；v2風控 +458.7%/1682.2%；A2風控 +399.6%/1372.9%（A2-v2 -59.1pp）
- 彙總層 A2-v2 符號在 C/D 間再度翻轉——四窗一致支持「A2 ≈ v2，差異在雜訊內」。
- 另一觀察：2023-2025 大多頭中 300 根長視窗幾乎從不判 trend_down（C/D 多數
  fold A2空bar=0），A2 在此類市場近似等於 long-only。

**決策（依使用者預先設定的分岔）**：反例出現 → **條件化A2 不實作**；v2 + 風控
近似續任基準。教訓已記入 AGENTS.md（重疊窗非獨立樣本；「恆≥0」修正為
「多數成立但存在大反例」）。下一步轉 C（event_engine）或 D（ATR 校正），等使用者選。

### 風控近似的方法論限制（必讀）

1. **這是近似，不是精確模擬**：進場價用「position 第一根的前一根收盤」近似，非真實滑點成交價
2. **再進場時機簡化**：強平後等下一個策略訊號，不是 event-driven 的精確處理
3. **所有回測報酬都未含保證金機制或真實滑點**，只適合相對比較，不是真實可預期績效
4. **long-only / regime-filter 的歷史數字也未套用任何風控**，比較基準同樣是「理想化」條件

完整精確驗證需要 `backtest/event_engine.py`（Phase 2 正式工作）。

## Phase 2：事件驅動回測（進行中，2026-07-06）

> **★數字基準 = 0bp（歷史快照，2026-07-15 標注）★**：本節與
> `run_phase2_event.py` 的所有數字產生於 `slippage_bps` 參數存在前
> （隱含零滑價）；`slippage_bps` 於 2026-07-15 新增且正式基準已切換為
> 2bp（見「滑價敏感度」節），`run_phase2_event.py` 已明確 pin
> `slippage_bps=0.0`，保住本節數字逐位可重現——**不會**因新預設值變動，
> 但也**不代表** 2bp 基準下重跑會得到相同數字。日後若需 2bp 基準下的
> Phase 2 對照，需另開任務重跑。

設計已於 2026-07-06 經使用者確認（決策①③④⑤⑥），分兩步實作。
**E0/E1、E2 風控整合、兩層驗收、完整跑批與 Δ成分歸因總表全部完成**
（`run_phase2_event.py` 一鍵重現全部數字）。

**已完成**：`backtest/event_engine.py`（MarketEvent→SignalEvent→OrderEvent→
FillEvent；直接呼叫 broker/paper.py 的 market_order，paper.py 未改；翻向先平
後開；最後一根不產生新訂單）＋ `tests/test_event_engine.py`（12 tests）＋
`run_phase2_event.py` 對照腳本。測試 135 → 147 passed。

**E0/E1 對照結果（乾淨基準 5-fold）**：
1. **多頭完美對齊**：long-only 全部 fold E0−向量 = 0.0pp（引擎正確性在真實
   資料驗證通過；fee=0 時合成資料逐位一致有測試背書）。
2. **成交時點在 1h 尺度無感**：E1（次根開盤成交）− E0（訊號根收盤成交）
   全部 fold ≈ 0.0pp（加密 1h 無跳空，open[t+1] ≈ close[t]）。
3. **★ fold4 拆解（引用歷史空頭數字必讀）★**：
   - v2：向量 +67.3% → E0 固定倉位 **+85.7%**（E0−向量 = **+18.4pp**，佔 +27%）
   - A2：向量 +80.7% → E0 固定倉位 **+100.2%**（E0−向量 = **+19.5pp**，佔 +24%）
   - 方向與直覺相反：向量的 -1 是「逐根再平衡的反向曝險」，在 fold4 這種
     **震盪下跌**路徑有波動拖累，**低估**了固定數量空單的獲利；只有單向
     直線趨勢中再平衡才佔優（凸性；兩個方向都有測試以精確公式驗證）。
   - 含義：過去所有向量版空頭報酬數字（含 fold4 +67.3%/+80.7%）帶有
     「再平衡假設」的路徑相依偏差，**幅度可達雙位數 pp、方向不定**；
     引用時必須註明來自向量近似，精確數字以事件引擎為準。

**★ E2 範圍限制（設計決策⑥，誤用警告）★**：E2 的倉位大小 = 「該方向槓桿
上限內全倉」（多 1.0x、空 0.5x=規則二）。**規則一（risk_per_trade 比例倉位）
在 E2 不生效**，因為現行策略沒有明確停損價定義，`position_size()` 無從呼叫。
不要拿 E2 的倉位假設與規則一的邏輯做比較；規則一的整合等策略有停損定義後
另開任務。另注意：E2 空頭名目 0.5x 是規則二的真實效果，與向量版隱含 1.0x
對照時屬預期差異，不是 bug。
**（2026-07-13 更新：ATR N=3/w=14 定案後停損定義已備，規則一已以
`sizing_mode="risk_per_trade"` 選配接入，預設模式仍為本節所述槓桿上限全倉、
逐位不變——見「規則一 sizing 接入 E2」節；決策⑥的「不生效」自此僅適用
預設模式。）**

**E2 兩層驗收結果（2026-07-06，乾淨基準 5-fold，v2/A2）**：

*第一層（機制正確性）*：強平觸發 bar 集合，overlay vs 事件引擎（同觸發定義
close[t]、同進場價近似、全窗 ATR 切片）**10/10 fold 完全一致**（v2 觸發於
fold1/2/5 共 4 次、A2 於 fold3/5 共 3 次，時間戳逐一相同）。機制層無 bug。

*第二層（報酬差異歸因，C1向量+近似 → C2+倉位模型 → C3a+sizing → C3+熔斷）*：

| 成分 | 量級（年化 pp） | 說明 |
|---|---|---|
| Δ倉位模型（C2−C1） | **-18.0 ~ +19.5** | 路徑相依、正負皆有：fold4（無觸發、震盪下跌）+18~20；fold5（有觸發）-11~-18，其中觸發根損益歸屬（E2 承受觸發根反彈、overlay 隱含豁免）貢獻總報酬 ~1.3% |
| Δsizing 規則二（C3a−C2） | **-67.8 ~ +25.4** | 最大成分：空頭曝險減半 → 空頭賺的 fold 獲利大減（fold4 v2 -58/A2 -68），空頭虧的 fold 損失減半（fold1/5 為正） |
| Δ熔斷（C3−C3a） | **全部 0.0** | fold4 有 2 個熔斷日但當日無開倉嘗試、拒單 0。**本批次乾淨基準未觸發熔斷情境，不代表熔斷機制無效，只是這批 fold 的市況沒有踩到門檻**（機制本身有單元測試覆蓋） |

**解讀紀律（使用者 2026-07-06 提醒，重要）**：比較 E2 與風控近似版時，
**不可用「最終報酬接近程度」判斷一致性**——總差 C3−C1（-48.3 ~ +13.9pp）
幾乎由 sizing 規則二主導，屬規則的真實效果；機制一致性只看第一層觸發 bar
集合。若未來觸發 bar 對不上才是要查的 bug。

**完整跑批（2026-07-06，乾淨基準 5-fold；E2 = next_open + RiskManager 預設）**：

| 策略 | 向量 mean/std | E2 mean/std | 說明 |
|---|---|---|---|
| long-only | +7.4%/18.9% | +7.5%/18.9% | E2−向量全 fold ≈ 0（fold4 熔斷 1 日但無開倉嘗試） |
| v2 | +5.1%/49.0% | **+7.8%/25.0%** | 空頭曝險 0.5x → 跨 fold std 近乎減半 |
| A2 | +6.3%/51.0% | +5.7%/25.7% | 同上 |

Δ成交時點（E2 next_open − C3 signal_close）全 fold 0.0——1h 尺度成交時點
無感（第一步 E1≈E0 的再確認）。

**★ Δ成分歸因總表（全 5 fold，v2/A2）——決定「之後看哪個數字」的證據 ★**

| 平均\|Δ\|（年化 pp） | Δ倉位模型 | Δsizing規則二 | Δ熔斷 |
|---|---|---|---|
| v2 | 10.6 | **22.9** | 0.0 |
| A2 | 7.1 | **21.6** | 0.0 |

- 主成分分佈：10 個 fold-案例中 **8 個 sizing 為最大成分**（1 個無空頭全零；
  唯一例外 v2 fold5，倉位模型 -18.0pp 略大於 sizing +13.2pp）。
- fold4 的 sizing 量級（v2 -58.2 / A2 -67.8pp）是極端值（該 fold 空頭 bar
  最多且獲利最大），但「sizing 主導」是普遍模式，不是 fold4 特例。

**★ 引用紀律（2026-07-06 起生效）★**：過去引用的向量版**空頭**報酬數字，
其量級主要由**曝險倍數差異**決定（向量隱含 1.0x vs 風控規則二 0.5x），
其次是倉位模型路徑差異，**都不是訊號品質差異**。任何「這個策略／regime
過濾／A2 到底值不值得」的評估，之後一律以 **E2 數字**為準，不要再用向量版
數字做判斷。向量引擎保留用途：訊號層快速迭代與同曝險假設下的相對比較。

**A2 的 E2 複查已結案棄做（2026-07-07 使用者決定）**：A2 定案經兩種獨立計算
方法交叉驗證——向量化（15-fold × 4 個獨立時間窗）與事件驅動 E2（乾淨基準
5-fold，排序 v2 +7.8% > A2 +5.7%、std 幾乎相同）——結論一致指向 v2 略優。
正式棄用複查；A2 程式碼與測試保留供未來參考，不再視為待辦事項。

## Phase 3：台股籌碼資料（進行中，2026-07-07）

使用者確認的方向：(1) 先 FinMind POC，之後評估 TWSE/TPEx 官方 OpenAPI；
(2) 籌碼**獨立 canonical schema**（每資料集一張表、date 用 ISO 字串、PK 含
source），不塞 K 線表；兩條資料流只在 `indicators/features.py` 對齊合併；
(3) 12 檔代表股（清單見 run_ingest_chips.py 的 POC_STOCKS，含上櫃 2 檔
＋小型股 1723）。

**已完成（2026-07-07）**：providers/finmind.py（主動節流 6.5s≈550req/hr +
402 長等待；402=配額用盡非 429）、cleaning.py 籌碼區段（四個 to_canonical +
check_margin_balance_identity 診斷函式）、storage_chips.py（chips_tw.sqlite
獨立 DB、WAL、冪等 upsert）、run_ingest_chips.py（ingest + POC 首日檢查）、
33 個新測試（合計 189 passed）。

**POC 首抓結果（2026-07-07；四項決定已於同日結案，見下方「決定落地」）**：
1. **融資恆等式 100% 成立**：2330 全歷史 6,274 列（2001→2026）
   `balance_today = yesterday + buy - sell - cash_repayment` 違反 0 筆。
   → **決定：納入 finmind_margin_to_canonical 驗證**，違反即拋錯（不靜默入庫）。
2. **融資融券單位確認為「張」**：2330 margin_limit=6,483,092 恰等於
   發行股數 25,932,370,067 / 4 / 1000（融資限額=股本 25%）。
3. **股權分散表（TaiwanStockHoldingSharesPer）免費層不可用**：12 檔全數
   HTTP 400「Your level is free」（官方文件未標注，實測 sponsor 限定）。
   → **決定：緩做**（不走 FinMind 付費贊助）。之後若需週頻股權分散資料，
   **優先評估 TDCC opendata（官方免費）**，不走 FinMind 付費贊助。
4. **TPEx 哨兵值 -1000000**：8069/5483 融資券早期資料在 ShortSaleLimit
   （2003~2005-10，7/15 筆）與 OffsetLoanAndShort（至 2008-09，287/295 筆）
   用 -1000000 表「無資料」，核心買賣/餘額欄位乾淨。
   → **決定：哨兵值轉 NULL（僅限 margin_limit/short_limit/offset_loan_and_short
   三欄，schema 改 nullable Int64），並加保護性斷言——轉換後任何欄位殘留負值
   一律拋錯（新哨兵值或哨兵出現在非預期核心欄位都會被攔下，不靜默略過）。**
5. **法人類別實測 6 種**（設計假設 5 種）：多了 legacy 的 `Dealer` 彙總類
   （10,786 列，2012-05 前舊制）——長格式 schema 無痛吸收，驗證了設計選擇。
6. **法人買賣超實際起始日與文件不符**：TWSE 個股從 2012-05-02 起（文件稱
   2005-01），TPEx 反而從 2005-01 起（8069/5483 各 2 萬列）。
7. **TPEx 覆蓋確認**：institutional/shareholding 完整；margin 哨兵處理後
   完整入庫；dispersion 被 sponsor 擋（緩做）。
8. **★ 6446 藥華藥 institutional 資料在 2024-01-25 後斷尾**（停在該日、
   僅 9,714 列），margin 亦只從 2023-07-28 起（712 列），FinMind 標記
   type=tpex——**疑似 FinMind 端資料品質問題，非本專案抓取邏輯錯誤**。
   使用者決定：**保留現況不換股**，作為之後設計資料品質監控/告警機制時的
   已知測試案例。
9. 股權分散表公布時點（使用者指定記錄項）：因 sponsor 限制**無法觀測**，
   留待資料源（TDCC opendata）決定後補記。

**決定落地與重跑驗證（2026-07-07）**：
- cleaning.py `finmind_margin_to_canonical` 加三道防線：哨兵→NULL、
  保護性非負斷言、恆等式強制驗證；`check_margin_balance_identity` 保留為
  獨立診斷函式。storage_chips.py 的 chip_margin 三欄改 nullable Int64，
  save_chips 補 pd.NA→None 轉換。
- 測試 189 → **195 passed**（新增哨兵轉 NULL、核心欄殘留負值拋錯、
  新哨兵值拋錯、恆等式違反拋錯、nullable 落地往返）。
- **chip_margin 全表重建（DROP 舊 NOT NULL 表 → nullable 重建）並重抓 12 檔，
  0 失敗**：8069（4,650 列，short_limit NULL 7 / offset NULL 287）、
  5483（4,658 列，NULL 15 / 295）現已入庫且恆等式 0 違反；10 檔 TWSE 在
  恆等式強制驗證下全數通過。institutional/shareholding 沿用首抓（未受影響）。

### indicators/features.py：日頻籌碼安全對齊到價格 bar（2026-07-08，已完成）

**模組邊界（單一職責）**：features.py 只做「把日頻籌碼安全對齊到一個價格 bar
時間軸、產出特徵矩陣」。不抓資料、不算 alpha 指標、不碰回測（特徵層邊界）。
公開介面 `align_chips_to_bars(price_bars, chip_frames, session_tz="Asia/Taipei",
max_age_days=None)`：
- 輸入 price_bars（單一 symbol、index 為 tz-aware datetime，頻率不限，主時間軸由
  呼叫端提供）、chip_frames（{來源名: 籌碼 canonical DataFrame}）。
- 輸出與 price_bars.index 逐列對齊的特徵矩陣：特徵欄 + 每來源的
  `<src>_asof_date`（實際採用的籌碼交易日）與 `<src>_age_days`（新鮮度）；
  設 max_age_days 時另加 `<src>_stale`。

**核心機制：排他式 as-of join 防未來函數**。用
`pd.merge_asof(direction="backward", allow_exact_matches=False)` 對齊，
`allow_exact_matches=False` 就是位移本身——交易日 D 的 bar 只能拿到籌碼交易日
T < D（嚴格早於）的最近一筆，永遠拿不到 chip[T]（T 日收盤後 20:00/21:00 才公布）。
位移變成 merge_asof 參數，取代自己寫「日期 +1」，可測可稽核。**交易日在 session_tz
當地時間定義**（非 UTC）：bar 時間戳先 tz_convert 到當地、normalize 到當地午夜當
對齊鍵——正規化到午夜是必要的，否則 chip[D] 午夜 < D 日盤中 bar 會洩漏。

**缺值/舊值標注**：as-of 天生 ffill 帶著走最後一筆籌碼，用 age_days（bar 當地日曆日
− asof_date）標注新鮮度：正常交易日 age=1、跨週末 age=3、農曆長假可 >3。存量變數
帶過來多天是誠實揭露、不強制歸零。設 max_age_days 時超標轉 NaN + `_stale`；不設
時只標注不砍。首個籌碼日之前的 bar 籌碼欄與 age 皆 NaN。

**20:00 vs 21:00 保守簡化（明確記錄）**：法人 20:00、融資融券/外資持股 21:00 略異，
但都在台股 13:30 收盤後，本輪一視同仁用排他式 as-of 處理（對日/1h K 線無影響）。
之後若要對齊分鐘級 K 線，才需逐資料集精確公布時間戳機制（未來工作）。

**本輪最小特徵集（YAGNI）**：流量型 `foreign_net`、`invest_trust_net`（法人長表 pivot
net by investor_type）；存量型 `margin_balance_today`、`short_balance_today`、
`foreign_shares_ratio`。真正的 alpha 特徵工程（動量/z-score/比率）不在本輪。

**測試 `tests/test_features_lookahead.py`（6 tests 全綠，總測試 195 → 201 passed）**：
1. 排他式位移/對抗預測：造 chip[T]=當日漲跌方向的完美同日預測子，正確版 T 日 bar
   拿 chip[T-1]（去相關）、bug 版（allow_exact_matches=True）拿 chip[T]（相關 1.0 洩漏）。
2. 未來截斷：截掉 C 後的籌碼重算，asof≤C 的 bar 特徵列逐位不變。
3. 盤中廣播：1h bar 同一日共用同一 asof、D 日 bar 永不帶 chip[D]（用寬表順帶覆蓋存量欄）。
4. 新鮮度/過期：斷尾後 age 超標轉 NaN + `_stale`、asof 凍結（模擬 6446 2024-01-25 斷尾）。
5. 時區邊界：當地 23:00（T 日）bar 看不到 chip[T]；用「UTC 前一日、當地隔日」bar 證明
   對齊用當地日曆日非 UTC 日。
6. 連續假期跳空：週一第一根 bar age=3（跳過週末）、正常日 age=1、農曆式長假 age>3。

**真實資料驗證（run_ingest_chips.py 落地的 12 檔）**：
- 2330 台積電三來源（instit/margin/shareholding）對齊 2026-06-24→07-07 日頻台北 bar
  軸：每根 asof 嚴格早於當日、週一 age=3、週間 age=1；foreign_net/invest_trust_net/
  margin_balance_today(張)/short_balance_today/foreign_shares_ratio(~69.7%) 皆正常。
- 6446 藥華藥 institutional 真實斷尾（停在 2024-01-25）：max_age_days=7 下，2024-01-25
  當日 bar asof=2024-01-24（永不同日）、01-26 bar 起 asof 凍結於 2024-01-25、age 遞增，
  age>7（2024-02-02 起）13 列 foreign_net 轉 NaN + stale=True。斷尾被 age 機制正確攔下。
- 註（此輪已補上）：上段 features.py 驗證當時用「合成台北 bar 軸 × 真實籌碼」，因
  TaiwanStockPrice 尚未接。**下面「台股日K」節已改用真實 TWSE/TPEx 日K bar 軸重做
  端到端對齊，並落 repo（run_ingest_tw_price.py）。**

**範圍外（記為未來工作）**：真實 alpha 特徵工程、多 symbol、每資料集獨立公布時間戳、
官方 TWSE/TPEx OpenAPI 的獨立 provider（POC 用 FinMind，比照籌碼延後評估）。

### 台股日K（TWSE/TPEx 價格）POC：價格＋籌碼首次真實端到端對齊（2026-07-08，已完成）

**ts 語意跨市場定案（重要，寫進 CLAUDE_2.md 硬性規範）**：canonical `ts` **統一代表
bar 開盤時間**。實證 Binance 現有落地 ts = open_time（整點、相鄰 1h bar 差 1.0h）；
故台股日K 錨定當地 **09:00 Asia/Taipei 開盤**（存對應 UTC、interval="1d"）。理由：ts
語意在不同 market 間必須一致，否則 vector_engine/walk_forward 等跨市場共用邏輯會靜默
處理到語意不一致的時間戳，validate_canonical 抓不到（只驗型別/遞增/不重複）。

**新增/改動**：
- `providers/finmind.py` 加 `fetch_price(stock_id, start, end, exchange)` → `TaiwanStockPrice`
  （上市/上櫃/興櫃同表；exchange 非 API 欄位，由呼叫端帶入 attrs 供 cleaning 填）。
- `cleaning.py` 加 `finmind_price_to_canonical` → K 線 canonical。欄名陷阱已處理：
  high←max、low←min、turnover←**Trading_money**（成交金額；**Trading_turnover 是成交
  筆數、不是金額**，同 spread 一併丟棄）；volume←Trading_Volume(股)、vwap=turnover/volume。
- 落地重用 `storage_sqlite.connect("data/klines_tw.sqlite")`（不進凍結的 BTC klines.sqlite）。
- `run_ingest_tw_price.py`：4 檔（2330/2603 TWSE、8069/6446 TPEx）× 2024-01-01→03-31，
  各 56 根日K 落地，接著跑真實日K bar 軸 × 真實籌碼的 align_chips_to_bars demo。

**★ features.py 修掉一個真實資料才會踩到的 bug（ns/us 解析度）★**：storage 以 int64
奈秒往返 → bar 軸是 `datetime64[ns]`，而籌碼 date 是 ISO 字串、pandas 3.0 的
`to_datetime` 解析成 `datetime64[us]`，兩邊 merge_asof join key 解析度不符 → `MergeError`。
既有 6 項測試漏掉是因為合成 bar 軸剛好也是 us（同解析度）。修正：`_session_dates` 與
`_align_source` 右表 key 都顯式 `as_unit("ns")`。補測試 7（ns bar × us 籌碼日期回歸，
先斷言前置解析度差異再驗對齊）。

**端到端真實資料結果**：
- 2330 正常對齊：asof 恆嚴格早於當日（排他式 PASS）；週間 age=1、週一 age=3。**真實
  資料自然踩到國定假日**：2024-01-02 首根 age=4（跨 2024-01-01 元旦休市＋週末）——
  age 純日曆算術，國定假日與週末走同一 code path，**features.py 無需為國定假日特殊處理**
  （這正是上輪只模擬週末、本輪要用真實資料檢驗的點）。
- **6446 分歧點（本輪最有價值一格）**：institutional 真實斷尾停在 2024-01-25。max_age_days=7
  下 2024-01-25 bar asof=2024-01-24（永不同日）、01-26 起 asof 凍結於 2024-01-25、age 遞增
  （1→4→5→6→7→8→…最大 64）、age>7（2024-02-02 起）foreign_net 轉 NaN + stale=True 共 33 列。
  斷尾被 age/stale 機制在**真實**資料上正確攔下（先前只在合成資料驗過）。

**範圍外（此 POC 未做，記為未來工作）**：TW 日K 全量/多檔擴充與凍結、TaiwanStockKBar
分鐘級（需 sponsor）、真實 alpha 特徵工程。（「併入回測/walk-forward 跑一輪」已於
下一節完成，不再是範圍外項目。）

### 籌碼特徵併入 walk-forward 一輪：訊號層驗證完成，不追 alpha（2026-07-08，已完成）

**目的**：驗證管線最後一段——「把籌碼特徵接進現有策略/回測骨架」——順不順、
及早抓角落 bug。**不是**在找 alpha 或下策略優劣結論（見下方「立場」）。

**新增檔案**：
- `strategy/ma_rsi_chip.py`：`MaRsiChipStrategy` + `decompose_chip_filtering`。
- `run_wf_tw_chip.py`：4 檔（2330/2603 TWSE、8069/6446 TPEx）× 2024Q1 的
  baseline vs chip-gated walk-forward 對照 runner。
- `tests/test_ma_rsi_chip.py`：6 個新測試。**測試 210 → 216 passed。**

**閘門邏輯設計（`MaRsiChipStrategy`）**：
- 訊號 = baseline MA/RSI 做多條件（`SMA_fast>SMA_slow AND RSI<70`，warmup 期
  空手；與 `ma_rsi.py` 同邏輯，刻意重算而非 import，讓兩者唯一差異就是閘門，
  便於歸因）**AND** 單一籌碼閘門 `foreign_net > 0`（外資買超）。
- **NaN 語意即安全預設**：`foreign_net` 在首個籌碼日之前、或 as-of 過期
  （stale，超過 `max_age_days`）時為 `NaN`。Python/NumPy `NaN > 0` 求值為
  `False`，閘門天然給 0（空手）——不需要額外的 `if pd.isna(...)` 分支，
  「籌碼缺失/過期時策略自動停止進場」是型別系統送的免費行為，不是刻意加的
  特殊案例判斷。
- **6446 藥華藥真實資料驗證了這個安全預設**：institutional 籌碼在
  2024-01-25 後斷尾（見「Phase 3」節第 8 點），是本輪唯一遇到「籌碼大範圍
  缺失」的真實案例。跑 `run_wf_tw_chip.py` 的實測結果——
  6446 全部 **24 根 baseline 做多 bar**，chip 版 **`chip_long=0`**：
  `濾:外資賣超=0`、`濾:籌碼缺失=24`（100%）。即，chip 版沒有因為策略邏輯
  判斷「不該做多」而空手，而是**籌碼資料斷尾之後 `foreign_net` 全為 NaN，
  閘門把所有本該做多的 bar 都轉成安全空手**。這正是設計預期的行為：資料
  品質空窗不會讓策略誤判、也不會 crash，只會誠實地停止進場。

**`decompose_chip_filtering` 拆解統計（4 檔實測，2024Q1，MA5/20/RSI14）**：

| stock | bars | base_long | chip_long | 濾:外資賣超 | 濾:籌碼缺失 |
|---|---|---|---|---|---|
| 2330/TWSE | 56 | 30 | 19 | 10 | 1 |
| 2603/TWSE | 56 | 12 | 3  | 9  | 0 |
| 8069/TPEx | 56 | 29 | 18 | 10 | 1 |
| 6446/TPEx | 56 | 24 | 0  | 0  | **24** |

恆等式 `base_long = chip_long + 濾:外資賣超 + 濾:籌碼缺失` 逐檔驗證成立
（也是 `test_decompose_partitions_baseline_long` 的單元測試不變量）。除
6446 外，三檔的過濾主要成因都是「濾:外資賣超」（閘門邏輯正常生效），只有
6446 因為籌碼斷尾而 100% 落在「濾:籌碼缺失」——兩種成因分開計數，避免把
資料品質空窗誤讀成策略邏輯效果（例如誤以為「chip 版比較保守」，實際是
資料斷尾）。

**walk_forward 對照（baseline vs chip-gated；`periods_per_year=252`、
`n_splits=3`、手續費 5bps 理想化，未含台股證交稅）**：實際跑出 4 檔 × 2
版本的 sharpe/MDD/win/annual 数字（fold 間差異大，例如 2330 baseline
mean sharpe 3.58 vs chip 2.68、8069 baseline mean sharpe -1.98 vs chip
0.33），**6446 因全樣本被籌碼閘門濾空，chip 版三個 fold 全部 0 筆交易
（sharpe/MDD/win/annual 皆為 0）**。

**立場（延續既有紀律，必須遵守）**：`n_splits=3`、每檔僅 56 根日K、單一
2024Q1 時間窗——**fold 數與樣本量都太小，數字雜訊大，這輪只驗證「管線
跑得動、籌碼特徵能被策略正確讀取並影響訊號」，不作為策略優劣或 chip
閘門是否有 alpha 的結論**。如果之後要下策略優劣結論，需要先擴大 TW 日K
歷史區間與檔數（見下方「建議的下一步」）。

### 台股真實交易成本接入回測（2026-07-10，已完成）

**背景與查證（使用者要求先查證再實作）**：台股成本結構不對稱——券商手續費
**牌告上限 0.1425%（14.25bps）買賣各收一次**（法定上限、全體適用）；證交稅
**0.3%（30bps）僅賣出課徵**（普通股現股，不分交易人身份）。電子下單折扣常見
2.8折~6折，但**依券商/個人而異、無統一值**→設計成參數。邊界情況（本輪未涵蓋，
先記錄）：ETF 證交稅 0.1%、現股當沖 0.15%（優惠至 2027 年底）、借券費。

**★ 已知限制（誠實標注）★**：**券商單筆最低手續費（多數 NT$20）無法建模**——
vector_engine 是報酬率制（淨值起始 1.0、無名目部位金額），算不出低消是否觸發，
**小額交易的實際成本會被低估**。此限制已寫進 costs.py docstring 與
run_wf_tw_chip.py 輸出（比照 short_risk_overlay 的近似標注慣例）。

**設計（使用者 2026-07-10 拍板兩點：解除 vector_engine.py 限制僅限此範圍；
fee_discount 預設 1.0 牌告無折扣保守假設，實際簽約券商後代參數重跑即可）**：
- 新增 `backtest/costs.py`：`TradeCosts(buy_bps, sell_bps)` frozen dataclass、
  `CRYPTO_DEFAULT = TradeCosts(5,5)`、`tw_stock_costs(fee_discount=1.0)` →
  買 14.25×折扣、賣 14.25×折扣+30（**折扣只砍手續費，證交稅不打折**）。
- `vector_engine.py` 最小擴充（★經使用者明確解除限制，僅此範圍★）：
  `run_backtest(..., costs: TradeCosts | None = None)`；成本行改為
  `Δposition.clip(lower=0)×buy_rate + (-Δposition).clip(lower=0)×sell_rate`。
  **costs=None 時退回 fee_bps 對稱費率，行為逐位（bitwise）不變**——數學上
  每根 bar Δ的正部/負部必有一為 0，對稱費率下與舊 `|Δ|×rate` 完全相同。
  shift(1) 核心、報酬計算、trades 計數、既有預設值全未動。
- `walk_forward.py` 加 `costs` 參數透傳（不在不要動清單）。
- 空頭語意備註：Δ<0 對空頭是建立空單，台股借券賣出同樣課證交稅，映射碰巧
  正確；但台股目前 long-only，空頭成本結構（借券費等）未建模。

**兩層無回歸驗證（保護加密貨幣既有結果，全部通過）**：
1. 單元層：`tests/test_costs.py` 5 個新測試（**216 → 221 passed**），含
   {0,1} 與 {-1,0,+1} 兩種訊號的「對稱費率 ≡ 舊 fee_bps 逐位相同」保護性
   回歸（`np.array_equal` 嚴格比對 strategy_returns/equity_curve）。
2. 系統層：改動前後各跑一次 5-fold 乾淨基準
   （`N_SPLITS=5 KLINE_START="2026-02-28 07:00" KLINE_END="2026-07-03 06:00"
   run_diagnosis_bidir.py`），完整輸出 **diff 逐字節相同**——加密貨幣路徑
   （不傳 costs）未受任何影響。

**台股 4 檔真實成本重跑結果（run_wf_tw_chip.py，附舊 5bps 對照）**：
- 成本不影響訊號：[A] 過濾拆解表與舊版逐字相同（做多 bar 數/換手不變）。
- 一個完整回合 = 14.25+44.25 = **58.5bps**，是舊制 10bps 的 **5.85 倍**。
- 侵蝕對照（mean annual，年化因子 252、fold 僅 14 根→年化放大 18 倍，
  pp 數字比原始季損耗誇大，量級解讀用原始損耗）：

| stock | strat | annual@5bps | annual@真實成本 | 侵蝕(pp 年化) |
|---|---|---|---|---|
| 2330/TWSE | base | +145.87% | +134.37% | -11.5 |
| 2330/TWSE | chip | +78.46% | +38.87% | **-39.6** |
| 2603/TWSE | base | +35.46% | +26.00% | -9.5 |
| 2603/TWSE | chip | +18.06% | +9.83% | -8.2 |
| 8069/TPEx | base | +21.44% | +13.81% | -7.6 |
| 8069/TPEx | chip | +191.65% | +141.07% | **-50.6** |
| 6446/TPEx | base | -19.54% | -21.66% | -2.1 |
| 6446/TPEx | chip | 0（無交易） | 0（無交易） | 0 |

- **本輪最有價值的觀察：chip 閘門會放大換手**——foreign_net 逐日翻正負，
  chip 版換手明顯高於 baseline（2330：base 3 次 vs chip 20 次；8069：6 vs
  14），而台股成本重在賣出端（44.25bps），**換手越頻繁被證交稅疊加侵蝕
  越重**：chip 版侵蝕（-39.6/-50.6pp 年化）遠大於同檔 baseline（-11.5/
  -7.6pp）。原始量級驗證：chip 2330 換手 20 次 × 每次額外 ~24.25bps ≈
  一季 4.85% 原始損耗，與事前估算 4~5% 吻合。
- 方法論提醒：若未來籌碼閘門要進正式策略，「閘門造成的換手成本」必須與
  「閘門帶來的訊號品質」一起評估，日頻翻轉的閘門在台股成本結構下天生
  吃虧——可能需要 smoothing（如 N 日淨買超）再閘（→ 已於下節實作驗證）。

### N 日淨買超 smoothing 閘門（2026-07-10，已完成一輪；N=5 後因方向結案不再校正）

**動機**：上節發現當日閘門逐日翻正負放大換手、在賣出端偏重的台股成本下
結構性吃虧（8069 chip 侵蝕 -50.6pp 年化）。本輪把閘門改為「過去 N 根 bar
的 foreign_net 累加 > 0」，**只跑 N=5 一次、不掃參數**（使用者紀律要求）。

**設計（使用者 2026-07-10 確認三點）**：rolling 放 strategy 層（features.py
「只對齊不算 alpha」邊界不動）；N 起點 = 5（一個交易週，當時標注待校正、
2026-07-12 方向結案後不再校正，比照 ATR N=3 慣例）；`min_periods=chip_window` 嚴格 NaN 語意（暖機與單日缺失
都關閘，缺失傳染 N 天）。
- `MaRsiChipStrategy` 加 `chip_window: int = 1`（預設 = 原當日閘門，**逐位相同**，
  保護性回歸測試固定）；模組級 `rolling_chip_sum()` 供策略與分析層共用。
- **防未來函數**：視窗含當根 bar 的 as-of 值，該值本身恆為嚴格早於當日的籌碼
  （上游排他式 as-of），整個視窗全是過去資料；測試②直接驗證「竄改未來
  foreign_net 不改變過去訊號」。
- **已知近似（docstring 有標注）**：rolling 對「對齊後 bar 序列」計算，中間籌碼日
  缺資料時 as-of 重複值會被重複計入（受 max_age_days=7 封頂）；精確版需在籌碼
  日曆上先 rolling 再對齊，屬未來工作。
- 測試 +6（**221 → 227 passed**）：N=1≡現行為逐位回歸、rolling 因果性、N 日累加
  語意雙向、暖機/NaN 傳染空手、chip_window<1 拋錯、rolling_chip_sum 手工比對。

**三方對照結果（baseline / chip N=1 / chip N=5，真實台股成本，好壞並陳）**：

| stock | 換手 base/N1/N5 | annual@真實 base/N1/N5 | 侵蝕pp N1→N5 |
|---|---|---|---|
| 2330 | 3 / 20 / **10** | +134.4% / +38.9% / **+126.4%** | -39.6 → **-29.1** |
| 2603 | 6 / 4 / 4 | +26.0% / +9.8% / +21.9% | -8.2 → -8.1 |
| 8069 | 6 / 14 / **8** | +13.8% / **+141.1%** / +37.9% | -50.6 → **-13.3** |
| 6446 | 2 / 0 / 0 | -21.7% / 0（無交易）/ 0 | — |

- **換手抑制有效**：兩個暴增案例都顯著壓回（2330 20→10、8069 14→8）；
  侵蝕同步縮小（2330 -39.6→-29.1pp、8069 -50.6→-13.3pp）。
- **及時性代價（同等份量呈現）**：2330 進場延後/被濾 6 bar＋出場延後 5 bar；
  8069 5＋8；2603 0＋4。「濾:缺失」從 N=1 的 1 bar 增至 5 bar（2330/8069，
  暖機＋NaN 傳染）。**出場延後的實害案例**：2603 fold3 N=5 版 -84.4%
  （外資當日賣超仍持有硬吃下跌），N=1 版該 fold 空手 0%。
- **不是全面變好**：8069 的 N=1 版 annual（+141.1%）反而高於 N=5（+37.9%）
  ——高換手在該檔某 fold 恰好抓到大波段（fold2 年化 +595%，14 根 bar 的
  年化放大 18 倍，雜訊主導）。**n_splits=3 小樣本紀律不變：本輪只確認
  「換手抑制機制有效、及時性代價可量化」，不下 N=5 優於 N=1 的策略結論**。
- 附帶觀察：2603 的 N5_long（7）> N1_long（3）——平滑不只濾訊號也會**保留**
  被單日賣超打斷的訊號，方向不對稱。
- **本節 2024Q1 小樣本數字已被下節長歷史結果取代**：小樣本上 chip 版看似
  大幅優於 baseline 的印象（8069 chip +141%）是雜訊；引用籌碼閘門結論
  一律以下節 15-fold × 獨立雙窗數字為準。

### 台股資料擴充＋獨立雙窗驗證：N=5 機制方向一致確認（2026-07-10，已完成）

**動機**：上節 n_splits=3、56 根/檔比當年被 3 小時資料位移打臉的 5-fold 還小，
任何 N=1 vs N=5 比較都在雜訊層級。比照放空/A2 輪紀律擴大樣本：雙位數 fold
＋完全獨立（零重疊）雙時間窗互驗。

**資料擴充（先查證後抓取，2026-07-10）**：
- **籌碼不需重抓**——查證發現 chips_tw.sqlite 在 POC 時就是全歷史
  （Foreign_Investor：2330/2603 自 2012-05-02、8069 自 2005-01-06、6446
  2014-03-11→斷尾 2024-01-25）；先前對齊範圍窄只是 runner 讀取視窗硬編碼。
- 價格按「價格∩籌碼交集」抓取：FinMind 實測 4 檔在各自交集起點都有價
  （2330 甚至可回到 1994-09，比文件早）。落地 15,161 根（2330:3471、
  2603:3464、8069:5217、6446:3009，皆至 2026-07-09），筆數/範圍檢核通過。
- 單 fold 測試窗從 14 根 → 163~326 根（15~22 倍），年化放大係數從 18 倍降到
  ~1 倍量級，單一 fold 雜訊主導全局的空間大幅壓縮。
- **6446 標注「僅作 stale 機制測試樣本」不入統計**：斷尾後死區約佔 22% bar。

**核心問題的答案：N=5−N=1 的機制方向在兩個獨立半窗 100% 一致**
（3 檔入統計 × H1/H2 共 6 個獨立窗）：

| stock | 窗 | Δ換手(N5−N1) | Δ侵蝕(pp) | Δannual@真實(pp) |
|---|---|---|---|---|
| 2330 | H1 / H2 | -200 / -208 | +6.5 / +6.0 | +5.9 / +19.5 |
| 2603 | H1 / H2 | -142 / -180 | +4.9 / +7.0 | **-0.2 / +0.3** |
| 8069 | H1 / H2 | -258 / -280 | +4.6 / +6.6 | +16.0 / +5.4 |

- **換手抑制（6/6 窗皆負）與侵蝕縮小（6/6 窗皆正，+4.6~+7.0pp）方向完全
  一致**——這兩個機制效果是穩健的，不是小樣本假象。
- **淨報酬效果分兩種**：2330/8069 兩窗皆正（+5.4~+19.5pp）；2603 兩窗
  皆 ≈0（-0.2/+0.3pp，符號翻轉）→ 2603 的淨效果**落在雜訊內**，比照 A2
  輪紀律不對該檔下結論。
- 8069 舊「N=1 反而贏」已翻轉：長樣本下 N=1 全窗 -15.8% vs N=5 -4.6%，
  2024Q1 的 +141% 確為單 fold 年化放大的雜訊。

**★ 更重要的誠實發現：chip 閘門本身（不論 N）在長樣本＋真實成本下全面
跑輸 baseline ★**（全窗 mean annual）：

| stock | baseline | chip N=1 | chip N=5 |
|---|---|---|---|
| 2330 | +2.7% | -13.0% | +0.2% |
| 2603 | +4.9% | -5.6% | -7.0% |
| 8069 | -0.9% | -15.8% | -4.6% |

9/9 個入統計窗中 chip 版（含 N=5）皆 ≤ baseline（最接近的是 2330 H2：
chip5 +9.1% vs base +9.8%）。**N=5 只是把 N=1 的傷害修回大半，不是把
閘門變成加分項**——「外資買超才做多」這個閘門在現行形式（含 5 日平滑）
下沒有 alpha，反而因換手成本與訊號截斷淨損。及時性代價在長樣本同樣
可觀（出場延後 285~438 bar > 進場延後 152~286 bar，出場端是主要代價）。
2024Q1 小樣本給出的樂觀印象全數不成立——這正是本輪擴大樣本要防的事。

**下一步屬使用者決策**：籌碼閘門是否轉向（改特徵/改用法/棄用）、或先做
其他站。N=5 起點值未再校正（維持只跑一次的承諾）。

### foreign_net IC 診斷：無正向資訊價值（2026-07-12，已完成）

**動機**：chip 閘門 9/9 窗跑輸 baseline 後，在「換特徵/連續加權/棄用」分岔前
先驗證 foreign_net 本身對未來報酬有無統計資訊價值。使用者 2026-07-12 確認
fwd return 用**標準版** `close(T+k)/close(T)−1`（非執行延遲版，純診斷）。

**實作（TDD，新增 3 檔案，不動任何「不要動清單」檔案）**：
- `backtest/ic.py`：`forward_return`（`close.shift(-k)`，尾端 k 根 NaN 不回繞）、
  `compute_ic`（Spearman 主指標 + Pearson 參照；NaN 配對逐對剔除、回報 n_valid；
  <3 對或零變異回 NaN 不拋錯）、`fold_ics`（**fold 自足**：15 個連續不重疊等長
  子段，fwd 在段內計算、fold 間零 close 共享，竄改後段不影響前段有測試背書）、
  `summarize_folds`（mean±std ddof=1、正 IC fold 佔比）。
- `tests/test_ic.py` 10 tests（**227 → 237 passed**）：手工數字驗 shift 方向、
  單一跳升位置驗方向沒寫反、合成預測力單調構造 Spearman=1.0 精確、規格版
  sign 構造強正（tie 使理論上限 ≈√3/2，實測 0.796）、反向對照（feature=已
  實現報酬，IC≈0）、NaN 逐對剔除/n_valid、樣本不足給 NaN、fold 自足性、彙總。
- `run_ic_foreign_net.py`：3 檔（2330/2603/8069，6446 照標注排除）× H1/H2
  獨立雙半窗（切法同 run_wf_tw_chip）× {raw, s5=rolling_chip_sum(fn,5)} ×
  k∈{1,5,10} × 15 fold。s5 在各窗內獨立計算（H1/H2 零資料共享）。

**36 格完整矩陣（Spearman mean IC±std over 15 folds；pos=正 IC fold 佔比）**：

| stock | feat | k | H1 mean±std (pos%) | H2 mean±std (pos%) | 判讀 |
|---|---|---|---|---|---|
| 2330 | raw | 1 | -0.029±0.098 (33%) | -0.041±0.079 (33%) | 跨窗一致**負**IC |
| 2330 | raw | 5 | -0.080±0.155 (13%) | +0.001±0.134 (60%) | 方向不一致（雜訊） |
| 2330 | raw | 10 | -0.060±0.173 (20%) | +0.032±0.155 (53%) | 方向不一致（雜訊） |
| 2330 | s5 | 1 | -0.033±0.081 (33%) | -0.005±0.106 (53%) | 同號但弱（<0.02） |
| 2330 | s5 | 5 | -0.068±0.175 (27%) | +0.079±0.169 (73%) | 方向不一致（雜訊） |
| 2330 | s5 | 10 | -0.059±0.183 (27%) | +0.086±0.235 (67%) | 方向不一致（雜訊） |
| 2603 | raw | 1 | -0.020±0.058 (33%) | -0.005±0.067 (47%) | 同號但弱（<0.02） |
| 2603 | raw | 5 | -0.079±0.113 (33%) | -0.000±0.076 (47%) | 同號但弱（<0.02） |
| 2603 | raw | 10 | -0.113±0.090 (0%) | +0.002±0.094 (47%) | 方向不一致（雜訊） |
| 2603 | s5 | 1 | -0.035±0.079 (40%) | +0.005±0.089 (47%) | 方向不一致（雜訊） |
| 2603 | s5 | 5 | -0.104±0.130 (27%) | +0.032±0.136 (67%) | 方向不一致（雜訊） |
| 2603 | s5 | 10 | -0.176±0.153 (13%) | +0.056±0.177 (67%) | 方向不一致（雜訊） |
| 8069 | raw | 1 | -0.046±0.068 (27%) | -0.038±0.072 (27%) | 跨窗一致**負**IC |
| 8069 | raw | 5 | -0.050±0.131 (40%) | -0.018±0.099 (40%) | 同號但弱（<0.02） |
| 8069 | raw | 10 | -0.038±0.153 (40%) | -0.031±0.106 (40%) | 跨窗一致**負**IC |
| 8069 | s5 | 1 | -0.039±0.109 (47%) | -0.023±0.082 (27%) | 跨窗一致**負**IC |
| 8069 | s5 | 5 | -0.016±0.190 (47%) | -0.012±0.123 (53%) | 同號但弱（<0.02） |
| 8069 | s5 | 10 | -0.012±0.234 (53%) | -0.067±0.183 (40%) | 同號但弱（<0.02） |

（每 fold 有效樣本 103~172 對；36 格全部 15/15 fold 有效；Pearson 方向與
Spearman 一致，數字見 `run_ic_foreign_net.py` 輸出。H1/H2 分界：2330/2603
= 2019-05、8069 = 2015-10。）

**三項回報（依任務規格）**：
1. **★ 沒有任何一格顯示跨 H1/H2 一致的正 IC ★**——raw 與 s5 兩個版本、三個
   horizon 全滅。彙總 mean IC（3 檔 × 2 窗平均）全為負：raw -0.030/-0.038/
   -0.035、s5 -0.022/-0.015/-0.029（k=1/5/10）。
2. **唯一跨窗一致的訊號是弱「負」IC**：raw k=1 **六窗全負**（2330/8069 兩檔
   達判讀門檻、2603 同號但弱），|IC|≈0.02~0.05——「外資買超」短線上如果有
   任何訊號，方向是**反向**的（買超後隔日報酬略偏弱），量級在可交易性邊緣。
   這與 chip 閘門 9/9 窗跑輸 baseline **互相印證且給出機制解釋**：閘門留下
   的 bar 正是未來報酬略差的 bar，訊號截斷＋換手成本再疊加。
3. **horizon 拉長不增加資訊**：k=5/10 的 mean IC 沒有系統性變強，std 隨 k
   放大（0.06~0.10 → 0.15~0.23）、H1/H2 方向翻轉格數增多——長 horizon 只
   放大雜訊。2330/2603 的 H2 在 k=5/10 出現正 IC（s5 至 +0.086）但 H1 同格
   為負，依既有紀律不拿單窗下結論。

**含義（事實層，決策屬使用者）**：foreign_net 在現行形式（原始值或 5 日平滑、
做多方向閘門）下對未來報酬**無正向資訊價值**，「改連續加權」缺乏統計基礎
（加權的底層訊號本身 IC≤0）；若要留在籌碼方向，剩餘選項是換特徵（如
investment_trust_net、margin 變化量——本輪未算）或把弱負 IC 當反向訊號研究
（量級 0.02~0.05 需先過成本可行性）；或接受診斷結論、籌碼閘門方向結案。
**→ 使用者 2026-07-12 選擇結案，見下節。**

### Phase 3 台股籌碼特徵：正式結案（2026-07-12，使用者決定）

**完整推論鏈（各環節證據見上面對應小節，此處只串邏輯）**：

1. **POC 建立資料管線（2026-07-07~08）**：FinMind 4 dataset → 籌碼獨立
   canonical schema → chips_tw.sqlite；features.py 排他式 as-of 對齊（防未來
   函數，`allow_exact_matches=False`）；台股日K 接上後完成價格×籌碼真實端到端
   對齊。恆等式強制驗證、哨兵轉 NULL、6446 斷尾被 age/stale 攔下——**管線層
   全程無資料品質事故、無未來函數違規**，後續所有負面結論都不是管線問題。
2. **訊號時間尺度錯位的發現與修正（2026-07-10，與放空輪「訊號時間特性互斥」
   教訓同型）**：當日閘門 foreign_net>0 逐日翻正負，時間尺度與 MA/RSI 趨勢
   持倉不相容 → 換手放大（2330 base 3 → chip 20 次）；台股成本重在賣出端
   （44.25bps 含證交稅），高換手被疊加重擊（侵蝕 -39.6/-50.6pp 年化）。
   修正 = N 日淨買超 smoothing（N=5，只跑一次不掃參數）。
3. **N=5 證明機制有效、代價明確（2026-07-10）**：換手抑制、侵蝕縮小；
   及時性代價同步量化（進出場延後，出場端為主；2603 fold3 -84.4% 實害案例）
   ——平滑不是免費的，兩面並陳。
4. **長樣本獨立雙窗驗證換手抑制穩健（2026-07-10）**：資料擴充至 15,161 根、
   15-fold × H1/H2 零重疊雙窗：Δ換手 6/6 窗皆負、Δ侵蝕 6/6 窗皆正——機制
   層穩健。**但同時發現 chip 閘門本身（不論 N）9/9 窗全面跑輸 baseline**，
   2024Q1 小樣本的樂觀印象全數不成立。
5. **IC 診斷找到根因（2026-07-12）**：foreign_net 對未來報酬**無正向資訊
   價值**（36 格矩陣零格跨窗一致正 IC；唯一跨窗一致訊號 = raw k=1 弱負 IC
   六窗全負）。9/9 窗跑輸不是成本、不是平滑參數、不是管線 bug，**是特徵本身
   沒有資訊**——N=5 只是修復 N=1 的自傷，修不出不存在的 alpha。

**結論**：現行 chip-gating 設計（foreign_net 做多向閘門，不論 N、不論加權
形式）**此路不通，正式結案**。

**★限制標注（2026-07-16，台股E2化前置查證發現，不重跑★）**：
`klines_tw.sqlite` 收盤價已查證為**未還原原始成交價**（65/65 個除權息事件
逐位驗證，`prev_close == FinMind before_price` 全部相符，見台股E2化前置查證
任務）——本節 36 格 IC 矩陣與長樣本雙窗回測都是建立在這份未還原價格上。
**使用者 2026-07-16 決定不重跑**，理由兩點：①Spearman IC 是排名相關性，
對單一極端值（如 2603 2023-06-30 單日 -45.2%）天然有抵抗力，不像 Pearson
會被少數極端值主導；②chip-gating 結案是 IC 診斷＋長樣本雙窗回測兩條獨立
方法交叉驗證得出的，不是單一脆弱證據，除權息汙染不足以動搖「foreign_net
無正向資訊價值」這個跨方法一致的結論。此限制標注僅記錄「證據基礎有此
已知汙染但判斷不受影響」，不代表汙染不存在、也不代表其他更敏感的分析
（如 Pearson 相關、單日報酬率統計）可以援引同樣的免重跑理由。

**保留資產（未來若有新特徵構想，直接重啟用）**：
- 資料管線全套：providers/finmind.py、cleaning 籌碼區段、storage_chips.py、
  features.py as-of 對齊、klines_tw.sqlite / chips_tw.sqlite（資料不刪）。
- 方法論框架：獨立雙窗＋15-fold 紀律、真實成本侵蝕拆解、過濾成因兩類拆解
  （decompose_chip_filtering）、IC 診斷框架（backtest/ic.py，換特徵時先過 IC
  再進回測）。
- 程式碼與測試全部保留不刪（ma_rsi_chip.py、run_wf_tw_chip.py、
  run_ic_foreign_net.py、237 tests）。
- **未診斷區**：investment_trust_net、margin 變化量等其他籌碼特徵本輪未算
  IC——結案的是「foreign_net 閘門」這條設計，不是「籌碼資料整體無用」的
  斷言；重啟的第一步應是對新特徵跑同一套 IC 診斷。

**參數結案標注**：chip_window=5 起點值不再校正（方向已結案，「只跑一次」
承諾自然終止）；A2/long_regime_window=300 早於 2026-07-06 定案不採用亦不再
校正——至此「待校正」清單只剩 ATR N=3/window=14（→ 已立項為下一任務）。

## 台股股價還原機制（2026-07-16，前置任務完成，TDD 實作）

**動機**：台股 E2 化前置查證發現 `klines_tw.sqlite` 收盤價是 FinMind
TaiwanStockPrice 的原始成交價，未做除權息還原（4 檔股票 65 個除權息事件
逐位驗證：`前一交易日收盤 == FinMind before_price` 全部相符）。MA/RSI/ATR
等 rolling 指標直接吃這份原始價格會在除權息日附近算到人工跳空（極端案例：
2603 長榮 2023-06-30 現金股利 70 元，原始報酬 93.5/155.0-1=-39.68%）。

**設計決策（使用者 2026-07-16 確認）**：
1. FinMind 有現成還原價 dataset（`TaiwanStockPriceAdj`，回傳完整還原 OHLC）
   但鎖付費 sponsor 層，目前免費層不可用（HTTP 400）——採自建方案，日後
   升級付費層可互為交叉驗證，不衝突。
2. 調整因子用 `after_price/before_price`，**不用 `reference_price`**——
   純配股事件（"權"）該欄位會停滯於 before_price（FinMind 的已知資料
   限制，2603 2017-11-07 為實測反例），只有 after_price 在所有事件類型
   都正確反映稀釋幅度。
3. 儲存：方案C（動態計算）為**預設路徑**，不落地覆蓋原始資料；方案B
   （凍結快照，獨立檔）為**選配**，只在需要固定錨點做 fold 對照時使用。
4. 執行語意邊界：漲跌停鎖死判定等「這個價位實際能不能成交」的判斷一律
   仍用**原始價格**，還原價只用於訊號/指標計算（MA/RSI/ATR）。

**實作**：
- `data/adjustment.py`：
  - `compute_cum_factor(bar_dates, dividends) -> pd.Series`——向量化（`np.searchsorted`
    + 後綴 log 累加，非 `apply(axis=1)`）算出 `cum_factor(t) = Π{ratio_i : 除權息日_i > t}`，
    最新一根 bar 恆為 1.0（錨點）。
  - `apply_back_adjustment(klines, dividends) -> pd.DataFrame`——open/high/low/close
    （與 vwap，若存在）乘上 cum_factor，volume/turnover 不動；含 before_price
    對帳防線（事件當日若在資料範圍內，前一交易日收盤需與 before_price 相符，
    不符即拋錯，比照融資融券哨兵值「已知情況轉換、未知情況拋錯」紀律）。
  - **這是刻意的非因果轉換，不是防未來函數 bug**（模組 docstring 與
    `test_adjustment_intentionally_uses_future_dividend_events` 明確標注）：
    還原價用「未來」除權息事件回填「過去」價格水位是產業標準做法，與
    `shift(1)` 訊號防未來函數紀律是兩件不同的事——前者是事後對歷史價格
    水位的重新表述，後者是禁止用還不存在的資訊做交易決策。
- `data/storage_adjusted.py`：凍結快照工具（方案B），獨立檔
  `data/klines_tw_adjusted.sqlite`（比照 storage_chips.py 隔離理由：原始
  資料日常寫入不進同一檔案，隔離失誤爆炸半徑）。`save_adjusted_snapshot`/
  `load_adjusted_snapshot` 以 `(symbol, exchange, interval, ts, as_of_date)`
  為主鍵，同一 symbol 可並存多個不同錨點的快照，互不覆蓋。
- `data/cleaning.py` 新增非正值 O/H/L/C 防線（與還原價無關的獨立資料品質
  問題，同輪一併處理）：`_sanitize` 丟棄任何 O/H/L/C <= 0 的列（6446 藥華藥
  2016-12-05 真實資料事故：OHLCV 全部等於 0.0，前後交易日正常，屬資料源
  缺漏列）；`validate_canonical` 新增保護性斷言，即使繞過 `_sanitize` 也會
  在最終關卡攔下非正值價格，不靜默放行進 DB。

**測試**：325 tests（+22，303→325，0 warnings）——`tests/test_price_adjustment.py`
13 個（含 2603 2023-06-30 精確數值斷言：調整後報酬精確等於 93.5/85.0-1=
+10.0%，不是模糊的「比較不極端」；after_price 防呆反例；文件性斷言）、
`tests/test_storage_adjusted.py` 7 個（WAL/往返/upsert冪等/多錨點並存）、
`tests/test_tw_price.py` 新增 2 個（全零列丟棄、validate_canonical 拋錯）。

**用法**：
```python
from data import storage_sqlite
from data.providers.finmind import fetch_dataset, DATASET_DIVIDEND_RESULT
from data.adjustment import apply_back_adjustment

conn = storage_sqlite.connect("data/klines_tw.sqlite")
klines = storage_sqlite.load_klines(conn, "2603", "1d", exchange="TWSE")
dividends = fetch_dataset(DATASET_DIVIDEND_RESULT, "2603")
adjusted = apply_back_adjustment(klines, dividends)  # 訊號/指標層用這份
# 漲跌停判定等執行語意判斷仍用 klines（原始價），不用 adjusted
```

**6446 2016-12-05 異常列處理：兩件不同的事，分開說明避免日後混淆**：
1. **一次性手動清除既有殘留（2026-07-16 使用者明確授權後執行，已完成）**：
   `data/klines_tw.sqlite` 裡這一筆歷史殘留列（symbol=6446, exchange=TPEx,
   interval=1d, ts=1480899600000000000 對應 2016-12-05 Asia/Taipei 交易日，
   OHLCV 五欄皆為 0.0）已用完整主鍵 `(symbol, exchange, interval, ts)` 精準
   `DELETE`。執行前先 `SELECT symbol=6446 AND close=0` 定位，確認**恰好 1
   筆**符合才動手；刪除後驗證：該 ts 剩餘筆數=0、6446 全表非正值 close
   筆數=0、前後交易日 2016-12-02（close=187.5）/2016-12-06（close=174.0）
   資料完整不受影響、6446 總筆數 3009→3008（僅減少這 1 筆）。刪除後重跑
   全套測試 **325 passed 不受影響**（這筆刪除不影響任何既有測試，因為新
   清理邏輯本來就是設計來處理這類異常，這裡只是清掉已存在的歷史殘留，
   不是測試依賴的資料）。這是**一次性、針對這一筆已知列的手動動作**，
   不會自動套用到其他任何列或未來的資料。
2. **未來自動防線（`data/cleaning.py` 的 `_sanitize`/`validate_canonical`，
   本任務稍早新增，機制常駐）**：往後只要 6446（或任何其他 symbol）這個
   日期區間被重新 `run_ingest_tw_price.py` 抓取，`finmind_price_to_canonical`
   會自動丟棄任何 O/H/L/C <= 0 的列，不需要再手動介入；`validate_canonical`
   則是最終保護性斷言，防止未來任何新資料源的轉換函式忘記處理同類情況。
   **這條防線本身不會、也不需要回溯清理第 1 點提到的既有殘留**——兩者是
   互補但獨立的兩個動作，第 1 點已經手動做完，第 2 點是往後自動生效。

**保留資產**：`data/adjustment.py`、`data/storage_adjusted.py` 均為新模組，
不在「不要動清單」上；`data/cleaning.py` 的變動只新增防線、不改變既有
正常資料的行為（保護性回歸：既有 303 個測試全數維持通過）。

**過程中額外發現並處理的資料品質問題（2026-07-16，與還原價無關但同層清理）**：
6446 藥華藥掛牌首日（2014-03-11）`open=0.0`，但 `high=254.99/low=207.79/
close=209.79/volume=2,291,389` 全部有效——與先前 2016-12-05 那筆「全零列」
本質不同（那筆是無交易的資料缺口，這筆是真實交易日、只有 open 單欄位缺
值）。使用者確認沿用既有規則（O/H/L/C 任一非正值即整列丟棄，不額外造規則
做部分欄位補值），已比照上次的三步驟協議（精準定位→確認恰好 1 筆→用完整
主鍵 DELETE→驗證刪除後鄰近交易日 2014-03-12/03-13 完整、6446 總筆數
3008→3007）手動清除既有殘留，跑批後重跑測試 334 passed 不受影響。全 4 檔
股票掃描確認這是唯一一筆，不是系統性問題。

## 台股 E2 化第一輪基準（2026-07-16，多頭 baseline，供未來輪次引用對照）

**範圍**（使用者定案）：策略 `strategy/ma_rsi_regime.py`（regime 過濾多頭，
不含放空，不動此既有檔案）；台股真實不對稱成本（`tw_stock_costs()`，買
14.25bps／賣 44.25bps 含證交稅）**必選**；股價全程用還原價（訊號生成與
E2 執行皆用 `apply_back_adjustment()` 輸出，原始價只用於 M3 診斷）；4 檔
股票各自完整歷史、各自獨立 `TimeSeriesSplit(n_splits=15)`；`slippage_bps`
明確 pin 0.0（引擎預設 2.0 是 BTCUSDT 校正值，未經台股驗證不能沿用，留給
未來輪次）。新檔 `run_e2_tw_baseline.py`。

**成本必選介面（三道防線，已驗證確實生效）**：
1. `run_symbol_e2()` 簽名 `costs: TradeCosts` 無預設值——省略時實測直接
   `TypeError: missing 1 required positional argument: 'costs'`。
2. 函式內第一行 `assert isinstance(costs, TradeCosts)`——實測傳 `None`
   或裸 `float` 皆正確拋出 `AssertionError`。
3. 全域唯一合法成本來源 `COSTS = tw_stock_costs()`（模組層級常數），
   runner 內所有 `run_event_backtest` 呼叫點寫死 `costs=costs`，不存在
   省略走引擎 fallback 的路徑。

**引擎層變動（`backtest/event_engine.py` 範圍性解鎖 + `broker/paper.py`）**：
`run_event_backtest()` 新增 `costs: TradeCosts | None = None`，透傳給
`PaperBroker(costs=costs)`；`PaperBroker.market_order()` 依 `Side` 查表
（BUY→buy_bps、SELL→sell_bps），`costs=None` 完全沿用舊 `fee_bps` 對稱
路徑，逐位不變（保護 BTC/ETH 既有呼叫端；`tests/test_paper_broker_costs.py`
4 個 + `tests/test_event_engine_costs.py` 5 個保護性回歸測試驗證）。
325→**334 tests**（+9：broker 層 4 個 + event_engine 層 5 個）。

### 事前判準結果（逐項，機制層全數 PASS 才解讀報酬數字）

**M1（不對稱成本確實生效）：PASS**——全部 385 筆 Fill（199 BUY + 186 SELL）
的 fee/notional 有效費率，與期望值最大偏差 BUY 3.55e-15bps／SELL
7.11e-15bps（純浮點噪音，非系統性偏差）。

**M2（熔斷觸發密度非零也非全觸發）：PASS**——60 個 fold-案例中 **43 個**
出現 `tripped_days>0`、17 個為 0（非 0、非全觸發）。**不重新論證「熔斷語意
塌縮」**（前置查證階段已定案，日頻下保護範圍塌縮成同根 bar 自我攔截，
這裡數字只證明機制沒有失效或失控）。個別 fold 觸發次數範圍 0~26 次不等
（8069 fold12 最高 26 次/326 根、6446 fold10 次高 23 次/187 根），符合
「小型股波動大、更容易單日跌破 3% 門檻」的直覺，不是異常。

**M3（漲跌停曝險清點，診斷性、不修正）**：
- 決策 bar 落在真實鎖死日的 fold：**8 / 60**
  - 2330 fold14（range）：2025-04-07、2025-04-10
  - 2603 fold14（range）：2025-04-07、2025-04-10
  - 8069 fold13（range）：2023-04-28
  - 8069 fold15（range）：2025-04-07、2025-04-10、2025-11-20、2026-05-11
  - 6446 fold8（range）：2020-10-22、2020-10-23、2020-10-26
  - 6446 fold9（range）：2021-08-26、2021-08-30、2021-11-15、2021-11-16、
    2021-11-17、2021-11-18、2021-11-19
  - 6446 fold10（trend_up）：2021-12-20、2022-02-16、2022-02-17
  - 6446 fold14（range）：2025-04-07、2025-04-10
- **實際成交 bar 落在真實鎖死日的 fold：1 / 60**——6446 fold10（trend_up），
  2021-12-20，BUY，成交價=238.95。這是本批數字中**唯一**一筆「引擎假設
  100% 成交、但當天實際上可能因鎖死而無法真實成交」的具體案例，fold10
  年化 +40.4% 的數字應該連同這個註記一起引用，不可脫離上下文單獨引用。
- 2025-04-07/04-10（美國關稅衝擊）在 4 檔中的 4 檔都出現（連台積電/長榮
  這種流動性最好的權值股都在決策 bar 撞到），與前置查證階段查證4 的
  發現一致；6446 另有兩段其他年份的密集鎖死區間（2020-10、2021-08~12），
  對應這檔小型股當時的高波動期。

**R1（年化/Sharpe 無 NaN 或發散值）：PASS**——0/60 異常。個別 fold 出現
102.7%（2330 fold15）、127.4%（2603 fold10）等高年化數字屬小樣本＋
2603 長榮 2021 航運超景氣循環等真實歷史事件驅動，不是數值錯誤。

**R2（regime filter 確實生效）：PASS**——trend_up 類 fold（21 個）平均
多頭曝險佔比 **38.5%**，非 trend_up 類（39 個）僅 **12.3%**，方向與量級
符合設計預期。

### 60 個 fold-案例基準表（4 檔 × 15-fold，年化報酬/Sharpe/熔斷日）

periods_per_year=252；還原價全程；costs=tw_stock_costs()；slippage_bps=0.0；
risk=RiskManager(equity=1.0) 預設值；forced_liquidation=False（不含放空）。

**[2330 TWSE，2012-05-02~2026-07-09，3471 根]**
| fold | regime | 年化 | Sharpe | 多頭bar | 熔斷日 |
|---|---|---|---|---|---|
| 1 | range | -8.7% | -1.02 | 29/216 | 2 |
| 2 | trend_up | +5.0% | +0.49 | 76/216 | 1 |
| 3 | range | +2.2% | +0.22 | 69/216 | 3 |
| 4 | trend_up | +27.1% | +1.61 | 128/216 | 1 |
| 5 | trend_up | +15.1% | +1.24 | 103/216 | 1 |
| 6 | range | -2.4% | -0.12 | 77/216 | 2 |
| 7 | range | -26.7% | -2.25 | 37/216 | 2 |
| 8 | trend_up | +8.6% | +0.71 | 69/216 | 1 |
| 9 | trend_up | +25.5% | +1.30 | 117/216 | 2 |
| 10 | range | -3.2% | -0.54 | 13/216 | 1 |
| 11 | trend_down | -2.7% | -0.56 | 10/216 | 0 |
| 12 | range | -11.5% | -1.41 | 49/216 | 0 |
| 13 | trend_up | +65.6% | +1.85 | 98/216 | 6 |
| 14 | range | -12.6% | -0.92 | 52/216 | 2 |
| 15 | trend_up | +102.7% | +2.67 | 172/216 | 4 |

mean=**+12.3%**  std=**33.2%**

**[2603 TWSE，2012-05-02~2026-07-09，3464 根]**
| fold | regime | 年化 | Sharpe | 多頭bar | 熔斷日 |
|---|---|---|---|---|---|
| 1 | range | -2.0% | -1.03 | 3/216 | 0 |
| 2 | range | +10.2% | +0.77 | 30/216 | 2 |
| 3 | trend_down | -10.6% | -0.50 | 61/216 | 5 |
| 4 | range | +0.0% | +0.00 | 0/216 | 0 |
| 5 | trend_up | -6.8% | -0.81 | 15/216 | 0 |
| 6 | trend_down | -23.2% | -1.88 | 25/216 | 5 |
| 7 | range | +0.0% | +0.00 | 0/216 | 0 |
| 8 | range | +0.0% | +0.00 | 0/216 | 0 |
| 9 | trend_up | +90.0% | +1.84 | 80/216 | 13 |
| 10 | range | +127.4% | +1.78 | 70/216 | 16 |
| 11 | range | -6.0% | -0.37 | 27/216 | 4 |
| 12 | trend_up | +1.7% | +0.18 | 68/216 | 2 |
| 13 | trend_up | +66.0% | +1.71 | 135/216 | 9 |
| 14 | range | -15.2% | -1.26 | 32/216 | 2 |
| 15 | trend_up | +2.0% | +0.20 | 65/216 | 2 |

mean=**+15.6%**  std=**43.2%**

**[8069 TPEx，2005-01-06~2026-07-09，5217 根]**
| fold | regime | 年化 | Sharpe | 多頭bar | 熔斷日 |
|---|---|---|---|---|---|
| 1 | trend_up | +51.5% | +1.11 | 92/326 | 13 |
| 2 | trend_down | -2.0% | -0.88 | 2/326 | 0 |
| 3 | range | +67.4% | +1.53 | 172/326 | 22 |
| 4 | range | +2.8% | +0.24 | 70/326 | 8 |
| 5 | trend_down | -5.2% | -1.32 | 7/326 | 1 |
| 6 | range | +5.2% | +0.39 | 50/326 | 5 |
| 7 | trend_down | -11.1% | -1.86 | 14/326 | 1 |
| 8 | trend_up | -0.7% | +0.06 | 47/326 | 4 |
| 9 | range | +24.5% | +0.89 | 142/326 | 13 |
| 10 | range | -7.6% | -0.64 | 34/326 | 5 |
| 11 | trend_up | +3.9% | +0.31 | 58/326 | 5 |
| 12 | trend_up | +78.3% | +1.62 | 208/326 | 26 |
| 13 | range | -20.2% | -1.22 | 25/326 | 6 |
| 14 | trend_up | -13.0% | -0.60 | 91/326 | 13 |
| 15 | range | +0.0% | +0.00 | 0/326 | 0 |

mean=**+11.6%**  std=**30.2%**

**[6446 TPEx，2014-03-12~2026-07-09，3007 根]**
| fold | regime | 年化 | Sharpe | 多頭bar | 熔斷日 |
|---|---|---|---|---|---|
| 1 | trend_down | +0.0% | +0.00 | 0/187 | 0 |
| 2 | trend_up | +0.9% | +0.17 | 52/187 | 6 |
| 3 | range | -17.2% | -1.81 | 7/187 | 2 |
| 4 | range | +0.0% | +0.00 | 0/187 | 0 |
| 5 | range | -5.3% | -0.18 | 65/187 | 5 |
| 6 | trend_down | +0.0% | +0.00 | 0/187 | 0 |
| 7 | range | +0.0% | +0.00 | 0/187 | 0 |
| 8 | range | +0.0% | +0.00 | 0/187 | 0 |
| 9 | range | +0.0% | +0.00 | 0/187 | 0 |
| 10 | trend_up | +40.4%（見上方 M3 註記：fold10 含 1 筆鎖死日成交） | +0.92 | 121/187 | 23 |
| 11 | trend_down | -24.5% | -1.56 | 26/187 | 4 |
| 12 | range | +0.0% | +0.00 | 0/187 | 0 |
| 13 | trend_up | +25.9% | +1.02 | 52/187 | 3 |
| 14 | range | +0.0% | +0.00 | 0/187 | 0 |
| 15 | trend_up | +29.3% | +1.54 | 27/187 | 3 |

mean=**+3.3%**  std=**16.7%**

**判讀**：M1/M2/R1/R2 四項機制層＋結果層判準全數 PASS，M3 診斷完整揭露
（8/60 決策bar 曝險、1/60 實際成交曝險，且都對應真實可查證的歷史事件，
非隨機雜訊）——**這批數字視為管線正確、可信賴地作為起點**。4 檔股票
mean 年化介於 +3.3%~+15.6%、std 介於 16.7%~43.2%，std 遠大於 mean（單
symbol 層級雜訊比高，與加密貨幣 1h 尺度的既有結論同型，不是台股特有
現象）。**這輪不對策略優劣下結論、不比較任何 A/B variant**——純粹是
台股 E2 執行層的第一份正確、可信賴、含完整成本+風控的基準數字，供未來
放空/籌碼重啟/ML 等後續輪次引用對照。

**已知限制標注（前置查證階段量化，本輪不解決）**：這批數字建立在兩個
已知簡化假設上——(1) 日內熔斷在日頻下保護範圍塌縮成「同根 bar 自我攔截」
（不是「當天剩餘 bar」保護，日頻沒有這個概念）；(2) `broker.market_order`
永遠假設市價單全額成交，未建模漲跌停鎖死時可能無法成交的情況（M3 已
量化：60 個 fold-案例中 1 個實際受影響、8 個決策 bar 曝險）。兩項限制的
簡短技術提醒已寫入 `backtest/event_engine.py` 模組 docstring，完整量化
數字在本節。

**保留資產**：`run_e2_tw_baseline.py`（新檔，可重跑重現，非落地凍結
資料——`klines_tw.sqlite` 本身已是落地 DB，但除權息事件是即時抓取，見
「台股股價還原機制」節「不建新的落地/快取層」的既有設計說明）；
`tests/test_paper_broker_costs.py`、`tests/test_event_engine_costs.py`
保留作 costs wiring 的保護性回歸；`event_engine.py`／`CLAUDE_2.md` 已
同步記錄這次範圍性解鎖。

## 台股放空第一輪：3.5% 禁空規則 + ATR 分層 + 漲跌停鎖死耦合（2026-07-16，實作完成）

**範圍（使用者定案）**：groundwork 六項查證後，這輪只做兩項——
① 3.5% 跌幅次日禁空規則（純價格驅動進場閘門）；② ATR 分層＋漲跌停耦合
強平機制。**item 3（強制回補日曆）另開一輪**（需新資料源：股東會/除權息
日期，工程量獨立）。

### 已知限制、本輪不建模（items 1/4/5，使用者定案，文件化而非解決）

- **item 1 借券費率的時間累積成本**：本輪用融券 0.08%（萬分之八）固定
  牌告費率做一次性近似，不做真正的隨持有時間累積成本模型。註記：台股
  放空實務有兩條管道——融券（0.08% 一次性＋保證金利息收入，牌告固定）
  與借券賣出 SBL（0.1%~20% 年化浮動、競價決定、可被召回）；現行
  TradeCosts 結構只能表達前者的一次性部分。
- **item 4 融券保證金維持率**：實務公式 = (擔保價款+保證金)/融券市值，
  開倉 190%（保證金成數 90%，監理可調——2025-04 危機期曾提高到 130%）、
  追繳線 130%（對應股價上漲 ~46% 才斷頭）。**已被自訂 ATR+15% 防線
  dominate 為更嚴格的約束**（我們的防線在 +5%~+15% 就觸發），維持率
  機制在本框架內幾乎不會 binding，故不建模。**資本效率但書（重要）**：
  融券實際凍結 190% 資金 vs 現行 `position_size` 假設名目=占用，資金
  效率被高估近一倍，影響報酬率分母——未來接近實盤時必須回頭處理。
- **item 5 券源限制**：crisis-period 尾部風險，且往往與 ATR/15% 防線
  觸發時期重疊（防線會先出場）。真實案例已查證記錄：2603 2010-07-26/27
  融券餘額≥融資餘額（券資比 100.75%，停券條件實際觸發）；8069 2007-12~
  2008 共 125 個交易日券源使用率 ≥90%（多數日子餘額直接超過被行政調降的
  限額，最低被砍到 1 張）——限額不是靜態股本比例，是會被監理動態調降的；
  對照 2330 全歷史使用率 max 1.19%（大型股券源永遠不是問題，**約束同樣
  分層**）。附帶發現：`chip_margin.short_limit` 欄位在 DB 中被存成 8-byte
  little-endian blob（nullable Int64 落地 roundtrip 問題），可無損解碼
  `int.from_bytes(v,'little')`，既有分析未用過此欄，記錄在案本輪不修。

### 實作（TDD，事前判準 P1~P4 先登錄後動工）

- **`backtest/event_engine.py` 第四次範圍性解鎖**（兩個新參數，預設 None=
  關閉=逐位不變，保護所有既有呼叫端）：
  - `short_uptick_rule_drop`：3.5% 禁空規則。放在**成交時點**檢查（非
    approve_order——執行約束不是風控約束：規則是成交價格條件式，高於前收
    仍可放空，決策時不知道明天成交價）。統一規則（兩種 fill_mode 都成立）：
    bar f 成交的 entry_short，若 close[f-1]/close[f-2]-1 ≤ -3.5% 且成交價
    （含滑價）< close[f-1] → 作廢、記入 `blocked_fills`。價格基底=還原價
    （交易所的跌幅基準本來就是除權息調整後參考價；「執行語意用原始價」
    邊界的但書已寫入 CLAUDE_2.md 與 data/adjustment.py docstring）。
  - `lock_up`/`lock_down`：漲跌停全日鎖死布林序列（runner 用原始價計算，
    分時代門檻 2015-06-01 前 7%／後 10%；引擎不含市場知識）。方向感知：
    漲停鎖死擋 BUY、跌停鎖死擋 SELL。風險縮減單（exit/forced_liquidation）
    **延後重試**到下一根未鎖死 bar（`deferred_fills` 記錄位移；延後期間
    mark-to-market 照常失血=誠實建模軋空、不重複產生強平單）；新倉單
    **作廢**（`blocked_fills`；訊號持續則下一根自然重新進場）；風險縮減
    單在途期間新倉單一律作廢（`pending_risk_reduction`，防舊倉未平新倉
    疊加）。
  - 新診斷欄位：`blocked_fills`（執行時攔截，區別於決策時 `rejections`）、
    `deferred_fills`（原定bar, 實際bar, reason）。
- **`backtest/liq_calibration.py` 新增 `stratified_forced_liq_n()`**：ATR
  分層 N 純函式。分層依據=股票自身 trailing ATR/price 分布（非市值/交易所
  別武斷分類）：`N = clamp(target_line_pct / trailing_median(ATR/close),
  n_min, n_max)`，預設 target=9%（15% 固定網的六成）、clamp [1.5, 5.0]、
  lookback 252。呼叫端只傳 train 側資料（嚴格因果）。直覺：把動態線長期
  典型水位錨定在 target，保留 ATR 短期適應性，只做跨股票尺度正規化。
  `risk/manager.py`／engine 零改動（經由既有 `RiskConfig.forced_liq_atr_n`
  參數注入）。
- 測試：`tests/test_event_engine_tw_rules.py` 14 個 + `tests/test_stratified_n.py`
  8 個，全部先紅後綠；既有測試逐位不受影響（判準 P3）。334→**356 tests**。
- 機制層診斷 runner：`run_tw_short_rules_diag.py`（4 檔 × 15-fold，v2 雙向
  測試訊號，P1/P2/P4 交叉驗證）。

### 機制層診斷結果（P1/P2/P4，含 P4 第一版 FAIL 與修正全過程）

**P1（uptick 規則）：PASS，空集合但已驗證正當性**——60 窗零攔截不是規則
沒生效：受限日真實存在（2330 54 天/1.6%、2603 175 天/5.1%、8069 473 天
/9.1%、6446 203 天/6.8%）；v2 測試訊號 60 窗合計 88 次空頭進場嘗試，恰有
2 次成交日落在受限日，但兩次開盤價都高於前收——依規則合法可空、正確放行。
規則在真實巧合上被運用過兩次且判斷正確；機制正確性另由 14 個單元測試覆蓋。

**P2（鎖死耦合）：PASS，空集合（合理）**——鎖死日僅佔 0.06%~0.56%，測試
訊號成交時點未撞上；延後/作廢語意由單元測試覆蓋（含延後重試、方向感知、
pending_risk_reduction 防疊倉、signal_close 模式延後）。

**★P4（分層 N）：第一版 median 錨定 FAIL → 根因診斷 → q95 修正 → 新判準
PASS（完整過程記錄，勿只看結論）★**

- **第一版（median 錨定 9%）FAIL**：事前預期「小型股 ≥15% 佔比塌縮到
  接近 0」，實際 8069 19.4%→4.5%（未達 ~0）、6446 13.3%→12.2%（幾乎沒
  改善）、2603 10.7%→**14.7%（惡化）**、2330 0.25%→**4.3%（惡化）**。
- **根因（已量化）**：①median 錨定 9% 只留 1.67 倍餘裕（15/9），但實測
  test 窗 q95 波動相對 train 尾段 median 的倍率 median 就有 1.46~1.79 倍、
  max 4.45 倍——**median 回答「平常日子線在哪」，機制要守的是「極端日子
  線會不會跑到固定網之上」，統計量與問題目標錯位**（與 RSI/regime 時間
  錯位同類教訓，見 AGENTS.md 對應節）；②train 尾段與 test 窗的波動
  regime 位移（2603 fold3：train 尾=平靜期→N=5.0，test 撞上 2015-08
  股災→75.5% bar 越線）。
- **修正（使用者拍板，兩件事一起）**：①錨定統計量 median→**trailing
  q95**、target 12%（保守端留餘裕）：把「連 95 分位波動日的線都留在網下」
  直接寫進公式；②**重新定義 P4 判準**：「trailing_q95 口徑下的典型極端值
  留在固定網之下」，不要求全部 bar 塌縮到 0——任何 trailing 校正都無法
  預見 train 段之後才發生的劇烈 regime 突變，這是「用歷史資料校正未來
  參數」的結構性限制（與漲跌停鎖死尾部風險同類：記錄不消除）。
- **q95 版結果（新判準 PASS）**：
  - (a) 常態 fold（test_q95 ≤ 1.25×train_q95，44/60）——塌縮達成：

    | symbol | 分層 q95 mean | vs 固定 N=3 mean |
    |---|---|---|
    | 2330 | 0.04% | 0.00%（不再惡化） |
    | 2603 | 1.35% | 4.67% |
    | 8069 | **0.86%** | 17.65% |
    | 6446 | **0.68%** | 11.72% |

  - (b) ★劇烈 regime 位移 fold（16/60）＝ trailing 校正的已知殘留風險，
    單獨列出不被平均掉★：最壞 **2603 fold9**（q95 位移 2.00 倍，對應
    2020-21 航運超級行情前夜的波動爆發）殘留 **21.3%**（固定 N=3 同窗
    31.5%，仍改善但殘留顯著）；其餘 15 個位移 fold 殘留 2.3%~16.0%。
    **誠實註記**：16 個位移 fold 中 5 個（2330 f3/f6/f13、2603 f6、
    6446 f5）分層版**劣於**固定 N=3——train 尾段低波動→校正把 N 拉高
    （至 4~5），隨後波動擴張時反而比靜態 N=3 傷——這是同一個結構性限制
    的另一面表現，不是 q95 修正沒做好；逐 bar 動態調整 N 之類的自適應
    設計屬下一層優化（使用者定案本輪不做，維持一輪一變因）。

## ★台股放空正式基準（2026-07-16，七環節整合第一輪，機制層/結果層全過）★

**定位**：台股放空執行層的**第一份正式基準數字**——七環節（①還原價
②不對稱成本必選 ③3.5% 禁空 ④漲跌停鎖死耦合 ⑤ATR 分層 N q95/12%＋15%
固定網 ⑥強制回補日曆 ⑦完整風控）第一次全開整合。策略 = v2（與 BTC 正式
基準同參數）；對照 = 多頭 baseline（與「台股 E2 化第一輪基準」同配置）
並排呈現、**不下優劣結論**。Runner：`run_tw_short_baseline.py`（全部讀
本地快照，零即時 API）。

### 機制層（使用者具體化的三層檢查，先於結果層判讀）

**I1 觸發清單 ISO（單獨開啟）vs FULL（七項全開）比對**：4 檔 × 15 fold ×
4 機制中 **3 筆差異，全部 drill-down 到具體因果鏈、全部是「合法的部位
路徑中介交互」，零機制誤傷**：
1. **2603 fold7 calendar**（ISO 多出 bar36-39 的日曆事件）：FULL 中價格
   強平在 bar20（2018-08-02 @4.47）提前平掉空頭，三週後的停券死線
   （bar36，2018-08-23）已無部位可回補——**價格強平先於日曆回補**，兩個
   平倉機制中先到者贏，設計語意正確。
2. **2603 fold9 price_liq**（FULL 多出 bar12 一次強平）：FULL 中日曆
   回補在 bar3（2020-04-17 COVID 期，@3.60）平掉原空單（04-15 進場
   @3.55），停券窗擋掉 bar3-6 再進場，bar7 重新進空 @3.41——**進場價
   更低 → 絕對觸發線更緊** → 四月底反彈在 bar12（04-30）越線強平
   （@3.69 回補）。ISO 無日曆機制，原 @3.55 空單的線從未被觸及、由
   訊號自然出場。「日曆回補→低價再進場→反彈觸發強平」是經濟上真實
   的鏈條，非 bug。
3. **8069 fold15 price_liq**（第二次強平 bar285→283 位移兩根）：同型
   鏈條——日曆回補 bar250（2026-03-20 @145）平掉 @180 空單、停券窗後
   @149 再進場，較低進場價使反彈觸發提前兩根（05-11 vs 05-13）。

**I2 三項疊加邊界（pytest 釘住，`tests/test_tw_rules_integration.py`）**：
1. uptick 不誤傷強平回補——「強平回補落在受限日且成交價低於前收」最壞
   組合下回補照常成交 ✓。
2. **同 bar 雙重平倉理由的處理（特徵化屬性，呈報使用者）**：價格強平
   的成交 bar 恰為停券死線時，現行語意 = 價格強平先建單、日曆層偵測
   在途單跳過——歸因記在 `liquidation_bars`、`calendar_cover_bars` 為空，
   **雙重理由成立的 bar 目前不留日曆側記錄**（單一回補單、無重複下單，
   行為正確；但若日後需要「雙重理由 bar」的統計，需另加診斷欄位）。
3. 鎖死延後 × 死線同日：疊加為**單一回補單持續重試**（一張 BUY、延後
   記錄完整、解鎖後一次回補），兩種邏輯不互相覆蓋不重複下單 ✓。

**機制層判定：PASS**——I1 三筆差異全部根因明確且屬合法交互（這正是
本輪要抓的「個別驗證都對、合在一起才出現」的現象，結論是交互存在
且全部符合設計語意）；I2 三邊界全過；uptick 全 60 fold 零攔截（與
放空第一輪診斷一致：88 次進場僅 2 次撞受限日且皆合法高於前收）；
鎖死延後/作廢在 FULL 配置零發生（v2 成交時點未撞全日鎖死 bar）。

### 結果層（R1：0/60 異常；60 fold-案例完整表）

配置：v2 七項全開 vs 多頭 baseline（並排、不下優劣結論）；
periods_per_year=252；slippage_bps=0.0（台股未校正，pin）；
回補日曆未覆蓋 25/60 fold（逐列標注）。

| symbol | v2 全開 mean±std | 多頭 baseline mean | 強平次數合計 | 日曆事件合計 |
|---|---|---|---|---|
| 2330 | **+18.7% ± 35.6%** | +12.3% | 4 | 5 |
| 2603 | **+17.9% ± 54.4%** | +15.6% | 6 | 16 |
| 8069 | **+9.4% ± 53.9%** | +11.6% | 15 | 7 |
| 6446 | **-4.5% ± 44.3%** | +3.3% | 12 | 0 |

（逐 fold 明細（60 列，含 regime/sharpe/各機制觸發數/熔斷日/覆蓋標注）
以 `run_tw_short_baseline.py` 一鍵重現，資料全凍結快照，逐位可重現。）

**判讀（管線層，非策略評價）**：std 遠大於 mean（36%~54%），與多頭
baseline 及加密貨幣既有結論同型——雙向策略在台股日頻同樣是高雜訊比；
小型股（8069/6446）強平次數明顯多於大型股（15/12 vs 4/6），與分層 N
的波動特性診斷一致；6446 的 v2 mean 為負且多頭亦僅 +3.3%，該標的本身
訊號品質弱（且回補日曆 12/15 fold 未覆蓋）。**這些觀察供未來輪次
形成假設用，本輪不下任何策略優劣結論。**

**已知限制（引用這批數字時必附）**：回補日曆 25/60 fold 未覆蓋
（2015-04 dataset 起點＋6446 融券資格 2023-07 起）；slippage 未校正
pin 0；items 1/4/5（借券費時間成本/資本效率 190%/券源）文件化未建模；
熔斷日頻塌縮與部分日鎖死不可偵測同前。

## 台股強制回補日曆（item 3，2026-07-16，實作完成＋C1~C5 全過）

**資料存在性查證（先於設計）**：FinMind 免費層有專門 dataset
`TaiwanStockMarginShortSaleSuspension`（stock_id/date=停券起/end_date=
停券迄/reason），reason 涵蓋全部強制回補觸發類型（除息/除權/除權息/
股東常會/現金增資/減資）。四檔實抓：2330 45 筆（2015-04 起，季配息後
年均 3.8 次）、2603 24 筆、8069 23 筆、6446 6 筆（2024-03 起——用自有
chip_margin 交叉確認 6446 融券資格 2023-07-28 才開始，屬真實反映非資料
缺漏）。與既有 DividendResult 交叉驗證：52 筆除權息類停券 51 筆精確對上
（除權息日=停券結束後 1 交易日），唯一失配是當時正在進行中的未來事件
——證明**事前公告制**。補充源：`TaiwanStockDividend` 含事前公告除息
交易日欄位。

**規則查證（非假設）**：停券窗禁新融券賣出 4 日；**最後回補日 = 停券窗
起始日（含）**，既有空頭須在該日前回補完成，逾期券商隔日市價強制回補
——新單面與既有部位面兩件事都要建模。

**歷史缺口處理（使用者定案選 (a)）**：dataset 起點 2015-04，之前時段
機制不生效（兩序列全 False，不觸發也不誤判）、不用 DividendResult 反推
近似（會製造只覆蓋一半 reason 類型的誤導假象）、不縮限整體回測範圍。

**實作（TDD，第五次範圍性解鎖，預設 None 逐位不變）**：
- `short_entry_ban`：停券窗內 entry_short 成交作廢（blocked_fills
  reason="short_sale_suspension"）；BUY 回補不受限。死線 bar 隱含禁新空
  （即使只給 deadline 未給 ban 窗，也不允許回補後同根重新進空）。
- `forced_cover_deadline`：成交 bar 為死線日且仍持空頭 → 產生
  reason=**"calendar_forced_cover"** 的 BUY 平倉單，記入
  `calendar_cover_bars`——與價格驅動 `liquidation_bars` **完全平行、
  歸因分離**（不碰 check_forced_liquidation/risk manager）；價格強平
  在途時跳過不重複下單；作為風險縮減單自動繼承鎖死延後語意（死線撞
  漲停=軋空高峰情境，測試覆蓋）。
- ★非未來函數：bar t 決策讀 t+1 日曆旗標=事前公告的公開資訊，本專案
  **第二個合法非因果例外**（第一個=還原股價）；通用分類標準已建立於
  AGENTS.md「使用未來已公告資訊的合法性分類」節（三條件+對照組+執行
  要求），第三次遇到直接套用。文件性斷言測試
  `test_calendar_flags_are_preannounced_information_not_lookahead` 釘住
  行為＋驗證價格資訊仍嚴格因果。★
- 測試 +12（`tests/test_event_engine_calendar_cover.py`），356→**368
  tests** 全綠（C3 逐位不變）。診斷 runner：`run_tw_calendar_cover_diag.py`。

**C1~C5 判準結果（機制層，4 檔 × 15-fold 真實資料）**：
- **C1 PASS**：6 筆 calendar_forced_cover 全部準時在死線 bar 成交
  （offset=0，無鎖死延後案例）；日期 2330 2022-06-10、2603 2015-07-17/
  2018-10-25/2020-04-17、8069 2025-07-18/2026-03-20。
- **C2 PASS**：零筆空頭進場成交落在任何停券窗內（以 fills 部位路徑
  重建獨立交叉驗證）。
- **C4 未覆蓋 fold 完整揭露：25/60**——2330 f1-f2、2603 f1-f2 完全未
  覆蓋（2015-04 前）+ 各自 f3 部分覆蓋；8069 f1-f6 完全未覆蓋 + f7
  部分覆蓋（8069 歷史最長、缺口最大）；6446 f1 部分覆蓋 + f2-f12 未
  覆蓋（融券資格 2023-07 起的真實反映，僅 f13-f15 覆蓋）。引用這批
  資料的放空回測數字時，未覆蓋時段的「無回補日曆」屬已知限制。
- **C5 PASS**：97 個窗內停券窗 96 個恰為 4 個交易日、死線=窗起始日
  97/97；唯一例外 8069 2024-07-19~24 窗只有 3 個交易 bar——對應
  2024-07-24/25 凱米颱風休市（真實可查證事件，非資料錯誤）。

**主動避開量測（純量測，未來開優化輪的決策輸入）**：6 個撞死線案例，
持有天數 2~43（median 14）、來回費用合計 0.0149（equity=1.0 基準）；
PnL 正負皆有（-0.037~+0.060）——「避開」省的是確定的來回費用、放棄的
是不確定的持有損益，是否值得屬策略層假設檢驗，本輪不決定。

## 台股 regime/ATR 參數校正（2026-07-16，★結案：維持現值不變更，但 P1 有重大結構性發現，完整記錄如下，未來放空設計必讀★）

**問題**：BTC 1h 校正出的 ATR N=3/window=14 與 regime_window=120，套到
台股日K 上 bar 數字沒變、代表的實際時間長度完全不同（ATR window 14 小時
→14 個交易日≈3 週；regime_window 120 小時=5 天→120 個交易日≈6 個月）。
這輪只驗證現行值是否退化，不做網格搜尋。Runner：
`run_tw_atr_regime_calibration.py`（重用 `liq_calibration.py`，零改動任何
既有模組）。4 檔股票各自完整歷史還原價 × 15-fold。

**P0（動工前確認）**：剛結案的台股 E2 基準（純多頭 `ma_rsi_regime.py` +
`forced_liquidation=False` + `sizing_mode="leverage_cap"`）裡 **ATR 完全
沒被呼叫過**（`need_atr=False`，連算都沒算），是結構性休眠非巧合——所以
本輪任何發現都不影響已結案的基準數字。regime_window=120 則活在那批基準的
訊號生成裡，P3 若有問題會回頭牽動基準（結果：沒有，見 P3）。

### P1：觸發密度 + ★固定網角色的結構性發現（本輪最重要輸出）★

觸發密度（v2 雙向測試訊號的空頭區段，延續 BTC 校正輪方法論；**非實際
部署策略**）：4 檔 25%~60%（合計 95 區段 36 觸發、37.9%），非零非病態。

**★固定網「純異常備援」不變量在台股被打破——依股票分層★**。觸發時點
的動態線水位（N×ATR/entry）三組幾乎不重疊：

| 組別 | n | N×ATR/entry 分布 | 含義 |
|---|---|---|---|
| atr-only | 26 | median 8.08%（4.24%~11.56%） | 動態線明顯低於 15%，第一層敏感角色正常 |
| both（同根雙線齊穿） | 7 | median 15.44%（10.52%~16.56%） | 動態線膨脹到與固定網**同水位**，兩線收斂 |
| fixed-only | 3 | median 22.87%（19.02%~24.97%） | 動態線**高於**固定網，**兩層角色徹底反轉** |

fixed-only 3 筆全部對應可查證真實危機：8069 2008-02-25／2008-11-07
（金融海嘯）、6446 2015-08-28（2015 年 8 月全球股災）——在最需要防線的
時刻，設計上的「第一層」動態線反而是慢的那條。

**全歷史 bar 層級（不限觸發時點，3×ATR/close 分布）——證明這是結構性
而非事件性**：

| symbol | median | 動態線 ≥15% 的 bar 佔比 | ≥12% 佔比 |
|---|---|---|---|
| 2330（大型權值股） | 5.52% | **0.23%** | 1.04% |
| 2603 | 7.79% | **10.03%** | 16.43% |
| 8069（TPEx 小型股） | 11.49% | **19.52%** | 44.47% |
| 6446（TPEx 小型股） | 9.66% | **13.43%** | 28.76% |

**判讀**：N=3 對 2330 設計意圖完好（0.23%，與 BTC/ETH 同型）；對 TPEx
小型股是**長期性結構失效**——8069 近五分之一的交易日動態線在固定網之上、
近半交易日在 12% 以上，「動態線是更敏感的第一層」這個設計前提對小型股
**長期不成立**，不是危機期偶發。根因：N=3 是用 BTC 1h 的單根波動/ATR
尺度校正的；台股日K 一根吸收整天波動，小型股日 ATR/價格比長期 3~5%，
乘 3 自然坐在 10~15% 區間——頻率×波動特性的結構性結果。

**★處置（使用者 2026-07-16 拍板）：不現在開 N 值校正輪、不變更任何參數★**。
理由：ATR 強平在現行台股基準（純多頭）本來就不被使用，校正一個尚未
被使用機制的參數時機不對；正確的 N 值（甚至要不要按股票波動特性分層）
本質上要等台股放空策略設計成型才能回答——「要不要分層」本身是策略設計
決策，不是單純參數調整。**本發現未導致任何參數變更，是保留給「未來
台股放空設計」任務的關鍵輸入**（見「掛帳」清單的對應加註）。

### P2：機制預測 vs 實際 E2 觸發 bar 集合（59/60 一致，1 例已根因分析）

2330/2603/6446 三檔 15-fold 全部逐位一致。8069 fold5 一筆位移
（機制預測 bar315 vs E2 實際 bar314）：訊號 bar303（2013-01-24）轉 -1
當天熔斷擋單（`rejected_circuit_breaker` 逐位確認）、bar304 隔天跨日
重置後重新進場成交 14.46，而 `liq_calibration` 的近似進場價用 bar303
收盤 14.60（不知道熔斷存在）——進場價高 0.14 → 觸發線偏高 → 預測晚一根。
**非 bug**：`liq_calibration.py` docstring 本來就標注進場價為已知近似；
與 ETH 輪 P1（部位整段不存在）同族但更輕微（僅一根位移）。發生率 1/60。

### P3：regime_window=120 頻率轉換敏感度（PASS，基準不需重看）

window∈{60,120,180,252}（≈3/6/9/12 個月）掃描 4 檔全歷史：分布全部平滑
過渡（如 2330 trend_up 43.6%→48.1%→54.2%→58.5%），**120 無孤立斷點**。
已結案的台股 E2 基準（建立在 regime_window=120 上）不需因此重新檢視。

**結案狀態**：N=3/window=14、regime_window=120 全部維持現值；P1 分層
發現完整記錄未打折；AGENTS.md 新增「跨市場/頻率沿用風控參數需檢查子群體
波動特性分布」方法論教訓。

## ATR 強平防線校正（2026-07-13，機制層＋結果層完成，★已定案：維持現值 N=3.0/w=14，使用者拍板★）

> **★數字基準 = 0bp（歷史快照，2026-07-15 標注）★**：`run_atr_calibration.py`
> 已明確 pin `slippage_bps=0.0`，本節結果層 E2 數字逐位可重現、不隨新預設值
> （2bp，見「滑價敏感度」節）變動。**方向性結論不受影響**——「維持現值
> N=3.0/w=14」建立在觸發 bar 集合與誤殺/保護分類的機制層不變量上，與滑價
> 無關；機制層（mech/high 兩階段）本身不呼叫 event_engine，完全不受本次
> 變更觸及。

**任務**：`check_forced_liquidation` 的 ATR 動態防線 N（現值 3.0）與 window（現值 14）
自放空風控輪起標注「待校正，非最終值」，是最後一個待校正參數。使用者 2026-07-13
確認完整規格後動工（TDD，259 tests）。

**規格要點（使用者定案）**：close-only 觸發（high 觸價敏感度另開一輪，仍掛帳）；
網格 N∈{1.5,2,3,4} × window∈{7,14,21}；凍結 klines.sqlite 三個互相零重疊 8000 根窗
（C/D/A，窗B 與 A 重疊 86% 依教訓排除）× 各 15-fold；訊號 = v2 空頭區段；
任何主張需三窗方向一致；**預設立場 = 維持現值**；誤殺判準（觸發後該空單持有到
自然翻轉其實是賺的）是事後全知視角、有系統性把門檻校鬆的偏差，權衡表須併陳
「觸發當下未實現虧損」分布。

**樣本量（誠實標注，動工前先數）**：三窗空頭區段合計 **165**（C 54 / D 42 / A 69），
區段長度中位數 14~19 根、含 1 根極短段，單 fold 區段數 0~15 不均。每組合每窗
觸發數僅 9~34，**組合間差異多為個位數事件，本輪所有比較都在小樣本域**，
12 組合 × 3 窗的表面完整度不代表統計檢定力。

**機制層結果（run_atr_calibration.py --stage mech 可重現）**：
1. **★固定網 15% 在全部 12 組合 × 三窗 × 所有觸發中，一次都沒有先於 ATR 線觸發★**
   （三窗最大不利波幅 8.58~9.67% < 15%）——現行資料域內固定網純屬 ATR 異常備援，
   本輪只動 N/window 而凍結 15% 是安全的設計選擇。
2. 現值 N=3/14：觸發 17/19/17 次（C/D/A），**誤殺 0/0/1**、保護 17/19/16，
   觸發當下浮虧 med 2.06~2.93%、max 5.75~7.57%。
3. 收緊（N=1.5/2.0）：觸發變多，保護增加但誤殺同步增加（N=1.5 誤殺至 +6），
   觸發浮虧 med 降至 ~1.3-1.7%（更早介入）。**沒有任何組合做到「誤殺減少且保護
   不減」的三窗一致改善**——現值誤殺本來就近零，沒有可收割的改善空間。
4. 放寬（N=4）：三窗一致少 4~7 次保護，換到的誤殺減少幾乎為零（0/0/-1），
   且觸發時浮虧已達 med 2.7~3.75%、max 8.1~8.9%。

**結果層 E2（入圍 3 組：現值 3.0/14、收緊代表 2.0/21［機制層誤殺增幅最小
+1/+0/+0 且保護一致增加］、放寬探針 4.0/14）**：

| 窗（15-fold E2 年化 mean/std） | N=3.0/14（現值） | N=2.0/21 | N=4.0/14 |
|---|---|---|---|
| C（2023-08→2024-07） | +135.1%/289.3%（強平17） | +137.7%（25） | +132.4%（12） |
| D（2024-07→2025-06） | +359.5%/1325.5%（19） | +361.9%（21） | +345.3%（15） |
| A（2025-06→2026-05） | -10.4%/75.0%（17） | -7.7%（25） | -10.7%（9） |

乾淨基準 5-fold 重點案例：**fold4（空頭大賺 +27.5%）在三組下都 0 次強平、
逐位不變——沒有任何候選會誤殺它**；fold3 同樣三組不變。差異全在已知觸發的
fold1/2/5：2.0/21 改善 f1（-17.7% vs -31.1%）與 f5（+9.0% vs -1.2%）；
4.0/14 惡化 f1（-37.5%）與 f2（+8.1% vs +13.9%）。

**判讀（依校正紀律，改善量級供拍板）**：
- **2.0/21 三窗方向一致優於現值，但量級僅 +2.4~+2.7pp 年化 mean**，對照
  fold 間 std 75%~1325%（mean 的標準誤 ≈19~342pp），統計上深陷雜訊；且
  2.0/21 是**看過機制層數據後入圍的**（選擇偏差，E2 的一致小勝需再打折）；
  乾淨基準 f1/f5 的改善同屬「這批已知觸發案例」的事後觀察。機制層代價：
  觸發次數 17→21~25（介入更頻繁）、窗C 誤殺 +1。
- **4.0/14 三窗一致變差（-2.7/-14.2/-0.3pp）**：「放寬沒有好處」是本輪
  方向一致且量級相對明確的結論，機制層（保護少 4~7 次、換不到誤殺減少）
  與結果層互相印證。
- **★最終建議：維持現值 N=3.0 / window=14★**——業界起點值在本資料域表現
  已近最優（誤殺近零、fold4 不誤殺、放寬確定變差、收緊的改善在雜訊內）。
  若使用者想採 2.0/21，該 +2.5pp 量級與其選擇偏差已如實列出，可自行權衡。
  **→ 2026-07-13 使用者拍板：維持現值，正式定案。N×ATR 自此成為明確的
  停損定義，後續供規則一 position_size() 使用。**

**本輪未做**：盤中 high 觸價敏感度（觸發時機變因，另開一輪——
**→ 已於 2026-07-14 完成並結案，見下節「high 觸價敏感度」**）；固定網 15%
的校正（本輪凍結；資料域內從未先觸發，優先級低，high 輪重驗後仍成立）。

## high 觸價敏感度（2026-07-14，★結案：接受 close-only 簡化，使用者拍板★）

**定調（與 ATR 校正輪的本質差異）**：這不是參數校正，是 close-only 觸發
（E2 決策③）簡化假設的**壓力測試**——問題是「close-only 有沒有系統性漏看
盤中已觸線的強平事件、低估風險」，不是「哪個觸發依據較好」。high ≥ close
是數學保證（觸發集合 ⊇ close 模式、觸發時點 ≤；property test ＋全部實資料
區段斷言雙重背書），沒有模糊地帶，只量化「差多少、差在哪」。

**變因控制**：N=3.0/w=14 凍結（不重掃網格）、固定網 15% 凍結、機制層純量測
不涉 sizing；三個零重疊窗 C/D/A × 15-fold ＋乾淨基準 5-fold。實作 =
`liq_calibration.py` 加 `trigger_source="close"|"high"`（預設 close 逐位不變，
保護性回歸測試）；**event_engine.py 未動（不要動清單未破）**。268→277 tests。
`run_atr_calibration.py --stage high` 一鍵重現。close 模式觸發數
17/19/17（C/D/A）＋乾淨 4 與校正輪逐位重現，無資料漂移。

**結果**：
1. **★fold4：high 下仍 0 觸發★**——5 個空頭區段安全邊際 +0.54~+2.89×ATR
   （最貼近的是大賺空單段 seg2/41 根，盤中 high 最高 2.46×ATR，距線 +0.54）。
   「fold4 無強平、無誤殺」不是 close/high 度量差距下的僥倖。
2. 觸發次數 57→67（**+17.5%**；C +23.5% / D +5.3% / A +23.5% / 乾淨 +25%；
   乾淨窗新增 1 筆與窗A f13 為同一事件——乾淨窗與 A 時間重疊，**獨立新增
   = 9 筆**）。增幅落在事前預估 +15~35% 區間。**D 窗量級與 C/A 不一致
   （+5.3% vs +23.5%）、每窗新增僅 1~4 筆，小樣本域**——量級跨窗不齊，但
   強度分析顯示的淺層性質三窗一致（見 3）。
3. **強度分析（判決的關鍵證據）**：9 筆獨立新增觸發全數為「碰線即回」型。
   反事實逐筆對照：若真在觸線價強平，結果**全部等於或略差於** close-only
   繼續持有到自然翻轉（逐筆差 0.04~1.74pp，未年化）；漏掉事件的盤中最壞
   浮虧上界僅 3.40%（窗A f12）。分類 8 保護／1 誤殺（C f1：觸線 +0.36% vs
   持有其實 +1.38%）。**close-only 漏看的是淺浮虧邊緣事件，沒有深水炸彈。**
4. 提早量：57 筆共同觸發中 27 筆（47%）同一根、多數提早 1~4 根；2 筆離群
   （C +19 根、A +12 根）觸線浮虧僅 1.22%/1.67%，損益差可忽略。
5. **固定網 15%：high 下 67 次觸發仍 100% 由 ATR 線先觸發**（fixed/both 皆
   0，四窗一致）——「固定網純屬 ATR 異常備援」在盤中觸價語意下依然成立。

**判決（2026-07-14 使用者拍板）**：接受 close-only 簡化為足夠近似，不解鎖
event_engine.py 做報酬層驗證，掛帳項①正式結案。**理由（使用者原話要旨）**：
+17.5% 技術上超過事前設定的 10% 自動結案門檻，但門檻的作用是**觸發更深入
檢驗**，不是自動要求開新工程；本輪已同步完成的強度分析回答了門檻真正想問
的問題——「漏看的事件有沒有藏著嚴重風險」，答案是沒有。既然更深入的檢驗
已完成且結論明確，不需為技術性超標再付出解鎖 event_engine.py＋重跑雙層
驗證的成本，去確認一個高機率早已知道答案（差異在雜訊內）的結論。

**已知近似（誠實標注，寫在 liq_calibration docstring）**：high 模式觸線浮虧
為觸線價近似，未含滑點與開盤跳空（當根開盤即高於線時實際成交價更差，此處
低估）；與 overlay/機制層既有近似標注同級。

## 規則一 sizing 接入 E2（2026-07-13，完成；基準是否切換等使用者決定）

> **★數字基準 = 0bp（歷史快照，2026-07-15 標注）★**：`run_rule1_sizing.py`
> 已明確 pin `slippage_bps=0.0`，本節 Δ規則一數字（曝險倍數、強平損失/
> 預算比、fold4 參與度分析）逐位可重現、不隨新預設值（2bp）變動。判讀
> 建立在觸發 bar 集合相同、損失/預算比率的機制層分析上，方向性結論
> （E2 基準不切換、維持 leverage_cap）不受滑價影響。

**任務**：ATR N=3/w=14 定案後，N×ATR 成為明確停損定義，規則一
（risk_per_trade 比例倉位）接入 E2 取代「槓桿上限全倉」。**五決策點使用者
核可（2026-07-13）**：①只接空頭（選項A，多頭無停損機制、sizing 用假停損
違背規則一語意）；②決策 bar ATR NaN → fallback 槓桿上限；③short_risk_
multiplier 維持 0.5（校正留待 E2_r1 基準建立後另開一輪）；④event_engine.py
範圍性解鎖（sizing_mode 參數＋空頭進場數量分支＋OrderEvent.stop_distance
預設 None，比照 vector_engine costs 先例，完成後鎖回）；⑤對照範圍 =
乾淨基準＋C/D/A 三窗。補充要求：強平損失/預算逐 fold 逐筆呈現，不只彙總
（防彙總掩蓋個別 fold 超越幅度，比照 2603 fold3 教訓）。

**實作（TDD，259→268 tests）**：`sizing_mode="risk_per_trade"` 下空頭進場
數量 = `risk.position_size(fill_price, fill_price + forced_liq_atr_n×ATR[決策bar],
"short")`（規則二 0.5x 上限內建封頂）；停損距離一律取**決策 bar** ATR
（fill bar ATR 含未來資訊）；文件化近似：sizing 用進場時 ATR 快照而強平線
逐根重算、next_open 成交時權益 ≤1 根陳舊。

**雙層回歸（保護現行結果，全過）**：①單元層 9 新測試含「顯式 leverage_cap
≡ 預設逐位相同」；②系統層 run_phase2_event.py 改動前後完整輸出
**byte-identical PASS**——過去所有 E2 數字不受影響。

**Δ規則一結果（run_rule1_sizing.py 可重現；E2_r1−E2_cap，年化）**：

| 窗 | cap mean/std | r1 mean/std | Δ mean | 平均\|Δ\| |
|---|---|---|---|---|
| 乾淨基準(5f) | +7.8%/25.0% | +6.4%/20.1% | -1.4pp | 10.3pp |
| C(15f) | +135.1%/289.3% | +146.8%/293.3% | +11.6pp | 11.6pp |
| D(15f) | +359.5%/1325.5% | +388.2%/1392.3% | +28.6pp | 29.1pp |
| A(15f) | -10.4%/75.0% | -6.8%/82.1% | +3.6pp | 12.6pp |

**判讀（與 Δsizing規則二同型的教訓）**：
1. **Δ的主成分仍是曝險量，不是訊號品質**：r1 空頭平均名目曝險 0.12~0.42x
   （vs cap 固定 0.5x）——虧錢空單 fold 曝險縮小 → Δ>0 居多；賺錢空單 fold
   同樣被縮 → Δ<0（乾淨基準 f4 -29.2pp、窗A f5 -25.5pp/f10 -24.5pp）。
   D 窗 Δ mean +28.6 被單一 f5（cap +5141%→r1 +5410%，Δ+270pp）主導，
   勿引用彙總 Δ 當規則一「更好」的證據。
2. **★fold4 核心案例：無誤殺、但參與度被吃★**——乾淨基準 f4 強平 0 次
   （cap/r1 相同），+27.5%→-1.7% 全來自高波動下跌段 ATR 大 → 倉位縮到
   平均 0.15x。規則一用「單筆虧損≈固定預算」換「高波動段（含賺錢段）
   參與度下降」，這是本質權衡，兩面都要看。
3. **風險預算兌現良好**：全部 57 筆強平（乾淨 4＋C 17＋D 19＋A 17；與 ATR
   校正輪/high 輪 close 模式觸發數逐位一致——觸發集合 cap/r1 相同的直接推論。
   原文誤植 44，2026-07-15 依 run_rule1_sizing.py 重跑更正）的實際損失/0.5%
   預算 = **0.33x~1.94x**
   （close-only 超越損失上限約 2 倍預算，即單筆最大實損 ≈1% 權益）；
   逐 fold 分布已列（run_rule1_sizing.py 輸出），無單一 fold 特別失控
   （最大三筆：D f12 1.94x、乾淨 f1 1.89x、A f13 1.89x）。
4. **規則一/規則二分工**：封頂（3×ATR/price<1%）僅 ~5% 進場生效（185 筆
   進場中 9 筆）——規則一為主要約束、規則二退為低波動安全繩，符合設計。
5. **觸發 bar 集合逐 fold 相同**（強平次數 cap/r1 全部相等）：sizing 不影響
   觸發機制，機制層不變。ATR NaN fallback 全程 0 次（全窗 ATR 切片無 NaN）。

**基準切換：★已定案不切換（2026-07-13 使用者拍板）★**——E2 正式基準維持
leverage_cap。理由：固定曝險 vs 呼吸曝險是**風控哲學選擇**而非「數字好就換」
的技術問題，Δ分析已證明差異主成分是曝險量，尚無足夠證據判斷哪種哲學更適合
此策略族。r1 完整實作＋雙層回歸驗證保留，日後做哲學選擇（如 ML 訊號層要
決定底層 sizing）時直接用現成路徑跑對照。short_risk_multiplier=0.5 校正
維持掛帳，屆時一併回頭處理（**→ 已於 2026-07-14 另開一輪完成並結案：
維持 0.5，見「short_risk_multiplier 校正」節**）。方法論教訓（sizing 設計
vs 風控介入的歸因區分）已記入 AGENTS.md「向量化回測的再平衡假設偏差」節
補充二。

## short_risk_multiplier 校正（2026-07-14，★結案：維持 m=0.5，使用者拍板★）

> **★數字基準 = 0bp（歷史快照，2026-07-15 標注）★**：`run_multiplier_
> calibration.py` 已明確 pin `slippage_bps=0.0`，本節 P1~P4 事前預期對照表
> 與報酬/風險端數字逐位可重現、不隨新預設值（2bp）變動。P1（觸發 bar 集合
> 跨 m 不變）、P2（損失/預算比率跨 m 逐位不變）皆為機制層不變量，與滑價
> 無關；判決「維持 m=0.5」不受影響。

**定調**：校正對象 = `RiskConfig.short_risk_multiplier`（現值 0.5）。只在
r1 路徑（`sizing_mode="risk_per_trade"`）生效，**正式基準 leverage_cap 不受
影響**；規則二 `max_short_leverage` 的 0.5x 是獨立參數，本輪凍結。網格
m ∈ {0.25, 0.5, 0.75, 1.0}（1.0 = 「空頭風險預算不打折」參照點，直接檢驗
折減前提本身）。**改動判準（事前設定）**：(a) 三窗方向一致且乾淨基準不反例、
(b) 風險端有實質差異（不能只是報酬端線性放大）、(c) 量級對 fold std 站得住；
預設立場 = 維持 0.5。事前預期 P1~P4 寫在 `run_multiplier_calibration.py`
docstring（可證偽），下表逐條對照。

**變因控制**：N=3.0/w=14、固定網 15%、規則二 0.5x 封頂全部凍結；範圍 =
乾淨基準 5-fold＋C/D/A 三零重疊窗 × 15-fold（與規則一輪同）。實作（TDD，
277→284 tests）：`run_multiplier_calibration.py`＋`tests/test_multiplier_sizing.py`
（7 tests）；run_rule1_sizing.py 的預算改參數化（預設 = 現值，**本檔輸出
逐位不變**有回歸測試）。`run_multiplier_calibration.py` 一鍵重現
（2026-07-15 文件補記時已重跑，數字逐位重現）。

**事前預期對照（P1~P3 = 機制層，精準命中；P2 若不成立本應停下深究）**：

| 事前預期 | 實測 |
|---|---|
| P1 觸發 bar 集合跨 m 不變（forced_liq 只看倉位正負號） | ✓ 四範圍全 fold 一致：強平 4/17/19/17（乾淨/C/D/A，計 57 筆）在每個 m 下逐位相同 |
| P2 未封頂強平的損失/預算比率跨 m 逐位不變（損失∝m、預算∝m 約掉）；封頂事件比率 < 未封頂 | ✓ 155 個比較對最大偏差 2.2e-16（純浮點噪音）；16 個封頂改變的 (事件,m) 組合，比率全數 ≤ 現值同筆（如 D f5 1.43x→1.00x@m=1） |
| P3 封頂生效率隨 m 上升（封頂 ⟺ 3×ATR/price < 2%×m）；未封頂段曝險 ∝ m | ✓ 封頂率 2.9~7.4%（m=0.5）→ 13~21.4%（0.75）→ 38.1~45.0%（1.0）；空頭曝險 fold 均 0.11~0.13（0.25）→ 0.23~0.25（0.5）→ 0.38~0.43（1.0），高 m 端因封頂介入偏離純線性 |
| P4 報酬端 Δ(m−0.5) 方向 = 該窗空頭淨損益方向 × sign(m−0.5)，跨窗大概率不一致 → 誠實預期 = 維持 0.5 | 機制成立，但「跨窗不一致」未出現：四範圍空頭 bar 合計皆淨虧 → 小 m 一致略優（見下）；結論仍 = 維持 0.5，理由改由判準 (b)(c) 承擔 |

**結果層——報酬端（對照軸 = r1(m) vs r1(0.5)；E2_cap 只做定錨不參與比較）**：

| 範圍 | Δmean(0.25−0.5) | Δmean(0.75−0.5) | Δmean(1.0−0.5) | r1 fold std 量級 |
|---|---|---|---|---|
| 乾淨基準(5f) | +0.8pp | -1.5pp | -2.5pp | 19~22% |
| C(15f) | +2.9pp | -3.1pp | -4.4pp | ~293% |
| D(15f) | +0.7pp | -20.7pp | -23.6pp | ~1324-1392% |
| A(15f) | +2.0pp | -1.8pp | -3.4pp | 75~87% |

- 四範圍方向一致（m 越小 mean 越高）＝本輪資料域空頭淨虧下「縮預算自然
  好看」的線性效果，但：①量級 +0.7~+2.9pp 深陷 fold std；②逐 fold 符號
  並不一致（C f9/f15、D f15 等 m 大反而好），且被單 fold 主導——A 窗
  +2.0pp 幾乎全由 f12 一根（+33.9pp）貢獻、D 窗 0.75/1.0 的 -20.7/-23.6pp
  被 f5 單 fold（-269.6pp）主導。與 Δ規則一同型教訓：**勿引用彙總 Δ 當
  「m 越小越好」的證據**。

**結果層——風險端（判準 (b) 的直接檢驗：無實質差異）**：
- 損失/預算比率分布跨 m 幾乎不動：m=0.25/0.5/0.75 的 min/med/max 與 >1x
  筆數**完全相同**（P2 的必然結果）；m=1.0 僅封頂事件壓低個別比率（>1x
  各窗 -1、C 窗 max 1.41→1.37）。全網格 >2x 預算 = 0 筆、全域 max 1.94x
  （D f12，未封頂、跨 m 不變）。
- **★「比率不變」≠「絕對風險不變」★**：單筆實際損失 = 比率 × (1%×m)，
  隨 m 線性放大（m=1.0 時 1.94x ⇒ 單筆最大實損 ≈1.9% 權益 vs 現值 ≈1%）。
  校正軸上不存在「同報酬更低風險」或「同風險更高報酬」的組合——報酬與
  絕對風險共用同一個線性係數，這正是判準 (b) 說的「只是報酬端線性放大」。
- m=1.0 的結構性副作用：規則二封頂率升到 38~45%（185 筆進場 77 筆封頂
  vs 現值 9 筆）——「規則一主約束、規則二退安全繩」的分工在 m=1.0 崩解，
  規則二變成常態約束，倉位語意混成兩套規則的拼接。

**乾淨基準 fold4 聚焦（參與度 vs 預算的線性兌換，全網格 0 強平）**：
cap 定錨 +27.5%；r1@0.25 -7.4%（曝險 0.076x）→ @0.5 -1.7%（0.153x）→
@0.75 +4.3%（0.229x）→ @1.0 +10.7%（0.305x）。年化與曝險隨 m 近似線性
回向 cap，但即使 m=1.0（預算不打折）曝險仍只有 0.305x、只收復 +10.7%：
**規則一「ATR 呼吸」造成的高波動段參與度缺口不是 m 補得回來的，加大 m
= 等比多冒險多參與的直接兌換，非免費改善**——與規則一節「本質權衡非
誤殺」互相印證。

**判決（2026-07-14 使用者拍板）：維持 m=0.5**。依事前判準：(a) 小 m 的
彙總方向雖四範圍一致，但 (b) 風險端無實質差異——P2 證明損失/預算比率
跨 m 逐位不變，報酬差異純屬曝險線性縮放；(c) 量級深陷雜訊且被單 fold
主導。m 本質是**風險胃納參數**（願意撥給空頭多少預算），與 cap vs r1
同屬風控哲學選擇、非資料可判定的技術優劣；機制層 P1~P3 精準命中同時
證明 r1 路徑實作行為完全符合設計、無隱藏非線性。**至此規則一輪的全部
掛帳（sizing 接入 → 基準不切換 → multiplier 校正）結案**；r1 路徑含
m 參數保留為現成對照工具，日後哲學選擇時直接重用。

**已知近似**：同規則一節（sizing 用決策 bar ATR 快照、next_open 成交
權益 ≤1 根陳舊）；封頂判定門檻隨 m 移動（2%×m）已參數化於 _entry_stats。

## Phase 4 ML 訊號層第一輪（2026-07-15 判定；★同日正式結案：使用者拍板，不開第二輪★）

> **★數字基準 = 0bp（歷史快照，2026-07-15 當日標注）★**：`run_ml_signal.py`
> 兩處呼叫（smoke／e2）皆已明確 pin `slippage_bps=0.0`，本節判準結果表逐位
> 可重現、不隨同日稍後拍板的新預設值（2bp）變動。判準 (a)(b)(c) 全 FAIL 的
> 結論建立在配對 Δ 的統計檢定上，v2 與 ML 兩側同時受滑價影響、方向性結論
> 不受此次基準切換影響。

**任務定位（使用者 2026-07-15 核可設計後動工）**：「值不值得投入 ML」的
驗證性任務——ML 學現有特徵的非線性組合能否超越 v2，花最少成本得到誠實
答案，非生產級模型。**一次定案跑批，無績效導向重跑**（事前重申的紀律，
已遵守：判定跑批只跑了一次）。

**設計要點（四決策點使用者拍板）**：
- **基準 = E2**（next_open/fee 5bps/RiskManager 預設/leverage_cap），ML 只換
  訊號層，Δ(ML−v2) 只反映訊號品質（向量化空頭失真教訓的直接防範）。
  實跑 v2 各窗 E2 數字與既有記錄**逐位重現**（乾淨 +7.8%/C +135.1%/
  D +359.5%/A -10.4%），配對前提成立。
- **特徵預先註冊 9 欄鎖定**：基礎 5（ma_ratio/close_dev/rsi14/atr_norm/
  trend_down）＋交互 4——完全由 v2/baseline 規則的 AND 結構窮舉映射
  （多頭 1 配對＋空頭三元 AND 的 C(3,2)=3 配對），零裁量零試跑。無籌碼
  特徵（已否證線不碰）。
- **模型 = L2 邏輯回歸固定 C=1.0/lbfgs**（無調參、無早停 → 消滅該洩漏
  通道；凸優化確定性 → 多 seed 規範自動滿足）；純線性 LR 連 v2 的 AND
  都表達不了，故交互項是公平檢驗的必要條件，不是加料。
- label = k=1 forward return 方向（D1）；訓練 = expanding 全歷史＋purge k
  根（D2）；τ 中性帶 = 訓練集尾端 20% 內部驗證選取，grid {0,.02,.05,.10,.15}
  平手取大（D3）；判準 (b) 合併 t≥2（D4，不拉到 t≥3——四判準疊加已夠保守）。
- ML 特有洩漏防治 9 條測試背書（tests/test_ml_signal.py）：特徵因果性、
  purge 邊界（竄改 test 首根 close 訓練逐位不變）、測試期零洩漏、τ 單調、
  AND 可表達性、確定性、單類別退化空手。284→**293 tests**。

**判定結果（run_ml_signal.py --stage e2 一鍵重現；Δ = ML−v2 配對年化）**：

| 窗 | Δmean | SE | t | 剔除對ML最有利fold後 | 逐fold勝率 |
|---|---|---|---|---|---|
| C | -149.3% | 75.9% | -1.97 | -166.4% | 40% |
| D | +560.6% | 558.5% | +1.00 | **+3.8%** | 40% |
| A | +9.0% | 20.0% | +0.45 | +2.6% | 53% |
| 合併(45 fold) | +140.1% | — | +0.74 | — | 44% |

- **(a) 三窗方向一致：FAIL**（C 大幅為負）。**(b) 量級：FAIL**（合併 t 0.74
  遠低於 2、勝率 44%）。**(c) 非單 fold 主導：FAIL**（D 窗 +560.6% 幾乎全由
  f5 一根貢獻——v2 +5141% vs ML +13497%、Δ+8356pp，剔除後只剩 +3.8%；
  正是判準 (c) 設計要攔的型態）。**排除 3 個 thin fold（窗C 前段，n_train
  580~1580）複驗：三判準仍全 FAIL**，結論不因訓練集過薄 fold 改變。
- **(d) 歸因（Δ 的本質不是選 bar 品質）**：ML 行為兩極——τ 選 0.15 的
  fold（27/45）**全空手**（annual 0.0%，Δ = −v2 該 fold 報酬，純參與度效果）；
  τ 選 0 的 fold（窗D 中段連續 7 個、C f1）換手暴增（單 fold 成交 98~157 筆
  vs v2 約 30 筆），成本拖累與單 fold 幸運並存。內部驗證在「全進」與
  「全不進」間擺盪，正是底層機率貼著 0.5、無穩定訊號的行為特徵。
- **乾淨基準定錨（與窗A重疊不入判準）**：ML 5 fold 全空手（τ=0.10~0.15），
  mean 0.0% vs v2 +7.8%。

**結論**：在現有資料量（單 symbol BTC 1h、25,300 根）、現有特徵集（MA/RSI/
ATR/regime 衍生量）、k=1 方向預測的形式下，**LR＋交互項未顯示任何可偵測的
優勢，多數情況下模型學到的最優行為是「不要交易」**——與既有結論「1h 尺度
此策略族訊號雜訊比本來就高」互相印證。依事前預設立場：**v2 續任正式基準**。

**本輪結論的邊界（誠實標注，事前已註冊）**：
- 檢定力只夠偵測「大且一致」的改善（約 6~15pp 年化）；「ML 有小幅真實
  優勢但被雜訊淹沒」與「ML 無優勢」在本設計下不可區分，依判準同判不值得。
- 只對 BTC 1h、此特徵集、此模型類（線性+註冊交互項）成立；樹模型/新特徵/
  更低頻 = 明示的新一輪（規格另議），不是本輪的延伸。
- τ 內部驗證的損益代理是向量近似（僅用於訓練資料內挑 τ，不做跨策略比較）。

**保留資產**：ai/ml_train.py（特徵矩陣＋防洩漏訓練管線）、strategy/
ml_signal.py、run_ml_signal.py、9 tests——日後重啟時 purge/內部驗證/
判準框架直接重用。

### 正式結案（2026-07-15 使用者拍板：不開第二輪）

**判決理由（使用者原話要旨）**：(d) 歸因已給出清楚診斷——45 fold 中 τ 在
「全空手」與「全進場換手暴增」兩極擺盪，是底層機率貼著 0.5、模型判斷不出
方向性的典型症狀。這**不是「模型表達力不夠」的問題**（LR＋交互項已能表達
v2 的 AND 邏輯），**是這批特徵在 1h 頻率、這個 symbol 上的資訊含量已被 v2
的簡單規則消化得差不多**。與 IC 診斷否證 foreign_net 是同一類型的**乾淨
否證**：問題不在包裝方式，在特徵×頻率組合本身。

**★方法論成功案例存檔：事前判準設計（使用者指定記錄）★**：本輪判準在
動工前寫死並逐條事前註冊，實戰中確實攔住了容易被誤判的雜訊——最典型的是
判準 (c)：D 窗彙總 Δmean +560.6% 表面上像 ML 大勝，剔除單一最有利 fold
（f5，Δ+8356pp）後現形為 +3.8%，「單 fold 主導的彙總幻象」被事前寫好的
規則自動識破，不依賴事後判斷力。此案例證明「先定義贏、再跑數字」的紀律
在 ML 這種高自由度任務上尤其必要，後續各輪比照。

**重啟備忘（掛帳記錄，非現在的延伸、不動工）**：若日後重啟 ML 訊號線，
**「換更低頻 label」比「換更強模型」更有道理**——已知 1h 尺度訊號雜訊比
本來就高，降頻有機會提升訊噪比；換更強模型（樹模型）攻擊的是表達力，
而表達力已被本輪排除為根因。此為**明確的新方向、新規格**（label 定義、
purge 幅度、判準門檻都需重議），不是本輪的參數調整。

## 滑價敏感度（2026-07-15，★量測完成：觸發「另開基準討論」出口，非「增量有限」★）

**任務定位（比照 high 觸價敏感度輪的紀律，與 ATR/multiplier 校正輪的
「選候選」判讀邏輯不同）**：這不是找最優滑價值——市價單滑價方向沒有懸念
（恆不利）。問題是「零滑價這個簡化假設，有沒有系統性低估交易成本、進而
高估報酬」，只有「增量有限（接受簡化）」與「增量顯著或核心案例質變（另
開基準討論）」兩個出口。

**★更正舊掛帳用詞（COLLAB.md 紀律，文件與程式碼不同步的具體案例）★**：
「建議的下一步」節舊寫「滑價敏感度（slippage_bps 已預留）」——查證後
`slippage_bps` 動工前**只存在於 `event_engine.py:45` 的 docstring 註解**
（「滑價目前為 0，敏感度另開任務」），`run_event_backtest()` 簽名裡沒有
這個參數，並非真正「已預留」。本輪是**新的範圍性解鎖**，不是延伸既有介面。

**event_engine.py 範圍性解鎖**：新增 `slippage_bps: float = 0.0`（預設值
＝現行行為，byte-diff 回歸背書），只調整 `_execute()` 送進 `broker.
market_order()` 的實際成交價——BUY ×(1+bps/1e4)、SELL ×(1−bps/1e4)；強平單
（本質是 BUY 回補）同樣套用不豁免（強平集中在高波動 bar，正是滑價壓力
測試最相關的情境）。**sizing 基準價／mark-to-market equity／強平觸發判定
（close-only）全部維持用未滑價原始價格**，只有 broker 收到的成交價受影響
——避免整體價格序列被污染而混淆不相干機制。TDD：`tests/test_event_engine_
slippage.py`（9 tests：預設 0 逐位不變、買貴賣賤方向、強平單套用、mark/
觸發判定不受影響、負值拋錯）。294→**303 tests**。

**變因控制**：`sizing_mode="leverage_cap"`（現行正式基準）、ATR N=3.0/
window=14（不動）、固定網 15%（不動）、`trigger_source` 維持 close-only
（不動）、`fee_bps=5.0`（不動，滑價是疊加成本非替代手續費）、
`fill_mode="next_open"`（現行正式）、訊號固定 v2（Phase 4 已結案不用 ML）。
網格 {0,2,5,10,20} bps（0=基準；2=BTCUSDT 正常流動性量級；5=與現行
`fee_bps` 同量級的直覺對照點；10=高波動/偏大單壓力情境；20=極端流動性
驟降尾端）。只鎖定加密（BTCUSDT）；台股尚未走進 E2 事件驅動管線，不外推。
三窗 C/D/A × 15-fold ＋乾淨基準 5-fold（`run_slippage_calibration.py`
一鍵重現，與窗A重疊只作定錨＋fold4 追蹤展示，不入獨立判準）。

**量級（Δ = annual(bps) − annual(0)，逐 fold 配對 mean/SE）**：

| bps | 乾淨基準 | 窗C | 窗D | 窗D（剔除f5後手算） | 窗A |
|---|---|---|---|---|---|
| 2 | -7.7% (SE 1.0%) | -25.1% (SE 8.8%) | -41.9% (SE 33.0%) | ≈ -3%（手算） | -8.8% (SE 2.3%) |
| 5 | -18.3% (SE 2.3%) | -57.7% (SE 20.2%) | -98.3% (SE 76.6%) | **≈ -21.9%（手算）** | -20.1% (SE 5.1%) |
| 10 | -33.2% (SE 4.1%) | -101.4% (SE 35.5%) | -176.6% (SE 136.0%) | — | -35.6% (SE 8.6%) |
| 20 | -55.3% (SE 6.2%) | -159.2% (SE 53.8%) | -285.6% (SE 218.1%) | **≈ -67.9%（手算）** | -56.5% (SE 13.1%) |

★窗D 的彙總數字被單一 fold（f5）嚴重扭曲——與 Phase 4 判準(c) 攔到的
D f5 是同一個 fold（baseline annual +5140.8%，短窗年化放大的已知極端值）★。
手算剔除 f5 後，窗D 5bp 的 Δmean 從 -98.3% 收斂到 **≈ -21.9%**，與窗A
（-20.1%）幾乎重合，20bp 從 -285.6% 收斂到 **≈ -67.9%**，落回窗C/A 量級。
**跨窗一致性的真實結論：三窗方向一致為負，量級對齊後（剔除 D f5 扭曲）
彼此接近，不是「D 特別糟」而是「D 的彙總被一個統計離群 fold 綁架」——
與 Phase 4 輪同型教訓的再次印證，此為跨輪重複出現的模式，非偶然。**

**強度①：fold4 核心案例（規則一輪錨點，cap 路徑現值 +27.5%）逐 bps 追蹤**：

| bps | 0 | 2 | 5 | 10 | 20 |
|---|---|---|---|---|---|
| annual | +27.5% | +22.3% | +14.8% | +3.4% | **-16.2%** |

在「正常流動性」（2bp）與「壓力情境」（10bp）**全程維持獲利**，只在
**極端流動性驟降（20bp）翻負**——依事前判準，這是一次真實的質變事件，
但只在網格最極端的一點發生，不是在現實或中壓力假設下發生。

**強度②：獲利→虧損翻轉掃描**（原獲利 fold 中翻轉的比例）：2bp 全部四個
範圍皆 **0 翻轉**；5bp 開始出現少量翻轉（乾淨 1/3、C 0/8、D 1/8、A 2/7）；
10bp 明顯增加（乾淨 2/3、C 0/8、D 3/8、A 3/7）；20bp 多數原獲利 fold 翻轉
（乾淨 3/3、C 5/8、D 4/8、A 7/7）。翻轉集中在 10~20bp 這段，2bp 完全無感。

**★強度③（固定呈現，依使用者要求不因結論方向省略）：換手拆解——侵蝕
是否集中在少數 fold★**：

| 窗 | 成交筆數 vs 侵蝕相關係數 | 單一 fold 佔總侵蝕最大比例 | 判讀 |
|---|---|---|---|
| 乾淨基準 | +0.19 | 26% | 分散 |
| C | +0.34 | 33% | 分散 |
| D | +0.20 | **78%（f5 一根）** | **集中——即上述扭曲的同一 fold** |
| A | +0.63 | 19% | 分散 |

三窗中兩窗（C/乾淨）＋A 屬分散型侵蝕（無單一 fold 主導），侵蝕與成交筆數
本身相關性偏弱（+0.19~+0.34），但與**該 fold 原始報酬量級**更相關——高
槓桿複利下的滑價侵蝕是乘性效果，年化報酬本身越極端的 fold 侵蝕絕對值越大
（例：窗C fold8 baseline +1004.5%、侵蝕 +796.45pp，是該窗最大侵蝕但佔比僅
33%，未達「集中」門檻）。**唯一例外是窗D，78% 侵蝕集中在單一離群 fold**，
且該 fold 就是扭曲窗D 彙總量級的同一個 f5——兩個獨立分析角度（量級的
D-vs-C/A 比較、強度③的換手拆解）**互相印證同一個發現**，不是巧合。

**判讀依兩個事前註冊出口**：
- **不是乾淨的「增量有限」**：5bp（與現行手續費同量級的直覺參照點）在
  三個獨立窗（C/D 剔除離群後/A）的 Δmean 落在 -20%~-22% 量級，相對 SE
  多數超過事前門檻（|Δmean|/SE ≥ 1，多數窗達 2.8~4.0），不是雜訊；20bp
  極端情境下 fold4 錨點翻負、多數原獲利 fold 翻轉。
- **也不是乾淨的「核心案例立即質變」**：fold4 錨點在現實（2bp）與中壓力
  （10bp）情境下**全程維持獲利**，翻負只發生在網格最極端的 20bp 點；
  2bp 全部範圍零翻轉，效果要到 5bp 以上才開始顯現。
- **★建議判決：觸發「開額外討論」出口，但範圍窄化★**——不是「零滑價
  整體不能用」，是「在 5bp 以上（尤其貼近或超過現行手續費量級的情境）
  零滑價假設會系統性高估報酬，且量級不在雜訊範圍內」。討論的具體問題
  建議聚焦：是否要把一個**溫和**滑價假設（如 2bp，現實流動性量級、目前
  網格中唯一「全零翻轉」的一點）納入 E2 基準，而非整體推翻現行設計——
  這不在本輪自動決定，留給使用者判斷。

**已知限制**：網格只鎖定 BTCUSDT／event_engine E2 路徑；台股尚未走進
E2 事件驅動管線，本輪結論不外推到台股。手算的 D 剔除 f5 數字是本輪報告
內的事後穩健性檢查，未寫入 `run_slippage_calibration.py`（該檔固定呈現
逐 fold 明細供使用者自行複驗，不預先做剔除）。

### ★基準切換執行（2026-07-15 使用者拍板：納入 2bp 滑價為新正式基準）★

**判決理由（使用者原話要旨）**：2bp 是網格中唯一對應 BTCUSDT 真實流動性
量級、有實證支持的候選，0bp 是已知偏離現實的簡化假設而非「更保守」的
選擇；2bp 全部四個範圍零翻轉、fold4 錨點維持穩健，代表這個改動不推翻
任何已拍板的方向性結論，只是讓報酬數字更誠實反映真實成本，與接入台股
真實交易成本（`costs.py`）同一個精神；不納入等於明知 0bp 系統性高估卻
不行動，與既有紀律不一致。

**執行方式（先評估範圍再動工，範圍確認比預期大後已回報並取得確認）**：
1. `backtest/event_engine.py` 的 `slippage_bps` 預設值由 `0.0` 改為
   `2.0`——這不是「保留舊行為當預設、新增選用參數」模式，是正式切換
   基準的模式選擇。
2. **範圍評估（實測，非推論）**：改動後跑全套測試，**18 個測試失敗**，
   分佈在 4 個檔案（`test_event_engine.py` 5 個、`test_event_engine_risk.py`
   4 個、`test_event_engine_sizing.py` 4 個、`test_multiplier_sizing.py`
   2 個、`test_event_engine_slippage.py` 自身 3 個）。根因統一：這些測試
   寫成時 `slippage_bps` 不存在，「不傳」曾隱含零摩擦；預設值變動後這個
   隱含假設失效。**修法機械式**：逐一補 `slippage_bps=0.0`，比照這些測試
   早就在用的 `fee_bps=0.0` 慣例（零摩擦比較需求明確表達，不依賴省略），
   不改任何斷言邏輯。18 個測試修復後全套 **303 passed**。
3. **另一個更關鍵的發現**：五支已結案 runner（`run_atr_calibration.py`、
   `run_rule1_sizing.py`、`run_multiplier_calibration.py`、
   `run_phase2_event.py`、`run_ml_signal.py`）也全部沒有傳
   `slippage_bps`——若照舊重跑會**靜默**產生 2bp 基準數字，與 HANDOFF
   記錄的歷史數字對不上，違反「固定資料集原則」的逐位重現保證。**已逐一
   明確 pin `slippage_bps=0.0`**（連同簡短程式碼註解說明原因），保住這五
   輪歷史數字的可重現性；各自的方向性結論（維持 N=3/14、E2 基準不切 r1、
   維持 m=0.5、Phase 2 Δ歸因、Phase 4 v2 續任）建立在機制層不變量（觸發
   bar 集合、比率不變性）上，與滑價無關，不受影響。
4. **基準標籤已標注**：Phase 2、ATR 校正、規則一 sizing、multiplier 校正、
   Phase 4 五個章節各自的段首已加註「數字基準 = 0bp（歷史快照）」，明確
   區隔於本節以下的新 2bp 基準數字，避免日後引用混淆。

**★新 2bp 正式基準：乾淨基準 + C/D/A 完整 E2 對照表★**（`slippage_bps`
省略即自動套用新預設值 2.0；其餘設定與歷史 0bp 表完全相同：
`sizing_mode="leverage_cap"`、ATR N=3.0/w=14、`fee_bps=5.0`、
`fill_mode="next_open"`、v2 訊號）：

| 範圍 | 0bp（歷史，已 pin） | 2bp（新正式基準） | Δ(2bp−0bp) |
|---|---|---|---|
| 乾淨基準(5f) | +7.8%/25.0% | **+0.1%/24.2%** | -7.7pp |
| C(15f) | +135.1%/289.3% | **+110.0%/255.3%** | -25.1pp |
| D(15f) | +359.5%/1325.5% | **+317.6%/1197.8%** | -41.9pp |
| A(15f) | -10.4%/75.0% | **-19.1%/66.7%** | -8.7pp |

（mean/std 年化；每欄與滑價敏感度節「量級」表的 2bp 欄逐位一致，交叉驗證
通過。窗D 的彙總同樣受單一離群 fold f5 影響大，見上方「滑價敏感度」節
的手算修正討論——引用窗D 數字時建議一併參考該節。）

**這張表即日起是所有新任務（多 symbol 擴充等）引用 E2 v2 表現的正式參照**；
省略 `slippage_bps` 參數即自動套用。舊 0bp 表僅作歷史快照保留，不作為
新任務的比較基準。

## 多 symbol 擴充（方向A：ETHUSDT，2026-07-15，★量測完成★）

**任務定位**：驗證性任務（同精神 Phase 4 ML）——檢查 v2 策略／ATR
N=3.0/w=14／multiplier m=0.5 這些已在 BTC 上拍板的結論是否可推廣到同類
資產，還是 BTC 特例；不為 ETH 重新校正參數，只跑一次。

**控制實驗設計**：沿用 BTC 既有 C/D/A 三窗＋乾淨基準的 calendar timestamp
邊界（不用 ETH 獨立切分）——固定市況背景只換資產。查證確認 ETHUSDT 與
BTCUSDT 在 Binance 歷史深度完全對稱（皆自 2017-08-17 04:00 UTC 上線）；
`klines.sqlite` 的 PRIMARY KEY 含 symbol，天然多 symbol 共存，未建新 DB。
`run_ingest.py` 加 `--symbol` 參數（預設 BTCUSDT 向後相容）；ETHUSDT 抓
26000 根覆蓋 2023-07-28→2026-07-15，**零缺口零重複驗證通過**（涵蓋窗C
最早邊界 2023-08-22 有餘裕）。正式基準全部沿用現行拍板值不覆寫：
slippage_bps 省略即 2.0、`sizing_mode="leverage_cap"`、RiskConfig 全預設。
`run_multi_symbol_eth.py` 一鍵重現（三個檢查一次跑完）。

### (A) v2 策略 E2 表現方向性對照

| 範圍 | BTC（2bp 基準） | ETH（原始 mean/std） | ETH（剔除單一極端 fold 後） |
|---|---|---|---|
| 乾淨基準(5f) | +0.1%/24.2% | -18.1%/92.0% | （無需剔除，fold4+134.5%非異常值） |
| C(15f) | +110.0%/255.3% | +548.5%/1557.5% | **+3.4%**（剔 fold8+9：+2528%/+5655%） |
| D(15f) | +317.6%/1197.8% | +60.4%/229.9% | **+12.8%**（剔 fold5：+727.6%） |
| A(15f) | -19.1%/66.7% | +150.2%/618.6% | **-7.0%**（剔 fold1：+2351.4%） |

**方向性模式：與 BTC 同型，不是 BTC 特例**——ETH 原始數字表面上與 BTC
不完全一致（甚至窗A 原始符號相反：BTC -19.1% vs ETH +150.2%），但這正是
本專案已反覆印證的「單一極端 fold 主導彙總」現象（Δsizing 規則二／
Phase 4 判準(c)／滑價輪 D f5 同型教訓）——**剔除該窗最大的單一 fold 後，
ETH 四個範圍全部收斂到深陷雜訊的小量級（-18.1%~+12.8%），窗A 符號從
+150.2%翻回 -7.0%，與 BTC 方向重新一致**。這個發現本身是正面結果：
「1h 尺度此策略族訊號雜訊比高、彙總易被單 fold 綁架」是**市場結構層級
的現象，在兩個高度相關的資產上都出現同型模式，不是 BTC 特例**——ETH
沒有推翻任何 BTC 既有結論，反而印證了同一套方法論教訓的普適性。

### (B) ATR N=3.0/w=14 機制層合理性（只測現值，不掃網格）

| 範圍 | 空頭區段 | 觸發 | 密度 | 防線分工(atr/fixed/both) | 誤殺/保護 |
|---|---|---|---|---|---|
| 乾淨基準 | 25 | 7 | 28.0% | 7/0/0 | 0/7 |
| C | 54 | 15 | 27.8% | 15/0/0 | 0/15 |
| D | 54 | 16 | 29.6% | 16/0/0 | 1/15 |
| A | 70 | 19 | 27.1% | 19/0/0 | 1/18 |
| **合計** | **203** | **57** | **28.1%** | — | — |

BTC 參考基準：165 區段、53 次觸發（密度 ≈32%）。**ETH 觸發密度 28.1%
與 BTC 32% 同量級，落在合理區間**——不是零觸發（防線沒有形同虛設）、
也不是密度暴增（沒有比 BTC 高一個數量級）。**固定網 15% 在 ETH 全部
四個範圍仍是 0 次先觸發（fixed=0 across all）——「固定網純屬異常備援」
角色在 ETH 上同樣成立**，未退化。

### (C) multiplier 機制層不變量

- **P2（未封頂損失/預算比率跨 m 不變）：✓ 成立**，最大偏差 4.44e-16
  （純浮點噪音）——sizing 公式**核心數學**在 ETH 上逐位驗證通過，這是
  最重要的機制層證據：規則一/二 sizing 邏輯本身與資產無關。
- **m=0.5 封頂率**：乾淨基準/D/A 三個範圍 0.0%，窗C 3.8%——與 BTC 參考
  基準（四範圍合計約 2.9~7.4%）同量級，不是「恆封頂」也不是「恆不封
  頂」的退化行為。
- **★P1（觸發 bar 集合跨 m 不變）：窗D fold9 出現 1 筆不一致，全域
  60 個 fold×窗組合中唯一一例★**——已深入查證根因（非猜測）：

  **根因鏈**（`run_event_backtest` 逐 bar 追蹤確認）：m=1.0 在窗D
  fold9 的 2025-02-09 21:00~23:00 三筆空頭進場單**未被日內熔斷拒單**
  （`approve_order` 通過），但 m=0.25/0.5/0.75 的同一批進場單**被
  `rejected_circuit_breaker` 拒絕**（`ev.rejections` 逐位確認）。原因：
  日內熔斷門檻（`update_equity` 的 `daily_pnl_pct ≤ -3%`）比較的是
  **當日累積權益**，而空頭部位的權益路徑隨 m 縮放——m 越大，同一段
  歷史累積下來的權益軌跡越不同，導致「當日 -3% 門檺是否已跨越」這個
  布林判定在特定 m 值上剛好落在門檻兩側。m=1.0 因為權益路徑不同，
  在那三個小時**還沒**觸發熔斷、進場單被核准，之後才在 bar413 觸發
  ATR 強平（多出一次），其餘三個 m 因為熔斷擋單、當時沒有部位、自然
  不會有 bar413 那次觸發。

  **這不是 sizing 公式的 bug**（P2 逐位成立已排除公式本身的非線性），
  是**日內熔斷這個獨立機制**與 sizing 路徑依賴（quantity 影響權益、
  權益影響隔天熔斷門檻）的真實交互作用——`multiplier校正輪`當時的 P1
  假設（「check_forced_liquidation 只看 quantity 正負號」）在**單看
  該函式本身**是對的，但沒有涵蓋「熔斷擋單導致部位根本不存在」這個
  上游分支，該假設的完整表述應該是「若無熔斷介入，觸發 bar 集合跨 m
  不變」——BTC 資料域内 60 個窗口從未踩到這個邊界情境（可能是 BTC 相對
  波動較低，當日 P&L 擺動較少接近 -3% 門檻附近），**ETH 較高的相對波動
  度使這個一直存在但罕見的邊界情境被踩中一次**。發生率 1/60，且
  觸發的「多一次強平」對應報酬影響已含在上表 (A) 的 ETH 窗D 逐 fold
  數字中（fold9 未被列為極端值剔除對象）。

**判讀（三項合併）**：ATR 密度與固定網角色（B）、sizing 核心數學
（C 的 P2）在 ETH 上完全合理、無退化跡象；v2 策略方向性模式（A）在
控制單 fold 主導效應後與 BTC 同型；**唯一的真實偏差是 P1 的邊界情境
（熔斷-sizing 交互作用），已根因分析清楚、非 bug、發生率 1/60，不影響
「既有參數在 ETH 上機制合理」的整體結論，但值得記錄為方法論補充**
（見 AGENTS.md「日內熔斷與 sizing 路徑依賴的交互作用」，待補）。

**結論：BTC 拍板的 v2/ATR/multiplier 結論可推廣至 ETH，非 BTC 特例**。
不觸發重新校正；ETH 的資料與管線（`klines.sqlite` ETHUSDT、
`run_multi_symbol_eth.py`）保留，供未來第二個同類資產（如需要）比照。

## 建議的下一步（尚未做，等使用者指示）

資料擴充（25300 根）與四時間窗驗證皆已完成。

**A2 已定案不採用（2026-07-06 使用者決定）**：v2 + 風控近似續任基準；A2 程式碼
與測試保留（不刪除）、`long_regime_window` 預設 None 不變；此假設不再投入更多
時間窗驗證。備忘（不需現在處理）：若日後重啟條件化A2，唯一可行形式是
「長視窗(300)判 trend_up 時擋空單」——原表述「短視窗判 trend_up 才套用過濾」
在現行訊號下是 no-op（v2 空單本來就要求短視窗判 trend_down）。

**C. 事件驅動回測第一階段完成（2026-07-06）**：E0/E1/E2、兩層驗收、完整跑批
與 Δ歸因總表皆完成（見「Phase 2」節）。候選下一步（等使用者指示）：

- ~~A2 定案複查~~ **已結案棄做（2026-07-07 使用者決定）**：兩種獨立計算方法
  （向量化四獨立窗、事件驅動 E2 5-fold）交叉驗證結論一致指向 v2 略優，
  正式棄用複查，不再視為待辦；A2 程式碼與測試保留供未來參考。
- Phase 2 續工候選：~~滑價敏感度~~（**已完成並結案 2026-07-15**：`slippage_bps`
  之前只是 docstring 註解、並非已預留的實際參數——此處更正舊用詞不準確；
  結果觸發「另開基準討論」出口，非「增量有限」，見「滑價敏感度」節）、
  ~~盤中 high 觸價強平~~
  （**已完成並結案 2026-07-14**：接受 close-only 簡化，見「high 觸價敏感度」節）、
  ~~規則一整合~~（**已完成 2026-07-13**，見「規則一 sizing 接入 E2」節；
  E2 基準已定案不切換 r1）、~~short_risk_multiplier=0.5 校正~~（**已結案
  2026-07-14：維持 0.5**，見「short_risk_multiplier 校正」節）。
  ~~paper.py utcnow deprecation~~ **已修復（2026-07-15）**：`Fill.ts` 未傳
  ts 時的 fallback 由 naive `datetime.utcnow()` 改為 tz-aware
  `datetime.now(timezone.utc)`（單行改動；下游零影響已查證——唯一牽動
  sizing/風控/報表的路徑 `event_engine.py` 永遠顯式傳入 canonical tz-aware
  ts，此 fallback 只在 `run_phase1.py` 與兩支未斷言 ts 值的舊測試觸發）。
  順帶修正一個潛在型別不一致：canonical schema 的 ts 全 tz-aware，這個
  fallback 曾是唯一的 naive 例外。新增 `test_market_order_default_ts_is_
  timezone_aware_utc` 鎖住修正後行為（TDD）。293→**294 tests**；16 個
  deprecation warning 全部消失（0 warnings）。
  ~~是否將滑價正式納入 E2 基準假設~~ **已結案（2026-07-15 使用者拍板：
  納入 2bp 為新正式基準）**，見「滑價敏感度」節「基準切換執行」小節。
  ~~固定網 15% 校正~~ **已定調關閉（2026-07-16）**：改標「由未來台股
  放空策略輪吸收」，見下方對應項。~~台股 E2 事件驅動驗證~~ 已完成
  （2026-07-16），見下方對應項。
- ~~Phase 4 ML 訊號層第一輪~~ **已正式結案（2026-07-15 使用者拍板，不開
  第二輪）**：判準全 FAIL、v2 續任，見「Phase 4 ML 訊號層第一輪」節。
  重啟備忘：更低頻 label 優先於更強模型（明確新方向新規格，不動工）。
- ~~D. ATR 的 N=3 / window=14 校正~~ **已完成（2026-07-13）：機制層＋結果層
  兩層完成，建議維持現值，等使用者拍板——見「ATR 強平防線校正」節。**
- ~~多 symbol 擴充（方向A：同幣種類別驗證）~~ **已完成（2026-07-15）**：
  ETHUSDT 驗證結果 = **v2/ATR/multiplier 結論可推廣，非 BTC 特例**（機制
  層無退化；v2 方向性剔除單 fold 主導效應後與 BTC 同型；唯一真實偏差是
  P1 熔斷-sizing 交互作用邊界情境，發生率 1/60、已根因分析、非 bug）。
  見「多 symbol 擴充（方向A：ETHUSDT）」節。方向B（不同市場結構，如美股）
  仍是未來候選，需與本輪「同幣種類別」的樣本量變因分開設計。
- ~~★台股 E2 事件驅動驗證：既定計畫★~~ **已完成（2026-07-16）**：多頭
  baseline（`ma_rsi_regime.py`）× 4 檔 × 15-fold，M1/M2/R1/R2 全 PASS、
  M3 診斷完整揭露，見「台股 E2 化第一輪基準」節。台股 regime/ATR 參數
  校正同日完成（維持現值），見「台股 regime/ATR 參數校正」節。
- ~~★台股放空設計（未來任務）★~~ **已完成（2026-07-16，同日多輪推進至
  執行層機制全數就位）**：五項結構落差已逐項查證（HANDOFF「台股放空
  第一輪」節 groundwork）→ 3.5% 禁空規則＋漲跌停鎖死耦合＋**ATR 分層 N
  已實作**（`stratified_forced_liq_n`，trailing q95 錨定 12%，正是本條目
  預告的「依分層特性重新設計」的落地——不沿用 N=3、依股票自身波動特性
  自我校準）→ item 3 強制回補日曆完成（C1~C5 全過）。台股放空執行層
  四機制（uptick／鎖死／分層N／回補日曆）全部就位，尚未做的是**整合
  跑批的正式放空基準**與策略層設計（見下一項）。
- **固定安全網 15%：掛帳關閉，改標「由未來台股放空策略輪吸收」**
  （2026-07-16 使用者拍板定調）：加密貨幣域內它從未先於 ATR 線觸發
  （獨立校正無意義）；台股域內 regime/ATR 校正輪 P1 已證實它在小型股＋
  危機期會**先於**動態線觸發——其正確值已不是獨立參數問題，而是台股
  放空策略設計的一部分（與分層 N 聯動：q95 錨定 12% 就是以 15% 網為
  前提設計的）。不再作為獨立「待校正」項存在。
- **主動避開優化輪（維持現狀，2026-07-16 盤點確認）**：量測已備
  （6 個撞死線案例、來回費用合計 0.0149），是否開輪屬策略層假設檢驗，
  等使用者決定。
- **台股滑價校正（維持現狀，2026-07-16 盤點確認）**：台股 runner 全部
  pin slippage_bps=0.0（BTC 校正的 2bp 不可沿用），台股數字用於真實
  決策前需自己的滑價輪。

> 提醒接手者：這份是現況快照。動工前仍先讀 `AGENTS.md` 與 `CLAUDE_2.md`，完成後若改了架構記得回頭更新它們與本檔。
