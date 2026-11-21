"""Issue #27: re-gating an approved roadmap, phase or issue must not deadlock.

When the default `make check` is red, the roadmap/phase/issue gate runs it a second
time with HARNESS_ASSUME_APPROVED=<target>: only that target's verdict reads approved
and only its seal reads matching. The reviewer may approve only when that
counterfactual run is green. Every decision-making caller (the gate's first run, the
close, the Stop hook) runs `make check` with the variable removed.

Projects are installed with the real install.sh (tests/i0_projects.py); the gate runs
in manual mode (its prompt is the output) or with a fake reviewer on PATH.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, bash_payload, bind_code_approval, write_control
from i0_projects import Installed, install_project

VAR = "HARNESS_ASSUME_APPROVED"
IID = "ISS-14"
ISSUE_DIR = f"docs/plan/issues/{IID}-regate"
ISSUE_HEADER = (
    f"# {IID} Regate\n- issue: https://github.com/acme/app/issues/14\n"
    "- type: fix\n- origin: 1.1\n- priority: next\n- goal: g.\n"
)
PHASE_FILE = "docs/plan/phase-01-greetings.md"
PHASE_DIR = "docs/plan/phase-01-greetings"
PHASE_MD = (
    "# Phase 01 — Greetings\n- goal: say hello.\n\n"
    "## 1.1 Greeting\n- layer: domain\n- goal: a greet function.\n"
)
PLAN_MD = "# Project plan\n\n## Vision\nA tiny greeting library.\n"
SPEC = (
    "# {sid} Sub {sid}\n- status: {status}\n- layer: domain\n- depends: {depends}\n"
    "- files:\n  - src/demo/m{slug}.py\n"
    "- acceptance:\n  - AC1 | works | tests/acceptance/test_{sid}.py::test_ac1\n"
)
ORACLE = 'def test_ac1() -> None:\n    """AC1: works."""\n    assert True\n'
REFERENCE = (
    "---\nid: {sid}\ndate: 2026-11-12\nalias: s\ntags: [t]\n---\n"
    "<!-- section: concept -->\n<!-- section: planning -->\n<!-- section: execution -->\n"
    "<!-- section: problems -->\n<!-- section: files -->\n"
)
RERUN = "(re-run its Codex gate: ./harness/review_with_codex.sh {})"
ROADMAP_RULE = "phase 01 is broken down but the roadmap is not ROADMAP_APPROVED"
COUNTERFACTUAL = re.compile(
    rf"{VAR}=(?P<target>\S+)\S*.*?exit code (?P<rc>\d+), full output in (?P<log>\S+?)\.?$",
    re.M | re.S,
)
FIRST_RUN = re.compile(r"exit code (?P<rc>\d+), full output in (?P<log>\S+?)\.?$", re.M)


# ── project builders ─────────────────────────────────────────────────────────


def _sh(project: Installed, *argv: str, env: dict[str, str] | None = None) -> str:
    res = project.run(*argv) if env is None else _run_env(project, env, *argv)
    assert res.returncode == 0, f"{argv}:\n{res.stdout}{res.stderr}"
    return res.stdout


def _run_env(
    project: Installed, env: dict[str, str], *argv: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=project.root, env=env, text=True, capture_output=True,
        check=False, timeout=900,
    )  # fmt: skip


def _write(project: Installed, rel: str, text: str) -> None:
    path = project.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _verdict(project: Installed, name: str, verdict: str) -> None:
    _write(project, f"progress/{name}", f"# Review\n\n**Verdict:** {verdict}\n")


def _seal(project: Installed, *what: str) -> None:
    _sh(project, "python3", "harness/_guard.py", "seal", *what)


def _done_leaf(project: Installed, sid: str) -> None:
    """Everything a `done` leaf needs to keep make check green."""
    _write(project, f"tests/acceptance/test_{sid}.py", ORACLE)
    _verdict(project, f"plan_review_{sid}.md", "PLAN_APPROVED")
    _verdict(project, f"review_{sid}.md", "APPROVED")
    _write(project, f"docs/reference/{sid}-s.md", REFERENCE.format(sid=sid))
    _seal(project, sid)


def _in_progress_leaf(project: Installed, sid: str) -> None:
    _write(project, f"tests/acceptance/test_{sid}.py", ORACLE)
    _verdict(project, f"plan_review_{sid}.md", "PLAN_APPROVED")
    _seal(project, sid)


def _spec(sid: str, status: str, depends: str = "none") -> str:
    return SPEC.format(sid=sid, status=status, depends=depends, slug=sid.replace(".", ""))


def _commit(project: Installed, msg: str) -> None:
    _sh(project, "git", "add", "-A")
    _sh(project, "git", "commit", "-qm", msg)


def _green(project: Installed) -> None:
    res = project.make("check")
    assert res.returncode == 0, res.stdout + res.stderr


def _issue_project(tmp: Path) -> Installed:
    """Issue ISS-14 approved and sealed: ISS-14.1 done, ISS-14.2 in_progress. Green."""
    project = install_project(tmp, "python")
    _sh(project, "git", "checkout", "-qb", f"fix/{IID}-regate")
    _write(project, f"docs/plan/issues/{IID}-regate.md", ISSUE_HEADER)
    _write(project, f"{ISSUE_DIR}/{IID}.1-s.md", _spec(f"{IID}.1", "done"))
    _write(project, f"{ISSUE_DIR}/{IID}.2-s.md", _spec(f"{IID}.2", "in_progress", f"{IID}.1"))
    _sh(project, "make", "-s", "plan")
    _verdict(project, f"issue_review_{IID}.md", "ISSUE_APPROVED")
    _seal(project, "issue", IID)
    _done_leaf(project, f"{IID}.1")
    _in_progress_leaf(project, f"{IID}.2")
    _commit(project, "approved issue")
    _green(project)
    return project


def _phase_project(tmp: Path) -> Installed:
    """Roadmap and phase 01 approved and sealed: 1.1.1 done, 1.1.2 in_progress. Green."""
    project = install_project(tmp, "python")
    _write(project, "docs/plan/PLAN.md", PLAN_MD)
    _write(project, PHASE_FILE, PHASE_MD)
    _write(project, f"{PHASE_DIR}/1.1.1-s.md", _spec("1.1.1", "done"))
    _write(project, f"{PHASE_DIR}/1.1.2-s.md", _spec("1.1.2", "in_progress", "1.1.1"))
    _sh(project, "make", "-s", "plan")
    _verdict(project, "roadmap_review.md", "ROADMAP_APPROVED")
    _seal(project, "roadmap")
    _verdict(project, "phase_review_01.md", "PHASE_APPROVED")
    _seal(project, "phase", "01")
    _done_leaf(project, "1.1.1")
    _in_progress_leaf(project, "1.1.2")
    _commit(project, "approved phase")
    _green(project)
    return project


def _edit(project: Installed, rel: str, old: str, new: str) -> None:
    path = project.root / rel
    text = path.read_text()
    assert old in text, (rel, old)
    path.write_text(text.replace(old, new))


def _resync(project: Installed) -> None:
    _sh(project, "make", "-s", "plan")


# Mutations: each one is a failure that only re-approving the target clears.


def _issue_seal_mismatch(project: Installed) -> None:
    _edit(project, f"{ISSUE_DIR}/{IID}.2-s.md", "- AC1 | works", "- AC1 | works well")
    _resync(project)


def _issue_changes_requested(project: Installed) -> None:
    _issue_seal_mismatch(project)
    _verdict(project, f"issue_review_{IID}.md", "CHANGES_REQUESTED")


def _phase_seal_mismatch(project: Installed) -> None:
    _edit(project, f"{PHASE_DIR}/1.1.2-s.md", "- AC1 | works", "- AC1 | works well")
    _resync(project)


def _phase_changes_requested(project: Installed) -> None:
    _phase_seal_mismatch(project)
    _verdict(project, "phase_review_01.md", "CHANGES_REQUESTED")


def _roadmap_seal_mismatch(project: Installed) -> None:
    # The vision is sealed only by the roadmap (a phase goal is in the phase seal too).
    _edit(project, "docs/plan/PLAN.md", "A tiny greeting library.", "A small greeting library.")
    _resync(project)


def _roadmap_changes_requested(project: Installed) -> None:
    _roadmap_seal_mismatch(project)
    _verdict(project, "roadmap_review.md", "CHANGES_REQUESTED")


# ── running make check and the gate ──────────────────────────────────────────


def _check(project: Installed, assume: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(project.env)
    if assume is not None:
        env[VAR] = assume
    return _run_env(project, env, "make", "-s", "--no-print-directory", "check")


def _gate_env(project: Installed, tmp: Path, **extra: str) -> dict[str, str]:
    logs = tmp / "logs"
    logs.mkdir(exist_ok=True)
    # Test-only channel (#90): the gate reads its mode from harness.toml.
    mode = extra.pop("HARNESS_CODEX_MODE", "manual")
    write_control(project.root, f'[codex]\nmode = "{mode}"\n')
    return {
        **project.env, "CLAUDE_PROJECT_DIR": str(project.root),
        "TMPDIR": str(logs), **extra,
    }  # fmt: skip


def _gate(
    project: Installed, tmp: Path, *args: str, **extra: str
) -> subprocess.CompletedProcess[str]:
    env = _gate_env(project, tmp, **extra)
    return _run_env(project, env, "bash", "harness/review_with_codex.sh", *args)


def _prompt(project: Installed, tmp: Path, *args: str, **extra: str) -> str:
    res = _gate(project, tmp, *args, **extra)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.split("--- prompt ---", 1)[1]


def _first_run(prompt: str) -> tuple[int, str]:
    match = FIRST_RUN.search(prompt)
    assert match is not None, prompt
    return int(match["rc"]), Path(match["log"]).read_text()


def _counterfactual(prompt: str) -> tuple[str, int, str]:
    match = COUNTERFACTUAL.search(prompt)
    assert match is not None, f"no counterfactual run in the prompt:\n{prompt}"
    return match["target"].rstrip(":,"), int(match["rc"]), Path(match["log"]).read_text()


def _assumed_line(text: str, target: str) -> bool:
    return any(line.startswith("[ASSUMED]") and target in line for line in text.splitlines())


GATES = {
    "issue": (_issue_project, ("issue", IID), f"issue:{IID}"),
    "phase": (_phase_project, ("phase", "01"), "phase:01"),
    "roadmap": (_phase_project, ("roadmap",), "roadmap"),
}


# ── 1-3. The cleared failures: first run red, counterfactual green ───────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("gate", "mutate", "expected"),
    [
        # AC1: issue, seal mismatch only (verdict still ISSUE_APPROVED).
        ("issue", _issue_seal_mismatch, [RERUN.format(f"issue {IID}")]),
        # AC2: issue after a CHANGES_REQUESTED round, one done + one in_progress leaf.
        ("issue", _issue_changes_requested, [
            f"{IID}.1 is done but issue {IID} is not ISSUE_APPROVED",
            f"{IID}.2 is in_progress but issue {IID} is not ISSUE_APPROVED",
        ]),
        # AC3: phase and roadmap.
        ("phase", _phase_seal_mismatch, [RERUN.format("phase 01")]),
        ("phase", _phase_changes_requested, [
            "1.1.1 is done but phase 01 is not PHASE_APPROVED",
            "1.1.2 is in_progress but phase 01 is not PHASE_APPROVED",
        ]),
        ("roadmap", _roadmap_seal_mismatch, [RERUN.format("roadmap")]),
        ("roadmap", _roadmap_changes_requested, [ROADMAP_RULE]),
    ],
    ids=["issue-seal", "issue-cr", "phase-seal", "phase-cr", "roadmap-seal", "roadmap-cr"],
)  # fmt: skip
def test_regate_first_run_red_counterfactual_green(
    tmp_path: Path, gate: str, mutate: object, expected: list[str]
) -> None:
    build, args, target = GATES[gate]
    project = build(tmp_path)
    assert callable(mutate)
    mutate(project)
    prompt = _prompt(project, tmp_path, *args)
    rc, log = _first_run(prompt)
    assert rc != 0, log
    for line in expected:
        assert line in log, (line, log)
    named, cf_rc, cf_log = _counterfactual(prompt)
    assert named == target
    assert cf_rc == 0, cf_log
    assert _assumed_line(cf_log, target), cf_log


# ── 4. A cleared failure plus an independent one: counterfactual stays red ───


def _red_verify(project: Installed) -> str:
    """A red lint step: no [FAIL] line of the harness."""
    _write(project, "src/demo/bad.py", "import os\n")
    return ""


def _red_env_check(project: Installed) -> str:
    """The project's env-check fails without a [FAIL] line."""
    _edit(project, "Makefile", "env-check:\n", "env-check:\n\t@exit 3\n")
    return ""


