# Skills System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Skills system letting the storage team encode vendor/domain knowledge (PureStorage, NetApp, ECS, NAS/SAN/object conventions) as always-on or on-demand instructions the agent loads into context.

**Architecture:** Skills are directories under `skills/` with a `SKILL.md` (YAML frontmatter + Markdown body) and optional `reference/` files. Always-on skills are appended to the system prompt at startup; on-demand skills appear as a name+description catalog and are pulled in via new `load_skill`/`read_skill_file` tools, following the exact same `Tool`/`build_X_tools()` pattern every other tool source in this project already uses.

**Tech Stack:** Python, `pyyaml` (new dependency, frontmatter parsing), existing FastAPI/pytest/uv toolchain — nothing else new.

**Spec:** `docs/superpowers/specs/2026-09-25-skills-design.md`

## Global Constraints

- Skills are instructions + reference data only — no script execution (explicit scope decision in the spec).
- Skill directory convention: `skills/<name>/SKILL.md` + optional `skills/<name>/reference/`.
- Frontmatter parsed with `pyyaml`'s `yaml.safe_load`, not hand-rolled parsing.
- A malformed `SKILL.md` is skipped with a logged warning; a duplicate skill `name` raises at startup (fail fast) — same resilience/fail-fast split as MCP servers and the tool registry elsewhere in this project.
- `read_skill_file`'s path containment reuses `resolve_in_sandbox()` from `agent/tools/files.py` — not reimplemented.
- Zero on-demand skills → `load_skill`/`read_skill_file` are not registered as tools at all.
- New `SKILLS_PATH` setting, default `./skills`, matching the existing `SANDBOX_ROOT`/`API_ALLOWLIST_PATH`/`MCP_SERVERS_PATH` convention.

## Review Focus

- A skill folder present but with no `SKILL.md` file at all (not malformed — just absent) → `load_skills()` must skip it silently, not crash.
- A skill's `reference/` directory exists but is empty → `load_skill` must not claim reference files are available when there are none.
- Reading a reference file larger than the size cap → must truncate with a note, same convention as `read_file`, not silently cut off unremarked.
- An always-on skill with unusually large instructions → logged warning at startup (not a hard block) — this directly matters here since it inflates every single prompt on the team's CPU-bound local model.
- `always_on` written as a quoted YAML string (`"false"`) instead of a real boolean → must not silently evaluate as truthy just because it's a non-empty string.

---

### Task 1: `agent/skills.py` — Skill loading

**Files:**
- Create: `agent/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: nothing new (stdlib `pathlib`, `logging`, `dataclasses`; `yaml` from the new `pyyaml` dependency).
- Produces: `Skill` (frozen dataclass: `name: str`, `description: str`, `always_on: bool`, `instructions: str`, `reference_dir: Path | None`), `DuplicateSkillName` (Exception), `load_skills(skills_dir: Path) -> list[Skill]`. Later tasks import all three from `agent.skills`.

- [ ] **Step 1: Add the `pyyaml` dependency**

Run: `uv add pyyaml`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_skills.py`:

