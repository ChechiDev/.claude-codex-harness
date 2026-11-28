"""Releases + CHANGELOG, delivery 1 (docs/design/releases.md): declarations, fragments,
the make check rules, the consolidation and the release plan (docs/releases.md)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan

sys.path.insert(0, str(TOOLKIT / "lib"))
import _changelog  # noqa: E402
import _close_scope  # noqa: E402
import _releases  # noqa: E402

DECL = "- changelog: added\n- incompatible: no\n"
FRAGMENT = "- id: {id}\n- incompatible: no\n\n### Added\n- A new thing.\n"


def _git(root: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return res.stdout.strip()


def _repo(plan: Plan) -> Plan:
    _git(plan.root, "init", "-q", "-b", "main")
    _git(plan.root, "config", "user.email", "t@example.com")
    _git(plan.root, "config", "user.name", "t")
    return plan


def _commit(plan: Plan, msg: str = "wip") -> None:
    _git(plan.root, "add", "-A")
    _git(plan.root, "commit", "-qm", msg)


def _activate(plan: Plan, finished: list[str] | None = None, since: str | None = None) -> Plan:
    record = {"version": 1, "since": since, "finished": finished or []}
    path = plan.root / ".harness" / "changelog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    (plan.root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
    (plan.root / "changelog.d").mkdir(exist_ok=True)
    return plan


def _spec(plan: Plan, sid: str) -> Path:
    found = sorted((plan.root / "docs" / "plan").rglob(f"{sid}-s.md"))
    assert found, sid
    return found[0]


def _declare(plan: Plan, sid: str, text: str = DECL) -> None:
    spec = _spec(plan, sid)
    body = spec.read_text(encoding="utf-8").replace("- acceptance:", text + "- acceptance:", 1)
    spec.write_text(body, encoding="utf-8")


def _resync(plan: Plan) -> None:
    """make plan, keeping the spec files as edited (plan.sync() rewrites them)."""
    res = plan.run_py("sync_plan.py")
    assert res.returncode == 0, res.stdout + res.stderr


def _fragment(plan: Plan, ident: str, text: str | None = None) -> Path:
    path = plan.root / "changelog.d" / f"{ident}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(FRAGMENT.format(id=ident) if text is None else text, encoding="utf-8")
    return path


def _task_plan(plan: Plan, status: str = "pending") -> Plan:
    """Phase 01 task-flow, task 1.1 approved with one subtask 1.1.1."""
    plan.approve_stack().phase("01", "1.1").sub("1.1.1", status).approve_roadmap()
    plan.approve_phase("01").verdict("task_review_1.1.md", "TASK_APPROVED")
    return plan.sync()


def _complete(plan: Plan, sid: str = "1.1.1") -> None:
    spec = _spec(plan, sid)
    text = spec.read_text(encoding="utf-8")
    spec.write_text(text.replace("- status: in_progress", "- status: done"), encoding="utf-8")
    _resync(plan)


# ── declarations ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "kinds", "none", "incompatible"),
    [
        ("- changelog: added, fixed\n- incompatible: no\n", ("added", "fixed"), "", ""),
        ("- changelog: none: internal\n- incompatible: no\n", (), "internal", ""),
        ("- changelog: removed\n- incompatible: yes: drops -o\n", ("removed",), "", "drops -o"),
    ],
)
def test_declaration_parses(
    text: str, kinds: tuple[str, ...], none: str, incompatible: str
) -> None:
    decl = _changelog.parse_declaration(text)
    assert decl is not None
    assert decl.kinds == kinds
    assert decl.none == none
    assert decl.incompatible == incompatible


def test_spec_without_declaration_parses_to_none() -> None:
    assert _changelog.parse_declaration("# 1.1.1 x\n- status: pending\n") is None


@pytest.mark.parametrize(
    "text",
    [
        "- changelog: improved\n- incompatible: no\n",
        "- changelog: none\n- incompatible: no\n",
        "- changelog: added\n",
        "- changelog: added\n- incompatible: yes\n",
        "- changelog: added\n- incompatible: maybe\n",
        "- incompatible: no\n",
        "- changelog: added, none: x\n- incompatible: no\n",
    ],
)
def test_malformed_declaration_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        _changelog.parse_declaration(text)


def test_task_gate_needs_the_declaration_in_an_activated_project(plan: Plan) -> None:
    _activate(_task_plan(plan))
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 1
    assert "1.1.1" in res.stdout and "changelog:" in res.stdout
    _declare(plan, "1.1.1")
    _resync(plan)
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 0, res.stdout + res.stderr


def test_task_gate_unchanged_without_activation(plan: Plan) -> None:
    _task_plan(plan)
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 0, res.stdout + res.stderr


def test_baseline_spec_needs_no_declaration(plan: Plan) -> None:
    _activate(_task_plan(plan, "baseline"))
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 0, res.stdout + res.stderr


def _commit_at(plan: Plan, msg: str, when: str) -> None:
    env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    _git(plan.root, "add", "-A")
    subprocess.run(["git", "commit", "-qm", msg], cwd=plan.root, env=env, check=True)


def _three_leaf_task(plan: Plan) -> Plan:
    """Task 1.1 approved before any activation: 1.1.1 done, 1.1.2 in progress, 1.1.3 to do."""
    plan.approve_stack().phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2", "in_progress")
    plan.sub("1.1.3").approve_roadmap().approve_phase("01")
    return plan.verdict("task_review_1.1.md", "TASK_APPROVED").sync()


def test_done_spec_needs_no_declaration_on_a_regate(plan: Plan) -> None:
    """A closed spec is never edited for the changelog (review rel1rev #3)."""
    _activate(_three_leaf_task(plan))
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 1
    assert "1.1.1" not in res.stdout and "1.1.2" in res.stdout and "1.1.3" in res.stdout


@pytest.mark.parametrize("branch", ["main", "feat/1.1-x"])
def test_regate_exempts_specs_approved_before_the_activation(plan: Plan, branch: str) -> None:
    """docs/design/releases.md §3: specs approved before activation are never re-gated
    for it, on the base branch or on a task branch in flight (review rel1rev #3)."""
    _three_leaf_task(_repo(plan))
    _commit_at(plan, "kickoff 1.1", "2026-01-01T10:00:00")
    _git(plan.root, "switch", "-qc", "feat/1.1-x")
    _git(plan.root, "switch", "-q", "main")
    _activate(plan)
    _commit_at(plan, "activate", "2026-01-02T10:00:00")
    _git(plan.root, "switch", "-q", branch)
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 0, res.stdout + res.stderr


def test_regate_asks_a_spec_written_after_the_activation(plan: Plan) -> None:
    _three_leaf_task(_repo(plan))
    _commit_at(plan, "kickoff 1.1", "2026-01-01T10:00:00")
    _activate(plan)
    _commit_at(plan, "activate", "2026-01-02T10:00:00")
    plan.sub("1.1.4").sync()  # replan: a new spec after the activation
    _commit_at(plan, "replan 1.1", "2026-01-03T10:00:00")
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 1
    assert "1.1.4" in res.stdout
    assert not any(f"1.1.{k}" in res.stdout for k in (1, 2, 3)), res.stdout


def _whole_phase(plan: Plan) -> Plan:
    """Phase 01 broken down whole (legacy flow): its specs go through the phase gate."""
    return plan.approve_stack().phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()


def test_whole_phase_gate_needs_the_declaration(plan: Plan) -> None:
    """No task gate in that flow: phase-ready asks for it (review rel1rev #5)."""
    _activate(_whole_phase(plan))
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 1
    assert "1.1.1" in res.stdout and "changelog:" in res.stdout
    _declare(plan, "1.1.1")
    _resync(plan)
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 0, res.stdout + res.stderr


def test_whole_phase_gate_unchanged_without_activation(plan: Plan) -> None:
    _whole_phase(plan)
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 0, res.stdout + res.stderr


def test_task_flow_phase_gate_asks_no_declaration(plan: Plan) -> None:
    """In the task flow the task gate asks for it, not the phase gate."""
    _activate(plan.approve_stack().phase("01", "1.1").approve_roadmap().sync())
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 0, res.stdout + res.stderr


def test_issue_gate_needs_the_declaration(plan: Plan) -> None:
    plan.approve_stack().issue(3).isub("ISS-3.1").sync()
    _activate(plan)
    res = plan.run_py("_harness.py", "issue-ready", "ISS-3")
    assert res.returncode == 1 and "ISS-3.1" in res.stdout
    _declare(plan, "ISS-3.1", "- changelog: fixed\n- incompatible: no\n")
    _resync(plan)
    assert plan.run_py("_harness.py", "issue-ready", "ISS-3").returncode == 0


def test_malformed_declaration_fails_make_check_when_activated(plan: Plan) -> None:
    _activate(_task_plan(plan))
    _declare(plan, "1.1.1", "- changelog: improved\n- incompatible: no\n")
    _resync(plan)
    res = plan.validate()
    assert res.returncode == 1
    assert "1.1.1" in res.stdout and "improved" in res.stdout


# ── fragments ─────────────────────────────────────────────────────────────────


def test_fragment_parses() -> None:
    text = (
        "- id: 1.2\n- incompatible: yes: the config key moved\n\n"
        "### Added\n- Export to CSV.\n\n### Fixed\n- Crash on empty input.\n"
    )
    frag = _changelog.parse_fragment(text, "1.2")
    assert frag.ident == "1.2"
    assert frag.incompatible == "the config key moved"
    assert frag.entries == {"Added": ["Export to CSV."], "Fixed": ["Crash on empty input."]}


def test_none_fragment_parses() -> None:
    text = "- id: ISS-4\n- incompatible: no\n- none: tests only\n"
    frag = _changelog.parse_fragment(text, "ISS-4")
    assert frag.none == "tests only" and frag.entries == {}


@pytest.mark.parametrize(
    "text",
    [
        "- id: 9.9\n- incompatible: no\n\n### Added\n- x\n",  # id mismatch
        "- id: 1.2\n\n### Added\n- x\n",  # no incompatible line
        "- id: 1.2\n- incompatible: no\n",  # nothing at all
        "- id: 1.2\n- incompatible: no\n- none: x\n\n### Added\n- y\n",  # none + entries
        "- id: 1.2\n- incompatible: no\n\n### Improved\n- y\n",  # unknown section
        "- id: 1.2\n- incompatible: no\n\n### Added\n",  # empty section
        "- id: 1.2\n- incompatible: no\n\nloose text\n### Added\n- y\n",  # stray text
    ],
)
def test_invalid_fragment_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        _changelog.parse_fragment(text, "1.2")


# ── make check: the fragment at the last close ────────────────────────────────


def _closing(plan: Plan) -> Plan:
    """Activated, task 1.1 in progress committed, then its close sets it done in the tree."""
    _activate(_task_plan(_repo(plan), "in_progress"))
    _commit(plan, "start")
    _complete(plan)
    return plan


def test_last_close_without_fragment_fails(plan: Plan) -> None:
    res = _closing(plan).validate()
    assert res.returncode == 1
    assert "changelog.d/1.1.md" in res.stdout


def test_last_close_with_fragment_passes(plan: Plan) -> None:
    _fragment(_closing(plan), "1.1")
    res = plan.validate()
    assert res.returncode == 0, res.stdout


def test_last_close_with_invalid_fragment_fails(plan: Plan) -> None:
    _fragment(_closing(plan), "1.1", "- id: 1.1\n")
    res = plan.validate()
    assert res.returncode == 1 and "changelog.d/1.1.md" in res.stdout


def test_consolidated_fragment_counts(plan: Plan) -> None:
    _closing(plan)
    (plan.root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n- A thing. (1.1)\n\n"
        "<!-- changelog.d/1.1.md -->\n",
        encoding="utf-8",
    )
    assert plan.validate().returncode == 0


# ── the close's own fragment (doc repair, review rel1rev #4) ──────────────────


def _owed(plan: Plan) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_changelog.py", "fragment", "1.1.1")


def test_fragment_owed_by_the_last_close_is_named(plan: Plan) -> None:
    res = _owed(_closing(plan))
    assert res.returncode == 1
    assert "changelog.d/1.1.md" in res.stdout and "missing" in res.stdout


def test_invalid_fragment_of_the_last_close_is_named(plan: Plan) -> None:
    _fragment(_closing(plan), "1.1", "- id: 1.1\n")
    res = _owed(plan)
    assert res.returncode == 1 and "invalid" in res.stdout


@pytest.mark.parametrize("case", ["valid", "consolidated", "unactivated", "not-last", "earlier"])
def test_no_fragment_owed(plan: Plan, case: str) -> None:
    if case == "unactivated":
        _task_plan(_repo(plan), "in_progress")
        _commit(plan, "start")
        _complete(plan)
    elif case == "not-last":
        _repo(plan).approve_stack().phase("01", "1.1").sub("1.1.1", "in_progress").sub("1.1.2")
        plan.approve_roadmap().approve_phase("01").verdict("task_review_1.1.md", "TASK_APPROVED")
        _activate(plan.sync())
        _commit(plan, "start")
        _complete(plan)
    elif case == "earlier":  # finished in HEAD already: only a make check WARN
        _closing(plan)
        _commit(plan, "close")
    else:
        _closing(plan)
    if case == "valid":
        _fragment(plan, "1.1")
    if case == "consolidated":
        (plan.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [Unreleased]\n\n<!-- changelog.d/1.1.md -->\n", encoding="utf-8"
        )
    res = _owed(plan)
    assert res.returncode == 0, res.stdout + res.stderr


def test_task_finished_earlier_without_fragment_only_warns(plan: Plan) -> None:
    _closing(plan)
    _commit(plan, "close")
    res = plan.validate()
    assert res.returncode == 0, res.stdout
    assert "[WARN]" in res.stdout and "changelog.d/1.1.md" in res.stdout


def test_task_finished_before_activation_is_silent(plan: Plan) -> None:
    _closing(plan)
    _activate(plan, finished=["1.1"])
    _commit(plan, "close")
    res = plan.validate()
    assert res.returncode == 0 and "changelog" not in res.stdout


def test_unactivated_project_needs_no_fragment(plan: Plan) -> None:
    _task_plan(_repo(plan), "in_progress")
    _commit(plan, "start")
    _complete(plan)
    res = plan.validate()
    assert res.returncode == 0, res.stdout
    assert "changelog" not in res.stdout.lower()


def test_activation_on_the_base_branch_reaches_a_task_branch(plan: Plan) -> None:
    _task_plan(_repo(plan), "in_progress")
    _commit(plan, "start")
    _git(plan.root, "switch", "-qc", "feat/1.1-x")
    _git(plan.root, "switch", "-q", "main")
    _activate(plan)
    _commit(plan, "activate")
    _git(plan.root, "switch", "-q", "feat/1.1-x")
    assert not (plan.root / ".harness" / "changelog.json").exists()
    _complete(plan)
    res = plan.validate()
    assert res.returncode == 1 and "changelog.d/1.1.md" in res.stdout


def test_all_baseline_task_needs_no_fragment(plan: Plan) -> None:
    _activate(_repo(plan))
    _commit(plan, "activate")
    _task_plan(plan, "baseline")
    res = plan.validate()
    assert res.returncode == 0, res.stdout


def test_issue_last_close_needs_its_fragment(plan: Plan) -> None:
    _repo(plan).approve_stack().issue(3).isub("ISS-3.1", "in_progress").approve_issue(3).sync()
    _activate(plan)
    _commit(plan, "start")
    _complete(plan, "ISS-3.1")
    res = plan.validate()
    assert res.returncode == 1 and "changelog.d/ISS-3.md" in res.stdout
    _fragment(plan, "ISS-3")
    assert plan.validate().returncode == 0


def test_due_tells_the_documenter_what_to_write(plan: Plan) -> None:
    _activate(_task_plan(_repo(plan), "in_progress"))
    _declare(plan, "1.1.1", "- changelog: added, fixed\n- incompatible: no\n")
    _resync(plan)
    res = plan.run_py("_changelog.py", "due", "1.1.1")
    assert res.returncode == 0, res.stderr
    assert "changelog.d/1.1.md" in res.stdout and "added, fixed" in res.stdout


def test_due_is_silent_for_a_close_that_does_not_finish_the_task(plan: Plan) -> None:
    plan.approve_stack().phase("01", "1.1").sub("1.1.1", "in_progress").sub("1.1.2")
    plan.approve_roadmap().approve_phase("01").verdict("task_review_1.1.md", "TASK_APPROVED")
    _activate(plan.sync())
    res = plan.run_py("_changelog.py", "due", "1.1.1")
    assert res.returncode == 1 and res.stdout == ""


def test_close_manifest_admits_only_its_own_fragment() -> None:
    leaf = {"id": "1.1.2", "phase": "01", "issue": "", "files": [], "spec": "", "migrates": []}
    manifest = _close_scope.Manifest("1.1.2", leaf)
    assert manifest.allows("changelog.d/1.1.md")
    assert not manifest.allows("changelog.d/1.2.md")
    assert not manifest.allows("CHANGELOG.md")
    issue = {**leaf, "id": "ISS-7.1", "phase": "", "issue": "ISS-7"}
    assert _close_scope.Manifest("ISS-7.1", issue).allows("changelog.d/ISS-7.md")


# ── consolidation ─────────────────────────────────────────────────────────────


def _consolidate(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_changelog.py", "consolidate", *args)


@pytest.mark.parametrize(
    "argv",
    [
        ["--apply", "consolidate"],
        ["--yes", "activate", ".", "--apply"],
        ["--apply", "activate", "."],
        ["--apply", "install", ".", "templates"],
        ["--yes", "due", "1.1"],
    ],
)
def test_flags_before_the_subcommand_are_a_usage_error(plan: Plan, argv: list[str]) -> None:
    """The subcommand comes first (review rel1rev #1): anything else changes nothing."""
    _fragment(_activate(plan), "1.1")
    before = (plan.root / "CHANGELOG.md").read_text()
    res = plan.run_py("_changelog.py", *argv)
    assert res.returncode == 2, res.stdout + res.stderr
    assert (plan.root / "CHANGELOG.md").read_text() == before
    assert (plan.root / "changelog.d" / "1.1.md").exists()


def test_consolidation_dry_run_writes_nothing(plan: Plan) -> None:
    _fragment(_activate(plan), "1.1")
    before = (plan.root / "CHANGELOG.md").read_text()
    res = _consolidate(plan)
    assert res.returncode == 0, res.stderr
    assert "A new thing. (1.1)" in res.stdout and "--apply" in res.stdout
    assert (plan.root / "CHANGELOG.md").read_text() == before
    assert (plan.root / "changelog.d" / "1.1.md").exists()


def test_consolidation_groups_by_section_and_is_idempotent(plan: Plan) -> None:
    _activate(plan)
    _fragment(plan, "1.2", "- id: 1.2\n- incompatible: no\n\n### Fixed\n- Fix B.\n")
    _fragment(
        plan,
        "1.10",
        "- id: 1.10\n- incompatible: yes: the --out flag is gone\n\n### Removed\n- Drop --out.\n",
    )
    _fragment(plan, "ISS-3", "- id: ISS-3\n- incompatible: no\n\n### Fixed\n- Fix C.\n")
    _fragment(plan, "1.3", "- id: 1.3\n- incompatible: no\n- none: tests only\n")
    assert _consolidate(plan, "--apply").returncode == 0
    text = (plan.root / "CHANGELOG.md").read_text()
    assert text.index("### Incompatible") < text.index("### Removed") < text.index("### Fixed")
    assert "- the --out flag is gone (1.10)" in text
    assert text.index("Fix B. (1.2)") < text.index("Fix C. (ISS-3)")
    for ident in ("1.2", "1.10", "ISS-3", "1.3"):
        assert text.count(f"<!-- changelog.d/{ident}.md ") == 1
    assert "tests only" not in text
    assert list((plan.root / "changelog.d").glob("*.md")) == []
    assert _consolidate(plan, "--apply").returncode == 0
    assert (plan.root / "CHANGELOG.md").read_text() == text


def test_consolidation_after_an_interruption_never_duplicates(plan: Plan) -> None:
    _fragment(_activate(plan), "1.1")
    assert _consolidate(plan, "--apply").returncode == 0
    text = (plan.root / "CHANGELOG.md").read_text()
    _fragment(plan, "1.1")  # the delete never happened
    assert _consolidate(plan, "--apply").returncode == 0
    assert (plan.root / "CHANGELOG.md").read_text() == text
    assert not (plan.root / "changelog.d" / "1.1.md").exists()


def test_consolidation_appends_to_existing_sections(plan: Plan) -> None:
    _activate(plan)
    (plan.root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n- Old. (1.0)\n\n## [0.1.0] - 2026-01-01\n"
        "\n### Added\n- First.\n",
        encoding="utf-8",
    )
    _fragment(plan, "1.1")
    assert _consolidate(plan, "--apply").returncode == 0
    text = (plan.root / "CHANGELOG.md").read_text()
    assert text.count("### Added") == 2
    assert text.index("- Old. (1.0)") < text.index("- A new thing. (1.1)") < text.index("[0.1.0]")


def test_consolidation_adds_a_missing_unreleased_heading(plan: Plan) -> None:
    _activate(plan)
    (plan.root / "CHANGELOG.md").write_text(
        "# Changelog\n\nOur history.\n\n## 0.1.0\n- First.\n", encoding="utf-8"
    )
    _fragment(plan, "1.1")
    assert _consolidate(plan, "--apply").returncode == 0
    text = (plan.root / "CHANGELOG.md").read_text()
    assert text.index("Our history.") < text.index("## [Unreleased]") < text.index("## 0.1.0")
    assert "- A new thing. (1.1)" in text


def test_invalid_fragment_stops_the_consolidation(plan: Plan) -> None:
    _fragment(_activate(plan), "1.1")
    _fragment(plan, "1.2", "- id: 1.2\n")
    before = (plan.root / "CHANGELOG.md").read_text()
    res = _consolidate(plan, "--apply")
    assert res.returncode == 1 and "changelog.d/1.2.md" in (res.stdout + res.stderr)
    assert (plan.root / "CHANGELOG.md").read_text() == before
    assert (plan.root / "changelog.d" / "1.1.md").exists()


def test_consolidation_refuses_an_unactivated_project(plan: Plan) -> None:
    res = _consolidate(plan, "--apply")
    assert res.returncode == 1 and "--changelog" in (res.stdout + res.stderr)


# ── the release plan (docs/releases.md) ───────────────────────────────────────

RELEASES = """# Releases
- applies: yes
- versioning: semver
- version source: {source}
- audience: the ops team

## Planned releases
| release | phases | goal |
|---------|--------|------|
| 0.1.0   | 01     | first usable CLI |

## Compatibility
SemVer; in 0.x an incompatible change bumps MINOR.

## Patches and early releases
A patch for any fix that cannot wait for the next phase.
"""


def _releases_doc(plan: Plan, text: str | None = None, source: str = "VERSION") -> Path:
    path = plan.root / "docs" / "releases.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RELEASES.format(source=source) if text is None else text, encoding="utf-8")
    return path


def test_template_is_a_draft() -> None:
    for tset in ("templates", "templates-c"):
        text = (TOOLKIT / tset / "releases.md").read_text(encoding="utf-8")
        problems = _releases.ready_problems_text(text, python=tset == "templates")
        assert any("placeholder" in p for p in problems), tset


def test_filled_plan_is_ready(plan: Plan) -> None:
    _releases_doc(plan)
    assert plan.run_py("_releases.py", "ready").returncode == 0


def test_not_applicable_needs_only_its_reason(plan: Plan) -> None:
    _releases_doc(plan, "# Releases\n- applies: no: an internal script, never distributed\n")
    assert plan.run_py("_releases.py", "ready").returncode == 0
    _releases_doc(plan, "# Releases\n- applies: no\n")
    assert plan.run_py("_releases.py", "ready").returncode == 1


def test_version_source_follows_the_stack(plan: Plan) -> None:
    (plan.root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n')
    _releases_doc(plan, source="VERSION")
    res = plan.run_py("_releases.py", "ready")
    assert res.returncode == 1 and "pyproject.toml" in res.stdout + res.stderr
    _releases_doc(plan, source="pyproject.toml")
    assert plan.run_py("_releases.py", "ready").returncode == 0


def test_missing_section_is_not_ready(plan: Plan) -> None:
    _releases_doc(plan, RELEASES.format(source="VERSION").split("## Compatibility")[0])
    res = plan.run_py("_releases.py", "ready")
    assert res.returncode == 1 and "Compatibility" in res.stdout + res.stderr


def _approve_releases(plan: Plan) -> None:
    plan.verdict("releases_review.md", "RELEASES_APPROVED")
    res = plan.run_py("_releases.py", "seal")
    assert res.returncode == 0, res.stdout + res.stderr


def test_states(plan: Plan) -> None:
    root = plan.root
    assert _releases.state(root) == _releases.MISSING
    _releases_doc(plan, (TOOLKIT / "templates" / "releases.md").read_text())
    assert _releases.state(root) == _releases.DRAFT
    _releases_doc(plan)
    assert _releases.state(root) == _releases.UNREVIEWED
    plan.verdict("releases_review.md", "CHANGES_REQUESTED")
    assert _releases.state(root) == _releases.REJECTED_STATE
    plan.verdict("releases_review.md", "RELEASES_APPROVED")
    assert _releases.state(root) == _releases.UNSEALED
    _approve_releases(plan)
    assert _releases.state(root) == _releases.OK_STATE
    _releases_doc(plan, RELEASES.format(source="VERSION") + "\nmore\n")
    assert _releases.state(root) == _releases.CHANGED


def test_seal_needs_the_approval(plan: Plan) -> None:
    _releases_doc(plan)
    assert plan.run_py("_releases.py", "seal").returncode == 1
    assert not (plan.root / "progress" / "releases.sha256").exists()


def test_seal_records_the_plan_and_the_roadmap_seal(plan: Plan) -> None:
    _releases_doc(plan)
    roadmap = plan.root / "progress" / "plan_roadmap.sha256"
    roadmap.write_text("abc  docs/plan/PLAN.md\n")
    _approve_releases(plan)
    lines = (plan.root / "progress" / "releases.sha256").read_text().splitlines()
    doc = hashlib.sha256((plan.root / "docs" / "releases.md").read_bytes()).hexdigest()
    assert lines[0] == f"{doc}  docs/releases.md"
    assert lines[1] == f"{hashlib.sha256(roadmap.read_bytes()).hexdigest()}  {roadmap.name}"


def _validated(plan: Plan) -> str:
    res = plan.validate()
    assert res.returncode == 0, res.stdout
    return res.stdout


def test_changed_plan_only_warns(plan: Plan) -> None:
    _activate(_task_plan(plan))
    _releases_doc(plan)
    _approve_releases(plan)
    assert "releases" not in _validated(plan)
    _releases_doc(plan, RELEASES.format(source="VERSION") + "\nmore\n")
    out = _validated(plan)
    assert "[WARN]" in out and "docs/releases.md changed" in out


def test_roadmap_change_warns_for_a_review(plan: Plan) -> None:
    _activate(_task_plan(plan))
    roadmap = plan.root / "progress" / "plan_roadmap.sha256"
    roadmap.write_text("abc  docs/plan/PLAN.md\n")
    _releases_doc(plan)
    _approve_releases(plan)
    roadmap.write_text("def  docs/plan/PLAN.md\n")
    out = _validated(plan)
    assert "roadmap changed" in out and "docs/releases.md" in out


def test_unplanned_incompatible_change_warns(plan: Plan) -> None:
    _activate(_task_plan(plan))
    _releases_doc(plan)
    _approve_releases(plan)
    _fragment(plan, "1.1", "- id: 1.1\n- incompatible: yes: x\n\n### Changed\n- y\n")
    out = _validated(plan)
    assert "incompatible" in out and "changelog.d/1.1.md" in out


def test_release_plan_silent_without_activation(plan: Plan) -> None:
    _task_plan(plan)
    _releases_doc(plan, RELEASES.format(source="VERSION") + "\nmore\n")
    plan.verdict("releases_review.md", "RELEASES_APPROVED")
    assert "releases" not in _validated(plan)


# ── make status / make check level ────────────────────────────────────────────


def _status(plan: Plan) -> str:
    res = plan.run_py("plan_status.py")
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def test_status_of_an_unactivated_project_has_no_changelog_line(plan: Plan) -> None:
    _task_plan(plan)
    out = _status(plan)
    assert "Changelog" not in out and "Releases" not in out and "Also:" not in out


def test_status_asks_for_the_release_discovery_once_the_roadmap_is_approved(plan: Plan) -> None:
    _activate(_task_plan(plan))
    out = _status(plan)
    assert "Releases   no docs/releases.md" in out
    assert "Also: release discovery" in out
    next_line = next(line for line in out.splitlines() if line.startswith("Next:"))
    assert "release" not in next_line


def test_status_shows_the_approved_release_plan(plan: Plan) -> None:
    _activate(_task_plan(plan))
    _releases_doc(plan)
    _approve_releases(plan)
    out = _status(plan)
    assert "Releases   RELEASES_APPROVED" in out and "Also:" not in out
    assert "Changelog  activated" in out


def test_level_check_warns_with_the_activation_command(plan: Plan) -> None:
    res = plan.run_py("_upgrade.py", "level", str(plan.root))
    assert res.returncode == 0
    assert "changelog not activated" in res.stdout and "--changelog" in res.stdout
    _activate(plan)
    res = plan.run_py("_upgrade.py", "level", str(plan.root))
    assert "changelog not activated" not in res.stdout


def test_resume_warns_about_a_changed_release_plan(plan: Plan) -> None:
    _task_plan(plan)
    _releases_doc(plan)
    _approve_releases(plan)
    _releases_doc(plan, RELEASES.format(source="VERSION") + "\nmore\n")
    res = plan.run_py("_resume.py")
    assert "docs/releases.md changed" not in res.stderr  # not activated: as before
    _activate(plan)
    res = plan.run_py("_resume.py")
    assert "[WARN]" in res.stderr and "docs/releases.md changed" in res.stderr


def test_consolidation_never_deletes_a_fragment_it_did_not_read(
    plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fragment(_activate(plan), "1.1")
    real = _changelog.merge

    def merge_while_a_close_lands(text: str, frags: list[_changelog.Fragment]) -> str:
        _fragment(plan, "1.2")  # written by a close between the read and the delete
        (plan.root / "changelog.d" / "1.1.md").write_text(
            FRAGMENT.format(id="1.1") + "### Fixed\n- Late edit.\n"
        )
        return real(text, frags)

    monkeypatch.setattr(_changelog, "merge", merge_while_a_close_lands)
    assert _changelog.consolidate(plan.root, apply=True) == 0
    assert (plan.root / "changelog.d" / "1.2.md").exists()
    assert "Late edit." in (plan.root / "changelog.d" / "1.1.md").read_text()
    assert "(1.2)" not in (plan.root / "CHANGELOG.md").read_text()
    # The edited 1.1 was consolidated in its old form: the next run refuses to drop it.
    res = _consolidate(plan, "--apply")
    assert res.returncode == 1 and "changelog.d/1.1.md changed" in res.stderr
    assert (plan.root / "changelog.d" / "1.1.md").exists()
