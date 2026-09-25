import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    content         TEXT,
    tool_calls      TEXT,
    tool_call_id    TEXT,
    name            TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    input           TEXT NOT NULL,
    result          TEXT,
    error           TEXT,
    conversation_id TEXT REFERENCES conversations(id),
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS api_configs (
    name             TEXT PRIMARY KEY,
    description      TEXT NOT NULL,
    base_url         TEXT NOT NULL,
    auth_type        TEXT NOT NULL,
    auth_value       TEXT,
    auth_header_name TEXT,
    operations       TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL
);

"""

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env(value: str | None) -> str | None:
    if value is None:
        return None
    return _ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    if path != ":memory:":
        await conn.execute("PRAGMA journal_mode=WAL")
    await conn.executescript(SCHEMA)
    await conn.commit()
    return conn


async def create_conversation(db: aiosqlite.Connection, source: str) -> str:
    conversation_id = str(uuid.uuid4())
    now = now_iso()
    await db.execute(
        "INSERT INTO conversations (id, source, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conversation_id, source, now, now),
    )
    await db.commit()
    return conversation_id


async def insert_message(
    db: aiosqlite.Connection,
    conversation_id: str,
    role: str,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    name: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO messages
            (conversation_id, role, content, tool_calls, tool_call_id, name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            role,
            content,
            json.dumps(tool_calls) if tool_calls is not None else None,
            tool_call_id,
            name,
            now_iso(),
        ),
    )
    await db.commit()


async def get_conversation_messages(
    db: aiosqlite.Connection, conversation_id: str
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT role, content, tool_calls, tool_call_id, name
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_message(row) for row in rows]


async def create_task(
    db: aiosqlite.Connection, input: str, conversation_id: str | None = None
) -> str:
    task_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO tasks (id, status, input, conversation_id, created_at)
        VALUES (?, 'pending', ?, ?, ?)
        """,
        (task_id, input, conversation_id, now_iso()),
    )
    await db.commit()
    return task_id


async def update_task(db: aiosqlite.Connection, task_id: str, **fields: Any) -> None:
    columns = ", ".join(f"{column} = ?" for column in fields)
    await db.execute(
        f"UPDATE tasks SET {columns} WHERE id = ?", (*fields.values(), task_id)
    )
    await db.commit()


async def get_task(db: aiosqlite.Connection, task_id: str) -> dict[str, Any] | None:
    cursor = await db.execute(
        """
        SELECT id, status, input, result, error, conversation_id, created_at, started_at, finished_at
        FROM tasks WHERE id = ?
        """,
        (task_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def seed_api_configs(db: aiosqlite.Connection, path: Path) -> None:
    configs = json.loads(Path(path).read_text())
    for config in configs:
        await db.execute(
            """
            INSERT INTO api_configs
                (name, description, base_url, auth_type, auth_value, auth_header_name,
                 operations, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                base_url=excluded.base_url,
                auth_type=excluded.auth_type,
                auth_value=excluded.auth_value,
                auth_header_name=excluded.auth_header_name,
                operations=excluded.operations,
                enabled=excluded.enabled
            """,
            (
                config["name"],
                config["description"],
                config["base_url"],
                config["auth_type"],
                _interpolate_env(config.get("auth_value")),
                config.get("auth_header_name"),
                json.dumps(config["operations"]),
                int(config.get("enabled", True)),
                now_iso(),
            ),
        )
    await db.commit()


async def list_enabled_api_configs(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT name, description, base_url, auth_type, auth_value, auth_header_name, operations
        FROM api_configs WHERE enabled = 1
        """
    )
    rows = await cursor.fetchall()
    return [
        {
            "name": row["name"],
            "description": row["description"],
            "base_url": row["base_url"],
            "auth_type": row["auth_type"],
            "auth_value": row["auth_value"],
            "auth_header_name": row["auth_header_name"],
            "operations": json.loads(row["operations"]),
        }
        for row in rows
    ]


def _row_to_message(row: aiosqlite.Row) -> dict[str, Any]:
    message: dict[str, Any] = {"role": row["role"]}
    if row["content"] is not None:
        message["content"] = row["content"]
    if row["tool_calls"]:
        message["tool_calls"] = json.loads(row["tool_calls"])
    if row["tool_call_id"] is not None:
        message["tool_call_id"] = row["tool_call_id"]
    if row["name"] is not None:
        message["name"] = row["name"]
    return message