```python
import logging

import pytest

from agent.skills import DuplicateSkillName, load_skills


def write_skill(base, folder_name, name=None, description="A skill.", always_on=False, body="Do the thing.", reference_files=None):
    name = name or folder_name
    skill_dir = base / folder_name
    skill_dir.mkdir()
    always_on_line = str(always_on).lower()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nalways_on: {always_on_line}\n---\n{body}\n"
    )
    if reference_files:
        ref_dir = skill_dir / "reference"
        ref_dir.mkdir()
        for filename, content in reference_files.items():
            (ref_dir / filename).write_text(content)
    return skill_dir


def test_load_skills_parses_valid_skill(tmp_path):
    write_skill(tmp_path, "purestorage", description="PureStorage conventions.", always_on=False, body="Full instructions here.")

    skills = load_skills(tmp_path)

    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "purestorage"
    assert skill.description == "PureStorage conventions."
    assert skill.always_on is False
    assert skill.instructions == "Full instructions here."
    assert skill.reference_dir is None


def test_load_skills_detects_reference_dir(tmp_path):
    write_skill(tmp_path, "purestorage", reference_files={"api.md": "cheatsheet"})

    skills = load_skills(tmp_path)

    assert skills[0].reference_dir == tmp_path / "purestorage" / "reference"


def test_load_skills_returns_empty_list_for_missing_dir(tmp_path):
    assert load_skills(tmp_path / "does-not-exist") == []


def test_load_skills_skips_folder_without_skill_md(tmp_path):
    (tmp_path / "empty-folder").mkdir()
    write_skill(tmp_path, "purestorage")

    skills = load_skills(tmp_path)

    assert [s.name for s in skills] == ["purestorage"]


def test_load_skills_skips_missing_frontmatter(tmp_path, caplog):
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("just some text, no frontmatter")
    write_skill(tmp_path, "purestorage")

    with caplog.at_level(logging.WARNING):
        skills = load_skills(tmp_path)

    assert [s.name for s in skills] == ["purestorage"]
    assert "broken" in caplog.text


def test_load_skills_skips_missing_name_or_description(tmp_path, caplog):
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: broken\n---\nbody")
    write_skill(tmp_path, "purestorage")

    with caplog.at_level(logging.WARNING):
        skills = load_skills(tmp_path)

    assert [s.name for s in skills] == ["purestorage"]


def test_load_skills_skips_non_boolean_always_on(tmp_path, caplog):
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: broken\ndescription: d\nalways_on: "false"\n---\nbody'
    )
    write_skill(tmp_path, "purestorage")

    with caplog.at_level(logging.WARNING):
        skills = load_skills(tmp_path)

    assert [s.name for s in skills] == ["purestorage"]


def test_load_skills_raises_on_duplicate_name(tmp_path):
    write_skill(tmp_path, "purestorage", name="purestorage")
    write_skill(tmp_path, "purestorage-2", name="purestorage", description="dup")

    with pytest.raises(DuplicateSkillName):
        load_skills(tmp_path)


def test_load_skills_warns_on_large_always_on_instructions(tmp_path, caplog):
    write_skill(tmp_path, "big", always_on=True, body="x" * 25_000)

    with caplog.at_level(logging.WARNING):
        skills = load_skills(tmp_path)

    assert len(skills) == 1
    assert "big" in caplog.text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_skills.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.skills'`

- [ ] **Step 4: Write the implementation**

Create `agent/skills.py`:

```python
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MAX_ALWAYS_ON_INSTRUCTIONS_CHARS = 20_000


class DuplicateSkillName(Exception):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    always_on: bool
    instructions: str
    reference_dir: Path | None


def load_skills(skills_dir: Path) -> list["Skill"]:
    skills: dict[str, Skill] = {}

    if not skills_dir.is_dir():
        return []

    for skill_dir in sorted(skills_dir.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue

        skill = _parse_skill_file(skill_file, skill_dir)
        if skill is None:
            continue

        if skill.name in skills:
            raise DuplicateSkillName(f"duplicate skill name: {skill.name!r}")
        skills[skill.name] = skill

    return list(skills.values())


def _parse_skill_file(skill_file: Path, skill_dir: Path) -> "Skill | None":
    text = skill_file.read_text()
    if not text.startswith("---"):
        logger.warning("skipping skill %r: missing frontmatter", skill_dir.name)
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("skipping skill %r: missing frontmatter", skill_dir.name)
        return None

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        logger.warning("skipping skill %r: invalid YAML frontmatter", skill_dir.name, exc_info=True)
        return None

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name or not description:
        logger.warning("skipping skill %r: missing name or description", skill_dir.name)
        return None

    always_on = frontmatter.get("always_on", False)
    if not isinstance(always_on, bool):
        logger.warning(
            "skipping skill %r: always_on must be a YAML boolean, got %r", skill_dir.name, always_on
        )
        return None

    instructions = parts[2].strip()
    if always_on and len(instructions) > MAX_ALWAYS_ON_INSTRUCTIONS_CHARS:
        logger.warning(
            "skill %r is always_on with %d chars of instructions (over %d) -- "
            "this is appended to every request's prompt",
            skill_dir.name, len(instructions), MAX_ALWAYS_ON_INSTRUCTIONS_CHARS,
        )

    reference_dir = skill_dir / "reference"
    if not reference_dir.is_dir():
        reference_dir = None

    return Skill(
        name=name,
        description=description,
        always_on=always_on,
        instructions=instructions,
        reference_dir=reference_dir,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_skills.py -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add agent/skills.py tests/test_skills.py pyproject.toml uv.lock
git commit -m "Add skill loading (agent/skills.py)"
```

