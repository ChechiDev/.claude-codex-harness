"""Deterministic resume and attempt budget (issue #46, + #61 item 3).

- Attempt ledger: review_with_codex.sh records every attempt of a step (a gate, the
  close, the PR) on one id under .git/harness-gate-runs/attempts_<step>_<id>.json.
  An attempt that ends in an outcome (a final verdict, the close commit, the PR)
  resets the streak; any other end (a red check, a reviewer crash, an invalid
  verdict, a failed commit or push, a killed run) is a failed attempt.
- Budget: after harness.toml [review] max_attempts failed attempts in a row (default
  3, 0 = off, no env override) the step needs the human.
- Resume (_resume.py): one next action derived from the artifacts (the specs, the
  verdicts, the gate records, the ledger), never from memory. It only names a
  command for an action the human already authorized (the cycle of the single
  in_progress leaf, a retry of its close or PR) and never invents a human OK.
- Rotation (#61 item 3): the numbered code-gate attempt records of a subtask are
  rotated (the newest kept); the approved record is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import _config
import _resume
import pytest
from _gate_run import live_run
from conftest import DECLARED, LEAF_ID, TOOLKIT, Plan, Project, bind_code_approval

GATE = "./harness/review_with_codex.sh"


# ── helpers ─────────────────────────────────────────────────────────────────


def _py(
    project: Project, script: str, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    return subprocess.run(
        ["python3", str(project.root / "harness" / script), *args],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
        env={**base, **(env or {})},
    )


def _runs(project: Project) -> Path:
    return project.root / ".git" / "harness-gate-runs"


def _ledger(project: Project, step: str, fid: str = LEAF_ID) -> Path:
    return _runs(project) / f"attempts_{step}_{fid}.json"


def _attempt(project: Project, path: Path, tag: str, outcome: str, reason: str) -> None:
    """One begin+end of the ledger, in process (same locked functions the CLI calls; the
    CLI wiring itself is covered by the tests that go through `_py`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cap = _config.load(project.root).max_attempts
    assert _resume.begin(path, tag, cap, live_run) is None
    _resume.end(path, tag, outcome, reason)


def _fail(project: Project, step: str, n: int, reason: str, fid: str = LEAF_ID) -> None:
    for i in range(n):
        _attempt(project, _ledger(project, step, fid), str(1000 + i), "fail", reason)


def _ok(project: Project, step: str, fid: str = LEAF_ID) -> None:
    _attempt(project, _ledger(project, step, fid), "77", "ok", "done")


def _resume_json(project: Project) -> dict[str, Any]:
    res = _py(project, "_resume.py", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    data: dict[str, Any] = json.loads(res.stdout)
    return data


def _status_next(project: Project) -> str:
    res = _py(project, "plan_status.py")
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith("Next: ")]
    assert len(lines) == 1, res.stdout
    return lines[0].removeprefix("Next: ")


def _fake_codex(tmp: Path, verdict: str = "CHANGES_REQUESTED", rc: int = 0) -> dict[str, str]:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    codex = bin_dir / "codex"
    body = f"printf '# r\\n\\n**Verdict:** {verdict}\\n' > progress/review_{LEAF_ID}.md\n"
    codex.write_text(
        '#!/usr/bin/env bash\ncat >/dev/null\ntouch "$CODEX_MARK"\n'
        + (body if rc == 0 else f"exit {rc}\n")
    )
    codex.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CODEX_MARK": str(tmp / "codex-ran")}


def _code_gate(project: Project, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return project.run("review_with_codex.sh", None, "code", LEAF_ID, env=env)


@pytest.fixture
def code_ready(project: Project) -> Project:
    project.write("tests/unit/test_user.py", "def test_u(): pass\n")
    project.write(f"tests/acceptance/test_{LEAF_ID}.py", "def test_ac(): pass\n")
    project.write(f"progress/acceptance_{LEAF_ID}.sha256", "sealed\n")
    project.commit_all("base")
    return project


# ── the ledger (pure) ───────────────────────────────────────────────────────


def test_failures_count_the_streak_since_the_last_outcome(tmp_path: Path) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    for i, outcome in enumerate(["fail", "ok", "fail", "fail"]):
        _resume.begin(path, str(i))
        _resume.end(path, str(i), outcome, f"r{i}")
    entries = _resume.read_entries(path)
    assert _resume.failures(entries) == 2
    last = _resume.last_failure(entries)
    assert last is not None and last.reason == "r3"


def test_an_outcome_resets_the_streak(tmp_path: Path) -> None:
    path = tmp_path / "attempts_pr_1.1.json"
    _resume.begin(path, "1")
    _resume.end(path, "1", "fail", "push failed")
    _resume.begin(path, "2")
    _resume.end(path, "2", "ok", "PR opened")
    entries = _resume.read_entries(path)
    assert _resume.failures(entries) == 0
    assert _resume.last_failure(entries) is None


def test_a_run_that_never_ended_counts_as_failed(tmp_path: Path) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    _resume.begin(path, "1")  # killed: no end
    _resume.begin(path, "2")
    entries = _resume.read_entries(path)
    assert entries[0].outcome == "fail" and "interrupted" in entries[0].reason
    assert entries[1].outcome == "pending"
    assert _resume.failures(entries) == 2


def test_the_ledger_keeps_only_the_latest_entries(tmp_path: Path) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    for i in range(_resume.MAX_ENTRIES + 7):
        _resume.begin(path, str(i))
        _resume.end(path, str(i), "fail", f"r{i}")
    entries = _resume.read_entries(path)
    assert len(entries) == _resume.MAX_ENTRIES
    assert entries[-1].reason == f"r{_resume.MAX_ENTRIES + 6}"


def test_end_without_a_begin_is_still_recorded(tmp_path: Path) -> None:
    path = tmp_path / "attempts_close_1.1.1.json"
    _resume.end(path, "9", "fail", "commit failed")
    assert _resume.failures(_resume.read_entries(path)) == 1


@pytest.mark.parametrize("fid", ["../x", ".hidden", "a/b", "", "1.1.1\n"])
def test_bad_ids_are_refused(tmp_path: Path, fid: str) -> None:
    with pytest.raises(_resume.LedgerError):
        _resume.ledger_path(tmp_path, "code", fid)


def test_unknown_steps_are_refused(tmp_path: Path) -> None:
    with pytest.raises(_resume.LedgerError):
        _resume.ledger_path(tmp_path, "deploy", "1.1.1")


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"schema": 2, "entries": []}', '{"schema": 1, "entries": [1]}']
)
def test_a_corrupt_ledger_is_an_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    path.write_text(content)
    with pytest.raises(_resume.LedgerError):
        _resume.read_entries(path)


