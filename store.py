"""SQLite 存储层。每次操作开新连接（WAL），线程安全；对外收发 dict，JSON 字段自动转换。"""

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DB_PATH = os.environ.get("TAGMATE_DB", "./data/tagmate.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    connections TEXT NOT NULL DEFAULT '[]',
    tools TEXT NOT NULL DEFAULT '[]',
    memory_policy TEXT NOT NULL DEFAULT 'session',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bindings (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    credentials TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS principals (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT,
    created_at REAL NOT NULL,
    UNIQUE (platform, user_id)
);
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    connection TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    expires_at REAL,
    refresh_expires_at REAL,
    scopes TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (principal_id, connection)
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    session_key TEXT NOT NULL UNIQUE,
    scene TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    thread_id TEXT,
    messages TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS invocations (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    scene TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    thread_id TEXT,
    input_text TEXT NOT NULL,
    output_text TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    invocation_id TEXT NOT NULL REFERENCES invocations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    principal_id TEXT,
    created_at REAL NOT NULL,
    UNIQUE (invocation_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_invocations_agent ON invocations(agent_id, started_at);
CREATE INDEX IF NOT EXISTS idx_invocations_session ON invocations(session_id, started_at);
"""

# 进出库自动 dumps/loads 的列
_JSON_COLS = {"connections", "tools", "credentials", "scopes", "messages", "payload", "meta"}

# append_event 的 seq 自增在 Python 侧做 read-then-write，用锁保证同进程内串行
_event_lock = threading.Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in _JSON_COLS & d.keys():
        d[k] = json.loads(d[k])
    return d


def _encode(fields: dict) -> dict:
    return {k: (json.dumps(v) if k in _JSON_COLS else v) for k, v in fields.items()}


def _insert(table: str, fields: dict) -> None:
    fields = _encode(fields)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with _connect() as conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(fields.values()))


def _update(table: str, id: str, fields: dict) -> None:
    if not fields:
        return
    fields = _encode(fields)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*fields.values(), id])


def _get(table: str, id: str) -> dict | None:
    with _connect() as conn:
        return _row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone())


def _query(sql: str, params: tuple = ()) -> list[dict]:
    with _connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def init_db(path: str | os.PathLike | None = None) -> None:
    """建目录 + 建表。传 path 时同时切换模块级 DB_PATH。"""
    global DB_PATH
    if path is not None:
        DB_PATH = str(path)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ---------- agents ----------

def create_agent(name: str, instructions: str = "", model: str = "",
                 connections: list | None = None, tools: list | None = None,
                 memory_policy: str = "session") -> dict:
    now = time.time()
    row = {
        "id": _new_id(), "name": name, "instructions": instructions, "model": model,
        "connections": connections or [], "tools": tools or [],
        "memory_policy": memory_policy, "created_at": now, "updated_at": now,
    }
    _insert("agents", row)
    return row


def get_agent(id: str) -> dict | None:
    return _get("agents", id)


def list_agents() -> list[dict]:
    return _query("SELECT * FROM agents ORDER BY created_at")


def update_agent(id: str, **fields) -> dict | None:
    fields["updated_at"] = time.time()
    _update("agents", id, fields)
    return get_agent(id)


def delete_agent(id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM agents WHERE id = ?", (id,))


# ---------- bindings ----------

def create_binding(agent_id: str, platform: str, credentials: dict, enabled: bool = True, meta: dict | None = None) -> dict:
    row = {
        "id": _new_id(), "agent_id": agent_id, "platform": platform,
        "credentials": credentials, "enabled": int(enabled),
        "meta": meta or {}, "created_at": time.time(),
    }
    _insert("bindings", row)
    return row


def get_binding(id: str) -> dict | None:
    return _get("bindings", id)


def list_bindings(agent_id: str | None = None) -> list[dict]:
    if agent_id is None:
        return _query("SELECT * FROM bindings ORDER BY created_at")
    return _query("SELECT * FROM bindings WHERE agent_id = ? ORDER BY created_at", (agent_id,))


def update_binding(id: str, **fields) -> dict | None:
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    _update("bindings", id, fields)
    return get_binding(id)


def delete_binding(id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM bindings WHERE id = ?", (id,))


# ---------- principals ----------

def get_or_create_principal(platform: str, user_id: str, display_name: str | None = None) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM principals WHERE platform = ? AND user_id = ?", (platform, user_id)
        ).fetchone()
        if row is not None:
            return _row_to_dict(row)
        new = {
            "id": _new_id(), "platform": platform, "user_id": user_id,
            "display_name": display_name, "created_at": time.time(),
        }
        conn.execute(
            "INSERT INTO principals (id, platform, user_id, display_name, created_at) "
            "VALUES (:id, :platform, :user_id, :display_name, :created_at)", new,
        )
        return new


def get_principal(id: str) -> dict | None:
    return _get("principals", id)


# ---------- credentials ----------

def upsert_credentials(principal_id: str, connection: str, access_token: str,
                       refresh_token: str | None = None, expires_at: float | None = None,
                       refresh_expires_at: float | None = None, scopes: list | None = None) -> dict:
    now = time.time()
    row = {
        "id": _new_id(), "principal_id": principal_id, "connection": connection,
        "access_token": access_token, "refresh_token": refresh_token,
        "expires_at": expires_at, "refresh_expires_at": refresh_expires_at,
        "scopes": scopes or [], "created_at": now, "updated_at": now,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO credentials (id, principal_id, connection, access_token, refresh_token, "
            "expires_at, refresh_expires_at, scopes, created_at, updated_at) "
            "VALUES (:id, :principal_id, :connection, :access_token, :refresh_token, "
            ":expires_at, :refresh_expires_at, :scopes, :created_at, :updated_at) "
            "ON CONFLICT (principal_id, connection) DO UPDATE SET "
            "access_token = excluded.access_token, refresh_token = excluded.refresh_token, "
            "expires_at = excluded.expires_at, refresh_expires_at = excluded.refresh_expires_at, "
            "scopes = excluded.scopes, updated_at = excluded.updated_at",
            _encode(row),
        )
    return get_credentials(principal_id, connection)


def get_credentials(principal_id: str, connection: str) -> dict | None:
    with _connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM credentials WHERE principal_id = ? AND connection = ?",
            (principal_id, connection),
        ).fetchone())


def delete_credentials(principal_id: str, connection: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM credentials WHERE principal_id = ? AND connection = ?",
                     (principal_id, connection))


# ---------- sessions ----------

def get_or_create_session(agent_id: str, session_key: str, scene: str, chat_id: str,
                          thread_id: str | None = None) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_key = ?", (session_key,)).fetchone()
        if row is not None:
            return _row_to_dict(row)
        now = time.time()
        new = {
            "id": _new_id(), "agent_id": agent_id, "session_key": session_key, "scene": scene,
            "chat_id": chat_id, "thread_id": thread_id, "messages": [],
            "created_at": now, "updated_at": now,
        }
        conn.execute(
            "INSERT INTO sessions (id, agent_id, session_key, scene, chat_id, thread_id, messages, "
            "created_at, updated_at) VALUES (:id, :agent_id, :session_key, :scene, :chat_id, "
            ":thread_id, :messages, :created_at, :updated_at)", _encode(new),
        )
        return new


def get_session(id: str) -> dict | None:
    return _get("sessions", id)


def list_sessions(agent_id: str | None = None, limit: int = 50) -> list[dict]:
    if agent_id is None:
        return _query("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,))
    return _query("SELECT * FROM sessions WHERE agent_id = ? ORDER BY updated_at DESC LIMIT ?",
                  (agent_id, limit))


def update_session_messages(id: str, messages: list) -> None:
    _update("sessions", id, {"messages": messages, "updated_at": time.time()})


# ---------- invocations ----------

def create_invocation(agent_id: str, binding_id: str, session_id: str, principal_id: str,
                      scene: str, chat_id: str, message_id: str, input_text: str,
                      thread_id: str | None = None) -> dict:
    row = {
        "id": _new_id(), "agent_id": agent_id, "binding_id": binding_id,
        "session_id": session_id, "principal_id": principal_id, "scene": scene,
        "chat_id": chat_id, "message_id": message_id, "thread_id": thread_id,
        "input_text": input_text, "output_text": None, "status": "running", "error": None,
        "started_at": time.time(), "finished_at": None,
    }
    _insert("invocations", row)
    return row


def finish_invocation(id: str, status: str, output_text: str | None = None,
                      error: str | None = None) -> dict | None:
    _update("invocations", id, {
        "status": status, "output_text": output_text, "error": error, "finished_at": time.time(),
    })
    return get_invocation(id)


def get_invocation(id: str) -> dict | None:
    return _get("invocations", id)


def list_invocations(agent_id: str | None = None, session_id: str | None = None,
                     limit: int = 50) -> list[dict]:
    where, params = [], []
    if agent_id is not None:
        where.append("agent_id = ?")
        params.append(agent_id)
    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    sql = "SELECT * FROM invocations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    return _query(sql, tuple(params))


# ---------- events ----------

def append_event(invocation_id: str, type: str, payload: dict,
                 principal_id: str | None = None) -> dict:
    with _event_lock, _connect() as conn:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE invocation_id = ?", (invocation_id,)
        ).fetchone()[0]
        row = {
            "id": _new_id(), "invocation_id": invocation_id, "seq": seq, "type": type,
            "payload": payload, "principal_id": principal_id, "created_at": time.time(),
        }
        conn.execute(
            "INSERT INTO events (id, invocation_id, seq, type, payload, principal_id, created_at) "
            "VALUES (:id, :invocation_id, :seq, :type, :payload, :principal_id, :created_at)",
            _encode(row),
        )
    return row


def list_events(invocation_id: str) -> list[dict]:
    return _query("SELECT * FROM events WHERE invocation_id = ? ORDER BY seq", (invocation_id,))
