from mcp.server.mcpserver import MCPServer

mcp = MCPServer("dummy")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    mcp.run()