# ── config ──────────────────────────────────────────────────────────────────


def test_max_attempts_defaults_to_three(tmp_path: Path) -> None:
    import _config

    assert _config.load(tmp_path).max_attempts == 3


@pytest.mark.parametrize("value", ["-1", '"3"', "2.5", "true"])
def test_invalid_max_attempts_is_a_config_error(tmp_path: Path, value: str) -> None:
    import _config

    (tmp_path / "harness.toml").write_text(f"[review]\nmax_attempts = {value}\n")
    with pytest.raises(_config.ConfigError, match="max_attempts"):
        _config.load(tmp_path)


# ── budget (CLI) ────────────────────────────────────────────────────────────


def test_below_the_budget_the_step_runs(project: Project) -> None:
    _fail(project, "code", 2, "check red (exit 1)")
    assert _py(project, "_resume.py", "budget", "code", LEAF_ID).returncode == 0


def test_at_the_budget_the_step_needs_the_human(project: Project) -> None:
    _fail(project, "code", 3, "check red (exit 1)")
    res = _py(project, "_resume.py", "budget", "code", LEAF_ID)
    assert res.returncode == 1
    assert "needs human (3 failed attempts)" in res.stderr
    assert "check red (exit 1)" in res.stderr
    assert "max_attempts" in res.stderr and "harness.toml" in res.stderr


def test_the_budget_comes_from_harness_toml(project: Project) -> None:
    project.write("harness.toml", "[review]\nmax_attempts = 5\n")
    _fail(project, "code", 4, "x")
    assert _py(project, "_resume.py", "budget", "code", LEAF_ID).returncode == 0
    _fail(project, "code", 1, "x")
    assert _py(project, "_resume.py", "budget", "code", LEAF_ID).returncode == 1


def test_zero_turns_the_budget_off(project: Project) -> None:
    project.write("harness.toml", "[review]\nmax_attempts = 0\n")
    _fail(project, "code", 9, "x")
    assert _py(project, "_resume.py", "budget", "code", LEAF_ID).returncode == 0


@pytest.mark.parametrize("name", ["HARNESS_MAX_ATTEMPTS", "MAX_ATTEMPTS"])
def test_no_environment_variable_lifts_the_budget(project: Project, name: str) -> None:
    _fail(project, "code", 3, "x")
    res = _py(project, "_resume.py", "budget", "code", LEAF_ID, env={name: "0"})
    assert res.returncode == 1


def test_budgets_are_per_step_and_id(project: Project) -> None:
    _fail(project, "code", 3, "x")
    assert _py(project, "_resume.py", "budget", "plan", LEAF_ID).returncode == 0
    assert _py(project, "_resume.py", "budget", "code", "1.1.2").returncode == 0


def test_red_records_from_before_the_ledger_do_not_count(code_ready: Project) -> None:
    """User decision: the budget starts at zero. Red code-gate records written before
    #46 (or by any other producer) never count; only ledger attempts do."""
    for tag in range(1, 6):
        _record(code_ready, tag)
    assert not _ledger(code_ready, "code").exists()
    assert _py(code_ready, "_resume.py", "budget", "code", LEAF_ID).returncode == 0
    assert _resume_json(code_ready)["kind"] == "implement"


def test_a_budget_above_the_kept_history_still_blocks(project: Project) -> None:
    project.write("harness.toml", "[review]\nmax_attempts = 25\n")
    _fail(project, "code", 24, "x")
    assert len(_resume.read_entries(_ledger(project, "code"))) == _resume.MAX_ENTRIES
    assert _py(project, "_resume.py", "budget", "code", LEAF_ID).returncode == 0
    _fail(project, "code", 1, "x")
    assert _py(project, "_resume.py", "budget", "code", LEAF_ID).returncode == 1


def test_begin_refused_at_the_budget_writes_nothing(project: Project) -> None:
    _fail(project, "code", 3, "x")
    before = _ledger(project, "code").read_bytes()
    res = _py(project, "_resume.py", "begin", "code", LEAF_ID, "5")
    assert res.returncode == 1 and "needs human (3 failed attempts)" in res.stderr
    assert _ledger(project, "code").read_bytes() == before


def test_begin_fails_closed_on_a_corrupt_ledger(project: Project) -> None:
    _ledger(project, "code").parent.mkdir(parents=True, exist_ok=True)
    _ledger(project, "code").write_text("{broken")
    assert _py(project, "_resume.py", "begin", "code", LEAF_ID, "5").returncode == 1


