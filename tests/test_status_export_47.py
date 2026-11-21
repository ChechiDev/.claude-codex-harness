"""_status_export.py (`make status-export`, issue #47): a read-only, static status view.

Gates, attempts, blocks, timings and links to evidence, as JSON or a static HTML
page with no controls. Optional per-subtask token cost from local transcripts,
"unknown" when their format is not understood. It never writes harness state.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import Plan

SID = "1.1.1"


def _git_init(root: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    runs = root / ".git" / "harness-gate-runs"
    runs.mkdir()
    return runs


def _attempt(runs: Path, name: str, rc: int, at: str, tail: str = "ok") -> None:
    rec = {
        "schema": 1,
        "mode": "code",
        "subtask": SID,
        "check_rc": rc,
        "log_tail": tail,
        "head": "abc123",
        "impl_fp": "f" * 64,
        "toolkit_fp": "t" * 64,
        "created_at": at,
    }
    (runs / f"{name}_{SID}.json").write_text(json.dumps(rec))


def _project(plan: Plan) -> tuple[Plan, Path]:
    plan.phase("01", "1.1").sub(SID, "in_progress").sub("1.1.2", "blocked", SID)
    plan.approve_roadmap().approve_phase("01")
    plan.verdict(f"plan_review_{SID}.md", "PLAN_APPROVED")
    plan.verdict(f"review_{SID}.md", "CHANGES_REQUESTED")
    plan.sync()
    return plan, _git_init(plan.root)


def _json(plan: Plan, *args: str) -> dict[str, Any]:
    res = plan.run_py("_status_export.py", "--format", "json", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    data: dict[str, Any] = json.loads(res.stdout)
    return data


def _leaf(data: dict[str, Any], sid: str) -> dict[str, Any]:
    return next(leaf for leaf in data["leaves"] if leaf["id"] == sid)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_json_exports_gates_blocks_and_evidence_links(plan: Plan) -> None:
    plan, _ = _project(plan)
    data = _json(plan)
    assert data["schema"] == 1
    leaf = _leaf(data, SID)
    assert leaf["status"] == "in_progress"
    assert leaf["gates"]["plan"] == {
        "verdict": "PLAN_APPROVED",
        "evidence": f"progress/plan_review_{SID}.md",
    }
    assert leaf["gates"]["code"]["verdict"] == "CHANGES_REQUESTED"
    blocked = _leaf(data, "1.1.2")
    assert blocked["status"] == "blocked"
    assert blocked["gates"]["plan"] == {"verdict": "", "evidence": None}


def test_attempts_and_timings_come_from_gate_run_records(plan: Plan) -> None:
    plan, runs = _project(plan)
    _attempt(runs, "code.2", 0, "2026-11-20T10:05:00+00:00")
    _attempt(runs, "code.1", 1, "2026-11-20T10:00:00+00:00", tail="FAILED test_x")
    _attempt(runs, "code.approved", 0, "2026-11-20T10:09:00+00:00")
    leaf = _leaf(_json(plan), SID)
    assert [a["check_rc"] for a in leaf["attempts"]] == [1, 0, 0]
    assert leaf["attempts"][0]["log_tail"] == "FAILED test_x"
    assert leaf["approved"] is True
    assert leaf["timing"] == {
        "first_attempt": "2026-11-20T10:00:00+00:00",
        "last_attempt": "2026-11-20T10:09:00+00:00",
    }
    assert _leaf(_json(plan), "1.1.2")["attempts"] == []


def test_malformed_gate_record_is_reported_not_fatal(plan: Plan) -> None:
    plan, runs = _project(plan)
    (runs / f"code.3_{SID}.json").write_text("{not json")
    leaf = _leaf(_json(plan), SID)
    assert leaf["attempts"] == []
    assert any("code.3" in w for w in leaf["warnings"])


def test_cost_is_unknown_without_transcripts(plan: Plan) -> None:
    plan, _ = _project(plan)
    assert _leaf(_json(plan), SID)["cost"] == "unknown"


def test_cost_sums_tokens_of_transcript_lines_on_the_leaf_branch(plan: Plan) -> None:
    plan, _ = _project(plan)
    tdir = plan.root / "transcripts"
    tdir.mkdir()
    branch = "feat/1.1-task-1-1"
    lines = [
        {"gitBranch": branch, "message": {"usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"gitBranch": branch, "message": {"usage": {"input_tokens": 1, "output_tokens": 2}}},
        {"gitBranch": "other", "message": {"usage": {"input_tokens": 99, "output_tokens": 99}}},
    ]
    body = "\n".join(json.dumps(x) for x in lines) + "\nnot json at all\n"
    (tdir / "s.jsonl").write_text(body)
    data = _json(plan, "--transcripts", str(tdir))
    assert _leaf(data, SID)["cost"] == {"input_tokens": 11, "output_tokens": 7}


def test_cost_is_unknown_when_transcript_format_is_not_understood(plan: Plan) -> None:
    plan, _ = _project(plan)
    tdir = plan.root / "transcripts"
    tdir.mkdir()
    (tdir / "s.jsonl").write_text('{"something": "else"}\n[1, 2]\n')
    assert _leaf(_json(plan, "--transcripts", str(tdir)), SID)["cost"] == "unknown"


def test_html_is_static_escaped_and_has_no_controls(plan: Plan) -> None:
    plan, runs = _project(plan)
    _attempt(runs, "code.1", 1, "2026-11-20T10:00:00+00:00", tail="<script>alert(1)</script>")
    res = plan.run_py("_status_export.py")
    assert res.returncode == 0, res.stderr
    html = res.stdout
    assert html.startswith("<!DOCTYPE html>")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    for forbidden in ("<script", "<form", "<button", "<input", "onclick"):
        assert forbidden not in html.lower()
    assert f'href="progress/plan_review_{SID}.md"' in html
    assert "PLAN_APPROVED" in html


def test_out_file_is_written_and_nothing_else_changes(plan: Plan) -> None:
    plan, runs = _project(plan)
    _attempt(runs, "code.1", 0, "2026-11-20T10:00:00+00:00")
    before = _snapshot(plan.root)
    out = plan.root.parent / "view.html"
    res = plan.run_py("_status_export.py", "--out", str(out))
    assert res.returncode == 0, res.stderr
    assert out.read_text().startswith("<!DOCTYPE html>")
    assert _snapshot(plan.root) == before


def test_works_outside_a_git_repo(plan: Plan) -> None:
    plan.phase("01", "1.1").sub(SID).sync()
    leaf = _leaf(_json(plan), SID)
    assert leaf["attempts"] == []


@pytest.mark.parametrize("bad", ["--format", "--transcripts"])
def test_missing_argument_value_fails_cleanly(plan: Plan, bad: str) -> None:
    plan.phase("01", "1.1").sub(SID).sync()
    res = plan.run_py("_status_export.py", bad)
    assert res.returncode == 2
    assert "Traceback" not in res.stderr


@pytest.mark.parametrize(
    "target", ["feature_list.json", "progress/x.html", "docs/plan/x.html", ".git/x.html"]
)
def test_out_refuses_harness_state(plan: Plan, target: str) -> None:
    plan, _ = _project(plan)
    before = _snapshot(plan.root)
    res = plan.run_py("_status_export.py", "--out", target)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "refused" in res.stderr
    assert _snapshot(plan.root) == before


def test_out_refuses_a_symlink(plan: Plan) -> None:
    plan, _ = _project(plan)
    link = plan.root / "view.html"
    link.symlink_to(plan.root / "feature_list.json")
    before = (plan.root / "feature_list.json").read_bytes()
    res = plan.run_py("_status_export.py", "--out", "view.html")
    assert res.returncode == 2
    assert (plan.root / "feature_list.json").read_bytes() == before


def test_evidence_links_are_relative_to_the_out_file(plan: Plan) -> None:
    plan, _ = _project(plan)
    (plan.root / "reports").mkdir()
    res = plan.run_py("_status_export.py", "--out", "reports/status.html")
    assert res.returncode == 0, res.stderr
    html = (plan.root / "reports" / "status.html").read_text()
    assert f'href="../progress/plan_review_{SID}.md"' in html


def test_undecodable_evidence_is_a_warning_not_fatal(plan: Plan) -> None:
    plan, _ = _project(plan)
    (plan.root / "progress" / f"review_{SID}.md").write_bytes(b"\xff\xfe\x00bad")
    leaf = _leaf(_json(plan), SID)
    assert leaf["gates"]["code"]["verdict"] == ""
    assert any(f"review_{SID}.md" in w for w in leaf["warnings"])


def test_out_refuses_a_hard_link(plan: Plan) -> None:
    plan, _ = _project(plan)
    (plan.root / "view.html").hardlink_to(plan.root / "feature_list.json")
    before = (plan.root / "feature_list.json").read_bytes()
    res = plan.run_py("_status_export.py", "--out", "view.html")
    assert res.returncode == 2
    assert (plan.root / "feature_list.json").read_bytes() == before


def test_out_refuses_the_git_dir_of_a_linked_worktree(plan: Plan, tmp_path: Path) -> None:
    plan, _ = _project(plan)
    git = ["git", "-C", str(plan.root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run([*git, "worktree", "add", "-q", str(wt)], check=True)
    target = plan.root / ".git" / "worktrees" / "wt" / "x.html"
    res = subprocess.run(
        [
            "python3",
            str(Path(__file__).parent.parent / "lib" / "_status_export.py"),
            "--out",
            str(target),
        ],
        cwd=wt,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 2, res.stdout + res.stderr
    assert not target.exists()


def test_out_refuses_the_toolkit_through_the_harness_symlink(plan: Plan, tmp_path: Path) -> None:
    plan, _ = _project(plan)
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()
    (toolkit / "_harness.py").write_text("keep\n")
    (plan.root / "harness").symlink_to(toolkit)
    res = plan.run_py("_status_export.py", "--out", "harness/_harness.py")
    assert res.returncode == 2
    assert (toolkit / "_harness.py").read_text() == "keep\n"


def test_out_refuses_the_real_toolkit(plan: Plan) -> None:
    plan, _ = _project(plan)
    target = Path(__file__).parent.parent / "lib" / "never-written.html"
    res = plan.run_py("_status_export.py", "--out", str(target))
    assert res.returncode == 2
    assert not target.exists()
