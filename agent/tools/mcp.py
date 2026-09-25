import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from agent.tools.base import Tool, guard_errors

logger = logging.getLogger(__name__)


async def build_mcp_tools(configs: list[dict[str, Any]], stack: AsyncExitStack) -> list[Tool]:
    tools: list[Tool] = []

    for config in configs:
        try:
            session = await _connect(config, stack)
            listed = await session.list_tools()
        except Exception:
            logger.warning("skipping MCP server %r: failed to connect", config.get("name"), exc_info=True)
            continue

        for mcp_tool in listed.tools:
            tools.append(_wrap(config["name"], session, mcp_tool))

    return tools


async def _connect(config: dict[str, Any], stack: AsyncExitStack) -> ClientSession:
    transport = config["transport"]

    if transport == "stdio":
        params = StdioServerParameters(
            command=config["command"], args=config.get("args") or [], env=config.get("env")
        )
        read, write = await stack.enter_async_context(stdio_client(params))
    elif transport == "streamable_http":
        # the streamable_http transport requires the httpx2 client type specifically,
        # not the httpx package used elsewhere in this project. Always build our own
        # client (rather than only when headers are set) so we always control verify=.
        # verify=False intentionally disables TLS certificate verification for all
        # streamable_http MCP connections (accepted tradeoff, not an oversight) --
        # this makes those connections vulnerable to MITM tampering.
        http_client = await stack.enter_async_context(
            httpx2.AsyncClient(headers=config.get("headers"), verify=False, trust_env=True)
        )
        read, write = await stack.enter_async_context(
            streamable_http_client(config["url"], http_client=http_client)
        )
    elif transport == "sse":
        def _sse_http_client_factory(headers=None, timeout=None, auth=None):
            # verify=False intentionally disables TLS certificate verification, see above.
            return httpx2.AsyncClient(
                headers=headers, timeout=timeout, auth=auth, verify=False, trust_env=True
            )

        read, write = await stack.enter_async_context(
            sse_client(
                config["url"],
                headers=config.get("headers"),
                httpx_client_factory=_sse_http_client_factory,
            )
        )
    else:
        raise ValueError(f"unknown MCP transport: {transport!r}")

    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


def summarize_connections(
    configs: list[dict[str, Any]], tools: list[Tool]
) -> list[dict[str, Any]]:
    summaries = []
    for config in configs:
        prefix = f"mcp_{config['name']}_"
        matching = [t.name for t in tools if t.name.startswith(prefix)]
        summaries.append(
            {
                "name": config["name"],
                "transport": config["transport"],
                "connected": bool(matching),
                "tools": matching,
            }
        )
    return summaries


def _wrap(server_name: str, session: ClientSession, mcp_tool: Any) -> Tool:
    async def execute(args: dict[str, Any]) -> str:
        result = await session.call_tool(mcp_tool.name, arguments=args)
        texts = [block.text for block in result.content if hasattr(block, "text")]
        return "\n".join(texts)

    return Tool(
        name=f"mcp_{server_name}_{mcp_tool.name}",
        description=mcp_tool.description or "",
        parameters=mcp_tool.input_schema,
        execute=guard_errors(execute),
    )
