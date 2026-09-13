"""真实启停一个 feishu binding：start -> 等待 -> 打印状态 -> stop -> 确认 ws loop 线程退出。不发消息。

用法：python scripts/try_bridge.py [binding_id] [--hold SECONDS]
不传 binding_id 时用 DB 里第一个 enabled 的 feishu binding。
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridges
import store
from bridges import feishu


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("binding_id", nargs="?")
    ap.add_argument("--hold", type=float, default=5.0, help="保持连接的秒数")
    args = ap.parse_args()

    store.init_db()
    if args.binding_id:
        binding = store.get_binding(args.binding_id)
    else:
        binding = next((b for b in store.list_bindings() if b["platform"] == "feishu" and b["enabled"]), None)
    if binding is None:
        sys.exit("no feishu binding found")

    print(f"start binding {binding['id']}")
    bridges.start_binding(binding)
    deadline = time.monotonic() + args.hold
    while time.monotonic() < deadline:
        time.sleep(1)
        print("status:", bridges.get_status(binding["id"]).to_dict())

    t = feishu.ws_loop_thread()
    print("stop binding")
    t0 = time.monotonic()
    bridges.stop_binding(binding["id"])
    t.join(5)
    print(f"stopped in {time.monotonic() - t0:.2f}s, loop thread alive={t.is_alive()}, registry={bridges.get_all()}")
    if t.is_alive():
        sys.exit(1)


if __name__ == "__main__":
    main()
