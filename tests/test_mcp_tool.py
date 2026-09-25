import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from agent.tools.mcp import build_mcp_tools, load_mcp_server_configs, summarize_connections

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


async def test_summarize_connections_reports_connected_and_failed_servers():
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
        summary = summarize_connections([bad_config, good_config], tools)

    assert summary == [
        {"name": "broken", "transport": "stdio", "connected": False, "tools": []},
        {"name": "dummy", "transport": "stdio", "connected": True, "tools": ["mcp_dummy_add"]},
    ]


def test_load_mcp_server_configs_interpolates_env_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("REMOTE_MCP_TOKEN", "xyz789")
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "name": "local_tools",
                    "transport": "stdio",
                    "command": "python",
                    "args": ["server.py"],
                    "env": {"FOO": "bar"},
                },
                {
                    "name": "remote_tools",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com",
                    "headers": {"Authorization": "Bearer ${REMOTE_MCP_TOKEN}"},
                },
            ]
        )
    )

    configs = load_mcp_server_configs(config_path)

    assert len(configs) == 2

    local = next(c for c in configs if c["name"] == "local_tools")
    assert local["transport"] == "stdio"
    assert local["command"] == "python"
    assert local["args"] == ["server.py"]
    assert local["env"] == {"FOO": "bar"}

    remote = next(c for c in configs if c["name"] == "remote_tools")
    assert remote["transport"] == "streamable_http"
    assert remote["url"] == "https://mcp.example.com"
    assert remote["headers"] == {"Authorization": "Bearer xyz789"}


def test_load_mcp_server_configs_excludes_disabled(tmp_path):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(
        json.dumps(
            [
                {"name": "a", "transport": "stdio", "command": "python", "args": [], "enabled": True},
                {"name": "b", "transport": "stdio", "command": "python", "args": [], "enabled": False},
            ]
        )
    )

    configs = load_mcp_server_configs(config_path)

    assert [c["name"] for c in configs] == ["a"]
