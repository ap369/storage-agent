import asyncio

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request

from agent.core import run_conversation
from api.schemas import TaskCreateRequest, TaskCreateResponse, TaskStatusResponse
from auth import parse_bearer_token, verify_token
from storage.db import (
    create_conversation,
    create_task,
    get_conversation_messages,
    get_task,
    insert_message,
    now_iso,
    update_task,
)

router = APIRouter()


def _require_auth(request: Request, authorization: str | None) -> None:
    settings = request.app.state.settings
    if not verify_token(parse_bearer_token(authorization), settings.API_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/tasks", status_code=202, response_model=TaskCreateResponse)
async def create_task_endpoint(
    body: TaskCreateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> TaskCreateResponse:
    _require_auth(request, authorization)

    db = request.app.state.db
    conversation_id = await create_conversation(db, source="task")
    task_id = await create_task(db, input=body.input, conversation_id=conversation_id)

    asyncio.create_task(_run_task(request.app, task_id, conversation_id, body.input))

    return TaskCreateResponse(task_id=task_id, status="pending")


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_endpoint(
    task_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> TaskStatusResponse:
    _require_auth(request, authorization)

    task = await get_task(request.app.state.db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="not found")

    return TaskStatusResponse(
        task_id=task["id"],
        status=task["status"],
        input=task["input"],
        result=task["result"],
        error=task["error"],
        created_at=task["created_at"],
        started_at=task["started_at"],
        finished_at=task["finished_at"],
    )


async def _run_task(app: FastAPI, task_id: str, conversation_id: str, input_text: str) -> None:
    db = app.state.db
    registry = app.state.registry
    system_prompt = app.state.system_prompt
    llm_client = app.state.llm_client
    max_turns = app.state.settings.MAX_TOOL_TURNS

    await update_task(db, task_id, status="running", started_at=now_iso())
    await insert_message(db, conversation_id, role="user", content=input_text)

    try:
        history = await get_conversation_messages(db, conversation_id)
        result = await run_conversation(
            llm_client, registry, system_prompt, history, max_turns=max_turns
        )
        await insert_message(db, conversation_id, role="assistant", content=result)
        await update_task(db, task_id, status="completed", result=result, finished_at=now_iso())
    except Exception as exc:  # a background task must never fail silently or crash the loop
        await update_task(db, task_id, status="failed", error=str(exc), finished_at=now_iso())
