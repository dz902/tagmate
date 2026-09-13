"""开发环境种子：从 .env 的 APP_ID/APP_SECRET 建默认 agent 'TagMate' + 一个 feishu binding。幂等。

用法：python scripts/seed_dev.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import store

AGENT_NAME = "TagMate"
INSTRUCTIONS = "你是 TagMate，一个团队协作 AI 助手。用简洁的中文回答问题。"


def main() -> None:
    app_id, app_secret = os.environ.get("APP_ID"), os.environ.get("APP_SECRET")
    if not app_id or not app_secret:
        sys.exit("APP_ID / APP_SECRET 未设置（.env）")

    store.init_db()
    existing = [a for a in store.list_agents() if a["name"] == AGENT_NAME]
    if existing:
        print(f"agent '{AGENT_NAME}' 已存在 (id={existing[0]['id']})，跳过")
        return

    agent = store.create_agent(AGENT_NAME, instructions=INSTRUCTIONS, tools=["current_time"])
    binding = store.create_binding(agent["id"], "feishu", {"app_id": app_id, "app_secret": app_secret})
    print(f"created agent id={agent['id']}, binding id={binding['id']} (db={store.DB_PATH})")


if __name__ == "__main__":
    main()
