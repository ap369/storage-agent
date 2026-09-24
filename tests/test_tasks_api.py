import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent.llm import AssistantMessage
from agent.registry import build_registry
from api.tasks import router
from storage.db import init_db


class FakeLLMClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def complete(self, messages, tools):
        return self._responses.pop(0)


@pytest.fixture
async def app_and_client():
    db = await init_db(":memory:")
    app = FastAPI()
    app.include_router(router)
    app.state.db = db
    app.state.registry = build_registry([])
    app.state.system_prompt = "you are a test agent"
    app.state.settings = SimpleNamespace(API_TOKEN="secret-token", MAX_TOOL_TURNS=5)
    app.state.llm_client = FakeLLMClient([AssistantMessage(content="task done", tool_calls=[])])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await db.close()


async def test_post_tasks_rejects_missing_token(app_and_client):
    response = await app_and_client.post("/tasks", json={"input": "do something"})
    assert response.status_code == 401


async def test_post_tasks_rejects_wrong_token(app_and_client):
    response = await app_and_client.post(
        "/tasks",
        json={"input": "do something"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


async def test_post_tasks_returns_202_with_pending_task_id(app_and_client):
    response = await app_and_client.post(
        "/tasks",
        json={"input": "do something"},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["task_id"]


async def test_get_tasks_rejects_missing_token(app_and_client):
    response = await app_and_client.get("/tasks/some-id")
    assert response.status_code == 401


async def test_get_unknown_task_returns_404(app_and_client):
    response = await app_and_client.get(
        "/tasks/nonexistent", headers={"Authorization": "Bearer secret-token"}
    )
    assert response.status_code == 404


async def test_task_completes_and_is_pollable(app_and_client):
    create_response = await app_and_client.post(
        "/tasks",
        json={"input": "do something"},
        headers={"Authorization": "Bearer secret-token"},
    )
    task_id = create_response.json()["task_id"]

    for _ in range(50):
        status_response = await app_and_client.get(
            f"/tasks/{task_id}", headers={"Authorization": "Bearer secret-token"}
        )
        body = status_response.json()
        if body["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("task did not complete in time")

    assert body["result"] == "task done"
    assert body["input"] == "do something"
    assert body["started_at"] is not None
    assert body["finished_at"] is not None
