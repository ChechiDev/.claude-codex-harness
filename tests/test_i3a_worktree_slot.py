"""I3a (D): the verdict slot of a linked worktree lies outside the root; still codex-owned."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Project, bash_payload, write_payload
from i3a_helpers import BLOCKED, git


@pytest.fixture
def linked(project: Project, tmp_path: Path) -> Project:
    wt = tmp_path / "wt"
    git(project.root, "worktree", "add", "-q", str(wt), "-b", "feat")
    (wt / "harness").symlink_to((project.root / "harness").resolve())
    (wt / ".claude").mkdir(exist_ok=True)
    return Project(wt)


@pytest.mark.parametrize("name", ["harness-verdict-slot", "harness-verdict-partial"])
@pytest.mark.parametrize("inner", ["", "/x.md"])
def test_linked_worktree_slot_is_codex_owned(linked: Project, name: str, inner: str) -> None:
    raw = git(linked.root, "rev-parse", "--git-path", name)
    slot = (linked.root / raw).resolve()
    assert not slot.is_relative_to(linked.root)  # precondition: outside the root
    target = f"{slot}{inner}"
    for hook, payload in (
        ("guard_write.sh", write_payload(target)),
        ("guard_bash.sh", bash_payload(f"echo x > {target}")),
    ):
        res = linked.run(hook, payload)
        assert res.returncode == BLOCKED, (hook, target, res.stderr)
