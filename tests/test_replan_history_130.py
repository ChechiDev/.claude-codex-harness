"""Issue #130: `_oracle.py replan-history` only records the harness's own snapshot.

The re-plan of issue #125 copies the sealed oracle of a subtask, verified against its
seal, into a harness-owned directory; after the new seal, `replan-history <leaf> <dir>`
appends the superseded oracle's diff to the subtask's Codex-owned review history and
drops the copy. The command is open to Claude (the documented retry after a failed
history write), so the directory it is given is not trusted:

- only the verified snapshot the harness made for that subtask is accepted: a
  directory the guard would let Claude write, another subtask's copy, a symlinked
  copy or a copy whose content no longer matches its manifest is refused;
- every manifest path is one of the subtask's oracle files;
- a refused call writes nothing and keeps the pending snapshot, so the legitimate
  retry still works afterwards.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan, Project, bash_payload, write_payload
from test_regate_coupled_125 import _git, _guard, _replan_plan, _write_oracle
from test_regate_deadlock import _gate_env, _phase_project, _run_env

LEAF = "1.1.1"
ORACLE = f"tests/acceptance/test_{LEAF}.py"
HISTORY = f"progress/review_history/{LEAF}.md"
OLD_BODY = "def test_ac1_works() -> None:\n    assert 1\n"
NEW_BODY = "def test_ac1_works() -> None:\n    assert 1 + 1 == 2\n"
FORGED = "FORGED-MARKER"
SECRET = "SECRET-MARKER"
BLOCKED = 2


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    env["CLAUDE_PROJECT_DIR"] = str(root)
    return subprocess.run(
        ["python3", str(TOOLKIT / "lib" / "_oracle.py"), *args],
        cwd=root, env=env, text=True, capture_output=True, check=False,
    )  # fmt: skip


def _history(root: Path, snap: Path | str) -> subprocess.CompletedProcess[str]:
    return _run(root, "replan-history", LEAF, str(snap))


def _replan_dir(root: Path) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--git-path", "harness-replan"],
        cwd=root, text=True, capture_output=True, check=True,
    ).stdout.strip()  # fmt: skip
    return Path(os.path.realpath(root / out))


def _resealed(plan: Plan) -> Path:
    """1.1.1 re-planned: its sealed oracle copied by the harness, then rewritten by the
    reviewer and sealed again. Returns the pending snapshot."""
    _replan_plan(plan)
    res = _run(plan.root, "replan-snapshot", LEAF)
    assert res.returncode == 0, res.stdout + res.stderr
    snap = Path(res.stdout.strip())
    _write_oracle(plan.root, LEAF, NEW_BODY)
    res = _guard(plan, "seal", LEAF)
    assert res.returncode == 0, res.stdout + res.stderr
    return snap


def _forge(where: Path, files: dict[str, str]) -> Path:
    """A snapshot-shaped directory: oracle/<rel> files and a manifest matching them,
    named after the manifest's hash as the harness names its own."""
    text = "".join(f"{_sha(body.encode())}  {rel}\n" for rel, body in files.items())
    snap = where / _sha(text.encode())[:16]
    for rel, body in files.items():
        path = Path(os.path.normpath(snap / "oracle" / rel)) if not rel.startswith("/") else None
        if path is not None and path.is_relative_to(snap):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "manifest.sha256").write_text(text)
    return snap


def _assert_untouched(plan: Plan, pending: Path) -> None:
    """Nothing written to the history and the pending snapshot intact."""
    history = plan.root / HISTORY
    assert not history.exists(), history.read_text()
    assert (pending / "oracle" / ORACLE).read_text() == OLD_BODY


def _assert_retry_works(plan: Plan, pending: Path) -> None:
    res = _history(plan.root, pending)
    assert res.returncode == 0, res.stdout + res.stderr
    text = (plan.root / HISTORY).read_text()
    assert "## Superseded oracle (re-plan, issue #125)" in text
    assert "-    assert 1\n" in text and "+    assert 1 + 1 == 2\n" in text
    assert FORGED not in text and SECRET not in text
    assert not pending.exists()


def _assert_refused(plan: Plan, res: subprocess.CompletedProcess[str], pending: Path) -> None:
    """Refused, nothing written, the pending snapshot intact, and the retry works."""
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert "[FAIL]" in out
    _assert_untouched(plan, pending)
    _assert_retry_works(plan, pending)


# ── the legitimate path keeps working ────────────────────────────────────────


