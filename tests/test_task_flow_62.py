"""Issue #62: task-by-task breakdown, task gate, task-close and phase-close gates.

A phase sealed WITHOUT subtask specs follows the task flow: its specs are written
per task at kickoff, reviewed by the task gate (TASK_APPROVED, sealed in
progress/plan_task_N.M.sha256), and each finished task / phase is checked by a
task-close / phase-close gate (TASK_CLOSE_APPROVED / PHASE_CLOSE_APPROVED) that
carries a traceability matrix.

A phase sealed WITH its specs (broken down whole, before #62) is legacy: no task
gate, and `make plan` records once which tasks were already done
(progress/task_flow_legacy_NN.json, sealed in the phase seal); those are exempt
from the task-close gate.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan, write_control

SPEC_DIR = "docs/plan/phase-01-p01"
LEGACY = "progress/task_flow_legacy_01.json"
MATRIX_OK = (
    "## Traceability\n\n| Point | Test | Status |\n|---|---|---|\n"
    "| goal: g. | tests/acceptance/test_1.1.1.py::test_ac1_works | met |\n"
)
MATRIX_GAP = MATRIX_OK + "| AC2 of 1.1.2 | - | gap |\n"
MATRIX_ODD = MATRIX_OK + "| AC2 of 1.1.2 | - | gap — missing test |\n"


def _guard(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_guard.py", *args)


def _out(res: subprocess.CompletedProcess[str]) -> str:
    return res.stdout + res.stderr


def _spec(plan: Plan, sid: str, status: str = "pending", depends: str = "none") -> None:
    """Write a spec after the phase gate (task kickoff) and re-sync."""
    plan.sub(sid, status, depends).sync()


def _set_status(plan: Plan, sid: str, status: str) -> None:
    status_was = plan.specs[sid][0]
    path = plan.root / SPEC_DIR / f"{sid}-s.md"
    path.write_text(path.read_text().replace(f"status: {status_was}", f"status: {status}"))
    plan.specs[sid] = (status, plan.specs[sid][1])
    assert plan.run_py("sync_plan.py").returncode == 0


def _task_flow(plan: Plan, *tasks: str) -> Plan:
    """Roadmap + phase 01 approved and sealed with tasks only (no specs)."""
    plan.phase("01", *tasks).phase("02").approve_roadmap().sync()
    assert _guard(plan, "seal", "roadmap").returncode == 0
    plan.approve_phase("01")
    res = _guard(plan, "seal", "phase", "01")
    assert res.returncode == 0, _out(res)
    return plan


def _legacy(plan: Plan, done: tuple[str, ...] = ()) -> Plan:
    """Phase 01 broken down whole (1.1.1, 1.2.1, 1.3.1) and sealed with its specs."""
    plan.phase("01", "1.1", "1.2", "1.3").phase("02")
    for sid in ("1.1.1", "1.2.1", "1.3.1"):
        plan.sub(sid, "done" if sid in done else "pending")
    plan.approve_roadmap().sync()
    assert _guard(plan, "seal", "roadmap").returncode == 0
    plan.approve_phase("01")
    assert _guard(plan, "seal", "phase", "01").returncode == 0
    assert plan.run_py("sync_plan.py").returncode == 0  # the first make plan after it
    return plan


def _approve_task(plan: Plan, tid: str) -> None:
    plan.verdict(f"task_review_{tid}.md", "TASK_APPROVED")
    res = _guard(plan, "seal", "task", tid)
    assert res.returncode == 0, _out(res)


def _close_task(plan: Plan, tid: str) -> None:
    (plan.root / "progress" / f"task_close_{tid}.md").write_text(
        f"# Task close\n\n**Verdict:** TASK_CLOSE_APPROVED\n\n{MATRIX_OK}"
    )


def _status(plan: Plan) -> str:
    res = plan.run_py("plan_status.py")
    assert res.returncode == 0, _out(res)
    return res.stdout


def _next(plan: Plan) -> str:
    return next(line for line in _status(plan).splitlines() if line.startswith("Next:"))


# ── seals ────────────────────────────────────────────────────────────────────


def test_task_flow_phase_seal_holds_no_specs(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    sealed = (plan.root / "progress/plan_phase_01.sha256").read_text()
    assert "docs/plan/phase-01-p01.md" in sealed
    _spec(plan, "1.1.1")  # kickoff writes the spec after the phase gate
    res = _guard(plan, "integrity")
    assert res.returncode == 0, res.stdout


@pytest.mark.e2e
def test_task_seal_needs_task_approved(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1")
    res = _guard(plan, "seal", "task", "1.1")
    assert res.returncode == 1
    assert "TASK_APPROVED" in _out(res)


@pytest.mark.e2e
def test_task_seal_protects_its_specs(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1")
    _approve_task(plan, "1.1")
    assert (plan.root / "progress/plan_task_1.1.sha256").is_file()
    assert _guard(plan, "integrity").returncode == 0
    _set_status(plan, "1.1.1", "in_progress")  # status moves freely
    assert _guard(plan, "integrity").returncode == 0
    path = plan.root / SPEC_DIR / "1.1.1-s.md"
    path.write_text(path.read_text().replace("src/app/m111.py", "src/app/other.py"))
    _set_status(plan, "1.1.1", "pending")
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{SPEC_DIR}/1.1.1-s.md changed after task 1.1 was approved" in res.stdout
    assert "review_with_codex.sh task 1.1" in res.stdout


@pytest.mark.e2e
def test_spec_added_to_an_approved_task_fails(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1")
    _approve_task(plan, "1.1")
    _spec(plan, "1.1.2")
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{SPEC_DIR}/1.1.2-s.md added after task 1.1 was approved" in res.stdout


@pytest.mark.e2e
def test_plan_gate_reseals_its_spec_in_the_task_seal(plan: Plan) -> None:
    import _plan_seal

    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1")
    _approve_task(plan, "1.1")
    path = plan.root / SPEC_DIR / "1.1.1-s.md"
    path.write_text(path.read_text() + "\n## Description\nclarified.\n")
    cwd = Path.cwd()
    os.chdir(plan.root)
    try:
        assert _plan_seal.reseal_spec(plan.root, f"{SPEC_DIR}/1.1.1-s.md", "01")
    finally:
        os.chdir(cwd)
    assert _guard(plan, "integrity").returncode == 0
    assert "1.1.1-s.md" not in (plan.root / "progress/plan_phase_01.sha256").read_text()


def test_legacy_phase_keeps_its_whole_seal(plan: Plan) -> None:
    _legacy(plan)
    path = plan.root / SPEC_DIR / "1.2.1-s.md"
    path.write_text(path.read_text().replace("src/app/m121.py", "src/app/x.py"))
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{SPEC_DIR}/1.2.1-s.md changed after phase 01 was approved" in res.stdout


# ── legacy record ────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_make_plan_records_the_done_tasks_of_a_legacy_phase_once(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1",))
    record = json.loads((plan.root / LEGACY).read_text())
    assert record["phase"] == "01"
    assert record["tasks_done"] == ["1.1"]
    assert LEGACY in (plan.root / "progress/plan_phase_01.sha256").read_text()
    assert _guard(plan, "integrity").returncode == 0
    _set_status(plan, "1.2.1", "done")  # later progress never rewrites the record
    assert json.loads((plan.root / LEGACY).read_text())["tasks_done"] == ["1.1"]


def test_task_flow_phase_gets_no_legacy_record(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    assert plan.run_py("sync_plan.py").returncode == 0
    assert not (plan.root / LEGACY).exists()


def test_editing_the_legacy_record_fails_integrity(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1",))
    path = plan.root / LEGACY
    path.write_text(path.read_text().replace('"1.1"', '"1.1", "1.2"'))
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{LEGACY} changed after phase 01 was approved" in res.stdout


@pytest.mark.e2e
def test_deleting_the_legacy_record_fails_and_is_not_recreated(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1",))
    (plan.root / LEGACY).unlink()
    assert plan.run_py("sync_plan.py").returncode == 0
    assert not (plan.root / LEGACY).exists()
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{LEGACY} removed after phase 01 was approved" in res.stdout


def test_legacy_record_is_codex_owned(plan: Plan) -> None:
    import _guard as guard

    for rel in (
        LEGACY,
        "progress/task_review_1.1.md",
        "progress/task_close_1.1.md",
        "progress/phase_close_01.md",
    ):
        assert guard.RE_CODEX_OWNED.match(rel), rel


# ── plan rules ───────────────────────────────────────────────────────────────


def test_subtask_of_a_task_flow_phase_needs_task_approved(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "in_progress")
    res = plan.validate()
    assert res.returncode == 1
    assert "1.1.1 is in_progress but task 1.1 is not TASK_APPROVED" in res.stdout
    plan.verdict("task_review_1.1.md", "TASK_APPROVED")
    res = plan.validate()
    assert res.returncode == 0, res.stdout


def test_legacy_phase_needs_no_task_gate(plan: Plan) -> None:
    _legacy(plan)
    _set_status(plan, "1.1.1", "in_progress")
    res = plan.validate()
    assert res.returncode == 0, res.stdout


def test_approved_unsealed_phase_passes_as_before(plan: Plan) -> None:
    plan.phase("01", "1.1", "1.2").sub("1.1.1", "done").sub("1.2.1", "in_progress")
    plan.approve_roadmap().approve_phase("01").sync()
    res = plan.validate()
    assert res.returncode == 0, res.stdout


@pytest.mark.e2e
def test_next_task_needs_the_previous_task_close(plan: Plan) -> None:
    _task_flow(plan, "1.1", "1.2")
    _spec(plan, "1.1.1", "done")
    _approve_task(plan, "1.1")
    _spec(plan, "1.2.1", "in_progress")
    plan.verdict("task_review_1.2.md", "TASK_APPROVED")
    res = plan.validate()
    assert res.returncode == 1
    assert "task 1.2 started but task 1.1 is not TASK_CLOSE_APPROVED" in res.stdout
    _close_task(plan, "1.1")
    res = plan.validate()
    assert res.returncode == 0, res.stdout


@pytest.mark.e2e
def test_legacy_record_exempts_only_the_tasks_done_at_detection(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1",))
    _set_status(plan, "1.2.1", "in_progress")
    assert plan.validate().returncode == 0  # 1.1 was done before the task flow
    _set_status(plan, "1.2.1", "done")
    _set_status(plan, "1.3.1", "in_progress")
    res = plan.validate()
    assert res.returncode == 1
    assert "task 1.3 started but task 1.2 is not TASK_CLOSE_APPROVED" in res.stdout


@pytest.mark.e2e
def test_next_phase_needs_the_phase_close(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "done")
    _approve_task(plan, "1.1")
    _close_task(plan, "1.1")
    assert plan.validate().returncode == 0
    plan.phase("02", "2.1").sub("2.1.1").approve_phase("02").sync()
    _guard(plan, "seal", "phase", "02")
    res = plan.validate()
    assert res.returncode == 1
    assert "phase 02 is broken down but phase 01 is not PHASE_CLOSE_APPROVED" in res.stdout
    plan.verdict("phase_close_01.md", "PHASE_CLOSE_APPROVED")
    res = plan.validate()
    assert res.returncode == 0, res.stdout


def test_legacy_phase_done_at_detection_needs_no_phase_close(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1", "1.2.1", "1.3.1"))
    plan.phase("02", "2.1").sub("2.1.1").sync()
    res = plan.validate()
    assert res.returncode == 0, res.stdout


# ── readiness ────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_task_ready(plan: Plan) -> None:
    _task_flow(plan, "1.1", "1.2")
    res = plan.run_py("_harness.py", "task-ready", "1.1")
    assert res.returncode == 1 and "no subtask specs yet" in res.stdout
    _spec(plan, "1.1.1")
    assert plan.run_py("_harness.py", "task-ready", "1.1").returncode == 0
    _spec(plan, "1.2.1")
    res = plan.run_py("_harness.py", "task-ready", "1.2")
    assert res.returncode == 1 and "task 1.1 is not TASK_CLOSE_APPROVED" in res.stdout


def test_task_ready_rejects_a_legacy_phase(plan: Plan) -> None:
    _legacy(plan)
    res = plan.run_py("_harness.py", "task-ready", "1.2")
    assert res.returncode == 1
    assert "broken down whole" in res.stdout


@pytest.mark.e2e
def test_task_close_ready(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "done")
    _spec(plan, "1.1.2")
    _approve_task(plan, "1.1")
    res = plan.run_py("_harness.py", "task-close-ready", "1.1")
    assert res.returncode == 1 and "1.1.2" in res.stdout
    _set_status(plan, "1.1.2", "done")
    assert plan.run_py("_harness.py", "task-close-ready", "1.1").returncode == 0


def test_task_close_ready_rejects_a_task_exempt_by_the_legacy_record(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1",))
    res = plan.run_py("_harness.py", "task-close-ready", "1.1")
    assert res.returncode == 1
    assert LEGACY in res.stdout


@pytest.mark.e2e
def test_phase_close_ready(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "done")
    _approve_task(plan, "1.1")
    res = plan.run_py("_harness.py", "phase-close-ready", "01")
    assert res.returncode == 1 and "task 1.1 is not TASK_CLOSE_APPROVED" in res.stdout
    _close_task(plan, "1.1")
    assert plan.run_py("_harness.py", "phase-close-ready", "01").returncode == 0


@pytest.mark.e2e
def test_close_commits_lists_the_task_commits(plan: Plan) -> None:
    _task_flow(plan, "1.1", "1.2")
    root = plan.root
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    for key, value in (("user.email", "d@e.x"), ("user.name", "d")):
        subprocess.run([*git, "config", key, value], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "plan"], check=True)
    shas = []
    for sid in ("1.1.1", "1.2.1"):
        (root / "tests/acceptance").mkdir(parents=True, exist_ok=True)
        (root / f"tests/acceptance/test_{sid}.py").write_text("x = 1\n")
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", f"feat: close [{sid}]"], check=True)
        out = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True)
        shas.append(out.stdout.decode().strip())
    plan.sub("1.1.1", "done").sub("1.2.1", "done").sync()
    res = plan.run_py("_harness.py", "close-commits", "1.1")
    assert res.stdout.split() == [shas[0]]
    res = plan.run_py("_harness.py", "close-commits", "01")
    assert res.stdout.split() == shas


# ── verdict matrix ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("gate", ["task-close", "phase-close"])
def test_close_approval_needs_a_matrix_without_gaps(gate: str) -> None:
    import _verdict

    token = _verdict.GATE_TOKENS[gate]
    ok = f"**Verdict:** {token}\n\n{MATRIX_OK}".encode()
    assert _verdict.checked_token(gate, "v.md", ok) == token
    for bad in (
        f"**Verdict:** {token}\n",
        f"**Verdict:** {token}\n\n{MATRIX_GAP}",
        f"**Verdict:** {token}\n\n{MATRIX_ODD}",
    ):
        with pytest.raises(_verdict.InvalidVerdict):
            _verdict.checked_token(gate, "v.md", bad.encode())
    rejected = f"**Verdict:** CHANGES_REQUESTED\n\n{MATRIX_GAP}".encode()
    assert _verdict.checked_token(gate, "v.md", rejected) == "CHANGES_REQUESTED"


def test_task_gate_token() -> None:
    import _verdict

    assert _verdict.GATE_TOKENS["task"] == "TASK_APPROVED"
    assert _verdict.verdict_path("task", "1.1") == "progress/task_review_1.1.md"
    assert _verdict.verdict_path("task-close", "1.1") == "progress/task_close_1.1.md"
    assert _verdict.verdict_path("phase-close", "01") == "progress/phase_close_01.md"


# ── make status ──────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_status_next_walks_the_task_flow(plan: Plan) -> None:
    plan.phase("01", "1.1", "1.2").phase("02").approve_roadmap().sync()
    assert "phase gate: ./harness/review_with_codex.sh phase 01" in _next(plan)
    _guard(plan, "seal", "roadmap")
    plan.approve_phase("01")
    _guard(plan, "seal", "phase", "01")
    assert "kickoff task 1.1" in _next(plan)
    _spec(plan, "1.1.1")
    assert "task gate: ./harness/review_with_codex.sh task 1.1" in _next(plan)
    _approve_task(plan, "1.1")
    assert "start subtask 1.1.1" in _next(plan)
    _set_status(plan, "1.1.1", "done")
    assert "task-close gate: ./harness/review_with_codex.sh task-close 1.1" in _next(plan)
    plan.verdict("task_close_1.1.md", "CHANGES_REQUESTED")
    assert "fix the gaps of task 1.1" in _next(plan)
    _close_task(plan, "1.1")
    assert "kickoff task 1.2" in _next(plan)
    _spec(plan, "1.2.1", "done")
    _approve_task(plan, "1.2")
    _close_task(plan, "1.2")
    assert "phase-close gate: ./harness/review_with_codex.sh phase-close 01" in _next(plan)
    plan.verdict("phase_close_01.md", "PHASE_CLOSE_APPROVED")
    assert "break down phase 02 into tasks" in _next(plan)


@pytest.mark.e2e
def test_status_shows_the_task_verdicts(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "done")
    _approve_task(plan, "1.1")
    _close_task(plan, "1.1")
    out = _status(plan)
    assert "task gate: TASK_APPROVED" in out
    assert "close: TASK_CLOSE_APPROVED" in out


def test_status_of_a_legacy_phase_is_unchanged(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1",))
    out = _status(plan)
    assert "task gate" not in out
    assert "start subtask 1.2.1" in _next(plan)


# ── review_with_codex.sh: close gates reuse the green record ────────────────


def _gate_project(plan: Plan, counter: Path) -> Plan:
    """A git project with the harness linked and a `make check` that counts its runs."""
    root = plan.root
    (root / "harness").symlink_to(TOOLKIT / "lib")
    (root / ".claude").mkdir()
    (root / ".claude" / "codex").symlink_to(TOOLKIT / "codex")
    (root / ".gitignore").write_text("harness\n.claude/\n")
    (root / "Makefile").write_text(f"check:\n\t@echo run >> {counter}\n")
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    for key, value in (("user.email", "d@e.x"), ("user.name", "d")):
        subprocess.run([*git, "config", key, value], check=True)
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "done")
    _approve_task(plan, "1.1")
    (root / "progress/impl_1.1.1.md").write_text("impl\n")
    (root / "tests/acceptance").mkdir(parents=True)
    (root / "tests/acceptance/test_1.1.1.py").write_text("x = 1\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "feat: close [1.1.1]"], check=True)
    return plan


def _gate(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    write_control(plan.root)  # manual mode (#90)
    env = {**base, "CLAUDE_PROJECT_DIR": str(plan.root)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # new toolkit .pyc files change the fingerprint
    return subprocess.run(
        ["bash", str(plan.root / "harness/review_with_codex.sh"), *args],
        cwd=plan.root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _runs(counter: Path) -> int:
    return len(counter.read_text().splitlines()) if counter.exists() else 0


@pytest.mark.e2e
def test_task_close_reuses_a_matching_green_record(plan: Plan, tmp_path: Path) -> None:
    counter = tmp_path.parent / f"{tmp_path.name}-runs.txt"
    _gate_project(plan, counter)
    first = _gate(plan, "task-close", "1.1")
    assert first.returncode == 0, _out(first)
    assert _runs(counter) == 1  # no record yet: one recorded make check
    second = _gate(plan, "task-close", "1.1")
    assert second.returncode == 0, _out(second)
    assert _runs(counter) == 1  # the record matches: no extra make check
    assert "reused the green record" in second.stdout


@pytest.mark.e2e
def test_task_close_prompt_reads_only_specs_reports_and_range(plan: Plan, tmp_path: Path) -> None:
    _gate_project(plan, tmp_path.parent / f"{tmp_path.name}-runs.txt")
    res = _gate(plan, "task-close", "1.1")
    assert res.returncode == 0, _out(res)
    out = res.stdout
    assert f"{SPEC_DIR}/1.1.1-s.md" in out
    assert "progress/impl_1.1.1.md" in out
    assert "git show" in out
    assert "Context you must read: AGENTS.md" not in out
    assert "progress/task_close_1.1.md" in out


@pytest.mark.e2e
def test_task_close_gate_refuses_an_unfinished_task(plan: Plan, tmp_path: Path) -> None:
    _gate_project(plan, tmp_path.parent / f"{tmp_path.name}-runs.txt")
    _spec(plan, "1.1.2")
    res = _gate(plan, "task-close", "1.1")
    assert res.returncode == 1
    assert "1.1.2 not done" in _out(res)


@pytest.mark.e2e
def test_task_gate_prints_its_seal_step(plan: Plan, tmp_path: Path) -> None:
    _gate_project(plan, tmp_path.parent / f"{tmp_path.name}-runs.txt")
    res = _gate(plan, "task", "1.1")
    assert res.returncode == 0, _out(res)
    assert "progress/task_review_1.1.md" in res.stdout
    assert "review_with_codex.sh seal task 1.1" in res.stdout


@pytest.mark.e2e
def test_phase_close_gate_needs_every_task_close(plan: Plan, tmp_path: Path) -> None:
    _gate_project(plan, tmp_path.parent / f"{tmp_path.name}-runs.txt")
    res = _gate(plan, "phase-close", "01")
    assert res.returncode == 1
    assert "task 1.1 is not TASK_CLOSE_APPROVED" in _out(res)
    _close_task(plan, "1.1")
    res = _gate(plan, "phase-close", "01")
    assert res.returncode == 0, _out(res)
    assert "progress/phase_close_01.md" in res.stdout
    assert "docs/plan/PLAN.md" in res.stdout


# ── docs and routing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["task", "task-close", "phase-close"])
def test_new_gates_run_on_the_planner(mode: str) -> None:
    script = (TOOLKIT / "lib/review_with_codex.sh").read_text()
    route = next(
        line
        for line in script.splitlines()
        if "ROLE=" in line and mode in line.strip().split(")", 1)[0].split("|")
    )
    assert 'ROLE="$CODEX_DIR/codex-planner.md"' in route


@pytest.mark.parametrize("doc", ["agents/leader.md", "lib/WORKFLOW.md"])
def test_routing_tables_cover_the_task_flow(doc: str) -> None:
    text = (TOOLKIT / doc).read_text()
    planner = next(line for line in text.splitlines() if "`codex-planner.md`" in line)
    for gate in ("`task`", "`task-close`", "`phase-close`"):
        assert gate in planner, (doc, gate)


def test_reviewer_defines_the_new_modes() -> None:
    text = (TOOLKIT / "codex/reviewer.md").read_text()
    assert "## `task` mode" in text
    assert "## `task-close` and `phase-close` modes" in text
    assert "traceability matrix" in text.lower()


def test_cost_is_documented() -> None:
    text = (TOOLKIT / "docs/design/task-flow.md").read_text()
    assert "## Cost" in text
    assert "0 extra full checks" in text


@pytest.mark.e2e
def test_deleting_a_task_seal_fails_integrity(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1")
    _approve_task(plan, "1.1")
    (plan.root / "progress/plan_task_1.1.sha256").unlink()
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert "task 1.1 is TASK_APPROVED but not sealed" in res.stdout


def test_a_planted_legacy_record_fails_integrity(plan: Plan) -> None:
    plan.phase("01", "1.1", "1.2").phase("02").sub("1.1.1").sub("1.2.1").approve_roadmap()
    plan.sync()
    _guard(plan, "seal", "roadmap")
    plan.approve_phase("01")
    _guard(plan, "seal", "phase", "01")
    (plan.root / LEGACY).write_text('{"phase": "01", "tasks_done": ["1.1", "1.2"]}\n')
    assert plan.run_py("sync_plan.py").returncode == 0
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{LEGACY} added after phase 01 was approved" in res.stdout


def test_specs_of_a_later_task_before_its_kickoff_fail(plan: Plan) -> None:
    _task_flow(plan, "1.1", "1.2")
    _spec(plan, "1.1.1")
    _spec(plan, "1.2.1")
    res = plan.validate()
    assert res.returncode == 1
    assert "task 1.2 has subtask specs before its kickoff" in res.stdout


def test_a_legacy_phase_keeps_its_later_specs(plan: Plan) -> None:
    _legacy(plan)
    res = plan.validate()
    assert res.returncode == 0, res.stdout


# ── close manifest (issue #40 rules): exact task-flow bookkeeping ────────────


def _scope_repo(root: Path) -> Path:
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    for key, value in (("user.email", "d@e.x"), ("user.name", "d")):
        subprocess.run([*git, "config", key, value], check=True)
    (root / "README.md").write_text("x\n")
    _scope_plan(root, {"01": ["1.1", "1.2", "1.3"], "02": ["2.1"]})
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    return root


def _scope_plan(root: Path, phases: dict[str, list[str]]) -> None:
    """A feature_list.json with this task order (the close manifest reads it)."""
    data = {
        "phases": [{"id": pid, "tasks": tids} for pid, tids in phases.items()],
        "features": [{"id": t, "phase": pid} for pid, tids in phases.items() for t in tids],
    }
    (root / "feature_list.json").write_text(json.dumps(data))


def _scope_leaf(sid: str) -> dict[str, object]:
    return {
        "id": sid,
        "phase": f"{int(sid.split('.')[0]):02d}",
        "issue": "",
        "files": [],
        "spec": f"{SPEC_DIR}/{sid}-s.md",
        "migrates": [],
    }


def _scope(root: Path, sid: str, *rels: str) -> tuple[str, ...]:
    import _close_scope

    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n")
    return _close_scope.check(root, sid, _scope_leaf(sid)).disallowed


@pytest.mark.parametrize(
    "rel",
    [
        "progress/task_review_1.2.md",
        "progress/verdict_task_review_1.2.md.sha256",
        "progress/plan_task_1.2.sha256",
        "progress/review_history/1.2.md",
        "progress/task_close_1.2.md",
        "progress/task_close_1.1.md",
        "progress/verdict_task_close_1.1.md.sha256",
        LEGACY,
    ],
)
def test_close_manifest_admits_the_task_flow_bookkeeping(tmp_path: Path, rel: str) -> None:
    assert _scope(_scope_repo(tmp_path), "1.2.1", rel) == ()


def test_close_manifest_admits_the_previous_phase_close(tmp_path: Path) -> None:
    root = _scope_repo(tmp_path)
    rels = (
        "progress/phase_close_01.md",
        "progress/verdict_phase_close_01.md.sha256",
        "progress/review_history/01.md",
    )
    leaf = {**_scope_leaf("2.1.1"), "spec": "docs/plan/phase-02-p02/2.1.1-s.md"}
    import _close_scope

    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n")
    assert _close_scope.check(root, "2.1.1", leaf).disallowed == ()


@pytest.mark.parametrize(
    "rel",
    [
        "progress/task_review_1.3.md",
        "progress/plan_task_1.3.sha256",
        "progress/task_close_1.3.md",
        "progress/phase_close_01.md",
        "progress/task_flow_legacy_02.json",
    ],
)
def test_close_manifest_rejects_another_task_or_phase(tmp_path: Path, rel: str) -> None:
    assert _scope(_scope_repo(tmp_path), "1.2.1", rel) == (rel,)


def test_close_manifest_admits_the_legacy_record_only_as_a_creation(tmp_path: Path) -> None:
    root = _scope_repo(tmp_path)
    assert _scope(root, "1.2.1", LEGACY) == ()
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "record"], check=True)
    (root / LEGACY).write_text("changed\n")
    assert _scope(root, "1.2.1") == (LEGACY,)


# ── round 2 of Codex ─────────────────────────────────────────────────────────


def test_phase_gate_accepts_a_tasks_only_phase(plan: Plan) -> None:
    plan.phase("01", "1.1", "1.2").phase("02").approve_roadmap().sync()
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 0, _out(res)
    plan.phase("01").sync()  # no task at all: nothing to review
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 1


@pytest.mark.e2e
def test_deleting_a_task_seal_and_all_its_specs_fails(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1")
    _approve_task(plan, "1.1")
    (plan.root / "progress/plan_task_1.1.sha256").unlink()
    (plan.root / SPEC_DIR / "1.1.1-s.md").unlink()
    del plan.specs["1.1.1"]
    assert plan.run_py("sync_plan.py").returncode == 0
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert "task 1.1 is TASK_APPROVED but not sealed" in res.stdout


def test_matrix_row_without_a_leading_pipe_counts() -> None:
    import _verdict

    text = f"**Verdict:** TASK_CLOSE_APPROVED\n\n{MATRIX_OK}AC2 | - | gap\n"
    with pytest.raises(_verdict.InvalidVerdict):
        _verdict.checked_token("task-close", "v.md", text.encode())


@pytest.mark.e2e
def test_a_subtask_without_close_commit_is_a_gap(plan: Plan) -> None:
    import _verdict

    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "done")
    _spec(plan, "1.1.2", "done")
    git = ["git", "-C", str(plan.root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    for key, value in (("user.email", "d@e.x"), ("user.name", "d")):
        subprocess.run([*git, "config", key, value], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "feat: close [1.1.1]"], check=True)
    ok = f"**Verdict:** TASK_CLOSE_APPROVED\n\n{MATRIX_OK}".encode()
    cwd = Path.cwd()
    os.chdir(plan.root)
    try:
        with pytest.raises(_verdict.InvalidVerdict, match="1.1.2"):
            _verdict.checked_token("task-close", "progress/task_close_1.1.md", ok)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "fix: x [1.1.2]"], check=True)
        token = _verdict.checked_token("task-close", "progress/task_close_1.1.md", ok)
        assert token == "TASK_CLOSE_APPROVED"
    finally:
        os.chdir(cwd)


# ── round 3 of Codex ─────────────────────────────────────────────────────────


def test_close_manifest_follows_the_plan_order_not_the_ids(tmp_path: Path) -> None:
    root = _scope_repo(tmp_path)
    _scope_plan(root, {"01": ["1.1", "1.3"], "03": ["3.1"]})
    assert _scope(root, "1.3.1", "progress/task_close_1.1.md") == ()
    assert _scope(root, "1.3.1", "progress/task_close_1.2.md") == ("progress/task_close_1.2.md",)
    import _close_scope

    leaf = {**_scope_leaf("3.1.1"), "spec": "docs/plan/phase-03-p03/3.1.1-s.md"}
    (root / "progress/task_close_1.2.md").unlink()
    (root / "progress/phase_close_01.md").write_text("x\n")
    assert _close_scope.check(root, "3.1.1", leaf).disallowed == ("progress/task_close_1.1.md",)


@pytest.mark.e2e
def test_a_tag_in_the_commit_body_is_not_a_close_commit(plan: Plan) -> None:
    _task_flow(plan, "1.1")
    _spec(plan, "1.1.1", "done")
    git = ["git", "-C", str(plan.root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    for key, value in (("user.email", "d@e.x"), ("user.name", "d")):
        subprocess.run([*git, "config", key, value], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "chore: plan", "-m", "see [1.1.1]"], check=True)
    res = plan.run_py("_harness.py", "close-missing", "1.1")
    assert res.stdout.split() == ["1.1.1"]
    assert plan.run_py("_harness.py", "close-commits", "1.1").stdout.split() == []


@pytest.mark.e2e
def test_status_names_the_first_task_close_still_missing(plan: Plan) -> None:
    _legacy(plan, done=("1.1.1",))
    _set_status(plan, "1.2.1", "done")
    _set_status(plan, "1.3.1", "done")
    assert "task-close 1.2" in _next(plan)
