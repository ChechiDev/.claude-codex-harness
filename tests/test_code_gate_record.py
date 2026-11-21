"""review_with_codex.sh records every code-gate attempt (_gate_run, issue #39).

- code mode, red check: no reviewer, no verdict, no round; non-zero exit + log tail.
- plan mode, red check: the reviewer still runs (red is expected there).
- an approved code review binds the implementation (files: + tests/unit/) and the
  toolkit; the close refuses a stale approval.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import DECLARED, LEAF_ID, Project, write_control

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

SUBJECT = f"feat(domain): add user entity [{LEAF_ID}]"


def _git_path(project: Project, name: str) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--git-path", name],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return (project.root / out).resolve()


def _record(project: Project, kind: str) -> Path:
    return _git_path(project, f"harness-gate-runs/{kind}_{LEAF_ID}.json")


def _fake_codex(tmp: Path, body: str) -> dict[str, str]:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    codex = bin_dir / "codex"
    codex.write_text('#!/usr/bin/env bash\ncat >/dev/null\ntouch "$CODEX_MARK"\n' + body)
    codex.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CODEX_MARK": str(tmp / "codex-ran")}


def _reviewer(tmp: Path, verdict: str) -> dict[str, str]:
    return _fake_codex(
        tmp, f"printf '# r\\n\\n**Verdict:** {verdict}\\n' > progress/review_{LEAF_ID}.md\n"
    )


def _documenter(tmp: Path) -> dict[str, str]:
    return _fake_codex(
        tmp,
        "mkdir -p docs/reference && echo documented > docs/reference/1.1.1-user-entity.md\n"
        f"printf '%s\\n' {SUBJECT!r} > progress/close_{LEAF_ID}.msg\n",
    )


def _code_gate(project: Project, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if env.pop("HARNESS_CODEX_MODE", None) == "manual":  # test-only channel (#90)
        write_control(project.root)
    return project.run("review_with_codex.sh", None, "code", LEAF_ID, env=env)


def _head(project: Project) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project.root, text=True, capture_output=True, check=True
    ).stdout


@pytest.fixture
def committed(project: Project) -> Project:
    project.write("tests/unit/test_user.py", "def test_u(): pass\n")
    project.commit_all("base")
    return project


# ── red check in code mode stops before the review ──────────────────────────


def test_red_code_check_skips_the_reviewer(committed: Project, tmp_path: Path) -> None:
    env = {**_reviewer(tmp_path, "APPROVED"), "HARNESS_CHECK_CMD": "echo BOOM-LINE; exit 3"}
    res = _code_gate(committed, env)
    assert res.returncode != 0
    assert "BOOM-LINE" in res.stderr
    assert not (tmp_path / "codex-ran").exists()
    assert not (committed.root / f"progress/review_{LEAF_ID}.md").exists()
    history = committed.root / "progress/review_history"
    assert not history.exists() or not any(history.iterdir())
    recs = list(_record(committed, "code").parent.glob(f"code.*_{LEAF_ID}.json"))
    assert len(recs) == 1
    assert '"check_rc": 3' in recs[0].read_text()


def test_red_code_check_keeps_the_previous_verdict(committed: Project, tmp_path: Path) -> None:
    committed.set_verdict("code", "CHANGES_REQUESTED")
    before = (committed.root / f"progress/review_{LEAF_ID}.md").read_text()
    env = {**_reviewer(tmp_path, "APPROVED"), "HARNESS_CHECK_CMD": "exit 1"}
    assert _code_gate(committed, env).returncode != 0
    assert (committed.root / f"progress/review_{LEAF_ID}.md").read_text() == before


def test_red_code_check_stops_in_manual_mode_too(committed: Project) -> None:
    env = {"HARNESS_CODEX_MODE": "manual", "HARNESS_CHECK_CMD": "echo RED-MANUAL; exit 2"}
    res = _code_gate(committed, env)
    assert res.returncode != 0
    assert "RED-MANUAL" in res.stderr
    assert "codex exec" not in res.stdout


def test_red_plan_check_still_reviews(committed: Project, tmp_path: Path) -> None:
    env = {**_fake_codex(tmp_path, ""), "HARNESS_CHECK_CMD": "exit 1"}
    committed.run("review_with_codex.sh", None, "plan", LEAF_ID, env=env)
    assert (tmp_path / "codex-ran").exists()


# ── approval binds the implementation; the close refuses a stale one ────────


def _approve(project: Project, tmp_path: Path) -> None:
    env = {**_reviewer(tmp_path / "rev", "APPROVED"), "HARNESS_CHECK_CMD": "true"}
    res = _code_gate(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _record(project, "code.approved").is_file()


def _close(project: Project, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**_documenter(tmp_path / "doc"), "HARNESS_CHECK_CMD": "true"}
    return project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)


@pytest.fixture
def dirs(tmp_path: Path) -> Path:
    (tmp_path / "rev").mkdir()
    (tmp_path / "doc").mkdir()
    return tmp_path


def test_fresh_approval_closes(committed: Project, dirs: Path) -> None:
    _approve(committed, dirs)
    committed.write("docs/reference/1.1.1-user-entity.md", "docs never invalidate an approval\n")
    res = _close(committed, dirs)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "closed ->" in res.stdout


@pytest.mark.parametrize("changed", [DECLARED, "tests/unit/test_user.py"])
def test_close_refuses_a_stale_approval(committed: Project, dirs: Path, changed: str) -> None:
    _approve(committed, dirs)
    committed.write(changed, "# changed after approval\n")
    head = _head(committed)
    res = _close(committed, dirs)
    assert res.returncode != 0
    assert "stale" in (res.stdout + res.stderr)
    assert _head(committed) == head


def test_close_command_refuses_a_stale_approval(committed: Project, dirs: Path) -> None:
    _approve(committed, dirs)
    committed.write(DECLARED, "# changed\n")
    committed.write(f"progress/close_{LEAF_ID}.msg", SUBJECT + "\n")
    head = _head(committed)
    res = committed.run(
        "review_with_codex.sh", None, "close", LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )
    assert res.returncode != 0
    assert "stale" in (res.stdout + res.stderr)
    assert _head(committed) == head


def test_close_refuses_an_approval_without_a_record(committed: Project, dirs: Path) -> None:
    committed.set_verdict("code", "APPROVED")  # e.g. approved before #39: no record
    head = _head(committed)
    res = _close(committed, dirs)
    assert res.returncode != 0
    assert "code gate" in (res.stdout + res.stderr)
    assert _head(committed) == head


def test_rejected_review_drops_the_approved_record(committed: Project, dirs: Path) -> None:
    _approve(committed, dirs)
    (dirs / "rev2").mkdir()
    env = {**_reviewer(dirs / "rev2", "CHANGES_REQUESTED"), "HARNESS_CHECK_CMD": "true"}
    assert _code_gate(committed, env).returncode == 0
    assert not _record(committed, "code.approved").exists()


def test_manual_code_gate_prints_the_bind_step(committed: Project) -> None:
    env = {"HARNESS_CODEX_MODE": "manual", "HARNESS_CHECK_CMD": "true"}
    res = _code_gate(committed, env)
    assert res.returncode == 0, res.stderr
    assert f"python3 harness/_gate_run.py approve {LEAF_ID} " in res.stdout


def test_approve_refuses_without_an_approved_verdict(committed: Project) -> None:
    env = {"HARNESS_CODEX_MODE": "manual", "HARNESS_CHECK_CMD": "true"}
    out = _code_gate(committed, env).stdout
    tag = out.split(f"_gate_run.py approve {LEAF_ID} ")[1].split()[0]
    committed.set_verdict("code", "CHANGES_REQUESTED")
    res = subprocess.run(
        ["python3", "harness/_gate_run.py", "approve", LEAF_ID, tag],
        cwd=committed.root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    assert not _record(committed, "code.approved").exists()
    committed.set_verdict("code", "APPROVED")
    res = subprocess.run(
        ["python3", "harness/_gate_run.py", "approve", LEAF_ID, tag],
        cwd=committed.root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert _record(committed, "code.approved").is_file()


def test_close_refuses_when_the_check_rewrites_code(committed: Project, dirs: Path) -> None:
    _approve(committed, dirs)
    env = {**_documenter(dirs / "doc"), "HARNESS_CHECK_CMD": f"echo '# gen' >> {DECLARED}"}
    head = _head(committed)
    res = committed.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode != 0
    assert "stale" in res.stderr
    assert _head(committed) == head