def test_the_harness_snapshot_is_recorded_and_dropped(plan: Plan) -> None:
    snap = _resealed(plan)
    res = _history(plan.root, snap)
    assert res.returncode == 0, res.stdout + res.stderr
    text = (plan.root / HISTORY).read_text()
    assert f"--- a/{ORACLE}" in text and f"+++ b/{ORACLE}" in text
    assert "+    assert 1 + 1 == 2\n" in text
    assert not snap.exists()


def test_a_second_call_after_success_is_refused(plan: Plan) -> None:
    snap = _resealed(plan)
    assert _history(plan.root, snap).returncode == 0
    before = (plan.root / HISTORY).read_text()
    res = _history(plan.root, snap)
    assert res.returncode == 1
    assert (plan.root / HISTORY).read_text() == before


# ── a directory that is not the harness's snapshot for this subtask ──────────


def test_a_foreign_directory_is_refused(plan: Plan, tmp_path: Path) -> None:
    pending = _resealed(plan)
    forged = _forge(tmp_path / "elsewhere", {ORACLE: f"# {FORGED}\n"})
    _assert_refused(plan, _history(plan.root, forged), pending)


def test_a_forged_copy_inside_the_project_is_refused(plan: Plan) -> None:
    pending = _resealed(plan)
    forged = _forge(plan.root / "progress" / "fake", {ORACLE: f"# {FORGED}\n"})
    _assert_refused(plan, _history(plan.root, forged), pending)


def test_another_subtasks_snapshot_is_refused(plan: Plan) -> None:
    pending = _resealed(plan)
    other = _replan_dir(plan.root) / "1.1.2" / pending.name
    shutil.copytree(pending, other)
    (other / "oracle" / ORACLE).write_text(f"# {FORGED}\n")
    _assert_refused(plan, _history(plan.root, other), pending)


@pytest.mark.parametrize("spelling", ["leaf-dir", "replan-dir", "dotdot"])
def test_not_a_snapshot_directory_is_refused(plan: Plan, spelling: str) -> None:
    pending = _resealed(plan)
    arg = {
        "leaf-dir": str(pending.parent),
        "replan-dir": str(pending.parent.parent),
        "dotdot": f"{pending}/oracle/..",
    }[spelling]
    res = _history(plan.root, arg)
    if spelling == "dotdot":
        # The same directory spelled with `..`: still the harness's own snapshot.
        assert res.returncode == 0, res.stdout + res.stderr
        return
    _assert_refused(plan, res, pending)


# ── symlinks ──────────────────────────────────────────────────────────────────


def test_a_symlinked_snapshot_is_refused(plan: Plan, tmp_path: Path) -> None:
    pending = _resealed(plan)
    forged = _forge(tmp_path / "elsewhere", {ORACLE: f"# {FORGED}\n"})
    link = pending.parent / forged.name
    link.symlink_to(forged, target_is_directory=True)
    _assert_refused(plan, _history(plan.root, link), pending)


def test_a_symlinked_leaf_directory_is_refused(plan: Plan, tmp_path: Path) -> None:
    pending = _resealed(plan)
    forged = _forge(tmp_path / "elsewhere", {ORACLE: f"# {FORGED}\n"})
    keep = tmp_path / "kept"
    shutil.move(str(pending.parent), keep)
    pending.parent.symlink_to(forged.parent, target_is_directory=True)
    res = _history(plan.root, pending.parent / forged.name)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    assert (keep / pending.name / "oracle" / ORACLE).read_text() == OLD_BODY


def test_a_symlinked_oracle_file_in_the_snapshot_is_refused(plan: Plan, tmp_path: Path) -> None:
    """Even a link to equal content: the copy is made of regular files only."""
    pending = _resealed(plan)
    elsewhere = tmp_path / "same-content.py"
    elsewhere.write_text(OLD_BODY)
    (pending / "oracle" / ORACLE).unlink()
    (pending / "oracle" / ORACLE).symlink_to(elsewhere)
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    assert (pending / "oracle" / ORACLE).is_symlink()  # nothing removed


def test_a_symlinked_current_oracle_file_is_refused(plan: Plan, tmp_path: Path) -> None:
    pending = _resealed(plan)
    current = plan.root / ORACLE
    elsewhere = tmp_path / "same-new-content.py"
    elsewhere.write_text(current.read_text())
    current.unlink()
    current.symlink_to(elsewhere)
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    assert pending.exists()


