# TagMate 设计

## 1. 产品定位

让普通人在管理面自助创建团队/公司级 agent，绑定到飞书、Slack 等平台的机器人，用户在平台里 @ 机器人触发。

两个关键性质：

- agent 是 by-user 的：agent 带着调用者的身份去取数据。同一个 agent，A 问它只能看到 A 有权限看的文档，B 问它看到的是 B 的。
- 群里带群上下文：在群里 @ 时，agent 知道自己在哪个群、这轮对话之前群里说了什么，回复全群可见。

交互形态沿用 Claude Tag：@ 后 agent 后台执行，在 thread / 原消息下回复结果。

### Claude Tag 能力拆解（参考）

| # | 能力 | 描述 | TagMate 取舍 |
|---|------|------|-------------|
| 1 | Channel-scoped Agent | 一个 channel 一个 agent 实例，所有人共享 | 改为 agent 是一等实体，channel 只是 session 维度之一 |
| 2 | Scoped Memory | memory 按 channel 隔离 | 保留：群 session 按 chat_id 隔离，DM 按 principal 隔离 |
| 3 | Memory 三来源 | 用户显式告知、agent 自动保存、agent 读历史 | 后续 |
| 4 | Proactivity / Routines | cron、watch channel、subscribe PR | 后续 |
| 5 | Agent Identity / Scopes | admin 定义 scope（channel 组 + tools + connections） | 改为 per-user 身份：工具用调用者凭证，而不是 scope 级共享凭证 |
| 6 | Async Execution | tag 后后台跑，thread 回复 | 保留 |
| 7 | Connections / Tools | 外部工具，scope 级权限 | 保留，但凭证粒度是 (principal, connection) |
| 8 | Admin Controls | token 预算、审计日志、memory CRUD | MVP 只做 invocation 日志 |

与 Claude Tag 最大的差别在 5：Tag 的工具凭证是 scope 级（一个 channel 里所有人共享同一个 GitHub 连接），TagMate 是用户级。这是"普通人建公司级 agent"的前提：管理员不需要也不应该把自己的凭证借给全公司用。

---

## 2. 核心实体

### Agent 定义

管理面创建。字段：

| 字段 | 说明 |
|------|------|
| id, name | 标识 |
| instructions | system prompt |
| model | Bedrock model id |
| connections | 允许使用的 connection 列表（如 `feishu`），决定哪些用户身份工具可注册 |
| tools | 允许使用的工具名列表 |
| memory_policy | 记忆策略：MVP 只有 session 内短期记忆，字段先占位 |

### Binding

agent 与平台机器人的绑定。一个 agent 可以绑多个 bot（比如同时挂到飞书和 Slack，或两个飞书应用）。

| 字段 | 说明 |
|------|------|
| agent_id | 所属 agent |
| platform | `feishu` / `slack` |
| credentials | 飞书：app_id / app_secret；Slack：bot token / signing secret |
| status | 连接状态 |

bridge 进程按 binding 启动：每个 binding 一个飞书 WebSocket 长连接（或一个 Slack Socket Mode 连接）。

### Principal

`(platform, user_id)`。飞书用 open_id（应用内稳定，跨应用不同）。

MVP 不做跨平台身份合并：同一个人在飞书和 Slack 是两个 principal，各自有各自的 credentials。

### Credentials

`(principal, connection) -> access_token, refresh_token, expires_at, refresh_expires_at, scopes`。

per-user OAuth 授权得到的凭证。MVP 第一个 connection 就是飞书自身：用飞书的 user_access_token 以用户身份读飞书文档、日历、消息。

### Invocation context

每条消息由 bridge 组装，穿透到 agent 层：

```python
@dataclass
class InvocationContext:
    agent_id: str
    binding_id: str
    principal: Principal          # 发言人
    scene: Literal["dm", "group", "thread"]
    chat_id: str
    message_id: str               # 回复锚点
    thread_id: str | None
    session_key: str              # 见下
```

现有代码 `agent(user_text)` 只传文本，这是要改的根：agent 层必须知道"谁在问、在哪问"，才能选凭证、选 session、决定注册哪些工具。

### Session key

