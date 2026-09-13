"""InvocationContext：bridge 组装、穿透到 agent 层的"谁在问、在哪问"。见 DESIGN.md 第 2 节。"""

from dataclasses import dataclass
from typing import Literal

Scene = Literal["dm", "group", "thread"]


@dataclass(frozen=True)
class Principal:
    platform: str
    user_id: str
    display_name: str | None = None


@dataclass(frozen=True)
class InvocationContext:
    agent_id: str
    binding_id: str
    principal: Principal
    scene: Scene
    chat_id: str
    message_id: str
    thread_id: str | None
    session_key: str


def make_session_key(agent_id: str, principal: Principal, scene: Scene,
                     chat_id: str, thread_id: str | None = None) -> str:
    """dm 按 principal 隔离；group 按 chat；thread 按 chat + thread。"""
    if scene == "dm":
        return f"{agent_id}:dm:{principal.platform}:{principal.user_id}"
    if scene == "group":
        return f"{agent_id}:chat:{chat_id}"
    if scene == "thread":
        if not thread_id:
            raise ValueError("thread scene requires thread_id")
        return f"{agent_id}:chat:{chat_id}:{thread_id}"
    raise ValueError(f"unknown scene: {scene}")


def build_context(agent_id: str, binding_id: str, principal: Principal, scene: Scene,
                  chat_id: str, message_id: str, thread_id: str | None = None) -> InvocationContext:
    """组装 ctx 并自动算 session_key。"""
    return InvocationContext(
        agent_id=agent_id, binding_id=binding_id, principal=principal, scene=scene,
        chat_id=chat_id, message_id=message_id, thread_id=thread_id,
        session_key=make_session_key(agent_id, principal, scene, chat_id, thread_id),
    )
