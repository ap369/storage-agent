import pytest

from agent.tools.files import build_file_tools


@pytest.fixture
def sandbox_root(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    return root


@pytest.fixture
def tools(sandbox_root):
    return {t.name: t for t in build_file_tools(sandbox_root)}


async def test_write_then_read_round_trip(tools):
    write_result = await tools["write_file"].execute({"path": "notes.txt", "content": "hello"})
    assert "notes.txt" in write_result

    read_result = await tools["read_file"].execute({"path": "notes.txt"})
    assert read_result == "hello"


async def test_write_creates_parent_dirs(tools, sandbox_root):
    await tools["write_file"].execute({"path": "a/b/c.txt", "content": "deep"})
    assert (sandbox_root / "a" / "b" / "c.txt").read_text() == "deep"


async def test_read_missing_file_returns_error(tools):
    result = await tools["read_file"].execute({"path": "missing.txt"})
    assert result.startswith("Error:")


async def test_read_rejects_traversal(tools):
    result = await tools["read_file"].execute({"path": "../outside.txt"})
    assert result.startswith("Error:")


async def test_list_dir_shows_entries(tools, sandbox_root):
    (sandbox_root / "one.txt").write_text("1")
    (sandbox_root / "nested").mkdir()
    (sandbox_root / "nested" / "two.txt").write_text("2")

    top_level = await tools["list_dir"].execute({"path": "."})
    assert "one.txt" in top_level
    assert "nested" in top_level
    assert "two.txt" not in top_level

    recursive = await tools["list_dir"].execute({"path": ".", "recursive": True})
    assert "two.txt" in recursive


async def test_search_files_finds_matching_lines(tools, sandbox_root):
    (sandbox_root / "a.txt").write_text("hello world\nfoo bar\n")
    (sandbox_root / "b.txt").write_text("nothing here\n")

    result = await tools["search_files"].execute({"pattern": "hello"})

    assert "a.txt:1" in result
    assert "b.txt" not in result


async def test_search_files_no_matches(tools, sandbox_root):
    (sandbox_root / "a.txt").write_text("nothing to see\n")
    result = await tools["search_files"].execute({"pattern": "zzz"})
    assert result == "(no matches)"


async def test_edit_file_replaces_unique_match(tools, sandbox_root):
    (sandbox_root / "f.txt").write_text("foo bar baz")
    await tools["edit_file"].execute({"path": "f.txt", "old_string": "bar", "new_string": "qux"})
    assert (sandbox_root / "f.txt").read_text() == "foo qux baz"


async def test_edit_file_zero_matches_errors(tools, sandbox_root):
    (sandbox_root / "f.txt").write_text("foo bar baz")
    result = await tools["edit_file"].execute(
        {"path": "f.txt", "old_string": "nope", "new_string": "x"}
    )
    assert result.startswith("Error:")
    assert (sandbox_root / "f.txt").read_text() == "foo bar baz"


async def test_edit_file_multiple_matches_errors(tools, sandbox_root):
    (sandbox_root / "f.txt").write_text("bar bar")
    result = await tools["edit_file"].execute(
        {"path": "f.txt", "old_string": "bar", "new_string": "x"}
    )
    assert result.startswith("Error:")
    assert (sandbox_root / "f.txt").read_text() == "bar bar"


async def test_delete_file(tools, sandbox_root):
    (sandbox_root / "f.txt").write_text("bye")
    await tools["delete_file"].execute({"path": "f.txt"})
    assert not (sandbox_root / "f.txt").exists()


async def test_delete_non_empty_dir_refused(tools, sandbox_root):
    (sandbox_root / "d").mkdir()
    (sandbox_root / "d" / "f.txt").write_text("x")
    result = await tools["delete_file"].execute({"path": "d"})
    assert result.startswith("Error:")
    assert (sandbox_root / "d" / "f.txt").exists()


async def test_move_file(tools, sandbox_root):
    (sandbox_root / "src.txt").write_text("content")
    await tools["move_file"].execute({"src": "src.txt", "dest": "dest.txt"})
    assert not (sandbox_root / "src.txt").exists()
    assert (sandbox_root / "dest.txt").read_text() == "content"


async def test_move_file_rejects_traversal_on_either_side(tools, sandbox_root):
    (sandbox_root / "src.txt").write_text("content")

    result = await tools["move_file"].execute({"src": "src.txt", "dest": "../escaped.txt"})
    assert result.startswith("Error:")
    assert (sandbox_root / "src.txt").exists()

    result = await tools["move_file"].execute({"src": "../escaped.txt", "dest": "dest.txt"})
    assert result.startswith("Error:")
