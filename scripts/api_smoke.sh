#!/usr/bin/env bash
# 管理面 API 冒烟：起临时 uvicorn（独立 /tmp 库）走一遍 agent / binding / 观测接口，跑完自清理。
# 用法: scripts/api_smoke.sh [port]   默认 8199
set -u
cd "$(dirname "$0")/.."
PORT=${1:-8199}
DB=/tmp/tagmate_api_smoke_$PORT.db
LOG=/tmp/tagmate_api_smoke_$PORT.log
PY=.venv/bin/python
B=http://localhost:$PORT/api

rm -f "$DB"*
TAGMATE_DB=$DB .venv/bin/uvicorn main:app --port "$PORT" --log-level warning > "$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null; rm -f "$DB"* "$LOG"; echo "== cleaned"' EXIT
for _ in $(seq 1 30); do curl -s "$B/status" >/dev/null && break; sleep 0.2; done

jid() { $PY -c "import sys,json;print(json.load(sys.stdin)['id'])"; }

echo "== status"; curl -s "$B/status"
echo; echo "== POST agent"
A=$(curl -s -X POST "$B/agents" -H 'content-type: application/json' -d '{"name":"demo","tools":["current_time"]}')
echo "$A"; AID=$(echo "$A" | jid)
echo "== POST binding (enabled=false, fake creds)"
BD=$(curl -s -X POST "$B/bindings" -H 'content-type: application/json' \
  -d "{\"agent_id\":\"$AID\",\"platform\":\"feishu\",\"enabled\":false,\"credentials\":{\"app_id\":\"cli_fake\",\"app_secret\":\"fake_secret\"}}")
echo "$BD"; BID=$(echo "$BD" | jid)
echo "== GET bindings"; curl -s "$B/bindings?agent_id=$AID"
echo; echo "== PUT enabled=true"; curl -s -X PUT "$B/bindings/$BID" -H 'content-type: application/json' -d '{"enabled":true}'
sleep 3
echo; echo "== GET binding after 3s"; curl -s "$B/bindings/$BID"
echo; echo "== POST stop"; curl -s -X POST "$B/bindings/$BID/stop"
echo; echo "== GET tools"; curl -s "$B/tools"
echo; echo "== GET models"; curl -s "$B/models"
echo; echo "== GET sessions / invocations"; curl -s "$B/sessions"; curl -s "$B/invocations"
echo; echo "== DELETE agent"; curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "$B/agents/$AID"
echo "== GET bindings after delete"; curl -s "$B/bindings"
echo; echo "== uvicorn log"; tail -20 "$LOG"
