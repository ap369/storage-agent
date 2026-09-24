import json
from typing import Any

from openai import AsyncOpenAI

from agent.llm import AssistantMessage, ToolCallRequest


class OpenAICompatibleClient:
    def __init__(self, raw_client: AsyncOpenAI, model: str):
        self._raw_client = raw_client
        self._model = model

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantMessage:
        response = await self._raw_client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools or None,
        )
        message = response.choices[0].message
        tool_calls = [
            ToolCallRequest(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments),
            )
            for tc in (message.tool_calls or [])
        ]
        return AssistantMessage(content=message.content, tool_calls=tool_calls)


def build_openai_client(base_url: str, api_key: str, model: str) -> OpenAICompatibleClient:
    raw_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return OpenAICompatibleClient(raw_client, model=model)
