# TagMate — 通用版 Claude Tag on AgentCore

## 目标

做一个类似 Claude Tag 的产品：在团队协作平台（Slack/Discord/Teams/飞书/...）中 @agent，
agent 后台工作，thread 回复结果。不锁定底层模型，不锁定 Slack。

使用 AWS Bedrock AgentCore 作为 infra 底座。

---

## Claude Tag 核心能力拆解

| # | 能力 | 描述 |
|---|------|------|
| 1 | **Channel-scoped Agent** | 一个 channel 一个 agent 实例，所有人共享（multiplayer） |
| 2 | **Scoped Memory** | memory 按 channel 隔离。public → workspace 共享，private → channel only |
| 3 | **Memory 三来源** | 用户显式告知、agent 自动保存、agent 读历史 |
| 4 | **Proactivity / Routines** | cron 定时任务、watch channel、subscribe PR、ambient 主动通知 |
| 5 | **Agent Identity / Scopes** | admin 定义 scope（channel 组 + tools + connections），scope 间完全隔离 |
| 6 | **Async Execution** | tag 后 agent 后台跑，完成后 thread 回复 |
| 7 | **Connections / Tools** | GitHub/Jira/Slack/Datadog 等外部工具，scope 级权限控制 |
| 8 | **Admin Controls** | token 预算（org + channel）、审计日志、memory CRUD |

---

## AgentCore 能力映射

### 直接可用（配置即用）

| Tag 能力 | AgentCore 服务 | 用法 |
|----------|--------------|------|
| Agent loop / orchestration | **Harness**（声明式）或 **Runtime**（自定义代码） | Harness: model + prompt + tools 声明；Runtime: 带框架代码部署 |
| Tool connections | **Gateway** | 1-click integrations (Slack/Jira/Salesforce...)，支持 MCP/OpenAPI/Lambda |
| Short-term memory | **Memory** (short-term) | session 内 turn-by-turn context |
| Long-term memory | **Memory** (long-term) | 跨 session 自动提取 insights |
| Code execution | **Code Interpreter** | 隔离沙箱执行代码 |
| Web browsing | **Browser** | 托管浏览器环境 |
| Observability / Tracing | **Observability** | OpenTelemetry trace, unified dashboard |
| Agent auth / credential | **Identity** | workload identity + OAuth credential exchange |
| Tool access policy | **Policy** | Cedar 语言细粒度规则 |

### 需要自建的层

| Tag 能力 | 自建组件 | 复杂度 | 说明 |
|----------|---------|--------|------|
| Channel Adapter | `ChannelAdapter` | **中** | Slack/Discord/Teams/飞书的 webhook/event 接收 + 消息发送。每个平台一个 adapter |
| Scope Manager | `ScopeManager` | **中** | admin 定义 scope（channel 组 → harness/memory store/tools 绑定），scope 间隔离 |
| Multiplayer Session | `SessionRouter` | **中** | 同一 channel 的多人消息 → 同一个 agent session。需要并发控制 |
| Proactivity / Scheduler | `SchedulerService` | **高** | 解析自然语言定义的 routine → cron expression → EventBridge 触发 → agent 执行 |
| Memory Scope Bridge | `MemoryScopeBridge` | **低** | channel → AgentCore Memory store 的映射。public channel → shared store，private → isolated store |
| Budget Controller | `BudgetController` | **低** | token/cost 计数 + 限额检查。per-org + per-scope + per-channel |
| Admin API | `AdminService` | **中** | scope CRUD、memory 管理、budget 配置、审计日志查询 |

---

## 架构分层

```
┌─────────────────────────────────────────────────┐
│              Messaging Platforms                 │
│  Slack  │  Discord  │  Teams  │  飞书  │  ...   │
└────────────────────┬────────────────────────────┘
                     │ webhooks / events
                     ▼
┌─────────────────────────────────────────────────┐
│            Channel Adapter Layer                 │
│  SlackAdapter │ DiscordAdapter │ FeishuAdapter   │
│  - receive @mention events                      │
│  - normalize to internal Message format          │
│  - send replies back to thread                   │
└────────────────────┬────────────────────────────┘
                     │ internal Message
                     ▼
┌─────────────────────────────────────────────────┐
│              Core Application Layer              │
│                                                  │
│  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ ScopeManager │  │ SessionRouter            │ │
│  │ - scope CRUD │  │ - channel → session map  │ │
│  │ - tools bind │  │ - multiplayer routing    │ │
│  │ - memory bind│  │ - concurrency control    │ │
│  └──────────────┘  └──────────────────────────┘ │
│                                                  │
│  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ Scheduler    │  │ BudgetController         │ │
│  │ - routines   │  │ - token counting         │ │
│  │ - cron jobs  │  │ - spend limits           │ │
│  │ - PR watch   │  │ - per-scope / per-org    │ │
│  └──────────────┘  └──────────────────────────┘ │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │ AdminService                              │   │
│  │ - scope management UI/API                 │   │
│  │ - memory management                       │   │
│  │ - audit log                               │   │
│  └──────────────────────────────────────────┘   │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│         AWS Bedrock AgentCore (infra)            │
│                                                  │
│  Harness/Runtime │ Memory │ Gateway │ Identity   │
│  Code Interpreter│Browser │ Policy  │ Observ.    │
│                                                  │
│  - agent loop (model-agnostic)                   │
│  - scoped memory stores                          │
│  - MCP tool gateway with auth                    │
│  - workload identity + OAuth                     │
│  - sandbox (code/browser)                        │
│  - tracing + evaluation                          │
└─────────────────────────────────────────────────┘
```

