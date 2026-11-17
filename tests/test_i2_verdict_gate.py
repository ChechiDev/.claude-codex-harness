"""I2 (A/B): review_with_codex.sh only succeeds on a valid verdict written by that run.

Driven on the conftest project with a fake reviewer CLI whose behaviour is set by
FAKE_MODE; plus lib/_verdict.py as a CLI in a linked worktree whose git dir lives
on another filesystem.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import LEAF_ID, TOOLKIT, Project
from test_review_issue_gates import DRAFT, _with_issue

PREV = "# Review\n\n**Verdict:** CHANGES_REQUESTED\n\nold round\n"

FAKE = r"""#!/usr/bin/env bash
if [ "$FAKE_MODE" = closestdin ]; then exec 0<&-; else cat > "$FAKE_LOG.prompt"; fi
echo run >> "$FAKE_LOG"
prev="$(sed -n 's/^Previous verdict (read-only copy): //p' "$FAKE_LOG.prompt" 2>/dev/null)"
[ -z "$prev" ] || cp "$prev" "$FAKE_LOG.prev"
v="$FAKE_VERDICT"
write() { printf '# Review\n\n**Verdict:** %s\n' "$1" > "$v"; }
if [ -n "${FAKE_ACCEPT:-}" ]; then
  mkdir -p tests/acceptance
  printf 'def test_ac1_works() -> None:\n    assert True\n' > "$FAKE_ACCEPT"
fi
case "$FAKE_MODE" in
  none) ;;
  token|closestdin) write "$FAKE_TOKEN" ;;
  notoken) printf '# Review\n\nlooks fine\n' > "$v" ;;
  symlink) write "$FAKE_TOKEN"; mv "$v" "$FAKE_LOG.real"; ln -s "$FAKE_LOG.real" "$v" ;;
  fail) printf 'half written\n' > "$v"; exit 3 ;;
  sleep) printf 'half written\n' > "$v"; touch "$FAKE_LOG.started"; sleep 3 ;;
