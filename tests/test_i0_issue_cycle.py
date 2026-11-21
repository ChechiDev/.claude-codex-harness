"""I0.5: the whole issues lane on an installed Python project, as it behaves today.

GitHub issue #14 (label `enhancement`) -> `make issue` (queue) -> `make issue-start`
(claim tag, assignee, branch, header) -> one subtask ISS-14.1 -> issue gate -> plan gate
(sealed oracle) -> implementation -> code gate -> close (one commit) -> PR with
`Closes #14` -> claim dropped. Codex is the fake of tests/i0_cycle.py; gh is the
stateful tests/fake_gh.py plus `pr view` / `pr create`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from i0_cycle import fake_tools, gate, git, hook, spec
from i0_projects import Installed, install_project

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
N = "14"
IID = f"ISS-{N}"
SID = f"{IID}.1"
ORACLE = f"tests/acceptance/test_{SID}.py"
CODE = "src/demo/greet.py"
ORACLE_BODY = (
    f'"""Acceptance test of {SID} (written by Codex at the plan gate)."""\n\n'
    "from demo.greet import greet\n\n\ndef test_ac1_greets() -> None:\n"
    '    """AC1: greets by name."""\n'
    '    assert greet("Ana") == "Hello, Ana!"\n'
)
CODE_BODY = (
    '"""Greeting."""\n\n\ndef greet(name: str) -> str:\n'
    '    """Return a greeting for name."""\n'
    '    return f"Hello, {name}!"\n'
)
GH_WRAPPER = f"""#!/usr/bin/env bash
# fake_gh.py for the issues, plus the two PR calls the close makes.
echo "$*" >> "$I0_GH_LOG"
case "$1 $2" in
  "pr view") exit 1 ;;
  "pr create")
    while [ $# -gt 0 ]; do [ "$1" = --body-file ] && cp "$2" "$I0_GH_LOG.body"; shift; done
    exit 0 ;;
esac
exec python3 {FAKE_GH} "$@"
"""


def _ok(res: subprocess.CompletedProcess[str], what: str) -> None:
    assert res.returncode == 0, f"{what} failed (rc={res.returncode}):\n{res.stdout}{res.stderr}"


def _gh_env(tmp: Path, env: dict[str, str]) -> dict[str, str]:
    state = tmp / "gh.json"
    issue = {"title": "Add a greeting", "labels": ["enhancement"], "assignees": [],
             "body": "## Problem\nNobody is greeted.\n"}  # fmt: skip
    state.write_text(json.dumps({"me": "dev", "next": 15, "issues": {N: issue}}))
    wrapper = tmp / "fakebin" / "gh"  # replaces the task-cycle gh on PATH
    wrapper.write_text(GH_WRAPPER)
    wrapper.chmod(0o755)
    return {**env, "HARNESS_GH": str(wrapper), "FAKE_GH_STATE": str(state),
            "I0_PR_OWNER": IID}  # fmt: skip


def _gh_issue(tmp: Path) -> dict[str, Any]:
    issue: dict[str, Any] = json.loads((tmp / "gh.json").read_text())["issues"][N]
    return issue


def _run(project: Installed, env: dict[str, str], *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), cwd=project.root, env=env, text=True,
                          capture_output=True, check=False, timeout=900)  # fmt: skip


def _queue_and_start(project: Installed, env: dict[str, str], tmp_path: Path) -> str:
    """Steps 1-2; returns the issue branch."""
    # 1. Queue: priority label from the default (next); the type comes from the label.
    _ok(_run(project, env, "make", "-s", "issue", f"N={N}"), "make issue")
    assert "priority:next" in _gh_issue(tmp_path)["labels"]

    # 2. Start: claim tag on origin, assigned + in progress, branch and header.
    _ok(_run(project, env, "make", "-s", "issue-start", f"N={N}"), "make issue-start")
    branch = git(project, "branch", "--show-current")
    assert branch == f"feat/{IID}-add-a-greeting"
    issue = _gh_issue(tmp_path)
    assert issue["assignees"] == ["dev"]
    assert "status:in-progress" in issue["labels"] and "priority:next" not in issue["labels"]
    remote = tmp_path / "origin.git"
    assert git(project, "--git-dir", str(remote), "tag", "-l", f"claim/{IID}")
    header = project.root / "docs/plan/issues" / f"{IID}-add-a-greeting.md"
    assert header.read_text().startswith(f"# {IID} Add a greeting\n")
    return branch


def test_issue_cycle_end_to_end(tmp_path: Path) -> None:
    project = install_project(tmp_path, "python")
    spec_dir = "docs/plan/issues"
    env = _gh_env(tmp_path, fake_tools(project, tmp_path, ORACLE, ORACLE_BODY, ""))
    git(project, "push", "-q", "origin", "main")
    git(project, "fetch", "-q", "origin")

    branch = _queue_and_start(project, env, tmp_path)
    remote = tmp_path / "origin.git"

    # 3. Breakdown: one subtask spec, issue gate (no roadmap or phase gate needed).
    spec_path = f"{spec_dir}/{IID}-add-a-greeting/{SID}-greet.md"
    env["I0_SPEC"] = spec_path
    text = spec([CODE], ORACLE, "in_progress").replace("# 1.1.1 ", f"# {SID} ")
    (project.root / spec_path).parent.mkdir(parents=True)
    (project.root / spec_path).write_text(text)
    _ok(project.make("plan"), "make plan")
    _ok(gate(project, env, "issue", IID), "issue gate")
    assert (project.root / f"progress/issue_review_{IID}.md").is_file()

    # 4. The usual subtask cycle: plan gate (sealed oracle, red), guard, code, code gate.
    _ok(gate(project, env, "plan", SID), "plan gate")
    assert (project.root / f"progress/acceptance_{SID}.sha256").is_file()
    assert project.make("check").returncode != 0, "the new oracle must be red first"
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(project.root / CODE)}}
    assert hook(project, "guard_write.sh", json.dumps(payload)).returncode == 0
    (project.root / CODE).write_text(CODE_BODY)
    _ok(project.make("check"), "make check after the implementation")
    _ok(gate(project, env, "code", SID), "code gate")

    # 5. Close: one commit on the issue branch, spec done, clean tree, no attribution.
    before = int(git(project, "rev-list", "--count", "HEAD"))
    _ok(gate(project, env, "doc", SID), "close (doc gate)")
    assert int(git(project, "rev-list", "--count", "HEAD")) == before + 1
    message = git(project, "log", "-1", "--format=%B")
    assert message.startswith(f"feat(domain): greet by name [{SID}]")
    assert "co-authored-by" not in message.lower()
    assert git(project, "status", "--porcelain") == ""
    assert "- status: done" in (project.root / spec_path).read_text()

    # 6. PR from the issue branch into main that closes the issue; the claim is dropped.
    assert git(project, "--git-dir", str(remote), "rev-parse", branch) == git(
        project, "rev-parse", "HEAD"
    )
    calls = (tmp_path / "gh.log").read_text().splitlines()
    create = next(c for c in calls if c.startswith("pr create"))
    assert f"--head {branch}" in create and "--base main" in create
    body = (tmp_path / "gh.log.body").read_text()
    assert f"Closes #{N}" in body and f"Greeting {IID}." in body
    assert not git(project, "--git-dir", str(remote), "tag", "-l", f"claim/{IID}")
    assert not git(project, "tag", "-l", f"claim/{IID}")

    # 7. GitHub closes the issue on merge (the user's call): until then it stays claimed.
    issue = _gh_issue(tmp_path)
    assert issue.get("state", "OPEN") == "OPEN" and "status:in-progress" in issue["labels"]
    _ok(project.make("check"), "final make check")
    gates = (tmp_path / "codex.log").read_text().split()
    assert gates == ["issue", IID, "plan", SID, "code", SID, "doc", SID]
