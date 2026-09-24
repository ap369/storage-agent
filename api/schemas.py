from pydantic import BaseModel


class TaskCreateRequest(BaseModel):
    input: str


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    input: str
    result: str | None
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class MCPServerStatus(BaseModel):
    name: str
    transport: str
    connected: bool
    tools: list[str]
