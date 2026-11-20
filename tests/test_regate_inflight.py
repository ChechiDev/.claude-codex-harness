"""Issue #31: re-gating an issue while one of its subtasks is mid-implementation.

When the issue gate's counterfactual run (#27) is still red, the gate computes the set
T of test files whose failure this approval expects (the acceptance files of the
issue's in-progress, plan-approved, not code-approved subtasks, and their `migrates:`
entries when every entry is eligible) and runs the check a third time with
HARNESS_ASSUME_APPROVED=issue:ISS-n and HARNESS_EXPECT_RED=<T>, excluding T by code
(pytest --deselect / a C `TESTS=` override). The reviewer may approve a red
counterfactual run only when that third run is green and it accounts for every test
of every file in T. Every decision-making run drops the variables.

Projects are installed with the real install.sh; the gate runs in manual mode (its
prompt is the output) or with a fake reviewer on PATH, as in test_regate_deadlock.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import TOOLKIT, bash_payload, bind_code_approval
from i0_projects import Installed, install_project
from test_regate_deadlock import (
    COUNTERFACTUAL,
    FIRST_RUN,
    IID,
    INDEPENDENT,
    ISSUE_DIR,
    ISSUE_HEADER,
    REFERENCE,
    _check,
    _commit,
    _edit,
    _flat,
    _gate,
    _gate_env,
    _issue_project,
    _issue_seal_mismatch,
    _phase_project,
    _phase_seal_mismatch,
    _prompt,
    _resync,
    _run_env,
    _seal,
    _sh,
    _verdict,
    _write,
)

EXPECT = "HARNESS_EXPECT_RED"
THIRD = re.compile(
    rf"{EXPECT}=(?P<t>[^\s]+?)[.,:;]?\s.*?exit code (?P<rc>\d+), full output in (?P<log>\S+?)\.?$",
    re.M | re.S,
)
LEAF1, LEAF2, LEAF3 = f"{IID}.1", f"{IID}.2", f"{IID}.3"
PROD = "src/demo/greet.py"
OWN = f"tests/acceptance/test_{LEAF2}.py"
ORACLE1 = f"tests/acceptance/test_{LEAF1}.py"
ORACLE1_BODY = (
    "from demo.greet import greet\n\n\ndef test_ac1() -> None:\n"
    '    """AC1: works."""\n    assert greet() == "hello"\n'
)
# The in-progress subtask's own acceptance test: red until its implementation lands.
OWN_BODY = (
    "from demo.greet import greet\n\n\ndef test_ac1() -> None:\n"
    '    """AC1: works."""\n    assert greet() == "hi!"\n'
)
SPEC2 = f"{ISSUE_DIR}/{LEAF2}-s.md"


# ── builders ─────────────────────────────────────────────────────────────────


def _inflight(tmp: Path) -> Installed:
    """ISS-14.1 done, its sealed oracle asserts greet() == "hello"; ISS-14.2
    in_progress, declares src/demo/greet.py, its own sealed oracle is red."""
    project = _issue_project(tmp)
    _write(project, PROD, 'def greet() -> str:\n    return "hello"\n')
    _write(project, ORACLE1, ORACLE1_BODY)
    _seal(project, LEAF1)
    _edit(project, SPEC2, "- files:\n", f"- files:\n  - {PROD}\n")
    _resync(project)
    _seal(project, "issue", IID)
    _write(project, OWN, OWN_BODY)
    _seal(project, LEAF2)
    _commit(project, "14.2 in progress")
    return project


def _break_oracle(project: Installed) -> None:
    """ISS-14.2's implementation turns the sealed oracle of ISS-14.1 red."""
    _write(project, PROD, 'def greet() -> str:\n    return "hi"\n')


def _declare_migrates(project: Installed, *entries: str) -> None:
    listed = "".join(f"  - {e}\n" for e in entries)
    _edit(project, SPEC2, "- acceptance:", f"- migrates:\n{listed}- acceptance:")
    _resync(project)


def _repro(tmp: Path) -> Installed:
    """The reproduced deadlock (repro_b): oracle broken, declared in `migrates:`."""
    project = _inflight(tmp)
    _break_oracle(project)
    _declare_migrates(project, ORACLE1)
    return project


def _third(prompt: str) -> tuple[set[str], int, str]:
    match = THIRD.search(prompt)
    assert match is not None, f"no third run in the prompt:\n{prompt}"
    return set(match["t"].split(",")), int(match["rc"]), Path(match["log"]).read_text()


def _no_third(prompt: str) -> None:
    assert THIRD.search(prompt) is None, prompt


def _first_log(prompt: str) -> tuple[int, str]:
    match = FIRST_RUN.search(prompt)
    assert match is not None, prompt
    return int(match["rc"]), Path(match["log"]).read_text()


def _second_log(prompt: str) -> tuple[int, str]:
    match = COUNTERFACTUAL.search(prompt)
    assert match is not None, prompt
    return int(match["rc"]), Path(match["log"]).read_text()


def _assumed_names(log: str, files: set[str]) -> bool:
    lines = [ln for ln in log.splitlines() if ln.startswith("[ASSUMED]") and EXPECT in ln]
    return any(all(f in ln for f in files) for ln in lines)


# ── AC1. The reproduced sequence: third run green ────────────────────────────


