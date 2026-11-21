"""Shared harness Make targets (lib/harness.mk) and the one-time migration of
existing projects (install.sh --migrate): what is shared is reached through the
harness/ symlink, so a new harness target reaches every project with no edit."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT

HARNESS_TARGETS = ("plan", "status", "check", "issue", "issue-start", "issue-new")
OWN_TARGETS = ("lint", "type", "test", "env-check")  # verify has no recipe of its own
TEMPLATES = ("templates", "templates-c")


def _old_template(template: str) -> str:
    """The template Makefile as installed before harness.mk existed."""
    return subprocess.run(
        ["git", "show", f"54ca858:{template}/Makefile"],
        cwd=TOOLKIT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def _project(tmp_path: Path, makefile: str, link: bool = True) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Makefile").write_text(makefile)
    (root / "CLAUDE.md").write_text("# Project instructions for Claude\n\n## Project context\nx\n")
    (root / "AGENTS.md").write_text("# AGENTS.md — Navigation map for agents\n")
    if link:
        (root / "harness").symlink_to(TOOLKIT / "lib")
    return root


def _origins(root: Path) -> dict[str, str]:
    """Target -> the makefile that defines its recipe, from `make -pn` (GNU make)."""
    out = subprocess.run(
        ["make", "-pn", "-f", "Makefile", "__harness_none__"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    found: dict[str, str] = {}
    current = None
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+):", line)
        if m:
            current = m.group(1)
            continue
        src = re.match(r"^#\s+recipe to execute \(from '([^']+)', line \d+\):", line)
        if src and current:
            found[current] = src.group(1)
    return found


# The links install.sh creates: the migration only verifies them (M0a, #14).
LINKS = {
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
    "harness": "lib",
}


def _migrate(root: Path) -> subprocess.CompletedProcess[str]:
    for rel, target in LINKS.items():
        if not (root / rel).is_symlink():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).symlink_to(TOOLKIT / target)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    env["CLAUDE_HOME"] = str(root.parent / "claude-home")
    return subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(root), "--migrate", "--apply", "--yes"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


# ── harness.mk and the templates ─────────────────────────────────────────────


@pytest.mark.parametrize("template", TEMPLATES)
def test_templates_take_the_harness_targets_from_harness_mk(tmp_path: Path, template: str) -> None:
    root = _project(tmp_path, (TOOLKIT / template / "Makefile").read_text())
    origins = _origins(root)
    for target in HARNESS_TARGETS:
        assert origins.get(target) == "harness/harness.mk", target
    for target in OWN_TARGETS:
        assert origins.get(target) == "Makefile", target


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_default_goal_stays_the_projects(tmp_path: Path, template: str) -> None:
    root = _project(tmp_path, (TOOLKIT / template / "Makefile").read_text())
    out = subprocess.run(["make", "-pn"], cwd=root, text=True, capture_output=True).stdout
    assert ".DEFAULT_GOAL := lint" in out


def test_a_missing_harness_link_says_how_to_fix_it(tmp_path: Path) -> None:
    root = _project(tmp_path, (TOOLKIT / "templates/Makefile").read_text(), link=False)
    res = subprocess.run(["make", "status"], cwd=root, text=True, capture_output=True)
    assert res.returncode != 0
    assert "harness/ is missing on this machine: run install.sh" in res.stderr


def test_issue_targets_call_the_issue_script(tmp_path: Path) -> None:
    root = _project(tmp_path, (TOOLKIT / "templates/Makefile").read_text())

    def dry(*args: str) -> str:
        res = subprocess.run(["make", "-n", *args], cwd=root, text=True, capture_output=True)
        return res.stdout + res.stderr

    assert "harness/issue.sh queue 14 now" in dry("issue", "N=14", "PRIORITY=now")
    assert "harness/issue.sh queue 14 next" in dry("issue", "N=14")
    assert "harness/issue.sh start 14" in dry("issue-start", "N=14")
    assert "harness/issue.sh new" in dry("issue-new")
    assert "N=<issue number> is required" in dry("issue")


@pytest.mark.parametrize("template", TEMPLATES)
def test_claude_md_template_imports_the_shared_workflow(template: str) -> None:
    text = (TOOLKIT / template / "CLAUDE.md").read_text()
    assert "\n@harness/WORKFLOW.md\n" in text
    assert "## Project context" in text
    assert "Hard harness rules" not in text  # now shared, in WORKFLOW.md


def test_workflow_describes_the_issues_lane() -> None:
    text = (TOOLKIT / "lib/WORKFLOW.md").read_text()
    for needle in (
        "Hard harness rules",
        "Startup protocol",
        "Issues lane",
        "make issue N=",
        "make issue-start N=",
        "ISS-<n>.<k>",
        "review_with_codex.sh issue",
    ):
        assert needle in text, needle


@pytest.mark.parametrize("template", TEMPLATES)
def test_agents_md_points_to_the_shared_workflow(template: str) -> None:
    assert "harness/WORKFLOW.md" in (TOOLKIT / template / "AGENTS.md").read_text()


# ── install.sh --migrate (existing projects, once) ───────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize("template", TEMPLATES)
def test_migrate_moves_an_untouched_project_to_harness_mk(tmp_path: Path, template: str) -> None:
    root = _project(tmp_path, _old_template(template))
    res = _migrate(root)
    assert res.returncode == 0, res.stdout + res.stderr
    text = (root / "Makefile").read_text()
    assert "include harness/harness.mk" in text
    assert not re.search(r"^(plan|status|check):", text, re.MULTILINE)
    origins = _origins(root)
    assert all(origins.get(t) == "harness/harness.mk" for t in HARNESS_TARGETS)
    assert (root / "CLAUDE.md").read_text().startswith("@harness/WORKFLOW.md\n")
    assert "harness/WORKFLOW.md" in (root / "AGENTS.md").read_text()


@pytest.mark.e2e
def test_migrate_is_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path, _old_template("templates"))
    assert _migrate(root).returncode == 0
    before = {p: (root / p).read_text() for p in ("Makefile", "CLAUDE.md", "AGENTS.md")}
    res = _migrate(root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert {p: (root / p).read_text() for p in before} == before
    assert "already migrated" in res.stdout + res.stderr


@pytest.mark.e2e
def test_migrate_stops_on_a_customised_harness_target(tmp_path: Path) -> None:
    custom = _old_template("templates").replace(
        "\t./harness/check.sh || rc=1; \\", "\t./harness/check.sh || rc=1; ./extra.sh; \\"
    )
    root = _project(tmp_path, custom)
    res = _migrate(root)
    assert res.returncode == 1
    assert "check: customised in Makefile" in res.stdout + res.stderr
    assert (root / "Makefile").read_text() == custom  # nothing changed
    assert not (root / "CLAUDE.md").read_text().startswith("@harness")


@pytest.mark.e2e
def test_migrate_keeps_project_targets_and_variables(tmp_path: Path) -> None:
    extra = "\n# project target\ndocker:\n\tdocker compose up\n"
    root = _project(tmp_path, _old_template("templates") + extra)
    assert _migrate(root).returncode == 0
    text = (root / "Makefile").read_text()
    assert "docker:\n\tdocker compose up" in text
    assert "STATUS := ./harness/status.sh" in text
    assert _origins(root).get("docker") == "Makefile"


@pytest.mark.parametrize("template", TEMPLATES)
def test_new_projects_ship_the_issues_lane_on(template: str) -> None:
    readme = TOOLKIT / template / "docs/plan/issues/README.md"
    assert "Issues lane" in readme.read_text()


@pytest.mark.e2e
def test_migrate_turns_the_issues_lane_on(tmp_path: Path) -> None:
    root = _project(tmp_path, _old_template("templates"))
    assert _migrate(root).returncode == 0
    assert (root / "docs/plan/issues/README.md").is_file()


# ── CI: harness/ is per machine, a checkout elsewhere has it dangling ────────

OLD_GUARD = (
    "\n# Harness targets, shared by every project (keep this block last).\n"
    "ifeq ($(wildcard harness/harness.mk),)\n"
    "$(error harness/ is missing on this machine: run install.sh <project> first)\n"
    "endif\n"
    "include harness/harness.mk\n"
)


@pytest.mark.e2e
@pytest.mark.parametrize("template", TEMPLATES)
def test_project_targets_work_without_the_harness_link(tmp_path: Path, template: str) -> None:
    root = _project(tmp_path, (TOOLKIT / template / "Makefile").read_text(), link=False)
    (root / "harness").symlink_to("/nonexistent/lib")  # as in a CI checkout
    res = subprocess.run(["make", "-n", "verify"], cwd=root, text=True, capture_output=True)
    assert "harness/ is missing" not in res.stdout + res.stderr
    assert "uv run" in res.stdout or "cc " in res.stdout or res.returncode == 0
    res = subprocess.run(["make", "check"], cwd=root, text=True, capture_output=True)
    assert res.returncode != 0
    assert "harness/ is missing on this machine: run install.sh" in res.stderr


@pytest.mark.e2e
def test_migrate_replaces_the_first_guard_block(tmp_path: Path) -> None:
    first = _old_template("templates")
    for target in ("plan", "status", "check"):  # drop the copied targets, as --migrate did
        first = re.sub(rf"(# [^\n]*\n)*{target}:\n(\t[^\n]*\n)+\n?", "", first)
    root = _project(tmp_path, first + OLD_GUARD)
    res = _migrate(root)
    assert res.returncode == 0, res.stdout + res.stderr
    text = (root / "Makefile").read_text()
    assert "$(error" not in text
    assert text.count("harness/harness.mk") >= 2
    assert _migrate(root).returncode == 0  # idempotent
    assert (root / "Makefile").read_text() == text


@pytest.mark.e2e
def test_the_fallback_still_fails_when_a_file_has_a_target_name(tmp_path: Path) -> None:
    root = _project(tmp_path, (TOOLKIT / "templates/Makefile").read_text(), link=False)
    (root / "harness").symlink_to("/nonexistent/lib")
    (root / "check").write_text("not a target\n")
    res = subprocess.run(["make", "check"], cwd=root, text=True, capture_output=True)
    assert res.returncode != 0
    assert "harness/ is missing on this machine" in res.stderr
