# storage-agent: Skills system

Status: implemented.

## Context

storage-agent is a self-hosted AI agent dedicated to a storage infrastructure team, covering PureStorage, NetApp, Dell ECS, and general NAS/SAN/object storage administration — shares, DFS, export policies, naming conventions, and vendor-specific procedures. The core system (sandboxed file tools, an allowlisted REST-API tool, an MCP client, a chat webview, and a trigger REST API) is already built and running (see `docs/superpowers/specs/2026-09-24-storage-agent-design.md`).

"Skills" — named, loadable domain knowledge — were deferred during the original build, with `agent/prompt.py::build_system_prompt()` deliberately left as a discrete function specifically so this could be added later without restructuring. This spec designs that addition.

A skill in this project is **instructional content, not executable code**: vendor-specific conventions, safety rules, API/CLI syntax references, and procedures — text that shapes how the agent uses the tools it already has (REST, MCP, file tools), not a new way for it to act. The project deliberately has no code-execution tool, and this design doesn't add one; that was an explicit scope decision, not an oversight.

## Goals

- Let the team encode storage-domain knowledge (per vendor: PureStorage, NetApp, ECS, etc., plus cross-cutting rules) as versioned, reviewable files the agent loads into context.
- Keep small, universally-relevant knowledge ("always confirm before deleting a volume") available on every request without extra latency.
- Keep large, situational reference material (a full ONTAP CLI reference, a REST API cheat sheet) out of every request's prompt by default — this matters concretely here, since the team runs this against a local, CPU-bound model where prompt size directly costs response time.
- Fit the existing extensibility pattern (`Tool` dataclass + `build_X_tools()` + `ToolRegistry`) rather than introducing a parallel mechanism.

## Architecture

```
skills/
  team-safety-rules/
    SKILL.md              # always_on: true — short, universal
  purestorage/
    SKILL.md              # always_on: false
    reference/
      rest-api-cheatsheet.md
      naming-conventions.md
  netapp/
    SKILL.md
    reference/
      ontap-cli-reference.md
```

Two triggering modes, chosen per skill via frontmatter:

- **Always-on**: the skill's full instructions are appended to the system prompt once at startup. For short, universally-relevant knowledge.
- **On-demand**: only the skill's name + one-line description appear in the system prompt (a short catalog). The LLM calls a `load_skill(name)` tool to pull in the full instructions when a task actually needs them — the same progressive-disclosure pattern Claude Code's own skills use, and one the team is already directly familiar with from using this very tool.

## Components

- **`agent/skills.py`** (new):
  - `Skill` dataclass: `name: str`, `description: str`, `always_on: bool`, `instructions: str`, `reference_dir: Path | None`.
  - `load_skills(skills_dir: Path) -> list[Skill]` — scans `skills/*/SKILL.md`. For each, parses YAML frontmatter (`name`, `description`, `always_on`, via `pyyaml`'s `yaml.safe_load`) plus the Markdown body as `instructions`. Sets `reference_dir` to the skill's own directory if a `reference/` subdirectory exists, else `None`.
    - A malformed `SKILL.md` (missing `name` or `description`) is skipped with a logged warning — the same resilience pattern `build_mcp_tools()` already uses for a misconfigured MCP server: one bad skill folder doesn't block the others or the app.
    - A duplicate `name` across skill folders raises `DuplicateSkillName` at startup — a real config bug, fail fast (mirrors `DuplicateToolName` in `agent/registry.py`).
  - Returns the full list; callers partition into always-on vs. on-demand by the `always_on` flag.

- **`agent/tools/skills.py`** (new) — `build_skill_tools(on_demand_skills: list[Skill]) -> list[Tool]`. Follows the exact same shape as `build_file_tools`/`build_rest_tools`/`build_mcp_tools`. Returns `[]` if `on_demand_skills` is empty (no pointless tools registered, same convention as REST/MCP with empty config). Otherwise returns two tools:
  - **`load_skill(name)`** — looks up the skill by name among on-demand skills. Returns its `instructions`, followed by a listing of any reference files under `reference_dir` (relative paths) with a note to use `read_skill_file` for one. Unknown name → `"Error: unknown skill 'x'"` (via `guard_errors`, never a crash — same convention as every other tool).
  - **`read_skill_file(skill, path)`** — resolves `path` within that skill's `reference_dir` using `resolve_in_sandbox()` (imported from `agent/tools/files.py`, reused as-is — the containment/traversal-safety requirement is identical, so this isn't reimplemented). Returns the file's content, size-capped and truncated using the same convention as `read_file` (`DEFAULT_MAX_READ_BYTES`). Unknown skill, unknown path, or a traversal attempt → an `"Error: ..."` string, not a crash.

