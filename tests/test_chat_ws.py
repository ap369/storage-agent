from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent.llm import AssistantMessage, ToolCallRequest
from agent.registry import build_registry
from agent.tools.base import Tool
from api.chat import router
from storage.db import get_conversation_messages, init_db


class FakeLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def complete(self, messages, tools):
        return self._responses.pop(0)


def make_echo_tool():
    async def execute(args):
        return f"echoed:{args.get('x')}"

    return Tool(name="echo", description="d", parameters={"type": "object"}, execute=execute)


@pytest.fixture
async def app_and_db():
    db = await init_db(":memory:")
    app = FastAPI()
    app.include_router(router)
    app.state.db = db
    app.state.registry = build_registry([make_echo_tool()])
    app.state.system_prompt = "you are a test agent"
    app.state.settings = SimpleNamespace(API_TOKEN="secret-token", MAX_TOOL_TURNS=5)
    yield app, db
    await db.close()


async def test_rejects_wrong_token(app_and_db):
    app, _ = app_and_db
    app.state.llm_client = FakeLLMClient([])
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"token": "wrong"})
        message = ws.receive_json()
        assert message == {"type": "error", "message": "unauthorized"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


async def test_authorized_chat_round_trip_persists_and_replies(app_and_db):
    app, db = app_and_db
    app.state.llm_client = FakeLLMClient([AssistantMessage(content="hi back", tool_calls=[])])
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"token": "secret-token"})
        ws.send_json({"type": "message", "conversation_id": None, "content": "hello"})

        final = ws.receive_json()
        assert final["type"] == "final"
        assert final["content"] == "hi back"
        conversation_id = final["conversation_id"]
        assert conversation_id

    messages = await get_conversation_messages(db, conversation_id)
    roles_and_content = [(m["role"], m["content"]) for m in messages]
    assert roles_and_content == [("user", "hello"), ("assistant", "hi back")]


async def test_llm_failure_sends_error_event_without_crashing_connection(app_and_db):
    app, _ = app_and_db

    class FailingLLMClient:
        async def complete(self, messages, tools):
            raise RuntimeError("Connection error.")

    app.state.llm_client = FailingLLMClient()
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"token": "secret-token"})
        ws.send_json({"type": "message", "conversation_id": None, "content": "hello"})

        error_event = ws.receive_json()
        assert error_event == {"type": "error", "message": "Connection error."}

        # the connection must still be usable for a follow-up message
        ws.send_json({"type": "message", "conversation_id": None, "content": "hello again"})
        second_error = ws.receive_json()
        assert second_error["type"] == "error"


async def test_streams_tool_call_and_tool_result_events(app_and_db):
    app, _ = app_and_db
    app.state.llm_client = FakeLLMClient(
        [
            AssistantMessage(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="echo", arguments={"x": "y"})],
            ),
            AssistantMessage(content="done", tool_calls=[]),
        ]
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"token": "secret-token"})
        ws.send_json({"type": "message", "conversation_id": None, "content": "go"})

        tool_call_event = ws.receive_json()
        tool_result_event = ws.receive_json()
        final_event = ws.receive_json()

    assert tool_call_event == {"type": "tool_call", "name": "echo", "arguments": {"x": "y"}}
    assert tool_result_event == {"type": "tool_result", "name": "echo", "result": "echoed:y"}
    assert final_event["type"] == "final"
    assert final_event["content"] == "done"
