"""Review rel1rev #4: a close that finishes its task without the changelog fragment.

In an activated project the last close of a task or issue owes changelog.d/<owner>.md
(docs/design/releases.md §3). When the documenter forgets it, the doc repair mode of
issue #133 covers it too: the repair may write only that fragment (and the reference
doc when it also fails the doc gate), the snapshot admits only those paths, `close`
names the missing fragment and points to `doc`, and when the repairs run out the
human decides. Nothing is committed without the fragment.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import _resume
import pytest
from conftest import DECLARED, LEAF_ID, Project

GATE = "./harness/review_with_codex.sh"
SUBJECT = f"feat(domain): add user entity [{LEAF_ID}]"
MSG = f"progress/close_{LEAF_ID}.msg"
REF = f"docs/reference/{LEAF_ID}-user-entity.md"
FRAG = "changelog.d/1.1.md"
ANCHORS = ("concept", "planning", "execution", "problems", "files")
FRONT = f"---\nid: {LEAF_ID}\ndate: 2026-11-26 10:00\nalias:\n  - user\ntags:\n  - domain\n---\n"
BROKEN_DOC = FRONT + "<!-- section: concept -->\n## User\n\nOnly the concept.\n"
GOOD_DOC = FRONT + "".join(f"\n<!-- section: {a} -->\n## {a}\n\nText.\n" for a in ANCHORS)
FRAGMENT = "- id: 1.1\n- incompatible: no\n\n### Added\n- A user entity.\n"
RECORD = '{"version": 1, "since": null, "finished": []}'


def _git(project: Project, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project.root, text=True, capture_output=True, check=True
    ).stdout


def _ledger(project: Project) -> Path:
    return project.root / ".git" / "harness-gate-runs" / f"attempts_close_{LEAF_ID}.json"


def _snapshot(project: Project) -> Path:
    return project.root / ".git" / f"harness-doc-repair-{LEAF_ID}.json"


def _activate(project: Project) -> None:
    project.write(".harness/changelog.json", RECORD)
    project.commit_all("activate the changelog")


def _fake_documenter(tmp: Path, repair: str, first_doc: str = GOOD_DOC) -> dict[str, str]:
    """A fake Codex documenter that never writes the fragment on its close run (the
    forgotten fragment). A repair run (the prompt says "Repair mode") does `repair`."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    (tmp / "first.md").write_text(first_doc, encoding="utf-8")
    (tmp / "good.md").write_text(GOOD_DOC, encoding="utf-8")
    (tmp / "frag.md").write_text(FRAGMENT, encoding="utf-8")
    write_frag = f'mkdir -p changelog.d; cp "$FAKE_DIR/frag.md" {FRAG}'
    actions = {
        "nothing": ":",
        "fragment": write_frag,
        "both": f'{write_frag}; cp "$FAKE_DIR/good.md" {REF}',
        "doc-too": f"{write_frag}; echo extra >> {REF}",  # the doc passed: not its to touch
        "code": f'{write_frag}; echo "X = 1" >> {DECLARED}',
        "other-fragment": f"{write_frag}; cp {FRAG} changelog.d/1.2.md",
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


def _prompts(tmp: Path) -> str:
    return (tmp / "prompts").read_text(encoding="utf-8")


def _approved(project: Project) -> None:
    _activate(project)
    project.set_verdict("code", "APPROVED", with_gate_record=True)


def _pending_close(project: Project, doc: str = GOOD_DOC) -> None:
    """The close ran (spec done, message waiting, no close commit) without the fragment."""
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
        cwd=project.root, text=True, capture_output=True, check=False, env=base,
    )  # fmt: skip
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith("Next: ")]
    assert len(lines) == 1, res.stdout
    return lines[0].removeprefix("Next: ")


# ── the negative path: nothing is committed without the fragment ────────────


@pytest.mark.e2e
def test_close_without_fragment_commits_nothing(project: Project, tmp_path: Path) -> None:
    _approved(project)
    head = _git(project, "rev-parse", "HEAD")
    res = _doc(project, _fake_documenter(tmp_path, repair="nothing"))
    assert res.returncode == 1
    assert _calls(tmp_path) == 3  # the close and two repairs
    assert _git(project, "rev-parse", "HEAD") == head  # nothing committed
    assert (project.root / MSG).read_text().startswith(SUBJECT)  # kept for the human
    assert not (project.root / FRAG).exists()
    assert "needs human" in res.stderr and FRAG in res.stderr
    assert _status_next(project).startswith("needs human")


