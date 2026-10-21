"""sync_plan.py: docs/plan (phases + subtask specs) -> feature_list.json + PLAN.md index."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

PLAN_MD = """\
# Project plan

## Vision
A user registry.

<!-- plan:index:start -->
stale
<!-- plan:index:end -->

## Notes
kept as written
"""

PHASE_01 = """\
# Phase 01 — Users
- goal: Users register with a validated email.

## 1.1 User entity
- layer: domain
- goal: User value object with a validated email.

## 1.2 Persistence
- layer: infrastructure
- goal: Store users.
"""

PHASE_02 = """\
# Phase 02 — API
- goal: Expose users over HTTP.
"""

SPEC_111 = """\
# 1.1.1 Email value object
- status: done
- layer: domain
- depends: none
- files:
  - src/app/domain/email.py   # crea: Email VO

  - src/app/domain/__init__.py # modifica: export
- acceptance:
  - AC1 | rejects an address without '@' | tests/acceptance/test_1.1.1.py::test_ac1_no_at
  - AC2 | lowercases the domain | tests/acceptance/test_1.1.1.py::test_ac2_lower

## Description
Immutable value object.
- status: this line is body text, not metadata
"""

SPEC_112 = """\
# 1.1.2 User entity
- status: In Progress
- layer: domain
- depends: 1.1.1
- files:
  - src/app/domain/user.py
- acceptance:
  - AC1 | a user has an email | tests/acceptance/test_1.1.2.py::test_ac1_email
"""

SPEC_121 = """\
# 1.2.1 User repository
- status: pending
- layer: infrastructure
- depends: 1.1.2
- files:
  - src/app/infra/repo.py
- acceptance:
  - AC1 | saves a user | tests/acceptance/test_1.2.1.py::test_ac1_save
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _plan(root: Path, **extra: str) -> None:
    files = {
        "docs/plan/PLAN.md": PLAN_MD,
        "docs/plan/phase-01-users.md": PHASE_01,
        "docs/plan/phase-01-users/1.1.1-email-value-object.md": SPEC_111,
        "docs/plan/phase-01-users/1.1.2-user-entity.md": SPEC_112,
        "docs/plan/phase-01-users/1.2.1-user-repository.md": SPEC_121,
        "docs/plan/phase-02-api.md": PHASE_02,
    }
    files.update(extra)
    _write(root, files)


def _sync(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(TOOLKIT / "lib" / "sync_plan.py"), *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def _features(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / "feature_list.json").read_text())
    return data


