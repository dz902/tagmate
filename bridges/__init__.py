"""Bridge 基类与按 binding 管理的运行时注册表。"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import store


class BridgeStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class BridgeInfo:
    """Bridge 运行时状态快照。"""

    name: str
    type: str  # "feishu", "slack", ...
    binding_id: str
    agent_id: str
    status: BridgeStatus = BridgeStatus.DISCONNECTED
    connected_at: datetime | None = None
    message_count: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "binding_id": self.binding_id,
            "agent_id": self.agent_id,
            "status": self.status.value,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "message_count": self.message_count,
            "last_error": self.last_error,
        }


class Bridge(ABC):
    """消息平台适配器基类，一个 binding 一个实例。"""

    def __init__(self, binding: dict):
        self.binding = binding
        self.info = BridgeInfo(name=binding["id"], type=binding["platform"],
                               binding_id=binding["id"], agent_id=binding["agent_id"])

    @abstractmethod
    def start(self) -> None:
        """启动 bridge（非阻塞，内部起后台线程）。"""

    @abstractmethod
    def stop(self) -> None:
        """停止 bridge（尽力而为）。"""


# --- 运行时注册表：binding_id -> Bridge ---

_bridges: dict[str, Bridge] = {}
_lock = threading.Lock()


def _make_bridge(binding: dict) -> Bridge:
    platform = binding["platform"]
    if platform == "feishu":
        from bridges.feishu import FeishuBridge
        return FeishuBridge(binding)
    raise ValueError(f"unsupported platform: {platform}")


def start_binding(binding: dict) -> BridgeInfo:
    """按 binding 启动 bridge；同 id 已在跑的先 stop 再替换。"""
    bridge = _make_bridge(binding)
    with _lock:
        old = _bridges.pop(binding["id"], None)
        if old is not None:
            old.stop()
        _bridges[binding["id"]] = bridge
    bridge.start()
    return bridge.info


def stop_binding(binding_id: str) -> bool:
    with _lock:
        bridge = _bridges.pop(binding_id, None)
    if bridge is None:
        return False
    bridge.stop()
    return True


def get_status(binding_id: str) -> BridgeInfo | None:
    with _lock:
        bridge = _bridges.get(binding_id)
    return bridge.info if bridge else None


def get_all() -> list[BridgeInfo]:
    with _lock:
        return [b.info for b in _bridges.values()]


def start_all_enabled() -> list[BridgeInfo]:
    infos = []
    for binding in store.list_bindings():
        if not binding["enabled"]:
            continue
        try:
            infos.append(start_binding(binding))
        except Exception as e:
            print(f"[bridges] start binding {binding['id']} failed: {e}")
    return infos


def stop_all() -> None:
    with _lock:
        ids = list(_bridges)
    for bid in ids:
        stop_binding(bid)
