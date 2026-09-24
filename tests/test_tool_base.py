from agent.tools.base import Tool, to_openai_spec


async def _noop(args: dict) -> str:
    return "ok"


def test_to_openai_spec_wraps_tool_as_function_spec():
    tool = Tool(
        name="read_file",
        description="Read a file from the sandbox",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=_noop,
    )

    spec = to_openai_spec(tool)

    assert spec == {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the sandbox",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
