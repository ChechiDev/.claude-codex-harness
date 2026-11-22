"""Issue #107: the #44 review policy at the phase, task and issue gates, and I7.

1. The phase, task and issue gates review the specs of their subtasks before any of
   them reaches its own plan gate. They take the effort and the checklists of the
   strictest rating among those subtasks (each one resolved as in #44: spec, sealed
   rating and files:), never below the role's effort. An invalid rating of an open
   subtask stops the gate; a done/baseline one is exempt, as in `make check`.
2. I7: the close message is removed only after a successful commit. A failed or
   killed commit leaves progress/close_<id>.msg in place, so the retry (`close`)
   reuses it instead of regenerating it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import _review_risk
import pytest
from conftest import DECLARED, LEAF_ID, TOOLKIT, Plan, Project, bind_code_approval, write_control

CODEX = TOOLKIT / "codex"
HIGH = "- risk: high\n- surfaces: security\n"


# ── the policy of a phase / task / issue gate ───────────────────────────────


def _sub(sid: str, folder: str, rating: str, root: Path, **extra: object) -> dict[str, object]:
    spec = f"{folder}/{sid}-s.md"
    (root / folder).mkdir(parents=True, exist_ok=True)
    status = str(extra.pop("status", "pending"))
    (root / spec).write_text(f"# {sid} s\n- status: {status}\n{rating}", encoding="utf-8")
    files = extra.pop("files", [f"src/app/m{sid.replace('.', '')}.py"])
    return {"id": sid, "title": "s", "status": status, "files": files, "spec": spec, **extra}


def _plan(root: Path, ratings: dict[str, str], toml: str = "", **status: str) -> None:
    """Phase 01 with tasks 1.1 (1.1.1, 1.1.2) and 1.2 (1.2.1), issue ISS-3 (ISS-3.1)."""
    # Outside docs/plan/: without it the gates read feature_list.json, as here.
    folder = "specs/phase-01"
    ifolder = "specs/ISS-3"

    def sub(sid: str, where: str) -> dict[str, object]:
        st = status.get(sid.replace(".", "_").replace("-", "_"), "pending")
        return _sub(sid, where, ratings.get(sid, ""), root, status=st)

    tasks = [
        {"id": "1.1", "phase": "01", "title": "t", "subtasks": [sub("1.1.1", folder),
                                                                 sub("1.1.2", folder)]},
        {"id": "1.2", "phase": "01", "title": "t", "subtasks": [sub("1.2.1", folder)]},
    ]  # fmt: skip
    issue = {"id": "ISS-3", "title": "i", "subtasks": [sub("ISS-3.1", ifolder)]}
    data = {"phases": [{"id": "01", "title": "p"}], "features": tasks, "issues": [issue]}
    (root / "feature_list.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "harness.toml").write_text(toml, encoding="utf-8")


def _gate(root: Path, gate: str, cid: str, base: str = "high") -> _review_risk.Policy:
    return _review_risk.gate_policy(root, gate, cid, base, CODEX)


def _names(policy: _review_risk.Policy) -> list[str]:
    return [p.name for p in policy.checklists]


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(("gate", "cid"), [("phase", "01"), ("task", "1.1"), ("issue", "ISS-3")])
def test_an_unrated_container_reviews_as_before(here: Path, gate: str, cid: str) -> None:
    _plan(here, {})
    assert _gate(here, gate, cid) == _review_risk.Policy("high", (), ())


def test_the_phase_gate_takes_the_strictest_rating_of_its_subtasks(here: Path) -> None:
    _plan(here, {"1.1.1": HIGH, "1.2.1": "- surfaces: data\n"})
    policy = _gate(here, "phase", "01")
    assert policy.effort == "xhigh"
    assert set(_names(policy)) == {
        "checklist-high-risk.md",
        "checklist-security.md",
        "checklist-data.md",
    }
    assert all(p.parent == CODEX for p in policy.checklists)
    assert any(r.startswith("1.1.1: risk high") for r in policy.reasons)
    assert any(r.startswith("1.2.1: surface data") for r in policy.reasons)


def test_the_task_gate_takes_only_its_own_subtasks(here: Path) -> None:
    _plan(here, {"1.1.2": "- surfaces: data\n", "1.2.1": HIGH})
    policy = _gate(here, "task", "1.1")
    assert policy.effort == "high"
    assert _names(policy) == ["checklist-data.md"]
    assert not any(r.startswith("1.2.1") for r in policy.reasons)
    assert _gate(here, "task", "1.2").effort == "xhigh"


def test_the_issue_gate_takes_only_its_own_subtasks(here: Path) -> None:
    _plan(here, {"1.1.1": HIGH, "ISS-3.1": "- surfaces: migrations\n"})
    policy = _gate(here, "issue", "ISS-3")
    assert policy.effort == "xhigh"  # migrations raises it, not 1.1.1
    assert _names(policy) == ["checklist-migrations.md"]
    assert _gate(here, "phase", "01").effort == "xhigh"
    assert "checklist-migrations.md" not in _names(_gate(here, "phase", "01"))


def test_a_container_never_goes_below_the_role_effort(here: Path) -> None:
    _plan(here, {"1.1.1": "- risk: low\n"}, '[review.risk.low]\ncodex_effort = "low"\n')
    assert _gate(here, "phase", "01", base="high").effort == "high"


def test_the_files_of_a_subtask_raise_its_container(here: Path) -> None:
    _plan(here, {}, '[sessions]\nhigh_risk_paths = ["src/app/m112.py"]\n')
    policy = _gate(here, "task", "1.1")
    assert policy.effort == "xhigh"
    assert "checklist-high-risk.md" in _names(policy)
    assert any(r.startswith("1.1.2: risk high (files:") for r in policy.reasons)


def test_an_invalid_rating_of_an_open_subtask_stops_the_container(here: Path) -> None:
    _plan(here, {"1.1.2": "- surfaces: securty\n"})
    for gate, cid in (("phase", "01"), ("task", "1.1")):
        with pytest.raises(_review_risk.RatingError, match="securty"):
            _gate(here, gate, cid)


def test_a_done_subtask_with_an_old_rating_is_exempt(here: Path) -> None:
    _plan(here, {"1.1.1": "- risk: very\n", "1.1.2": HIGH}, **{"1_1_1": "done"})
    policy = _gate(here, "phase", "01")
    assert policy.effort == "xhigh"
    assert "checklist-security.md" in _names(policy)


def test_a_valid_rating_of_a_done_subtask_still_counts(here: Path) -> None:
    _plan(here, {"1.1.1": HIGH}, **{"1_1_1": "done"})
    assert _gate(here, "phase", "01").effort == "xhigh"


def test_a_container_without_subtasks_has_no_rating(here: Path) -> None:
    _plan(here, {})
    assert _gate(here, "phase", "02") == _review_risk.Policy("high", (), ())


def test_the_leaf_gates_keep_the_policy_of_issue_44(here: Path) -> None:
    _plan(here, {"1.1.1": HIGH})
    for gate in ("plan", "code"):
        assert _gate(here, gate, "1.1.1") == _review_risk.policy(here, "1.1.1", "high", CODEX)
    with pytest.raises(_review_risk.RatingError):
        _gate(here, "roadmap", "roadmap")


def test_a_done_subtask_with_a_missing_checklist_is_exempt(here: Path) -> None:
    toml = '[review.surfaces.payments]\nchecklists = ["nope"]\n'
    _plan(here, {"1.1.1": "- surfaces: payments\n", "1.1.2": HIGH}, toml, **{"1_1_1": "done"})
    assert _gate(here, "task", "1.1").effort == "xhigh"
    _plan(here, {"1.1.1": "- surfaces: payments\n"}, toml)
    with pytest.raises(_review_risk.RatingError, match="checklist-nope.md"):
        _gate(here, "task", "1.1")


# ── the CLI reads docs/plan/ itself for a container gate ────────────────────

TOOL = TOOLKIT / "lib" / "_review_risk.py"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(TOOL), "gate-policy", *args, "high", str(CODEX)],
        cwd=root, text=True, capture_output=True, check=False,
    )  # fmt: skip


def _rate_spec(path: Path, lines: str) -> None:
    path.write_text(path.read_text().replace("- layer: domain\n", f"- layer: domain\n{lines}", 1))


def test_the_gate_policy_cli(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").issue(3).isub("ISS-3.1").approve_roadmap().sync()
    _rate_spec(plan.root / "docs/plan/issues/ISS-3-i3/ISS-3.1-s.md", "- surfaces: data\n")
    assert plan.run_py("sync_plan.py").returncode == 0
    res = _cli(plan.root, "issue", "ISS-3")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == "high"
    assert lines[1].startswith("ISS-3.1: surface data")
    assert lines[2:] == [str(CODEX / "checklist-data.md")]
    bad = _cli(plan.root, "doc", "1.1.1")
    assert bad.returncode != 0 and bad.stdout == ""


def test_a_stale_index_cannot_hide_a_spec_from_its_container(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()
    # A rated spec written after the last `make plan`: feature_list.json lacks it.
    folder = plan.root / "docs/plan/phase-01-p01"
    text = (folder / "1.1.1-s.md").read_text().replace("1.1.1", "1.1.2")
    (folder / "1.1.2-s.md").write_text(text)
    _rate_spec(folder / "1.1.2-s.md", HIGH)
    assert "1.1.2" not in (plan.root / "feature_list.json").read_text()
    for gate, cid in (("phase", "01"), ("task", "1.1")):
        res = _cli(plan.root, gate, cid)
        assert res.returncode == 0, res.stderr
        assert res.stdout.splitlines()[0] == "xhigh"
        assert "1.1.2: risk high (declared)" in res.stdout


def test_a_stale_status_cannot_exempt_an_open_subtask(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().sync()
    spec = plan.root / "docs/plan/phase-01-p01/1.1.1-s.md"
    spec.write_text(spec.read_text().replace("- status: done", "- status: pending"))
    _rate_spec(spec, "- surfaces: securty\n")
    res = _cli(plan.root, "phase", "01")
    assert res.returncode == 1 and "securty" in res.stderr


def test_a_plan_that_does_not_parse_stops_the_container(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()
    spec = plan.root / "docs/plan/phase-01-p01/1.1.1-s.md"
    spec.write_text(spec.read_text().replace("- status: pending", "- status: bogus"))
    res = _cli(plan.root, "phase", "01")
    assert res.returncode == 1
    assert "docs/plan does not parse" in res.stderr and res.stdout == ""


# ── the gates (manual mode prints the codex command and prompt) ─────────────

ISSUE_HEADER = (
    "# ISS-14 Replace the mask\n- issue: https://github.com/acme/app/issues/14\n"
    "- type: refactor\n- origin: 1.1\n- priority: next\n- goal: g.\n"
)
ISSUE_SPEC = (
    "# ISS-14.1 Flags\n- status: pending\n- layer: domain\n- depends: none\n{rating}"
    "- files:\n  - src/pkg/domain/flags.py\n"
    "- acceptance:\n  - AC1 | works | tests/acceptance/test_ISS-14.1.py::test_ac1\n"
)
LEAF_SPEC = f"docs/plan/phase-01-users/{LEAF_ID}-user-entity.md"


def _manual(project: Project, *args: str) -> subprocess.CompletedProcess[str]:
    write_control(project.root, '[codex]\nmode = "manual"\n')
    return project.run("review_with_codex.sh", None, *args, env={"HARNESS_CHECK_CMD": "true"})


def _with_issue(project: Project, rating: str) -> Project:
    project.write("docs/plan/issues/ISS-14-replace-the-mask.md", ISSUE_HEADER)
    project.write(
        "docs/plan/issues/ISS-14-replace-the-mask/ISS-14.1-flags.md",
        ISSUE_SPEC.format(rating=rating),
    )
    res = subprocess.run(
        ["python3", "harness/sync_plan.py"], cwd=project.root, text=True,
        capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 0, res.stdout + res.stderr
    return project


PHASE_FILE = "# Phase 01 — Users\n- goal: g.\n\n## 1.1 Users\n- layer: domain\n- goal: g.\n"
LEAF_TEXT = (
    f"# {LEAF_ID} User entity\n- status: in_progress\n- layer: domain\n- depends: none\n"
    "{rating}- files:\n  - src/pkg/domain/user.py\n"
    f"- acceptance:\n  - AC1 | works | tests/acceptance/test_{LEAF_ID}.py::test_ac1\n"
)


def _rate_leaf(project: Project, rating: str) -> None:
    """Phase 01 in docs/plan/ (the source the container gates read), synced."""
    project.write("docs/plan/PLAN.md", "# Project plan\n\n## Vision\nv.\n")
    project.write("docs/plan/phase-01-users.md", PHASE_FILE)
    project.write(LEAF_SPEC, LEAF_TEXT.format(rating=rating))
    res = subprocess.run(
        ["python3", "harness/sync_plan.py"], cwd=project.root, text=True,
        capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 0, res.stdout + res.stderr


def _assert_rated(project: Project, out: str) -> None:
    assert "model_reasoning_effort=xhigh" in out
    assert "Review policy (issue #44)" in out
    for name in ("high-risk", "security"):
        assert f"{project.root}/.claude/codex/checklist-{name}.md" in out
    # At these gates there is no code yet: the items apply to the specs.
    assert "apply every item to the subtask specs under review" in out


@pytest.mark.e2e
def test_the_phase_gate_gets_the_effort_and_checklists(project: Project) -> None:
    _rate_leaf(project, HIGH)
    res = _manual(project, "phase", "01")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Phase: phase" in res.stdout
    _assert_rated(project, res.stdout)
    assert f"{LEAF_ID}: risk high (declared)" in res.stdout


@pytest.mark.e2e
def test_the_task_gate_gets_the_effort_and_checklists(project: Project) -> None:
    _rate_leaf(project, HIGH)
    project.write("progress/task_review_1.1.md", "# Task\n\n**Verdict:** CHANGES_REQUESTED\n")
    res = _manual(project, "task", "1.1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Phase: task" in res.stdout
    _assert_rated(project, res.stdout)


@pytest.mark.e2e
def test_the_issue_gate_gets_the_effort_and_checklists(project: Project) -> None:
    res = _manual(_with_issue(project, HIGH), "issue", "ISS-14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Phase: issue" in res.stdout
    _assert_rated(project, res.stdout)
    assert "ISS-14.1: risk high (declared)" in res.stdout


@pytest.mark.e2e
@pytest.mark.parametrize("gate", ["phase", "issue"])
def test_an_unrated_container_gate_reviews_as_before(project: Project, gate: str) -> None:
    if gate == "issue":
        _with_issue(project, "")
    res = _manual(project, gate, "01" if gate == "phase" else "ISS-14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "model_reasoning_effort=high" in res.stdout
    assert "xhigh" not in res.stdout
    assert "checklist-" not in res.stdout
    assert "Review policy" not in res.stdout


def test_an_invalid_rating_stops_the_issue_gate(project: Project) -> None:
    res = _manual(_with_issue(project, "- surfaces: securty\n"), "issue", "ISS-14")
    assert res.returncode == 1
    assert "securty" in res.stderr
    assert "codex exec" not in res.stdout


# ── I7: the close message survives a failed or killed commit ────────────────

SUBJECT = f"feat(domain): add user entity [{LEAF_ID}]"
MSG = f"progress/close_{LEAF_ID}.msg"


def _git(project: Project, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project.root, text=True, capture_output=True, check=True
    ).stdout


@pytest.fixture
def closing(project: Project) -> Project:
    project.set_verdict("code", "APPROVED")
    project.write(DECLARED, "class User: ...\n")
    bind_code_approval(project.root, LEAF_ID)
    project.write(MSG, SUBJECT + "\n")
    return project


def _close(project: Project) -> subprocess.CompletedProcess[str]:
    return project.run(
        "review_with_codex.sh", None, "close", LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )


def _hook(project: Project, body: str) -> None:
    hook = project.root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)


# A pre-commit hook's parent is git, git's parent the gate.
KILL_GATE = 'kill -{sig} "$(ps -o ppid= -p "$PPID" | tr -d " ")"\nsleep 1\nexit 1\n'
KILL_GIT = 'kill -{sig} "$PPID"\nsleep 1\nexit 1\n'


def _backup(project: Project) -> Path:
    return project.root / f".git/harness-close-{LEAF_ID}.msg"


def _retry_commits(project: Project) -> None:
    (project.root / ".git/hooks/pre-commit").unlink()
    res = _close(project)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _git(project, "log", "-1", "--format=%s") == SUBJECT + "\n"
    assert not (project.root / MSG).exists()
    assert not _backup(project).exists()
    assert MSG not in _git(project, "show", "--name-only", "--format=", "HEAD").split()


@pytest.mark.e2e
@pytest.mark.parametrize(("target", "sig"), [(KILL_GATE, "TERM"), (KILL_GIT, "TERM"),
                                             (KILL_GIT, "KILL")])  # fmt: skip
def test_a_signal_during_the_commit_puts_the_message_back(
    closing: Project, target: str, sig: str
) -> None:
    _hook(closing, target.format(sig=sig))
    head = _git(closing, "rev-parse", "HEAD")
    assert _close(closing).returncode != 0
    assert _git(closing, "rev-parse", "HEAD") == head
    assert (closing.root / MSG).read_text() == SUBJECT + "\n"
    assert not _backup(closing).exists()
    _retry_commits(closing)


@pytest.mark.e2e
def test_a_killed_gate_leaves_the_message_for_the_retry(closing: Project) -> None:
    # SIGKILL of the gate itself: no handler runs, the message waits in .git/.
    _hook(closing, KILL_GATE.format(sig="KILL"))
    head = _git(closing, "rev-parse", "HEAD")
    assert _close(closing).returncode != 0
    assert _git(closing, "rev-parse", "HEAD") == head
    assert _backup(closing).read_text() == SUBJECT + "\n"
    _retry_commits(closing)  # the next close restores it and commits it


def _doc(project: Project) -> subprocess.CompletedProcess[str]:
    write_control(project.root, '[codex]\nmode = "manual"\n')
    return project.run(
        "review_with_codex.sh", None, "doc", LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )


@pytest.mark.e2e
@pytest.mark.parametrize("hook", [KILL_GATE.format(sig="KILL"), "exit 1\n"])
def test_the_doc_gate_never_regenerates_a_waiting_message(closing: Project, hook: str) -> None:
    # Killed (the message waits in .git/) or failed (it is back in progress/): every
    # later doc run refuses and points to close; the message is never regenerated.
    _hook(closing, hook)
    assert _close(closing).returncode != 0
    for _ in range(2):
        res = _doc(closing)
        assert res.returncode != 0
        assert f"review_with_codex.sh close {LEAF_ID}" in res.stderr
        assert "codex exec" not in res.stdout
        assert (closing.root / MSG).read_text() == SUBJECT + "\n"
        assert not _backup(closing).exists()
    _retry_commits(closing)


@pytest.mark.e2e
def test_a_hook_that_stages_everything_never_commits_the_message(closing: Project) -> None:
    _hook(closing, "git add -A\nexit 0\n")
    res = _close(closing)
    assert res.returncode == 0, res.stdout + res.stderr
    assert MSG not in _git(closing, "show", "--name-only", "--format=", "HEAD").split()
    assert not (closing.root / MSG).exists()
    assert not _backup(closing).exists()


@pytest.mark.e2e
def test_a_leftover_backup_of_a_committed_close_is_dropped(closing: Project) -> None:
    # Killed after the commit, before the cleanup: the next close sees it committed.
    assert _close(closing).returncode == 0
    _backup(closing).write_text(SUBJECT + "\n")
    head = _git(closing, "rev-parse", "HEAD")
    _close(closing)
    assert _git(closing, "rev-parse", "HEAD") == head
    assert not _backup(closing).exists()
    assert not (closing.root / MSG).exists()


@pytest.mark.e2e
def test_the_retry_reuses_the_kept_message(closing: Project) -> None:
    _hook(closing, "exit 1\n")
    assert _close(closing).returncode != 0
    assert (closing.root / MSG).read_text() == SUBJECT + "\n"
    (closing.root / ".git/hooks/pre-commit").unlink()
    res = _close(closing)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _git(closing, "log", "-1", "--format=%s") == SUBJECT + "\n"
    assert not (closing.root / MSG).exists()  # removed only after the commit
    committed = _git(closing, "show", "--name-only", "--format=", "HEAD").split()
    assert MSG not in committed and DECLARED in committed


@pytest.mark.e2e
def test_a_staged_message_is_never_committed(closing: Project) -> None:
    _git(closing, "add", MSG)
    head = _git(closing, "rev-parse", "HEAD")
    res = _close(closing)
    assert res.returncode != 0
    assert "in the git index" in res.stderr
    assert _git(closing, "rev-parse", "HEAD") == head
    assert (closing.root / MSG).read_text() == SUBJECT + "\n"


def _lowered_after_seal(plan: Plan, gate: str) -> Path:
    """A spec sealed with `risk: high` by its phase/issue gate, then lowered."""
    plan.phase("01", "1.1").sub("1.1.1").issue(3).isub("ISS-3.1").approve_roadmap().sync()
    if gate == "phase":
        spec, seal = plan.root / "docs/plan/phase-01-p01/1.1.1-s.md", ("phase", "01")
        plan.approve_phase("01")
    else:
        spec, seal = plan.root / "docs/plan/issues/ISS-3-i3/ISS-3.1-s.md", ("issue", "ISS-3")
        plan.approve_issue(3)
    _rate_spec(spec, HIGH)
    assert plan.run_py("sync_plan.py").returncode == 0
    res = plan.run_py("_guard.py", "seal", *seal)
    assert res.returncode == 0, res.stdout + res.stderr
    spec.write_text(spec.read_text().replace(HIGH, ""))
    return spec


@pytest.mark.parametrize(("gate", "cid"), [("phase", "01"), ("issue", "ISS-3")])
def test_a_container_keeps_the_sealed_rating_as_a_floor(plan: Plan, gate: str, cid: str) -> None:
    _lowered_after_seal(plan, gate)
    res = _cli(plan.root, gate, cid)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines()[0] == "xhigh"
    assert "sealed by its phase/task/issue gate" in res.stdout
    assert str(CODEX / "checklist-security.md") in res.stdout
