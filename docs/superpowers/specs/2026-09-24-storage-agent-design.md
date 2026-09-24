# storage-agent: AI agent with sandboxed file ops, allowlisted REST, MCP, chat webview, and a trigger API

Status: implemented (Milestones 1–4 complete).

## Context

storage-agent is a self-hosted AI agent (Python, server-hosted) that can:

- manipulate files inside one configured sandbox subdirectory,
- call REST APIs, restricted to an admin-configured allowlist (not arbitrary URLs),
- connect to MCP servers — both local stdio subprocesses and remote HTTP/SSE — and use their tools,
- be used interactively via a browser chat webview,
- and be triggered programmatically by external scripts/apps via a REST endpoint (submit a task, poll for status/result), separate from the human chat flow.

The project was built from scratch. The guiding constraint was that the code stay **easy to read and extend**: one common `Tool` interface that file ops, REST-allowlist calls, and MCP tools all implement, aggregated into a single registry the agent loop consumes. Adding a new capability later means adding one small module/config entry, not touching the core loop.

Two things are intentionally deferred but designed not to block later:

- **"Skills"** (Claude-Code-style: named instructions, optionally with bundled scripts, injected into the system prompt when triggered) — not built. `agent/prompt.py::build_system_prompt()` is kept as its own discrete function specifically so a `SkillRegistry` can hook in later without restructuring.
- **System prompt editing** — loads `config/system_prompt.md` as a static file at startup. No runtime/admin API to edit it.

## Architecture

Single Python/FastAPI monolith, one process, one SQLite file. Both entry points (webview and trigger API) share the same agent core and tool registry; they only differ in how they feed it messages and how they return results (streaming over WebSocket vs. async task + polling).

```
Browser ──WS──▶ Chat Webview (WebSocket) ─┐
                                            ├──▶ Agent Core (LLM tool-calling loop) ──▶ Tool Registry ──▶ File tools (sandboxed)
External ──REST─▶ Trigger API (POST/GET) ─┘         │                                                  ├─▶ REST tools (allowlist)
Script                                              ▼                                                  └─▶ MCP tools (stdio/HTTP)
                                              SQLite (conversations, messages,
                                              tasks, api_configs, mcp_servers)
                                                     │
                                              OpenAI-compatible chat completions
                                              endpoint (user's own base_url + key)
```

Auth: a single shared bearer token, required on both the WebSocket (sent as the first frame after connect, since browsers can't set custom headers on a WS upgrade) and the trigger REST API (`Authorization: Bearer <token>` header).

## Directory structure (as built)

```
storage-agent/
  pyproject.toml                 # deps via uv
  .env.example
  .gitignore                     # data/, .env, __pycache__, .venv
  main.py                        # FastAPI app + lifespan (startup/shutdown)
  settings.py                    # pydantic-settings, reads env/.env
  auth.py                        # parse_bearer_token / verify_token (pure logic, no framework coupling)

  config/
    system_prompt.md             # static system prompt
    api_allowlist.json           # seed data -> api_configs table (starts empty: [])
    mcp_servers.json              # seed data -> mcp_servers table (starts empty: [])

  agent/
    core.py                      # run_conversation() - the LLM tool-calling loop
    llm.py                       # LLMClient protocol, AssistantMessage/ToolCallRequest dataclasses
    openai_client.py             # OpenAICompatibleClient: LLMClient adapter over the openai SDK
    prompt.py                    # build_system_prompt() - discrete step, future SkillRegistry seam
    registry.py                  # ToolRegistry: aggregates tools, dispatch by name, fails fast on name collision
    tools/
      base.py                    # Tool dataclass, to_openai_spec(), guard_errors() (shared error-safety wrapper)
      files.py                   # sandboxed file tools + resolve_in_sandbox()
      rest.py                    # allowlisted REST tool wrapper
      mcp.py                     # MCP client wrapper (stdio + streamable_http + sse)

  api/
    chat.py                      # WebSocket /ws/chat
    tasks.py                     # POST /tasks, GET /tasks/{id}
    schemas.py                   # pydantic request/response models

  storage/
    db.py                        # schema, init, seeding, CRUD helpers (aiosqlite)

  web/
    index.html                   # chat UI shell
    chat.js                      # WebSocket connect + auth frame + streaming render
    style.css

  data/                          # gitignored at runtime
    storage_agent.db
    sandbox/                     # default SANDBOX_ROOT

  tests/                         # 78 tests, all passing
    test_sandbox_traversal.py
    test_files_tool.py
    test_tool_base.py
    test_registry.py
    test_db.py
    test_settings.py
    test_auth.py
    test_core.py
    test_openai_client.py
    test_prompt.py
    test_chat_ws.py
    test_tasks_api.py
    test_rest_tool.py
    test_mcp_tool.py
    fixtures/dummy_mcp_server.py  # tiny real MCP stdio server used by test_mcp_tool.py
```

## Core interface: the `Tool` protocol

Every capability — hand-written file tools, dynamically generated REST-allowlist tools, and discovered MCP tools — is just an instance of one dataclass (`agent/tools/base.py`). This is the whole extensibility mechanism; there is no separate class hierarchy per tool source.

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]                       # JSON schema for arguments
    execute: Callable[[dict[str, Any]], Awaitable[str]]
