"""Status tags: [OK] green, [SKIP] orange, [WARN] yellow, [FAIL] red — on a terminal only."""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, Project

NC = "\033[0m"
COLORS = {
    "ok": ("[OK]", "\033[0;32m"),
    "skip": ("[SKIP]", "\033[38;5;208m"),
    "warn": ("[WARN]", "\033[0;33m"),
    "fail": ("[FAIL]", "\033[0;31m"),
}
STATUS_SH = str(TOOLKIT / "lib" / "status.sh")
PY_STATUS = "import sys, _status; _status.say(sys.argv[1].upper(), 'hello')"


def _env(**extra: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    return {**base, "PYTHONPATH": str(TOOLKIT / "lib"), **extra}


def _on_tty(cmd: list[str], env: dict[str, str], cwd: Path | None = None) -> str:
    master, slave = pty.openpty()
    subprocess.run(cmd, stdout=slave, stderr=slave, env=env, cwd=cwd, check=False)
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:  # EIO: the pty slave is closed and drained
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(master)
    return b"".join(chunks).decode()


def _sources(kind: str) -> list[list[str]]:
    return [["bash", STATUS_SH, kind, "hello"], ["python3", "-c", PY_STATUS, kind]]


@pytest.mark.parametrize("kind", sorted(COLORS))
@pytest.mark.parametrize("source", [0, 1], ids=["shell", "python"])
def test_tags_are_colored_on_a_terminal(kind: str, source: int) -> None:
    tag, color = COLORS[kind]
    out = _on_tty(_sources(kind)[source], _env())
    assert f"{color}{tag}{NC}" in out
    assert "hello" in out


@pytest.mark.parametrize("source", [0, 1], ids=["shell", "python"])
def test_captured_output_is_plain_and_aligned(source: int) -> None:
    for kind, (tag, _) in COLORS.items():
        cmd = _sources(kind)[source]
        res = subprocess.run(cmd, env=_env(), text=True, capture_output=True, check=False)
        assert res.stdout == f"{tag.ljust(8)}hello\n"


@pytest.mark.parametrize("source", [0, 1], ids=["shell", "python"])
def test_no_color_disables_colors_on_a_terminal(source: int) -> None:
    out = _on_tty(_sources("ok")[source], _env(NO_COLOR="1"))
    assert "\033[" not in out
    assert "[OK]" in out


@pytest.mark.e2e
def test_check_sh_output_captured_by_hooks_has_no_escape_codes(project: Project) -> None:
    res = project.run("check.sh")
    assert "\033[" not in res.stdout + res.stderr


@pytest.mark.parametrize("template", ["templates", "templates-c"])
def test_makefiles_print_tags_only_through_status_sh(template: str) -> None:
    text = (TOOLKIT / template / "Makefile").read_text()
    for raw in ("[ok]", "[OK]", "[skip]", "[SKIP]", "[FAIL]", "[WARN]"):
        assert raw not in text, raw


@pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")
def test_c_makefile_ok_is_green_on_a_terminal(tmp_path: Path) -> None:
    (tmp_path / "harness").symlink_to(TOOLKIT / "lib")
    shutil.copy(TOOLKIT / "templates-c" / "Makefile", tmp_path / "Makefile")
    out = _on_tty(["make", "-s", "type"], _env(), cwd=tmp_path)
    assert f"{COLORS['ok'][1]}[OK]{NC}" in out


@pytest.mark.parametrize("source", [0, 1], ids=["shell", "python"])
def test_force_color_colors_captured_output(source: int) -> None:
    tag, color = COLORS["ok"]
    cmd = _sources("ok")[source]
    res = subprocess.run(cmd, env=_env(FORCE_COLOR="1"), text=True, capture_output=True)
    assert f"{color}{tag}{NC}" in res.stdout
