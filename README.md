# storage-agent

A self-hosted AI agent with sandboxed file access, an allowlisted REST-API tool, an MCP client, a browser chat webview, and a REST API for triggering it programmatically. See [`docs/superpowers/specs/2026-09-24-storage-agent-design.md`](docs/superpowers/specs/2026-09-24-storage-agent-design.md) for the full design.

## Requirements

- Python 3.12+ (the project pins this via `.python-version`; [`uv`](https://docs.astral.sh/uv/) manages the interpreter and virtualenv for you, so a separate manual install isn't required)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)

## Setup

```bash
git clone <this repo>
cd storage-agent
uv sync                       # creates .venv and installs all dependencies
cp .env.example .env          # then edit .env, see Configuration below
mkdir -p data/sandbox          # the agent's sandboxed working directory
```

## Configuration

### Environment variables (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | yes | — | Base URL of your OpenAI-compatible chat completions endpoint. |
| `LLM_API_KEY` | yes | — | API key for that endpoint. |
| `LLM_MODEL` | yes | — | Model name to request. |
| `SANDBOX_ROOT` | yes | — | Directory the file tools are restricted to. Must exist before startup (the app fails fast if it doesn't). Use a dedicated, empty directory — never a path containing anything sensitive. |
| `API_TOKEN` | yes | — | Shared bearer token required by both the chat WebSocket and the trigger REST API. Generate a strong random value, e.g. `openssl rand -hex 32`. |
| `DB_PATH` | no | `./data/storage_agent.db` | SQLite database file (conversations, messages, tasks, allowlist/MCP config). |
| `SYSTEM_PROMPT_PATH` | no | `./config/system_prompt.md` | System prompt loaded once at startup. |
| `API_ALLOWLIST_PATH` | no | `./config/api_allowlist.json` | Allowlisted REST APIs the agent may call. |
| `MCP_SERVERS_PATH` | no | `./config/mcp_servers.json` | MCP servers the agent connects to. |
| `MAX_TOOL_TURNS` | no | `20` | Safety cap on tool-call round trips per conversation turn before the agent gives up with an error. |
| `LOG_LEVEL` | no | `INFO` | Standard Python logging level. |

### System prompt

Edit `config/system_prompt.md` directly (plain text/Markdown, loaded verbatim). Changes require a restart to take effect — there's no runtime edit API in this version.

### REST API allowlist (`config/api_allowlist.json`)

Starts as `[]` (no external APIs callable). Each entry becomes a set of named tools — only the `operations` you declare are ever callable, never arbitrary URLs:

```json
[
  {
    "name": "weather_api",
    "description": "Public weather lookups",
    "base_url": "https://api.weather.example.com",
    "auth_type": "bearer",
    "auth_value": "${WEATHER_API_KEY}",
    "operations": [
      {
        "name": "get_forecast",
        "method": "GET",
        "path": "/forecast/{city}",
        "description": "Get the forecast for a city",
        "params_schema": {
          "type": "object",
          "properties": {
            "city": { "type": "string" },
            "units": { "type": "string" }
          },
          "required": ["city"]
        }
      }
    ]
  }
]
```

This produces one tool named `weather_api_get_forecast`. `{city}` in `path` is substituted from the matching argument; any remaining arguments become query params (`GET`/`DELETE`) or a JSON body (other methods).

- `auth_type`: `none`, `bearer`, `api_key_header` (also set `auth_header_name`), or `basic`.
- `auth_value` supports `${ENV_VAR}` interpolation — put the real secret in `.env` (e.g. `WEATHER_API_KEY=...`) instead of committing it to this file.
- Set `"enabled": false` on an entry to seed it into the database but keep it inactive.

Changes require a restart (the file is re-seeded into SQLite on every startup).

### MCP servers (`config/mcp_servers.json`)

Starts as `[]`. Supports local stdio subprocesses and remote HTTP servers:

```json
[
  {
    "name": "local_tools",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "some-mcp-server"],
    "env": { "SOME_TOKEN": "${SOME_MCP_TOKEN}" }
  },
  {
    "name": "remote_tools",
    "transport": "streamable_http",
    "url": "https://mcp.example.com",
    "headers": { "Authorization": "Bearer ${REMOTE_MCP_TOKEN}" }
  }
]
```

Discovered tools are namespaced as `mcp_<server_name>_<tool_name>` to avoid collisions. `transport` is `stdio`, `streamable_http`, or `sse` (legacy fallback). `env`/`headers` values support the same `${ENV_VAR}` interpolation as the REST allowlist. If a server fails to connect at startup, it's logged and skipped — it never blocks the app or other servers from starting. Changes require a restart.

## Running locally

```bash
uv run uvicorn main:app --reload
```

Open `http://localhost:8000/` for the chat webview (it'll prompt for `API_TOKEN` on first connect and remember it in `localStorage`).

## Using the trigger API

```bash
# Submit a task
curl -X POST http://localhost:8000/tasks \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input": "list the files in the sandbox"}'
# -> {"task_id": "...", "status": "pending"}

# Poll for the result
curl http://localhost:8000/tasks/<task_id> -H "Authorization: Bearer $API_TOKEN"
```

## Running tests

```bash
uv run pytest
```

## Deployment

This is a single Python process with local SQLite persistence — no external services (database, queue, cache) to stand up.

1. **Run the process.** In production, drop `--reload` and bind explicitly:
   ```bash
   uv run uvicorn main:app --host 0.0.0.0 --port 8000
   ```
   Keep it to a single worker process, or if you need more than one, ensure they all run on the **same host** sharing the same `data/` directory (SQLite with WAL, already enabled, handles same-host multi-process access correctly — task status is read from SQLite, not memory). Do **not** split workers across multiple hosts; there's no shared state beyond the local SQLite file.

2. **Put it behind a reverse proxy** (nginx, Caddy, etc.) for TLS termination. Terminate HTTPS/WSS there and proxy to the app over plain HTTP/WS on localhost. Example nginx snippet:
   ```nginx
   location / {
       proxy_pass http://127.0.0.1:8000;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header Host $host;
   }
   ```
   The `Upgrade`/`Connection` headers are required for the `/ws/chat` WebSocket to work through the proxy.

3. **Run it as a service** (systemd example):
   ```ini
   [Unit]
   Description=storage-agent
   After=network.target

   [Service]
   WorkingDirectory=/opt/storage-agent
   EnvironmentFile=/opt/storage-agent/.env
   ExecStart=/opt/storage-agent/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
   Restart=on-failure
   User=storage-agent

   [Install]
   WantedBy=multi-user.target
   ```
   Run `uv sync --frozen` during deploy/release to build `.venv` from `uv.lock`, then point `ExecStart` at `.venv/bin/uvicorn` as above.

4. **Persist `data/`.** It holds the SQLite database and the sandbox directory — back it up and make sure it survives redeploys (mount it as a volume if you're containerizing).

5. **Security checklist**
   - `API_TOKEN` must be a long random value, kept out of version control (`.env` is gitignored).
   - Always run production traffic through HTTPS/WSS — the bearer token and WebSocket auth frame are sent in plaintext otherwise.
   - `SANDBOX_ROOT` must be a directory containing nothing sensitive; the agent's file tools are restricted to it, but treat that boundary as the blast radius if something goes wrong.
   - Only add REST APIs and MCP servers you trust to `config/api_allowlist.json` / `config/mcp_servers.json` — their tools run with whatever credentials you configure for them.

## Project structure

```
main.py            FastAPI app + startup/shutdown wiring
settings.py         Configuration (env vars)
auth.py              Bearer-token verification
agent/               Agent core, LLM client, tool implementations
api/                 WebSocket chat + trigger REST API
storage/             SQLite schema and access layer
web/                 Static chat webview (no build step)
config/              system_prompt.md, api_allowlist.json, mcp_servers.json
tests/               78 tests covering every module
docs/superpowers/specs/   Design spec
```
