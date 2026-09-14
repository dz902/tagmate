# tagmate

让普通人在管理面创建团队级 agent，绑到飞书/Slack 机器人，用户在平台里 @ 触发。agent 是 by-user 的：用调用者身份取数据；群里带群上下文。设计文档 DESIGN.md。

## 技术栈
Python 3.14 / FastAPI / Strands + Bedrock / sqlite3 标准库 / lark-oapi WebSocket / Vite + Vue 3（无 TS、无组件库、无 pinia）/ pnpm。

## 结构
- `store.py` SQLite：agents, bindings, principals, credentials, sessions, invocations, events。每操作新连接 + WAL。
- `context.py` Principal / InvocationContext / make_session_key（dm 按 principal，群按 chat_id，thread 按 thread_id）。
- `runtime.py` TOOL_REGISTRY（ToolSpec.requires_user_credentials）、select_tools（scene != dm 过滤用户凭证工具，硬防护）、run_invocation。
- `bridges/` 按 binding 启停；`feishu.py` 把 lark 模块级 loop 收进单一 `feishu-ws-loop` 线程，依赖 lark_oapi 1.7.3 私有方法（_connect/_disconnect/_ping_loop）。连接后拉 bot/v3/info 与 tenant/v2/tenant/query 写 `bindings.meta`（租户接口需应用开通 `tenant:tenant:readonly`）。
- `api.py` `/api` 路由；binding credentials 中 secret/token 字段打码 `***`，PUT 时 `***` 保留原值。
- `frontend/` 构建产物入 `static/`（入库）；main.py 托管 `/assets` + `/`。
- `scripts/` seed_dev.py、try_runtime.py（真实 Bedrock）、try_bridge.py、api_smoke.sh、static_smoke.sh。

## 约定
- 管理面不提供 web 聊天，暂无 auth。
- 不加 migration；schema 改动先删 data/ 重 seed。
- 测试 `pytest -q`，dev 依赖 requirements-dev.txt。
- 飞书 OAuth 事实（端点、scope、有效期）以 DESIGN.md 第 3 节为准，用户身份工具只在 DM 场景注册。
