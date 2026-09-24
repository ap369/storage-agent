from dataclasses import dataclass
from typing import Any, Awaitable, Callable

ToolExecute = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecute


def guard_errors(fn: ToolExecute) -> ToolExecute:
    async def wrapped(args: dict[str, Any]) -> str:
        try:
            return await fn(args)
        except Exception as exc:  # a tool error must never crash the agent loop
            return f"Error: {exc}"

    return wrapped


def to_openai_spec(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
