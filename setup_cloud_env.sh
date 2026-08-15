#!/usr/bin/env bash
# 雲端 session 環境重建腳本（2026-08-15 建立）
#
# 用途：Claude Code on the web 的容器是 ephemeral 的，每個新 session 都要
# 重建 .venv。這支腳本把 2026-08-15 那輪手動查證的結果固定成可重複流程，
# 免得每次重新摸索。完整背景見 HANDOFF.md「雲端環境基準」節與 docs/CLOUD_SETUP.md。
#
# 用法：
#   ./setup_cloud_env.sh          # 建 venv + 裝 pin 死的相依 + 跑全套測試
#   ./setup_cloud_env.sh --full   # 上述 + 凍結資料庫校驗和 + 離線（封 socket）測試
#
# 預期結果：379 passed, 0 warnings。數字不符**先回報、不要自行修測試**
# （COLLAB.md「測試是唯一的真相來源」）。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VENV="${VENV:-.venv}"
PY="${PYTHON_BIN:-python3}"
FULL=0
[[ "${1:-}" == "--full" ]] && FULL=1

echo "=== [1/4] 檢查 Python ==="
"$PY" --version
# 基準是 3.11.x（雲端 sandbox 預設）。其他版本不擋，但要出聲——
# 版本差異本身已驗證不影響數字（見 HANDOFF），但 pin 死的 numpy 可能裝不起來。
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)'; then
  echo "  ⚠ 注意：基準環境是 Python 3.11.x，目前不是。"
  echo "    requirements.txt 的 numpy==2.4.6 只有 3.11 之前的 wheel，可能安裝失敗。"
  echo "    若要在其他版本上跑，見 requirements.txt 的「本機 3.14.6 環境的但書」。"
fi

echo "=== [2/4] 建立 venv：$VENV ==="
if [[ -d "$VENV" ]]; then
  echo "  已存在，沿用（要重建請先 rm -rf $VENV）"
else
  "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
[[ -x "$VPY" ]] || VPY="$VENV/Scripts/python.exe"   # Windows 本機相容

echo "=== [3/4] 安裝 pin 死的相依 ==="
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r requirements.txt
"$VPY" -m pip list 2>/dev/null | grep -iE "^(numpy|pandas|scikit-learn|scipy|pytest) " || true

echo "=== [4/4] 全套測試 ==="
"$VPY" -m pytest tests/ -q

if [[ "$FULL" -eq 1 ]]; then
  echo
  echo "=== [額外] 凍結資料庫校驗和 ==="
  "$VPY" - <<'PYEOF'
import sqlite3, sys

# 期望值來源：HANDOFF.md「台股固定資料集原則」凍結校驗和（2026-07-16 快照）
EXPECT_KLINES_TW = {
    "2330": (3471, 1565048.60), "2603": (3464, 241254.02),
    "6446": (3007, 811043.34),  "8069": (5217, 371298.27),
}
EXPECT_MAX_TS = 1783558800000000000
EXPECT_DIV = {"2330": (45, 46236.27), "2603": (18, 2429.56),
              "6446": (5, 6361.59),   "8069": (14, 2558.37)}
EXPECT_SUSPEND = {"2330": 45, "2603": 24, "6446": 6, "8069": 23}
EXPECT_KLINES = {"BTCUSDT": 25300, "ETHUSDT": 26000}

ok = True
def check(label, got, want):
    global ok
    good = got == want
    ok &= good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got={got} want={want}")

k = sqlite3.connect("data/klines_tw.sqlite")
for sym, (n, s) in EXPECT_KLINES_TW.items():
    row = k.execute(
        "SELECT COUNT(*), ROUND(SUM(close),2), MAX(ts) FROM klines WHERE symbol=?", (sym,)
    ).fetchone()
    check(f"klines_tw {sym}", (row[0], row[1], row[2]), (n, s, EXPECT_MAX_TS))

e = sqlite3.connect("data/events_tw.sqlite")
for sym, (n, s) in EXPECT_DIV.items():
    row = e.execute(
        "SELECT COUNT(*), ROUND(SUM(before_price)+SUM(after_price),2) "
        "FROM dividend_result WHERE stock_id=?", (sym,)
    ).fetchone()
    check(f"dividend_result {sym}", (row[0], row[1]), (n, s))
for sym, n in EXPECT_SUSPEND.items():
    row = e.execute(
        "SELECT COUNT(*) FROM short_sale_suspension WHERE stock_id=?", (sym,)
    ).fetchone()
    check(f"short_sale_suspension {sym}", row[0], n)

c = sqlite3.connect("data/klines.sqlite")
for sym, n in EXPECT_KLINES.items():
    row = c.execute("SELECT COUNT(*) FROM klines WHERE symbol=?", (sym,)).fetchone()
    check(f"klines {sym}", row[0], n)

print("  => 凍結資料庫校驗和：", "全部 PASS" if ok else "★有不符，先回報不要自行修正★")
sys.exit(0 if ok else 1)
PYEOF

  echo
  echo "=== [額外] 離線測試（封鎖 socket，證明不需 FinMind token）==="
  BLOCKDIR="$(mktemp -d)"
  cat > "$BLOCKDIR/sitecustomize.py" <<'PYEOF'
"""封鎖所有對外連線：任何需要即時打 API 的測試都會直接炸開，不會靜默通過。"""
import socket

def _deny(*a, **k):
    raise RuntimeError("NETWORK ACCESS BLOCKED BY setup_cloud_env.sh --full")

socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.create_connection = _deny
socket.getaddrinfo = _deny
PYEOF
  PYTHONPATH="$BLOCKDIR" HTTPS_PROXY= HTTP_PROXY= "$VPY" -m pytest tests/ -q
  rm -rf "$BLOCKDIR"
  echo "  => 全套測試在零網路下通過：確認所有測試都讀已落地的凍結資料庫。"
fi

echo
echo "環境就緒。之後所有指令用：$VPY"
echo "中文輸出請加：PYTHONUTF8=1 PYTHONIOENCODING=utf-8"
