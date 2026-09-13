"""用假 ctx 真实调一次 Bedrock，验证 messages 往返与 events 落库。

用法：TAGMATE_DB=/tmp/tagmate_try.db python scripts/try_runtime.py [问题] [--scene dm|group] [--rounds N]
默认 dm 场景、问"现在几点"、跑 2 轮（第二轮验证历史 messages 能从库里读回再喂给 Strands）。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store
import runtime
from context import Principal, build_context


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="现在几点？")
    ap.add_argument("--scene", default="dm", choices=["dm", "group", "thread"])
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    store.init_db()
    print(f"db={store.DB_PATH}")
    agent = store.create_agent("try", instructions="你是测试助手，用简洁中文回答。", tools=["current_time"])
    binding = store.create_binding(agent["id"], "feishu", {"app_id": "x", "app_secret": "y"})
    principal = Principal("feishu", "ou_try", display_name="张三")
    ctx = build_context(agent["id"], binding["id"], principal, args.scene, "oc_try", "om_try",
                        thread_id="omt_try" if args.scene == "thread" else None)

    for i in range(args.rounds):
        q = args.question if i == 0 else "刚才你说几点？只重复时间。"
        print(f"\n=== round {i + 1}: {q}")
        out = runtime.run_invocation(ctx, q)
        print(f"reply: {out}")

    session = store.get_or_create_session(agent["id"], ctx.session_key, ctx.scene, ctx.chat_id, ctx.thread_id)
    print(f"\nsession messages: {len(session['messages'])}")
    for inv in reversed(store.list_invocations(agent_id=agent["id"])):
        print(f"\ninvocation {inv['id']} status={inv['status']} error={inv['error']}")
        for e in store.list_events(inv["id"]):
            print(f"  [{e['seq']}] {e['type']}: {str(e['payload'])[:160]}")


if __name__ == "__main__":
    main()
