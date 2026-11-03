"""I0.1: start-up smoke test of every hook wired in settings.json (regression baseline).

Each hook must start and answer a valid payload and a malformed one. Today's behaviour
is RECORDED, not judged: where it is known to be wrong, the test carries
`@pytest.mark.pins("<issue>")` and that issue will flip it explicitly.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
from conftest import TOOLKIT, Project, bash_payload, write_payload

HOOKS = {"guard_write.sh", "guard_bash.sh", "post_write.sh", "stop_gate.sh"}
MALFORMED = ("not json{", "")


def _wired_hooks() -> set[str]:
    settings = json.loads((TOOLKIT / "settings.json").read_text())
    commands = [
        hook["command"]
        for entries in settings["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    return {m for c in commands for m in re.findall(r"harness/([a-z_]+\.sh)", c)}


def test_the_baseline_covers_every_hook_wired_in_settings() -> None:
    """A new hook must be added to this baseline before it ships."""
    assert _wired_hooks() == HOOKS


def _raw(project: Project, script: str, stdin: str) -> tuple[int, str]:
    """Run a hook with a raw stdin (Project.run only takes dict payloads)."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    res = subprocess.run(
        ["bash", str(project.root / "harness" / script)],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=project.root,
        env={**base, "CLAUDE_PROJECT_DIR": str(project.root)},
        check=False,
        timeout=120,
    )
    return res.returncode, res.stdout + res.stderr


# ── guard_write.sh ───────────────────────────────────────────────────────────


def test_guard_write_allows_a_harmless_write(project: Project) -> None:
    assert project.run("guard_write.sh", write_payload(project.root / "notes.md")).returncode == 0


def test_guard_write_is_alive_and_blocks_the_oracle(project: Project) -> None:
    payload = write_payload(project.root / "tests" / "acceptance" / "test_x.py")
    assert project.run("guard_write.sh", payload).returncode == 2


@pytest.mark.pins("I9")
@pytest.mark.parametrize("stdin", MALFORMED)
def test_guard_write_fails_open_on_a_malformed_payload_today(project: Project, stdin: str) -> None:
    """Known wrong: a guard that cannot read its input lets the action through. I9 makes
    it fail closed (blocking)."""
    code, _ = _raw(project, "guard_write.sh", stdin)
    assert code == 0


# ── guard_bash.sh ────────────────────────────────────────────────────────────


def test_guard_bash_allows_a_harmless_command(project: Project) -> None:
    assert project.run("guard_bash.sh", bash_payload("ls")).returncode == 0


def test_guard_bash_is_alive_and_blocks_a_claude_commit(project: Project) -> None:
    assert project.run("guard_bash.sh", bash_payload("git commit -m x")).returncode == 2


@pytest.mark.pins("I9")
@pytest.mark.parametrize("stdin", MALFORMED)
def test_guard_bash_fails_open_on_a_malformed_payload_today(project: Project, stdin: str) -> None:
    code, _ = _raw(project, "guard_bash.sh", stdin)
    assert code == 0


# ── post_write.sh (feedback only, never blocks) ──────────────────────────────


@pytest.mark.parametrize("stdin", ["valid", *MALFORMED])
def test_post_write_never_blocks(project: Project, stdin: str) -> None:
    raw = json.dumps(write_payload(project.root / "notes.md")) if stdin == "valid" else stdin
    code, _ = _raw(project, "post_write.sh", raw)
    assert code == 0


# ── stop_gate.sh ─────────────────────────────────────────────────────────────


def _makefile(project: Project, check_ok: bool) -> None:
    body = "\t@echo check-ok\n" if check_ok else "\t@echo check-red; exit 1\n"
    project.write("Makefile", f"check:\n{body}")


@pytest.mark.parametrize("stdin", [json.dumps({"stop_hook_active": False}), *MALFORMED])
def test_stop_gate_lets_the_session_close_when_check_is_green(project: Project, stdin: str) -> None:
    _makefile(project, check_ok=True)
    code, out = _raw(project, "stop_gate.sh", stdin)
    assert code == 0, out
    assert "session ready to close" in out


@pytest.mark.parametrize("stdin", [json.dumps({"stop_hook_active": False}), *MALFORMED])
def test_stop_gate_blocks_when_check_is_red(project: Project, stdin: str) -> None:
    _makefile(project, check_ok=False)
    code, out = _raw(project, "stop_gate.sh", stdin)
    assert code == 2
    assert "make check FAILED" in out


@pytest.mark.pins("I12c")
def test_stop_gate_lets_a_second_red_stop_through_today(project: Project) -> None:
    """Deliberate today (avoids a loop): the second blocked stop passes. I12c decides."""
    _makefile(project, check_ok=False)
    code, out = _raw(project, "stop_gate.sh", json.dumps({"stop_hook_active": True}))
    assert code == 0
    assert "not blocking again" in out


def test_stop_gate_without_a_makefile_blocks(project: Project) -> None:
    code, out = _raw(project, "stop_gate.sh", "")
    assert code == 2, out
