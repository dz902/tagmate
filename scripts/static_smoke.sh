#!/usr/bin/env bash
# 前端静态托管冒烟：起临时 uvicorn（独立 /tmp 库），检查 / 返 index.html、/assets/*.js|css 200、/api/status 正常。
# 用法: scripts/static_smoke.sh [port]   默认 8198（先 pnpm build 产出 static/）
set -u
cd "$(dirname "$0")/.."
PORT=${1:-8198}
DB=/tmp/tm_fe_$PORT.db
LOG=/tmp/tm_fe_$PORT.log
B=http://localhost:$PORT

[ -f static/index.html ] || { echo "static/index.html 不存在，先 cd frontend && pnpm build"; exit 1; }

rm -f "$DB"*
TAGMATE_DB=$DB .venv/bin/uvicorn main:app --port "$PORT" --log-level warning > "$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null; rm -f "$DB"* "$LOG"; echo "== cleaned"' EXIT
for _ in $(seq 1 30); do curl -s "$B/api/status" >/dev/null && break; sleep 0.2; done

fail=0
check() {  # check <path> <expect-substring-in-body>
    local code body
    body=$(curl -s -w '\n%{http_code}' "$B$1")
    code=${body##*$'\n'}
    body=${body%$'\n'*}
    if [ "$code" = 200 ] && [[ "$body" == *"$2"* ]]; then echo "ok   $1 ($code)"
    else echo "FAIL $1 ($code)"; fail=1; fi
}

check / '<div id="app">'
for f in static/assets/*; do check "/assets/$(basename "$f")" ""; done
check /api/status '"status":"ok"'
exit $fail