| 场景 | session key | 说明 |
|------|-------------|------|
| DM | `(agent_id, principal)` | 一个人和一个 agent 的私聊是一条连续对话 |
| 群 | `(agent_id, chat_id)` | 群内多人共享一条对话，agent 能看到之前别人问了什么 |
| thread | `(agent_id, chat_id, thread_id)` | 有 thread 时按 thread 隔离，避免群内多话题互相污染 |

session 持有对话历史（Strands 的 messages），按 session key 存取。

---

## 3. 身份与凭证流程（飞书）

以下是核实过的飞书事实，每条带文档 URL。

### 事件里没有用户凭证

机器人事件 `im.message.receive_v1` 的 payload 只含 sender 的 open_id / union_id / user_id，没有任何用户凭证。`header.token` 是事件订阅的 Verification Token，用于校验事件来源，与用户无关。
https://open.feishu.cn/document/server-docs/im-v1/message/events/receive

结论：光靠机器人收消息，只能知道"是谁"，不能"以他的身份"做任何事。用户身份操作必须走 OAuth。

### OAuth 授权码

授权页：`GET https://accounts.feishu.cn/open-apis/authen/v1/authorize`

参数：`client_id`、`response_type=code`、`redirect_uri`（必须在开发者后台登记）、`scope`（空格分隔）、`state`。

code 有效期 5 分钟，一次性。
https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code

### 换 token（v3）

`POST https://accounts.feishu.cn/oauth/v3/token`，`application/x-www-form-urlencoded`

参数：`grant_type=authorization_code`、`client_id`、`client_secret`、`code`、`redirect_uri`。

v2 接口已 deprecated，不要用。
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token-v3

### 刷新

同一端点，`grant_type=refresh_token`。前提：后台开通 `offline_access` 权限，且授权时 scope 里带上它，否则响应里没有 refresh_token。

refresh_token 一次性：用过之后旧的失效，必须用响应里的新 refresh_token 覆盖存储。
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/refresh-user-access-token-v3

### 有效期

- access_token：`expires_in`，文档示例 7200s
- refresh_token：`refresh_token_expires_in`，文档示例 604800s
- 文档强调以响应为准，不要硬编码
- 用户授权后 365 天是硬上限，到期必须重新走授权

### 授权确认体验

- 飞书客户端内打开授权链接：免登，且可以直接跳转免确认页
- PC 端点击聊天中的链接：会跳系统浏览器，需要扫码或密码登录，再看授权页
- 没有"管理员开通即用户免确认"的机制，每个用户至少要过一次授权
- 增量授权只展示新增的 scope

https://open.feishu.cn/document/client-docs/build-login-free-system-

### scope

| 用途 | scope | 备注 |
|------|-------|------|
| 读 docx | `docx:document:readonly` | |
| 读日历 | `calendar:calendar:readonly` | |
| 搜消息 | `search:message` | 仅 user_access_token 可用 |
| 刷新 token | `offline_access` | |

scope 是 API 门槛，不等于数据权限：拿到 `docx:document:readonly` 也只能读该用户本来有权限的文档，文档级共享权限由飞书另判。这正是 by-user 模型想要的。
https://open.feishu.cn/document/server-docs/application-scope/scope-list

### 首次使用流程

```
用户 @agent "帮我总结这篇文档 <url>"
  -> bridge 组装 InvocationContext，调 agent
  -> agent 选择 read_feishu_doc 工具
  -> 工具查 credentials(principal, "feishu")，无 token
  -> 抛 NeedAuthorization(connection="feishu", scopes=[...])
  -> bridge 回一张授权卡片，链接为授权页 URL，state 绑定 principal + 待重跑的 invocation
  -> 用户点击授权
  -> 飞书回调 GET /oauth/feishu/callback?code=...&state=...
  -> 服务端校验 state，换 token，写 credentials
  -> 重跑原 invocation，工具拿到 token，正常返回
```

redirect_uri 必须是公网可达且已登记的地址。本地开发用隧道（cloudflared / ngrok），并把隧道地址登记到开发者后台。

---

## 4. 群场景策略

- principal 仍是发言人：群里 A @agent 读文档，用的是 A 的凭证（但见第 5 节，MVP 群里不开放用户凭证工具）
- 记忆 / 上下文 scope 是该群：session key 按 chat_id，agent 能看到群里之前的对话
- prompt 中声明"你的输出全群可见"，让模型知道不该把私人信息贴出来。这是提示，不是防护，防护见下一节

