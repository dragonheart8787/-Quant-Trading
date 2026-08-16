# CLOUD_SETUP.md — 雲端 session 環境重建

> 適用情境：本機電腦不可用期間，改用 Claude Code on the web 的雲端 session 工作。
> 容器是 **ephemeral** 的——每個新 session 都是重新 clone 的乾淨 repo，`.venv`
> 不會保留。這份文件把 2026-08-15 那輪手動查證的結果固定成可重複流程，
> 讓之後任何新 session 不需要重走一次完整查證。
>
> 驗證過程與結論（為什麼可以信這套流程）記在 `HANDOFF.md`「雲端環境基準」節。

## TL;DR

```bash
./setup_cloud_env.sh          # 建 venv + 裝 pin 死的相依 + 跑全套測試
./setup_cloud_env.sh --full   # 上述 + 凍結資料庫校驗和 + 離線（封 socket）測試
```

預期輸出：**379 passed, 0 warnings**。
數字不符 → **先回報，不要自行修測試、也不要假設是環境問題就略過**
（`COLLAB.md`「測試是唯一的真相來源」）。

## 手動步驟（等同上面的腳本，需要逐步排查時用）

```bash
python3 --version                       # 應為 3.11.x（雲端 sandbox 預設基準）
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q    # 應 379 passed, 0 warnings
```

之後所有指令一律用 `.venv/bin/python`（本機 Windows 是 `.venv\Scripts\python.exe`）。
中文輸出加 `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`。

## 這個環境的三個既定事實（已驗證，不用每次重查）

### 1. Python 版本差異不影響數字

雲端 sandbox 預設 **Python 3.11.15**，本機是 **3.14.6**。這個差異已用凍結資料集
實測，不是推論：`run_tw_short_baseline.py` 在（3.11.15 + numpy 2.4.6）與
（3.14 + numpy 2.5.2）兩組環境下輸出**逐位元完全相同**。

**已定案：雲端一律用 sandbox 預設 3.11.15**，不裝 3.14。理由是可信度問題已由
逐位對照解決，剩下的只是操作便利性——RC 版本是不必要的風險，全域安裝在
ephemeral 容器裡每次都要重來。

### 2. 不需要任何 API token

全套測試只讀已落地的凍結資料庫，**沒有任何測試會即時打 API**。這點是用
「封鎖 socket 後重跑全套」證明的，不是靠讀程式碼推論（`--full` 會重跑這個證明）。
所以雲端 session **不需要設定 `FINMIND_TOKEN`**，也不需要 Binance 金鑰。

台股分析 runner 同樣一律讀本地快照（`klines_tw.sqlite` / `events_tw.sqlite`），
見 `HANDOFF.md`「台股固定資料集原則」。

### 3. 資料抓取類 runner 在雲端「不能跑」（環境網路政策）

容器對外連線走 agent proxy，政策層**直接擋掉本專案的兩個資料源**：
`api.binance.com` 與 `api.finmindtrade.com` 皆回 `CONNECT tunnel failed, 403`
（policy denial）。`pypi.org` / `files.pythonhosted.org` / `github.com` 正常，
所以 pip 安裝與 git push 不受影響。

| runner 類型 | 雲端可用？ |
|---|---|
| 分析／回測（讀凍結 DB）：`run_tw_short_baseline.py`、`run_e2_tw_baseline.py`、`run_phase2_event.py` 等 | ✅ 可跑 |
| 資料抓取：`run_ingest.py`、`run_ingest_chips.py`、`run_ingest_tw_price.py`、`run_ingest_tw_events.py` | ❌ 連線階段即失敗 |

→ **任何需要擴充或刷新資料集的任務，本機修好之前不要排進雲端 session**，
否則會卡在連不出去。附帶效果：凍結資料集原則在雲端被環境強制執行，
不可能有 runner 偷偷即時抓資料造成數字漂移。

### 4. 四個資料庫隨 repo 一起 clone

`.gitignore` **刻意不排除** `data/*.sqlite`（固定資料集原則）。clone 完就該有：

| 檔案 | 大小 | 內容 |
|---|---|---|
| `data/chips_tw.sqlite` | ~43.7MB | 台股籌碼（institutional / foreign_shareholding / margin） |
| `data/klines.sqlite` | ~7.6MB | BTCUSDT 25300 根 + ETHUSDT 26000 根 1h |
| `data/klines_tw.sqlite` | ~1.8MB | 台股日K 四檔（2330/2603/6446/8069） |
| `data/events_tw.sqlite` | ~0.04MB | 除權息結果 + 停券日曆 |

`--full` 會逐項核對 `HANDOFF.md` 記錄的凍結校驗和。

## 依賴版本為什麼 pin 死

`requirements.txt` 的所有版本（含遞移相依）都用 `==` 鎖定，理由與完整但書寫在
該檔開頭的註解裡。一句話版本：把「凍結資料集、逐位可重現」延伸到依賴層——
專案已經因為 pandas 3.0 的 `to_datetime` 解析度行為踩過一次 ns/us 的靜默 bug，
不該讓下一次乾淨安裝再抓到未驗證的新版套件。

**本機 3.14.6 的但書**：numpy 無法兩邊共用同一個 pin（2.4.6 沒有 cp314 wheel、
2.5.x 不支援 3.11）。本檔的 pin 以雲端基準為準；本機裝不起來時，正確做法是
本機另存對應的 pin 組合並重跑逐位對照，**不是**把 `requirements.txt` 改回
範圍寫法。

## 已知落差（歷史，已修）

- `requirements.txt` 原本**漏列 `scikit-learn`**（本機 venv 早已裝好所以沒暴露）。
  乾淨環境照舊檔安裝會有 7 個測試模組直接 collection error
  （`backtest/walk_forward.py` import `TimeSeriesSplit`）。2026-08-15 已補上並 pin。
