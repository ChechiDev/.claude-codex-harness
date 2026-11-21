"""I16 (issue #81): validate() warns when a subtask's `files:` is empty or has more than 3."""

from __future__ import annotations

import json

import pytest
from conftest import TOOLKIT, Plan

MARKER = "Test-only verification (no production behaviour change)"


def _ready(plan: Plan, status: str = "pending") -> Plan:
    return plan.phase("01", "1.1").sub("1.1.1", status).approve_roadmap().approve_phase("01").sync()


def _set_files(plan: Plan, files: list[str]) -> None:
    path = plan.root / "feature_list.json"
    data = json.loads(path.read_text())
    data["features"][0]["subtasks"][0]["files"] = files
    path.write_text(json.dumps(data))


def _files(n: int) -> list[str]:
    return [f"src/app/m{i}.py" for i in range(n)]


@pytest.mark.parametrize("count", [0, 4])
def test_empty_or_too_many_files_warns_with_id_and_count(plan: Plan, count: int) -> None:
    _set_files(_ready(plan), _files(count))
    res = plan.validate()
    assert res.returncode == 0, res.stdout
    assert "WARN" in res.stdout
    assert "1.1.1" in res.stdout and f"{count} files" in res.stdout


@pytest.mark.parametrize("count", [1, 2, 3])
def test_one_to_three_files_is_silent(plan: Plan, count: int) -> None:
    _set_files(_ready(plan), _files(count))
    res = plan.validate()
    assert res.returncode == 0, res.stdout
    assert "WARN" not in res.stdout


@pytest.mark.parametrize("status", ["done", "baseline"])
def test_done_and_baseline_are_exempt(plan: Plan, status: str) -> None:
    plan.phase("01", "1.1").sub("1.1.1", status).approve_roadmap().approve_phase("01").sync()
    _set_files(plan, _files(5))
    res = plan.validate()
    assert "files:" not in res.stdout


def test_warning_does_not_change_a_failing_exit_code(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()  # phase not approved
    plan.sub("1.1.1", "in_progress").sync()
    _set_files(plan, _files(4))
    res = plan.validate()
    assert res.returncode == 1
    assert "WARN" in res.stdout and "FAIL" in res.stdout


def test_test_only_marker_allows_empty_files(plan: Plan) -> None:
    _ready(plan)
    spec = next((plan.root / "docs" / "plan").glob("phase-01-*/1.1.1-*.md"))
    spec.write_text(spec.read_text() + f"\n{MARKER}\n")
    _set_files(plan, [])
    res = plan.validate()
    assert "WARN" not in res.stdout, res.stdout


def test_test_only_marker_does_not_allow_too_many(plan: Plan) -> None:
    _ready(plan)
    spec = next((plan.root / "docs" / "plan").glob("phase-01-*/1.1.1-*.md"))
    spec.write_text(spec.read_text() + f"\n{MARKER}\n")
    _set_files(plan, _files(4))
    assert "4 files" in plan.validate().stdout


def test_unchanged_legacy_leaf_is_exempt(
    plan: Plan, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_files(_ready(plan), _files(4))
    monkeypatch.chdir(plan.root)
    monkeypatch.syspath_prepend(str(TOOLKIT / "lib"))
    import _harness
    import _legacy

    monkeypatch.setattr(_legacy, "is_legacy_unchanged", lambda root, item: item == "1.1.1")
    assert _harness.files_limit_warnings(_harness.leaves()) == []
    monkeypatch.setattr(_legacy, "is_legacy_unchanged", lambda root, item: False)
    assert len(_harness.files_limit_warnings(_harness.leaves())) == 1
