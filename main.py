from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agent.openai_client import build_openai_client
from agent.prompt import build_system_prompt
from agent.registry import build_registry
from agent.tools.files import build_file_tools
from agent.tools.mcp import build_mcp_tools, summarize_connections
from agent.tools.rest import build_rest_tools
from api.chat import router as chat_router
from api.mcp_status import router as mcp_status_router
from api.tasks import router as tasks_router
from settings import Settings
from storage.db import (
    init_db,
    list_enabled_api_configs,
    list_enabled_mcp_servers,
    seed_api_configs,
    seed_mcp_servers,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings

    sandbox_root = Path(settings.SANDBOX_ROOT).resolve(strict=True)
    app.state.db = await init_db(settings.DB_PATH)

    base_prompt = Path(settings.SYSTEM_PROMPT_PATH).read_text()
    app.state.system_prompt = build_system_prompt(base_prompt)

    file_tools = build_file_tools(sandbox_root)

    await seed_api_configs(app.state.db, Path(settings.API_ALLOWLIST_PATH))
    api_configs = await list_enabled_api_configs(app.state.db)
    rest_tools = build_rest_tools(api_configs)

    mcp_stack = AsyncExitStack()
    app.state.mcp_stack = mcp_stack
    await seed_mcp_servers(app.state.db, Path(settings.MCP_SERVERS_PATH))
    mcp_servers = await list_enabled_mcp_servers(app.state.db)
    mcp_tools = await build_mcp_tools(mcp_servers, mcp_stack)
    app.state.mcp_status = summarize_connections(mcp_servers, mcp_tools)

    app.state.registry = build_registry(file_tools, rest_tools, mcp_tools)

    app.state.llm_client = build_openai_client(
        settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL
    )

    yield

    await mcp_stack.aclose()
    await app.state.db.close()


app = FastAPI(lifespan=lifespan)
app.include_router(chat_router)
app.include_router(tasks_router)
app.include_router(mcp_status_router)
app.mount("/", StaticFiles(directory="web", html=True), name="web")
