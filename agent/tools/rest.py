from contextlib import AsyncExitStack
from typing import Any

import httpx

from agent.tools.base import Tool, guard_errors


def _auth_headers(config: dict[str, Any]) -> dict[str, str]:
    auth_type = config["auth_type"]
    if auth_type == "none":
        return {}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {config['auth_value']}"}
    if auth_type == "api_key_header":
        return {config["auth_header_name"]: config["auth_value"]}
    if auth_type == "basic":
        return {"Authorization": f"Basic {config['auth_value']}"}
    raise ValueError(f"unknown auth_type: {auth_type!r}")


async def build_rest_tools(configs: list[dict[str, Any]], stack: AsyncExitStack) -> list[Tool]:
    tools: list[Tool] = []

    for config in configs:
        # one pooled client per API, reused across all of its operations/calls,
        # instead of opening a fresh connection (and TLS handshake) per tool call
        client = await stack.enter_async_context(
            httpx.AsyncClient(base_url=config["base_url"], headers=_auth_headers(config))
        )
        for operation in config["operations"]:
            tools.append(_build_operation_tool(config, operation, client))

    return tools


def _build_operation_tool(
    config: dict[str, Any], operation: dict[str, Any], client: httpx.AsyncClient
) -> Tool:
    async def call(args: dict[str, Any]) -> str:
        path = operation["path"]
        remaining = dict(args)
        for key in list(remaining):
            placeholder = "{" + key + "}"
            if placeholder in path:
                path = path.replace(placeholder, str(remaining.pop(key)))

        method = operation["method"].upper()

        if method in ("GET", "DELETE"):
            response = await client.request(method, path, params=remaining)
        else:
            response = await client.request(method, path, json=remaining)

        response.raise_for_status()
        return response.text

    return Tool(
        name=f"{config['name']}_{operation['name']}",
        description=operation["description"],
        parameters=operation["params_schema"],
        execute=guard_errors(call),
    )
