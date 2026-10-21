"""review_with_codex.sh: least-privilege sandbox and acceptance-test sealing."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from conftest import LEAF_ID, Project

UV_CACHE = "/tmp/fake-uv-cache"


def _manual(project: Project, phase: str, *args: str, **env: str) -> str:
    res = _manual_run(project, phase, *(args or (LEAF_ID,)), **env)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def _manual_run(
    project: Project, phase: str, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    return project.run(
        "review_with_codex.sh",
        None,
        phase,
        *args,
        env={"HARNESS_CODEX_MODE": "manual", "UV_CACHE_DIR": UV_CACHE, **env},
    )


@pytest.mark.parametrize("phase", ["plan", "code"])
def test_review_phases_use_workspace_write_without_git_or_network(
    project: Project, phase: str
) -> None:
    out = _manual(project, phase)
    assert "--sandbox workspace-write" in out
    assert "danger-full-access" not in out
    assert UV_CACHE in out
    assert f"{project.root}/.git" not in out
    assert "network_access=true" not in out


def test_doc_phase_may_write_git_to_commit(project: Project) -> None:
    out = _manual(project, "doc")
    assert "--sandbox workspace-write" in out
    assert f"{project.root}/.git" in out


def test_network_is_opt_in(project: Project) -> None:
    out = _manual(project, "code", HARNESS_CODEX_NETWORK="1")
    assert "sandbox_workspace_write.network_access=true" in out


def test_full_access_is_an_explicit_override(project: Project) -> None:
    out = _manual(project, "plan", HARNESS_CODEX_SANDBOX="danger-full-access")
    assert "--sandbox danger-full-access" in out
    assert "writable_roots" not in out


def _fake_codex(tmp: Path, verdict: str) -> Path:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        f"mkdir -p tests/acceptance && echo 'def test_ac1(): assert False' "
        f"> tests/acceptance/test_{LEAF_ID}.py\n"
        f"printf '# r\\n\\n**Verdict:** {verdict}\\n' > progress/plan_review_{LEAF_ID}.md\n"
    )
    codex.chmod(0o755)
    return bin_dir


def test_approved_plan_gate_seals_the_acceptance_tests(project: Project, tmp_path: Path) -> None:
    bin_dir = _fake_codex(tmp_path, "PLAN_APPROVED")
    res = project.run(
        "review_with_codex.sh",
        None,
        "plan",
        LEAF_ID,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    assert res.returncode == 0, res.stderr
    manifest = project.root / f"progress/acceptance_{LEAF_ID}.sha256"
    test_file = project.root / f"tests/acceptance/test_{LEAF_ID}.py"
    digest = hashlib.sha256(test_file.read_bytes()).hexdigest()
    assert manifest.read_text().split()[0] == digest


def test_rejected_plan_gate_does_not_seal(project: Project, tmp_path: Path) -> None:
    bin_dir = _fake_codex(tmp_path, "CHANGES_REQUESTED")
    project.run(
        "review_with_codex.sh",
        None,
        "plan",
        LEAF_ID,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    assert not (project.root / f"progress/acceptance_{LEAF_ID}.sha256").exists()


def test_manual_seal_command(project: Project) -> None:
    project.write(f"tests/acceptance/test_{LEAF_ID}.py", "def test_ac1(): ...\n")
    res = project.run("review_with_codex.sh", None, "seal", LEAF_ID)
    assert res.returncode == 0, res.stderr
    assert (project.root / f"progress/acceptance_{LEAF_ID}.sha256").exists()


def test_seal_refuses_without_plan_approved(project: Project) -> None:
    project.set_verdict("plan", "CHANGES_REQUESTED")
    project.write(f"tests/acceptance/test_{LEAF_ID}.py", "def test_ac1(): ...\n")
    res = project.run("review_with_codex.sh", None, "seal", LEAF_ID)
    assert res.returncode != 0


# ── roadmap and phase gates ──────────────────────────────────────────────────


def _codex_writing(tmp: Path, rel: str, verdict: str) -> dict[str, str]:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        f"printf '# r\\n\\n**Verdict:** {verdict}\\n' > {rel}\n"
    )
    codex.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def test_roadmap_gate_takes_no_id_and_names_the_plan_files(project: Project) -> None:
    out = _manual(project, "roadmap", "")
    assert "Phase: roadmap" in out
    assert "docs/plan/PLAN.md" in out
    assert "progress/roadmap_review.md" in out
    assert "seal roadmap" in out


def test_phase_gate_names_the_phase_and_its_verdict_file(project: Project) -> None:
    out = _manual(project, "phase", "01")
    assert "Phase: phase" in out
    assert "progress/phase_review_01.md" in out
    assert "seal phase 01" in out


def test_phase_gate_refuses_before_the_roadmap_is_approved(project: Project) -> None:
    (project.root / "progress/roadmap_review.md").unlink()
    res = _manual_run(project, "phase", "01")
    assert res.returncode != 0
    assert "ROADMAP_APPROVED" in res.stdout + res.stderr


def test_plan_gate_refuses_before_the_phase_is_approved(project: Project) -> None:
    (project.root / "progress/phase_review_01.md").unlink()
    res = _manual_run(project, "plan", LEAF_ID)
    assert res.returncode != 0
    assert "PHASE_APPROVED" in res.stdout + res.stderr


def test_plan_gate_prompt_points_at_the_subtask_spec(project: Project) -> None:
    out = _manual(project, "plan")
    assert f"docs/plan/phase-01-users/{LEAF_ID}-user-entity.md" in out


def test_approved_roadmap_gate_seals_the_roadmap(project: Project, tmp_path: Path) -> None:
    project.write("docs/plan/PLAN.md", "# Plan\n")
    env = _codex_writing(tmp_path, "progress/roadmap_review.md", "ROADMAP_APPROVED")
    res = project.run("review_with_codex.sh", None, "roadmap", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (project.root / "progress/plan_roadmap.sha256").is_file()


def test_approved_phase_gate_seals_the_phase(project: Project, tmp_path: Path) -> None:
    project.write("docs/plan/phase-01-users.md", "# Phase 01 — users\n- goal: g\n")
    env = _codex_writing(tmp_path, "progress/phase_review_01.md", "PHASE_APPROVED")
    res = project.run("review_with_codex.sh", None, "phase", "01", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (project.root / "progress/plan_phase_01.sha256").is_file()


def test_rejected_phase_gate_does_not_seal(project: Project, tmp_path: Path) -> None:
    project.write("docs/plan/phase-01-users.md", "# Phase 01 — users\n- goal: g\n")
    env = _codex_writing(tmp_path, "progress/phase_review_01.md", "CHANGES_REQUESTED")
    project.run("review_with_codex.sh", None, "phase", "01", env=env)
    assert not (project.root / "progress/plan_phase_01.sha256").exists()


@pytest.mark.parametrize("args", [["seal", "roadmap"], ["seal", "phase", "01"]])
def test_seal_forwards_plan_targets(project: Project, args: list[str]) -> None:
    project.write("docs/plan/phase-01-users.md", "# Phase 01 — users\n- goal: g\n")
    res = project.run("review_with_codex.sh", None, *args)
    assert res.returncode == 0, res.stdout + res.stderr


def test_usage_lists_every_gate(project: Project) -> None:
    res = project.run("review_with_codex.sh", None, "bogus")
    assert res.returncode == 2
    for gate in ("roadmap", "phase <NN>", "plan|code|doc", "seal"):
        assert gate in res.stderr