---

## 关键设计决策

### 1. Harness vs Runtime

**推荐：先用 Harness，后期按需切 Runtime**

- Harness = 声明式，零代码。model + prompt + tools 声明即可，AgentCore 管 loop。
  适合 MVP：快速验证 Tag 模式是否 work。
- Runtime = 自定义代码部署（Strands/LangGraph/自研框架）。
  当 Harness 不够灵活时再切——比如需要自定义 tool 选择逻辑、multi-agent 编排等。
- Harness 可以 export 成 Strands 代码 → 无缝迁移到 Runtime。

### 2. Memory 拓扑

Claude Tag 的 memory 拓扑：
- workspace store（public channels 共享读写）
- channel store（private channel 独有）
- DM store（per-person 私有）

映射到 AgentCore Memory：
- 一个 workspace = 一个 shared memory store（namespace: `ws:{workspace_id}`）
- 一个 private channel = 一个 isolated memory store（namespace: `ch:{channel_id}`）
- 一个 DM = 一个 user memory store（namespace: `dm:{user_id}`）
- agent 读取时：public context → 查 ws store；private context → 查 ch store + ws store (readonly)

### 3. Channel Adapter 接口

```python
class ChannelAdapter(ABC):
    """Messaging platform adapter — 一个平台一个实现"""

    @abstractmethod
    async def start(self):
        """启动 webhook listener / WebSocket 连接"""

    @abstractmethod
    async def on_mention(self, event: MentionEvent) -> None:
        """收到 @agent mention 时调用"""

    @abstractmethod
    async def send_reply(self, channel_id: str, thread_id: str, content: str) -> None:
        """向 channel thread 发送回复"""

    @abstractmethod
    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        """获取 channel 元信息（public/private、name 等）"""

    @abstractmethod
    async def get_channel_history(self, channel_id: str, limit: int) -> list[Message]:
        """获取 channel 历史消息（用于 agent 自主学习 context）"""
```

### 4. Scope 模型

```python
@dataclass
class Scope:
    id: str
    name: str                          # e.g. "engineering", "sales-support"
    channels: list[str]                # channel IDs assigned to this scope
    harness_id: str                    # AgentCore Harness ID (model + prompt + tools)
    memory_stores: dict[str, str]      # channel_id → memory_store_id mapping
    shared_memory_store: str           # workspace-level shared store ID
    gateway_id: str                    # AgentCore Gateway endpoint
    tools: list[str]                   # allowed tool names
    budget: Budget                     # token/cost limits
    routines: list[Routine]            # scheduled jobs
```

### 5. Proactivity / Scheduler

MVP 方案：
- 用户在 channel 中用自然语言描述 routine
- Agent 解析为结构化 routine spec（schedule + action + output channel）
- 持久化到 DB
- 一个 poller/scheduler 进程定期检查到期的 routine → 调 Harness invoke
- 结果通过 ChannelAdapter.send_reply 投递回 channel

后期可迁移到 EventBridge Scheduler，但 MVP 不需要。

---

## 实现优先级

### Phase 1: MVP — 能用 (2-3 weeks)

目标：Slack 中 @agent，后台执行，thread 回复。有基础 memory。

1. **SlackAdapter** — Slack Events API 接收 @mention，reply 到 thread
2. **SessionRouter** — channel → Harness session 映射，multiplayer 路由
3. **Harness 集成** — 声明式创建 agent（model + prompt + tools）
4. **Memory 集成** — 一个 shared memory store，所有 channel 共用（简化版）
5. **Gateway 集成** — 配几个基础 tools（GitHub、web search）

### Phase 2: Scoping + Memory 隔离 (1-2 weeks)

6. **ScopeManager** — admin 定义 scope，channel→scope 绑定
7. **MemoryScopeBridge** — channel 级 memory 隔离
8. **BudgetController** — 基础 token 计数 + 限额

### Phase 3: Proactivity (2-3 weeks)

9. **Scheduler** — routine 解析 + 定时执行
10. **Channel watch** — 监听其他 channel 消息
11. **PR subscription** — GitHub webhook → agent 响应

### Phase 4: Multi-platform + Admin (2+ weeks)

12. **DiscordAdapter / FeishuAdapter** — 更多平台接入
13. **Admin Dashboard** — scope 管理、memory 管理、审计日志
14. **Optimization** — AgentCore Evaluations + A/B testing

---

## 技术选择

| 组件 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.12+ | AgentCore SDK 原生支持，async 生态成熟 |
| Web 框架 | FastAPI | Slack Events API webhook + Admin API |
| AgentCore SDK | `boto3` bedrock-agentcore | 官方 SDK |
| Slack SDK | `slack-bolt` | 官方 Python SDK，支持 Events API + Socket Mode |
| 数据库 | DynamoDB 或 SQLite | Scope/Routine 元数据。MVP 可用 SQLite，生产用 DynamoDB |
| Scheduler | 内置 poller (MVP) → EventBridge (prod) | 渐进式 |
| 部署 | Lambda + API Gateway 或 ECS | Lambda 适合 webhook 接收；ECS 适合长时运行的 agent session |
