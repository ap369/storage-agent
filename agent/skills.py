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
    # Resolve to absolute up front: a relative skills_dir (e.g. the SKILLS_PATH
    # default "./skills") would otherwise leak into Skill.reference_dir as a
    # relative path, and resolve_in_sandbox() always rejects an absolute resolved
    # candidate against a relative root, breaking read_skill_file entirely.
    skills_dir = Path(skills_dir).resolve()
    skills: dict[str, Skill] = {}

    if not skills_dir.is_dir():
        return []

    for skill_dir in sorted(skills_dir.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue

        try:
            skill = _parse_skill_file(skill_file, skill_dir)
        except Exception:
            # a single unreadable/unparseable SKILL.md must never take down the
            # whole app at startup -- same resilience convention as a misconfigured
            # MCP server in agent/tools/mcp.py
            logger.warning("skipping skill %r: failed to parse", skill_dir.name, exc_info=True)
            continue
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