---

### Task 2: `agent/tools/skills.py` — `load_skill` and `read_skill_file` tools

**Files:**
- Create: `agent/tools/skills.py`
- Test: `tests/test_skills_tool.py`

**Interfaces:**
- Consumes: `Skill` from `agent.skills` (Task 1); `Tool`, `guard_errors` from `agent.tools.base`; `resolve_in_sandbox` from `agent.tools.files` (existing).
- Produces: `build_skill_tools(on_demand_skills: list[Skill], max_reference_bytes: int = 1_000_000) -> list[Tool]`. Task 4 imports this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills_tool.py`:

```python
from agent.skills import Skill
from agent.tools.skills import build_skill_tools


def make_skill(tmp_path, name="purestorage", always_on=False, reference_files=None):
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    reference_dir = None
    if reference_files:
        reference_dir = skill_dir / "reference"
        reference_dir.mkdir()
        for filename, content in reference_files.items():
            (reference_dir / filename).write_text(content)
    return Skill(
        name=name,
        description="A skill.",
        always_on=always_on,
        instructions="Full instructions.",
        reference_dir=reference_dir,
    )


def test_build_skill_tools_empty_list_returns_no_tools():
    assert build_skill_tools([]) == []


def test_build_skill_tools_returns_load_and_read_tools(tmp_path):
    skill = make_skill(tmp_path)
    tools = {t.name for t in build_skill_tools([skill])}
    assert tools == {"load_skill", "read_skill_file"}


async def test_load_skill_returns_instructions(tmp_path):
    skill = make_skill(tmp_path)
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["load_skill"].execute({"name": "purestorage"})

    assert result == "Full instructions."


async def test_load_skill_lists_reference_files(tmp_path):
    skill = make_skill(tmp_path, reference_files={"cheatsheet.md": "content"})
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["load_skill"].execute({"name": "purestorage"})

    assert "Full instructions." in result
    assert "cheatsheet.md" in result
    assert "Reference files available" in result


async def test_load_skill_omits_reference_note_when_dir_empty(tmp_path):
    skill_dir = tmp_path / "purestorage"
    skill_dir.mkdir()
    (skill_dir / "reference").mkdir()
    skill = Skill(
        name="purestorage", description="d", always_on=False,
        instructions="Full instructions.", reference_dir=skill_dir / "reference",
    )
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["load_skill"].execute({"name": "purestorage"})

    assert result == "Full instructions."


async def test_load_skill_unknown_name_returns_error(tmp_path):
    skill = make_skill(tmp_path)
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["load_skill"].execute({"name": "nonexistent"})

    assert result.startswith("Error:")


async def test_read_skill_file_returns_content(tmp_path):
    skill = make_skill(tmp_path, reference_files={"cheatsheet.md": "the content"})
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["read_skill_file"].execute({"skill": "purestorage", "path": "cheatsheet.md"})

    assert result == "the content"


async def test_read_skill_file_truncates_large_files(tmp_path):
    skill = make_skill(tmp_path, reference_files={"big.md": "x" * 100})
    tools = {t.name: t for t in build_skill_tools([skill], max_reference_bytes=50)}

    result = await tools["read_skill_file"].execute({"skill": "purestorage", "path": "big.md"})

    assert "truncated at 50 bytes" in result


async def test_read_skill_file_rejects_traversal(tmp_path):
    skill = make_skill(tmp_path, reference_files={"cheatsheet.md": "content"})
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["read_skill_file"].execute({"skill": "purestorage", "path": "../../SKILL.md"})

    assert result.startswith("Error:")


async def test_read_skill_file_unknown_skill_returns_error(tmp_path):
    skill = make_skill(tmp_path)
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["read_skill_file"].execute({"skill": "nonexistent", "path": "x.md"})

    assert result.startswith("Error:")


async def test_read_skill_file_unknown_path_returns_error(tmp_path):
    skill = make_skill(tmp_path, reference_files={"cheatsheet.md": "content"})
    tools = {t.name: t for t in build_skill_tools([skill])}

    result = await tools["read_skill_file"].execute({"skill": "purestorage", "path": "missing.md"})

    assert result.startswith("Error:")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skills_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.tools.skills'`

- [ ] **Step 3: Write the implementation**

