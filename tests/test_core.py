import pytest

from agent.core import ToolTurnLimitExceeded, run_conversation
from agent.llm import AssistantMessage, ToolCallRequest
from agent.registry import build_registry
from agent.tools.base import Tool


class FakeLLMClient:
    def __init__(self, responses: list[AssistantMessage]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def complete(self, messages, tools):
        self.calls.append(messages)
        return self._responses.pop(0)


def make_echo_tool(record: list):
    async def execute(args):
        record.append(args)
        return "echoed"

    return Tool(name="echo", description="echoes", parameters={"type": "object"}, execute=execute)


async def test_returns_final_content_when_no_tool_calls():
    client = FakeLLMClient([AssistantMessage(content="hi there", tool_calls=[])])
    registry = build_registry([])

    result = await run_conversation(client, registry, "system prompt", [{"role": "user", "content": "hello"}])

    assert result == "hi there"


async def test_executes_tool_call_and_feeds_result_back():
    calls_made: list = []
    tool = make_echo_tool(calls_made)
    registry = build_registry([tool])

    client = FakeLLMClient(
        [
            AssistantMessage(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="echo", arguments={"x": "y"})],
            ),
            AssistantMessage(content="done", tool_calls=[]),
        ]
    )

    result = await run_conversation(client, registry, "system prompt", [{"role": "user", "content": "go"}])

    assert result == "done"
    assert calls_made == [{"x": "y"}]

    second_call_messages = client.calls[1]
    tool_result_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_result_messages == [{"role": "tool", "tool_call_id": "call_1", "name": "echo", "content": "echoed"}]


async def test_unknown_tool_returns_error_without_crashing():
    registry = build_registry([])
    client = FakeLLMClient(
        [
            AssistantMessage(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="nonexistent", arguments={})],
            ),
            AssistantMessage(content="handled", tool_calls=[]),
        ]
    )

    result = await run_conversation(client, registry, "system prompt", [{"role": "user", "content": "go"}])

    assert result == "handled"
    tool_message = client.calls[1][-1]
    assert tool_message["role"] == "tool"
    assert "Error" in tool_message["content"]


async def test_raises_when_max_turns_exceeded():
    registry = build_registry([])
    looping_response = AssistantMessage(
        content=None,
        tool_calls=[ToolCallRequest(id="call_1", name="nonexistent", arguments={})],
    )
    client = FakeLLMClient([looping_response, looping_response])

    with pytest.raises(ToolTurnLimitExceeded):
        await run_conversation(
            client, registry, "system prompt", [{"role": "user", "content": "go"}], max_turns=2
        )


async def test_on_event_callback_receives_tool_and_final_events():
    events: list[dict] = []

    async def on_event(event):
        events.append(event)

    tool = make_echo_tool([])
    registry = build_registry([tool])
    client = FakeLLMClient(
        [
            AssistantMessage(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="echo", arguments={"x": 1})],
            ),
            AssistantMessage(content="done", tool_calls=[]),
        ]
    )

    await run_conversation(
        client, registry, "system prompt", [{"role": "user", "content": "go"}], on_event=on_event
    )

    event_types = [e["type"] for e in events]
    assert event_types == ["tool_call", "tool_result", "final"]
