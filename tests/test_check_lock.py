"""Issue #52: `make check` is serialized per repo with flock, so the Stop hook and
the gates never build the same tree at the same time.

The project is minimal: harness/ is a directory of links to this toolkit's lib/
(so harness.mk and stop_gate.sh are the real ones) plus a fake check.sh, and a
`verify` that logs start/end around a sleep so overlaps are visible.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from pathlib import Path
from types import ModuleType

import pytest
from conftest import TOOLKIT

MAKEFILE = """\
verify:
\t@echo "start $$$$" >> run.log; sleep $${VERIFY_SLEEP:-1}; echo "end $$$$" >> run.log

include harness/harness.mk
"""
FAKE_CHECK = "#!/usr/bin/env bash\nexit 0\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "harness").mkdir(parents=True)
    for src in (TOOLKIT / "lib").iterdir():
        if src.name != "check.sh":
            (root / "harness" / src.name).symlink_to(src)
    check = root / "harness" / "check.sh"
    check.write_text(FAKE_CHECK)
    check.chmod(0o755)
    (root / "Makefile").write_text(MAKEFILE)
    (root / ".gitignore").write_text("run.log\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "MAKE"))}
    env.update(extra)
    return env


def _check(root: Path, **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["make", "check"],
        cwd=root,
        env=_env(**extra),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _serial(log: list[str]) -> bool:
    """Every start is immediately followed by its own end."""
    pairs = [log[i : i + 2] for i in range(0, len(log), 2)]
    return all(
        len(p) == 2 and p[0].startswith("start ") and p[1] == "end " + p[0][6:] for p in pairs
    )


def _lock(root: Path) -> Path:
    return root / _git(root, "rev-parse", "--git-path", "harness-check.lock")


def test_two_concurrent_checks_are_serialized(proj: Path) -> None:
    a, b = _check(proj), _check(proj)
    assert a.wait(30) == 0, a.communicate()
    assert b.wait(30) == 0, b.communicate()
    log = (proj / "run.log").read_text().splitlines()
    assert len(log) == 4 and _serial(log), log


def test_lock_lives_in_git_dir_and_tree_stays_clean(proj: Path) -> None:
    p = _check(proj)
    assert p.wait(30) == 0, p.communicate()
    assert _lock(proj).is_file()
    assert ".git" in _lock(proj).relative_to(proj).parts
    assert _git(proj, "status", "--porcelain") == ""


def test_stop_hook_waits_for_a_running_check(proj: Path) -> None:
    gate = _check(proj, VERIFY_SLEEP="2")
    deadline = time.time() + 10
    while not (proj / "run.log").exists() and time.time() < deadline:
        time.sleep(0.05)
    stop = subprocess.run(
        ["bash", "harness/stop_gate.sh"],
        input='{"stop_hook_active": false}',
        cwd=proj,
        env=_env(CLAUDE_PROJECT_DIR=str(proj)),
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert gate.wait(30) == 0, gate.communicate()
    assert stop.returncode == 0, stop.stdout + stop.stderr
    log = (proj / "run.log").read_text().splitlines()
    assert len(log) == 4 and _serial(log), log


def _hold_lock(root: Path) -> subprocess.Popen[bytes]:
    holder = subprocess.Popen(["flock", str(_lock(root)), "sleep", "5"])
    time.sleep(0.3)
    return holder


def test_check_times_out_with_a_clear_error(proj: Path) -> None:
    holder = _hold_lock(proj)
    try:
        p = _check(proj, HARNESS_CHECK_LOCK_TIMEOUT="1")
        _, err = p.communicate(timeout=30)
    finally:
        holder.kill()
    assert p.returncode != 0
    assert "timed out" in err and "harness-check.lock" in err, err
    assert not (proj / "run.log").exists()


def test_stop_hook_times_out_and_blocks(proj: Path) -> None:
    holder = _hold_lock(proj)
    try:
        stop = subprocess.run(
            ["bash", "harness/stop_gate.sh"],
            input='{"stop_hook_active": false}',
            cwd=proj,
            env=_env(CLAUDE_PROJECT_DIR=str(proj), HARNESS_CHECK_LOCK_TIMEOUT="1"),
            text=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        holder.kill()
    assert stop.returncode == 2, stop.stdout + stop.stderr
    assert "timed out" in stop.stderr
    assert not (proj / "run.log").exists()


def test_missing_flock_fails_clearly(proj: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for d in os.environ["PATH"].split(os.pathsep):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            dst = bin_dir / name
            if name != "flock" and not dst.exists() and not dst.is_symlink():
                dst.symlink_to(Path(d) / name)
    p = _check(proj, PATH=str(bin_dir))
    _, err = p.communicate(timeout=30)
    assert p.returncode != 0
    assert "flock" in err and "not found" in err, err
    assert not (proj / "run.log").exists()


def test_stop_hook_ignores_an_inherited_lock_held_flag(proj: Path) -> None:
    """A stray HARNESS_CHECK_LOCK_HELD in the session must not skip the lock."""
    holder = _hold_lock(proj)
    try:
        stop = subprocess.run(
            ["bash", "harness/stop_gate.sh"],
            input='{"stop_hook_active": false}',
            cwd=proj,
            env=_env(
                CLAUDE_PROJECT_DIR=str(proj),
                HARNESS_CHECK_LOCK_TIMEOUT="1",
                HARNESS_CHECK_LOCK_HELD="1",
            ),
            text=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        holder.kill()
    assert stop.returncode == 2, stop.stdout + stop.stderr
    assert not (proj / "run.log").exists()


# The `check` recipe projects carried in their own Makefile before harness.mk
# locked it (issue #52): --migrate must still recognise it, exactly.
OLD_CHECK = """\
check:
\t@rc=0; \\
\t$(MAKE) --no-print-directory -k verify || rc=1; \\
\t./harness/check.sh || rc=1; \\
\texit $$rc
"""


def _upgrade_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_upgrade_i52", TOOLKIT / "lib/_upgrade.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migrate_accepts_the_pre_lock_check_recipe() -> None:
    up = _upgrade_module()
    out = up.migrated_makefile("verify:\n\ttrue\n\n" + OLD_CHECK)
    assert out is not None
    assert "check:" not in out and "include harness/harness.mk" in out


def test_migrate_still_refuses_a_customised_check() -> None:
    up = _upgrade_module()
    custom = OLD_CHECK.replace("./harness/check.sh", "./harness/check.sh --extra")
    with pytest.raises(up.MigrationError, match="check: customised"):
        up.migrated_makefile("verify:\n\ttrue\n\n" + custom)
