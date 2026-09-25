import json
from typing import Any, Awaitable, Callable

from agent.llm import LLMClient
from agent.registry import ToolRegistry

OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


class ToolTurnLimitExceeded(Exception):
    pass


async def run_conversation(
    client: LLMClient,
    registry: ToolRegistry,
    system_prompt: str,
    history: list[dict[str, Any]],
    max_turns: int = 20,
    on_event: OnEvent | None = None,
) -> str:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *history]

    for _ in range(max_turns):
        assistant = await client.complete(messages, registry.tool_specs)

        if not assistant.tool_calls:
            if on_event:
                await on_event({"type": "final", "content": assistant.content})
            return assistant.content or ""

        messages.append(
            {
                "role": "assistant",
                "content": assistant.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in assistant.tool_calls
                ],
            }
        )

        for tc in assistant.tool_calls:
            if on_event:
                await on_event({"type": "tool_call", "name": tc.name, "arguments": tc.arguments})

            tool = registry.by_name.get(tc.name)
            if tool is None:
                result = f"Error: unknown tool {tc.name!r}"
            else:
                result = await tool.execute(tc.arguments)

            if on_event:
                await on_event({"type": "tool_result", "name": tc.name, "result": result})

            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result}
            )

    raise ToolTurnLimitExceeded(f"exceeded max_turns={max_turns}")
