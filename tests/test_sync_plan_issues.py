"""sync_plan.py: the issues lane (docs/plan/issues/) -> feature_list.json `issues` + index."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_sync_plan import _features, _plan, _sync

HEADER_14 = """\
# ISS-14 Replace parse_config bit mask with invalid flags
- issue: https://github.com/acme/app/issues/14
- type: refactor
- origin: 2.3
- priority: next
- goal: Report invalid arguments with an array instead of a bit mask.

## Problem
The mask asks the reader to know bitwise operators.
"""

SPEC_14_1 = """\
# ISS-14.1 Parse config flags
- status: in_progress
- layer: core
- depends: 1.1.1
- files:
  - src/app/parse_config.py   # modifica: invalid[] out-parameter
- migrates:
  - tests/acceptance/test_1.1.1.py   # mask assertions become invalid[] checks
- acceptance:
  - AC1 | same accepted inputs | tests/acceptance/test_ISS-14.1.py::test_ac1_same

## Description
Structural refactor (no behaviour change).
"""

SPEC_14_2 = """\
# ISS-14.2 Print argument errors
- status: pending
- layer: interfaces
- depends: ISS-14.1
- files:
  - src/app/main.py
- acceptance:
  - AC1 | same error lines | tests/acceptance/test_ISS-14.2.py::test_ac1_lines
