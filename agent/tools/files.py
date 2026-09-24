import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from agent.tools.base import Tool, guard_errors

DEFAULT_MAX_READ_BYTES = 1_000_000
DEFAULT_MAX_SEARCH_MATCHES = 200


class SandboxViolation(Exception):
    pass


def resolve_in_sandbox(sandbox_root: Path, user_path: str) -> Path:
    if PurePosixPath(user_path).is_absolute():
        raise SandboxViolation(f"absolute paths are not allowed: {user_path!r}")

    if ".." in Path(user_path).parts:
        raise SandboxViolation(f"parent traversal is not allowed: {user_path!r}")

    resolved = (sandbox_root / user_path).resolve(strict=False)

    if resolved != sandbox_root and not resolved.is_relative_to(sandbox_root):
        raise SandboxViolation(f"path escapes the sandbox: {user_path!r}")

    return resolved


def build_file_tools(
    sandbox_root: Path,
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
    max_search_matches: int = DEFAULT_MAX_SEARCH_MATCHES,
) -> list[Tool]:
    def _resolve(user_path: str) -> Path:
        return resolve_in_sandbox(sandbox_root, user_path)

    async def list_dir(args: dict[str, Any]) -> str:
        raw_path = args.get("path", ".")
        target = _resolve(raw_path)
        if not target.is_dir():
            raise NotADirectoryError(f"not a directory: {raw_path}")
        entries = target.rglob("*") if args.get("recursive") else target.iterdir()
        lines = sorted(
            f"{'dir ' if p.is_dir() else 'file'}  {p.relative_to(sandbox_root)}" for p in entries
        )
        return "\n".join(lines) if lines else "(empty)"

    async def search_files(args: dict[str, Any]) -> str:
        target = _resolve(args.get("path", "."))
        regex = re.compile(args["pattern"])
        matches: list[str] = []
        truncated = False
        for file_path in sorted(target.rglob("*")):
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    if len(matches) >= max_search_matches:
                        truncated = True
                        break
                    rel = file_path.relative_to(sandbox_root)
                    matches.append(f"{rel}:{lineno}: {line.strip()}")
            if truncated:
                break
        if not matches:
            return "(no matches)"
        result = "\n".join(matches)
        if truncated:
            result += f"\n... truncated at {max_search_matches} matches"
        return result

    async def read_file(args: dict[str, Any]) -> str:
        raw_path = args["path"]
        target = _resolve(raw_path)
        if not target.is_file():
            raise FileNotFoundError(f"not a file: {raw_path}")
        data = target.read_bytes()
        text = data[:max_read_bytes].decode(errors="replace")
        if len(data) > max_read_bytes:
            text += f"\n... truncated at {max_read_bytes} bytes"
        return text

    async def write_file(args: dict[str, Any]) -> str:
        target = _resolve(args["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"])
        return f"wrote {target.relative_to(sandbox_root)}"

    async def edit_file(args: dict[str, Any]) -> str:
        raw_path = args["path"]
        target = _resolve(raw_path)
        old_string = args["old_string"]
        new_string = args["new_string"]
        text = target.read_text()
        count = text.count(old_string)
        if count == 0:
            raise ValueError(f"old_string not found in {raw_path}")
        if count > 1:
            raise ValueError(f"old_string is not unique in {raw_path} ({count} occurrences)")
        target.write_text(text.replace(old_string, new_string, 1))
        return f"edited {target.relative_to(sandbox_root)}"

    async def delete_file(args: dict[str, Any]) -> str:
        raw_path = args["path"]
        target = _resolve(raw_path)
        if target.is_dir():
            if any(target.iterdir()):
                raise IsADirectoryError(f"refusing to delete non-empty directory: {raw_path}")
            target.rmdir()
        else:
            target.unlink()
        return f"deleted {raw_path}"

    async def move_file(args: dict[str, Any]) -> str:
        raw_src = args["src"]
        raw_dest = args["dest"]
        src = _resolve(raw_src)
        dest = _resolve(raw_dest)
        if not src.exists():
            raise FileNotFoundError(f"not found: {raw_src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return f"moved {raw_src} -> {raw_dest}"

    return [
        Tool(
            name="list_dir",
            description="List entries in a directory within the sandbox.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path, relative to the sandbox root.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "List the full tree instead of one level.",
                    },
                },
            },
            execute=guard_errors(list_dir),
        ),
        Tool(
            name="search_files",
            description="Search file contents within the sandbox using a regex pattern.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for."},
                    "path": {
                        "type": "string",
                        "description": "Directory to search under, relative to the sandbox root.",
                    },
                },
                "required": ["pattern"],
            },
            execute=guard_errors(search_files),
        ),
        Tool(
            name="read_file",
            description="Read a file's contents from the sandbox.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            execute=guard_errors(read_file),
        ),
        Tool(
            name="write_file",
            description="Create or overwrite a file in the sandbox.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            execute=guard_errors(write_file),
        ),
        Tool(
            name="edit_file",
            description="Replace an exact, unique substring in a sandbox file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            execute=guard_errors(edit_file),
        ),
        Tool(
            name="delete_file",
            description="Delete a file or empty directory in the sandbox.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            execute=guard_errors(delete_file),
        ),
        Tool(
            name="move_file",
            description="Move or rename a file within the sandbox.",
            parameters={
                "type": "object",
                "properties": {"src": {"type": "string"}, "dest": {"type": "string"}},
                "required": ["src", "dest"],
            },
            execute=guard_errors(move_file),
        ),
    ]