def test_concurrent_begins_respect_the_budget(project: Project) -> None:
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    tool = str(project.root / "harness" / "_resume.py")
    procs = [
        subprocess.Popen(
            ["python3", tool, "begin", "code", LEAF_ID, str(2000 + i)],
            cwd=project.root,
            env=base,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i in range(10)
    ]
    codes = [p.wait() for p in procs]
    assert codes.count(0) == 3
    assert len(_resume.read_entries(_ledger(project, "code"))) == 3


def test_a_live_pending_attempt_is_neither_failed_nor_counted(tmp_path: Path) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    _resume.begin(path, "1")
    _resume.begin(path, "2", cap=3, live=lambda tag: tag == "1")
    entries = _resume.read_entries(path)
    assert [e.outcome for e in entries] == ["pending", "pending"]
    assert _resume.failures(entries, live=lambda tag: tag in ("1", "2")) == 0


def test_status_applies_the_budget_to_plan_level_gates(project: Project) -> None:
    (project.root / "progress/roadmap_review.md").unlink()
    assert _status_next(project) == f"roadmap gate: {GATE} roadmap"
    _fail(project, "roadmap", 3, "reviewer exited 1", fid="roadmap")
    action = _resume_json(project)
    assert action["kind"] == "human"
    assert action["text"].startswith("needs human (3 failed attempts)")


def test_a_corrupt_ledger_fails_closed(project: Project) -> None:
    _ledger(project, "code").parent.mkdir(parents=True, exist_ok=True)
    _ledger(project, "code").write_text("{broken")
    res = _py(project, "_resume.py", "budget", "code", LEAF_ID)
    assert res.returncode == 1
    assert "attempts_code" in res.stderr


# ── the gate records its attempts ───────────────────────────────────────────


@pytest.mark.e2e
def test_a_red_code_gate_is_a_failed_attempt(code_ready: Project, tmp_path: Path) -> None:
    env = {**_fake_codex(tmp_path), "HARNESS_CHECK_CMD": "false"}
    assert _code_gate(code_ready, env).returncode != 0
    entries = _resume.read_entries(_ledger(code_ready, "code"))
    assert [e.outcome for e in entries] == ["fail"]
    assert "red" in entries[0].reason


@pytest.mark.e2e
def test_the_budget_stops_a_red_loop_before_the_check(code_ready: Project, tmp_path: Path) -> None:
    mark = tmp_path / "check-ran"
    env = {**_fake_codex(tmp_path), "HARNESS_CHECK_CMD": f"touch {mark}; false"}
    for _ in range(3):
        assert _code_gate(code_ready, env).returncode != 0
    mark.unlink()
    res = _code_gate(code_ready, env)
    assert res.returncode != 0
    assert "needs human (3 failed attempts)" in res.stderr
    assert not mark.exists()
    assert not (tmp_path / "codex-ran").exists()


@pytest.mark.e2e
def test_a_reviewer_crash_is_a_failed_attempt(code_ready: Project, tmp_path: Path) -> None:
    env = {**_fake_codex(tmp_path, rc=3), "HARNESS_CHECK_CMD": "true"}
    assert _code_gate(code_ready, env).returncode != 0
    entries = _resume.read_entries(_ledger(code_ready, "code"))
    assert entries[-1].outcome == "fail" and "3" in entries[-1].reason


@pytest.mark.e2e
def test_a_final_verdict_resets_the_streak(code_ready: Project, tmp_path: Path) -> None:
    _fail(code_ready, "code", 2, "check red (exit 1)")
    env = {**_fake_codex(tmp_path, "CHANGES_REQUESTED"), "HARNESS_CHECK_CMD": "true"}
    res = _code_gate(code_ready, env)
    assert res.returncode == 0, res.stdout + res.stderr
    entries = _resume.read_entries(_ledger(code_ready, "code"))
    assert entries[-1].outcome == "ok"
    assert _resume.failures(entries) == 0


@pytest.mark.e2e
def test_manual_mode_records_no_gate_attempt(code_ready: Project, tmp_path: Path) -> None:
    from conftest import write_control

    write_control(code_ready.root)
    env = {**_fake_codex(tmp_path), "HARNESS_CHECK_CMD": "true"}
    assert _code_gate(code_ready, env).returncode == 0
    assert not _ledger(code_ready, "code").exists()


@pytest.mark.e2e
def test_a_failed_close_is_a_failed_attempt(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    res = code_ready.run("review_with_codex.sh", None, "close", LEAF_ID)  # no close msg
    assert res.returncode != 0
    entries = _resume.read_entries(_ledger(code_ready, "close"))
    assert [e.outcome for e in entries] == ["fail"]


def test_the_close_is_refused_at_the_budget(code_ready: Project) -> None:
    _fail(code_ready, "close", 3, "git commit failed")
    res = code_ready.run("review_with_codex.sh", None, "close", LEAF_ID)
    assert res.returncode != 0
    assert "needs human (3 failed attempts)" in res.stderr
    assert len(_resume.read_entries(_ledger(code_ready, "close"))) == 3


def test_the_pr_is_refused_at_the_budget(code_ready: Project) -> None:
    _fail(code_ready, "pr", 3, "push failed", fid="1.1")
    res = code_ready.run("review_with_codex.sh", None, "pr", "1.1")
    assert res.returncode != 0
    assert "needs human (3 failed attempts)" in res.stderr


@pytest.mark.e2e
def test_a_refused_pr_is_a_failed_attempt(code_ready: Project) -> None:
    code_ready.write("dirty.txt", "x")  # uncommitted: the PR barrier refuses
    res = code_ready.run("review_with_codex.sh", None, "pr", "1.1")
    assert res.returncode != 0
    entries = _resume.read_entries(_ledger(code_ready, "pr", "1.1"))
    assert [e.outcome for e in entries] == ["fail"]
    assert "uncommitted" in entries[0].reason


# ── resume: one next action from the artifacts ──────────────────────────────


def test_resume_matches_make_status(code_ready: Project) -> None:
    action = _resume_json(code_ready)
    assert action["text"] == _status_next(code_ready)
    assert action["kind"] == "implement"
    assert action["authorized"] is True
    assert action["command"] == [GATE, "code", LEAF_ID]


def test_a_red_code_gate_resumes_as_a_fix(code_ready: Project) -> None:
    _fail(code_ready, "code", 1, "check red (exit 2)")
    action = _resume_json(code_ready)
    assert action["kind"] == "fix"
    assert "red" in action["text"] and "check red (exit 2)" in action["text"]
    assert action["command"] == [GATE, "code", LEAF_ID]
    assert action["text"] == _status_next(code_ready)


def test_a_crashed_review_resumes_as_the_same_review(code_ready: Project) -> None:
    _fail(code_ready, "code", 1, "reviewer exited 3")
    action = _resume_json(code_ready)
    assert action["kind"] == "review"
    assert "reviewer exited 3" in action["text"]
    assert action["command"] == [GATE, "code", LEAF_ID]


def test_an_exhausted_budget_resumes_as_wait_for_the_human(code_ready: Project) -> None:
    _fail(code_ready, "code", 3, "check red (exit 2)")
    action = _resume_json(code_ready)
    assert action["kind"] == "human"
    assert action["authorized"] is False
    assert action["command"] is None
    assert action["text"].startswith("needs human (3 failed attempts)")
    assert _status_next(code_ready) == action["text"]


def test_a_waiting_close_message_resumes_as_a_close_retry(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    _fail(code_ready, "close", 1, "git commit failed")
    action = _resume_json(code_ready)
    assert action["kind"] == "close"
    assert action["command"] == [GATE, "close", LEAF_ID]
    assert action["authorized"] is True


def test_a_close_message_under_a_fresh_approval_resumes_as_close(code_ready: Project) -> None:
    # i107: `doc` refuses while the message waits and the approval still covers the
    # tree, so even with no failed attempt in the ledger the step is `close`.
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    expected = (
        f"retry the close of {LEAF_ID} (progress/close_{LEAF_ID}.msg is waiting): "
        f"{GATE} close {LEAF_ID}"
    )
    assert _status_next(code_ready) == expected
    action = _resume_json(code_ready)
    assert action["text"] == expected
    assert action["kind"] == "close" and action["authorized"] is True
    assert action["command"] == [GATE, "close", LEAF_ID]


@pytest.mark.parametrize("approval", ["stale", "missing"])
def test_a_close_message_without_a_fresh_approval_changes_nothing(
    code_ready: Project, approval: str
) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=approval == "stale")
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    if approval == "stale":
        code_ready.write(DECLARED, "LATE = 1\n")  # the approval no longer covers it
    expected = f"close: {GATE} doc {LEAF_ID}"
    assert _status_next(code_ready) == expected
    assert _resume_json(code_ready)["text"] == expected


def test_an_uncommitted_done_close_resumes_as_a_close_retry(code_ready: Project) -> None:
    # The documenter set status: done, the commit failed: the spec says done but no
    # close commit carries [1.1.1] and its message is waiting.
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    code_ready.set_leaf("done")
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    _fail(code_ready, "close", 1, "git commit failed")
    action = _resume_json(code_ready)
    assert action["kind"] == "close"
    assert action["command"] == [GATE, "close", LEAF_ID]


def _second_leaf(project: Project, first: str, second: str) -> None:
    data = json.loads((project.root / "feature_list.json").read_text())
    sub = data["features"][0]["subtasks"][0]
    sub["status"] = first
    data["features"][0]["subtasks"].append({**sub, "id": "1.1.2", "status": second})
    project.write("feature_list.json", json.dumps(data))


def test_an_old_close_retry_defers_to_the_active_leaf(code_ready: Project) -> None:
    _second_leaf(code_ready, "done", "in_progress")
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    _fail(code_ready, "close", 1, "git commit failed")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None
    assert LEAF_ID in action["text"] and "1.1.2" in action["text"]


def test_an_old_pr_retry_defers_to_the_active_leaf(code_ready: Project) -> None:
    _second_leaf(code_ready, "done", "in_progress")
    data = json.loads((code_ready.root / "feature_list.json").read_text())
    data["features"][0]["subtasks"][1]["id"] = "1.2.1"  # another task is active
    code_ready.write("feature_list.json", json.dumps(data))
    _fail(code_ready, "pr", 1, "could not push", fid="1.1")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None
    assert "1.1" in action["text"] and "1.2.1" in action["text"]


def test_a_stale_approval_resumes_as_a_code_gate(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    code_ready.write("src/pkg/domain/user.py", "changed = 1\n")  # after the approval
    assert _status_next(code_ready) == f"close: {GATE} doc {LEAF_ID}"  # no ledger: as before
    _ok(code_ready, "code")
    action = _resume_json(code_ready)
    assert action["kind"] == "review"
    assert action["command"] == [GATE, "code", LEAF_ID]
    assert "no longer covers" in action["text"]


def test_a_fresh_approval_resumes_as_the_close(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    action = _resume_json(code_ready)
    assert action["kind"] == "close"
    assert action["command"] == [GATE, "doc", LEAF_ID]


def test_an_unfinished_seal_resumes_as_the_plan_gate(code_ready: Project) -> None:
    (code_ready.root / f"progress/acceptance_{LEAF_ID}.sha256").unlink()
    _fail(code_ready, "plan", 1, "seal failed")
    action = _resume_json(code_ready)
    assert action["kind"] == "seal"
    assert action["command"] == [GATE, "plan", LEAF_ID]
    assert action["authorized"] is True


def test_a_missing_seal_without_ledger_evidence_changes_nothing(code_ready: Project) -> None:
    from conftest import write_control

    (code_ready.root / f"progress/acceptance_{LEAF_ID}.sha256").unlink()
    assert _status_next(code_ready).startswith(f"implement {LEAF_ID}")
    write_control(code_ready.root)  # manual mode: no attempts, the human seals
    assert _status_next(code_ready).startswith(f"implement {LEAF_ID}")


def test_a_failed_pr_resumes_as_a_pr_retry(code_ready: Project) -> None:
    code_ready.set_leaf("done")
    _fail(code_ready, "pr", 1, "could not push", fid="1.1")
    action = _resume_json(code_ready)
    assert action["kind"] == "pr"
    assert action["command"] == [GATE, "pr", "1.1"]
    assert action["authorized"] is True
    assert "could not push" in action["text"]


def test_a_pr_that_went_through_is_not_retried(code_ready: Project) -> None:
    code_ready.set_leaf("done")
    _fail(code_ready, "pr", 1, "could not push", fid="1.1")
    _ok(code_ready, "pr", fid="1.1")
    assert _resume_json(code_ready)["kind"] != "pr"


def test_two_in_progress_leaves_need_the_human(project: Project) -> None:
    data = json.loads((project.root / "feature_list.json").read_text())
    extra = {**data["features"][0]["subtasks"][0], "id": "1.1.2", "title": "second"}
    data["features"][0]["subtasks"].append(extra)
    project.write("feature_list.json", json.dumps(data))
    action = _resume_json(project)
    assert action["kind"] == "human"
    assert "1.1.1" in action["text"] and "1.1.2" in action["text"]
    assert action["command"] is None


def test_starting_the_next_subtask_waits_for_the_users_ok(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2").approve_roadmap()
    plan.approve_phase("01").sync()
    res = plan.run_py("_resume.py", "--json")
    assert res.returncode == 0, res.stderr
    action = json.loads(res.stdout)
    assert action["kind"] == "human"
    assert action["authorized"] is False
    assert action["command"] is None
    assert "start subtask 1.1.2" in action["text"]


def test_a_plan_level_gate_is_authorized(plan: Plan) -> None:
    res = plan.phase("01").phase("02").sync().run_py("_resume.py", "--json")
    action = json.loads(res.stdout)
    assert action["kind"] == "review"
    assert action["command"] == [GATE, "roadmap"]


def test_the_human_line_is_printed_without_a_command(code_ready: Project) -> None:
    _fail(code_ready, "code", 3, "check red (exit 2)")
    res = _py(code_ready, "_resume.py")
    assert res.returncode == 0
    assert "Wait for the human" in res.stdout
    assert "Run:" not in res.stdout


def test_an_authorized_action_prints_its_command(code_ready: Project) -> None:
    res = _py(code_ready, "_resume.py")
    assert f"Run: {GATE} code {LEAF_ID}" in res.stdout


def test_resume_writes_nothing(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    _fail(code_ready, "pr", 1, "x", fid="1.1")

    def snapshot() -> dict[str, bytes]:
        out = {}
        for path in sorted(code_ready.root.rglob("*")):
            rel = path.relative_to(code_ready.root).as_posix()
            if path.is_file() and not rel.startswith(("harness", ".claude", ".git/objects")):
                out[rel] = path.read_bytes()
        return out

    before = snapshot()
    _py(code_ready, "_resume.py")
    _py(code_ready, "_resume.py", "--json")
    assert snapshot() == before


@pytest.mark.parametrize(
    ("text", "kind", "command"),
    [
        (f"plan gate: {GATE} plan 1.1.1", "review", [GATE, "plan", "1.1.1"]),
        (f"task gate: {GATE} task 1.1 (then the user's OK)", "review", [GATE, "task", "1.1"]),
        (
            f"fix the roadmap (progress/roadmap_review.md), then roadmap gate: {GATE} roadmap",
            "fix",
            [GATE, "roadmap"],
        ),
        ("fix the spec of 1.1.1 (progress/plan_review_1.1.1.md), then plan gate", "fix", None),
        (
            f"fix the gaps of task 1.1 (progress/task_close_1.1.md): new subtask(s) for them, "
            f"then task-close gate: {GATE} task-close 1.1",
            "human",
            None,
        ),
        ("fix something nobody planned for", "human", None),
        (
            f"fix 1.1.1: the check was red at its last code gate (check red (exit 2)), "
            f"then code gate: {GATE} code 1.1.1",
            "fix",
            [GATE, "code", "1.1.1"],
        ),
        (f"fix 1.1.1: something new, then code gate: {GATE} code 1.1.1", "human", None),
        ("fix 1.1.1 (progress/review_1.1.2.md), then code gate", "human", None),
        (f"close: {GATE} doc 1.1.1", "close", [GATE, "doc", "1.1.1"]),
        ("start subtask 1.1.2 (leader: in_progress + make plan)", "human", None),
        ("kickoff task 1.2: one question at a time", "human", None),
        ("break down phase 02 into tasks and subtask specs", "human", None),
        ("start issue #7 (now): make issue-start N=7", "human", None),
        ("resume task 1.1: harness/issue.sh resume", "human", None),
        ("needs human (3 rounds): the code gate on 1.1.1 ...", "human", None),
        ("fix harness.toml: [review] max_rounds must be ...", "human", None),
        ("all planned work is done", "human", None),
        ("something nobody planned for", "human", None),
        (
            f"fix the roadmap after the human approves: {GATE} code 1.2.3",
            "human",
            None,
        ),
        (f"plan gate: {GATE} plan 1.1.1 && rm -rf /", "human", None),
        (f"implement 1.1.1 (implementer), then code gate: {GATE} code 1.1.2", "human", None),
        (f"close: {GATE} doc 1.1.1 then push", "human", None),
        (
            f"finish the seal of phase 01 (last attempt: seal failed after PHASE_APPROVED): "
            f"phase gate again: {GATE} phase 01",
            "seal",
            [GATE, "phase", "01"],
        ),
    ],
)
def test_classify_is_fail_safe(text: str, kind: str, command: list[str] | None) -> None:
    action = _resume.classify(text)
    assert action.kind == kind
    assert action.command == command
    assert action.authorized is (kind != "human")


def test_every_step_line_in_plan_status_is_classified() -> None:
    """A new `Next:` phrase in plan_status must be taught to classify(): an unknown
    one resumes as `wait for the human`, which is safe but would hide real work."""
    import re

    source = (TOOLKIT / "lib" / "plan_status.py").read_text()
    prefixes = {p for p in re.findall(r'(?:return|or \(?)\s*f?"([a-z][^"{]*)', source)}
    not_steps = {"pending Codex review", "unreadable ledger", "missing", "stale", "unknown"}
    unknown = {p for p in prefixes - not_steps if not _resume.known_prefix(p)}
    assert prefixes, "the scan found no step phrase"
    assert not unknown, sorted(unknown)


# ── rotation of the code-gate attempt records (#61 item 3) ──────────────────


def _records(project: Project, sid: str = LEAF_ID) -> list[str]:
    return sorted(p.name for p in _runs(project).glob(f"code.*_{sid}.json"))


def _record(project: Project, tag: int, rc: int = 1, sid: str = LEAF_ID) -> None:
    log = project.root / ".git" / "check.log"
    log.write_text("FAILED\n")
    res = _py(project, "_gate_run.py", "record", sid, str(rc), str(log), str(tag))
    assert res.returncode in (0, 10), res.stderr


def test_red_attempt_records_are_rotated(code_ready: Project) -> None:
    import _gate_run

    for tag in range(1, 10):
        _record(code_ready, tag)
    names = _records(code_ready)
    assert len(names) == _gate_run.KEEP_ATTEMPTS
    assert f"code.9_{LEAF_ID}.json" in names  # the newest is always kept


def test_rotation_keeps_the_approved_record_and_other_subtasks(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED")
    bind_code_approval(code_ready.root, LEAF_ID)
    for tag in range(1, 10):
        _record(code_ready, tag)
    assert f"code.approved_{LEAF_ID}.json" in _records(code_ready)
    import _gate_run

    numbered = [n for n in _records(code_ready) if not n.startswith("code.approved")]
    assert len(numbered) == _gate_run.KEEP_ATTEMPTS


def test_rotation_never_drops_the_record_just_written(code_ready: Project) -> None:
    # Same created_at second for every record: the one just written must survive.
    for tag in (900, 800, 700, 600, 500, 400, 300):
        _record(code_ready, tag)
    assert f"code.300_{LEAF_ID}.json" in _records(code_ready)


def test_rotation_keeps_the_record_of_a_live_run(code_ready: Project) -> None:
    proc = subprocess.Popen(["bash", "-c", "exec -a review_with_codex.sh sleep 60"])
    try:
        _record(code_ready, proc.pid)
        for tag in range(1, 10):
            _record(code_ready, tag)
        assert f"code.{proc.pid}_{LEAF_ID}.json" in _records(code_ready)
    finally:
        proc.kill()
        proc.wait()


def test_the_approval_still_binds_after_a_rotation(code_ready: Project) -> None:
    for tag in range(1, 9):
        _record(code_ready, tag)
    _record(code_ready, 42, rc=0)
    code_ready.set_verdict("code", "APPROVED")
    res = _py(code_ready, "_gate_run.py", "approve", LEAF_ID, "42")
    assert res.returncode == 0, res.stderr
    assert _py(code_ready, "_gate_run.py", "fresh", LEAF_ID).returncode == 0


def test_stray_temp_files_are_cleaned(code_ready: Project) -> None:
    _runs(code_ready).mkdir(parents=True, exist_ok=True)
    stray = _runs(code_ready) / f".code.1_{LEAF_ID}.json.abc.tmp"
    stray.write_text("{}")
    old = 1_000_000_000
    os.utime(stray, (old, old))
    _record(code_ready, 1)
    assert not stray.exists()


# ── round 2 (Codex R2) ──────────────────────────────────────────────────────


def test_outcomes_count_in_the_order_they_happen(tmp_path: Path) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    _resume.begin(path, "1")
    _resume.begin(path, "2", live=lambda tag: tag == "1")  # A still running
    _resume.end(path, "2", "fail", "B failed")
    _resume.end(path, "1", "ok", "A approved")
    assert _resume.failures(_resume.read_entries(path)) == 0
    _resume.begin(path, "3")
    _resume.begin(path, "4", live=lambda tag: tag == "3")
    _resume.end(path, "4", "ok", "D approved")
    _resume.end(path, "3", "fail", "C failed")
    assert _resume.failures(_resume.read_entries(path)) == 1


def test_a_close_that_stopped_before_its_message_needs_the_human(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    code_ready.set_leaf("done")  # the documenter set done, then died before the message
    _fail(code_ready, "close", 1, "documenter exited 1")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None
    assert "stopped before its commit" in action["text"]


def test_a_failed_roadmap_seal_resumes_as_the_roadmap_gate(code_ready: Project) -> None:
    _fail(code_ready, "roadmap", 1, "seal failed after ROADMAP_APPROVED", fid="roadmap")
    action = _resume_json(code_ready)
    assert action["kind"] == "seal"
    assert action["command"] == [GATE, "roadmap"]
    assert action["text"].startswith("finish the seal of the roadmap")


def test_a_stale_index_authorizes_nothing_but_make_plan(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "in_progress").approve_roadmap().approve_phase("01")
    plan.sync()
    spec = plan.root / "docs/plan/phase-01-p01/1.1.1-s.md"
    spec.write_text(spec.read_text().replace("status: in_progress", "status: done"))
    action = json.loads(plan.run_py("_resume.py", "--json").stdout)
    assert action["kind"] == "fix"
    assert action["command"] is None
    assert "make plan" in action["text"]


def test_a_refused_plan_attempt_keeps_the_red_record(code_ready: Project) -> None:
    red = code_ready.root / ".git" / "harness-oracle" / f"red_{LEAF_ID}.json"
    red.parent.mkdir(parents=True, exist_ok=True)
    red.write_text("{}")
    _fail(code_ready, "plan", 3, "reviewer exited 1")
    res = code_ready.run("review_with_codex.sh", None, "plan", LEAF_ID)
    assert res.returncode != 0
    assert "needs human (3 failed attempts)" in res.stderr
    assert red.exists()


def test_a_failed_issue_seal_stops_its_leaf(plan: Plan) -> None:
    import shutil

    del shutil
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01")
    plan.issue(14).isub("ISS-14.1", "in_progress").approve_issue(14).sync()
    subprocess.run(["git", "init", "-q"], cwd=plan.root, check=True)
    runs = plan.root / ".git" / "harness-gate-runs"
    _resume.begin(_resume.ledger_path(runs, "issue", "ISS-14"), "1")
    _resume.end(_resume.ledger_path(runs, "issue", "ISS-14"), "1", "fail", "seal failed after X")
    action = json.loads(plan.run_py("_resume.py", "--json").stdout)
    assert action["kind"] == "seal"
    assert action["command"] == [GATE, "issue", "ISS-14"]


def test_a_seal_failure_survives_a_failed_retry(code_ready: Project) -> None:
    _fail(code_ready, "roadmap", 1, "seal failed after ROADMAP_APPROVED", fid="roadmap")
    _fail(code_ready, "roadmap", 1, "reviewer exited 1", fid="roadmap")
    action = _resume_json(code_ready)
    assert action["kind"] == "seal"
    assert action["command"] == [GATE, "roadmap"]


def test_a_failed_code_attempt_after_an_approval_comes_first(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    _ok(code_ready, "code")
    _fail(code_ready, "code", 1, "check red (exit 2)")
    action = _resume_json(code_ready)
    assert action["kind"] == "fix" and "check red (exit 2)" in action["text"]
    _fail(code_ready, "code", 2, "check red (exit 2)")
    assert _resume_json(code_ready)["kind"] == "human"


def test_a_seal_failure_survives_a_trimmed_streak(code_ready: Project) -> None:
    code_ready.write("harness.toml", "[review]\nmax_attempts = 0\n")
    _fail(code_ready, "roadmap", 1, "seal failed after ROADMAP_APPROVED", fid="roadmap")
    _fail(code_ready, "roadmap", _resume.MAX_ENTRIES + 2, "reviewer exited 1", fid="roadmap")
    assert _resume_json(code_ready)["kind"] == "seal"


def test_a_done_close_retry_needs_a_fresh_approval(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    code_ready.set_leaf("done")
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    code_ready.write("src/pkg/domain/user.py", "changed = 1\n")  # approval now stale
    _fail(code_ready, "close", 1, "the code approval is stale or missing")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None
    assert "no longer covers" in action["text"]


def test_a_failed_reseal_is_not_hidden_by_an_older_seal(code_ready: Project) -> None:
    assert (code_ready.root / f"progress/acceptance_{LEAF_ID}.sha256").is_file()
    _fail(code_ready, "plan", 1, "seal failed after PLAN_APPROVED")
    action = _resume_json(code_ready)
    assert action["kind"] == "seal"
    assert action["command"] == [GATE, "plan", LEAF_ID]


def test_trimming_keeps_live_pending_attempts(tmp_path: Path) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    live = {"1"}
    _resume.begin(path, "1")
    for i in range(2, _resume.MAX_ENTRIES + 5):
        _resume.begin(path, str(i), live=lambda tag: tag in live)
        _resume.end(path, str(i), "ok", "done")
    entries = _resume.read_entries(path)
    assert [e.tag for e in entries if e.outcome == "pending"] == ["1"]
    _resume.end(path, "1", "fail", "late failure")
    entries = _resume.read_entries(path)
    assert [e.tag for e in entries].count("1") == 1
    assert _resume.failures(entries) == 1


@pytest.mark.parametrize(
    ("gate", "verdict", "expected"),
    [
        ("plan", "CHANGES_REQUESTED", f"fix the spec of {LEAF_ID}"),
        ("code", "CHANGES_REQUESTED", f"fix {LEAF_ID} (progress/review_{LEAF_ID}.md)"),
    ],
)
def test_requested_changes_come_before_a_crashed_retry(
    code_ready: Project, gate: str, verdict: str, expected: str
) -> None:
    code_ready.set_verdict(gate, verdict)
    _fail(code_ready, gate, 1, "reviewer exited 3")
    action = _resume_json(code_ready)
    assert action["kind"] == "fix"
    assert action["text"].startswith(expected)
    assert "reviewer exited 3" in action["text"]


def test_a_run_in_progress_is_waited_for(code_ready: Project) -> None:
    proc = subprocess.Popen(["bash", "-c", "exec -a review_with_codex.sh sleep 60"])
    try:
        res = _py(code_ready, "_resume.py", "begin", "code", LEAF_ID, str(proc.pid))
        assert res.returncode == 0, res.stderr
        action = _resume_json(code_ready)
        assert action["kind"] == "human" and action["command"] is None
        assert action["text"].startswith("wait: a code run")
    finally:
        proc.kill()
        proc.wait()
    assert _resume_json(code_ready)["kind"] == "review"  # gone: a crashed run, retry


def test_manual_mode_hands_a_failed_seal_to_the_human(code_ready: Project) -> None:
    from conftest import write_control

    _fail(code_ready, "plan", 1, "seal failed after PLAN_APPROVED")
    _fail(code_ready, "phase", 1, "seal failed after PHASE_APPROVED", fid="01")
    write_control(code_ready.root)
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None
    assert f"{GATE} seal phase 01" in action["text"]
    (code_ready.root / ".git/harness-gate-runs/attempts_phase_01.json").unlink()
    action = _resume_json(code_ready)
    assert action["kind"] == "human"
    assert f"{GATE} seal {LEAF_ID}" in action["text"]


@pytest.mark.parametrize(("step", "fid"), [("pr", "1.1"), ("close", LEAF_ID), ("phase", "01")])
def test_any_live_run_is_waited_for(code_ready: Project, step: str, fid: str) -> None:
    proc = subprocess.Popen(["bash", "-c", "exec -a review_with_codex.sh sleep 60"])
    try:
        _fail(code_ready, step, 1, "seal failed after X", fid=fid)
        assert _py(code_ready, "_resume.py", "begin", step, fid, str(proc.pid)).returncode == 0
        _ok(code_ready, step, fid=fid)  # an overlapping run ended after it started
        action = _resume_json(code_ready)
        assert action["kind"] == "human" and action["command"] is None
        assert action["text"].startswith(f"wait: a {step} run on {fid}")
    finally:
        proc.kill()
        proc.wait()


def test_a_live_run_comes_before_a_stale_index(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "in_progress").approve_roadmap().approve_phase("01")
    plan.sync()
    subprocess.run(["git", "init", "-q"], cwd=plan.root, check=True)
    spec = plan.root / "docs/plan/phase-01-p01/1.1.1-s.md"
    spec.write_text(spec.read_text().replace("status: in_progress", "status: done"))
    runs = plan.root / ".git" / "harness-gate-runs"
    proc = subprocess.Popen(["bash", "-c", "exec -a review_with_codex.sh sleep 60"])
    try:
        _resume.begin(_resume.ledger_path(runs, "close", "1.1.1"), str(proc.pid))
        action = json.loads(plan.run_py("_resume.py", "--json").stdout)
        assert action["kind"] == "human" and action["text"].startswith("wait: a close run")
    finally:
        proc.kill()
        proc.wait()


def test_a_killed_overlapping_run_still_counts(tmp_path: Path) -> None:
    path = tmp_path / "attempts_code_1.1.1.json"
    _resume.begin(path, "1")
    _resume.begin(path, "2", live=lambda tag: tag == "1")
    _resume.end(path, "2", "ok", "B approved")
    _resume.begin(path, "3")  # A (tag 1) is dead now: its failure comes after B's ok
    entries = _resume.read_entries(path)
    assert [e.tag for e in entries] == ["2", "1", "3"]
    assert _resume.failures(entries) == 2


def test_an_unreadable_close_ledger_of_a_done_leaf_needs_the_human(code_ready: Project) -> None:
    code_ready.set_leaf("done")
    _ledger(code_ready, "close").parent.mkdir(parents=True, exist_ok=True)
    _ledger(code_ready, "close").write_text("{broken")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and "no close commit" in action["text"]


def test_readers_see_a_killed_overlapping_run_before_any_begin(code_ready: Project) -> None:
    code_ready.write("harness.toml", "[review]\nmax_attempts = 1\n")
    proc = subprocess.Popen(["bash", "-c", "exec -a review_with_codex.sh sleep 60"])
    assert _py(code_ready, "_resume.py", "begin", "code", LEAF_ID, str(proc.pid)).returncode == 0
    _ok(code_ready, "code")  # B ends well while A still runs
    proc.kill()
    proc.wait()  # A is dead now: a failure after B's ok
    assert _py(code_ready, "_resume.py", "budget", "code", LEAF_ID).returncode == 1
    assert _resume_json(code_ready)["text"].startswith("needs human (1 failed attempts)")


def test_a_corrupt_code_ledger_stops_the_close(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    _ledger(code_ready, "code").parent.mkdir(parents=True, exist_ok=True)
    _ledger(code_ready, "code").write_text("{broken")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None


def test_a_killed_overlapping_pr_attempt_is_retried(code_ready: Project) -> None:
    code_ready.set_leaf("done")
    proc = subprocess.Popen(["bash", "-c", "exec -a review_with_codex.sh sleep 60"])
    assert _py(code_ready, "_resume.py", "begin", "pr", "1.1", str(proc.pid)).returncode == 0
    _ok(code_ready, "pr", fid="1.1")
    proc.kill()
    proc.wait()
    action = _resume_json(code_ready)
    assert action["kind"] == "pr" and action["command"] == [GATE, "pr", "1.1"]


@pytest.mark.parametrize(("step", "fid"), [("roadmap", "roadmap"), ("plan", LEAF_ID)])
def test_a_corrupt_ledger_of_an_approved_gate_needs_the_human(
    code_ready: Project, step: str, fid: str
) -> None:
    _ledger(code_ready, step, fid).parent.mkdir(parents=True, exist_ok=True)
    _ledger(code_ready, step, fid).write_text("{broken")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None
    assert f"attempts_{step}_" in action["text"]


def test_a_corrupt_ledger_needs_the_human_even_with_no_cap(code_ready: Project) -> None:
    code_ready.write("harness.toml", "[review]\nmax_attempts = 0\n")
    _ledger(code_ready, "code").parent.mkdir(parents=True, exist_ok=True)
    _ledger(code_ready, "code").write_text("{broken")
    assert _py(code_ready, "_resume.py", "budget", "code", LEAF_ID).returncode == 1
    assert _resume_json(code_ready)["kind"] == "human"
    red = code_ready.root / ".git" / "harness-oracle" / f"red_{LEAF_ID}.json"
    red.parent.mkdir(parents=True, exist_ok=True)
    red.write_text("{}")
    _ledger(code_ready, "plan").write_text("{broken")
    assert code_ready.run("review_with_codex.sh", None, "plan", LEAF_ID).returncode != 0
    assert red.exists()


def test_rotation_drops_an_undecodable_record_first(code_ready: Project) -> None:
    _runs(code_ready).mkdir(parents=True, exist_ok=True)
    bad = _runs(code_ready) / f"code.5000_{LEAF_ID}.json"
    bad.write_bytes(b"\xff\xfe not utf-8")
    for tag in range(1, 6):
        _record(code_ready, tag)
    assert not bad.exists()
    assert len(_records(code_ready)) == 5


def test_manual_mode_never_rotates_numbered_records(code_ready: Project) -> None:
    from conftest import write_control

    write_control(code_ready.root)
    for tag in range(1, 10):
        _record(code_ready, tag)
    assert len(_records(code_ready)) == 9


def test_make_status_does_not_act_on_a_stale_index(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "in_progress").approve_roadmap().approve_phase("01")
    plan.sync()
    spec = plan.root / "docs/plan/phase-01-p01/1.1.1-s.md"
    spec.write_text(spec.read_text().replace("status: in_progress", "status: done"))
    out = plan.run_py("plan_status.py").stdout
    assert f"Next: {_resume.STALE_INDEX}" in out


def test_a_live_gate_run_marker_is_waited_for(code_ready: Project) -> None:
    proc = subprocess.Popen(["bash", "-c", "exec -a review_with_codex.sh sleep 60"])
    try:
        _runs(code_ready).mkdir(parents=True, exist_ok=True)
        (_runs(code_ready) / f"run_{proc.pid}").write_text("")
        action = _resume_json(code_ready)
        assert action["kind"] == "human" and action["text"].startswith("wait: a gate run")
    finally:
        proc.kill()
        proc.wait()
    assert _resume_json(code_ready)["kind"] == "implement"


# ── #105: no docs/plan/ (feature_list.json unchecked) warns, same action ────


def test_resume_without_docs_plan_warns_and_keeps_the_action(code_ready: Project) -> None:
    assert not (code_ready.root / "docs/plan").exists()
    expected = _status_next(code_ready)
    res = _py(code_ready, "_resume.py")
    assert res.returncode == 0, res.stderr
    assert "WARN" in res.stderr
    assert "docs/plan/" in res.stderr and "feature_list.json" in res.stderr
    assert f"Resume: implement · {expected}" in res.stdout
    assert f"Run: {GATE} code {LEAF_ID}" in res.stdout


def test_resume_json_without_docs_plan_lists_the_warning(code_ready: Project) -> None:
    res = _py(code_ready, "_resume.py", "--json")
    assert res.returncode == 0, res.stderr
    action = json.loads(res.stdout)
    assert action["text"] == _status_next(code_ready)
    assert action["command"] == [GATE, "code", LEAF_ID]
    assert action["authorized"] is True
    assert len(action["warnings"]) == 1
    assert "docs/plan/" in action["warnings"][0]
    assert "feature_list.json" in action["warnings"][0]
    assert "WARN" in res.stderr


def test_resume_with_docs_plan_does_not_warn(plan: Plan) -> None:
    res = plan.phase("01").phase("02").sync().run_py("_resume.py", "--json")
    assert res.returncode == 0, res.stderr
    action = json.loads(res.stdout)
    assert action["command"] == [GATE, "roadmap"]
    assert action["warnings"] == []
    assert "WARN" not in res.stderr
    text = plan.run_py("_resume.py")
    assert text.returncode == 0, text.stderr
    assert "WARN" not in text.stderr and "docs/plan/" not in text.stderr


def test_a_stale_index_with_docs_plan_does_not_warn_about_the_source(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "in_progress").approve_roadmap().approve_phase("01")
    plan.sync()
    spec = plan.root / "docs/plan/phase-01-p01/1.1.1-s.md"
    spec.write_text(spec.read_text().replace("status: in_progress", "status: done"))
    res = plan.run_py("_resume.py", "--json")
    action = json.loads(res.stdout)
    assert action["kind"] == "fix" and action["warnings"] == []
    assert "WARN" not in res.stderr


# ── i107: a waiting close message under a fresh approval resumes as `close` ──


def test_a_waiting_close_message_without_a_failed_attempt_is_a_close_retry() -> None:
    text = (
        f"retry the close of {LEAF_ID} (progress/close_{LEAF_ID}.msg is waiting): "
        f"{GATE} close {LEAF_ID}"
    )
    action = _resume.classify(text)
    assert action.kind == "close" and action.authorized is True
    assert action.command == [GATE, "close", LEAF_ID]


def test_a_close_retry_with_mismatched_ids_waits_for_the_human() -> None:
    text = f"retry the close of {LEAF_ID} (progress/close_1.1.2.msg is waiting): {GATE} close 1.1.2"
    action = _resume.classify(text)
    assert action.kind == "human" and action.command is None


def test_with_code_attempts_a_stale_approval_comes_before_a_waiting_message(
    code_ready: Project,
) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    _ok(code_ready, "code")
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    code_ready.write(DECLARED, "LATE = 1\n")
    expected = f"the code approval of {LEAF_ID} no longer covers the tree: code gate again: "
    assert _status_next(code_ready) == f"{expected}{GATE} code {LEAF_ID}"
    assert _resume_json(code_ready)["command"] == [GATE, "code", LEAF_ID]


def test_the_close_budget_comes_before_a_waiting_message(code_ready: Project) -> None:
    code_ready.set_verdict("code", "APPROVED", with_gate_record=True)
    code_ready.write(f"progress/close_{LEAF_ID}.msg", "feat: x [1.1.1]\n")
    _fail(code_ready, "close", 3, "git commit failed")
    action = _resume_json(code_ready)
    assert action["kind"] == "human" and action["command"] is None
    assert action["text"].startswith("needs human (3 failed attempts)")
    assert _status_next(code_ready) == action["text"]