# ── manifest paths limited to the subtask's oracle files ─────────────────────


SECRET_BODY = f"# {SECRET}\n"


def _secret(factory: pytest.TempPathFactory) -> Path:
    """A file outside the project, with content that hashes as its manifest line says."""
    path = factory.mktemp("outside") / "secret.txt"
    path.write_text(SECRET_BODY)
    return path


@pytest.mark.parametrize(
    "kind", ["relative-dotdot", "absolute", "source-file", "other-oracle", "nested", "unparsable"]
)
def test_a_snapshot_manifest_outside_the_oracle_is_refused(
    plan: Plan, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, kind: str
) -> None:
    pending = _resealed(plan)
    secret = _secret(tmp_path_factory)
    (plan.root / "src" / "app").mkdir(parents=True, exist_ok=True)
    (plan.root / "src/app/m111.py").write_text(SECRET_BODY)
    bad = {
        "relative-dotdot": os.path.relpath(secret, plan.root),
        "absolute": str(secret),
        "source-file": "src/app/m111.py",
        "other-oracle": "tests/acceptance/test_1.1.2.py",
        "nested": f"tests/acceptance/sub/test_{LEAF}.py",
        "unparsable": "",
    }[kind]
    assert not bad.startswith("..") or kind == "relative-dotdot"
    files = {ORACLE: OLD_BODY}
    if bad:
        files[bad] = SECRET_BODY
    built = _forge(tmp_path / "build", files)
    manifest = built / "manifest.sha256"
    if kind == "unparsable":
        manifest.write_text(manifest.read_text() + "not a manifest line\n")
    snap = pending.parent / _sha(manifest.read_bytes())[:16]
    assert snap != pending
    shutil.move(str(built), snap)
    _assert_refused(plan, _history(plan.root, snap), pending)


@pytest.mark.parametrize("kind", ["relative-dotdot", "absolute", "source-file"])
def test_a_current_manifest_outside_the_oracle_is_refused(
    plan: Plan, tmp_path_factory: pytest.TempPathFactory, kind: str
) -> None:
    pending = _resealed(plan)
    secret = _secret(tmp_path_factory)
    (plan.root / "src" / "app").mkdir(parents=True, exist_ok=True)
    (plan.root / "src/app/m111.py").write_text(SECRET_BODY)
    rel = {
        "relative-dotdot": os.path.relpath(secret, plan.root),
        "absolute": str(secret),
        "source-file": "src/app/m111.py",
    }[kind]
    manifest = plan.root / f"progress/acceptance_{LEAF}.sha256"
    original = manifest.read_text()
    manifest.write_text(original + f"{_sha(SECRET_BODY.encode())}  {rel}\n")
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    _assert_untouched(plan, pending)
    manifest.write_text(original)
    _assert_retry_works(plan, pending)


# ── a manipulated copy ───────────────────────────────────────────────────────


def test_a_tampered_oracle_in_the_snapshot_is_refused(plan: Plan) -> None:
    pending = _resealed(plan)
    target = pending / "oracle" / ORACLE
    target.write_text(f"# {FORGED}\n")
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    assert target.read_text() == f"# {FORGED}\n"  # kept for the human to look at


def test_a_rewritten_snapshot_manifest_is_refused(plan: Plan) -> None:
    """Manifest and files rewritten consistently, but the name no longer is its hash."""
    pending = _resealed(plan)
    (pending / "oracle" / ORACLE).write_text(f"# {FORGED}\n")
    forged = f"# {FORGED}\n"
    (pending / "manifest.sha256").write_text(f"{_sha(forged.encode())}  {ORACLE}\n")
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    assert pending.exists()


def test_an_extra_file_in_the_snapshot_is_refused(plan: Plan) -> None:
    pending = _resealed(plan)
    extra = pending / "oracle" / "tests" / "acceptance" / "conftest.py"
    extra.write_text(f"# {FORGED}\n")
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    extra.unlink()
    _assert_retry_works(plan, pending)


def test_a_current_oracle_off_its_seal_is_refused(plan: Plan) -> None:
    pending = _resealed(plan)
    (plan.root / ORACLE).write_text(f"# {FORGED}\n")
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    assert pending.exists()


