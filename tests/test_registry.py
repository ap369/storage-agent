import pytest

from agent.registry import DuplicateToolName, build_registry
from agent.tools.base import Tool


async def _noop(args: dict) -> str:
    return "ok"


def make_tool(name: str) -> Tool:
    return Tool(name=name, description="d", parameters={"type": "object"}, execute=_noop)


def test_aggregates_tools_from_multiple_groups():
    registry = build_registry([make_tool("a"), make_tool("b")], [make_tool("c")])

    assert [t.name for t in registry.tools] == ["a", "b", "c"]
    assert registry.by_name["a"].name == "a"
    assert registry.by_name["c"].name == "c"


def test_raises_on_duplicate_tool_name():
    with pytest.raises(DuplicateToolName):
        build_registry([make_tool("a")], [make_tool("a")])


def test_tool_specs_precomputed_matching_tool_order():
    registry = build_registry([make_tool("a"), make_tool("b")])

    assert [spec["function"]["name"] for spec in registry.tool_specs] == ["a", "b"]
    assert registry.tool_specs[0] == {
        "type": "function",
        "function": {"name": "a", "description": "d", "parameters": {"type": "object"}},
    }
