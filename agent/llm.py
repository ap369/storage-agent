from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantMessage:
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)


class LLMClient(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantMessage: ...
