"""Approved plan seals: roadmap and phase breakdowns cannot change silently."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import Plan

SPEC_DIR = "docs/plan/phase-01-p01"


def _guard(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_guard.py", *args)


def _edit(plan: Plan, rel: str, old: str, new: str) -> None:
    path = plan.root / rel
    path.write_text(path.read_text().replace(old, new))


def _approved_phase(plan: Plan) -> Plan:
    plan.phase("01", "1.1").sub("1.1.1").sub("1.1.2").phase("02").approve_roadmap().sync()
    assert _guard(plan, "seal", "roadmap").returncode == 0
    plan.approve_phase("01")
    res = _guard(plan, "seal", "phase", "01")
    assert res.returncode == 0, res.stdout + res.stderr
    return plan


def test_roadmap_seal_needs_approval(plan: Plan) -> None:
    plan.phase("01").sync()
    res = _guard(plan, "seal", "roadmap")
    assert res.returncode == 1
    assert "ROADMAP_APPROVED" in res.stdout + res.stderr


def test_phase_seal_needs_approval(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()
    assert _guard(plan, "seal", "phase", "01").returncode == 1


def test_sealed_plan_passes_integrity(plan: Plan) -> None:
    res = _guard(_approved_phase(plan), "integrity")
    assert res.returncode == 0, res.stdout


def test_changing_a_phase_goal_after_roadmap_approval_fails(plan: Plan) -> None:
    _approved_phase(plan)
    _edit(plan, "docs/plan/phase-02-p02.md", "P02 goal.", "Another goal.")
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert "docs/plan/phase-02-p02.md changed after the roadmap was approved" in res.stdout


def test_adding_a_phase_after_roadmap_approval_fails(plan: Plan) -> None:
    _approved_phase(plan)
    (plan.root / "docs/plan/phase-03-p03.md").write_text("# Phase 03 — P03\n- goal: x\n")
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert "docs/plan/phase-03-p03.md added after the roadmap was approved" in res.stdout


@pytest.mark.e2e
def test_vision_edit_after_roadmap_approval_fails_but_the_index_does_not(plan: Plan) -> None:
    _approved_phase(plan)
    _edit(plan, f"{SPEC_DIR}/1.1.1-s.md", "status: pending", "status: blocked")
    assert plan.run_py("sync_plan.py").returncode == 0  # regenerates the PLAN.md index
    assert _guard(plan, "integrity").returncode == 0
    plan_md = plan.root / "docs/plan/PLAN.md"
    plan_md.write_text("# Changed vision\n" + plan_md.read_text())
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert "docs/plan/PLAN.md changed after the roadmap was approved" in res.stdout


def test_status_moves_freely_after_phase_approval(plan: Plan) -> None:
    _approved_phase(plan)
    _edit(plan, f"{SPEC_DIR}/1.1.1-s.md", "status: pending", "status: done")
    assert _guard(plan, "integrity").returncode == 0


def test_editing_a_spec_after_phase_approval_fails(plan: Plan) -> None:
    _approved_phase(plan)
    _edit(plan, f"{SPEC_DIR}/1.1.2-s.md", "src/app/m112.py", "src/app/other.py")
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{SPEC_DIR}/1.1.2-s.md changed after phase 01 was approved" in res.stdout


def test_adding_a_spec_after_phase_approval_fails(plan: Plan) -> None:
    _approved_phase(plan)
    spec = (plan.root / f"{SPEC_DIR}/1.1.2-s.md").read_text().replace("1.1.2", "1.1.3")
    (plan.root / f"{SPEC_DIR}/1.1.3-s.md").write_text(spec)
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{SPEC_DIR}/1.1.3-s.md added after phase 01 was approved" in res.stdout


@pytest.mark.e2e
def test_breaking_down_a_phase_keeps_the_roadmap_seal(plan: Plan) -> None:
    plan.phase("01").phase("02").approve_roadmap().sync()
    assert _guard(plan, "seal", "roadmap").returncode == 0
    plan.phase("01", "1.1").sub("1.1.1").sync()
    res = _guard(plan, "integrity")
    assert res.returncode == 0, res.stdout


def test_spec_under_its_plan_gate_only_warns(plan: Plan) -> None:
    _approved_phase(plan)
    _edit(plan, f"{SPEC_DIR}/1.1.1-s.md", "status: pending", "status: in_progress")
    _edit(plan, f"{SPEC_DIR}/1.1.1-s.md", "src/app/m111.py", "src/app/fixed.py")
    assert plan.run_py("sync_plan.py").returncode == 0
    plan.verdict("plan_review_1.1.1.md", "CHANGES_REQUESTED")
    res = _guard(plan, "integrity")
    assert res.returncode == 0, res.stdout
    assert "WARN" in res.stdout
    assert "1.1.1" in res.stdout


@pytest.mark.e2e
def test_plan_gate_approval_reseals_the_spec(plan: Plan) -> None:
    _approved_phase(plan)
    _edit(plan, f"{SPEC_DIR}/1.1.1-s.md", "status: pending", "status: in_progress")
    _edit(plan, f"{SPEC_DIR}/1.1.1-s.md", "src/app/m111.py", "src/app/fixed.py")
    assert plan.run_py("sync_plan.py").returncode == 0
    plan.verdict("plan_review_1.1.1.md", "PLAN_APPROVED")
    acceptance = plan.root / "tests/acceptance/test_1.1.1.py"
    acceptance.parent.mkdir(parents=True)
    acceptance.write_text("def test_ac1_works(): ...\n")
    res = _guard(plan, "seal", "1.1.1")
    assert res.returncode == 0, res.stdout + res.stderr
    res = _guard(plan, "integrity")
    assert res.returncode == 0, res.stdout
    assert "plan gate" not in res.stdout


def test_approved_but_unsealed_plan_warns(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().approve_phase("01").sync()
    res = _guard(plan, "integrity")
    assert res.returncode == 0
    assert "roadmap is approved but not sealed" in res.stdout
    assert "phase 01 is approved but not sealed" in res.stdout


def test_seal_files_are_codex_owned_paths(plan: Plan) -> None:
    _approved_phase(plan)
    assert (plan.root / "progress/plan_roadmap.sha256").is_file()
    assert (plan.root / "progress/plan_phase_01.sha256").is_file()


def test_plan_manifest_lists_relative_paths(plan: Plan) -> None:
    _approved_phase(plan)
    paths = [
        line.split(maxsplit=1)[1]
        for line in (plan.root / "progress/plan_phase_01.sha256").read_text().splitlines()
    ]
    assert paths == [
        "docs/plan/phase-01-p01.md",
        f"{SPEC_DIR}/1.1.1-s.md",
        f"{SPEC_DIR}/1.1.2-s.md",
    ]
    assert all(not Path(p).is_absolute() for p in paths)