- **`agent/prompt.py::build_system_prompt(base_prompt, always_on_skills, on_demand_skills)`** (signature extended — this is the seam the original design left for exactly this): appends each always-on skill's full `instructions` as its own section, then (if any on-demand skills exist) a short catalog: one line per skill, `name — description`, with a note that `load_skill` loads one's full instructions.

- **`main.py`**: new `SKILLS_PATH` setting (default `./skills`, matching `SANDBOX_ROOT`/`API_ALLOWLIST_PATH`/`MCP_SERVERS_PATH`'s convention). At startup: `load_skills(Path(settings.SKILLS_PATH))`, partition by `always_on`, pass both lists into `build_system_prompt()`, and add `build_skill_tools(on_demand_skills)`'s output into the registry alongside file/REST/MCP tools.

## Data flow example

1. A user asks the agent to provision a PureStorage volume.
2. The LLM sees `purestorage — PureStorage FlashArray/FlashBlade provisioning...` in the system prompt's on-demand catalog and calls `load_skill({"name": "purestorage"})`.
3. The tool result gives it the skill's conventions/procedures, plus: `Reference files available (use read_skill_file to view): rest-api-cheatsheet.md, naming-conventions.md` — paths relative to the skill's own `reference/` directory, not including the `reference/` prefix.
4. If the task needs exact REST syntax, the LLM calls `read_skill_file({"skill": "purestorage", "path": "rest-api-cheatsheet.md"})` to pull that specific file rather than having it in context by default.
5. The LLM proceeds using the (already-existing) REST-allowlist tools, now informed by the loaded skill's conventions.

## Error handling summary

| Condition | Behavior |
|---|---|
| Malformed `SKILL.md` (missing `name`/`description`) | Skipped, logged warning, app starts normally |
| Duplicate skill `name` | `DuplicateSkillName` raised at startup (fail fast) |
| `load_skill` with unknown name | `"Error: unknown skill '...'"` string, not a crash |
| `read_skill_file` with unknown skill/path, or a traversal attempt | `"Error: ..."` string, not a crash (reuses `resolve_in_sandbox`) |
| Zero on-demand skills configured | `load_skill`/`read_skill_file` not registered as tools at all |
| Zero skills configured at all (`skills/` empty or missing) | System prompt unchanged from today; app starts normally |

## Testing

- `tests/test_skills.py`: `load_skills()` — parses a valid `SKILL.md` correctly (including `reference_dir` detection), skips a malformed one with a warning (doesn't block loading the rest), raises `DuplicateSkillName` on a repeated `name`. `load_skill`/`read_skill_file` tool behavior, including every row of the error table above, using `tmp_path` fixtures (never the real project tree) — same pattern as `tests/test_files_tool.py`.
- `tests/test_prompt.py`: extend for the new `build_system_prompt(base_prompt, always_on_skills, on_demand_skills)` signature — always-on instructions appended, on-demand catalog appended with correct name/description lines, no catalog section when there are no on-demand skills.
- Live verification: create real example skill folders (an always-on `team-safety-rules` and an on-demand `purestorage` with a reference file), run the local Ollama-backed server, and drive an actual chat message that should trigger `load_skill` then `read_skill_file`, confirming the full round trip end-to-end (matching how every other feature in this project was verified during its own build).

## Dependencies

Adds `pyyaml` (frontmatter parsing) — a standard, minimal, well-known dependency; avoids a hand-rolled parser being wrong on an edge case like a `description` field containing a colon.

## Out of scope (explicit)

- **Script execution.** Considered and explicitly declined — storage-agent has no code-execution tool today, and adding one was judged a meaningfully bigger, more security-sensitive addition than this design warrants for a storage-infra agent. Skills are instructions + reference data only.
- **Runtime skill management (create/edit/toggle via API or UI).** Skills are managed as files on disk, loaded once at startup — consistent with how `config/api_allowlist.json` and `config/mcp_servers.json` already work (edit the file, restart). Not addressed here; would be a separate, later design if actually needed.