esac
exit 0
"""


@dataclass(frozen=True)
class Gate:
    args: tuple[str, ...]
    verdict: str
    token: str
    other: str
    history: str
    seal: str | None
    accept: str = ""

    @property
    def receipt(self) -> str:
        return f"progress/verdict_{Path(self.verdict).name}.sha256"


PLAN = Gate(
    ("plan", LEAF_ID),
    f"progress/plan_review_{LEAF_ID}.md",
    "PLAN_APPROVED",
    "APPROVED",
    f"progress/review_history/{LEAF_ID}.md",
    f"progress/acceptance_{LEAF_ID}.sha256",
    f"tests/acceptance/test_{LEAF_ID}.py",
)
CODE = Gate(
    ("code", LEAF_ID),
    f"progress/review_{LEAF_ID}.md",
    "APPROVED",
    "PLAN_APPROVED",
    f"progress/review_history/{LEAF_ID}.md",
    None,
)
DRAFT_GATE = Gate(
    ("issue-draft",),
    "progress/issue_draft_review.md",
    "DRAFT_APPROVED",
    "ISSUE_APPROVED",
    "progress/review_history/issue-draft.md",
    "progress/issue_draft.sha256",
)
ISSUE = Gate(
    ("issue", "ISS-14"),
    "progress/issue_review_ISS-14.md",
    "ISSUE_APPROVED",
    "DRAFT_APPROVED",
    "progress/review_history/ISS-14.md",
    "progress/plan_issue_ISS-14.sha256",
)
GATES = [
    pytest.param(PLAN, id="plan"),
    pytest.param(CODE, id="code"),
    pytest.param(DRAFT_GATE, id="issue-draft"),
    pytest.param(ISSUE, id="issue"),
]


@dataclass
class Run:
    project: Project
    gate: Gate
    log: Path
    env: dict[str, str]

    def path(self, rel: str) -> Path:
        return self.project.root / rel

    def text(self, rel: str) -> str | None:
        path = self.path(rel)
        return path.read_text() if path.is_file() else None

    def go(self, mode: str, token: str = "", **extra: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, "FAKE_MODE": mode, "FAKE_TOKEN": token or self.gate.token, **extra}
        return self.project.run("review_with_codex.sh", None, *self.gate.args, env=env)

    def reviewer_ran(self) -> bool:
        return self.log.exists()


def _prepare(project: Project, gate: Gate, tmp: Path, prev: bool = True) -> Run:
    if gate is ISSUE:
        _with_issue(project)
    if gate is DRAFT_GATE:
        project.write("progress/issue_draft.md", DRAFT)
    if prev:
        project.write(gate.verdict, PREV)
    elif (project.root / gate.verdict).exists():
        (project.root / gate.verdict).unlink()
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(FAKE)
    (bin_dir / "codex").chmod(0o755)
    log = tmp / "fake.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HARNESS_CHECK_CMD": "true",
        "FAKE_LOG": str(log),
        "FAKE_VERDICT": gate.verdict,
        "FAKE_ACCEPT": gate.accept,
    }
    return Run(project, gate, log, env)


def _git_path(project: Project, name: str) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--git-path", name],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return (project.root / out).resolve()


def _assert_nothing_kept(run: Run, history: str | None) -> None:
    assert run.text(run.gate.verdict) == PREV
    assert run.text(run.gate.history) == history
    assert not run.path(run.gate.receipt).exists()
    if run.gate.seal:
        assert not run.path(run.gate.seal).exists()
    slot = _git_path(run.project, "harness-verdict-slot")
    assert not slot.exists() or not any(slot.iterdir())


# ── B: a gate fails on anything but a valid verdict of this run ──────────────


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("mode", ["none", "notoken", "other", "symlink"])
def test_exit_zero_without_a_valid_verdict_fails(
    project: Project, tmp_path: Path, gate: Gate, mode: str
) -> None:
    run = _prepare(project, gate, tmp_path)
    history = run.text(gate.history)
    res = run.go("token" if mode == "other" else mode, gate.other if mode == "other" else "")
    assert res.returncode != 0, res.stdout + res.stderr
    assert gate.verdict in res.stderr
    _assert_nothing_kept(run, history)


@pytest.mark.parametrize("gate", GATES)
def test_reviewer_failure_restores_and_keeps_the_partial(
    project: Project, tmp_path: Path, gate: Gate
) -> None:
    run = _prepare(project, gate, tmp_path)
    history = run.text(gate.history)
    res = run.go("fail")
    assert res.returncode != 0
    _assert_nothing_kept(run, history)
    partials = list(_git_path(project, "harness-verdict-partial").iterdir())
    assert len(partials) == 1
    assert partials[0].read_text() == "half written\n"
    assert str(partials[0]) in res.stderr


@pytest.mark.parametrize("gate", GATES)
def test_approval_writes_receipt_then_records_and_seals(
    project: Project, tmp_path: Path, gate: Gate
) -> None:
    run = _prepare(project, gate, tmp_path)
    res = run.go("token")
    assert res.returncode == 0, res.stdout + res.stderr
    data = run.path(gate.verdict).read_bytes()
    assert gate.token in data.decode()
    digest = hashlib.sha256(data).hexdigest()
    assert run.text(gate.receipt) == f"{digest}  {gate.verdict}  {gate.args[0]}\n"
    assert gate.token in (run.text(gate.history) or "")
    if gate.seal:
        assert run.path(gate.seal).is_file()


@pytest.mark.parametrize("gate", GATES)
def test_changes_requested_writes_receipt_and_records_without_seal(
    project: Project, tmp_path: Path, gate: Gate
) -> None:
    run = _prepare(project, gate, tmp_path, prev=False)
    res = run.go("token", "CHANGES_REQUESTED")
    assert res.returncode == 0, res.stdout + res.stderr
    assert run.path(gate.receipt).is_file()
    assert "CHANGES_REQUESTED" in (run.text(gate.history) or "")
    if gate.seal:
        assert not run.path(gate.seal).exists()


def test_reviewer_closing_stdin_early_and_exiting_zero_succeeds(
    project: Project, tmp_path: Path
) -> None:
    run = _prepare(project, CODE, tmp_path)
    res = run.go("closestdin")
    assert res.returncode == 0, res.stdout + res.stderr
    assert run.path(CODE.receipt).is_file()


# ── after commit: record / seal failures never undo the verdict ──────────────


def test_record_failure_warns_and_keeps_verdict_and_receipt(
    project: Project, tmp_path: Path
) -> None:
    run = _prepare(project, CODE, tmp_path)
    project.write("progress/review_history", "not a directory\n")
    res = run.go("token")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "could not record" in res.stderr
    assert "APPROVED" in (run.text(CODE.verdict) or "")
    assert run.path(CODE.receipt).is_file()


def test_seal_failure_exits_non_zero_and_keeps_verdict_and_receipt(
    project: Project, tmp_path: Path
) -> None:
    # The draft seal is written only by `seal draft`: no precondition reads it.
    run = _prepare(project, DRAFT_GATE, tmp_path)
    assert DRAFT_GATE.seal
    run.path(DRAFT_GATE.seal).mkdir(parents=True)
    res = run.go("token")
    assert run.reviewer_ran()
    assert res.returncode != 0
    assert "DRAFT_APPROVED" in (run.text(DRAFT_GATE.verdict) or "")
    assert run.path(DRAFT_GATE.receipt).is_file()


# ── A: signals, a live owner, the previous verdict as context ────────────────


def test_sigterm_during_the_review_restores(project: Project, tmp_path: Path) -> None:
    run = _prepare(project, CODE, tmp_path)
    full = {
        **{k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")},
        "CLAUDE_PROJECT_DIR": str(project.root),
        **run.env,
        "FAKE_MODE": "sleep",
    }
    proc = subprocess.Popen(
        ["bash", str(project.root / "harness/review_with_codex.sh"), *CODE.args],
        cwd=project.root,
        env=full,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = Path(f"{run.log}.started")
    deadline = time.monotonic() + 30
    while not started.exists():
        assert time.monotonic() < deadline, "the fake reviewer never started"
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=60)
    assert proc.returncode != 0
    _assert_nothing_kept(run, None)


def test_a_second_gate_with_a_live_owner_refuses_without_running(
    project: Project, tmp_path: Path
) -> None:
    run = _prepare(project, CODE, tmp_path)
    slot = _git_path(project, "harness-verdict-slot") / Path(CODE.verdict).name
    slot.mkdir(parents=True)
    (slot / "owner").write_text(f"{os.getpid()}\n")
    (slot / "phase").write_text("active prev\n")
    (slot / "prev").write_text(PREV)
    res = run.go("token")
    assert res.returncode != 0
    assert str(slot) in res.stderr
    assert not run.reviewer_ran()
    assert run.text(CODE.verdict) == PREV


def test_prompt_names_a_readable_copy_of_the_previous_verdict(
    project: Project, tmp_path: Path
) -> None:
    run = _prepare(project, CODE, tmp_path)
    assert run.go("token").returncode == 0
    prompt = Path(f"{run.log}.prompt").read_text()
    assert re.search(r"^Previous verdict \(read-only copy\): \S+$", prompt, re.MULTILINE)
    assert Path(f"{run.log}.prev").read_text() == PREV


def test_prompt_without_a_previous_verdict_has_no_copy_line(
    project: Project, tmp_path: Path
) -> None:
    run = _prepare(project, CODE, tmp_path, prev=False)
    assert run.go("token").returncode == 0
    assert "Previous verdict" not in Path(f"{run.log}.prompt").read_text()


def test_reviewer_status_is_its_own_not_a_pipeline_status() -> None:
    script = (TOOLKIT / "lib" / "review_with_codex.sh").read_text()
    assert not re.search(r"\|\s*codex exec", script)


# ── .git on another filesystem: a linked worktree ────────────────────────────

SHM = Path("/dev/shm")


def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(TOOLKIT / "lib" / "_verdict.py"), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.skipif(not SHM.is_dir(), reason="no /dev/shm")
@pytest.mark.parametrize("outcome", ["commit", "abort"])
def test_cli_in_a_linked_worktree_on_another_filesystem(tmp_path: Path, outcome: str) -> None:
    with tempfile.TemporaryDirectory(dir=SHM) as shm:
        main = Path(shm) / "main"
        main.mkdir()
        _git(main, "init", "-q", "-b", "main")
        _git(
            main,
            "-c",
            "user.email=d@e",
            "-c",
            "user.name=d",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )
        wt = tmp_path / "wt"
        _git(main, "worktree", "add", "-q", str(wt))
        if os.stat(main).st_dev == os.stat(wt).st_dev:
            pytest.skip("/dev/shm is on the same filesystem")
        verdict = wt / CODE.verdict
        verdict.parent.mkdir(parents=True)
        verdict.write_text(PREV)
        owner = str(os.getpid())
        begin = _cli(wt, "begin", "--owner", owner, "code", CODE.verdict)
        assert begin.returncode == 0, begin.stderr
        assert not verdict.exists()
        if outcome == "commit":
            verdict.write_text("# Review\n\n**Verdict:** APPROVED\n")
            res = _cli(wt, "commit", "--owner", owner, "code", CODE.verdict)
            assert res.returncode == 0, res.stderr
            assert res.stdout.strip() == "APPROVED"
            assert (wt / CODE.receipt).is_file()
        else:
            verdict.write_text("half written\n")
            res = _cli(wt, "abort", "--owner", owner, "code", CODE.verdict)
            assert res.returncode == 0, res.stderr
            assert verdict.read_text() == PREV
        slots = Path(shm) / "main/.git/worktrees/wt/harness-verdict-slot"
        assert not slots.exists() or not any(slots.iterdir())


# ── the doc gate stays outside the verdict protocol (auto mode) ──────────────


def test_auto_doc_gate_is_unaffected(project: Project, tmp_path: Path) -> None:
    run = _prepare(project, CODE, tmp_path)
    project.set_verdict("code", "APPROVED", with_gate_record=True)
    code_verdict = run.text(CODE.verdict)
    receipts = sorted(p.name for p in (project.root / "progress").glob("verdict_*.sha256"))
    # The documenter fails: doc passes its status through and touches no verdict.
    env = {**run.env, "FAKE_MODE": "fail", "FAKE_VERDICT": str(tmp_path / "doc_out.md")}
    res = project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)
    assert run.reviewer_ran(), res.stdout + res.stderr
    assert res.returncode == 3, res.stdout + res.stderr
    assert run.text(CODE.verdict) == code_verdict
    assert sorted(p.name for p in (project.root / "progress").glob("verdict_*.sha256")) == receipts
    assert not _git_path(project, "harness-verdict-slot").exists()
    assert "previous verdict restored" not in res.stderr