Create `agent/tools/skills.py`:

```python
from typing import Any

from agent.skills import Skill
from agent.tools.base import Tool, guard_errors
from agent.tools.files import resolve_in_sandbox

DEFAULT_MAX_REFERENCE_FILE_BYTES = 1_000_000


def build_skill_tools(
    on_demand_skills: list[Skill],
    max_reference_bytes: int = DEFAULT_MAX_REFERENCE_FILE_BYTES,
) -> list[Tool]:
    if not on_demand_skills:
        return []

    skills_by_name = {skill.name: skill for skill in on_demand_skills}

    async def load_skill(args: dict[str, Any]) -> str:
        name = args["name"]
        skill = skills_by_name.get(name)
        if skill is None:
            raise ValueError(f"unknown skill: {name!r}")

        result = skill.instructions

        if skill.reference_dir is not None:
            files = sorted(
                str(p.relative_to(skill.reference_dir))
                for p in skill.reference_dir.rglob("*")
                if p.is_file()
            )
            if files:
                file_list = "\n".join(f"- {f}" for f in files)
                result += (
                    f"\n\nReference files available (use read_skill_file to view one):\n{file_list}"
                )

        return result

    async def read_skill_file(args: dict[str, Any]) -> str:
        name = args["skill"]
        skill = skills_by_name.get(name)
        if skill is None:
            raise ValueError(f"unknown skill: {name!r}")
        if skill.reference_dir is None:
            raise ValueError(f"skill {name!r} has no reference files")

        target = resolve_in_sandbox(skill.reference_dir, args["path"])
        if not target.is_file():
            raise FileNotFoundError(f"not a file: {args['path']}")

        data = target.read_bytes()
        text = data[:max_reference_bytes].decode(errors="replace")
        if len(data) > max_reference_bytes:
            text += f"\n... truncated at {max_reference_bytes} bytes"
        return text

    return [
        Tool(
            name="load_skill",
            description="Load a storage-domain skill's instructions by name.",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            execute=guard_errors(load_skill),
        ),
        Tool(
            name="read_skill_file",
            description="Read one reference file belonging to a loaded skill.",
            parameters={
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Path relative to the skill's reference/ directory.",
                    },
                },
                "required": ["skill", "path"],
            },
            execute=guard_errors(read_skill_file),
        ),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_skills_tool.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add agent/tools/skills.py tests/test_skills_tool.py
git commit -m "Add load_skill and read_skill_file tools"
```

---

### Task 3: Extend `agent/prompt.py::build_system_prompt()`

**Files:**
- Modify: `agent/prompt.py` (entire file — currently 3 lines)
- Modify: `tests/test_prompt.py` (add tests; keep the existing one)

**Interfaces:**
- Consumes: `Skill` from `agent.skills` (Task 1).
- Produces: `build_system_prompt(base_prompt: str, always_on_skills: list[Skill] | None = None, on_demand_skills: list[Skill] | None = None) -> str`. Task 4 calls this with both lists.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_prompt.py` (the existing `test_build_system_prompt_returns_base_prompt_unchanged` stays as-is — new signature is backward compatible via defaults):

```python
from agent.skills import Skill


def make_skill(name, description="d", always_on=False, instructions="do the thing"):
    return Skill(
        name=name, description=description, always_on=always_on,
        instructions=instructions, reference_dir=None,
    )


def test_build_system_prompt_appends_always_on_skill_instructions():
    skill = make_skill("safety", instructions="Always confirm before deleting a volume.")

    result = build_system_prompt("base prompt", always_on_skills=[skill])

    assert "base prompt" in result
    assert "Always confirm before deleting a volume." in result


def test_build_system_prompt_appends_on_demand_catalog():
    skill = make_skill("purestorage", description="PureStorage conventions.")

    result = build_system_prompt("base prompt", on_demand_skills=[skill])

    assert "purestorage" in result
    assert "PureStorage conventions." in result
    assert "load_skill" in result


def test_build_system_prompt_no_catalog_section_when_no_on_demand_skills():
    always_on = make_skill("safety", instructions="Confirm before deleting.")

    result = build_system_prompt("base prompt", always_on_skills=[always_on])

    assert "Available skills" not in result


