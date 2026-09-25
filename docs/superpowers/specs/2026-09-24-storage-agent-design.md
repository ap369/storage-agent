# storage-agent: AI agent with sandboxed file ops, allowlisted REST, MCP, chat webview, and a trigger API

Status: implemented (Milestones 1–4 complete), plus post-implementation additions: a WebSocket resilience fix, webview UX polish, and an MCP connection-status feature.

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
                                              tasks, api_configs)
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
    mcp_servers.json              # read directly at startup, no DB round-trip (starts empty: [])

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
    mcp_status.py                # GET /mcp/status
    schemas.py                   # pydantic request/response models

  storage/
    db.py                        # schema, init, seeding, CRUD helpers (aiosqlite)

  web/
    index.html                   # chat UI shell + MCP status panel
    chat.js                      # WebSocket connect + auth frame + streaming render + thinking indicator + MCP status fetch
    style.css

  data/                          # gitignored at runtime
    storage_agent.db
    sandbox/                     # default SANDBOX_ROOT

  tests/                         # 84 tests, all passing
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
    test_mcp_status_api.py
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
- `agent/tools/rest.py::build_rest_tools(configs, stack)` — one `Tool` per `(api_config, operation)` pair, named `{config_name}_{operation_name}`. **Optimization**: builds exactly one pooled `httpx.AsyncClient` per `api_config` (entered into the shared `resources_stack`, same lifecycle pattern as MCP connections) and reuses it across every operation/call for that config, instead of opening a fresh connection (and TLS handshake, for HTTPS APIs) on every single tool invocation. Covered by `tests/test_rest_tool.py::test_builds_one_http_client_per_config_shared_across_operations`, which spies on `httpx.AsyncClient.__init__` to prove construction count stays at 1 regardless of operation count.
- `agent/tools/mcp.py::build_mcp_tools(configs, stack)` — connects to each configured MCP server, lists its tools, wraps each as a `Tool` named `mcp_{server_name}_{tool_name}`.
- `agent/registry.py::build_registry(*tool_groups)` — aggregates everything into one list + a `by_name` dict; raises `DuplicateToolName` at startup on any name collision (fail fast, not silent shadowing). **Optimization**: also precomputes `registry.tool_specs` (each tool's OpenAI function-call spec via `to_openai_spec()`) once here, since the tool set is fixed for the process lifetime — `run_conversation()` previously rebuilt this list from scratch on every single call (every chat message, every triggered task), which was pure repeated work for data that never changes after startup.
- `agent/tools/mcp.py::summarize_connections(configs, tools)` — pure function, no new connection attempts. For each configured MCP server, checks whether any tool in the already-built `tools` list carries that server's `mcp_{name}_` prefix, and reports `{name, transport, connected, tools}`. Computed once at startup and stored on `app.state.mcp_status` for `GET /mcp/status` to serve.

## The agent core loop

`agent/core.py::run_conversation(client, registry, system_prompt, history, max_turns, on_event)`:

1. Prepends `system_prompt` to `history`, using `registry.tool_specs` (precomputed once — see above, not rebuilt per call).
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
- `streamable_http`: `streamable_http_client(url, http_client=...)`. **Implementation note discovered while building this**: this transport's `http_client` parameter requires an instance of the separate `httpx2` package (not the `httpx` package used everywhere else in this project) — that's what the installed `mcp` SDK's transport layer is built on.
- `sse`: `sse_client(url, headers=..., httpx_client_factory=...)` (kept only as a legacy fallback).

**TLS verification note — SECURITY TRADEOFF, deliberately accepted**: `httpx2`'s default `verify=True` builds its SSL context via `truststore.SSLContext(...)` — i.e. it verifies against the *OS-native* certificate trust store, not a bundled CA list. This was traced (by reading `httpx2._config.create_ssl_context()`) as the cause of a `CERTIFICATE_VERIFY_FAILED` error connecting to a remote `streamable_http` MCP server from one particular machine, while the certificate itself was independently confirmed valid (checked with `openssl s_client`) and the same connection succeeded from the development machine — the signature of an incomplete/outdated OS trust store on that other machine, not a real certificate problem.

A first fix (`certifi_ssl_context()`, building an explicit `ssl.SSLContext` from `certifi`'s bundled CA list) was implemented, tested, and verified working, but was then manually overridden by the user to `verify=False` on both the `streamable_http` and `sse` code paths in `agent/tools/mcp.py` instead. **`verify=False` disables TLS certificate verification entirely for every `streamable_http`/`sse` MCP connection this app makes — not scoped to one server, not a trust-store swap, an outright removal of verification.** This was flagged explicitly (broken tests from the edit, and the MITM risk) and the user confirmed they want it kept this way. It is documented here, in code comments at both call sites, so this isn't mistaken for an oversight later. Anyone deploying this against untrusted networks or servers they don't fully control should reintroduce certificate verification (the removed `certifi_ssl_context()` approach is the straightforward way back, see git history) or scope the bypass to specific trusted servers only, rather than relying on this default.

All connections are long-lived async context managers entered into `app.state.resources_stack` (an `AsyncExitStack` created during FastAPI's `lifespan` in `main.py`) and closed together on shutdown — this same stack also holds the pooled REST `httpx.AsyncClient`s, see the REST allowlist section above. Each server connection is attempted independently with its own try/except in `build_mcp_tools` — a failing server is logged (`logger.warning(..., exc_info=True)`) and skipped, it never blocks the rest of the app or other servers from starting. Verified with `tests/test_mcp_tool.py` against a real local stdio server (`tests/fixtures/dummy_mcp_server.py`) and against a deliberately-broken server config in the same run.

Discovered MCP tools are wrapped using the SDK's actual (snake_case) attribute names: `mcp_tool.name`, `mcp_tool.description`, `mcp_tool.input_schema` (the JSON alias `inputSchema` is not the Python attribute name in this SDK version), and `CallToolResult.content` items exposing `.text` for text blocks.

## Chat WebSocket resilience (bug found while running the app live)

The original `api/chat.py` only caught `ToolTurnLimitExceeded` around `run_conversation()`. Driving the app against a real (but unreachable) LLM endpoint surfaced an `openai.APIConnectionError` that propagated out of the WebSocket handler uncaught, killing the connection outright (`ConnectionClosedError: no close frame received or sent` on the client side) instead of reporting a clean error. Fixed by widening the `except` to catch any `Exception` from `run_conversation()`, matching the resilience `api/tasks.py` already had — the connection now sends `{"type": "error", "message": ...}` and stays open for the next message. Covered by `tests/test_chat_ws.py::test_llm_failure_sends_error_event_without_crashing_connection`, and re-verified live.

## Webview UX additions

- **Thinking indicator** (`web/chat.js`): local models can take tens of seconds per turn with no intermediate output, which looked indistinguishable from the app being broken. A pulsing "thinking…" line now appears immediately after sending a message and after each `tool_result` (since the loop goes back to the model), and disappears the instant any server event arrives.
- **MCP status side panel** (`web/index.html` `#sidebar` / `#mcp-status`, rendered by `chat.js::loadMcpStatus()`/`renderMcpStatus()`): the page layout is a fixed-width left sidebar plus a chat main panel. On page load, the sidebar fetches `GET /mcp/status` (using the same cached token as the WebSocket) and renders one entry per configured MCP server — a status dot (● connected / ○ disconnected) plus name, with the full list of its discovered tools shown underneath when connected. Display-only by design (no enable/disable toggle) — an earlier design question confirmed this narrower scope over live connect/disconnect, which would need per-server connection lifecycle management instead of the current startup-only shared `AsyncExitStack`.

## Data model (SQLite)

- `conversations(id, source['webview'|'task'], created_at, updated_at)`
- `messages(id, conversation_id, role, content, tool_calls, tool_call_id, name, created_at)` — `get_conversation_messages()` omits any `None`-valued fields per row, so the returned dicts are directly usable as OpenAI-format chat messages without extra cleanup.
- `tasks(id, status['pending'|'running'|'completed'|'failed'], input, result, error, conversation_id, created_at, started_at, finished_at)`
- `api_configs(name, description, base_url, auth_type, auth_value, auth_header_name, operations, enabled, created_at)`

`config/api_allowlist.json` is a seed input; on startup `seed_api_configs()` idempotently upserts it into `api_configs` (`INSERT ... ON CONFLICT(name) DO UPDATE`). The running app reads REST tool definitions from SQLite, not the file directly — a clean seam for a future admin API to edit rows without touching files.

**MCP servers are intentionally *not* DB-backed** (revised from an earlier version of this design that did mirror `mcp_servers.json` into a SQLite table the same way `api_configs` works). Asked directly why an "MCPs list" existed in the database, the honest answer was: it existed only to leave a seam for a future runtime admin/toggle API, but that feature was explicitly never built (the status panel is display-only, see above) — so the table was dead weight, always fully overwritten from the JSON file on every startup with no code path ever diverging it. `agent/tools/mcp.py::load_mcp_server_configs(path)` now reads and `${ENV_VAR}`-interpolates `config/mcp_servers.json` directly at startup, with no SQLite round-trip at all. If a future MCP admin/toggle API is actually built, `api_configs`' pattern (DB-backed, JSON as seed) is the template to follow at that point — don't add the indirection back speculatively before it's needed.

## API surface

- **`WebSocket /ws/chat`**: client's first frame must be `{"token": "..."}` (else `{"type": "error", "message": "unauthorized"}` then closed with code 4401). Then exchanges `{"type": "message", "conversation_id": "<uuid|null>", "content": "..."}` for streamed `{"type": "tool_call"|"tool_result"|"final"|"error", ...}` frames. A `null` `conversation_id` creates a new conversation; the server's own `final` event (not the core loop's) carries the `conversation_id` so the client can persist it for the next message.
- **`POST /tasks`** (`Authorization: Bearer <token>`, body `{"input": "..."}`) → creates a task + conversation row, runs the agent loop as a background `asyncio.create_task`, returns `202` `{"task_id", "status": "pending"}` immediately.
- **`GET /tasks/{task_id}`** (same auth) → `{"task_id", "status", "input", "result", "error", "created_at", "started_at", "finished_at"}`, `404` if unknown. A task interrupted by a server restart stays `running` rather than resuming (acceptable for v1; not retried automatically). A background task's own exceptions are always caught and recorded as `status="failed"`, `error=str(exc)` — verified live against an unreachable LLM endpoint.
- **`GET /mcp/status`** (same auth) → `list[{"name", "transport", "connected", "tools"}]`, one entry per configured MCP server (regardless of whether it connected successfully), computed once at startup from `summarize_connections()`. Surfaced in the webview's sidebar (see Webview UX additions above).

## Config

Env vars (`settings.py`, `pydantic-settings`): `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `SANDBOX_ROOT`, `API_TOKEN`, `DB_PATH`, `SYSTEM_PROMPT_PATH`, `API_ALLOWLIST_PATH`, `MCP_SERVERS_PATH`, `MAX_TOOL_TURNS` (default 20), `LOG_LEVEL`.

Config files: `config/api_allowlist.json`, `config/mcp_servers.json` (both start as `[]`), `config/system_prompt.md`.

## Dependencies (via `uv`, `pyproject.toml`)

Runtime: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `aiosqlite`, `openai` (pointed at the custom `base_url`/`api_key`), `httpx` (for allowlisted REST calls), `mcp` (pulls in `httpx2` as a transitive dependency, used only inside `agent/tools/mcp.py`).
Dev/test: `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"` in `pyproject.toml`), `respx` (httpx mocking).

Target Python 3.12 via `uv venv --python 3.12` (managed automatically by `uv`).

## Verification performed

All 84 automated tests pass (`uv run pytest`), written test-first throughout. In addition:

- **Milestone 1**: server starts, static webview serves, WebSocket auth handshake rejects a wrong token and accepts the correct one.
- **Milestone 2**: `POST /tasks` without a token → `401`; with a token → `202` + `task_id`; polling `GET /tasks/{id}` showed the real `pending → running → failed` lifecycle (failure expected — the smoke test used a placeholder, unreachable `LLM_BASE_URL`) with the connection error captured in `error`, proving the background task never crashes the server.
- **Milestone 3**: server starts cleanly with the (empty) REST allowlist wired into the registry.
- **Milestone 4**: server starts cleanly with a real local MCP stdio server (the same `dummy_mcp_server.py` fixture used in tests) configured in `mcp_servers.json`, with no connection warnings logged. Later re-verified against a real remote `streamable_http` MCP server (a public test/demo server), discovering 4 real tools with no code changes needed.
- **Real end-to-end LLM round trip**: verified against a locally-run Ollama instance (model `qwen3:4b`, served over its OpenAI-compatible endpoint at `http://localhost:11434/v1`) — the agent correctly called `write_file` then `read_file` as real tool calls in response to a natural-language instruction, and the file was confirmed to actually exist in the sandbox afterward.
- **`GET /mcp/status`**: verified live, returning the real connected server and its 4 discovered tool names.

## Deferred (not built, by design)

- **Skills system** — `agent/prompt.py::build_system_prompt()` is the seam; a `SkillRegistry` would inject matching skills' instructions here based on the incoming request.
- **Runtime-editable system prompt** — currently a static file read once at startup.