@pytest.mark.e2e
def test_close_refuses_the_missing_fragment_and_points_to_doc(project: Project) -> None:
    _pending_close(project)
    head = _git(project, "rev-parse", "HEAD")
    res = project.run(
        "review_with_codex.sh", None, "close", LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )
    assert res.returncode == 1
    assert FRAG in res.stderr and f"{GATE} doc {LEAF_ID}" in res.stderr
    assert _git(project, "rev-parse", "HEAD") == head
    entries = _resume.read_entries(_ledger(project))
    assert [e.outcome for e in entries] == ["fail"]
    assert "fragment" in entries[0].reason
    assert _status_next(project).startswith(f"close: {GATE} doc {LEAF_ID}")


# ── the repair writes the fragment ──────────────────────────────────────────


@pytest.mark.e2e
def test_doc_repairs_a_forgotten_fragment_before_the_commit(
    project: Project, tmp_path: Path
) -> None:
    _approved(project)
    res = _doc(project, _fake_documenter(tmp_path, repair="fragment"))
    assert res.returncode == 0, res.stdout + res.stderr
    assert _calls(tmp_path) == 2
    assert _git(project, "log", "-1", "--format=%s").strip() == SUBJECT
    assert _git(project, "show", f"HEAD:{FRAG}") == FRAGMENT
    repair = _prompts(tmp_path).split("=====")[1]
    assert FRAG in repair and "declared:" in repair  # what `due` says to write
    assert REF not in repair.split("Change NO other file")[0].split("Do only this")[1]
    assert not _snapshot(project).exists()


@pytest.mark.e2e
def test_doc_repairs_the_doc_and_the_fragment_together(project: Project, tmp_path: Path) -> None:
    _approved(project)
    res = _doc(project, _fake_documenter(tmp_path, repair="both", first_doc=BROKEN_DOC))
    assert res.returncode == 0, res.stdout + res.stderr
    assert _calls(tmp_path) == 2
    assert _git(project, "show", f"HEAD:{FRAG}") == FRAGMENT
    assert _git(project, "show", f"HEAD:{REF}") == GOOD_DOC


@pytest.mark.e2e
def test_doc_repairs_a_pending_close_without_its_fragment(project: Project, tmp_path: Path) -> None:
    env = _fake_documenter(tmp_path, repair="fragment")
    _pending_close(project)
    res = _doc(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _calls(tmp_path) == 1 and "Repair mode" in _prompts(tmp_path)
    assert _git(project, "log", "-1", "--format=%B").startswith(SUBJECT + "\n\nWhy it matters.")
    assert _git(project, "show", f"HEAD:{FRAG}") == FRAGMENT


@pytest.mark.e2e
@pytest.mark.parametrize("repair", ["doc-too", "code", "other-fragment"])
def test_a_fragment_repair_changes_nothing_else(
    project: Project, tmp_path: Path, repair: str
) -> None:
    env = _fake_documenter(tmp_path, repair=repair)
    _pending_close(project)
    head = _git(project, "rev-parse", "HEAD")
    res = _doc(project, env)
    assert res.returncode == 1
    assert "needs human" in res.stderr
    assert _git(project, "rev-parse", "HEAD") == head
    assert _snapshot(project).exists()


# ── unchanged outside an activated project ──────────────────────────────────


def test_doc_still_refuses_an_unactivated_pending_close_whose_doc_passes(
    project: Project, tmp_path: Path
) -> None:
    env = _fake_documenter(tmp_path, repair="fragment")
    project.set_verdict("code", "APPROVED", with_gate_record=True)
    project.set_leaf("done")
    project.write(REF, GOOD_DOC)
    project.write(MSG, SUBJECT + "\n")
    res = _doc(project, env)
    assert res.returncode == 1
    assert f"{GATE} close {LEAF_ID}" in res.stderr
    assert _calls(tmp_path) == 0
