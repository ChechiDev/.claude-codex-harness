"""I11: the agent-attribution check matches whole terms only (no `codexion`, no paths).

The same texts go through the three callers: guard_bash (commits / publishes), the
close-message check (`_guard.py commit-message`) and the issue-draft publish check.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan, Project, bash_payload
from test_issue import FRONT, GOOD_DRAFT_BODY, Repo, _draft
from test_issue import repo as repo  # noqa: F401  (fixture re-export)

BLOCKED = 2
LEAF = "1.1.1"

# Real attribution: must stay blocked everywhere.
ATTRIBUTION = [
    "Co-Authored-By: Bot <b@x>",
    "Generated with Claude Code",
    "🤖 done",
    "Reviewed by Codex",
    "reviewed by codex",
    "Thanks Anthropic",
    "made with Claude Code",
    "Reviewed by Codex.",
    "Codex, thanks",
    "(Codex)",
    "Codex: notes",
    "see notes...Codex",
    "**Codex**",
    "line one\nCodex\nline three",
]

# Words and paths that merely contain a term: must no longer be blocked.
NOT_ATTRIBUTION = [
    "fix codexion parser",
    "see ~/.claude-codex-harness/lib",
    "update Claude Code-guide notes",
    "add anthropic-skills pack",
    "edit codex.md",
    "touch lib/codex/documenter.md",
    "Codex-reviewed change",
]


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


# ── guard_bash ───────────────────────────────────────────────────────────────


def _guard_bash(project: Project, cmd: str) -> tuple[int, str]:
    res = project.run("guard_bash.sh", bash_payload(cmd), env={"HARNESS_ALLOW_COMMIT": "1"})
    return res.returncode, res.stderr


@pytest.mark.e2e
@pytest.mark.parametrize("text", ATTRIBUTION)
def test_guard_bash_blocks_attribution_in_commit_and_pr(project: Project, text: str) -> None:
    q = _shell_quote(text)
    for cmd in (f"git commit -m {q}", f"gh pr create --title x --body {q}"):
        code, err = _guard_bash(project, cmd)
        assert code == BLOCKED, (cmd, err)
        assert "attribution" in err


@pytest.mark.parametrize(
    "cmd",
    [
        'git commit -m"Codex"',
        "gh pr create --title x --body=...Codex",
    ],
)
def test_guard_bash_blocks_glued_option_forms(project: Project, cmd: str) -> None:
    code, err = _guard_bash(project, cmd)
    assert code == BLOCKED, (cmd, err)
    assert "attribution" in err


@pytest.mark.e2e
@pytest.mark.parametrize("text", NOT_ATTRIBUTION)
def test_guard_bash_allows_words_containing_a_term(project: Project, text: str) -> None:
    q = _shell_quote(text)
    for cmd in (
        f"git commit -m {q}",
        f"gh pr create --title {q} --body ok",
        f"gh pr create --title ok --body {q}",
    ):
        code, err = _guard_bash(project, cmd)
        assert code == 0, (cmd, err)


@pytest.mark.parametrize(
    "cmd",
    [
        "cd ~/codexion && git commit -m 'fix: parser'",
        "cd ~/.claude-codex-harness && git commit -m 'fix: parser'",
        "git -C ~/.claude-codex-harness-wt/i11 commit -m 'fix: parser'",
    ],
)
def test_guard_bash_allows_paths_containing_codex(project: Project, cmd: str) -> None:
    code, err = _guard_bash(project, cmd)
    assert code == 0, (cmd, err)


# ── close message ────────────────────────────────────────────────────────────


def _close_message(plan: Plan, tmp_path: Path, body: str) -> tuple[int, str]:
    msg = tmp_path / "close.msg"
    msg.write_text(f"fix(core): close the leaf [{LEAF}]\n\n{body}\n")
    res = plan.run_py("_guard.py", "commit-message", str(msg), LEAF)
    return res.returncode, res.stdout + res.stderr


@pytest.mark.parametrize("text", ATTRIBUTION)
def test_close_message_rejects_attribution(plan: Plan, tmp_path: Path, text: str) -> None:
    code, out = _close_message(plan, tmp_path, text)
    assert code == 1
    assert "agent attribution" in out


@pytest.mark.parametrize("text", NOT_ATTRIBUTION)
def test_close_message_allows_words_containing_a_term(
    plan: Plan, tmp_path: Path, text: str
) -> None:
    code, out = _close_message(plan, tmp_path, text)
    assert code == 0, out


# ── issue draft ──────────────────────────────────────────────────────────────


def _publish(repo: Repo, extra: str) -> subprocess.CompletedProcess[str]:
    _draft(repo, FRONT + GOOD_DRAFT_BODY + f"\n{extra}\n")
    return repo.issue("publish")


@pytest.mark.parametrize("text", ATTRIBUTION)
def test_issue_draft_rejects_attribution(repo: Repo, text: str) -> None:  # noqa: F811
    res = _publish(repo, text)
    assert res.returncode == 1
    assert "agent attribution" in res.stdout + res.stderr
    assert "15" not in repo.gh_state()["issues"]


@pytest.mark.parametrize("text", NOT_ATTRIBUTION)
def test_issue_draft_allows_words_containing_a_term(repo: Repo, text: str) -> None:  # noqa: F811
    res = _publish(repo, text)
    assert res.returncode == 0, res.stdout + res.stderr
    assert text in repo.gh_state()["issues"]["15"]["body"]


# ── one pattern, one place ───────────────────────────────────────────────────


def test_pattern_is_defined_once() -> None:
    defined = [
        path.name
        for path in sorted((TOOLKIT / "lib").glob("*.py"))
        if re.search(r"^RE_ATTRIBUTION\s*=", path.read_text("utf-8"), re.M)
    ]
    assert defined == ["_attribution.py"]
