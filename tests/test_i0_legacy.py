"""I0.6: legacy state (approved/done items, a baseline subtask), as it behaves today.

The healthy legacy project must stay green with no warning: that is what 0.6 protects
when I1, I4 and I16 add rules derived from state. The `pins(...)` tests record today's
fail-open behaviour on that same state; each issue flips its own pins explicitly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from i0_cycle import SID, git, hook
from i0_legacy import BASELINE_SPEC, ORACLE, build_legacy, copy_legacy
from i0_projects import Installed
from test_m0b_inventory import Repo, _fake_codex, _sealed

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

STATUS = "feature_list.json"
# The one WARN a healthy legacy project may show: the changelog is opt-in and the human
# activates it (docs/design/releases.md §1). Any other WARN, or this one twice, fails.
NOT_ACTIVATED = re.compile(
    r"\s*\[WARN\]\s+changelog not activated: the human runs bash \S+/install\.sh (\S+)"
    r" --changelog \(dry-run; then --apply\)\. Claude never runs it"
)


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    return build_legacy(tmp_path_factory.mktemp("legacy"))


@pytest.fixture
def project(legacy: Installed, tmp_path: Path) -> Installed:
    return copy_legacy(legacy, tmp_path / "copy")


def _check(project: Installed) -> tuple[int, str]:
    res = project.make("check")
    return res.returncode, res.stdout + res.stderr


def _unexpected_warnings(root: Path, out: str) -> list[str]:
    """The [WARN]/[FAIL] lines of `out`, without at most one changelog activation WARN."""
    lines = [ln for ln in out.splitlines() if "[WARN]" in ln or "[FAIL]" in ln]
    for i, ln in enumerate(lines):
        if (m := NOT_ACTIVATED.fullmatch(ln)) and m[1] == str(root.resolve()):
            return lines[:i] + lines[i + 1 :]
    return lines


def _leaves(project: Installed) -> dict[str, str]:
    data = json.loads((project.root / STATUS).read_text())
    return {str(s["id"]): str(s["status"]) for f in data["features"] for s in f["subtasks"]}


# ── the healthy legacy state ─────────────────────────────────────────────────


def test_legacy_project_is_green_without_warnings(project: Installed, tmp_path: Path) -> None:
    """With its sealed legacy inventory (plan 0.6), as real projects will have it."""
    _sealed(Repo(project.root, project.repo, _fake_codex(tmp_path, project.env)))
    code, out = _check(project)
    assert code == 0, out
    assert _unexpected_warnings(project.root, out) == [], out


@pytest.mark.parametrize(
    "extra",
    [
        "[WARN]  migration level 0 < required 1: run it",
        "[FAIL]  something broke",
        "[WARN]  changelog not activated: the human runs bash /t/install.sh {root} --changelog"
        " (dry-run; then --apply). Claude never runs it",  # a second one
        "[WARN]  changelog not activated: the human runs bash /t/install.sh /other --changelog"
        " (dry-run; then --apply). Claude never runs it",  # another project
        "[WARN]  changelog not activated: the human runs bash /t/install.sh {root} --changelog"
        " (dry-run; then --apply). Claude never runs it; and more",
    ],
)
def test_only_the_changelog_activation_warning_is_tolerated(tmp_path: Path, extra: str) -> None:
    root = str(tmp_path.resolve())
    activation = (
        f"       [WARN]  changelog not activated: the human runs bash /t/install.sh {root}"
        " --changelog (dry-run; then --apply). Claude never runs it"
    )
    assert _unexpected_warnings(tmp_path, f"[OK]    a\n{activation}\n[OK]    b\n") == []
    out = f"{activation}\n       {extra.format(root=root)}\n"
    assert _unexpected_warnings(tmp_path, out) == [f"       {extra.format(root=root)}"]


def test_legacy_state_has_a_done_leaf_and_a_baseline(project: Installed) -> None:
    assert _leaves(project) == {SID: "done", "1.2.1": "baseline"}
    assert (project.root / BASELINE_SPEC).is_file()
    for seal in ("plan_roadmap", "plan_phase_01", f"acceptance_{SID}"):
        assert (project.root / f"progress/{seal}.sha256").is_file(), seal
    assert git(project, "status", "--porcelain") == ""


@pytest.mark.parametrize("rel", ["src/demo/greet.py", "src/demo/hello.py"])
def test_guard_blocks_src_with_nothing_in_progress(project: Installed, rel: str) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(project.root / rel)}}
    res = hook(project, "guard_write.sh", json.dumps(payload))
    assert res.returncode == 2
    assert "no in_progress feature" in res.stdout + res.stderr


def test_guard_keeps_the_legacy_oracle_read_only(project: Installed) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(project.root / ORACLE)}}
    assert hook(project, "guard_write.sh", json.dumps(payload)).returncode == 2


# ── I1: seals fail open ──────────────────────────────────────────────────────


@pytest.mark.pins("I1")
@pytest.mark.parametrize(
    ("seal", "warning"),
    [
        ("plan_roadmap", "the roadmap is approved but not sealed"),
        ("plan_phase_01", "phase 01 is approved but not sealed"),
    ],
)
def test_a_missing_plan_seal_only_warns_today(project: Installed, seal: str, warning: str) -> None:
    """I1 keeps this as WARN (plan 0.5); the later FAIL issue flips it."""
    (project.root / f"progress/{seal}.sha256").unlink()
    git(project, "commit", "-qam", f"drop {seal}")
    code, out = _check(project)
    assert code == 0, out
    assert warning in out


@pytest.mark.pins("I1")
@pytest.mark.parametrize("committed", [False, True])
def test_a_deleted_oracle_seal_of_a_done_leaf_passes_today(
    project: Installed, committed: bool
) -> None:
    """I1 reports the lost seal as WARN (plan 0.5); the later FAIL issue flips it."""
    (project.root / f"progress/acceptance_{SID}.sha256").unlink()
    if committed:
        git(project, "commit", "-qam", "drop the oracle seal")
    code, out = _check(project)
    assert code == 0, out
    assert "sealed acceptance tests intact (0 seal(s))" in out
    assert f"acceptance tests of {SID} are not sealed" in out


# ── I4: oracle execution integrity ───────────────────────────────────────────


def test_the_sealed_oracle_catches_broken_code(project: Installed) -> None:
    """Control for the I4 pins: with nothing hidden, the same broken code is red."""
    (project.root / "src/demo/greet.py").write_text('"""Broken."""\n')
    git(project, "commit", "-qam", "break greet")
    code, out = _check(project)
    assert code != 0, out


@pytest.mark.pins("I4")
@pytest.mark.parametrize(
    ("conftest", "text"),
    [
        ("tests/conftest.py", 'collect_ignore_glob = ["acceptance/*"]\n'),
        ("tests/acceptance/conftest.py", 'collect_ignore_glob = ["*.py"]\n'),
    ],
)
def test_an_uncollected_sealed_oracle_is_green_today(
    project: Installed, conftest: str, text: str
) -> None:
    """Known wrong: a conftest hides the sealed oracle from pytest and nothing notices."""
    (project.root / conftest).write_text(text)
    (project.root / "src/demo/greet.py").write_text('"""Broken."""\n')  # oracle would fail
    git(project, "add", "-A")
    git(project, "commit", "-qm", "hide the oracle")
    code, out = _check(project)
    assert code == 0, out


# ── I10: check scripts fail closed on helper failure ─────────────────────────


@pytest.mark.parametrize(
    ("script", "ok"),
    [
        ("gate.sh", "gates consistent"),
        ("doc_gate.sh", "reference docs complete"),
    ],
)
def test_check_scripts_fail_when_done_ids_fails(project: Installed, script: str, ok: str) -> None:
    """An unreadable feature_list.json: the done list is unknown, so the gate fails
    (it was OK before I10, pinned in I0)."""
    (project.root / STATUS).write_text("{not json")
    res = project.run("bash", f"harness/{script}")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "[FAIL]" in res.stdout + res.stderr and "done" in res.stdout + res.stderr
    assert ok not in res.stdout
