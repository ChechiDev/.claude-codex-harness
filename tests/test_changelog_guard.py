"""Releases + CHANGELOG, delivery 1: the guard keeps Claude out of the changelog, the
release plan's verdict and seal, and the human-only commands (docs/design/releases.md)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from conftest import TOOLKIT, Project, bash_payload, write_control, write_payload

sys.path.insert(0, str(TOOLKIT / "lib"))
import _resume  # noqa: E402
import _verdict  # noqa: E402

BLOCKED = 2
# Protected only in an activated project (review rel1rev #2): a started project that
# keeps its own CHANGELOG.md or towncrier fragments is untouched until it activates.
CHANGELOG_PATHS = ("CHANGELOG.md", "changelog.d/1.1.md", "changelog.d/README.md")
# Protected always: the record, the release plan's verdict and seal.
ALWAYS_OWNED = (
    ".harness/changelog.json",
    "progress/releases_review.md",
    "progress/releases.sha256",
)
AGENTS = (None, "claude-planner", "implementer")
RECORD = '{"version": 1, "since": null, "finished": []}'


def _as(payload: dict[str, object], agent: str | None) -> dict[str, object]:
    return payload if agent is None else {**payload, "agent_id": "a1", "agent_type": agent}


@pytest.fixture
def activated(project: Project) -> Project:
    project.write(".harness/changelog.json", RECORD)
    return project


def _write_rc(project: Project, rel: str, agent: str | None = None) -> list[int]:
    return [
        project.run(
            "guard_write.sh", _as(write_payload(project.root / rel, tool), agent)
        ).returncode
        for tool in ("Write", "Edit")
    ]


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("rel", ALWAYS_OWNED)
def test_claude_never_writes_the_record_nor_the_release_seal(
    project: Project, rel: str, agent: str | None
) -> None:
    assert _write_rc(project, rel, agent) == [BLOCKED, BLOCKED], (rel, agent)


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("rel", CHANGELOG_PATHS)
def test_claude_never_writes_the_changelog_once_activated(
    activated: Project, rel: str, agent: str | None
) -> None:
    assert _write_rc(activated, rel, agent) == [BLOCKED, BLOCKED], (rel, agent)


@pytest.mark.parametrize("rel", (*CHANGELOG_PATHS, "changelog.d/123.feature.md"))
def test_unactivated_project_keeps_its_own_changelog(project: Project, rel: str) -> None:
    """Without activation nothing changes but a WARN (docs/design/releases.md)."""
    assert _write_rc(project, rel) == [0, 0], rel


@pytest.mark.parametrize("record", ["{not json", '["a list"]', "dir", "dangling-link"])
def test_an_unreadable_record_counts_as_activated(project: Project, record: str) -> None:
    path = project.root / ".harness" / "changelog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if record == "dir":
        path.mkdir()
    elif record == "dangling-link":
        path.symlink_to(project.root / "missing.json")
    else:
        path.write_text(record, encoding="utf-8")
    assert _write_rc(project, "CHANGELOG.md") == [BLOCKED, BLOCKED], record


def test_activation_on_the_base_branch_protects_a_task_branch(project: Project) -> None:
    project.write(".harness/changelog.json", RECORD)
    project.commit_all("activate")
    subprocess.run(["git", "checkout", "-qb", "feat/x"], cwd=project.root, check=True)
    (project.root / ".harness" / "changelog.json").unlink()
    assert _write_rc(project, "changelog.d/1.1.md") == [BLOCKED, BLOCKED]


def test_planner_writes_the_release_plan(project: Project) -> None:
    payload = _as(write_payload(project.root / "docs/releases.md"), "claude-planner")
    res = project.run("guard_write.sh", payload)
    assert res.returncode == 0, res.stderr


CHANGELOG_COMMANDS = (
    "echo x > CHANGELOG.md",
    "echo '- x' >> changelog.d/1.1.md",
    "rm changelog.d/1.1.md",
    "rm -rf changelog.d",
    "sed -i s/a/b/ CHANGELOG.md",
)


@pytest.mark.parametrize("cmd", CHANGELOG_COMMANDS)
def test_bash_cannot_change_the_changelog_once_activated(activated: Project, cmd: str) -> None:
    res = activated.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize("cmd", [*CHANGELOG_COMMANDS, "rm changelog.d/123.feature.md"])
def test_bash_keeps_an_unactivated_changelog_open(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "cp /tmp/x progress/releases.sha256",
        "echo x > progress/releases_review.md",
        "rm -rf .harness",
        "python3 harness/_changelog.py consolidate --apply",
        "python3 ./harness/_changelog.py consolidate --apply",
        "bash -c 'python3 harness/_changelog.py consolidate --apply'",
        "python3 harness/_changelog.py activate . --apply --yes",
        "python3 harness/_changelog.py install . templates",
        # flags before the subcommand (review rel1rev #1): main() refuses them, and the
        # guard stays fail-closed whatever the order
        "python3 harness/_changelog.py --apply consolidate",
        "python3 harness/_changelog.py consolidate --yes --apply",
        "python3 harness/_changelog.py --yes activate . --apply",
        "python3 harness/_changelog.py --apply activate .",
        "python3 harness/_changelog.py --apply --yes activate .",
        "python3 harness/_changelog.py --apply install . templates",
        "python3 harness/_changelog.py x activate .",
        "cd harness && python3 _changelog.py --apply consolidate",
        "cd harness && python3 _changelog.py --yes activate . --apply",
        "env python3 harness/_changelog.py --apply consolidate",
        "env python3 harness/_changelog.py --yes activate . --apply",
        "cd harness && python3 -m _changelog --apply consolidate",
        "bash -c 'python3 harness/_changelog.py --apply consolidate'",
        "python3 harness/_releases.py seal",
        "./harness/review_with_codex.sh seal releases",
        f"bash {TOOLKIT}/install.sh . --changelog --apply --yes",
        f"{TOOLKIT}/install.sh . --changelog",
    ],
)
def test_bash_cannot_change_the_changelog_or_run_human_commands(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "cat CHANGELOG.md",
        "ls changelog.d",
        "git diff -- changelog.d",
        "python3 harness/_changelog.py consolidate",
        "python3 harness/_changelog.py due 1.1.1",
        "python3 harness/_releases.py ready",
        "python3 harness/_releases.py check",
    ],
)
def test_reading_and_dry_runs_stay_open(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


# ── the releases gate (review_with_codex.sh releases) ─────────────────────────

RELEASES = (
    "# Releases\n- applies: yes\n- versioning: semver\n- version source: VERSION\n"
    "- audience: the ops team\n\n## Planned releases\n| release | phases | goal |\n"
    "|---|---|---|\n| 0.1.0 | 01 | first CLI |\n\n## Compatibility\nSemVer; 0.x bumps MINOR.\n"
    "\n## Patches and early releases\nA patch for urgent fixes.\n"
)


def _manual_gate(project: Project, *args: str) -> subprocess.CompletedProcess[str]:
    write_control(project.root)
    return project.run(
        "review_with_codex.sh", None, *args, env={"UV_CACHE_DIR": "/tmp/fake-uv-cache"}
    )


def test_verdict_protocol_knows_the_releases_gate() -> None:
    assert _verdict.GATE_TOKENS["releases"] == "RELEASES_APPROVED"
    assert _verdict.verdict_path("releases", "releases") == "progress/releases_review.md"
    assert "releases" in _resume.STEPS


@pytest.mark.e2e
def test_releases_gate_prompt_names_the_plan_and_its_verdict(project: Project) -> None:
    project.write("docs/releases.md", RELEASES)
    project.commit_all("release plan")
    res = _manual_gate(project, "releases")
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert "Phase: releases" in out and "codex-planner.md" in out
    assert "docs/releases.md" in out and "progress/releases_review.md" in out
    assert "seal releases" in out


def test_releases_gate_refuses_an_unfilled_plan(project: Project) -> None:
    project.write("docs/releases.md", (TOOLKIT / "templates" / "releases.md").read_text())
    res = _manual_gate(project, "releases")
    assert res.returncode != 0 and "placeholder" in res.stdout + res.stderr


def test_guard_seal_releases_needs_the_approval(project: Project) -> None:
    project.write("docs/releases.md", RELEASES)
    project.write("progress/releases_review.md", "# R\n\n**Verdict:** RELEASES_APPROVED\n")
    res = subprocess.run(
        ["python3", str(project.root / "harness" / "_guard.py"), "seal", "releases"],
        cwd=project.root, env={**os.environ, "CLAUDE_PROJECT_DIR": str(project.root)},
        text=True, capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 0, res.stdout + res.stderr
    assert (project.root / "progress" / "releases.sha256").is_file()
