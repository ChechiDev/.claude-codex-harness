"""I9: the two PreToolUse guards block (exit 2) whenever they cannot do their job.

Claude Code only blocks a PreToolUse call on exit 2; any other non-zero exit lets the
action through. Every failure mode below must therefore end in exit 2 with a message.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT, Project, bash_payload, write_payload

BLOCK = 2
GUARDS = ("guard_write.sh", "guard_bash.sh")
FUTURE = "from __future__ import annotations\n"


def _harmless(project: Project, guard: str) -> dict[str, Any]:
    if guard == "guard_write.sh":
        return write_payload(project.root / "notes.md")
    return bash_payload("ls")


def _raw(
    project: Project, guard: str, stdin: str, env: dict[str, str] | None = None
) -> tuple[int, str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    res = subprocess.run(
        ["bash", str(project.root / "harness" / guard)],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=project.root,
        env={**base, "CLAUDE_PROJECT_DIR": str(project.root), **(env or {})},
        check=False,
        timeout=120,
    )
    return res.returncode, res.stdout + res.stderr


def _relink_to_copy(project: Project, tmp_path: Path) -> Path:
    """Point the project's harness link at a throwaway copy of the toolkit lib/."""
    lib = tmp_path / "toolkit" / "lib"
    shutil.copytree(TOOLKIT / "lib", lib, symlinks=True)
    link = project.root / "harness"
    link.unlink()
    link.symlink_to(lib)
    return lib


def _inject(module: Path, code: str) -> None:
    text = module.read_text()
    assert text.count(FUTURE) == 1, module
    module.write_text(text.replace(FUTURE, FUTURE + code))


# ── malformed and non-object payloads ───────────────────────────────────────


@pytest.mark.parametrize("guard", GUARDS)
@pytest.mark.parametrize("stdin", ["[]", '"x"', "42", "null"])
def test_a_non_object_payload_blocks(project: Project, guard: str, stdin: str) -> None:
    code, out = _raw(project, guard, stdin)
    assert code == BLOCK, out
    assert "BLOCKED" in out


# ── incomplete payloads ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Write"},
        {"tool_name": "Write", "tool_input": None},
        {"tool_name": "Write", "tool_input": "notes.md"},
        {"tool_name": "Write", "tool_input": {}},
        {"tool_name": "Write", "tool_input": {"file_path": 5}},
        {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": ["x"]}},
    ],
)
def test_guard_write_blocks_an_incomplete_payload(project: Project, payload: object) -> None:
    code, out = _raw(project, "guard_write.sh", json.dumps(payload))
    assert code == BLOCK, out
    assert "BLOCKED" in out


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Bash"},
        {"tool_name": "Bash", "tool_input": None},
        {"tool_name": "Bash", "tool_input": "ls"},
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash", "tool_input": {"command": 5}},
        {"tool_name": "Bash", "tool_input": {"command": None}},
    ],
)
def test_guard_bash_blocks_an_incomplete_payload(project: Project, payload: object) -> None:
    code, out = _raw(project, "guard_bash.sh", json.dumps(payload))
    assert code == BLOCK, out
    assert "BLOCKED" in out


@pytest.mark.parametrize("command", ["", "   "])
def test_guard_bash_still_allows_an_empty_command(project: Project, command: str) -> None:
    code, out = _raw(project, "guard_bash.sh", json.dumps(bash_payload(command)))
    assert code == 0, out


# ── start-up failures (throwaway toolkit copy) ──────────────────────────────


@pytest.mark.parametrize("guard", GUARDS)
@pytest.mark.parametrize(
    ("module", "code"),
    [
        ("_guard.py", "raise RuntimeError('broken on import')\n"),
        ("_harness.py", "raise ImportError('broken dependency')\n"),
        ("_attribution.py", "def broken(:\n"),  # syntax error in an imported module
        ("_guard.py", "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"),
    ],
)
def test_a_guard_that_cannot_start_blocks(
    project: Project, tmp_path: Path, guard: str, module: str, code: str
) -> None:
    lib = _relink_to_copy(project, tmp_path)
    _inject(lib / module, code)
    rc, out = _raw(project, guard, json.dumps(_harmless(project, guard)))
    assert rc == BLOCK, out
    assert "BLOCKED" in out
    assert guard in out  # the message names the hook


@pytest.mark.parametrize("guard", GUARDS)
def test_a_guard_blocks_without_python(project: Project, tmp_path: Path, guard: str) -> None:
    """Only bash and dirname on PATH: `python3` is missing (exit 127 today)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "dirname"):
        found = shutil.which(tool)
        assert found
        (bin_dir / tool).symlink_to(found)
    rc, out = _raw(project, guard, json.dumps(_harmless(project, guard)), {"PATH": str(bin_dir)})
    assert rc == BLOCK, out
    assert "BLOCKED" in out
    assert guard in out


# ── swallowed helper error: git alias lookup ────────────────────────────────


def test_a_failing_git_alias_lookup_blocks(project: Project) -> None:
    config = project.root / ".git" / "config"
    config.write_text(config.read_text() + "\n[alias\n  ci = commit\n")  # corrupt section
    rc, out = _raw(project, "guard_bash.sh", json.dumps(bash_payload("git ci -m x")))
    assert rc == BLOCK, out


def test_an_unset_git_alias_is_still_not_a_commit(project: Project) -> None:
    rc, out = _raw(project, "guard_bash.sh", json.dumps(bash_payload("git st")))
    assert rc == 0, out


# ── settings.json: Claude Code blocks when a guard cannot start or times out ─


def _handlers() -> dict[str, dict[str, Any]]:
    settings = json.loads((TOOLKIT / "settings.json").read_text())
    return {
        hook["command"].rsplit("/", 1)[-1].rstrip('"'): hook
        for entries in settings["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    }


def test_both_guards_block_on_hook_failure() -> None:
    handlers = _handlers()
    for guard in GUARDS:
        assert handlers[guard].get("onFailure") == "block", guard


def test_feedback_and_stop_hooks_do_not_set_on_failure() -> None:
    handlers = _handlers()
    for hook in ("post_write.sh", "stop_gate.sh"):
        assert "onFailure" not in handlers[hook], hook


# ── controls: healthy payloads keep today's decisions ───────────────────────


@pytest.mark.parametrize("guard", GUARDS)
def test_a_harmless_action_is_still_allowed(project: Project, guard: str) -> None:
    rc, out = _raw(project, guard, json.dumps(_harmless(project, guard)))
    assert rc == 0, out


def test_the_oracle_and_a_claude_commit_are_still_blocked(project: Project) -> None:
    oracle = write_payload(project.root / "tests" / "acceptance" / "test_x.py")
    rc, out = _raw(project, "guard_write.sh", json.dumps(oracle))
    assert rc == BLOCK and "independent oracle" in out
    rc, out = _raw(project, "guard_bash.sh", json.dumps(bash_payload("git commit -m x")))
    assert rc == BLOCK and "BLOCKED" in out
