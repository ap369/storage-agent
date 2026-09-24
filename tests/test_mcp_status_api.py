from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.mcp_status import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(API_TOKEN="secret-token")
    app.state.mcp_status = [
        {"name": "dummy", "transport": "stdio", "connected": True, "tools": ["mcp_dummy_add"]},
        {"name": "broken", "transport": "stdio", "connected": False, "tools": []},
    ]
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_rejects_missing_token(client):
    async with client as c:
        response = await c.get("/mcp/status")
    assert response.status_code == 401


async def test_rejects_wrong_token(client):
    async with client as c:
        response = await c.get("/mcp/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


async def test_returns_configured_server_statuses(client):
    async with client as c:
        response = await c.get(
            "/mcp/status", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 200
    assert response.json() == [
        {"name": "dummy", "transport": "stdio", "connected": True, "tools": ["mcp_dummy_add"]},
        {"name": "broken", "transport": "stdio", "connected": False, "tools": []},
    ]
