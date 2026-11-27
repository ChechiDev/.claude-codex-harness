"""Issue #133: repair the close when the reference doc fails the doc gate.

- `doc` checks the leaf's reference doc against the doc gate once the documenter is
  done and, while it fails, relaunches the documenter in repair mode (bounded by the
  attempt budget, harness.toml [review] max_attempts, and a per-run ceiling).
- With a close pending (its message waiting, the spec already done, no close commit)
  and red because of the doc, `doc` runs again in repair mode instead of refusing.
- A repair may change only the leaf's reference doc: the harness snapshots the tree
  before it and refuses the close (fail closed) when anything else changed, the
  waiting message, the code, the tests and the seals included.
- `close` names the doc gate as the reason and `make status` / resume point to `doc`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import _resume
import pytest
from conftest import DECLARED, LEAF_ID, Project, write_control

GATE = "./harness/review_with_codex.sh"
SUBJECT = f"feat(domain): add user entity [{LEAF_ID}]"
MSG = f"progress/close_{LEAF_ID}.msg"
REF = f"docs/reference/{LEAF_ID}-user-entity.md"
ANCHORS = ("concept", "planning", "execution", "problems", "files")
FRONT = f"---\nid: {LEAF_ID}\ndate: 2026-11-26 10:00\nalias:\n  - user\ntags:\n  - domain\n---\n"
BROKEN_DOC = FRONT + "<!-- section: concept -->\n## User\n\nOnly the concept.\n"
GOOD_DOC = FRONT + "".join(f"\n<!-- section: {a} -->\n## {a}\n\nText.\n" for a in ANCHORS)


# ── helpers ─────────────────────────────────────────────────────────────────


def _git(project: Project, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project.root, text=True, capture_output=True, check=True
    ).stdout


def _ledger(project: Project, step: str = "close") -> Path:
    return project.root / ".git" / "harness-gate-runs" / f"attempts_{step}_{LEAF_ID}.json"


def _snapshot(project: Project) -> Path:
    return project.root / ".git" / f"harness-doc-repair-{LEAF_ID}.json"


def _fake_documenter(tmp: Path, repair: str = "fix", first_doc: str = BROKEN_DOC) -> dict[str, str]:
    """A fake Codex documenter. A normal close run writes `first_doc`, marks the leaf
    done (feature_list.json) and leaves the commit message. A repair run (the prompt
    says "Repair mode") does `repair`: fix (a complete doc), nothing (the doc stays
    broken), message (rewrites the waiting message too), code (touches the code too)."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    (tmp / "first.md").write_text(first_doc, encoding="utf-8")
    (tmp / "good.md").write_text(GOOD_DOC, encoding="utf-8")
    actions = {
        "fix": f'cp "$FAKE_DIR/good.md" {REF}',
        "nothing": ":",
        "message": f'cp "$FAKE_DIR/good.md" {REF}; echo "feat: other [{LEAF_ID}]" > {MSG}',
        "code": f'cp "$FAKE_DIR/good.md" {REF}; echo "X = 1" >> {DECLARED}',
        "other": f'cp "$FAKE_DIR/good.md" {REF}; cp {REF} docs/reference/{LEAF_ID}-other.md',
        "index": f'cp "$FAKE_DIR/good.md" {REF}; '
        f'h="$(echo "X = 1" | git hash-object -w --stdin)"; '
        f'git update-index --cacheinfo "100644,$h,{DECLARED}"',
    }
    script = f"""#!/usr/bin/env bash
prompt="$(cat)"
n="$(cat "$FAKE_DIR/calls" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$FAKE_DIR/calls"
printf '%s\\n=====\\n' "$prompt" >> "$FAKE_DIR/prompts"
mkdir -p docs/reference
if printf '%s' "$prompt" | grep -q 'Repair mode'; then
  {actions[repair]}
  exit 0
fi
cp "$FAKE_DIR/first.md" {REF}
sed -i 's/"in_progress"/"done"/g' feature_list.json
printf '%s\\n\\nWhy it matters.\\n' '{SUBJECT}' > {MSG}
"""
    codex = bin_dir / "codex"
    codex.write_text(script, encoding="utf-8")
    codex.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DIR": str(tmp),
        "HARNESS_CHECK_CMD": "true",
    }


def _calls(tmp: Path) -> int:
    path = tmp / "calls"
    return int(path.read_text()) if path.exists() else 0


def _approved(project: Project) -> None:
    project.set_verdict("code", "APPROVED", with_gate_record=True)


def _pending_close(project: Project, doc: str = BROKEN_DOC) -> None:
    """The real case: the documenter wrote an incomplete doc and status: done, the
    message waits, and no close commit exists."""
    _approved(project)
    project.set_leaf("done")
    project.write(REF, doc)
    project.write(MSG, SUBJECT + "\n\nWhy it matters.\n")


def _doc(project: Project, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)


def _status_next(project: Project) -> str:
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    res = subprocess.run(
        ["python3", str(project.root / "harness" / "plan_status.py")],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
        env=base,
    )
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith("Next: ")]
    assert len(lines) == 1, res.stdout
    return lines[0].removeprefix("Next: ")


