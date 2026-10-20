"""review_with_codex.sh: least-privilege sandbox and acceptance-test sealing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from conftest import LEAF_ID, Project

UV_CACHE = "/tmp/fake-uv-cache"


def _manual(project: Project, phase: str, **env: str) -> str:
    res = project.run(
        "review_with_codex.sh",
        None,
        phase,
        LEAF_ID,
        env={"HARNESS_CODEX_MODE": "manual", "UV_CACHE_DIR": UV_CACHE, **env},
    )
    assert res.returncode == 0, res.stderr
    return res.stdout


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
