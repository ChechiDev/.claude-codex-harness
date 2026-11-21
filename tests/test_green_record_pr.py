"""Green record producers and the PR barrier (issue #55).

Gates and close write .git/harness-check.ok (the Stop hook's format: the bare
`_guard.py fingerprint` of the tree) only after a real, clean, green `make check`
whose fingerprint did not change during the run. `review_with_codex.sh pr` and the
PR a close opens require that record to match the exact current state.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import LEAF_ID, Project, write_control

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

GREEN = "check:\n\t@echo ok\n"
RED = "check:\n\t@echo boom; exit 1\n"
# Changes the tree while the check runs (an untracked file with fresh content).
DRIFT = "check:\n\t@date +%s%N > drift.txt\n"
# Red unless the gate's counterfactual assumption is set.
COUNTERFACTUAL = 'check:\n\t@test -n "$$HARNESS_ASSUME_APPROVED"\n'


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return res.stdout.strip()


def _record(project: Project) -> Path:
    return project.root / ".git" / "harness-check.ok"


def _fingerprint(project: Project) -> str:
    res = subprocess.run(
        ["python3", str(project.root / "harness" / "_guard.py"), "fingerprint"],
        cwd=project.root,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project.root)},
    )
    return res.stdout.strip()


def _makefile(project: Project, recipe: str) -> None:
    project.write("Makefile", recipe)
    project.commit_all("makefile")


def _code_gate(project: Project, env: dict[str, str] | None = None) -> None:
    write_control(project.root)
    res = project.run(
        "review_with_codex.sh",
        None,
        "code",
        LEAF_ID,
        env=env or {},
    )
    assert "MANUAL MODE" in res.stdout or res.returncode != 0, res.stdout + res.stderr


# ── producers ────────────────────────────────────────────────────────────────


def test_green_clean_check_writes_the_record(project: Project) -> None:
    _makefile(project, GREEN)
    _code_gate(project)
    assert _record(project).read_text() == _fingerprint(project)


def test_red_check_writes_no_record(project: Project) -> None:
    _makefile(project, RED)
    _code_gate(project)
    assert not _record(project).exists()


def test_red_check_drops_an_old_record(project: Project) -> None:
    _makefile(project, RED)
    _record(project).write_text(_fingerprint(project))
    _code_gate(project)
    assert not _record(project).exists()


def test_fingerprint_changed_during_the_check_writes_no_record(project: Project) -> None:
    _makefile(project, DRIFT)
    _code_gate(project)
    assert not _record(project).exists()


def test_overridden_check_command_writes_no_record(project: Project) -> None:
    _makefile(project, GREEN)
    _code_gate(project, {"HARNESS_CHECK_CMD": "true"})
    assert not _record(project).exists()


def test_inherited_test_exclusion_is_dropped_before_the_check(project: Project) -> None:
    # The check runs clean: an inherited PYTEST_ADDOPTS/TESTS never reaches it.
    _makefile(project, 'check:\n\t@test -z "$$PYTEST_ADDOPTS$$TESTS"\n')
    _code_gate(project, {"PYTEST_ADDOPTS": "--ignore=tests", "TESTS": "none"})
    assert _record(project).read_text() == _fingerprint(project)


def test_counterfactual_run_writes_no_record(project: Project) -> None:
    _makefile(project, COUNTERFACTUAL)
    project.write("docs/plan/PLAN.md", "# Plan\n")
    project.write("docs/plan/phase-01-users.md", "# Phase 01: users\n\nGoal: users.\n")
    project.commit_all("plan")
    write_control(project.root)
    res = project.run("review_with_codex.sh", None, "roadmap")
    assert "Counterfactual run" in res.stdout, res.stdout + res.stderr
    assert "exit code 0" in res.stdout.split("Counterfactual run", 1)[1]
    assert not _record(project).exists()


# ── PR barrier ───────────────────────────────────────────────────────────────


def _gh_env(project: Project, tmp: Path) -> dict[str, str]:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        '#!/usr/bin/env bash\necho "$*" >> "$FAKE_LOG"\n'
        'if [ "$1 $2" = "pr view" ]; then exit 1; fi\nexit 0\n'
    )
    gh.chmod(0o755)
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(project.root, "remote", "add", "origin", str(remote))
    _git(project.root, "push", "-q", "origin", "main")
    _git(project.root, "switch", "-qc", "feat/1.1-users")
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(tmp / "gh.log")}


def _pr(project: Project, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return project.run("review_with_codex.sh", None, "pr", "1.1", env=env)


def _remote_has_branch(tmp: Path) -> bool:
    res = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "refs/heads/feat/1.1-users"],
        cwd=tmp / "remote.git",
        capture_output=True,
        check=False,
    )
    return res.returncode == 0


def test_pr_without_a_record_runs_the_check_and_opens_on_green(
    project: Project, tmp_path: Path
) -> None:
    _makefile(project, GREEN)
    env = _gh_env(project, tmp_path)
    res = _pr(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _record(project).read_text() == _fingerprint(project)
    assert "pr create" in (tmp_path / "gh.log").read_text()
    assert _remote_has_branch(tmp_path)


def test_pr_without_a_record_blocks_on_red(project: Project, tmp_path: Path) -> None:
    _makefile(project, RED)
    env = _gh_env(project, tmp_path)
    res = _pr(project, env)
    assert res.returncode == 1
    assert "make check" in res.stderr
    assert not _record(project).exists()
    assert not (tmp_path / "gh.log").exists()
    assert not _remote_has_branch(tmp_path)


def test_pr_with_a_stale_record_checks_again_and_blocks_on_red(
    project: Project, tmp_path: Path
) -> None:
    _makefile(project, GREEN)
    env = _gh_env(project, tmp_path)
    _record(project).write_text(_fingerprint(project))
    _makefile(project, RED)  # the tree moved on (committed) and is red now
    res = _pr(project, env)
    assert res.returncode == 1
    assert "make check" in res.stderr
    assert not (tmp_path / "gh.log").exists()


def test_pr_with_an_overridden_check_command_blocks(project: Project, tmp_path: Path) -> None:
    _makefile(project, GREEN)
    env = {**_gh_env(project, tmp_path), "HARNESS_CHECK_CMD": "true"}
    res = _pr(project, env)
    assert res.returncode == 1
    assert "make check" in res.stderr
    assert not (tmp_path / "gh.log").exists()


def test_pr_passes_with_a_matching_record(project: Project, tmp_path: Path) -> None:
    _makefile(project, 'check:\n\t@touch "$$CHECK_MARKER"\n')
    env = _gh_env(project, tmp_path)
    _record(project).write_text(_fingerprint(project))
    marker = tmp_path / "check-ran"
    env["CHECK_MARKER"] = str(marker)
    res = _pr(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not marker.exists()  # a valid record: the check is not run again
    assert "pr create" in (tmp_path / "gh.log").read_text()
    assert _remote_has_branch(tmp_path)


# ── close: the PR it opens at the end of a task ──────────────────────────────


def _codex_closes(tmp: Path) -> str:
    bin_dir = tmp / "fakebin"
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        'sed -i \'s/"in_progress"/"done"/g\' feature_list.json\n'
        f"printf 'feat(domain): add user entity [{LEAF_ID}]\\n' > progress/close_{LEAF_ID}.msg\n"
        "printf '## Overview\\nUser entity.\\n' > progress/pr_1.1.md\n"
    )
    codex.chmod(0o755)
    return str(bin_dir)


def test_last_close_records_green_after_its_commit_and_opens_the_pr(
    project: Project, tmp_path: Path
) -> None:
    _makefile(project, GREEN)
    env = _gh_env(project, tmp_path)
    _codex_closes(tmp_path)
    project.set_verdict("code", "APPROVED", with_gate_record=True)
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "closed ->" in res.stdout
    assert _record(project).read_text() == _fingerprint(project)
    assert "pr create" in (tmp_path / "gh.log").read_text()


def test_last_close_with_overridden_check_does_not_open_the_pr(
    project: Project, tmp_path: Path
) -> None:
    _makefile(project, GREEN)
    env = {**_gh_env(project, tmp_path), "HARNESS_CHECK_CMD": "true"}
    _codex_closes(tmp_path)
    project.set_verdict("code", "APPROVED", with_gate_record=True)
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "closed ->" in res.stdout
    assert not (tmp_path / "gh.log").exists()
    assert "make check" in res.stderr


def test_last_close_red_after_its_commit_opens_no_pr(project: Project, tmp_path: Path) -> None:
    # Green before the close commit, red once HEAD is the close commit.
    _makefile(project, "check:\n\t@! git log -1 --format=%s | grep -q 'add user entity'\n")
    env = _gh_env(project, tmp_path)
    _codex_closes(tmp_path)
    project.set_verdict("code", "APPROVED", with_gate_record=True)
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "closed ->" in res.stdout
    assert not _record(project).exists()
    assert not (tmp_path / "gh.log").exists()
    assert "red after the close commit" in res.stderr


def test_pr_blocks_a_dirty_tree_even_with_a_matching_record(
    project: Project, tmp_path: Path
) -> None:
    # git push sends only HEAD: a green check of uncommitted fixes proves nothing about it.
    _makefile(project, GREEN)
    env = _gh_env(project, tmp_path)
    project.write("src/pkg/fix.py", "x = 1\n")
    _record(project).write_text(_fingerprint(project))
    res = _pr(project, env)
    assert res.returncode == 1
    assert "uncommitted" in res.stderr
    assert not (tmp_path / "gh.log").exists()


def test_pr_blocks_index_flags_that_hide_changes(project: Project, tmp_path: Path) -> None:
    # assume-unchanged / skip-worktree hide edits from git status and the fingerprint.
    _makefile(project, GREEN)
    env = _gh_env(project, tmp_path)
    _git(project.root, "update-index", "--assume-unchanged", "Makefile")
    project.write("Makefile", RED)
    res = _pr(project, env)
    assert res.returncode == 1
    assert "assume-unchanged" in res.stderr
    assert not (tmp_path / "gh.log").exists()


def test_index_flags_block_the_record(project: Project) -> None:
    # A flagged file is checked with content the fingerprint cannot see.
    _makefile(project, GREEN)
    _git(project.root, "update-index", "--skip-worktree", "Makefile")
    _code_gate(project)
    assert not _record(project).exists()
