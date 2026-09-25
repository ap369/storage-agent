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
                        "description": (
                            "Path relative to the skill's reference/ directory -- do NOT "
                            "include a leading 'reference/'. Example: 'cheatsheet.md', "
                            "not 'reference/cheatsheet.md'. Use the exact filename from "
                            "load_skill's 'Reference files available' list."
                        ),
                    },
                },
                "required": ["skill", "path"],
            },
            execute=guard_errors(read_skill_file),
        ),
    ]