def _red_scope(project: Installed) -> str:
    _write(project, "src/demo/undeclared.py", '"""Out of scope."""\n')
    return "undeclared.py"


def _red_test(project: Installed) -> str:
    _write(project, "tests/test_broken.py", "def test_broken() -> None:\n    assert False\n")
    return "test_broken"


INDEPENDENT = {
    "verify": _red_verify,
    "env-check": _red_env_check,
    "scope": _red_scope,
    "red-test": _red_test,
}


@pytest.mark.e2e
@pytest.mark.parametrize("failure", sorted(INDEPENDENT))
def test_independent_failure_keeps_the_counterfactual_red(tmp_path: Path, failure: str) -> None:
    project = _issue_project(tmp_path)
    _issue_changes_requested(project)
    needle = INDEPENDENT[failure](project)
    prompt = _prompt(project, tmp_path, "issue", IID)
    assert _first_run(prompt)[0] != 0
    _, cf_rc, cf_log = _counterfactual(prompt)
    assert cf_rc != 0, cf_log
    assert needle in cf_log


@pytest.mark.e2e
def test_other_targets_failures_keep_the_counterfactual_red(tmp_path: Path) -> None:
    """A seal mismatch and a plan-rule failure of phase 01 survive the issue's run."""
    project = _phase_project(tmp_path)
    _write(project, f"docs/plan/issues/{IID}-regate.md", ISSUE_HEADER)
    _write(project, f"{ISSUE_DIR}/{IID}.1-s.md", _spec(f"{IID}.1", "pending"))
    _resync(project)
    _verdict(project, f"issue_review_{IID}.md", "ISSUE_APPROVED")
    _seal(project, "issue", IID)
    _commit(project, "issue")
    _green(project)
    _issue_changes_requested_pending(project)
    _phase_changes_requested(project)
    prompt = _prompt(project, tmp_path, "issue", IID)
    _, cf_rc, cf_log = _counterfactual(prompt)
    assert cf_rc != 0, cf_log
    assert "1.1.1 is done but phase 01 is not PHASE_APPROVED" in cf_log
    assert "./harness/review_with_codex.sh phase 01)" in cf_log


