"""FeishuBridge 多实例启停：不连真飞书，把 lark ws Client 的建连/心跳协程换成假的。"""

import asyncio
import time

import pytest
import lark_oapi.ws.client as lark_ws

import bridges
from bridges import BridgeStatus
from bridges import feishu


def _binding(bid: str) -> dict:
    return {
        "id": bid,
        "agent_id": "agent-1",
        "platform": "feishu",
        "enabled": 1,
        "credentials": {"app_id": f"cli_{bid}", "app_secret": "x"},
    }


def _wait_status(bid: str, status: BridgeStatus, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = bridges.get_status(bid)
        if info is not None and info.status == status:
            return
        time.sleep(0.02)
    info = bridges.get_status(bid)
    raise AssertionError(f"{bid} status={info.status if info else None}, expected {status}")


@pytest.fixture
def fake_ws(monkeypatch):
    """lark ws Client：_connect 立刻成功，_ping_loop 长睡（模拟长连接保持）。
    同时 patch _fetch_meta 避免真实网络调用。"""

    async def fake_connect(self):
        pass

    async def fake_ping_loop(self):
        await asyncio.sleep(3600)

    def fake_fetch_meta(self):
        return {}

    monkeypatch.setattr(lark_ws.Client, "_connect", fake_connect)
    monkeypatch.setattr(lark_ws.Client, "_ping_loop", fake_ping_loop)
    monkeypatch.setattr(feishu.FeishuBridge, "_fetch_meta", fake_fetch_meta)
    yield
    bridges.stop_all()


def test_two_bindings_connect_and_stop_independently(fake_ws):
    bridges.start_binding(_binding("b1"))
    bridges.start_binding(_binding("b2"))
    _wait_status("b1", BridgeStatus.CONNECTED)
    _wait_status("b2", BridgeStatus.CONNECTED)
    assert bridges.get_status("b1").last_error is None
    assert bridges.get_status("b2").last_error is None

    t = feishu.ws_loop_thread()
    assert t is not None and t.is_alive()

    b1 = bridges._bridges["b1"]
    assert bridges.stop_binding("b1") is True
    assert b1.info.status == BridgeStatus.DISCONNECTED
    assert bridges.get_status("b1") is None
    assert b1._future is None

    # b2 不受影响，loop 线程仍在跑
    assert bridges.get_status("b2").status == BridgeStatus.CONNECTED
    assert feishu.ws_loop_thread() is t and t.is_alive()


def test_stop_all_exits_loop_thread(fake_ws):
    bridges.start_binding(_binding("b1"))
    bridges.start_binding(_binding("b2"))
    _wait_status("b1", BridgeStatus.CONNECTED)
    _wait_status("b2", BridgeStatus.CONNECTED)
    t = feishu.ws_loop_thread()

    started = time.monotonic()
    bridges.stop_all()
    assert time.monotonic() - started < 3.0
    t.join(3.0)
    assert not t.is_alive()
    assert feishu.ws_loop_thread() is None
    assert bridges.get_all() == []


def test_restart_after_stop_all_reuses_loop(fake_ws):
    bridges.start_binding(_binding("b1"))
    _wait_status("b1", BridgeStatus.CONNECTED)
    bridges.stop_all()
    assert feishu.ws_loop_thread() is None

    bridges.start_binding(_binding("b1"))
    _wait_status("b1", BridgeStatus.CONNECTED)
    assert feishu.ws_loop_thread().is_alive()


def test_connect_failure_marks_error(monkeypatch):
    async def failing_connect(self):
        raise lark_ws.ClientException(1, "bad credentials")

    monkeypatch.setattr(lark_ws.Client, "_connect", failing_connect)
    try:
        bridges.start_binding(_binding("bad"))
        _wait_status("bad", BridgeStatus.ERROR)
        assert "bad credentials" in bridges.get_status("bad").last_error
    finally:
        bridges.stop_all()
    assert feishu.ws_loop_thread() is None


def test_fetch_meta_populates_store_and_bridge_info(monkeypatch, tmp_path):
    """_fetch_meta 的结果写入 store.binding.meta 和 BridgeInfo.bot_name/tenant_name。"""
    import store as _store

    _store.init_db(tmp_path / "t.db")

    fake_meta = {
        "bot_name": "TestBot",
        "bot_open_id": "ou_abc",
        "avatar_url": "https://example.com/a.png",
        "tenant_name": "TestCorp",
        "tenant_key": "tk_123",
    }

    def fake_fetch_meta(self):
        return fake_meta

    async def fake_connect(self):
        pass

    async def fake_ping_loop(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(lark_ws.Client, "_connect", fake_connect)
    monkeypatch.setattr(lark_ws.Client, "_ping_loop", fake_ping_loop)
    monkeypatch.setattr(feishu.FeishuBridge, "_fetch_meta", fake_fetch_meta)

    # 创建真实 binding 记录
    agent = _store.create_agent("a1", "test agent", "you are test")
    binding = _store.create_binding(agent["id"], "feishu", {"app_id": "cli_x", "app_secret": "s"})

    b = _binding(binding["id"])
    b["agent_id"] = agent["id"]
    try:
        bridges.start_binding(b)
        _wait_status(binding["id"], BridgeStatus.CONNECTED)

        # 等一小会让 meta 写入完成（to_thread 在 CONNECTED 之后）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            info = bridges.get_status(binding["id"])
            if info and info.bot_name is not None:
                break
            time.sleep(0.02)

        # 检查 BridgeInfo
        info = bridges.get_status(binding["id"])
        assert info.bot_name == "TestBot"
        assert info.tenant_name == "TestCorp"

        # 检查 store
        stored = _store.get_binding(binding["id"])
        assert stored["meta"]["bot_name"] == "TestBot"
        assert stored["meta"]["tenant_key"] == "tk_123"
    finally:
        bridges.stop_all()
