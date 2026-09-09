"""Bridge 基类与注册表。"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class BridgeStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class BridgeInfo:
    """Bridge 运行时状态快照。"""

    name: str
    type: str  # "feishu", "slack", "discord", ...
    status: BridgeStatus = BridgeStatus.DISCONNECTED
    connected_at: datetime | None = None
    message_count: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "status": self.status.value,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "message_count": self.message_count,
            "last_error": self.last_error,
        }


class Bridge(ABC):
    """消息平台适配器基类。"""

    def __init__(self, name: str, bridge_type: str):
        self.info = BridgeInfo(name=name, type=bridge_type)

    @abstractmethod
    def start(self) -> None:
        """启动 bridge（在后台线程中调用）。"""

    @abstractmethod
    def stop(self) -> None:
        """停止 bridge。"""


# --- 全局注册表 ---

_bridges: dict[str, Bridge] = {}
_lock = threading.Lock()


def register(bridge: Bridge) -> None:
    with _lock:
        _bridges[bridge.info.name] = bridge


def get_all() -> list[BridgeInfo]:
    with _lock:
        return [b.info for b in _bridges.values()]


def get(name: str) -> Bridge | None:
    with _lock:
        return _bridges.get(name)