def test_no_plan_approval_is_refused(plan: Plan) -> None:
    pending = _resealed(plan)
    plan.verdict(f"plan_review_{LEAF}.md", "CHANGES_REQUESTED")
    res = _history(plan.root, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    assert pending.exists()


# ── pending snapshots survive ────────────────────────────────────────────────


def test_a_fresh_snapshot_of_the_new_oracle_does_not_drop_the_pending_one(plan: Plan) -> None:
    """A snapshot of the current oracle supersedes nothing: no history entry, and the
    pending copy of the oracle it replaced stays for the retry."""
    pending = _resealed(plan)
    res = _run(plan.root, "replan-snapshot", LEAF)
    assert res.returncode == 0, res.stdout + res.stderr
    fresh = Path(res.stdout.strip())
    assert fresh != pending
    res = _history(plan.root, fresh)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (plan.root / HISTORY).exists()
    _assert_untouched(plan, pending)
    _assert_retry_works(plan, pending)


# ── the snapshot directory is harness-owned in every worktree ────────────────


@pytest.fixture
def linked(project: Project, tmp_path: Path) -> Project:
    wt = tmp_path / "wt"
    (project.root / "README").write_text("x\n")
    _git(project.root, "add", "README")
    _git(project.root, "commit", "-qm", "base")
    _git(project.root, "worktree", "add", "-q", str(wt), "-b", "feat")
    (wt / "harness").symlink_to((project.root / "harness").resolve())
    (wt / ".claude").mkdir(exist_ok=True)
    return Project(wt)


@pytest.mark.parametrize("where", ["main", "linked"])
def test_the_agent_cannot_write_a_replan_snapshot(
    project: Project, linked: Project, where: str
) -> None:
    proj = project if where == "main" else linked
    folder = _replan_dir(proj.root)
    if where == "linked":
        assert not folder.is_relative_to(proj.root)  # precondition: outside the root
    folder.mkdir(parents=True, exist_ok=True)
    for target in (str(folder), f"{folder}/{LEAF}/0123456789abcdef/manifest.sha256"):
        res = proj.run("guard_write.sh", write_payload(target))
        assert res.returncode == BLOCKED, (target, res.stdout, res.stderr)


def _git_dir(root: Path) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()
    return Path(os.path.realpath(root / out))


@pytest.mark.parametrize("where", ["main", "linked"])
def test_the_agent_cannot_replace_the_git_dir_holding_the_snapshots(
    project: Project, linked: Project, tmp_path: Path, where: str
) -> None:
    """Replacing an ancestor of the snapshot directory would plant a prepared tree."""
    proj = project if where == "main" else linked
    git_dir = _git_dir(proj.root)
    targets = [git_dir, git_dir.parent] if where == "linked" else [git_dir]
    for target in targets:
        for command in (f"rm -rf {target}", f"mv {target} {tmp_path}/moved"):
            res = proj.run("guard_bash.sh", bash_payload(command))
            assert res.returncode == BLOCKED, (command, res.stdout, res.stderr)


@pytest.mark.parametrize("where", ["main", "linked"])
def test_the_agent_cannot_repoint_the_projects_git_entry(
    project: Project, linked: Project, tmp_path: Path, where: str
) -> None:
    """The project's .git (a file in a linked worktree) says where the git dir is."""
    proj = project if where == "main" else linked
    res = proj.run("guard_write.sh", write_payload(proj.root / ".git"))
    assert res.returncode == BLOCKED, (res.stdout, res.stderr)
    for command in (f"echo 'gitdir: {tmp_path}/x' > .git", "rm -rf .git", "mv .git .git-old"):
        res = proj.run("guard_bash.sh", bash_payload(command))
        assert res.returncode == BLOCKED, (command, res.stdout, res.stderr)


def test_the_git_dir_comes_from_the_project_not_the_environment(
    plan: Plan, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """GIT_DIR on the call cannot point the snapshot location at a prepared repository."""
    pending = _resealed(plan)
    fake = tmp_path_factory.mktemp("fake")
    assert not fake.is_relative_to(plan.root)
    _git(fake, "init", "-q")
    forged = _forge(_replan_dir(fake) / LEAF, {ORACLE: f"# {FORGED}\n"})
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    for var in ("GIT_DIR", "GIT_COMMON_DIR"):
        res = subprocess.run(
            ["python3", str(TOOLKIT / "lib" / "_oracle.py"), "replan-history", LEAF, str(forged)],
            cwd=plan.root, text=True, capture_output=True, check=False,
            env={**env, "CLAUDE_PROJECT_DIR": str(plan.root), var: str(fake / ".git")},
        )  # fmt: skip
        assert res.returncode == 1, (var, res.stdout, res.stderr)
        _assert_untouched(plan, pending)
    _assert_retry_works(plan, pending)


def _committed_linked(plan: Plan, tmp_path: Path) -> Path:
    _git(plan.root, "add", "-A")
    _git(plan.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "state")
    wt = tmp_path / "wt"
    _git(plan.root, "worktree", "add", "-q", str(wt), "-b", "feat")
    return wt


def test_the_replan_records_its_history_in_a_linked_worktree(plan: Plan, tmp_path: Path) -> None:
    _replan_plan(plan)
    wt = _committed_linked(plan, tmp_path)
    res = _run(wt, "replan-snapshot", LEAF)
    assert res.returncode == 0, res.stdout + res.stderr
    snap = Path(res.stdout.strip())
    assert not snap.is_relative_to(wt)  # precondition: the git dir is elsewhere
    _write_oracle(wt, LEAF, NEW_BODY)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    seal = subprocess.run(
        ["python3", str(TOOLKIT / "lib" / "_guard.py"), "seal", LEAF],
        cwd=wt, env={**env, "CLAUDE_PROJECT_DIR": str(wt)},
        text=True, capture_output=True, check=False,
    )  # fmt: skip
    assert seal.returncode == 0, seal.stdout + seal.stderr
    res = _history(wt, snap)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "+    assert 1 + 1 == 2\n" in (wt / HISTORY).read_text()
    assert not snap.exists()


def test_another_checkouts_snapshot_is_refused(plan: Plan, tmp_path: Path) -> None:
    pending = _resealed(plan)
    wt = _committed_linked(plan, tmp_path)
    res = _history(wt, pending)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (wt / HISTORY).exists()
    assert pending.exists()


# ── e2e: the retry after a failed history write, auto mode ───────────────────

FAKE_REPLAN = r"""#!/usr/bin/env bash
cat > /dev/null
cp "$FAKE_ORACLE" tests/acceptance/test_1.1.2.py
printf '# Review\n\n**Verdict:** PLAN_APPROVED\n' > progress/plan_review_1.1.2.md
"""


@pytest.mark.e2e
def test_the_auto_retry_after_a_failed_history_write(tmp_path: Path) -> None:
    """The gate seals, the history write fails and the step prints the retry; a forged
    directory is refused without dropping the snapshot, and the printed retry works."""
    project = _phase_project(tmp_path)  # 1.1.2 in progress, its oracle sealed
    oracle = project.root / "tests/acceptance/test_1.1.2.py"
    old = oracle.read_text()
    rewritten = tmp_path / "rewritten.py"
    rewritten.write_text(old + "\n\ndef test_ac2() -> None:\n    assert True\n")
    blocker = project.root / "progress/review_history/1.1.2.md"
    blocker.mkdir(parents=True)  # the history cannot be appended to
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(FAKE_REPLAN)
    (bin_dir / "codex").chmod(0o755)
    env = _gate_env(
        project, tmp_path, HARNESS_CODEX_MODE="auto", FAKE_ORACLE=str(rewritten),
        PATH=f"{bin_dir}:{project.env['PATH']}",
    )  # fmt: skip
    res = _run_env(project, env, "bash", "harness/review_with_codex.sh", "plan", "1.1.2")
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert oracle.read_text() == rewritten.read_text()  # approved and sealed
    retry = re.search(r"python3 harness/_oracle\.py replan-history 1\.1\.2 (\S+)", out)
    assert retry is not None, out
    snap = Path(retry[1])
    assert (snap / "oracle/tests/acceptance/test_1.1.2.py").read_text() == old

    blocker.rmdir()
    forged = _forge(tmp_path / "elsewhere", {"tests/acceptance/test_1.1.2.py": f"# {FORGED}\n"})
    cmd = ["python3", "harness/_oracle.py", "replan-history", "1.1.2"]
    res = _run_env(project, env, *cmd, str(forged))
    assert res.returncode == 1, res.stdout + res.stderr
    assert not blocker.exists()
    assert snap.is_dir()

    res = _run_env(project, env, *cmd, str(snap))
    assert res.returncode == 0, res.stdout + res.stderr
    text = blocker.read_text()
    assert "## Superseded oracle (re-plan, issue #125)" in text
    assert "+def test_ac2() -> None:\n" in text and FORGED not in text
    assert not snap.exists()