def test_phases_tasks_and_specs_are_indexed(tmp_path: Path) -> None:
    _plan(tmp_path)
    res = _sync(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "2 phases · 2 tasks · 3 subtasks" in res.stdout
    data = _features(tmp_path)

    assert data["phases"] == [
        {
            "id": "01",
            "title": "Users",
            "goal": "Users register with a validated email.",
            "file": "docs/plan/phase-01-users.md",
            "status": "in_progress",
            "tasks": ["1.1", "1.2"],
        },
        {
            "id": "02",
            "title": "API",
            "goal": "Expose users over HTTP.",
            "file": "docs/plan/phase-02-api.md",
            "status": "pending",
            "tasks": [],
        },
    ]
    task = data["features"][0]
    assert {k: task[k] for k in ("id", "title", "phase", "layer", "goal", "status")} == {
        "id": "1.1",
        "title": "User entity",
        "phase": "01",
        "layer": "domain",
        "goal": "User value object with a validated email.",
        "status": "in_progress",
    }
    assert task["subtasks"][0] == {
        "id": "1.1.1",
        "title": "Email value object",
        "status": "done",
        "layer": "domain",
        "depends": [],
        "files": ["src/app/domain/email.py", "src/app/domain/__init__.py"],
        "acceptance": [
            {
                "id": "AC1",
                "criterion": "rejects an address without '@'",
                "test": "tests/acceptance/test_1.1.1.py::test_ac1_no_at",
            },
            {
                "id": "AC2",
                "criterion": "lowercases the domain",
                "test": "tests/acceptance/test_1.1.1.py::test_ac2_lower",
            },
        ],
        "spec": "docs/plan/phase-01-users/1.1.1-email-value-object.md",
    }
    assert task["subtasks"][1]["status"] == "in_progress"  # "In Progress" normalized
    assert task["subtasks"][1]["depends"] == ["1.1.1"]
    assert data["features"][1]["status"] == "pending"


def test_plan_md_gets_a_generated_index_and_keeps_the_rest(tmp_path: Path) -> None:
    _plan(tmp_path)
    assert _sync(tmp_path).returncode == 0
    text = (tmp_path / "docs/plan/PLAN.md").read_text()
    assert text.startswith("# Project plan\n\n## Vision\nA user registry.\n")
    assert text.endswith("<!-- plan:index:end -->\n\n## Notes\nkept as written\n")
    assert "stale" not in text
    for expected in (
        "1 of 3 subtasks done · 1 in progress · 1 pending · 0 blocked",
        "### Phase 01 — Users · 1/3 done",
        "### Phase 02 — API · not broken down yet",
        "- **1.1 User entity** — in progress",
        "  - [x] [1.1.1 Email value object](phase-01-users/1.1.1-email-value-object.md)"
        " · [plan review](../../progress/plan_review_1.1.1.md)"
        " · [code review](../../progress/review_1.1.1.md)"
        " · [reference](../reference/1.1.1-email-value-object.md)",
        "  - [~] [1.1.2 User entity](phase-01-users/1.1.2-user-entity.md) — in progress",
        "  - [ ] [1.2.1 User repository](phase-01-users/1.2.1-user-repository.md)",
    ):
        assert expected in text, expected


def test_index_is_appended_when_plan_md_has_no_markers(tmp_path: Path) -> None:
    _plan(tmp_path, **{"docs/plan/PLAN.md": "# Project plan\n"})
    assert _sync(tmp_path).returncode == 0
    text = (tmp_path / "docs/plan/PLAN.md").read_text()
    assert text.startswith("# Project plan\n\n<!-- plan:index:start -->\n")
    assert text.endswith("<!-- plan:index:end -->\n")


def test_check_passes_after_sync_and_flags_stale_outputs(tmp_path: Path) -> None:
    _plan(tmp_path)
    assert _sync(tmp_path).returncode == 0
    assert _sync(tmp_path, "--check").returncode == 0

    spec = tmp_path / "docs/plan/phase-01-users/1.2.1-user-repository.md"
    spec.write_text(SPEC_121.replace("status: pending", "status: blocked"))
    res = _sync(tmp_path, "--check")
    assert res.returncode == 1
    assert "feature_list.json is stale" in res.stdout + res.stderr
    assert "PLAN.md index is stale" in res.stdout + res.stderr


def test_check_does_not_write(tmp_path: Path) -> None:
    _plan(tmp_path)
    before = (tmp_path / "docs/plan/PLAN.md").read_text()
    _sync(tmp_path, "--check")
    assert (tmp_path / "docs/plan/PLAN.md").read_text() == before
    assert not (tmp_path / "feature_list.json").exists()


def test_an_empty_plan_is_valid(tmp_path: Path) -> None:
    _write(tmp_path, {"docs/plan/PLAN.md": "# Project plan\n"})
    res = _sync(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _features(tmp_path)["phases"] == []
    assert _features(tmp_path)["features"] == []
    assert "No phases planned yet" in (tmp_path / "docs/plan/PLAN.md").read_text()


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        (
            {"docs/plan/phase-01-users/1.3.1-orphan.md": "# 1.3.1 Orphan\n- status: pending\n"},
            "1.3.1: task 1.3 does not exist in phase 01",
        ),
        (
            {"docs/plan/phase-01-users/1.1.9-x.md": "# 1.1.8 Mismatch\n- status: pending\n"},
            "1.1.9-x.md: heading id 1.1.8 does not match the file name",
        ),
        (
            {"docs/plan/phase-01-users/1.1.3-x.md": "# 1.1.3 Bad\n- status: started\n"},
            "1.1.3: invalid status 'started'",
        ),
        (
            {
                "docs/plan/phase-01-users/1.1.3-x.md": (
                    "# 1.1.3 X\n- status: pending\n- depends: 9.9.9\n"
                )
            },
            "1.1.3: depends on unknown subtask 9.9.9",
        ),
        (
            {"docs/plan/phase-01-users.md": PHASE_01.replace("## 1.2 ", "## 2.2 ")},
            "task 2.2 belongs to phase 02, not 01",
        ),
        (
            {"docs/plan/phase-03-x/3.1.1-y.md": "# 3.1.1 Y\n- status: pending\n"},
            "phase-03-x/: spec folder without docs/plan/phase-03-x.md",
        ),
        (
            {"docs/plan/phase-02-api.md": "# Phase 05 — API\n- goal: x\n"},
            "phase-02-api.md: heading says phase 05",
        ),
    ],
)
def test_structural_errors_fail_without_writing(
    tmp_path: Path, extra: dict[str, str], error: str
) -> None:
    _plan(tmp_path, **extra)
    res = _sync(tmp_path)
    assert res.returncode == 1
    assert error in res.stdout + res.stderr
    assert not (tmp_path / "feature_list.json").exists()


def test_missing_plan_dir_fails(tmp_path: Path) -> None:
    res = _sync(tmp_path)
    assert res.returncode == 1
    assert "docs/plan does not exist" in res.stdout + res.stderr
