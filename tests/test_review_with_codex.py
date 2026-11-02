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


def test_doc_phase_cannot_write_git(project: Project) -> None:
    # The harness commits the close outside the sandbox, so Codex never needs .git.
    project.set_verdict("code", "APPROVED")
    out = _manual(project, "doc")
    assert "--sandbox workspace-write" in out
    assert f"{project.root}/.git" not in out


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
    for gate in ("roadmap", "phase <NN>", "plan|code|doc", "close <N.M.K>", "seal"):
        assert gate in res.stderr


# ── the test suite runs outside the sandbox (it blocks local sockets) ────────


def test_check_runs_before_codex_and_its_result_is_in_the_prompt(project: Project) -> None:
    out = _manual(project, "code", HARNESS_CHECK_CMD="echo SUITE-OUTPUT; exit 3")
    assert "Do NOT run `make check`" in out
    assert "exit code 3" in out
    log = next(w for w in out.split() if "harness-check" in w).strip("`.,")
    assert "SUITE-OUTPUT" in Path(log).read_text()


def test_default_check_is_make_check(project: Project) -> None:
    out = _manual(project, "roadmap", "")
    assert "The harness ran `make check`" in out


def _codex_closing(project: Project, tmp: Path, message: str | None) -> dict[str, str]:
    """A fake documenter (code review APPROVED): marks the change, leaves its message."""
    project.set_verdict("code", "APPROVED")
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    write_msg = f"printf '%b\\n' {message!r} > progress/close_{LEAF_ID}.msg\n" if message else ""
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\necho documented > docs_ref.md\n" + write_msg
    )
    codex.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HARNESS_CHECK_CMD": "true"}


def _git_out(project: Project, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project.root, text=True, capture_output=True, check=True
    ).stdout


SUBJECT = f"feat(domain): add user entity [{LEAF_ID}]"


def test_close_commits_codex_message_after_a_green_check(project: Project, tmp_path: Path) -> None:
    env = _codex_closing(project, tmp_path, SUBJECT + "\n\nWhy it matters.")
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _git_out(project, "log", "-1", "--format=%s") == SUBJECT + "\n"
    assert "docs_ref.md" in _git_out(project, "show", "--name-only", "--format=")
    assert not (project.root / f"progress/close_{LEAF_ID}.msg").exists()
    assert _git_out(project, "status", "--porcelain") == ""
    assert "closed ->" in res.stdout


def test_close_does_not_commit_with_a_red_check(project: Project, tmp_path: Path) -> None:
    head = _git_out(project, "rev-parse", "HEAD")
    env = {**_codex_closing(project, tmp_path, SUBJECT), "HARNESS_CHECK_CMD": "echo RED; exit 1"}
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode == 1
    assert "nothing committed" in res.stderr
    assert _git_out(project, "rev-parse", "HEAD") == head


@pytest.mark.parametrize(
    "message",
    [
        f"feat: add user entity [{LEAF_ID}]\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        "feat: add user entity [9.9.9]",  # another subtask's id
        None,  # the documenter stopped before writing it
    ],
    ids=["attribution", "wrong-id", "missing"],
)
def test_close_refuses_a_bad_or_missing_message(
    project: Project, tmp_path: Path, message: str | None
) -> None:
    head = _git_out(project, "rev-parse", "HEAD")
    res = project.run(
        "review_with_codex.sh", None, "doc", LEAF_ID, env=_codex_closing(project, tmp_path, message)
    )
    assert res.returncode == 1
    assert _git_out(project, "rev-parse", "HEAD") == head


def test_manual_close_prints_the_commit_step(project: Project) -> None:
    project.set_verdict("code", "APPROVED")
    out = _manual(project, "doc")
    assert f"./harness/review_with_codex.sh close {LEAF_ID}" in out
    assert f"progress/close_{LEAF_ID}.msg" in out


def test_close_refuses_a_subtask_already_closed(project: Project, tmp_path: Path) -> None:
    # A close that finished but looked interrupted must not run again: no Codex,
    # no second commit; the message points at the PR retry instead.
    env = _codex_closing(project, tmp_path, SUBJECT)
    project.set_leaf("done")
    project.commit_all("close")
    head = _git_out(project, "rev-parse", "HEAD")
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode == 1
    assert f"{LEAF_ID} is already closed" in res.stdout
    assert "./harness/review_with_codex.sh pr 1.1" in res.stdout
    assert not (project.root / "docs_ref.md").exists()
    assert _git_out(project, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("verdict", [None, "CHANGES_REQUESTED"], ids=["no-review", "rejected"])
def test_close_refuses_without_an_approved_code_review(
    project: Project, tmp_path: Path, verdict: str | None
) -> None:
    env = _codex_closing(project, tmp_path, SUBJECT)
    review = project.root / f"progress/review_{LEAF_ID}.md"
    if verdict is None:
        review.unlink()
    else:
        project.set_verdict("code", verdict)
    head = _git_out(project, "rev-parse", "HEAD")
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode == 1
    assert f"progress/review_{LEAF_ID}.md" in res.stdout
    assert not (project.root / "docs_ref.md").exists()
    assert _git_out(project, "rev-parse", "HEAD") == head


def test_close_refuses_an_unknown_subtask(project: Project) -> None:
    res = _manual_run(project, "doc", "9.9.9")
    assert res.returncode == 1
    assert "9.9.9 does not exist" in res.stdout


def test_claude_cannot_write_the_close_message(project: Project) -> None:
    from conftest import write_payload

    res = project.run(
        "guard_write.sh", write_payload(project.root / f"progress/close_{LEAF_ID}.msg")
    )
    assert res.returncode == 2
