"""#76: explicit hook timeouts, write-tool matchers and file-scoped post-write lint."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import pytest
from conftest import LEAF_ID, TOOLKIT, Project, write_payload

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
GUARDS = ("guard_write.sh", "guard_bash.sh")
# Upper bounds per hook (seconds): guards short, advisory Stop short, lint moderate.
MAX_TIMEOUT = {"guard_write.sh": 30, "guard_bash.sh": 30, "stop_gate.sh": 30, "post_write.sh": 180}


def _settings() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((TOOLKIT / "settings.json").read_text(encoding="utf-8"))
    return data


def _entries() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    return [
        (event, entry, hook)
        for event, entries in _settings()["hooks"].items()
        for entry in entries
        for hook in entry["hooks"]
    ]


def _script(hook: dict[str, Any]) -> str:
    return str(hook["command"]).rsplit("/", 1)[-1].rstrip('"')


def test_every_hook_has_an_explicit_timeout_within_its_budget() -> None:
    for _, _, hook in _entries():
        name = _script(hook)
        timeout = hook.get("timeout")
        assert isinstance(timeout, int) and not isinstance(timeout, bool), name
        assert 0 < timeout <= MAX_TIMEOUT[name], (name, timeout)


def test_guards_are_shorter_than_the_post_write_lint() -> None:
    timeouts = {_script(h): h["timeout"] for _, _, h in _entries()}
    assert max(timeouts[g] for g in GUARDS) < timeouts["post_write.sh"]


def test_a_timed_out_guard_blocks() -> None:
    # Claude Code applies onFailure to start-up failures and timeouts alike (I9).
    for _, _, hook in _entries():
        if _script(hook) in GUARDS:
            assert hook["onFailure"] == "block"
            assert hook["timeout"] > 0


@pytest.mark.parametrize("script", ["guard_write.sh", "post_write.sh"])
def test_write_hooks_match_every_file_writing_tool(script: str) -> None:
    matchers = [e["matcher"] for _, e, h in _entries() if _script(h) == script]
    assert matchers
    for matcher in matchers:
        assert set(matcher.split("|")) >= WRITE_TOOLS, matcher


# ── guards on MultiEdit / NotebookEdit payloads ──────────────────────────────


def _multiedit(path: str) -> dict[str, Any]:
    return {
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": path, "edits": [{"old_string": "a", "new_string": "b"}]},
    }


@pytest.mark.parametrize(
    "payload",
    [
        _multiedit(f"tests/acceptance/test_{LEAF_ID}.py"),
        write_payload(f"tests/acceptance/test_{LEAF_ID}.ipynb", tool="NotebookEdit"),
        _multiedit("src/pkg/undeclared.py"),
        write_payload("src/pkg/undeclared.ipynb", tool="NotebookEdit"),
    ],
)
def test_protected_multiedit_and_notebook_writes_are_blocked(
    project: Project, payload: dict[str, Any]
) -> None:
    res = project.run("guard_write.sh", payload)
    assert res.returncode == 2, res.stderr


# ── post_write.sh: feedback for every tool, lint scoped to the written file ──


def _post_write(project: Project, payload: dict[str, Any] | str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    env["CLAUDE_PROJECT_DIR"] = str(project.root)
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    res = subprocess.run(
        ["bash", str(TOOLKIT / "lib" / "post_write.sh")],
        input=raw,
        capture_output=True,
        text=True,
        env=env,
        cwd=project.root,
        check=False,
        timeout=60,
    )
    return res.returncode, res.stdout + res.stderr


SCOPED_MAKEFILE = "LINT_FILES ?= .\nlint:\n\t@echo lint-on:$(LINT_FILES)\n"


@pytest.mark.parametrize(
    "payload",
    [_multiedit("src/pkg/user.py"), write_payload("src/pkg/user.ipynb", tool="NotebookEdit")],
)
def test_post_write_gives_feedback_for_multiedit_and_notebook(
    project: Project, payload: dict[str, Any]
) -> None:
    project.write("Makefile", SCOPED_MAKEFILE)
    code, out = _post_write(project, payload)
    assert code == 0
    assert "fast lint after the write" in out


def test_post_write_scopes_lint_to_the_written_file_when_the_makefile_allows_it(
    project: Project,
) -> None:
    project.write("Makefile", SCOPED_MAKEFILE)
    project.write("src/pkg/user.py", "")
    code, out = _post_write(project, _multiedit(str(project.root / "src/pkg/user.py")))
    assert code == 0
    assert "lint-on:src/pkg/user.py" in out


def test_post_write_falls_back_to_the_full_lint_without_lint_files(project: Project) -> None:
    project.write("Makefile", "lint:\n\t@echo full-lint:$(LINT_FILES)\n")
    code, out = _post_write(project, write_payload(project.root / "notes.md"))
    assert code == 0
    assert "full-lint:" in out and "notes.md" not in out


@pytest.mark.parametrize("raw", ["not json{", ""])
def test_post_write_falls_back_to_the_full_lint_on_a_bad_payload(
    project: Project, raw: str
) -> None:
    project.write("Makefile", SCOPED_MAKEFILE)
    code, out = _post_write(project, raw)
    assert code == 0
    assert "lint-on:." in out


def test_post_write_ignores_a_path_outside_the_project(project: Project) -> None:
    project.write("Makefile", SCOPED_MAKEFILE)
    code, out = _post_write(project, write_payload("/etc/passwd"))
    assert code == 0
    assert "lint-on:." in out


EXTS_MAKEFILE = "LINT_FILES ?= .\nLINT_EXTS := .py .pyi\nlint:\n\t@echo lint-on:$(LINT_FILES)\n"


@pytest.mark.parametrize(("rel", "scoped"), [("src/pkg/user.py", True), ("docs/usage.md", False)])
def test_post_write_scopes_only_files_with_a_lint_extension(
    project: Project, rel: str, scoped: bool
) -> None:
    project.write("Makefile", EXTS_MAKEFILE)
    code, out = _post_write(project, write_payload(rel))
    assert code == 0
    assert (f"lint-on:{rel}" in out) is scoped
    assert ("lint-on:." in out) is not scoped
