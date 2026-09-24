from dataclasses import dataclass, field
from typing import Iterable

from agent.tools.base import Tool


class DuplicateToolName(Exception):
    pass


@dataclass
class ToolRegistry:
    tools: list[Tool] = field(default_factory=list)
    by_name: dict[str, Tool] = field(default_factory=dict)


def build_registry(*tool_groups: Iterable[Tool]) -> ToolRegistry:
    registry = ToolRegistry()
    for group in tool_groups:
        for tool in group:
            if tool.name in registry.by_name:
                raise DuplicateToolName(f"duplicate tool name: {tool.name!r}")
            registry.tools.append(tool)
            registry.by_name[tool.name] = tool
    return registry
