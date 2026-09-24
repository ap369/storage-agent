import sys
from contextlib import AsyncExitStack
from pathlib import Path

from agent.tools.mcp import build_mcp_tools

FIXTURE_SERVER = str(Path(__file__).parent / "fixtures" / "dummy_mcp_server.py")


def make_stdio_config(name="dummy"):
    return {
        "name": name,
        "transport": "stdio",
        "command": sys.executable,
        "args": [FIXTURE_SERVER],
        "env": None,
    }


async def test_discovers_and_namespaces_tools_from_stdio_server():
    async with AsyncExitStack() as stack:
        tools = await build_mcp_tools([make_stdio_config()], stack)

        names = [t.name for t in tools]
        assert names == ["mcp_dummy_add"]


async def test_calls_discovered_tool_and_returns_text_result():
    async with AsyncExitStack() as stack:
        tools = await build_mcp_tools([make_stdio_config()], stack)
        add_tool = next(t for t in tools if t.name == "mcp_dummy_add")

        result = await add_tool.execute({"a": 2, "b": 3})

        assert "5" in result


async def test_misconfigured_server_is_skipped_without_blocking_others():
    bad_config = {
        "name": "broken",
        "transport": "stdio",
        "command": "this-command-does-not-exist",
        "args": [],
        "env": None,
    }
    good_config = make_stdio_config(name="dummy")

    async with AsyncExitStack() as stack:
        tools = await build_mcp_tools([bad_config, good_config], stack)

        names = [t.name for t in tools]
        assert names == ["mcp_dummy_add"]


async def test_unknown_transport_is_skipped():
    bad_config = {"name": "weird", "transport": "carrier_pigeon"}

    async with AsyncExitStack() as stack:
        tools = await build_mcp_tools([bad_config], stack)

        assert tools == []