"""

ISSUE_DIR = "docs/plan/issues/ISS-14-parse-config-flags"


def _issue(**extra: str) -> dict[str, str]:
    files = {
        f"{ISSUE_DIR}.md": HEADER_14,
        f"{ISSUE_DIR}/ISS-14.1-parse-config-flags.md": SPEC_14_1,
        f"{ISSUE_DIR}/ISS-14.2-print-arg-errors.md": SPEC_14_2,
    }
    files.update(extra)
    return files


def test_a_plan_without_issues_keeps_its_feature_list_shape(tmp_path: Path) -> None:
    _plan(tmp_path)
    assert _sync(tmp_path).returncode == 0
    data = _features(tmp_path)
    assert "issues" not in data  # existing projects stay in sync after the upgrade
    assert "Issues" not in (tmp_path / "docs/plan/PLAN.md").read_text()


def test_issues_are_indexed_apart_from_phases(tmp_path: Path) -> None:
    _plan(tmp_path, **_issue())
    res = _sync(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 issue · 2 issue subtasks" in res.stdout
    data = _features(tmp_path)

    assert [t["id"] for t in data["features"]] == ["1.1", "1.2"]  # never mixed in
    issue = data["issues"][0]
    assert {k: v for k, v in issue.items() if k != "subtasks"} == {
        "id": "ISS-14",
        "number": 14,
        "title": "Replace parse_config bit mask with invalid flags",
        "issue": "https://github.com/acme/app/issues/14",
        "type": "refactor",
        "origin": "2.3",
        "priority": "next",
        "goal": "Report invalid arguments with an array instead of a bit mask.",
        "file": f"{ISSUE_DIR}.md",
        "status": "in_progress",
    }
    first, second = issue["subtasks"]
    assert first == {
        "id": "ISS-14.1",
        "title": "Parse config flags",
        "status": "in_progress",
        "layer": "core",
        "depends": ["1.1.1"],
        "files": ["src/app/parse_config.py"],
        "acceptance": [
            {
                "id": "AC1",
                "criterion": "same accepted inputs",
                "test": "tests/acceptance/test_ISS-14.1.py::test_ac1_same",
            }
        ],
        "spec": f"{ISSUE_DIR}/ISS-14.1-parse-config-flags.md",
        "migrates": ["tests/acceptance/test_1.1.1.py"],
    }
    assert second["depends"] == ["ISS-14.1"]
    assert "migrates" not in second  # only present when declared


def test_interrupts_is_kept_only_when_set(tmp_path: Path) -> None:
    header = HEADER_14.replace("- priority: next", "- priority: now\n- interrupts: feat/1.2-x")
    _plan(tmp_path, **_issue(**{f"{ISSUE_DIR}.md": header}))
    assert _sync(tmp_path).returncode == 0
    issue = _features(tmp_path)["issues"][0]
    assert issue["priority"] == "now"
    assert issue["interrupts"] == "feat/1.2-x"


def test_a_header_without_specs_is_an_issue_being_planned(tmp_path: Path) -> None:
    _plan(tmp_path, **{f"{ISSUE_DIR}.md": HEADER_14})
    assert _sync(tmp_path).returncode == 0
    issue = _features(tmp_path)["issues"][0]
    assert issue["subtasks"] == []
    assert issue["status"] == "pending"
    assert (
        "**ISS-14 Replace parse_config bit mask with invalid flags** · planning"
        in (tmp_path / "docs/plan/PLAN.md").read_text()
    )


def test_issues_are_sorted_by_number(tmp_path: Path) -> None:
    header_9 = HEADER_14.replace("ISS-14", "ISS-9").replace("issues/14", "issues/9")
    _plan(tmp_path, **_issue(**{"docs/plan/issues/ISS-9-other.md": header_9}))
    assert _sync(tmp_path).returncode == 0
    assert [i["id"] for i in _features(tmp_path)["issues"]] == ["ISS-9", "ISS-14"]


def test_plan_md_index_has_an_issues_block(tmp_path: Path) -> None:
    _plan(tmp_path, **_issue())
    assert _sync(tmp_path).returncode == 0
    text = (tmp_path / "docs/plan/PLAN.md").read_text()
    for expected in (
        "### Issues · 0/2 done",
        "- **ISS-14 Replace parse_config bit mask with invalid flags** — in progress"
        " · refactor · priority next",
        "  - [~] [ISS-14.1 Parse config flags]"
        "(issues/ISS-14-parse-config-flags/ISS-14.1-parse-config-flags.md) — in progress",
        "  - [ ] [ISS-14.2 Print argument errors]"
        "(issues/ISS-14-parse-config-flags/ISS-14.2-print-arg-errors.md)",
    ):
        assert expected in text, expected
    # the phase counters do not include issue subtasks
    assert "1 of 3 subtasks done" in text


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        (
            {f"{ISSUE_DIR}.md": HEADER_14.replace("type: refactor", "type: chore")},
            "ISS-14: invalid type 'chore' (fix | refactor | feat | docs)",
        ),
        (
            {f"{ISSUE_DIR}.md": HEADER_14.replace("priority: next", "priority: soon")},
            "ISS-14: invalid priority 'soon' (now | next | backlog)",
        ),
        (
            {f"{ISSUE_DIR}.md": HEADER_14.replace("# ISS-14 ", "# ISS-15 ")},
            "ISS-14-parse-config-flags.md: heading id ISS-15 does not match the file name",
        ),
        (
            {f"{ISSUE_DIR}/ISS-15.1-x.md": "# ISS-15.1 X\n- status: pending\n"},
            "ISS-15.1: spec outside its issue folder (ISS-14-parse-config-flags/)",
        ),
        (
            {f"{ISSUE_DIR}/1.1.9-x.md": "# 1.1.9 X\n- status: pending\n"},
            "ISS-14-parse-config-flags/1.1.9-x.md: not a <ISS-n.k>-<slug>.md spec",
        ),
        (
            {"docs/plan/issues/ISS-20-x/ISS-20.1-y.md": "# ISS-20.1 Y\n- status: pending\n"},
            "issues/ISS-20-x/: spec folder without docs/plan/issues/ISS-20-x.md",
        ),
        (
            {
                f"{ISSUE_DIR}/ISS-14.3-x.md": (
                    "# ISS-14.3 X\n- status: pending\n- depends: ISS-14.9\n"
                )
            },
            "ISS-14.3: depends on unknown subtask ISS-14.9",
        ),
        (
            {
                f"{ISSUE_DIR}/ISS-14.3-x.md": (
                    "# ISS-14.3 X\n- status: pending\n- migrates:\n  - src/app/x.py\n"
                )
            },
            "ISS-14.3: migrates 'src/app/x.py' is not an acceptance test path",
        ),
        (
            {
                "docs/plan/issues/ISS-30-dup.md": HEADER_14.replace("ISS-14", "ISS-30"),
                "docs/plan/issues/ISS-30-dup2.md": HEADER_14.replace("ISS-14", "ISS-30"),
            },
            "ISS-30: duplicate issue id",
        ),
    ],
)
def test_issue_structural_errors_fail_without_writing(
    tmp_path: Path, extra: dict[str, str], error: str
) -> None:
    _plan(tmp_path, **_issue(**extra))
    res = _sync(tmp_path)
    assert res.returncode == 1
    assert error in res.stdout + res.stderr
    assert not (tmp_path / "feature_list.json").exists()
