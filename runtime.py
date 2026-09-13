"""运行时：按 InvocationContext 构造 Strands Agent 跑一轮，落 session / invocation / events。

安全边界（DESIGN.md 第 5 节）在 select_tools 里由代码强制：非 dm 场景不注册需要用户凭证的工具。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from strands import Agent, tool
from strands.models.bedrock import BedrockModel

import store
from context import InvocationContext, Scene

DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_REGION = "us-east-1"
TOOL_RESULT_MAX_CHARS = 2000


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable
    requires_user_credentials: bool = False


TOOL_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(name: str, fn: Callable, requires_user_credentials: bool = False) -> None:
    TOOL_REGISTRY[name] = ToolSpec(fn=fn, requires_user_credentials=requires_user_credentials)


@tool
def current_time() -> str:
    """返回当前本地日期和时间（ISO 8601）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


register_tool("current_time", current_time)


def select_tools(agent_tools: list[str], scene: Scene) -> list[Callable]:
    """按 agent 定义挑工具；不在注册表的跳过；非 dm 场景过掉需要用户凭证的工具。"""
    out = []
    for name in agent_tools:
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            continue
        if spec.requires_user_credentials and scene != "dm":
            continue
        out.append(spec.fn)
    return out


def build_system_prompt(instructions: str, ctx: InvocationContext) -> str:
    who = ctx.principal.display_name or ctx.principal.user_id
    if ctx.scene == "dm":
        where = "私聊（DM）"
    elif ctx.scene == "thread":
        where = "群聊话题（thread）"
    else:
        where = "群聊"
    lines = [instructions.strip(), "", "## 运行时上下文", f"- 当前场景：{where}", f"- 发言人：{who}"]
    if ctx.scene != "dm":
        lines.append("- 你的回复全群可见，不要输出任何人的私人信息。")
    return "\n".join(lines)


def _truncate(s: str, n: int = TOOL_RESULT_MAX_CHARS) -> str:
    return s if len(s) <= n else s[:n] + f"...[truncated {len(s) - n} chars]"


def _record_events(invocation_id: str, new_messages: list[dict[str, Any]]) -> None:
    for m in new_messages:
        for block in m.get("content", []):
            if m["role"] == "assistant" and "toolUse" in block:
                tu = block["toolUse"]
                store.append_event(invocation_id, "tool_call",
                                   {"toolUseId": tu.get("toolUseId"), "name": tu["name"], "input": tu.get("input")})
            elif m["role"] == "user" and "toolResult" in block:
                tr = block["toolResult"]
                content = _truncate(json.dumps(tr.get("content", []), ensure_ascii=False, default=str))
                store.append_event(invocation_id, "tool_result",
                                   {"toolUseId": tr.get("toolUseId"), "status": tr.get("status"), "content": content})


def run_invocation(ctx: InvocationContext, user_text: str) -> str:
    agent_def = store.get_agent(ctx.agent_id)
    if agent_def is None:
        raise LookupError(f"agent not found: {ctx.agent_id}")

    principal = store.get_or_create_principal(ctx.principal.platform, ctx.principal.user_id,
                                              ctx.principal.display_name)
    session = store.get_or_create_session(ctx.agent_id, ctx.session_key, ctx.scene, ctx.chat_id,
                                          ctx.thread_id)
    inv = store.create_invocation(ctx.agent_id, ctx.binding_id, session["id"], principal["id"],
                                  ctx.scene, ctx.chat_id, ctx.message_id, user_text, ctx.thread_id)

    history: list[dict] = session["messages"]
    history_len = len(history)
    try:
        agent = Agent(
            model=BedrockModel(model_id=agent_def["model"] or DEFAULT_MODEL, region_name=DEFAULT_REGION),
            system_prompt=build_system_prompt(agent_def["instructions"], ctx),
            tools=select_tools(agent_def["tools"], ctx.scene),
            messages=history,
            callback_handler=None,
        )
        result = agent(user_text)
        output = str(result)

        # MVP 没有用户凭证工具，events 的 principal_id 一律 None
        _record_events(inv["id"], agent.messages[history_len:])
        store.append_event(inv["id"], "assistant", {"text": output})
        store.update_session_messages(session["id"], agent.messages)
        store.finish_invocation(inv["id"], "completed", output_text=output)
        return output
    except Exception as e:
        store.append_event(inv["id"], "error", {"error": str(e), "type": type(e).__name__})
        store.finish_invocation(inv["id"], "failed", error=str(e))
        raise
