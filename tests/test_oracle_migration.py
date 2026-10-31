"""Issues lane: an issue subtask may rewrite sealed oracles of other subtasks, but only
the ones its approved spec declares in `migrates:`, re-sealed atomically at its plan gate."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from conftest import Plan

ORACLE = "tests/acceptance/test_1.1.1.py"
OTHER = "tests/acceptance/test_1.1.2.py"
SPEC = "docs/plan/issues/ISS-14-i14/ISS-14.1-s.md"
OWN = "tests/acceptance/test_ISS-14.1.py"


def _guard(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_guard.py", *args)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seal_oracle(plan: Plan, rel: str, leaf: str, text: str) -> None:
    path = plan.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    (plan.root / f"progress/acceptance_{leaf}.sha256").write_text(f"{_sha(path)}  {rel}\n")


def _issue_with_migration(plan: Plan, migrates: list[str] | None = None) -> Plan:
    """Phase 01 done with sealed oracles; ISS-14.1 declares `migrates:`, issue approved."""
    plan.phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2", "done")
    plan.approve_roadmap().approve_phase("01")
    plan.issue(14).isub("ISS-14.1", "in_progress").sync()
    _seal_oracle(plan, ORACLE, "1.1.1", "def test_ac1_mask(): assert mask == 0x10C\n")
    _seal_oracle(plan, OTHER, "1.1.2", "def test_ac1_other(): ...\n")
    lines = "".join(f"  - {rel}\n" for rel in (migrates if migrates is not None else [ORACLE]))
    spec = plan.root / SPEC
    spec.write_text(spec.read_text().replace("- acceptance:", f"- migrates:\n{lines}- acceptance:"))
    assert plan.run_py("sync_plan.py").returncode == 0
    plan.approve_issue(14)
    res = _guard(plan, "seal", "issue", "ISS-14")
    assert res.returncode == 0, res.stdout + res.stderr
    return plan


def _codex_plan_gate(
    plan: Plan, new_oracle: str = "def test_ac1_flags(): assert invalid[2]\n"
) -> None:
    """What Codex does at the plan gate: write its oracle, migrate the declared one."""
    (plan.root / OWN).write_text("def test_ac1_works(): ...\n")
    (plan.root / ORACLE).write_text(new_oracle)
    plan.verdict("plan_review_ISS-14.1.md", "PLAN_APPROVED")


# ── snapshot frozen at the issue gate ────────────────────────────────────────


def test_issue_seal_freezes_the_migration_snapshot(plan: Plan) -> None:
    _issue_with_migration(plan)
    snapshot = json.loads((plan.root / "progress/issue_migrations_ISS-14.json").read_text())
    assert snapshot == {"ISS-14.1": [ORACLE]}
    manifest = (plan.root / "progress/plan_issue_ISS-14.sha256").read_text()
    assert "progress/issue_migrations_ISS-14.json" in manifest


def test_changing_migrates_after_the_issue_gate_breaks_the_issue_seal(plan: Plan) -> None:
    _issue_with_migration(plan)
    spec = plan.root / SPEC
    spec.write_text(spec.read_text().replace(f"  - {ORACLE}\n", f"  - {ORACLE}\n  - {OTHER}\n"))
    assert plan.run_py("sync_plan.py").returncode == 0
    res = _guard(plan, "integrity")
    assert res.returncode == 0  # the spec is in its open plan gate: a warning only
    res = _guard(plan, "migrates-ready", "ISS-14.1")
    assert res.returncode == 1
    assert "ISS-14.1: migrates: differs from the list approved at the issue gate" in (
        res.stdout + res.stderr
    )


# ── plan gate precondition ───────────────────────────────────────────────────


def test_migrates_ready_passes_for_sealed_untouched_oracles(plan: Plan) -> None:
    res = _guard(_issue_with_migration(plan), "migrates-ready", "ISS-14.1")
    assert res.returncode == 0, res.stdout + res.stderr


def test_migrates_ready_refuses_an_ownerless_oracle(plan: Plan) -> None:
    rel = "tests/acceptance/test_9.9.9.py"
    _issue_with_migration(plan, [rel])
    (plan.root / rel).write_text("def test_x(): ...\n")
    res = _guard(plan, "migrates-ready", "ISS-14.1")
    assert res.returncode == 1
    assert f"{rel} is not sealed by any acceptance manifest" in res.stdout + res.stderr


def test_migrates_ready_refuses_an_absent_oracle(plan: Plan) -> None:
    _issue_with_migration(plan)
    (plan.root / ORACLE).unlink()
    res = _guard(plan, "migrates-ready", "ISS-14.1")
    assert res.returncode == 1
    assert f"{ORACLE} does not exist" in res.stdout + res.stderr


def test_migrates_ready_refuses_an_already_modified_oracle(plan: Plan) -> None:
    _issue_with_migration(plan)
    (plan.root / ORACLE).write_text("tampered\n")
    res = _guard(plan, "migrates-ready", "ISS-14.1")
    assert res.returncode == 1
    assert f"{ORACLE} no longer matches its seal" in res.stdout + res.stderr


def test_migrates_ready_without_migrates_is_a_no_op(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2").sync()
    assert _guard(plan, "migrates-ready", "1.1.2").returncode == 0


# ── seal at PLAN_APPROVED ────────────────────────────────────────────────────


def test_plan_gate_seal_reseals_the_migrated_oracle(plan: Plan) -> None:
    _issue_with_migration(plan)
    old = _sha(plan.root / ORACLE)
    _codex_plan_gate(plan)
    assert _guard(plan, "integrity").returncode == 1  # before the seal: the oracle changed
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 0, res.stdout + res.stderr
    new = _sha(plan.root / ORACLE)
    owner = (plan.root / "progress/acceptance_1.1.1.sha256").read_text()
    assert owner == f"{new}  {ORACLE}\n"
    record = json.loads((plan.root / "progress/oracle_migrations/ISS-14.1.json").read_text())
    assert record == {
        "leaf": "ISS-14.1",
        "complete": True,
        "paths": {ORACLE: {"old": old, "new": new, "owners": ["progress/acceptance_1.1.1.sha256"]}},
    }
    res = _guard(plan, "integrity")
    assert res.returncode == 0, res.stdout


def test_an_undeclared_sealed_change_is_refused(plan: Plan) -> None:
    _issue_with_migration(plan)
    _codex_plan_gate(plan)
    (plan.root / OTHER).write_text("changed too\n")
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 1
    assert f"{OTHER} changed but is not in the approved migrates:" in res.stdout + res.stderr
    assert not (plan.root / "progress/oracle_migrations/ISS-14.1.json").exists()
    assert not (plan.root / "progress/acceptance_ISS-14.1.sha256").exists()


def test_a_declared_oracle_left_unchanged_is_refused(plan: Plan) -> None:
    _issue_with_migration(plan)
    (plan.root / OWN).write_text("def test_ac1_works(): ...\n")
    plan.verdict("plan_review_ISS-14.1.md", "PLAN_APPROVED")
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 1
    assert f"{ORACLE} is in migrates: but was not changed" in res.stdout + res.stderr


def test_seal_refuses_migrates_that_differ_from_the_snapshot(plan: Plan) -> None:
    _issue_with_migration(plan)
    spec = plan.root / SPEC
    spec.write_text(spec.read_text().replace(f"  - {ORACLE}\n", f"  - {ORACLE}\n  - {OTHER}\n"))
    assert plan.run_py("sync_plan.py").returncode == 0
    _codex_plan_gate(plan)
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 1
    assert "differs from the list approved at the issue gate" in res.stdout + res.stderr


def test_interrupted_seal_is_completed_by_a_retry(plan: Plan) -> None:
    _issue_with_migration(plan)
    _codex_plan_gate(plan)
    assert _guard(plan, "seal", "ISS-14.1").returncode == 0
    # simulate a crash after the record, before the owner manifest was rewritten
    owner = plan.root / "progress/acceptance_1.1.1.sha256"
    record = json.loads((plan.root / "progress/oracle_migrations/ISS-14.1.json").read_text())
    owner.write_text(f"{record['paths'][ORACLE]['old']}  {ORACLE}\n")
    assert _guard(plan, "integrity").returncode == 1
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _guard(plan, "integrity").returncode == 0


def test_retry_refuses_an_oracle_edited_after_approval(plan: Plan) -> None:
    _issue_with_migration(plan)
    _codex_plan_gate(plan)
    assert _guard(plan, "seal", "ISS-14.1").returncode == 0
    owner = plan.root / "progress/acceptance_1.1.1.sha256"
    record = json.loads((plan.root / "progress/oracle_migrations/ISS-14.1.json").read_text())
    owner.write_text(f"{record['paths'][ORACLE]['old']}  {ORACLE}\n")
    (plan.root / ORACLE).write_text("weakened after approval\n")
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 1
    assert f"{ORACLE} no longer matches the migration record" in res.stdout + res.stderr


def test_retry_refuses_an_incomplete_record(plan: Plan) -> None:
    _issue_with_migration(plan)
    _codex_plan_gate(plan)
    folder = plan.root / "progress/oracle_migrations"
    folder.mkdir(parents=True)
    (folder / "ISS-14.1.json").write_text(json.dumps({"leaf": "ISS-14.1", "complete": False}))
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 1
    assert "incomplete migration record" in res.stdout + res.stderr


def test_plan_gate_refuses_when_a_declared_oracle_is_not_ready(plan: Plan) -> None:
    _issue_with_migration(plan)
    (plan.root / ORACLE).write_text("tampered\n")
    toolkit = Path(__file__).resolve().parents[1]
    (plan.root / ".claude").mkdir()
    (plan.root / ".claude/codex").symlink_to(toolkit / "codex")
    res = subprocess.run(
        [
            "bash",
            str(toolkit / "lib/review_with_codex.sh"),
            "plan",
            "ISS-14.1",
        ],
        cwd=plan.root,
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "HARNESS_CODEX_MODE": "manual",
            "CLAUDE_PROJECT_DIR": str(plan.root),
        },
    )
    assert res.returncode != 0
    assert f"{ORACLE} no longer matches its seal" in res.stdout + res.stderr