def _resume_json(project: Project) -> dict[str, Any]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    res = subprocess.run(
        ["python3", str(project.root / "harness" / "_resume.py"), "--json"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
        env=base,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    data: dict[str, Any] = json.loads(res.stdout)
    return data


def _fail_close(project: Project, reason: str) -> None:
    import _config

    path = _ledger(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    cap = _config.load(project.root).max_attempts
    assert _resume.begin(path, "1001", cap) is None
    _resume.end(path, "1001", "fail", reason)


# ── doc_gate.sh --leaf ──────────────────────────────────────────────────────


def test_doc_gate_leaf_checks_only_that_leaf(project: Project) -> None:
    project.set_leaf("done")
    project.write(REF, BROKEN_DOC)
    res = project.run("doc_gate.sh", None, "--leaf", LEAF_ID)
    assert res.returncode == 1
    out = res.stdout + res.stderr
    for anchor in ANCHORS[1:]:
        assert f"<!-- section: {anchor} -->" in out
    assert "<!-- section: concept -->'" not in out
    project.write(REF, GOOD_DOC)
    assert project.run("doc_gate.sh", None, "--leaf", LEAF_ID).returncode == 0


def test_doc_gate_leaf_requires_nothing_of_a_leaf_not_done(project: Project) -> None:
    # The same rule as the full gate: only a done leaf needs its reference doc.
    assert project.run("doc_gate.sh", None, "--leaf", LEAF_ID).returncode == 0


def test_doc_gate_leaf_rejects_a_malformed_id(project: Project) -> None:
    res = project.run("doc_gate.sh", None, "--leaf", "../x")
    assert res.returncode == 2


# ── the doc step repairs its own doc ────────────────────────────────────────


@pytest.mark.e2e
def test_doc_repairs_an_incomplete_doc_before_the_commit(project: Project, tmp_path: Path) -> None:
    _approved(project)
    res = _doc(project, _fake_documenter(tmp_path))
    assert res.returncode == 0, res.stdout + res.stderr
    assert _calls(tmp_path) == 2  # the close, then one repair
    assert _git(project, "log", "-1", "--format=%s").strip() == SUBJECT
    assert _git(project, "show", f"HEAD:{REF}") == GOOD_DOC
    assert not (project.root / MSG).exists()
    assert not _snapshot(project).exists()
    entries = _resume.read_entries(_ledger(project))
    assert [e.outcome for e in entries] == ["fail", "ok"]
    assert "doc gate" in entries[0].reason


@pytest.mark.e2e
def test_doc_with_a_complete_doc_launches_no_repair(project: Project, tmp_path: Path) -> None:
    _approved(project)
    res = _doc(project, _fake_documenter(tmp_path, first_doc=GOOD_DOC))
    assert res.returncode == 0, res.stdout + res.stderr
    assert _calls(tmp_path) == 1
    assert [e.outcome for e in _resume.read_entries(_ledger(project))] == ["ok"]


@pytest.mark.e2e
def test_the_repairs_are_bounded_then_the_human_decides(project: Project, tmp_path: Path) -> None:
    _approved(project)
    head = _git(project, "rev-parse", "HEAD")
    res = _doc(project, _fake_documenter(tmp_path, repair="nothing"))
    assert res.returncode == 1
    assert _calls(tmp_path) == 3  # the close and two repairs (default max_attempts 3)
    assert _git(project, "rev-parse", "HEAD") == head
    assert (project.root / MSG).read_text().startswith(SUBJECT)  # kept for the human
    assert "needs human" in res.stderr
    assert _status_next(project).startswith("needs human")
    assert _resume_json(project)["authorized"] is False


@pytest.mark.e2e
def test_the_repair_ceiling_holds_without_a_budget(project: Project, tmp_path: Path) -> None:
    write_control(project.root, "[review]\nmax_attempts = 0\n")
    _approved(project)
    res = _doc(project, _fake_documenter(tmp_path, repair="nothing"))
    assert res.returncode == 1
    assert _calls(tmp_path) == 3
    assert "the human" in res.stderr
    assert _status_next(project).startswith("needs human")


# ── repair mode: `doc` on a pending, red close ──────────────────────────────


@pytest.mark.e2e
def test_doc_repairs_a_pending_close_red_by_its_doc(project: Project, tmp_path: Path) -> None:
    env = _fake_documenter(tmp_path)
    _pending_close(project)
    res = _doc(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _calls(tmp_path) == 1
    assert "Repair mode" in (tmp_path / "prompts").read_text()
    log = _git(project, "log", "-1", "--format=%B")
    assert log.startswith(SUBJECT + "\n\nWhy it matters.")  # the waiting message, as is
    assert _git(project, "show", f"HEAD:{REF}") == GOOD_DOC
    assert not _snapshot(project).exists()


@pytest.mark.e2e
@pytest.mark.parametrize("repair", ["message", "code", "other", "index"])
def test_a_repair_that_changes_more_than_the_doc_is_refused(
    project: Project, tmp_path: Path, repair: str
) -> None:
    env = _fake_documenter(tmp_path, repair=repair)
    _pending_close(project)
    head = _git(project, "rev-parse", "HEAD")
    res = _doc(project, env)
    assert res.returncode == 1
    assert "outside the reference doc" in res.stderr
    assert _git(project, "rev-parse", "HEAD") == head
    assert _snapshot(project).exists()  # the evidence stays for the human
    # A later close refuses too: the snapshot still says what changed.
    again = project.run("review_with_codex.sh", None, "close", LEAF_ID, env=env)
    assert again.returncode == 1
    assert "outside the reference doc" in again.stderr
    assert _git(project, "rev-parse", "HEAD") == head
    assert _status_next(project).startswith("needs human")


def test_doc_refuses_a_pending_close_whose_doc_passes(project: Project, tmp_path: Path) -> None:
    env = _fake_documenter(tmp_path)
    _pending_close(project, doc=GOOD_DOC)
    res = _doc(project, env)
    assert res.returncode == 1
    assert f"{GATE} close {LEAF_ID}" in res.stderr
    assert _calls(tmp_path) == 0


def test_doc_refuses_a_repair_without_a_fresh_approval(project: Project, tmp_path: Path) -> None:
    env = _fake_documenter(tmp_path)
    _pending_close(project)
    project.write(DECLARED, "LATE = 1\n")  # the approval no longer covers the code
    res = _doc(project, env)
    assert res.returncode == 1
    assert "stale" in res.stderr
    assert _calls(tmp_path) == 0
    assert not _snapshot(project).exists()


def test_doc_refuses_a_repair_once_the_close_is_committed(project: Project, tmp_path: Path) -> None:
    env = _fake_documenter(tmp_path)
    _pending_close(project)
    project.commit_all(SUBJECT)
    project.write(MSG, SUBJECT + "\n")
    res = _doc(project, env)
    assert res.returncode == 1
    assert _calls(tmp_path) == 0


def test_manual_repair_prints_the_prompt_and_guards_the_close(project: Project) -> None:
    write_control(project.root)
    _pending_close(project)
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Repair mode" in res.stdout
    assert f"{GATE} close {LEAF_ID}" in res.stdout
    assert _snapshot(project).exists()
    # The human's Codex run rewrites the waiting message: the close refuses it.
    project.write(REF, GOOD_DOC)
    project.write(MSG, "feat: rewritten [1.1.1]\n")
    head = _git(project, "rev-parse", "HEAD")
    close = project.run("review_with_codex.sh", None, "close", LEAF_ID)
    assert close.returncode == 1
    assert "outside the reference doc" in close.stderr
    assert _git(project, "rev-parse", "HEAD") == head


@pytest.mark.e2e
def test_manual_repair_then_close_commits(project: Project) -> None:
    write_control(project.root)
    _pending_close(project)
    assert project.run("review_with_codex.sh", None, "doc", LEAF_ID).returncode == 0
    project.write(REF, GOOD_DOC)  # what the human's Codex run did
    close = project.run(
        "review_with_codex.sh", None, "close", LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )
    assert close.returncode == 0, close.stdout + close.stderr
    assert _git(project, "log", "-1", "--format=%s").strip() == SUBJECT
    assert not _snapshot(project).exists()


# ── close and status name the doc ───────────────────────────────────────────


def test_close_names_the_doc_gate_and_points_to_doc(project: Project) -> None:
    _pending_close(project)
    head = _git(project, "rev-parse", "HEAD")
    res = project.run("review_with_codex.sh", None, "close", LEAF_ID)
    assert res.returncode == 1
    assert "doc gate" in res.stderr
    assert f"{GATE} doc {LEAF_ID}" in res.stderr
    assert _git(project, "rev-parse", "HEAD") == head
    entries = _resume.read_entries(_ledger(project))
    assert [e.outcome for e in entries] == ["fail"]
    assert "doc gate" in entries[0].reason


def test_status_points_a_doc_red_close_to_the_repair(project: Project) -> None:
    _pending_close(project)
    _fail_close(project, "the reference doc fails the doc gate")
    expected = f"close: {GATE} doc {LEAF_ID} (last attempt: the reference doc fails the doc gate)"
    assert _status_next(project) == expected
    action = _resume_json(project)
    assert action["text"] == expected
    assert action["kind"] == "close" and action["authorized"] is True
    assert action["command"] == [GATE, "doc", LEAF_ID]


def test_status_keeps_a_close_retry_for_other_failures(project: Project) -> None:
    _pending_close(project, doc=GOOD_DOC)
    _fail_close(project, "git commit failed")
    assert _resume_json(project)["command"] == [GATE, "close", LEAF_ID]


@pytest.mark.parametrize(
    "reason",
    [
        "the repair changed files outside the reference doc",
        "the repairs ran out: the reference doc still fails the doc gate",
    ],
)
def test_status_sends_a_failed_repair_to_the_human(project: Project, reason: str) -> None:
    _pending_close(project)
    _fail_close(project, reason)
    text = _status_next(project)
    assert text.startswith("needs human"), text
    assert reason in text
    assert _resume_json(project)["authorized"] is False
