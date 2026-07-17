# 任務：放空功能 — 風控規則實作

> 本檔是使用者與另一個 Claude 討論後定案的規則，使用者已明確同意修改
> `risk/manager.py`（原本在「不要動清單」上，本次任務範圍內解除此限制）。
> `strategy/ma_rsi.py`、`vector_engine.py`、`event_engine.py` 仍維持不要動。

## 背景與既有狀況確認（動工前先驗證，不要假設）

1. 檢查 `broker/paper.py`：`Position.quantity` 已支援負值（空頭），`_apply_to_position`
   與 `equity()` 邏輯看起來已經是方向無關的通用計算。**先寫測試驗證**：
   - 開空單（從 0 → 負）均價計算正確
   - 空單加碼均價計算正確
   - 空單部分回補（減倉）均價維持不變
   - 空翻多 / 多翻空（反向超過原倉位）均價改為新成交價
   - `equity()` 在持有空單時，價格上漲應反映虧損，價格下跌應反映獲利
   若驗證後發現邏輯沒問題，**`paper.py` 不需改動**，只需新增 `test_paper_broker_short.py`。
   若發現邊界案例有 bug，回報使用者確認後再修。

2. `risk/manager.py` 現狀：`RiskConfig`/`position_size`/`approve_order` 都是
   方向無關的單一數值，沒有多空分別。以下是要擴充的規則。

## 規則一：空頭單筆風險比例

- 新增 `risk_per_trade_short`，預設為 `risk_per_trade * 0.5`（即多頭 1% → 空頭 0.5%）。
- 不要寫成寫死的 0.005，而是用 multiplier 概念實作，方便之後依實測資料調整：
  例如 `RiskConfig` 新增欄位 `short_risk_multiplier: float = 0.5`，
  `risk_per_trade_short` 由 `risk_per_trade * short_risk_multiplier` 算出（可做成 property 或顯式欄位，由你判斷哪個更符合現有 dataclass 風格）。
- `position_size()` 需要新增 `side` 參數（或等價方式區分多空），依方向選用對應風險比例。

## 規則二：多空槓桿上限分開設定

- `max_leverage` 拆成 `max_long_leverage` 與 `max_short_leverage`，
  預設 `max_short_leverage = max_long_leverage * 0.5`（沿用同樣的保守邏輯）。
- 保留一個 `max_gross_leverage` 欄位（多空互斥情境下等同各自上限，但為未來若開放多空並存預留欄位），
  **但目前邏輯只鎖定單一方向持倉**：若已有多頭倉位不可開空頭（反之亦然），
  這個互斥檢查請加在 `approve_order` 或新增的下單前檢查裡，並寫對應測試。
- `approve_order()` 與 `position_size()` 都要依 `side` 套用對應槓桿上限。

## 規則三：空頭硬性強制平倉（獨立於日內熔斷之外）

這是新規則，獨立於既有的 `update_equity` / `is_circuit_broken` 機制，**只對空頭倉位**生效：

- **雙層防線，取較嚴格者觸發**：
  1. **動態防線（ATR-based）**：強平價格 = `entry_price + N × ATR(window)`。
     - 需要先在 `indicators/technical.py` 新增向量化 ATR 計算函式（rolling，因果，禁止用未來資料）。
     - `N`、`window` 先用預設值（建議 N=3, window=14，這是業界常見起點，
       不是實測校正值，請在文件註明「待用歷史資料校正，非最終值」）。
  2. **固定安全網**：未實現虧損超過倉位成本（notional at entry）的 **15%**，
     不論 ATR 算出什麼都強制觸發。這條是防止 ATR 異常（資料缺漏/極端值）時的最後防線。
- 實作建議：新增一個方法，例如 `RiskManager.check_forced_liquidation(position, current_price, atr_value) -> bool`，
  回傳是否該強制平倉，內部分別算兩條防線取最嚴格者。
- 這個檢查跟 `is_circuit_broken`（帳戶層級日內熔斯）是兩條獨立的線，互不取代：
  - 日內熔斷 = 帳戶整體當日虧損達到門檻 → 當日停止交易
  - 強制平倉 = 單一空頭倉位虧損過大 → 只平掉那一倉，不影響當日是否能交易其他倉位
  兩者要分開實作、分開測試，不要混在同一個函式裡判斷。

## 規則四：不模擬保證金/強平機制

- 不需要實作真實的 margin call / 維持率模擬。現貨資料源沒有合約保證金概念，
  上述「規則三」的硬性平倉已經是這個專案自訂的風控防線，不是模擬交易所機制。

## 新增策略訊號（不動 ma_rsi.py）

- 既有 `strategy/ma_rsi.py`（long-only, {0,1}）與 `strategy/ma_rsi_regime.py` 都不要改。
- 新增 `strategy/ma_rsi_bidirectional.py`（或你覺得更貼切的檔名），輸出 `{-1, 0, 1}`：
  - 多頭進場條件沿用既有 MA/RSI 邏輯。
  - 空頭進場條件需要明確定義（例如 MA 死亡交叉 + RSI 超買回落，需與使用者確認具體邏輯，
    不要自己假設一套規則就直接實作，先列出建議邏輯讓使用者過目）。

## 測試要求

- 所有新規則都要有對應單元測試，沿用既有 `tests/` 風格（pytest）。
- 既有 54 個測試必須全數維持通過。
- 至少新增：
  - `test_risk_manager_short.py`（規則一、二、三）
  - `test_paper_broker_short.py`（驗證背景知識第1點列的邊界案例）
  - ATR 計算的單元測試（含 lookahead 防護測試，比照 `test_lookahead_bias.py` 風格）

## 文件更新義務

完成後務必回頭更新：
- `AGENTS.md` / `CLAUDE_2.md`：補上這次新增的風控規則說明
- `HANDOFF.md`：更新現況快照，移除「目前不支援放空」的過時描述，記錄新檔案結構

## 待確認事項（不要自己決定，先問使用者）

- 空頭進場訊號的具體邏輯（死亡交叉等細節）
- ATR 的 N 倍數與 window 預設值是否要調整
- 是否需要為 `ma_rsi_bidirectional.py` 補一輪 walk-forward 驗證（比照既有三輪分析流程）