```

`base.py` also provides `guard_errors(fn)`, which wraps a tool's inner async function so any exception becomes an `"Error: ..."` string result instead of propagating — every tool source (files, REST, MCP) uses it, so a bad path, a failed HTTP call, or an unreachable MCP tool never crashes the agent loop.

- `agent/tools/files.py::build_file_tools(sandbox_root)` — builds `list_dir`, `search_files`, `read_file`, `write_file`, `edit_file`, `delete_file`, `move_file` as closures over `sandbox_root`.
- `agent/tools/rest.py::build_rest_tools(configs)` — one `Tool` per `(api_config, operation)` pair, named `{config_name}_{operation_name}`.
- `agent/tools/mcp.py::build_mcp_tools(configs, stack)` — connects to each configured MCP server, lists its tools, wraps each as a `Tool` named `mcp_{server_name}_{tool_name}`.
- `agent/registry.py::build_registry(*tool_groups)` — aggregates everything into one list + a `by_name` dict; raises `DuplicateToolName` at startup on any name collision (fail fast, not silent shadowing).

## The agent core loop

`agent/core.py::run_conversation(client, registry, system_prompt, history, max_turns, on_event)`:

1. Prepends `system_prompt` to `history` and converts `registry.tools` to OpenAI function-call specs.
2. Calls `client.complete(messages, tools)`. `client` is anything implementing the `LLMClient` protocol (`agent/llm.py`) — decoupled from the OpenAI SDK so the loop is unit-testable with a fake client.
3. If the model returns no tool calls, fires an `on_event({"type": "final", ...})` and returns the content.
4. Otherwise, for each requested tool call: fires `tool_call`/`tool_result` events, looks up the tool by name (unknown tool name → an `"Error: unknown tool ..."` string, not a crash), executes it, and appends the result as a `role: tool` message. Loops back to step 2.
5. Raises `ToolTurnLimitExceeded` if `max_turns` is exceeded without a final answer.

`agent/openai_client.py::OpenAICompatibleClient` implements `LLMClient` against the real `openai` SDK's `AsyncOpenAI`, pointed at the user's custom `base_url`/`api_key`/`model`. It omits the `tools` kwarg entirely (passes `None`) when the registry is empty, since some OpenAI-compatible endpoints reject an empty `tools` array.

## Sandboxing (file tools)

At startup, `SANDBOX_ROOT = Path(settings.SANDBOX_ROOT).resolve(strict=True)` (fails fast if missing). Every incoming path from the LLM goes through `resolve_in_sandbox()` before touching the filesystem:

1. Reject if the path is absolute, or `".."` appears in its parts.
2. `resolved = (sandbox_root / user_path).resolve(strict=False)` — resolves symlinks on existing components (defeats a symlink planted inside the sandbox pointing outside it) while still allowing new files to be created.
3. Reject unless `resolved.is_relative_to(sandbox_root)`.

`move_file` validates both `src` and `dest` independently. Covered by `tests/test_sandbox_traversal.py`, including a real symlink-escape case.

## REST allowlist tools

`config/api_allowlist.json` defines named APIs: `{name, description, base_url, auth_type, auth_value, auth_header_name, operations: [{name, method, path, description, params_schema}]}`. `auth_value` (and MCP `headers`/`env` values) support `${ENV_VAR}` interpolation resolved at seed time, so secrets aren't committed to the JSON files.

Only the declared `operations` become callable tools — the LLM never gets a generic "call any URL" tool. `auth_type` supports `none`, `bearer`, `api_key_header`, and `basic`. Path placeholders (`{param}`) in `operation.path` are substituted from the tool's arguments; any remaining arguments become query params (`GET`/`DELETE`) or a JSON body (other methods). A non-2xx response is caught by `guard_errors` and returned as an `"Error: ..."` string.

## MCP client

Uses the official `mcp` Python SDK (installed version: `mcp==2.2.0`; note this SDK's v2 line renamed `FastMCP` to `MCPServer` and its streamable-HTTP transport function is `streamable_http_client`, singular-underscored, not `streamablehttp_client`). Both transports are supported per server config (`transport: stdio | streamable_http | sse`):

- `stdio`: `StdioServerParameters(command, args, env)` + `stdio_client(...)`.
- `streamable_http`: `streamable_http_client(url, http_client=...)`. **Implementation note discovered while building this**: this transport's `http_client` parameter requires an instance of the separate `httpx2` package (not the `httpx` package used everywhere else in this project) — that's what the installed `mcp` SDK's transport layer is built on. `agent/tools/mcp.py` constructs an `httpx2.AsyncClient(headers=...)` only when the config specifies `headers`.
- `sse`: `sse_client(url, headers=...)` (accepts headers directly; kept only as a legacy fallback).

All connections are long-lived async context managers entered into one `AsyncExitStack` created during FastAPI's `lifespan` (`main.py`) and closed on shutdown. Each server connection is attempted independently with its own try/except in `build_mcp_tools` — a failing server is logged (`logger.warning(..., exc_info=True)`) and skipped, it never blocks the rest of the app or other servers from starting. Verified with `tests/test_mcp_tool.py` against a real local stdio server (`tests/fixtures/dummy_mcp_server.py`) and against a deliberately-broken server config in the same run.

Discovered MCP tools are wrapped using the SDK's actual (snake_case) attribute names: `mcp_tool.name`, `mcp_tool.description`, `mcp_tool.input_schema` (the JSON alias `inputSchema` is not the Python attribute name in this SDK version), and `CallToolResult.content` items exposing `.text` for text blocks.

## Data model (SQLite)

- `conversations(id, source['webview'|'task'], created_at, updated_at)`
- `messages(id, conversation_id, role, content, tool_calls, tool_call_id, name, created_at)` — `get_conversation_messages()` omits any `None`-valued fields per row, so the returned dicts are directly usable as OpenAI-format chat messages without extra cleanup.
- `tasks(id, status['pending'|'running'|'completed'|'failed'], input, result, error, conversation_id, created_at, started_at, finished_at)`
- `api_configs(name, description, base_url, auth_type, auth_value, auth_header_name, operations, enabled, created_at)`
- `mcp_servers(name, transport, command, args, env, url, headers, enabled, created_at)`

`config/*.json` are seed inputs; on startup `seed_api_configs()`/`seed_mcp_servers()` idempotently upsert them into their tables (`INSERT ... ON CONFLICT(name) DO UPDATE`). The running app reads tool definitions from SQLite, not the files directly — a clean seam for a future admin API to edit rows without touching files.

## API surface

- **`WebSocket /ws/chat`**: client's first frame must be `{"token": "..."}` (else `{"type": "error", "message": "unauthorized"}` then closed with code 4401). Then exchanges `{"type": "message", "conversation_id": "<uuid|null>", "content": "..."}` for streamed `{"type": "tool_call"|"tool_result"|"final"|"error", ...}` frames. A `null` `conversation_id` creates a new conversation; the server's own `final` event (not the core loop's) carries the `conversation_id` so the client can persist it for the next message.
- **`POST /tasks`** (`Authorization: Bearer <token>`, body `{"input": "..."}`) → creates a task + conversation row, runs the agent loop as a background `asyncio.create_task`, returns `202` `{"task_id", "status": "pending"}` immediately.
- **`GET /tasks/{task_id}`** (same auth) → `{"task_id", "status", "input", "result", "error", "created_at", "started_at", "finished_at"}`, `404` if unknown. A task interrupted by a server restart stays `running` rather than resuming (acceptable for v1; not retried automatically). A background task's own exceptions are always caught and recorded as `status="failed"`, `error=str(exc)` — verified live against an unreachable LLM endpoint.

## Config

Env vars (`settings.py`, `pydantic-settings`): `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `SANDBOX_ROOT`, `API_TOKEN`, `DB_PATH`, `SYSTEM_PROMPT_PATH`, `API_ALLOWLIST_PATH`, `MCP_SERVERS_PATH`, `MAX_TOOL_TURNS` (default 20), `LOG_LEVEL`.

Config files: `config/api_allowlist.json`, `config/mcp_servers.json` (both start as `[]`), `config/system_prompt.md`.

## Dependencies (via `uv`, `pyproject.toml`)

Runtime: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `aiosqlite`, `openai` (pointed at the custom `base_url`/`api_key`), `httpx` (for allowlisted REST calls), `mcp` (pulls in `httpx2` as a transitive dependency, used only inside `agent/tools/mcp.py`).
Dev/test: `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"` in `pyproject.toml`), `respx` (httpx mocking).

Target Python 3.12 via `uv venv --python 3.12` (managed automatically by `uv`).

## Verification performed

All 78 automated tests pass (`uv run pytest`), written test-first per milestone. In addition, each milestone was smoke-tested against the actual running server:

- **Milestone 1**: server starts, static webview serves, WebSocket auth handshake rejects a wrong token and accepts the correct one.
- **Milestone 2**: `POST /tasks` without a token → `401`; with a token → `202` + `task_id`; polling `GET /tasks/{id}` showed the real `pending → running → failed` lifecycle (failure expected — the smoke test used a placeholder, unreachable `LLM_BASE_URL`) with the connection error captured in `error`, proving the background task never crashes the server.
- **Milestone 3**: server starts cleanly with the (empty) REST allowlist wired into the registry.
- **Milestone 4**: server starts cleanly with a real local MCP stdio server (the same `dummy_mcp_server.py` fixture used in tests) configured in `mcp_servers.json`, with no connection warnings logged.

**Not yet verified**: an actual end-to-end chat/task round trip against a real OpenAI-compatible LLM endpoint — that requires the user's real `LLM_BASE_URL`/`LLM_API_KEY`, which weren't available in this session.

## Deferred (not built, by design)

- **Skills system** — `agent/prompt.py::build_system_prompt()` is the seam; a `SkillRegistry` would inject matching skills' instructions here based on the incoming request.
- **Runtime-editable system prompt** — currently a static file read once at startup.
