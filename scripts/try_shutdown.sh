#!/usr/bin/env bash
# 启动 main.py，等待连上飞书后发一次 SIGINT，测量退出耗时并打印日志尾部。
# 用法: scripts/try_shutdown.sh [warmup_seconds=6] [max_wait_seconds=15]
set -u
cd "$(dirname "$0")/.."
source .venv/bin/activate

WARMUP=${1:-6}
MAX_WAIT=${2:-15}
LOG=/tmp/tagmate_shutdown.log

python main.py > "$LOG" 2>&1 &
PID=$!
sleep "$WARMUP"

START=$(python -c 'import time; print(time.time())')
echo "--- SIGINT -> $PID"
kill -INT "$PID"

ELAPSED=""
for _ in $(seq 1 $((MAX_WAIT * 10))); do
  sleep 0.1
  if ! kill -0 "$PID" 2>/dev/null; then
    ELAPSED=$(python -c "import time; print(f'{time.time()-$START:.2f}')")
    break
  fi
done

if [ -z "$ELAPSED" ]; then
  echo "STILL ALIVE after ${MAX_WAIT}s, SIGKILL"
  kill -9 "$PID"
  wait "$PID" 2>/dev/null
  echo "result: FAIL"
else
  wait "$PID" 2>/dev/null
  echo "exited in ${ELAPSED}s"
fi

echo "--- log tail"
tail -15 "$LOG"
