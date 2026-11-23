"""Contract test for the toolkit CI workflow (#74).

The workflow is checked as text (no YAML dependency): the fast tier, ruff and mypy
run on pull_request/push/merge_group; the e2e tier (#117) runs on every pull_request and
merge_group (and nightly / on demand) as 8 pytest-split shards plus one aggregate job
that fails unless every shard ran green and together they ran the full e2e collection.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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
E2E_IF = "github.event_name != 'push'"
AGGREGATE_IF = "always() && github.event_name != 'push'"
SHARDS = 8
DURATIONS = ROOT / "tests" / ".test_durations"


def _job_if(body: str) -> str:
    cond = re.search(r"^    if:\s*(.+)$", body, re.MULTILINE)
    assert cond, "job needs an if: guard"
    return cond.group(1).strip()


def test_triggers_cover_pr_push_schedule_and_dispatch() -> None:
    on = _top_level_block(_text(), "on")
    for trigger in ("pull_request", "merge_group", "push", "schedule", "workflow_dispatch"):
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


def _shard_job() -> str:
    return _job_running(_jobs(_text()), "--splits")


def _aggregate_job() -> str:
    return _job_running(_jobs(_text()), "check-shards")


def test_e2e_shards_run_on_prs_merge_group_schedule_and_dispatch() -> None:
    shard = _shard_job()
    assert _job_if(shard) == E2E_IF  # only a push to main skips them (already merged)
    assert "github.event_name != 'push'" in E2E_IF


def test_e2e_shards_split_the_whole_e2e_tier() -> None:
    shard = _shard_job()
    shards = re.search(r"shard:\s*\[([0-9,\s]+)\]", shard)
    assert shards, "the shard job needs a shard matrix"
    assert [int(n) for n in shards.group(1).split(",")] == list(range(1, SHARDS + 1))
    assert "fail-fast: false" in shard
    run = re.search(r"uv run python tests/tools/pytest_timed\.py [^\n]*(?:\n {10}[^\n]*)*", shard)
    assert run and "--splits" in run.group(0), "the shard step runs pytest with --splits"
    cmd = " ".join(run.group(0).split())
    for needle in (
        "-o addopts=",
        "-m e2e",
        "-n 2",
        f"--splits {SHARDS}",
        "--group ${{ matrix.shard }}",
        # contiguous chunks keep a module (and its module-scoped install) in one shard
        "--splitting-algorithm=duration_based_chunks",
        "--durations-path=tests/.test_durations",
        "--timing-dir=",
        "--junitxml=",
    ):
        assert needle in cmd, needle
    assert "-rs" in shard, "e2e must report skip reasons"
    # an exported PYTHONPATH reaches every subprocess the tests start (run_check refuses it)
    assert "PYTHONPATH" not in shard


def test_e2e_shards_warm_the_uv_cache_before_the_tests() -> None:
    # the throwaway projects sync offline (tests/i0_projects.py): an install.sh run with
    # the network on fills the uv cache first, or every installed-project test errors
    shard = _shard_job()
    assert "warm_uv_cache.py" in shard
    assert shard.index("warm_uv_cache.py") < shard.index("--splits")


def test_e2e_shards_warm_the_cache_in_the_tests_own_environment() -> None:
    # isolated_env drops every UV_* variable (setup-uv's UV_PYTHON included), so a bare
    # install.sh in the workflow env resolves wheels for another interpreter than the
    # tests (#123: cp312 warmed, cp314 wanted). The warm runs under `uv run` (same PATH
    # as pytest) through the tool that reuses isolated_env.
    shard = _shard_job()
    assert re.search(r"run:\s*uv run python tests/tools/warm_uv_cache\.py", shard)
    assert "bash install.sh" not in shard


def test_e2e_shards_upload_their_records_even_on_failure() -> None:
    shard = _shard_job()
    upload = shard[shard.index("actions/upload-artifact") :]
    assert re.search(r"if:\s*always\(\)", shard[: shard.index("actions/upload-artifact")])
    assert "e2e-shard-${{ matrix.shard }}" in upload


def test_aggregate_fails_unless_every_shard_ran_green_and_complete() -> None:
    agg = _aggregate_job()
    assert _job_if(agg) == AGGREGATE_IF  # runs when a shard failed or was cancelled
    assert re.search(r"needs:\s*\[?\s*e2e-shard", agg)
    assert "needs.e2e-shard.result" in agg
    assert re.search(r"!= *\"?success", agg) or "= success" in agg
    assert f"--expected {SHARDS}" in agg
    assert "--collect-only" in agg and "-m e2e" in agg and "-o addopts=" in agg
    assert "actions/download-artifact" in agg
    # a skipped e2e test counts as not run, except the Docker compat tests (no image on
    # the runner): each allowed skip names its test files and its reason
    allowed = re.findall(r"--allow-skip\s+'([^']+)'", agg)
    assert allowed, "the aggregate lists the skips it accepts"
    for pattern in allowed:
        assert pattern.startswith("^tests/test_compat_"), pattern
        assert "Docker Engine" in pattern, pattern


def test_required_check_names_are_stable() -> None:
    names = re.findall(r"^    name:\s*(.+)$", _top_level_block(_text(), "jobs"), re.MULTILINE)
    assert "fast tier, ruff, mypy" in names
    assert "e2e tier" in names  # the aggregate: the one e2e check branch protection requires


def test_setup_uv_cache_is_enabled_everywhere() -> None:
    text = _text()
    assert text.count("astral-sh/setup-uv@") == text.count("enable-cache: true")


def test_durations_file_is_valid() -> None:
    data = json.loads(DURATIONS.read_text())
    assert isinstance(data, dict) and data
    assert all(isinstance(k, str) and "::" in k for k in data)
    assert all(isinstance(v, int | float) and v >= 0 for v in data.values())
    assert any(k.startswith("tests/test_i0_") for k in data)
