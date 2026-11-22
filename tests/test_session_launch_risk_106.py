"""Issue #106: the session launcher also escalates on the unit's spec `- risk: high`,
and the spec format documents `- risk:` / `- surfaces:` (follow-up of #44).

- `risk: high` in a unit's spec (declared, or sealed by its phase/task/issue gate)
  picks high_risk_model/high_risk_effort, the same as a high_risk_paths match;
- `low`/`medium`, or no rating, keep the default model;
- an invalid rating fails closed (the launcher prints no command).
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT, Plan

LAUNCHER = TOOLKIT / "lib" / "session_launch.sh"
SPEC_DIR = "docs/plan/phase-01-p"
ISSUE_DIR = "docs/plan/issues/ISS-7-fix"


def _spec(root: Path, rel: str, sid: str, rating: str, tail: str = "") -> str:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    body = f"# {sid} s\n- status: pending\n- layer: domain\n{rating}- files:\n  - src/app/m.py\n"
    (root / rel).write_text(body + tail, encoding="utf-8")
    return rel


def _project(
    root: Path, first: str = "", second: str = "", issue: str = "", tail: str = ""
) -> None:
    """Task 1.1 (subtasks 1.1.1 and 1.1.2) and issue ISS-7 (subtask ISS-7.1); no file
    matches any high_risk_paths, so only the specs' ratings can escalate."""

    def sub(sid: str, spec: str) -> dict[str, Any]:
        return {
            "id": sid,
            "title": f"S {sid}",
            "status": "pending",
            "files": ["src/app/m.py"],
            "spec": spec,
        }

    s1 = _spec(root, f"{SPEC_DIR}/1.1.1-a.md", "1.1.1", first, tail)
    s2 = _spec(root, f"{SPEC_DIR}/1.1.2-b.md", "1.1.2", second)
    s3 = _spec(root, f"{ISSUE_DIR}/ISS-7.1-c.md", "ISS-7.1", issue)
    data = {
        "phases": [{"id": "01", "title": "p", "status": "pending"}],
        "features": [
            {
                "id": "1.1",
                "phase": "01",
                "title": "t",
                "subtasks": [sub("1.1.1", s1), sub("1.1.2", s2)],
            }
        ],
        "issues": [
            {
                "id": "ISS-7",
                "title": "i",
                "type": "fix",
                "issue": "",
                "subtasks": [sub("ISS-7.1", s3)],
            }
        ],
    }
    (root / "feature_list.json").write_text(json.dumps(data), encoding="utf-8")