def _issue_changes_requested_pending(project: Installed) -> None:
    _edit(project, f"{ISSUE_DIR}/{IID}.1-s.md", "- AC1 | works", "- AC1 | works well")
    _resync(project)
    _verdict(project, f"issue_review_{IID}.md", "CHANGES_REQUESTED")


# ── 5. The variable only covers its target and never reaches a decision ──────


@pytest.mark.e2e
@pytest.mark.parametrize("other", ["issue:ISS-99", "phase:02", "roadmap", "phase:01", "bogus"])
def test_the_variable_changes_nothing_for_another_target(tmp_path: Path, other: str) -> None:
    project = _issue_project(tmp_path)
    _issue_changes_requested(project)
    res = _check(project, assume=other)
    assert res.returncode != 0
    assert f"{IID}.1 is done but issue {IID} is not ISSUE_APPROVED" in res.stdout
    assert f"./harness/review_with_codex.sh issue {IID})" in res.stdout


@pytest.mark.e2e
def test_make_check_under_the_variable_labels_the_assumption(tmp_path: Path) -> None:
    project = _issue_project(tmp_path)
    _issue_changes_requested(project)
    res = _check(project, assume=f"issue:{IID}")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _assumed_line(res.stdout, f"issue:{IID}"), res.stdout


@pytest.mark.e2e
def test_the_gates_first_run_ignores_an_inherited_variable(tmp_path: Path) -> None:
    project = _issue_project(tmp_path)
    _issue_changes_requested(project)
    prompt = _prompt(project, tmp_path, "issue", IID, **{VAR: f"issue:{IID}"})
    rc, log = _first_run(prompt)
    assert rc != 0
    assert f"{IID}.1 is done but issue {IID} is not ISSUE_APPROVED" in log
    assert not _assumed_line(log, IID)


