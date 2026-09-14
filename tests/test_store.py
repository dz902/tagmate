import sqlite3

import pytest

import store


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.init_db(tmp_path / "sub" / "t.db")
    yield


def test_agent_crud():
    a = store.create_agent("bot", instructions="hi", model="m", connections=["feishu"], tools=["t1"])
    assert len(a["id"]) == 12
    got = store.get_agent(a["id"])
    assert got["connections"] == ["feishu"]
    assert got["tools"] == ["t1"]
    assert got["memory_policy"] == "session"
    assert [x["id"] for x in store.list_agents()] == [a["id"]]

    upd = store.update_agent(a["id"], name="bot2", tools=["t1", "t2"])
    assert upd["name"] == "bot2"
    assert upd["tools"] == ["t1", "t2"]
    assert upd["updated_at"] >= a["updated_at"]

    store.delete_agent(a["id"])
    assert store.get_agent(a["id"]) is None
    assert store.list_agents() == []


def test_binding_cascade_delete():
    a = store.create_agent("bot")
    b = store.create_binding(a["id"], "feishu", {"app_id": "x", "app_secret": "y"})
    got = store.get_binding(b["id"])
    assert got["credentials"] == {"app_id": "x", "app_secret": "y"}
    assert got["enabled"] == 1
    assert store.list_bindings(a["id"]) == [got]
    assert store.update_binding(b["id"], enabled=False)["enabled"] == 0

    store.delete_agent(a["id"])
    assert store.get_binding(b["id"]) is None
    assert store.list_bindings() == []


def test_binding_meta_roundtrip():
    a = store.create_agent("bot")
    meta = {"bot_name": "TestBot", "tenant_key": "tk_123"}
    b = store.create_binding(a["id"], "feishu", {}, meta=meta)
    got = store.get_binding(b["id"])
    assert got["meta"] == meta

    # update meta
    updated = store.update_binding(b["id"], meta={"bot_name": "NewBot"})
    assert updated["meta"] == {"bot_name": "NewBot"}

    # default meta is {}
    b2 = store.create_binding(a["id"], "feishu", {})
    assert store.get_binding(b2["id"])["meta"] == {}


def test_binding_requires_agent():
    with pytest.raises(sqlite3.IntegrityError):
        store.create_binding("nope", "feishu", {})


def test_principal_get_or_create_idempotent():
    p1 = store.get_or_create_principal("feishu", "ou_1", display_name="A")
    p2 = store.get_or_create_principal("feishu", "ou_1", display_name="B")
    assert p1["id"] == p2["id"]
    assert p2["display_name"] == "A"
    p3 = store.get_or_create_principal("slack", "ou_1")
    assert p3["id"] != p1["id"]


def test_credentials_upsert_overwrites():
    p = store.get_or_create_principal("feishu", "ou_1")
    c1 = store.upsert_credentials(p["id"], "feishu", "tok1", refresh_token="r1",
                                  expires_at=1.0, scopes=["a"])
    c2 = store.upsert_credentials(p["id"], "feishu", "tok2", refresh_token="r2",
                                  expires_at=2.0, scopes=["a", "b"])
    assert c1["id"] == c2["id"]
    assert c2["access_token"] == "tok2"
    assert c2["scopes"] == ["a", "b"]
    assert c2["created_at"] == c1["created_at"]
    assert c2["updated_at"] >= c1["updated_at"]
    assert store.get_credentials(p["id"], "feishu") == c2
    assert store.get_credentials(p["id"], "other") is None

    store.delete_credentials(p["id"], "feishu")
    assert store.get_credentials(p["id"], "feishu") is None


def test_session_get_or_create_and_messages():
    a = store.create_agent("bot")
    s1 = store.get_or_create_session(a["id"], "k1", "dm", "chat1")
    s2 = store.get_or_create_session(a["id"], "k1", "dm", "chat1")
    assert s1["id"] == s2["id"]
    assert s2["messages"] == []

    msgs = [{"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"text": "hello"}]}]
    store.update_session_messages(s1["id"], msgs)
    got = store.get_session(s1["id"])
    assert isinstance(got["messages"], list)
    assert got["messages"] == msgs

    store.get_or_create_session(a["id"], "k2", "thread", "chat1", thread_id="th")
    assert len(store.list_sessions(a["id"])) == 2
    assert len(store.list_sessions(a["id"], limit=1)) == 1


def test_invocation_and_events():
    a = store.create_agent("bot")
    b = store.create_binding(a["id"], "feishu", {})
    p = store.get_or_create_principal("feishu", "ou_1")
    s = store.get_or_create_session(a["id"], "k", "dm", "c")
    inv = store.create_invocation(a["id"], b["id"], s["id"], p["id"], "dm", "c", "m1", "question")
    assert inv["status"] == "running"
    assert store.get_invocation(inv["id"])["finished_at"] is None

    e1 = store.append_event(inv["id"], "tool_call", {"name": "x"}, principal_id=p["id"])
    e2 = store.append_event(inv["id"], "tool_result", {"ok": True})
    e3 = store.append_event(inv["id"], "assistant", {"text": "done"})
    assert [e1["seq"], e2["seq"], e3["seq"]] == [1, 2, 3]
    evs = store.list_events(inv["id"])
    assert [e["seq"] for e in evs] == [1, 2, 3]
    assert evs[0]["payload"] == {"name": "x"}
    assert evs[0]["principal_id"] == p["id"]
    assert evs[1]["principal_id"] is None

    done = store.finish_invocation(inv["id"], "completed", output_text="answer")
    assert done["status"] == "completed"
    assert done["output_text"] == "answer"
    assert done["finished_at"] is not None

    assert [i["id"] for i in store.list_invocations(agent_id=a["id"])] == [inv["id"]]
    assert [i["id"] for i in store.list_invocations(session_id=s["id"])] == [inv["id"]]
    assert store.list_invocations(agent_id="other") == []

    failed = store.create_invocation(a["id"], b["id"], s["id"], p["id"], "dm", "c", "m2", "q2")
    store.finish_invocation(failed["id"], "failed", error="boom")
    assert store.get_invocation(failed["id"])["error"] == "boom"
    assert len(store.list_invocations()) == 2