def test_build_system_prompt_combines_both_kinds():
    always_on = make_skill("safety", instructions="Confirm before deleting.")
    on_demand = make_skill("purestorage", description="PureStorage conventions.")

    result = build_system_prompt("base prompt", always_on_skills=[always_on], on_demand_skills=[on_demand])

    assert "Confirm before deleting." in result
    assert "purestorage" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: FAIL — `TypeError: build_system_prompt() got an unexpected keyword argument 'always_on_skills'`

- [ ] **Step 3: Write the implementation**

Replace `agent/prompt.py` entirely:

```python
from agent.skills import Skill


def build_system_prompt(
    base_prompt: str,
    always_on_skills: list[Skill] | None = None,
    on_demand_skills: list[Skill] | None = None,
) -> str:
    always_on_skills = always_on_skills or []
    on_demand_skills = on_demand_skills or []

    sections = [base_prompt]

    for skill in always_on_skills:
        sections.append(f"## Skill: {skill.name}\n\n{skill.instructions}")

    if on_demand_skills:
        catalog_lines = "\n".join(f"- {s.name} — {s.description}" for s in on_demand_skills)
        sections.append(
            "## Available skills\n\n"
            "Call load_skill(name) to load full instructions for one of these "
            "when relevant to the task:\n\n" + catalog_lines
        )

    return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: 5 passed (the original test plus the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add agent/prompt.py tests/test_prompt.py
git commit -m "Extend build_system_prompt to inject skills"
```

---

### Task 4: Wire skills into `main.py`

**Files:**
- Modify: `settings.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `load_skills` (Task 1), `build_skill_tools` (Task 2), `build_system_prompt` new signature (Task 3).
- Produces: nothing new for later tasks — this is the integration point. Task 5 relies on `SKILLS_PATH` defaulting to `./skills`.

- [ ] **Step 1: Add `SKILLS_PATH` setting**

In `settings.py`, add this line among the other optional path settings (after `MCP_SERVERS_PATH`):

```python
    SKILLS_PATH: str = "./skills"
```

- [ ] **Step 2: Wire skill loading into the lifespan**

In `main.py`, add to the imports:

```python
from agent.prompt import build_system_prompt  # already imported — no change if already present
from agent.skills import load_skills
from agent.tools.skills import build_skill_tools
```

Replace the body of `lifespan()` from the `base_prompt = ...` line through the `app.state.registry = ...` line with:

```python
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
```

(The rest of `lifespan()` — the `llm_client` assignment, `yield`, and shutdown — is unchanged.)

- [ ] **Step 3: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: all tests pass (no existing test touches `main.py` directly, so this is a regression check, not new coverage)

- [ ] **Step 4: Live smoke test — app still starts cleanly with an empty `skills/` directory**

```bash
mkdir -p skills
lsof -ti:8000 -sTCP:LISTEN | xargs -r kill 2>/dev/null
rm -f data/storage_agent.db
uv run uvicorn main:app --port 8000 &> /tmp/storage-agent-run.log &
sleep 3
curl -s -o /dev/null -w "readiness: %{http_code}\n" http://localhost:8000/
grep -i "error\|traceback" /tmp/storage-agent-run.log || echo "no errors in startup log"
lsof -ti:8000 -sTCP:LISTEN | xargs -r kill
```

Expected: `readiness: 200`, no errors in the log (an empty `skills/` directory is valid — `load_skills` returns `[]`).

- [ ] **Step 5: Commit**

```bash
git add settings.py main.py
git commit -m "Wire skills into app startup"
```

---

### Task 5: Example skills and end-to-end verification

**Files:**
- Create: `skills/team-safety-rules/SKILL.md`
- Create: `skills/purestorage/SKILL.md`
- Create: `skills/purestorage/reference/rest-api-cheatsheet.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-25-skills-design.md` (status line only)

**Interfaces:**
- Consumes: the fully wired app from Task 4.
- Produces: nothing further downstream — this is the final task.

- [ ] **Step 1: Create the always-on safety-rules skill**

Create `skills/team-safety-rules/SKILL.md`:

```markdown
---
name: team-safety-rules
description: Universal safety rules for all storage operations.
always_on: true
---

