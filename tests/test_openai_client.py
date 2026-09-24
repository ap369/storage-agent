from types import SimpleNamespace

from agent.openai_client import OpenAICompatibleClient


class FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.last_call_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        return self._response


def make_fake_raw_client(response):
    completions = FakeCompletions(response)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def make_response(content, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


async def test_complete_returns_content_with_no_tool_calls():
    response = make_response(content="hello", tool_calls=None)
    raw_client, _ = make_fake_raw_client(response)
    client = OpenAICompatibleClient(raw_client, model="gpt-4o-mini")

    result = await client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert result.content == "hello"
    assert result.tool_calls == []


async def test_complete_parses_tool_calls_and_json_arguments():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="read_file", arguments='{"path": "a.txt"}'),
    )
    response = make_response(content=None, tool_calls=[tool_call])
    raw_client, _ = make_fake_raw_client(response)
    client = OpenAICompatibleClient(raw_client, model="gpt-4o-mini")

    result = await client.complete(messages=[], tools=[])

    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "a.txt"}


async def test_complete_sends_model_and_messages():
    response = make_response(content="ok")
    raw_client, completions = make_fake_raw_client(response)
    client = OpenAICompatibleClient(raw_client, model="gpt-4o-mini")

    messages = [{"role": "user", "content": "hi"}]
    await client.complete(messages=messages, tools=[])

    assert completions.last_call_kwargs["model"] == "gpt-4o-mini"
    assert completions.last_call_kwargs["messages"] == messages


async def test_complete_omits_empty_tools_list():
    response = make_response(content="ok")
    raw_client, completions = make_fake_raw_client(response)
    client = OpenAICompatibleClient(raw_client, model="gpt-4o-mini")

    await client.complete(messages=[], tools=[])

    assert completions.last_call_kwargs["tools"] is None


async def test_complete_passes_non_empty_tools_list():
    response = make_response(content="ok")
    raw_client, completions = make_fake_raw_client(response)
    client = OpenAICompatibleClient(raw_client, model="gpt-4o-mini")

    tools = [{"type": "function", "function": {"name": "read_file"}}]
    await client.complete(messages=[], tools=tools)

    assert completions.last_call_kwargs["tools"] == tools
