"""Releases + CHANGELOG, delivery 1: the whole issue cycle of a new install, with its
changelog activated (docs/design/releases.md). The issue gate refuses specs without the
kickoff's declaration; the last close writes changelog.d/ISS-14.md into its commit and
PR; the consolidation then moves it into CHANGELOG.md. Codex and gh are fakes."""

from __future__ import annotations

from pathlib import Path

import pytest
from i0_cycle import fake_tools, gate, git, hook, spec
from i0_projects import install_project
from test_i0_issue_cycle import (
    CODE,
    CODE_BODY,
    IID,
    ORACLE,
    ORACLE_BODY,
    SID,
    _gh_env,
    _ok,
    _queue_and_start,
    _run,
)

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)


def test_issue_cycle_with_the_changelog_activated(tmp_path: Path) -> None:
    project = install_project(tmp_path, "python", changelog=True)
    env = _gh_env(tmp_path, fake_tools(project, tmp_path, ORACLE, ORACLE_BODY, ""))
    git(project, "push", "-q", "origin", "main")
    git(project, "fetch", "-q", "origin")
    _queue_and_start(project, env, tmp_path)

    # Kickoff: without the declaration the issue gate refuses to run.
    spec_path = f"docs/plan/issues/{IID}-add-a-greeting/{SID}-greet.md"
    env["I0_SPEC"] = spec_path
    target = project.root / spec_path
    target.parent.mkdir(parents=True)
    target.write_text(spec([CODE], ORACLE, "in_progress").replace("# 1.1.1 ", f"# {SID} "))
    _ok(project.make("plan"), "make plan")
    refused = gate(project, env, "issue", IID)
    assert refused.returncode != 0 and "changelog declaration" in refused.stdout + refused.stderr
    text = spec([CODE], ORACLE, "in_progress", changelog=True).replace("# 1.1.1 ", f"# {SID} ")
    target.write_text(text)
    _ok(project.make("plan"), "make plan")
    _ok(gate(project, env, "issue", IID), "issue gate")

    # The subtask cycle, then the last close: its commit carries the fragment.
    _ok(gate(project, env, "plan", SID), "plan gate")
    payload = (
        '{"tool_name": "Write", "tool_input": {"file_path": "' + str(project.root / CODE) + '"}}'
    )
    assert hook(project, "guard_write.sh", payload).returncode == 0
    (project.root / CODE).write_text(CODE_BODY)
    _ok(gate(project, env, "code", SID), "code gate")
    _ok(gate(project, env, "doc", SID), "close (doc gate)")
    committed = git(project, "show", "--name-only", "--format=", "HEAD").split()
    assert f"changelog.d/{IID}.md" in committed, committed
    assert git(project, "status", "--porcelain") == ""
    assert "pr create" in (tmp_path / "gh.log").read_text()

    # Claude cannot touch it; the consolidation (the human's) moves it into CHANGELOG.md.
    blocked = hook(
        project,
        "guard_bash.sh",
        '{"tool_name": "Bash", "tool_input": {"command": '
        '"python3 harness/_changelog.py consolidate --apply"}}',
    )
    assert blocked.returncode == 2
    res = _run(project, env, "python3", "harness/_changelog.py", "consolidate", "--apply")
    _ok(res, "consolidate")
    changelog = (project.root / "CHANGELOG.md").read_text()
    assert f"- Greets by name. ({IID})" in changelog
    assert f"<!-- changelog.d/{IID}.md " in changelog
    assert not (project.root / f"changelog.d/{IID}.md").exists()
    _ok(project.make("check"), "make check after the consolidation")
