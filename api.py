"""管理面 API（DESIGN.md 第 6 节）：agent CRUD、binding 增删改与启停、session / invocation 观测。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import bridges
import runtime
import store

router = APIRouter(prefix="/api")

SECRET_MASK = "***"

# 前端下拉用；默认模型在前
MODELS = [
    {"id": runtime.DEFAULT_MODEL, "name": "Claude Sonnet 4"},
    {"id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0", "name": "Claude 3.7 Sonnet"},
    {"id": "us.anthropic.claude-3-5-haiku-20241022-v1:0", "name": "Claude 3.5 Haiku"},
]


# ---------- helpers ----------

def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return "secret" in k or "token" in k


def _mask_credentials(creds: dict) -> dict:
    return {k: (SECRET_MASK if _is_secret_key(k) else v) for k, v in creds.items()}


def _binding_view(binding: dict) -> dict:
    """store 的 binding dict + 运行时状态，credentials 打码。"""
    out = dict(binding)
    out["credentials"] = _mask_credentials(binding["credentials"])
    info = bridges.get_status(binding["id"])
    if info is None:
        out.update(status="disconnected", connected_at=None, message_count=0, last_error=None)
    else:
        d = info.to_dict()
        out.update(status=d["status"], connected_at=d["connected_at"],
                   message_count=d["message_count"], last_error=d["last_error"])
    return out


def _start_binding_safe(binding: dict) -> str | None:
    """启动 bridge，异常不抛，返回错误信息。"""
    try:
        bridges.start_binding(binding)
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def _get_or_404(getter, id: str, what: str) -> dict:
    row = getter(id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what} not found: {id}")
    return row


# ---------- agents ----------

class AgentCreate(BaseModel):
    name: str
    instructions: str = ""
    model: str = ""
    connections: list[str] = []
    tools: list[str] = []
    memory_policy: str = "session"


class AgentUpdate(BaseModel):
    name: str | None = None
    instructions: str | None = None
    model: str | None = None
    connections: list[str] | None = None
    tools: list[str] | None = None
    memory_policy: str | None = None


@router.get("/agents")
def list_agents():
    return store.list_agents()


@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate):
    return store.create_agent(**body.model_dump())


@router.get("/agents/{id}")
def get_agent(id: str):
    return _get_or_404(store.get_agent, id, "agent")


@router.put("/agents/{id}")
def update_agent(id: str, body: AgentUpdate):
    _get_or_404(store.get_agent, id, "agent")
    return store.update_agent(id, **body.model_dump(exclude_none=True))


@router.delete("/agents/{id}", status_code=204)
def delete_agent(id: str):
    _get_or_404(store.get_agent, id, "agent")
    for b in store.list_bindings(agent_id=id):
        bridges.stop_binding(b["id"])
    store.delete_agent(id)
    return Response(status_code=204)


# ---------- bindings ----------

class BindingCreate(BaseModel):
    agent_id: str
    platform: str
    credentials: dict[str, Any]
    enabled: bool = True


class BindingUpdate(BaseModel):
    credentials: dict[str, Any] | None = None
    enabled: bool | None = None


@router.get("/bindings")
def list_bindings(agent_id: str | None = None):
    return [_binding_view(b) for b in store.list_bindings(agent_id=agent_id)]


@router.post("/bindings", status_code=201)
def create_binding(body: BindingCreate):
    _get_or_404(store.get_agent, body.agent_id, "agent")
    binding = store.create_binding(body.agent_id, body.platform, body.credentials, body.enabled)
    start_error = _start_binding_safe(binding) if body.enabled else None
    view = _binding_view(binding)
    if start_error:
        view.update(status="error", last_error=start_error)
    return view


@router.get("/bindings/{id}")
def get_binding(id: str):
    return _binding_view(_get_or_404(store.get_binding, id, "binding"))


@router.put("/bindings/{id}")
def update_binding(id: str, body: BindingUpdate):
    old = _get_or_404(store.get_binding, id, "binding")
    fields: dict[str, Any] = {}
    if body.credentials is not None:
        merged = dict(body.credentials)
        for k, v in merged.items():
            if v == SECRET_MASK and k in old["credentials"]:
                merged[k] = old["credentials"][k]
        fields["credentials"] = merged
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    binding = store.update_binding(id, **fields)

    bridges.stop_binding(id)
    start_error = _start_binding_safe(binding) if binding["enabled"] else None
    view = _binding_view(binding)
    if start_error:
        view.update(status="error", last_error=start_error)
    return view


@router.delete("/bindings/{id}", status_code=204)
def delete_binding(id: str):
    _get_or_404(store.get_binding, id, "binding")
    bridges.stop_binding(id)
    store.delete_binding(id)
    return Response(status_code=204)


@router.post("/bindings/{id}/start")
def start_binding(id: str):
    binding = _get_or_404(store.get_binding, id, "binding")
    start_error = _start_binding_safe(binding)
    view = _binding_view(binding)
    if start_error:
        view.update(status="error", last_error=start_error)
    return view


@router.post("/bindings/{id}/stop")
def stop_binding(id: str):
    binding = _get_or_404(store.get_binding, id, "binding")
    bridges.stop_binding(id)
    return _binding_view(binding)


# ---------- sessions / invocations ----------

@router.get("/sessions")
def list_sessions(agent_id: str | None = None, limit: int = 50):
    out = []
    for s in store.list_sessions(agent_id=agent_id, limit=limit):
        messages = s.pop("messages")
        s["message_count"] = len(messages)
        out.append(s)
    return out


@router.get("/sessions/{id}")
def get_session(id: str):
    return _get_or_404(store.get_session, id, "session")


@router.get("/invocations")
def list_invocations(agent_id: str | None = None, session_id: str | None = None, limit: int = 50):
    return store.list_invocations(agent_id=agent_id, session_id=session_id, limit=limit)


@router.get("/invocations/{id}")
def get_invocation(id: str):
    inv = _get_or_404(store.get_invocation, id, "invocation")
    inv["events"] = store.list_events(id)
    inv["principal"] = store.get_principal(inv["principal_id"])
    return inv


# ---------- 元数据 ----------

@router.get("/tools")
def list_tools():
    return [{"name": name, "requires_user_credentials": spec.requires_user_credentials}
            for name, spec in runtime.TOOL_REGISTRY.items()]


@router.get("/models")
def list_models():
    return MODELS


@router.get("/status")
def status():
    return {"status": "ok", "bridges": len(bridges.get_all())}
