from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agent.openai_client import build_openai_client
from agent.prompt import build_system_prompt
from agent.registry import build_registry
from agent.skills import load_skills
from agent.tools.files import build_file_tools
from agent.tools.mcp import build_mcp_tools, load_mcp_server_configs, summarize_connections
from agent.tools.rest import build_rest_tools
from agent.tools.skills import build_skill_tools
from api.chat import router as chat_router
from api.mcp_status import router as mcp_status_router
from api.tasks import router as tasks_router
from settings import Settings
from storage.db import init_db, list_enabled_api_configs, seed_api_configs


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings

    sandbox_root = Path(settings.SANDBOX_ROOT).resolve(strict=True)
    app.state.db = await init_db(settings.DB_PATH)

    skills = load_skills(Path(settings.SKILLS_PATH))
    always_on_skills = [s for s in skills if s.always_on]
    on_demand_skills = [s for s in skills if not s.always_on]

    base_prompt = Path(settings.SYSTEM_PROMPT_PATH).read_text()
    app.state.system_prompt = build_system_prompt(base_prompt, always_on_skills, on_demand_skills)

    file_tools = build_file_tools(sandbox_root)
    skill_tools = build_skill_tools(on_demand_skills)

    # long-lived resources (pooled HTTP clients, MCP connections) that must
    # outlive the request that created them and be closed together at shutdown
    resources_stack = AsyncExitStack()
    app.state.resources_stack = resources_stack

    await seed_api_configs(app.state.db, Path(settings.API_ALLOWLIST_PATH))
    api_configs = await list_enabled_api_configs(app.state.db)
    rest_tools = await build_rest_tools(api_configs, resources_stack)

    mcp_servers = load_mcp_server_configs(Path(settings.MCP_SERVERS_PATH))
    mcp_tools = await build_mcp_tools(mcp_servers, resources_stack)
    app.state.mcp_status = summarize_connections(mcp_servers, mcp_tools)

    app.state.registry = build_registry(file_tools, rest_tools, mcp_tools, skill_tools)

    app.state.llm_client = build_openai_client(
        settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL
    )

    yield

    await resources_stack.aclose()
    await app.state.db.close()


app = FastAPI(lifespan=lifespan)
app.include_router(chat_router)
app.include_router(tasks_router)
app.include_router(mcp_status_router)
app.mount("/", StaticFiles(directory="web", html=True), name="web")
