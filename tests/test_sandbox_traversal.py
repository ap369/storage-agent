import pytest

from agent.tools.files import SandboxViolation, resolve_in_sandbox


@pytest.fixture
def sandbox_root(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "nested").mkdir()
    return root


def test_accepts_simple_relative_path(sandbox_root):
    resolved = resolve_in_sandbox(sandbox_root, "notes.txt")
    assert resolved == sandbox_root / "notes.txt"


def test_accepts_nested_relative_path(sandbox_root):
    resolved = resolve_in_sandbox(sandbox_root, "nested/notes.txt")
    assert resolved == sandbox_root / "nested" / "notes.txt"


def test_accepts_sandbox_root_itself(sandbox_root):
    resolved = resolve_in_sandbox(sandbox_root, ".")
    assert resolved == sandbox_root


def test_rejects_parent_traversal(sandbox_root):
    with pytest.raises(SandboxViolation):
        resolve_in_sandbox(sandbox_root, "../secret.txt")


def test_rejects_nested_parent_traversal(sandbox_root):
    with pytest.raises(SandboxViolation):
        resolve_in_sandbox(sandbox_root, "nested/../../secret.txt")


def test_rejects_absolute_path(sandbox_root):
    with pytest.raises(SandboxViolation):
        resolve_in_sandbox(sandbox_root, "/etc/passwd")


def test_rejects_symlink_escape(sandbox_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    (sandbox_root / "escape").symlink_to(outside)

    with pytest.raises(SandboxViolation):
        resolve_in_sandbox(sandbox_root, "escape/secret.txt")
