"""Issue #52: `make check` is serialized per repo with flock, so the Stop hook and
the gates never build the same tree at the same time.

The project is minimal: harness/ is a directory of links to this toolkit's lib/
(so harness.mk and stop_gate.sh are the real ones) plus a fake check.sh, and a
`verify` that logs start/end around a sleep so overlaps are visible.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import TOOLKIT

MAKEFILE = """\
verify:
\t@echo "start $$$$" >> run.log; \\
\tif [ -n "$${VERIFY_RELEASE:-}" ]; then \\
\t  n=0; while [ ! -e "$$VERIFY_RELEASE" ] && [ $$n -lt 1200 ]; do sleep 0.05; n=$$((n+1)); done; \\
\telse sleep 1; fi; \\
\techo "end $$$$" >> run.log

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


@pytest.mark.e2e
def test_two_concurrent_checks_are_serialized(proj: Path) -> None:
    a, b = _check(proj), _check(proj)
    assert a.wait(30) == 0, a.communicate()
    assert b.wait(30) == 0, b.communicate()
    log = (proj / "run.log").read_text().splitlines()
    assert len(log) == 4 and _serial(log), log


@pytest.mark.e2e
def test_lock_lives_in_git_dir_and_tree_stays_clean(proj: Path) -> None:
    p = _check(proj)
    assert p.wait(30) == 0, p.communicate()
    assert _lock(proj).is_file()
    assert ".git" in _lock(proj).relative_to(proj).parts
    assert _git(proj, "status", "--porcelain") == ""


def _stop(proj: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "harness/stop_gate.sh"],
        input='{"stop_hook_active": false}',
        cwd=proj,
        env=_env(CLAUDE_PROJECT_DIR=str(proj), **env),
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.e2e
def test_stop_hook_does_not_wait_for_a_running_check(proj: Path) -> None:
    """#55: the Stop is advisory; it never runs make check, so it never queues."""
    release = proj.parent / "release"
    gate = _check(proj, VERIFY_RELEASE=str(release))
    try:
        _wait_for(lambda: (proj / "run.log").exists(), gate)
        # The gate's verify is now blocked until `release` exists: a Stop that
        # waited for the check would hang here and hit _stop's timeout.
        stop = _stop(proj)
        assert gate.poll() is None, "the Stop returned before the gate's check ended"
    finally:
        release.touch()
    assert gate.wait(30) == 0, gate.communicate()
    assert stop.returncode == 0, stop.stdout + stop.stderr
    log = (proj / "run.log").read_text().splitlines()
    assert len(log) == 2 and _serial(log), log


def _wait_for(cond: Callable[[], bool], proc: subprocess.Popen[Any]) -> None:
    """Poll until cond() holds; fail fast if proc died first."""
    deadline = time.monotonic() + 60
    while not cond():
        assert proc.poll() is None, f"process exited early: {proc.returncode}"
        assert time.monotonic() < deadline, "timed out waiting for the event"
        time.sleep(0.02)


def _hold_lock(root: Path) -> subprocess.Popen[bytes]:
    """Hold the check lock until killed; return only once it is really held."""
    ready = root.parent / "lock-held"
    holder = subprocess.Popen(
        ["flock", str(_lock(root)), "sh", "-c", 'touch "$1"; exec sleep 120', "sh", str(ready)],
        start_new_session=True,
    )
    _wait_for(ready.exists, holder)
    return holder


def _release(holder: subprocess.Popen[bytes]) -> None:
    """Kill flock and its sleep child (which inherits the lock fd) together."""
    os.killpg(holder.pid, signal.SIGKILL)
    holder.wait()


@pytest.mark.e2e
def test_check_times_out_with_a_clear_error(proj: Path) -> None:
    holder = _hold_lock(proj)
    try:
        p = _check(proj, HARNESS_CHECK_LOCK_TIMEOUT="1")
        _, err = p.communicate(timeout=30)
    finally:
        _release(holder)
    assert p.returncode != 0
    assert "timed out" in err and "harness-check.lock" in err, err
    assert not (proj / "run.log").exists()


@pytest.mark.parametrize("held", ["", "1"])
def test_stop_hook_never_takes_the_lock_nor_blocks(proj: Path, held: str) -> None:
    """#55: with the lock held (and even a stray HARNESS_CHECK_LOCK_HELD) the Stop
    returns at once, allows the stop and runs nothing."""
    extra = {"HARNESS_CHECK_LOCK_HELD": held} if held else {}
    holder = _hold_lock(proj)
    try:
        stop = _stop(proj, HARNESS_CHECK_LOCK_TIMEOUT="1", **extra)
    finally:
        _release(holder)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert "timed out" not in stop.stdout + stop.stderr
    assert not (proj / "run.log").exists()


@pytest.mark.e2e
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
