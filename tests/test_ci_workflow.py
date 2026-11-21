"""Contract test for the toolkit CI workflow (#74).

The workflow is checked as text (no YAML dependency): the fast tier, ruff and mypy
run on pull_request/push, the e2e tier only on schedule/workflow_dispatch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _text() -> str:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _top_level_block(text: str, key: str) -> str:
    match = re.search(rf"^{key}:\n((?:[ \t].*\n|\n)*)", text, re.MULTILINE)
    assert match, f"no top-level {key!r} block"
    return match.group(1)


def _jobs(text: str) -> dict[str, str]:
    block = _top_level_block(text, "jobs")
    parts = re.split(r"^  ([A-Za-z0-9_-]+):\n", block, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _job_running(jobs: dict[str, str], needle: str) -> str:
    found = [body for body in jobs.values() if needle in body]
    assert len(found) == 1, f"expected exactly one job running {needle!r}, got {len(found)}"
    return found[0]


FAST_IF = "github.event_name != 'schedule' && github.event_name != 'workflow_dispatch'"
E2E_IF = "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"


def _job_if(body: str) -> str:
    cond = re.search(r"^    if:\s*(.+)$", body, re.MULTILINE)
    assert cond, "job needs an if: guard"
    return cond.group(1).strip()


def test_triggers_cover_pr_push_schedule_and_dispatch() -> None:
    on = _top_level_block(_text(), "on")
    for trigger in ("pull_request", "push", "schedule", "workflow_dispatch"):
        assert re.search(rf"^  {trigger}:", on, re.MULTILINE), trigger
    assert re.search(r"branches:\s*\[\s*main\s*\]", on)
    assert re.search(r"cron:", on)


def test_permissions_are_read_only() -> None:
    perms = _top_level_block(_text(), "permissions")
    assert perms.strip() == "contents: read"


def test_concurrency_cancels_superseded_runs() -> None:
    conc = _top_level_block(_text(), "concurrency")
    assert "group:" in conc
    # a push to main must not cancel the nightly/manual e2e run on the same ref
    assert "github.event_name" in conc
    assert "cancel-in-progress: true" in conc


def test_no_secrets_used() -> None:
    assert "secrets." not in _text()


def test_actions_are_pinned_to_a_commit_sha() -> None:
    uses = re.findall(r"uses:\s*(\S+)", _text())
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


def test_fast_job_runs_checks_on_pr_and_push_only() -> None:
    fast = _job_running(_jobs(_text()), "ruff check")
    assert "uv sync" in fast
    assert re.search(r"uv run pytest -n auto(?!.*-m e2e)", fast)
    assert "-m e2e" not in fast
    assert "uv run ruff check ." in fast
    assert "uv run ruff format --check ." in fast
    assert "uv run mypy" in fast
    assert _job_if(fast) == FAST_IF


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_e2e_job_runs_only_on_schedule_or_dispatch(event: str) -> None:
    e2e = _job_running(_jobs(_text()), "make test-e2e")
    assert _job_if(e2e) == E2E_IF
    assert f"github.event_name == '{event}'" in E2E_IF
    assert "-rs" in e2e or "PYTEST_ADDOPTS" in e2e, "e2e must report skip reasons"