---

## 5. 安全

原则：LLM 只决定意图，权限由代码决定。不靠 prompt 做访问控制。

### MVP 三条硬防护（代码层）

1. 只申请只读 scope。agent 拿不到写权限，最坏结果是读到不该读的东西，不会造成不可逆修改。
2. 群场景下，需要用户凭证的工具不注册给 LLM。群里 @agent，工具列表里就没有 `read_feishu_doc`，模型无从调用，也就不存在"A 的私人文档被贴到群里"的路径。判断依据是 `InvocationContext.scene`，在构造 Agent 时过滤工具列表。
3. 写操作一律审批卡片。MVP 没有写工具，自动满足；后续加写工具时，工具执行前必须经用户点卡片确认。

### 后续可选

- 污点标记：用用户凭证取回的数据打标，禁止出现在群输出中。这样可以在群里开放用户凭证工具，同时不泄漏
- 审计日志：每次工具调用记录用了谁的凭证、访问了什么资源

### prompt injection

文档内容、群消息都是不可信输入，可能包含指令注入。没有根治方法，策略是限制爆破半径：只读 scope、群里不带用户凭证、写操作人工确认。上面三条硬防护就是为此设计的。

---

## 6. 管理面（MVP）

只做三件事：

1. 建 agent：填 name、instructions、model、勾选 connections / tools
2. 绑 bot：给 agent 添加 binding，填平台凭证，看连接状态
3. 看 invocation 日志：session -> turn -> step -> tool call，每个 tool call 标明用了谁的身份（principal）

不做 web 聊天。web 端的身份和平台身份不一致（web 上没有 open_id），一旦提供 web 聊天就会引入"匿名 principal"污染 by-user 模型。要测 agent 就去飞书里 @ 它。

暂无 auth，开发阶段。

前端视觉和组件模式参考 mangent（`/Users/zhangdai/Code/mangent/frontend`，Vue）。

---

## 7. 技术选型

| 组件 | 选择 | 说明 |
|------|------|------|
| agent 底座 | Strands + Bedrock | 现有 agent.py 已用；Agent 按 invocation 构造（model + instructions + 过滤后的 tools + session messages） |
| 存储 | SQLite | 表：agents, bindings, principals, credentials, sessions, invocations, events |
| 飞书接入 | lark-oapi WebSocket 长连接 | 现有 bridges/feishu.py，不需要公网 webhook |
| Web 框架 | FastAPI | 管理面 API、OAuth 回调、静态前端 |
| 前端 | Vue | 参考 mangent |

### AgentCore（后续可选）

AgentCore 的 Identity（OAuth credential 托管）、Memory（长期记忆）、Gateway（工具连接）、Observability 都能对应上 TagMate 的需求，但 MVP 不接：

- Identity 对应 credentials 表，MVP 自己存 SQLite 够用，等 connection 多了再考虑托管
- Memory 对应 sessions 表 + 后续长期记忆
- Gateway 对应 connections，MVP 只有飞书一个 connection，直接调 lark-oapi

存储层做成接口，后续可替换。

---

## 8. 现状与差距

现状（agent.py + bridges/feishu.py + main.py）：

- 单个 FeishuBridge，凭证从环境变量读，只能挂一个飞书应用
- 全局一个 Strands Agent 实例，`agent(user_text)` 只传文本
- 没有 session：每条消息都是独立对话，群和 DM 不区分
- 没有身份：不知道谁在问，也没有任何用户凭证
- 没有存储
- 已有：WebSocket 收消息、去重、"思考中"卡片 + 完成后更新、纯文本兜底

### MVP 实现顺序

1. 存储与实体：SQLite schema，agents / bindings / principals / credentials / sessions / invocations / events 的读写
2. invocation context 穿透到 agent：bridge 组装 InvocationContext，按 session key 取历史，按 agent 定义 + scene 构造 Agent，记录 invocation / events
3. OAuth 回调与 credentials：授权页 URL 生成、state 管理、`/oauth/feishu/callback`、换 token、刷新、NeedAuthorization -> 授权卡片 -> 重跑
4. 首个用户身份工具：`read_feishu_doc`，用 (principal, feishu) 的 user_access_token 读 docx，只在 DM 场景注册
5. 管理面：建 agent、绑 bot、看 invocation 日志