def test_repro_third_run_green(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    prompt = _prompt(project, tmp_path, "issue", IID)
    rc1, log1 = _first_log(prompt)
    assert rc1 != 0 and f"FAILED {ORACLE1}::test_ac1" in log1, log1
    rc2, log2 = _second_log(prompt)
    assert rc2 != 0 and f"FAILED {ORACLE1}::test_ac1" in log2, log2
    tolerated, rc3, log3 = _third(prompt)
    assert tolerated == {OWN, ORACLE1}
    assert rc3 == 0, log3
    assert _assumed_names(log3, {OWN, ORACLE1}), log3
    for rel in (OWN, ORACLE1):
        assert rel in prompt
    assert len(re.findall(r"exit code \d+, full output in", prompt)) == 3, prompt


# ── AC2. Only the subtask's own acceptance tests red ─────────────────────────


def test_own_acceptance_only_third_run_green(tmp_path: Path) -> None:
    project = _inflight(tmp_path)
    _issue_seal_mismatch(project)
    prompt = _prompt(project, tmp_path, "issue", IID)
    assert _first_log(prompt)[0] != 0
    assert _second_log(prompt)[0] != 0
    tolerated, rc3, log3 = _third(prompt)
    assert tolerated == {OWN}
    assert rc3 == 0, log3


def test_own_acceptance_with_a_collection_error_third_run_green(tmp_path: Path) -> None:
    """TDD red often means a missing name: pytest fails to collect the file, which
    --deselect would not avoid; the third run ignores the file instead."""
    project = _inflight(tmp_path)
    _write(
        project,
        OWN,
        OWN_BODY.replace("import greet", "import greet, wave").replace(
            'assert greet() == "hi!"', "assert wave() == greet()"
        ),
    )
    _seal(project, LEAF2)
    _issue_seal_mismatch(project)
    prompt = _prompt(project, tmp_path, "issue", IID)
    rc1, log1 = _first_log(prompt)
    assert rc1 != 0 and "wave" in log1, log1
    tolerated, rc3, log3 = _third(prompt)
    assert tolerated == {OWN}
    assert rc3 == 0, log3


# ── AC3. Still blocked: any failure outside T ────────────────────────────────


@pytest.mark.parametrize("failure", sorted(INDEPENDENT))
def test_independent_failure_keeps_the_third_run_red(tmp_path: Path, failure: str) -> None:
    project = _repro(tmp_path)
    needle = INDEPENDENT[failure](project)
    if failure == "env-check":  # a Makefile change: committed, or no third run at all
        _commit(project, "red env-check")
    prompt = _prompt(project, tmp_path, "issue", IID)
    tolerated, rc3, log3 = _third(prompt)
    assert tolerated == {OWN, ORACLE1}
    assert rc3 != 0, log3
    assert needle in log3


def test_a_done_oracle_not_in_migrates_keeps_the_third_run_red(tmp_path: Path) -> None:
    project = _inflight(tmp_path)
    _break_oracle(project)
    _issue_seal_mismatch(project)
    prompt = _prompt(project, tmp_path, "issue", IID)
    tolerated, rc3, log3 = _third(prompt)
    assert tolerated == {OWN}
    assert rc3 != 0
    assert f"FAILED {ORACLE1}::test_ac1" in log3


# ── AC4. What T never contains ───────────────────────────────────────────────


def _with_leaf3(tmp: Path) -> Installed:
    """_inflight plus ISS-14.3, in_progress and plan-approved, its own oracle red:
    it keeps a third run in the prompt whatever happens to ISS-14.2."""
    project = _inflight(tmp)
    _write(project, f"{ISSUE_DIR}/{LEAF3}-s.md", (
        f"# {LEAF3} Sub\n- status: in_progress\n- layer: domain\n- depends: {LEAF1}\n"
        f"- files:\n  - src/demo/m{LEAF3.replace('.', '')}.py\n"
        f"- acceptance:\n  - AC1 | works | tests/acceptance/test_{LEAF3}.py::test_ac1\n"
    ))  # fmt: skip
    _resync(project)
    _seal(project, "issue", IID)
    _write(project, f"tests/acceptance/test_{LEAF3}.py", OWN_BODY)
    _verdict(project, f"plan_review_{LEAF3}.md", "PLAN_APPROVED")
    _seal(project, LEAF3)
    _commit(project, "14.3 in progress")
    _issue_seal_mismatch(project)
    return project


def _not_plan_approved(project: Installed) -> None:
    _verdict(project, f"plan_review_{LEAF2}.md", "CHANGES_REQUESTED")


def _code_approved(project: Installed) -> None:
    _verdict(project, f"review_{LEAF2}.md", "APPROVED")


@pytest.mark.parametrize("mutate", [_not_plan_approved, _code_approved], ids=["plan-cr", "code-ok"])
def test_t_excludes_subtasks_out_of_the_open_window(
    tmp_path: Path, mutate: Callable[[Installed], None]
) -> None:
    project = _with_leaf3(tmp_path)
    mutate(project)
    tolerated, rc3, log3 = _third(_prompt(project, tmp_path, "issue", IID))
    assert tolerated == {f"tests/acceptance/test_{LEAF3}.py"}
    assert OWN not in tolerated
    assert rc3 != 0 and OWN in log3, log3


def test_t_excludes_subtasks_of_other_issues(tmp_path: Path) -> None:
    project = _inflight(tmp_path)
    other, leaf = "ISS-15", "ISS-15.1"
    folder = f"docs/plan/issues/{other}-other"
    _write(project, f"{folder}.md", ISSUE_HEADER.replace(IID, other).replace("/14", "/15"))
    _write(project, f"{folder}/{leaf}-s.md", (
        f"# {leaf} Sub\n- status: in_progress\n- layer: domain\n- depends: none\n"
        f"- files:\n  - src/demo/m151.py\n"
        f"- acceptance:\n  - AC1 | works | tests/acceptance/test_{leaf}.py::test_ac1\n"
    ))  # fmt: skip
    _resync(project)
    _verdict(project, f"issue_review_{other}.md", "ISSUE_APPROVED")
    _seal(project, "issue", other)
    _write(project, f"tests/acceptance/test_{leaf}.py", OWN_BODY)
    _verdict(project, f"plan_review_{leaf}.md", "PLAN_APPROVED")
    _seal(project, leaf)
    _issue_seal_mismatch(project)
    tolerated, rc3, log3 = _third(_prompt(project, tmp_path, "issue", IID))
    assert tolerated == {OWN}
    assert rc3 != 0 and f"test_{leaf}.py" in log3, log3


# ── AC5. The boundary: inherited variables never reach a decision ────────────

DESELECT_ALL = f"--deselect {OWN} --deselect {ORACLE1}"
INHERITED = {
    EXPECT: f"{OWN},{ORACLE1}",
    "PYTEST_ADDOPTS": DESELECT_ALL,
}


@pytest.mark.parametrize("var", sorted(INHERITED))
def test_the_gates_first_and_second_runs_ignore_inherited_variables(
    tmp_path: Path, var: str
) -> None:
    project = _repro(tmp_path)
    prompt = _prompt(project, tmp_path, "issue", IID, **{var: INHERITED[var]})
    for rc, log in (_first_log(prompt), _second_log(prompt)):
        assert rc != 0
        assert f"FAILED {ORACLE1}::test_ac1" in log, log
        assert EXPECT not in log, log


def _tests_only_red(tmp: Path) -> Installed:
    """Only the tests of T are red: no seal, plan-rule or lint failure."""
    project = _inflight(tmp)
    _break_oracle(project)
    return project


@pytest.mark.parametrize("var", sorted(INHERITED))
def test_the_close_check_ignores_inherited_variables(tmp_path: Path, var: str) -> None:
    project = _tests_only_red(tmp_path)
    # A fresh approved code gate (issue #39), so only the red close check can refuse.
    _verdict(project, f"review_{LEAF2}.md", "APPROVED")
    bind_code_approval(project.root, LEAF2)
    _write(project, f"progress/close_{LEAF2}.msg", f"fix(domain): s [{LEAF2}]\n\nBody.\n")
    head = _sh(project, "git", "rev-parse", "HEAD")
    env = _gate_env(project, tmp_path, **{var: INHERITED[var]})
    res = _run_env(project, env, "bash", "harness/review_with_codex.sh", "close", LEAF2)
    assert res.returncode != 0, res.stdout + res.stderr
    assert "nothing committed" in res.stderr
    assert _sh(project, "git", "rev-parse", "HEAD") == head


@pytest.mark.parametrize("var", sorted(INHERITED))
def test_a_fresh_stop_ignores_inherited_variables(tmp_path: Path, var: str) -> None:
    project = _tests_only_red(tmp_path)
    env = {**project.env, "CLAUDE_PROJECT_DIR": str(project.root), var: INHERITED[var]}
    res = subprocess.run(
        ["bash", "harness/stop_gate.sh"], cwd=project.root, env=env, text=True,
        input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
        capture_output=True, check=False, timeout=900,
    )  # fmt: skip
    # #55: the Stop is advisory: it allows the stop, warns, never runs make check.
    assert res.returncode == 0, res.stdout + res.stderr
    assert "no green full check for this state" in res.stdout + res.stderr
    assert "FAILED" not in res.stdout + res.stderr


def test_make_check_does_not_tolerate_by_itself(tmp_path: Path) -> None:
    """HARNESS_EXPECT_RED is a label: make check excludes nothing because of it."""
    project = _tests_only_red(tmp_path)
    env = {**project.env, EXPECT: f"{OWN},{ORACLE1}"}
    res = _run_env(project, env, "make", "-s", "--no-print-directory", "check")
    assert res.returncode != 0
    assert f"FAILED {ORACLE1}::test_ac1" in res.stdout + res.stderr


@pytest.mark.parametrize(
    "cmd",
    [
        f"{EXPECT}={OWN} make check",
        f"export {EXPECT}=x",
        f"env {EXPECT}=x make check",
        f"make check {EXPECT}=x",
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
    assert EXPECT in res.stderr


def test_a_check_cmd_override_gets_no_third_run(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    prompt = _prompt(project, tmp_path, "issue", IID, HARNESS_CHECK_CMD="make check")
    assert _first_log(prompt)[0] != 0
    assert EXPECT not in prompt


# ── AC5b. The prompt and the reviewer require a per-file, per-test accounting ─

REVIEWER = TOOLKIT / "codex" / "reviewer.md"
TWO_TESTS_BODY = (
    "from demo.greet import greet\n\n\ndef test_ac1() -> None:\n"
    '    """AC1: works."""\n    assert greet() == "hello"\n\n\n'
    "def test_ac2() -> None:\n"
    '    """AC2: unrelated."""\n    assert greet().startswith("h")\n'
)


def test_the_prompt_requires_a_full_accounting_of_every_file_in_t(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    prompt = _prompt(project, tmp_path, "issue", IID)
    tolerated, _, _ = _third(prompt)
    flat = _flat(prompt)
    for rel in tolerated:
        assert rel in flat
    assert "every test" in flat.lower(), flat


def test_reviewer_states_the_in_progress_exception_for_issue_mode_only() -> None:
    text = REVIEWER.read_text()
    rules = [r for r in re.split(r"(?<=[.])\s+(?=- |\d+\. )|\n", text) if EXPECT in r]
    assert rules, f"no rule of codex/reviewer.md names {EXPECT}"
    for rule in rules:
        flat = _flat(rule)
        assert "`issue`" in flat, flat
        for mode in ("`roadmap`", "`phase`", "`plan`", "`code`", "`doc`", "`issue-draft`"):
            assert mode not in flat, (mode, flat)
    assert "every test" in _flat(" ".join(rules)).lower()


FAKE_CR = r"""#!/usr/bin/env bash
prompt="$(cat)"
printf '%s\n' "$prompt" > "$FAKE_PROMPT"
id="$(printf '%s\n' "$prompt" | sed -n 's/^Feature: id=\([^ ]*\).*/\1/p' | head -1)"
printf '# Review\n\n**Verdict:** CHANGES_REQUESTED\n' > "progress/issue_review_$id.md"
"""


def test_a_rejection_records_nothing_and_seals_nothing(tmp_path: Path) -> None:
    """A file of T with two tests, one red for the declared change and one for an
    unrelated regression: the prompt asks for both, the rejection seals nothing."""
    project = _inflight(tmp_path)
    _write(project, ORACLE1, TWO_TESTS_BODY)
    _seal(project, LEAF1)
    _commit(project, "two tests")
    _write(project, PROD, 'def greet() -> str:\n    return "Hi"\n')  # breaks both
    _declare_migrates(project, ORACLE1)
    snapshot = project.root / f"progress/issue_migrations_{IID}.json"
    before = snapshot.read_text() if snapshot.exists() else None
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(FAKE_CR)
    (bin_dir / "codex").chmod(0o755)
    seen = tmp_path / "prompt.txt"
    env = _gate_env(project, tmp_path, HARNESS_CODEX_MODE="auto", FAKE_PROMPT=str(seen),
                    PATH=f"{bin_dir}:{project.env['PATH']}")  # fmt: skip
    _run_env(project, env, "bash", "harness/review_with_codex.sh", "issue", IID)
    prompt = seen.read_text()
    tolerated, _, _ = _third(prompt)
    assert ORACLE1 in tolerated
    _, log1 = _first_log(prompt)
    assert f"{ORACLE1}::test_ac1" in log1 and f"{ORACLE1}::test_ac2" in log1, log1
    assert "every test" in _flat(prompt).lower()
    after = snapshot.read_text() if snapshot.exists() else None
    assert after == before
    assert _check(project).returncode != 0  # the issue seal still mismatches


# ── AC5c. Ineligible `migrates:` entries: no third run ───────────────────────


def _missing(_project: Installed) -> str:
    return "tests/acceptance/test_nope.py"


def _unsealed(project: Installed) -> str:
    rel = "tests/acceptance/test_extra.py"
    _write(project, rel, ORACLE1_BODY)
    return rel


def _changed(project: Installed) -> str:
    _write(project, ORACLE1, ORACLE1_BODY + "\n\ndef test_ac9() -> None:\n    assert True\n")
    return ORACLE1


INELIGIBLE = {"missing": _missing, "unsealed": _unsealed, "changed": _changed}


@pytest.mark.parametrize("with_valid", [False, True], ids=["alone", "with-valid"])
@pytest.mark.parametrize("kind", sorted(INELIGIBLE))
def test_an_ineligible_entry_gets_no_third_run(tmp_path: Path, kind: str, with_valid: bool) -> None:
    project = _inflight(tmp_path)
    _break_oracle(project)
    bad = INELIGIBLE[kind](project)
    entries = [bad] if not with_valid or bad == ORACLE1 else [ORACLE1, bad]
    _declare_migrates(project, *entries)
    prompt = _prompt(project, tmp_path, "issue", IID)
    assert _second_log(prompt)[0] != 0
    _no_third(prompt)
    named = [ln for ln in prompt.splitlines() if bad in ln and "migrat" in ln.lower()]
    assert named, prompt


def test_a_path_the_gate_cannot_exclude_exactly_gets_no_third_run(tmp_path: Path) -> None:
    """A path with a space would split in PYTEST_ADDOPTS: fail closed, no third run."""
    project = _inflight(tmp_path)
    _break_oracle(project)
    spaced = "tests/acceptance/test_ISS-14.1 copy.py"
    _write(project, spaced, ORACLE1_BODY)
    digest = hashlib.sha256((project.root / spaced).read_bytes()).hexdigest()
    with (project.root / f"progress/acceptance_{LEAF1}.sha256").open("a") as fh:
        fh.write(f"{digest}  {spaced}\n")
    _declare_migrates(project, ORACLE1, spaced)
    prompt = _prompt(project, tmp_path, "issue", IID)
    _no_third(prompt)
    assert any(spaced in ln and "exactly" in ln for ln in prompt.splitlines()), prompt


def test_an_inherited_makefiles_never_reaches_the_python_first_run(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    extra = tmp_path / "inherited.mk"
    extra.write_text(
        f"export PYTEST_ADDOPTS = {DESELECT_ALL.replace('--deselect ', '--ignore=')}\n"
    )
    prompt = _prompt(project, tmp_path, "issue", IID, MAKEFILES=str(extra))
    for rc, log in (_first_log(prompt), _second_log(prompt)):
        assert rc != 0
        assert f"FAILED {ORACLE1}::test_ac1" in log, log


def test_an_inherited_bash_env_never_reaches_the_first_run(tmp_path: Path) -> None:
    """BASH_ENV runs in the check's shell after the filter: it is dropped too."""
    project = _repro(tmp_path)
    rc_file = tmp_path / "bashenv.sh"
    rc_file.write_text(
        f"export PYTEST_ADDOPTS='{DESELECT_ALL.replace('--deselect ', '--ignore=')}'\n"
    )
    prompt = _prompt(project, tmp_path, "issue", IID, BASH_ENV=str(rc_file))
    for rc, log in (_first_log(prompt), _second_log(prompt)):
        assert rc != 0
        assert f"FAILED {ORACLE1}::test_ac1" in log, log


@pytest.mark.parametrize("name", ["GNUmakefile", "makefile"])
def test_c_another_makefile_name_gets_no_third_run(tmp_path: Path, name: str) -> None:
    project = _c_inflight(tmp_path, "template")
    _write(project, name, "include Makefile\n")
    prompt = _prompt(project, tmp_path, "issue", IID)
    assert _second_log(prompt)[0] != 0
    _no_third(prompt)


@pytest.mark.parametrize(
    "extra",
    [
        "check: TESTS += tests/unit/test_marker.c\n",
        "ifneq ($(MAKECMDGOALS),)\nendif\n",
        "TESTS += tests/unit/test_marker.c\n",
        "-include local.mk\n",
    ],
    ids=["target-specific", "makecmdgoals", "append", "include"],
)
def test_c_an_unverified_makefile_gets_no_third_run(tmp_path: Path, extra: str) -> None:
    project = _c_inflight(tmp_path, "template")
    path = project.root / "Makefile"
    head, tail = path.read_text().split("# Harness targets", 1)
    path.write_text(head + extra + "# Harness targets" + tail)
    prompt = _prompt(project, tmp_path, "issue", IID)
    _no_third(prompt)


def test_an_inherited_env_function_never_reaches_the_first_run(tmp_path: Path) -> None:
    """An exported `env` function that drops nothing: the gate runs an external env."""
    project = _repro(tmp_path)
    addopts = DESELECT_ALL.replace("--deselect ", "--ignore=")
    env = _gate_env(project, tmp_path)
    env["BASH_FUNC_env%%"] = f"() {{ PYTEST_ADDOPTS='{addopts}' command env \"$@\"; }}"
    res = _run_env(project, env, "bash", "harness/review_with_codex.sh", "issue", IID)
    prompt = res.stdout.split("--- prompt ---", 1)[1]
    for rc, log in (_first_log(prompt), _second_log(prompt)):
        assert rc != 0
        assert f"FAILED {ORACLE1}::test_ac1" in log, log


def test_c_a_test_list_conditional_gets_no_third_run(tmp_path: Path) -> None:
    """One global TESTS :=, but verify depends on it beyond the list itself."""
    project = _c_inflight(tmp_path, "template")
    path = project.root / "Makefile"
    text = path.read_text().replace(
        "verify: lint type test\n",
        "verify: lint type test $(if $(filter tests/acceptance/%,$(TESTS)),extra)\n"
        ".PHONY: extra\nextra:\n\t@exit 1\n",
    )
    path.write_text(text)
    prompt = _prompt(project, tmp_path, "issue", IID)
    _no_third(prompt)


@pytest.mark.parametrize(
    "cmd",
    [
        "f() { :; }; export -f f; ./harness/review_with_codex.sh issue ISS-14",
        "declare -fx read",
        "env 'BASH_FUNC_read%%=() { return 1; }' ./harness/review_with_codex.sh issue ISS-14",
    ],
)
def test_the_bash_guard_blocks_exported_functions(tmp_path: Path, cmd: str) -> None:
    project = install_project(tmp_path, "python")
    env = {**project.env, "CLAUDE_PROJECT_DIR": str(project.root)}
    res = subprocess.run(
        ["bash", "harness/guard_bash.sh"], cwd=project.root, env=env, text=True,
        input=json.dumps(bash_payload(cmd)), capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 2, (cmd, res.stdout, res.stderr)
    assert "exporting shell functions" in res.stderr


def test_an_inherited_read_function_never_reaches_the_first_run(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    env = _gate_env(
        project, tmp_path, PYTEST_ADDOPTS=DESELECT_ALL.replace("--deselect ", "--ignore=")
    )
    env["BASH_FUNC_read%%"] = "() { return 1; }"
    res = _run_env(project, env, "bash", "harness/review_with_codex.sh", "issue", IID)
    prompt = res.stdout.split("--- prompt ---", 1)[1]
    for rc, log in (_first_log(prompt), _second_log(prompt)):
        assert rc != 0
        assert f"FAILED {ORACLE1}::test_ac1" in log, log


def test_c_a_shell_condition_on_the_test_list_gets_no_third_run(tmp_path: Path) -> None:
    """The T path appears outside the expanded list: the comparison fails closed."""
    project = _c_inflight(tmp_path, "template")
    path = project.root / "Makefile"
    text = path.read_text().replace(
        "verify: lint type test\n",
        "verify: lint type test\n"
        '\t@if [ -n "$(filter tests/acceptance/%,$(TESTS))" ]; then exit 1; fi\n',
    )
    path.write_text(text)
    _no_third(_prompt(project, tmp_path, "issue", IID))


def test_the_bash_guard_blocks_bash_env(tmp_path: Path) -> None:
    project = install_project(tmp_path, "python")
    env = {**project.env, "CLAUDE_PROJECT_DIR": str(project.root)}
    cmd = "BASH_ENV=/tmp/x.sh ./harness/review_with_codex.sh issue ISS-14"
    res = subprocess.run(
        ["bash", "harness/guard_bash.sh"], cwd=project.root, env=env, text=True,
        input=json.dumps(bash_payload(cmd)), capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 2, res.stdout + res.stderr
    assert "BASH_ENV" in res.stderr


def test_c_a_condition_on_the_whole_test_list_gets_no_third_run(tmp_path: Path) -> None:
    """A shell condition on "$(TESTS)" itself: only the whitelisted uses pass."""
    project = _c_inflight(tmp_path, "template")
    path = project.root / "Makefile"
    text = path.read_text().replace(
        "verify: lint type test\n",
        'verify: lint type test\n\t@case "$(TESTS)" in *acceptance*) exit 1 ;; esac\n',
    )
    path.write_text(text)
    _no_third(_prompt(project, tmp_path, "issue", IID))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("verify: lint type test\n",
         "verify: lint type test\n\t@if grep -q RAN $(TESTS); then exit 1; fi\n"),
        ("# Harness targets",
         "check: $(if $(filter tests/acceptance/%,$(TESTS)),extra)\n"
         ".PHONY: extra\nextra:\n\t@exit 1\n# Harness targets"),
    ],
    ids=["grep-args", "check-prerequisite"],
)  # fmt: skip
def test_c_other_uses_of_the_test_list_get_no_third_run(tmp_path: Path, old: str, new: str) -> None:
    project = _c_inflight(tmp_path, "template")
    path = project.root / "Makefile"
    path.write_text(path.read_text().replace(old, new, 1))
    _no_third(_prompt(project, tmp_path, "issue", IID))


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("Makefile", None),
        ("tests/conftest.py", "import os\n"),
        ("pytest.ini", "[pytest]\n"),
    ],
    ids=["makefile-changed", "conftest-new", "pytest-ini-new"],
)
def test_an_uncommitted_test_configuration_gets_no_third_run(
    tmp_path: Path, rel: str, text: str | None
) -> None:
    project = _repro(tmp_path)
    path = project.root / rel
    path.write_text(path.read_text() + "\n" if text is None else text)
    _no_third(_prompt(project, tmp_path, "issue", IID))


def test_an_ignored_conftest_gets_no_third_run(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    with (project.root / ".gitignore").open("a") as fh:
        fh.write("tests/conftest.py\n")
    _commit(project, "ignore conftest")
    _write(project, "tests/conftest.py", "import os\n")
    _no_third(_prompt(project, tmp_path, "issue", IID))


def test_a_conftest_inside_the_venv_keeps_the_third_run(tmp_path: Path) -> None:
    """Third-party packages may ship a conftest.py: .venv is not test configuration."""
    project = _repro(tmp_path)
    _write(project, ".venv/lib/pkg/conftest.py", "import os\n")
    tolerated, rc3, log3 = _third(_prompt(project, tmp_path, "issue", IID))
    assert tolerated == {OWN, ORACLE1}
    assert rc3 == 0, log3


def test_an_assume_unchanged_makefile_gets_no_third_run(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    _sh(project, "git", "update-index", "--assume-unchanged", "Makefile")
    path = project.root / "Makefile"
    path.write_text(path.read_text() + "\n")
    _no_third(_prompt(project, tmp_path, "issue", IID))


def test_c_only_tolerated_tests_still_get_a_third_run(tmp_path: Path) -> None:
    """With every test in T the reduced list is empty: `[ -z "" ]` is still allowed."""
    project = _c_inflight(tmp_path, "template")
    _sh(project, "git", "rm", "-q", "tests/unit/test_marker.c")
    _commit(project, "no unit test")
    tolerated, rc3, log3 = _third(_prompt(project, tmp_path, "issue", IID))
    assert tolerated == {C_OWN, C_ORACLE1}
    assert rc3 == 0, log3


# ── AC6. Other prompts unchanged ─────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["plan", "code", "doc"])
def test_subtask_gates_get_no_third_run(tmp_path: Path, mode: str) -> None:
    project = _repro(tmp_path)
    res = _gate(project, tmp_path, mode, LEAF2)
    assert EXPECT not in res.stdout + res.stderr


def test_issue_draft_gets_no_third_run(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    _write(project, "progress/issue_draft.md", "---\ntitle: t\n---\n## Problem\np.\n")
    assert EXPECT not in _prompt(project, tmp_path, "issue-draft")


@pytest.mark.parametrize("gate", [("phase", "01"), ("roadmap",)], ids=["phase", "roadmap"])
def test_phase_and_roadmap_get_no_third_run(tmp_path: Path, gate: tuple[str, ...]) -> None:
    project = _phase_project(tmp_path)
    _write(project, "tests/acceptance/test_1.1.2.py", OWN_BODY.replace("demo.greet", "os"))
    _phase_seal_mismatch(project)
    res = _gate(project, tmp_path, *gate)
    assert EXPECT not in res.stdout + res.stderr


# ── AC7. C projects: the third run overrides TESTS, no Makefile change ───────

C_HDR = "#ifndef DEMO_GREET_H\n#define DEMO_GREET_H\n\nint greet(void);\n\n#endif\n"
C_SRC = '#include "demo/greet.h"\n\nint greet(void)\n{\n\treturn (%d);\n}\n'
C_TEST = (
    '#include <stdio.h>\n#include "demo/greet.h"\n\nint main(void)\n{\n'
    '\tprintf("RAN %s\\n");\n\treturn (greet() == %d ? 0 : 1);\n}\n'
)
C_MARKER = '#include <stdio.h>\n\nint main(void)\n{\n\tprintf("MARKER-RAN\\n");\n\treturn (0);\n}\n'
C_SPEC = (
    "# {sid} Sub\n- status: {status}\n- layer: domain\n- depends: {depends}\n"
    "- files:\n{files}"
    "- acceptance:\n  - AC1 | works | tests/acceptance/test_{sid}.c::main\n"
)
C_ORACLE1 = f"tests/acceptance/test_{LEAF1}.c"
C_OWN = f"tests/acceptance/test_{LEAF2}.c"

# The codexion shape: TESTS a plain `:=`, a UNIT filter on it, build/ objects, test: type.
CODEXION_RULES = """\
.PHONY: lint type test verify env-check
CC      := cc
WARN    := -Wall -Wextra -Werror
CFLAGS  := $(WARN) -Iinclude $(EXTRA_CFLAGS)
STATUS  := ./harness/status.sh
BUILD   := build
SRC      := $(shell find src -name '*.c' 2>/dev/null)
HDR      := $(shell find include -name '*.h' 2>/dev/null)
OBJ      := $(SRC:%.c=$(BUILD)/obj/%.o)
TESTS    := $(shell find tests -name 'test_*.c' 2>/dev/null)
UNIT     := $(filter tests/unit/%,$(TESTS))

$(BUILD)/obj/%.o: %.c $(HDR)
\t@mkdir -p $(dir $@)
\t$(CC) $(CFLAGS) -c $< -o $@

lint:
\t@$(STATUS) skip "norminette not installed"

type: $(OBJ)
\t@$(STATUS) ok "compiles clean"

test: type
\t@if [ -z "$(TESTS)" ]; then $(STATUS) ok "no tests yet"; else \\
\t  for t in $(TESTS); do \\
\t    bin=$(BUILD)/$${t%.c}.bin; mkdir -p $$(dirname $$bin); \\
\t    $(CC) $(CFLAGS) $$t $(OBJ) -o $$bin || { $(STATUS) fail "$$t (build)"; exit 1; }; \\
\t    ./$$bin || { $(STATUS) fail "$$t"; exit 1; }; \\
\t  done; \\
\t  $(STATUS) ok "tests passed"; \\
\tfi

verify: lint type test

env-check:
\t@command -v $(CC) >/dev/null 2>&1 || { $(STATUS) fail "$(CC) not found"; exit 1; }

"""


def _codexion_makefile(project: Installed) -> None:
    path = project.root / "Makefile"
    tail = path.read_text().split("# Harness targets", 1)[1]
    path.write_text(CODEXION_RULES + "# Harness targets" + tail)
    with (project.root / ".gitignore").open("a") as fh:
        fh.write("build/\n")


def _c_spec(sid: str, status: str, depends: str, files: list[str]) -> str:
    listed = "".join(f"  - {f}\n" for f in files)
    return C_SPEC.format(sid=sid, status=status, depends=depends, files=listed)


def _c_inflight(tmp: Path, shape: str) -> Installed:
    """The C twin of _inflight: ISS-14.1 done (oracle expects greet() == 1), ISS-14.2
    in_progress (declares src/greet.c, own oracle expects 3), a unit test that prints
    a marker; then ISS-14.2 changes greet() to 2 and declares the oracle in migrates:."""
    project = install_project(tmp, "c")
    if shape == "codexion":
        _codexion_makefile(project)
    _sh(project, "git", "checkout", "-qb", f"fix/{IID}-regate")
    _write(project, "include/demo/greet.h", C_HDR)
    _write(project, "src/greet.c", C_SRC % 1)
    _write(project, "tests/unit/test_marker.c", C_MARKER)
    _write(project, f"docs/plan/issues/{IID}-regate.md", ISSUE_HEADER)
    _write(
        project,
        f"{ISSUE_DIR}/{LEAF1}-s.md",
        _c_spec(LEAF1, "done", "none", ["include/demo/greet.h"]),
    )
    _write(project, f"{ISSUE_DIR}/{LEAF2}-s.md", _c_spec(LEAF2, "in_progress", LEAF1,
                                                         ["src/greet.c"]))  # fmt: skip
    _resync(project)
    _verdict(project, f"issue_review_{IID}.md", "ISSUE_APPROVED")
    _seal(project, "issue", IID)
    _write(project, C_ORACLE1, C_TEST % (LEAF1, 1))
    _verdict(project, f"plan_review_{LEAF1}.md", "PLAN_APPROVED")
    _verdict(project, f"review_{LEAF1}.md", "APPROVED")
    _write(project, f"docs/reference/{LEAF1}-s.md", REFERENCE.format(sid=LEAF1))
    _seal(project, LEAF1)
    _write(project, C_OWN, C_TEST % (LEAF2, 3))
    _verdict(project, f"plan_review_{LEAF2}.md", "PLAN_APPROVED")
    _seal(project, LEAF2)
    _commit(project, "14.2 in progress")
    _write(project, "src/greet.c", C_SRC % 2)
    spec = f"{ISSUE_DIR}/{LEAF2}-s.md"
    _edit(project, spec, "- acceptance:", f"- migrates:\n  - {C_ORACLE1}\n- acceptance:")
    _resync(project)
    return project


@pytest.mark.parametrize("shape", ["template", "codexion"])
def test_c_third_run_overrides_tests_without_a_makefile_change(tmp_path: Path, shape: str) -> None:
    project = _c_inflight(tmp_path, shape)
    makefile = (project.root / "Makefile").read_text()
    prompt = _prompt(project, tmp_path, "issue", IID)
    rc1, log1 = _first_log(prompt)
    assert rc1 != 0 and (C_ORACLE1 in log1 or C_OWN in log1), log1
    rc2, log2 = _second_log(prompt)
    assert rc2 != 0, log2
    tolerated, rc3, log3 = _third(prompt)
    assert tolerated == {C_OWN, C_ORACLE1}
    assert rc3 == 0, log3
    assert "MARKER-RAN" in log3, log3
    assert f"RAN {LEAF1}" not in log3 and f"RAN {LEAF2}" not in log3, log3
    assert (project.root / "Makefile").read_text() == makefile


C_INHERITED = {
    "MAKEFLAGS": "TESTS=tests/unit/test_marker.c",
    "GNUMAKEFLAGS": "TESTS=tests/unit/test_marker.c",
    "TESTS": "tests/unit/test_marker.c",
    "MAKEFILES": "override TESTS := tests/unit/test_marker.c\n",
}


def _c_inherited(tmp: Path, var: str) -> str:
    """MAKEFILES names a makefile make loads first: write it, return its path."""
    if var != "MAKEFILES":
        return C_INHERITED[var]
    extra = tmp / "inherited.mk"
    extra.write_text(C_INHERITED[var])
    return str(extra)


@pytest.mark.parametrize("var", sorted(C_INHERITED))
def test_c_first_run_ignores_an_inherited_tests_override(tmp_path: Path, var: str) -> None:
    project = _c_inflight(tmp_path, "template")
    prompt = _prompt(project, tmp_path, "issue", IID, **{var: _c_inherited(tmp_path, var)})
    for rc, log in (_first_log(prompt), _second_log(prompt)):
        assert rc != 0
        assert C_ORACLE1 in log or C_OWN in log, log


@pytest.mark.parametrize("var", sorted(C_INHERITED))
def test_c_fresh_stop_ignores_an_inherited_tests_override(tmp_path: Path, var: str) -> None:
    project = _c_inflight(tmp_path, "template")
    _edit(project, f"{ISSUE_DIR}/{LEAF2}-s.md", f"- migrates:\n  - {C_ORACLE1}\n", "")
    _resync(project)
    env = {**project.env, "CLAUDE_PROJECT_DIR": str(project.root), var: _c_inherited(tmp_path, var)}
    res = subprocess.run(
        ["bash", "harness/stop_gate.sh"], cwd=project.root, env=env, text=True,
        input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
        capture_output=True, check=False, timeout=900,
    )  # fmt: skip
    # #55: advisory Stop: allows the stop, warns, never builds nor runs the C tests.
    assert res.returncode == 0, res.stdout + res.stderr
    assert "no green full check for this state" in res.stdout + res.stderr
    assert C_ORACLE1 not in res.stdout + res.stderr


def test_c_prompt_requires_the_full_accounting_of_a_file_whose_log_stops_early(
    tmp_path: Path,
) -> None:
    """A C test binary stops at its first failure: the prompt asks for every test."""
    project = _c_inflight(tmp_path, "template")
    prompt = _prompt(project, tmp_path, "issue", IID)
    tolerated, _, _ = _third(prompt)
    for rel in tolerated:
        assert rel in prompt
    assert "every test" in _flat(prompt).lower()


# ── AC8. make check without the variables is unchanged ───────────────────────


def test_make_check_without_the_variables_is_unchanged(tmp_path: Path) -> None:
    project = _repro(tmp_path)
    res = _check(project)
    assert res.returncode != 0
    assert "[ASSUMED]" not in res.stdout + res.stderr
    assert f"FAILED {ORACLE1}::test_ac1" in res.stdout + res.stderr