- Always confirm with the user in plain language before deleting, unprovisioning, or resizing down any volume, share, or export — these actions can be destructive and are not easily reversible.
- Never disable an export policy or share access rule without explicit confirmation of the exact policy/rule name and target.
- When unsure which array, filer, or cluster a request applies to, ask rather than guessing.
```

- [ ] **Step 2: Create the on-demand PureStorage skill with a reference file**

Create `skills/purestorage/SKILL.md`:

```markdown
---
name: purestorage
description: PureStorage FlashArray/FlashBlade provisioning, naming conventions, and REST API usage.
always_on: false
---

Volume names follow the pattern `<env>-<app>-<purpose>-<size>` (e.g. `prod-erp-data-500g`).

For FlashArray REST API calls, use the `purestorage_*` tools if configured in the REST allowlist. See reference/rest-api-cheatsheet.md for endpoint details.
```

Create `skills/purestorage/reference/rest-api-cheatsheet.md`:

```markdown
# PureStorage FlashArray REST API quick reference

- `GET /api/2.x/volumes` — list volumes
- `POST /api/2.x/volumes` — create a volume, body: `{"names": ["..."], "provisioned": <bytes>}`
- `DELETE /api/2.x/volumes/<name>` — destroy (soft-delete, recoverable for a period)
```

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass (these example skills aren't referenced by any automated test, only used for the live check below)

- [ ] **Step 4: Live end-to-end verification against the real local model**

```bash
lsof -ti:8000 -sTCP:LISTEN | xargs -r kill 2>/dev/null
rm -f data/storage_agent.db
uv run uvicorn main:app --port 8000 &> /tmp/storage-agent-run.log &
sleep 3
curl -s http://localhost:8000/mcp/status -H "Authorization: Bearer dev-token" > /dev/null
```

Then drive a real chat message that should trigger `load_skill` (adjust the token if `.env`'s `API_TOKEN` differs from `dev-token`):

```bash
uv run python -c "
import asyncio, json
import websockets

async def main():
    async with websockets.connect('ws://127.0.0.1:8000/ws/chat') as ws:
        await ws.send(json.dumps({'token': 'dev-token'}))
        await ws.send(json.dumps({'type': 'message', 'conversation_id': None, 'content': 'What naming convention do we use for PureStorage volumes?'}))
        while True:
            reply = json.loads(await ws.recv())
            print(reply)
            if reply['type'] in ('final', 'error'):
                break

asyncio.run(main())
"
lsof -ti:8000 -sTCP:LISTEN | xargs -r kill
```

Expected: a `tool_call` event for `load_skill` with `{"name": "purestorage"}`, its `tool_result` containing the naming-convention text, and a `final` answer reflecting it. (A small local model may also call `read_skill_file` for the cheatsheet, or may answer directly from `load_skill`'s result — both are correct; the naming convention is in `SKILL.md` itself, so `load_skill` alone is sufficient.)

- [ ] **Step 5: Update README**

In `README.md`, add a row to the environment variables table (after the `MCP_SERVERS_PATH` row):

```markdown
| `SKILLS_PATH` | no | `./skills` | Directory of skill folders (domain knowledge injected into the agent's context). |
```

Add a new subsection after "### MCP servers (`config/mcp_servers.json`)":

```markdown
### Skills (`skills/`)

Each subdirectory of `skills/` is one skill: a `SKILL.md` with YAML frontmatter (`name`, `description`, `always_on`) and a Markdown body, plus an optional `reference/` directory of additional files.

```markdown
---
name: purestorage
description: PureStorage FlashArray/FlashBlade provisioning, naming conventions, and REST API usage.
always_on: false
---

Full instructions here...
```

- `always_on: true` skills are appended to the system prompt on every request — keep these short (safety rules, universal conventions).
- `always_on: false` skills appear only as a name + description in the system prompt; the agent calls `load_skill(name)` to pull in the full instructions when relevant, and `read_skill_file(skill, path)` to read a specific file under that skill's `reference/` directory. This keeps large reference material (API cheat-sheets, CLI references) out of every prompt by default.

Changes require a restart.
```

- [ ] **Step 6: Update the design spec's status line**

In `docs/superpowers/specs/2026-09-25-skills-design.md`, change:

```markdown
Status: design approved, not yet implemented.
```

to:

```markdown
Status: implemented.
```

- [ ] **Step 7: Commit**

```bash
git add skills/ README.md docs/superpowers/specs/2026-09-25-skills-design.md
git commit -m "Add example skills, README docs, mark skills spec implemented"
```
