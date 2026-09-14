"""管理面 API：agent CRUD、binding secret 打码 / *** 保留原值、delete agent 级联。不真连飞书。"""

import pytest
from fastapi.testclient import TestClient

import bridges
import store
from main import app


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.init_db(tmp_path / "t.db")
    yield


@pytest.fixture
def client():
    # 不进 lifespan：不触发 start_all_enabled
    return TestClient(app)


@pytest.fixture
def fake_bridges(monkeypatch):
    """bridges.start_binding / stop_binding 换成记录调用的假实现。"""
    started: list[str] = []
    stopped: list[str] = []

    def fake_start(binding):
        started.append(binding["id"])
        return bridges.BridgeInfo(name=binding["id"], type=binding["platform"],
                                  binding_id=binding["id"], agent_id=binding["agent_id"])

    def fake_stop(binding_id):
        stopped.append(binding_id)
        return True

    monkeypatch.setattr(bridges, "start_binding", fake_start)
    monkeypatch.setattr(bridges, "stop_binding", fake_stop)
    return started, stopped


def _create_agent(client, **kw):
    body = {"name": "bot", **kw}
    r = client.post("/api/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _create_binding(client, agent_id, enabled=False, **creds):
    body = {"agent_id": agent_id, "platform": "feishu", "enabled": enabled,
            "credentials": {"app_id": "cli_1", "app_secret": "s3cret", **creds}}
    r = client.post("/api/bindings", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_agent_crud(client):
    a = _create_agent(client, instructions="hi", tools=["current_time"])
    assert a["tools"] == ["current_time"]
    assert a["memory_policy"] == "session"

    assert client.get(f"/api/agents/{a['id']}").json()["name"] == "bot"
    assert [x["id"] for x in client.get("/api/agents").json()] == [a["id"]]

    r = client.put(f"/api/agents/{a['id']}", json={"name": "bot2"})
    assert r.status_code == 200
    assert r.json()["name"] == "bot2"
    assert r.json()["instructions"] == "hi"  # 没给的字段不动

    assert client.delete(f"/api/agents/{a['id']}").status_code == 204
    assert client.get(f"/api/agents/{a['id']}").status_code == 404
    assert client.get("/api/agents").json() == []


def test_agent_404(client):
    assert client.get("/api/agents/nope").status_code == 404
    assert client.put("/api/agents/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/agents/nope").status_code == 404


def test_binding_masks_secrets(client, fake_bridges):
    a = _create_agent(client)
    b = _create_binding(client, a["id"], access_token="tok")
    assert b["credentials"] == {"app_id": "cli_1", "app_secret": "***", "access_token": "***"}
    assert b["status"] == "disconnected"
    assert b["enabled"] == 0
    assert b["meta"] == {}

    lst = client.get("/api/bindings", params={"agent_id": a["id"]}).json()
    assert len(lst) == 1
    assert lst[0]["credentials"]["app_secret"] == "***"
    assert lst[0]["meta"] == {}
    detail = client.get(f"/api/bindings/{b['id']}").json()
    assert detail["credentials"]["app_secret"] == "***"
    assert detail["meta"] == {}
    # 库里仍是明文
    assert store.get_binding(b["id"])["credentials"]["app_secret"] == "s3cret"


def test_binding_create_enabled_starts(client, fake_bridges):
    started, _ = fake_bridges
    a = _create_agent(client)
    b = _create_binding(client, a["id"], enabled=True)
    assert started == [b["id"]]
    assert b["status"] == "disconnected"  # fake start 不改状态


def test_binding_create_start_error_in_response(client, monkeypatch):
    def boom(binding):
        raise RuntimeError("no network")

    monkeypatch.setattr(bridges, "start_binding", boom)
    a = _create_agent(client)
    b = _create_binding(client, a["id"], enabled=True)
    assert b["status"] == "error"
    assert "no network" in b["last_error"]


def test_binding_put_keeps_masked_secret(client, fake_bridges):
    started, stopped = fake_bridges
    a = _create_agent(client)
    b = _create_binding(client, a["id"])

    r = client.put(f"/api/bindings/{b['id']}",
                   json={"credentials": {"app_id": "cli_2", "app_secret": "***"}, "enabled": True})
    assert r.status_code == 200
    assert r.json()["credentials"] == {"app_id": "cli_2", "app_secret": "***"}
    assert r.json()["enabled"] == 1
    raw = store.get_binding(b["id"])["credentials"]
    assert raw == {"app_id": "cli_2", "app_secret": "s3cret"}
    # enabled -> stop + start
    assert stopped == [b["id"]]
    assert started == [b["id"]]

    # 换新 secret 生效；enabled=false 只 stop
    r = client.put(f"/api/bindings/{b['id']}",
                   json={"credentials": {"app_id": "cli_2", "app_secret": "new"}, "enabled": False})
    assert store.get_binding(b["id"])["credentials"]["app_secret"] == "new"
    assert r.json()["enabled"] == 0
    assert stopped == [b["id"], b["id"]]
    assert started == [b["id"]]


def test_binding_start_stop_delete(client, fake_bridges):
    started, stopped = fake_bridges
    a = _create_agent(client)
    b = _create_binding(client, a["id"])

    r = client.post(f"/api/bindings/{b['id']}/start")
    assert r.status_code == 200 and started == [b["id"]]
    r = client.post(f"/api/bindings/{b['id']}/stop")
    assert r.status_code == 200 and stopped == [b["id"]]

    assert client.delete(f"/api/bindings/{b['id']}").status_code == 204
    assert stopped == [b["id"], b["id"]]
    assert client.get(f"/api/bindings/{b['id']}").status_code == 404
    assert client.post("/api/bindings/nope/start").status_code == 404


def test_binding_requires_existing_agent(client):
    r = client.post("/api/bindings", json={"agent_id": "nope", "platform": "feishu",
                                           "credentials": {}, "enabled": False})
    assert r.status_code == 404


def test_delete_agent_cascades_bindings(client, fake_bridges):
    _, stopped = fake_bridges
    a = _create_agent(client)
    b1 = _create_binding(client, a["id"])
    b2 = _create_binding(client, a["id"])

    assert client.delete(f"/api/agents/{a['id']}").status_code == 204
    assert sorted(stopped) == sorted([b1["id"], b2["id"]])
    assert client.get("/api/bindings").json() == []
    assert store.list_bindings() == []


def test_sessions_and_invocations(client):
    a = _create_agent(client)
    s = store.get_or_create_session(a["id"], "k1", "dm", "chat1")
    store.update_session_messages(s["id"], [{"role": "user", "content": [{"text": "hi"}]}])
    p = store.get_or_create_principal("feishu", "ou_1", "Alice")
    inv = store.create_invocation(a["id"], "b1", s["id"], p["id"], "dm", "chat1", "m1", "hi")
    store.append_event(inv["id"], "assistant", {"text": "hello"})
    store.finish_invocation(inv["id"], "completed", output_text="hello")

    lst = client.get("/api/sessions", params={"agent_id": a["id"]}).json()
    assert len(lst) == 1
    assert "messages" not in lst[0]
    assert lst[0]["message_count"] == 1
    assert len(client.get(f"/api/sessions/{s['id']}").json()["messages"]) == 1

    invs = client.get("/api/invocations", params={"session_id": s["id"]}).json()
    assert [i["id"] for i in invs] == [inv["id"]]
    detail = client.get(f"/api/invocations/{inv['id']}").json()
    assert detail["status"] == "completed"
    assert [e["type"] for e in detail["events"]] == ["assistant"]
    assert detail["principal"]["display_name"] == "Alice"
    assert client.get("/api/invocations/nope").status_code == 404


def test_tools_models_status(client):
    tools = client.get("/api/tools").json()
    assert {"name": "current_time", "requires_user_credentials": False} in tools
    models = client.get("/api/models").json()
    assert models[0]["id"].startswith("us.anthropic.claude-sonnet")
    assert client.get("/api/status").json()["status"] == "ok"