def _launch(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _model(res: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    assert res.returncode == 0, res.stderr
    argv = shlex.split(res.stdout.strip())
    return argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]


HIGH = "- risk: high\n"


# ── the spec's risk escalates the session ───────────────────────────────────


def test_a_subtask_whose_spec_is_high_risk_escalates(tmp_path: Path) -> None:
    _project(tmp_path, first=HIGH)
    res = _launch(tmp_path, "1.1.1")
    assert _model(res) == ("opus", "high")
    assert "risk high" in res.stderr
    assert f"{SPEC_DIR}/1.1.1-a.md" in res.stderr


@pytest.mark.parametrize(
    "rating", ["", "- risk: low\n", "- risk: medium\n", "- surfaces: security\n"]
)
def test_a_spec_not_rated_high_keeps_the_default(tmp_path: Path, rating: str) -> None:
    _project(tmp_path, first=rating)
    res = _launch(tmp_path, "1.1.1")
    assert _model(res) == ("sonnet", "medium")
    assert "(default)" in res.stderr


def test_a_task_escalates_when_any_subtask_spec_is_high_risk(tmp_path: Path) -> None:
    _project(tmp_path, second=HIGH)
    assert _model(_launch(tmp_path, "1.1")) == ("opus", "high")
    assert _model(_launch(tmp_path, "1.1.1")) == ("sonnet", "medium")


def test_an_issue_and_its_subtask_escalate_on_the_spec_risk(tmp_path: Path) -> None:
    _project(tmp_path, issue=HIGH)
    assert _model(_launch(tmp_path, "ISS-7.1")) == ("opus", "high")
    assert _model(_launch(tmp_path, "ISS-7")) == ("opus", "high")
    assert _model(_launch(tmp_path, "1.1")) == ("sonnet", "medium")


def test_the_spec_risk_uses_the_configured_high_risk_model(tmp_path: Path) -> None:
    _project(tmp_path, first=HIGH)
    toml = '[sessions]\nhigh_risk_model = "fable"\nhigh_risk_effort = "xhigh"\n'
    (tmp_path / "harness.toml").write_text(toml, encoding="utf-8")
    assert _model(_launch(tmp_path, "1.1.1")) == ("fable", "xhigh")


def test_a_risk_line_after_the_first_section_is_not_a_rating(tmp_path: Path) -> None:
    _project(tmp_path, tail="\n## Description\n- risk: high\n")
    assert _model(_launch(tmp_path, "1.1.1")) == ("sonnet", "medium")


def test_the_case_of_the_key_does_not_hide_the_rating(tmp_path: Path) -> None:
    _project(tmp_path, first="- Risk: high\n")
    assert _model(_launch(tmp_path, "1.1.1")) == ("opus", "high")


def test_a_high_risk_paths_match_still_escalates_and_is_named(tmp_path: Path) -> None:
    _project(tmp_path, first=HIGH)
    toml = '[sessions]\nhigh_risk_paths = ["src/app/**"]\n'
    (tmp_path / "harness.toml").write_text(toml, encoding="utf-8")
    res = _launch(tmp_path, "1.1.1")
    assert _model(res) == ("opus", "high")
    assert "src/app/m.py matches src/app/**" in res.stderr


def test_the_sealed_rating_is_a_floor(tmp_path: Path) -> None:
    # The task gate sealed `risk: high`; the spec lowered it afterwards (make check
    # fails until the gate re-seals): the session still gets the high-risk model.
    _project(tmp_path, first="- risk: low\n")
    spec = f"{SPEC_DIR}/1.1.1-a.md"
    seal = tmp_path / "progress" / "plan_task_1.1.sha256"
    seal.parent.mkdir()
    seal.write_text(f"{'0' * 64}  {spec}\nhigh;-  rating:{spec}\n", encoding="utf-8")
    res = _launch(tmp_path, "1.1.1")
    assert _model(res) == ("opus", "high")
    assert "sealed" in res.stderr


@pytest.mark.parametrize(
    "rating", ["- risk: critical\n", "- risk: high\n- risk: low\n", "- surfaces: Bad Name\n"]
)
def test_an_invalid_rating_fails_closed(tmp_path: Path, rating: str) -> None:
    _project(tmp_path, first=rating)
    for target in ("1.1.1", "1.1"):
        res = _launch(tmp_path, target)
        assert res.returncode == 1, res.stdout
        assert res.stdout == ""
        assert "[FAIL]" in res.stderr
        assert "1.1.1-a.md" in res.stderr


def test_an_unreadable_sealed_rating_fails_closed(tmp_path: Path) -> None:
    _project(tmp_path)
    spec = f"{SPEC_DIR}/1.1.1-a.md"
    seal = tmp_path / "progress" / "plan_task_1.1.sha256"
    seal.parent.mkdir()
    seal.write_text(f"{'0' * 64}  {spec}\nextreme  rating:{spec}\n", encoding="utf-8")
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode == 1
    assert res.stdout == ""


def test_a_missing_spec_file_keeps_the_default(tmp_path: Path) -> None:
    _project(tmp_path, first=HIGH)
    (tmp_path / SPEC_DIR / "1.1.2-b.md").unlink()
    assert _model(_launch(tmp_path, "1.1.2")) == ("sonnet", "medium")


# ── the spec format documents the fields ────────────────────────────────────


def _read(rel: str) -> str:
    return (TOOLKIT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "rel",
    ["lib/sync_plan.py", "templates/docs/plan/PLAN.md", "templates-c/docs/plan/PLAN.md"],
)
def test_the_spec_format_documents_risk_and_surfaces(rel: str) -> None:
    text = _read(rel)
    assert "- risk: " in text and "low | medium | high" in text
    assert "- surfaces: " in text
    assert "[review.surfaces." in text
    assert "high_risk_model" in text


def test_both_plan_templates_document_the_fields_identically() -> None:
    def section(rel: str) -> list[str]:
        return [
            line
            for line in _read(rel).splitlines()
            if re.search(r"risk:|risk`|surfaces|high_risk", line)
        ]

    py = section("templates/docs/plan/PLAN.md")
    assert py and py == section("templates-c/docs/plan/PLAN.md")


def test_the_leader_rates_specs_and_launches_on_the_spec_risk() -> None:
    text = _read("agents/leader.md")
    assert "- risk:" in text and "- surfaces:" in text
    assert "`risk: high`" in text
    assert "high_risk_paths" in text


def test_a_malformed_seal_fails_closed_without_a_traceback(tmp_path: Path) -> None:
    _project(tmp_path)
    seal = tmp_path / "progress" / "plan_task_1.1.sha256"
    seal.parent.mkdir()
    seal.write_text("onlyonetoken\n", encoding="utf-8")
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode == 1
    assert res.stdout == ""
    assert "Traceback" not in res.stderr
    assert "[FAIL]" in res.stderr


# ── through the real plan pipeline (sync_plan + the phase seal) ─────────────


def _rate(plan: Plan, rel: str, old: str, new: str) -> None:
    path = plan.root / rel
    path.write_text(path.read_text().replace(old, new, 1))
    assert plan.run_py("sync_plan.py").returncode == 0


def test_a_rating_written_by_the_plan_pipeline_escalates_and_its_seal_is_a_floor(
    plan: Plan,
) -> None:
    spec = "docs/plan/phase-01-p01/1.1.1-s.md"
    plan.phase("01", "1.1").sub("1.1.1").sub("1.1.2").approve_roadmap().sync()
    _rate(plan, spec, "- layer: domain\n", "- layer: domain\n- risk: high\n")
    assert _model(_launch(plan.root, "1.1.1")) == ("opus", "high")
    assert _model(_launch(plan.root, "1.1.2")) == ("sonnet", "medium")
    plan.approve_phase("01")
    res = plan.run_py("_guard.py", "seal", "phase", "01")
    assert res.returncode == 0, res.stdout + res.stderr
    _rate(plan, spec, "- risk: high\n", "- risk: low\n")  # lowered after the seal
    res = _launch(plan.root, "1.1")
    assert _model(res) == ("opus", "high")
    assert "sealed" in res.stderr


# ── every rating is checked before any escalation (Codex R1) ────────────────


def _fails_closed(res: subprocess.CompletedProcess[str]) -> None:
    assert res.returncode == 1, res.stdout
    assert res.stdout == ""
    assert "[FAIL]" in res.stderr


def test_an_invalid_rating_after_a_high_one_still_fails_closed(tmp_path: Path) -> None:
    _project(tmp_path, first=HIGH, second="- risk: critical\n")
    _fails_closed(_launch(tmp_path, "1.1"))


def test_a_high_risk_paths_match_does_not_skip_the_rating_check(tmp_path: Path) -> None:
    _project(tmp_path, first="- risk: critical\n")
    toml = '[sessions]\nhigh_risk_paths = ["src/app/**"]\n'
    (tmp_path / "harness.toml").write_text(toml, encoding="utf-8")
    _fails_closed(_launch(tmp_path, "1.1.1"))


def test_an_unknown_surface_fails_closed(tmp_path: Path) -> None:
    _project(tmp_path, first="- surfaces: securty\n")
    res = _launch(tmp_path, "1.1.1")
    _fails_closed(res)
    assert "securty" in res.stderr


def test_a_surface_added_in_harness_toml_is_known(tmp_path: Path) -> None:
    _project(tmp_path, first="- surfaces: payments\n")
    toml = '[review.surfaces.payments]\nchecklists = ["security"]\n'
    (tmp_path / "harness.toml").write_text(toml, encoding="utf-8")
    assert _model(_launch(tmp_path, "1.1.1")) == ("sonnet", "medium")


def test_an_unknown_sealed_surface_fails_closed(tmp_path: Path) -> None:
    _project(tmp_path)
    spec = f"{SPEC_DIR}/1.1.1-a.md"
    seal = tmp_path / "progress" / "plan_task_1.1.sha256"
    seal.parent.mkdir()
    seal.write_text(f"{'0' * 64}  {spec}\n-;securty  rating:{spec}\n", encoding="utf-8")
    _fails_closed(_launch(tmp_path, "1.1.1"))


# ── a rating that cannot be read launches as high risk (Codex final) ───────
# Plans older than docs/plan (#105) may lack `spec` or the owner: the session is
# still launched, never with the low model, and a WARN names the missing key.


def _edit_plan(root: Path, edit: Any) -> None:
    path = root / "feature_list.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def _escalated_with_warning(res: subprocess.CompletedProcess[str], key: str) -> None:
    assert _model(res) == ("opus", "high")
    warn = [line for line in res.stderr.splitlines() if "[WARN]" in line]
    assert warn and all(key in line and "1.1.1" in line for line in warn), res.stderr


@pytest.mark.parametrize("value", [None, "", 7])
def test_a_subtask_without_a_spec_path_launches_as_high_risk(tmp_path: Path, value: object) -> None:
    _project(tmp_path)

    def edit(data: dict[str, Any]) -> None:
        sub = data["features"][0]["subtasks"][0]
        if value is None:
            del sub["spec"]
        else:
            sub["spec"] = value

    _edit_plan(tmp_path, edit)
    for target in ("1.1.1", "1.1"):
        _escalated_with_warning(_launch(tmp_path, target), "`spec`")
    assert _model(_launch(tmp_path, "1.1.2")) == ("sonnet", "medium")


@pytest.mark.parametrize("value", [None, ""])
def test_a_task_without_its_phase_launches_as_high_risk(tmp_path: Path, value: object) -> None:
    _project(tmp_path)

    def edit(data: dict[str, Any]) -> None:
        task = data["features"][0]
        if value is None:
            del task["phase"]
        else:
            task["phase"] = value

    _edit_plan(tmp_path, edit)
    _escalated_with_warning(_launch(tmp_path, "1.1.1"), "`phase`")


def test_a_complete_record_prints_no_warning(tmp_path: Path) -> None:
    _project(tmp_path)
    res = _launch(tmp_path, "1.1")
    assert _model(res) == ("sonnet", "medium")
    assert "[WARN]" not in res.stderr
