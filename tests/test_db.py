import json

import pytest

from storage.db import (
    create_conversation,
    create_task,
    get_conversation_messages,
    get_task,
    init_db,
    insert_message,
    list_enabled_api_configs,
    seed_api_configs,
    update_task,
)


@pytest.fixture
async def db():
    conn = await init_db(":memory:")
    yield conn
    await conn.close()


async def test_init_db_creates_conversations_and_messages_tables(db):
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    rows = await cursor.fetchall()
    table_names = {row[0] for row in rows}
    assert {"conversations", "messages", "tasks", "api_configs"} <= table_names


async def test_create_conversation_returns_id_and_persists_source(db):
    conversation_id = await create_conversation(db, source="webview")

    cursor = await db.execute(
        "SELECT source FROM conversations WHERE id = ?", (conversation_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == "webview"


async def test_insert_and_retrieve_messages_preserves_order(db):
    conversation_id = await create_conversation(db, source="task")

    await insert_message(db, conversation_id, role="system", content="you are an agent")
    await insert_message(db, conversation_id, role="user", content="hello")
    await insert_message(db, conversation_id, role="assistant", content="hi there")

    messages = await get_conversation_messages(db, conversation_id)

    assert [m["role"] for m in messages] == ["system", "user", "assistant"]
    assert messages[1]["content"] == "hello"


async def test_get_conversation_messages_omits_none_fields(db):
    conversation_id = await create_conversation(db, source="webview")
    await insert_message(db, conversation_id, role="user", content="hello")

    messages = await get_conversation_messages(db, conversation_id)

    assert messages == [{"role": "user", "content": "hello"}]


async def test_create_task_defaults_to_pending(db):
    task_id = await create_task(db, input="do the thing")

    task = await get_task(db, task_id)

    assert task["status"] == "pending"
    assert task["input"] == "do the thing"
    assert task["result"] is None
    assert task["error"] is None


async def test_update_task_changes_status_and_result(db):
    task_id = await create_task(db, input="do the thing")

    await update_task(db, task_id, status="running", started_at="2026-01-01T00:00:00+00:00")
    running = await get_task(db, task_id)
    assert running["status"] == "running"
    assert running["started_at"] == "2026-01-01T00:00:00+00:00"

    await update_task(
        db, task_id, status="completed", result="done", finished_at="2026-01-01T00:01:00+00:00"
    )
    completed = await get_task(db, task_id)
    assert completed["status"] == "completed"
    assert completed["result"] == "done"
    assert completed["finished_at"] == "2026-01-01T00:01:00+00:00"


async def test_get_task_returns_none_for_unknown_id(db):
    assert await get_task(db, "nonexistent") is None


async def test_seed_api_configs_upserts_from_json_file(db, tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_KEY", "abc123")
    config_path = tmp_path / "api_allowlist.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "name": "weather_api",
                    "description": "Weather lookups",
                    "base_url": "https://weather.example.com",
                    "auth_type": "bearer",
                    "auth_value": "${WEATHER_KEY}",
                    "operations": [
                        {
                            "name": "get_forecast",
                            "method": "GET",
                            "path": "/forecast",
                            "description": "d",
                            "params_schema": {"type": "object"},
                        }
                    ],
                }
            ]
        )
    )

    await seed_api_configs(db, config_path)

    configs = await list_enabled_api_configs(db)
    assert len(configs) == 1
    assert configs[0]["name"] == "weather_api"
    assert configs[0]["auth_value"] == "abc123"
    assert configs[0]["operations"][0]["name"] == "get_forecast"


async def test_seed_api_configs_is_idempotent(db, tmp_path):
    config_path = tmp_path / "api_allowlist.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "name": "weather_api",
                    "description": "d",
                    "base_url": "https://x",
                    "auth_type": "none",
                    "operations": [],
                }
            ]
        )
    )

    await seed_api_configs(db, config_path)
    await seed_api_configs(db, config_path)

    configs = await list_enabled_api_configs(db)
    assert len(configs) == 1


async def test_list_enabled_api_configs_excludes_disabled(db, tmp_path):
    config_path = tmp_path / "api_allowlist.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "name": "a",
                    "description": "d",
                    "base_url": "https://x",
                    "auth_type": "none",
                    "operations": [],
                    "enabled": True,
                },
                {
                    "name": "b",
                    "description": "d",
                    "base_url": "https://y",
                    "auth_type": "none",
                    "operations": [],
                    "enabled": False,
                },
            ]
        )
    )

    await seed_api_configs(db, config_path)
    configs = await list_enabled_api_configs(db)

    assert [c["name"] for c in configs] == ["a"]


async def test_insert_message_round_trips_tool_call_fields(db):
    conversation_id = await create_conversation(db, source="webview")

    tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
    await insert_message(
        db, conversation_id, role="assistant", content=None, tool_calls=tool_calls
    )
    await insert_message(
        db,
        conversation_id,
        role="tool",
        content="file contents",
        tool_call_id="call_1",
        name="read_file",
    )

    messages = await get_conversation_messages(db, conversation_id)

    assert messages[0]["tool_calls"] == tool_calls
    assert messages[1]["tool_call_id"] == "call_1"
    assert messages[1]["name"] == "read_file"