@pytest.mark.e2e
def test_the_close_check_ignores_an_inherited_variable(tmp_path: Path) -> None:
    project = _issue_project(tmp_path)
    _issue_changes_requested(project)
    # A fresh approved code gate (issue #39), so only the red close check can refuse.
    _verdict(project, f"review_{IID}.2.md", "APPROVED")
    bind_code_approval(project.root, f"{IID}.2")
    _write(project, f"progress/close_{IID}.2.msg", f"fix(domain): s [{IID}.2]\n\nBody.\n")
    head = _sh(project, "git", "rev-parse", "HEAD")
    env = _gate_env(project, tmp_path, **{VAR: f"issue:{IID}"})
    res = _run_env(project, env, "bash", "harness/review_with_codex.sh", "close", f"{IID}.2")
    assert res.returncode != 0, res.stdout + res.stderr
    assert "nothing committed" in res.stderr
    assert _sh(project, "git", "rev-parse", "HEAD") == head


@pytest.mark.e2e
def test_a_fresh_stop_ignores_an_inherited_variable(tmp_path: Path) -> None:
    project = _issue_project(tmp_path)
    _issue_changes_requested(project)
    env = {**project.env, "CLAUDE_PROJECT_DIR": str(project.root), VAR: f"issue:{IID}"}
    res = subprocess.run(
        ["bash", "harness/stop_gate.sh"], cwd=project.root, env=env, text=True,
        input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
        capture_output=True, check=False, timeout=900,
    )  # fmt: skip
    # #55: the Stop is advisory: it allows the stop and never runs make check.
    assert res.returncode == 0, res.stdout + res.stderr
    assert "no green full check for this state" in res.stdout + res.stderr


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        f"{VAR}=issue:{IID} make check",
        f"export {VAR}=roadmap",
        f"env {VAR}=phase:01 make check",
        f"make check {VAR}=issue:{IID}",
    ],
)
def test_the_bash_guard_blocks_commands_that_spell_the_variable(tmp_path: Path, cmd: str) -> None:
    project = install_project(tmp_path, "python")
    env = {**project.env, "CLAUDE_PROJECT_DIR": str(project.root)}
    res = subprocess.run(
        ["bash", "harness/guard_bash.sh"], cwd=project.root, env=env, text=True,
        input=json.dumps(bash_payload(cmd)), capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 2, (cmd, res.stdout, res.stderr)
    assert VAR in res.stderr


# ── 6. Other modes unchanged; HARNESS_CHECK_CMD gets no counterfactual run ───


@pytest.mark.e2e
@pytest.mark.parametrize("mode", ["plan", "code", "doc"])
def test_subtask_gates_get_no_counterfactual_run(tmp_path: Path, mode: str) -> None:
    project = _issue_project(tmp_path)
    _verdict(project, f"review_{IID}.2.md", "APPROVED")
    _issue_seal_mismatch(project)
    res = _gate(project, tmp_path, mode, f"{IID}.2")
    assert VAR not in res.stdout + res.stderr


@pytest.mark.e2e
def test_issue_draft_gets_no_counterfactual_run(tmp_path: Path) -> None:
    project = _issue_project(tmp_path)
    _issue_seal_mismatch(project)
    _write(project, "progress/issue_draft.md", "---\ntitle: t\n---\n## Problem\np.\n")
    prompt = _prompt(project, tmp_path, "issue-draft")
    assert VAR not in prompt


@pytest.mark.e2e
def test_a_check_cmd_override_gets_no_counterfactual_run(tmp_path: Path) -> None:
    project = _issue_project(tmp_path)
    _issue_changes_requested(project)
    prompt = _prompt(project, tmp_path, "issue", IID, HARNESS_CHECK_CMD="make check")
    assert _first_run(prompt)[0] != 0
    assert VAR not in prompt


@pytest.mark.e2e
def test_a_green_first_run_needs_no_counterfactual_run(tmp_path: Path) -> None:
    project = _issue_project(tmp_path)
    prompt = _prompt(project, tmp_path, "issue", IID)
    assert _first_run(prompt)[0] == 0
    assert VAR not in prompt


# ── 7. The reviewer contract ─────────────────────────────────────────────────

REVIEWER = TOOLKIT / "codex" / "reviewer.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_reviewer_states_the_counterfactual_exception() -> None:
    text = _flat(REVIEWER.read_text())
    assert VAR in text
    rules = [r for r in re.split(r"(?<=[.])\s+(?=- |\d+\. )|\n", REVIEWER.read_text()) if VAR in r]
    assert rules, "no rule names the counterfactual run"
    for rule in rules:
        flat = _flat(rule)
        for mode in ("`roadmap`", "`phase`", "`issue`"):
            assert mode in flat, (mode, flat)
        for mode in ("`code`", "`doc`", "`issue-draft`"):
            assert mode not in flat, (mode, flat)
        assert "green" in flat, flat


def test_reviewer_keeps_never_approve_red_for_the_other_modes() -> None:
    assert "never approve with the harness check red" in _flat(REVIEWER.read_text()).lower()


# ── 8. make check without the variable is unchanged ──────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "mutate", [_issue_seal_mismatch, _issue_changes_requested], ids=["seal", "cr"]
)
def test_make_check_without_the_variable_is_unchanged(tmp_path: Path, mutate: object) -> None:
    project = _issue_project(tmp_path)
    assert callable(mutate)
    mutate(project)
    res = _check(project)
    assert res.returncode != 0
    assert "[ASSUMED]" not in res.stdout + res.stderr
    assert "The harness is NOT healthy" in res.stdout
