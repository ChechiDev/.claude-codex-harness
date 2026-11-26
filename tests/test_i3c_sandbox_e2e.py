"""I3c part 1, end to end with real bubblewrap: the toolkit's sandbox policy on a
throwaway installed project.

Claude Code's sandbox runtime is not scriptable from a test, so this applies the
same policy with bwrap directly, read from settings.json: the whole machine
read-only, the project, the temp dir and `filesystem.allowWrite` writable, then
`filesystem.denyWrite` read-only again (a path that does not exist yet gets a
read-only /dev/null, as Claude Code's own bwrap command line does), no network
(UV_OFFLINE, as the fixture).
It proves the policy an opted-in project gets (the toolkit ships it off; the
project's .claude/settings.local.json switches it on): the denied paths cannot be
written even through expansions the guard cannot resolve, src/ can, and
`make check` stays green. Off, the doctor says nothing.
Manual runs with the real `claude -p` sandbox: docs/design/I3c-sandbox.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import TOOLKIT
from i0_projects import Installed, install_project

pytestmark = pytest.mark.e2e

SANDBOX = json.loads((TOOLKIT / "settings.json").read_text())["sandbox"]


def _bwrap_works() -> bool:
    if not shutil.which("bwrap"):
        return False
    probe = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"]
    return subprocess.run(probe, capture_output=True, check=False).returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap missing or unusable")


def _resolve(entry: str, root: Path, home: Path) -> Path:
    if entry.startswith("~/"):
        return home / entry[2:]
    if entry.startswith("/"):
        return Path(entry)
    return root / entry.removeprefix("./")


def _sandboxed(project: Installed, command: str) -> subprocess.CompletedProcess[str]:
    root = project.root
    home = Path(project.env["HOME"])
    writable = [root, Path(project.env.get("TMPDIR", "/tmp"))]
    writable += [_resolve(p, root, home) for p in SANDBOX["filesystem"]["allowWrite"]]
    writable.append(Path(project.env["UV_CACHE_DIR"]))  # what ~/.cache/uv is for the user
    argv = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    argv += ["--unshare-net", "--die-with-parent"]
    for path in writable:
        if path.exists():
            argv += ["--bind", str(path), str(path)]
    for entry in SANDBOX["filesystem"]["denyWrite"]:
        path = _resolve(entry.removesuffix("/**"), root, home)
        if path.exists():
            real = path.resolve()  # harness/ is a link: deny its target
            argv += ["--ro-bind", str(real), str(real)]
        else:  # as Claude Code does: a read-only /dev/null keeps it from being created
            argv += ["--ro-bind", "/dev/null", str(path)]
    argv += ["--chdir", str(root), "sh", "-c", command]
    env = {**project.env, "SANDBOX_RUNTIME": "1"}
    try:
        return subprocess.run(
            argv, env=env, text=True, capture_output=True, check=False, timeout=600
        )
    finally:
        _remove_mount_points(argv)


def _remove_mount_points(argv: list[str]) -> None:
    """bwrap leaves the empty file it mounted /dev/null on; Claude Code removes it too."""
    for i, arg in enumerate(argv[:-2]):
        if arg == "--ro-bind" and argv[i + 1] == "/dev/null":
            target = Path(argv[i + 2])
            if target.is_file() and target.stat().st_size == 0:
                target.unlink()


LOCAL = ".claude/settings.local.json"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    # No harness.toml and no tests/acceptance/ yet: denied paths that do not exist.
    installed = install_project(tmp_path_factory.mktemp("i3c"), "python")
    (installed.root / LOCAL).write_text(json.dumps({"sandbox": {"enabled": True}}))
    return installed


@contextmanager
def _opted_out(project: Installed) -> Iterator[None]:
    local = project.root / LOCAL
    saved = local.read_text()
    local.unlink()
    try:
        yield
    finally:
        local.write_text(saved)


def _env_check(project: Installed) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in project.env.items() if k != "SANDBOX_RUNTIME"}
    return subprocess.run(
        ["make", "-s", "--no-print-directory", "env-check"],
        cwd=project.root,
        env={**env, "NO_COLOR": "1", "PATH": os.environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )


@needs_bwrap
def test_a_write_to_progress_through_an_expansion_fails(project: Installed) -> None:
    res = _sandboxed(project, 'd=progress; echo x > "$(echo $d)/probe.md"')
    assert res.returncode != 0
    assert "Read-only file system" in res.stderr
    assert not (project.root / "progress/probe.md").exists()


@needs_bwrap
@pytest.mark.parametrize(
    "command",
    [
        "python3 -c \"open('progress/review_1.1.1.md','w').write('APPROVED')\"",
        'd=harness; touch "$(echo $d)/zz_probe"',
        'd=tests/acceptance; touch "$(echo $d)/test_probe.py"',
        "touch .claude/zz_probe",
        "echo '[scope]' > harness.toml",
    ],
)
def test_the_other_denied_paths_are_read_only(project: Installed, command: str) -> None:
    res = _sandboxed(project, command)
    assert res.returncode != 0, res.stdout + res.stderr
    # Read-only for what exists; a path that does not is a read-only /dev/null.
    denials = ("Read-only file system", "Not a directory", "Permission denied")
    assert any(d in res.stderr for d in denials), res.stderr
    assert not (TOOLKIT / "lib/zz_probe").exists()
    assert not (project.root / "harness.toml").exists()
    assert not (project.root / "tests/acceptance").exists()


@needs_bwrap
def test_src_stays_writable(project: Installed) -> None:
    res = _sandboxed(project, "echo 'X = 1' > src/demo/probe.py && cat src/demo/probe.py")
    assert res.returncode == 0, res.stderr
    assert (project.root / "src/demo/probe.py").read_text() == "X = 1\n"
    (project.root / "src/demo/probe.py").unlink()


@needs_bwrap
def test_make_check_passes_inside_the_sandbox(project: Installed) -> None:
    res = _sandboxed(project, "make check")
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
    assert "Harness invariants OK" in res.stdout
    assert "inside Claude Code's Bash sandbox" in res.stdout  # the doctor saw it


@needs_bwrap
def test_the_doctor_passes_on_this_machine(project: Installed) -> None:
    res = _env_check(project)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[OK]    Bash sandbox" in res.stdout


def test_without_the_opt_in_make_check_says_nothing_about_the_sandbox(
    project: Installed,
) -> None:
    """The toolkit default: every project runs env-check, none hears of the sandbox."""
    with _opted_out(project):
        res = _env_check(project)
        check = subprocess.run(
            ["make", "check"],
            cwd=project.root,
            env={**project.env, "NO_COLOR": "1"},
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "sandbox" not in (res.stdout + res.stderr).lower()
    assert check.returncode == 0, check.stdout[-4000:] + check.stderr[-2000:]
    assert "Bash sandbox" not in check.stdout + check.stderr
