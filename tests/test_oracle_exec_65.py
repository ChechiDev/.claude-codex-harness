"""Oracle execution integrity (issue #65, I4b) — WARN rollout.

Sealed oracles only prove something if the normal check selects and executes them.
The launcher reads node-level results (pytest junit, the C adapter's run log) and
warns when a sealed oracle of a done/in_progress/baseline leaf did not run, was
skipped or xfailed (done), or ran from a stale source. The selection inputs are
inventoried and compared with a sealed I4 baseline; without that baseline nothing
of this is active. Every finding is a [WARN] with a remedy and exit 0.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest
from conftest import LEAF_ID, Project

ORACLE = f"tests/acceptance/test_{LEAF_ID}.py"
PASSING = "def test_ok() -> None:\n    assert True\n"
FAILING = "def test_red() -> None:\n    assert False\n"
# Dotted oracle names (test_1.1.1.py) need importlib mode, as harness projects set.
PYTEST = f"{sys.executable} -m pytest -q -p no:cacheprovider --import-mode=importlib"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _oracle(project: Project, text: str = PASSING, rel: str = ORACLE) -> None:
    project.write(rel, text)
    project.write(f"progress/acceptance_{LEAF_ID}.sha256", f"{_sha(text)}  {rel}\n")


def _py(
    project: Project, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {"CLAUDE_PROJECT_DIR": str(project.root), "HARNESS_PYTEST_CMD": PYTEST}
    return subprocess.run(
        ["python3", str(project.root / "harness" / "_oracle.py"), *args],
        cwd=project.root,
        text=True,
        capture_output=True,
        env={**_clean_os_env(), **base, **(env or {})},
        check=False,
    )


def _clean_os_env() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "PYTEST_"))}


def _baseline(project: Project) -> None:
    project.commit_all("pre-rule")
    res = _py(project, "baseline-create", "--from", "HEAD")
    assert res.returncode == 0, res.stdout + res.stderr
    res = _py(project, "baseline-seal")
    assert res.returncode == 0, res.stdout + res.stderr


def _check(
    project: Project, env: dict[str, str] | None = None, *args: str
) -> subprocess.CompletedProcess[str]:
    return project.run(
        "run_check.sh", None, *args, env={"HARNESS_CHECK_CMD": f"{PYTEST} tests", **(env or {})}
    )


def _warns(res: subprocess.CompletedProcess[str]) -> list[str]:
    return [ln for ln in (res.stdout + res.stderr).splitlines() if ln.startswith("[WARN]")]


def _oracle_warns(res: subprocess.CompletedProcess[str]) -> list[str]:
    return [w for w in _warns(res) if "oracle" in w or "selection" in w]


def _selection_warns(res: subprocess.CompletedProcess[str]) -> list[str]:
    return [w for w in _warns(res) if "selection" in w]


def _done(project: Project) -> None:
    project.set_leaf("done")


# ── activation ───────────────────────────────────────────────────────────────


def test_without_baseline_nothing_is_active(project: Project) -> None:
    _done(project)
    _oracle(project, FAILING)  # would warn if active
    project.commit_all("oracle")
    res = _check(project)
    assert _oracle_warns(res) == [], res.stdout + res.stderr
    assert "I4b inactive" in res.stdout + res.stderr


def test_done_oracle_passing_gives_no_warning(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    res = _check(project)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _oracle_warns(res) == [], res.stdout + res.stderr


def test_baseline_tamper_warns(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    path = project.root / "progress/oracle_baseline.json"
    data = json.loads(path.read_text())
    data["selection"] = {}
    path.write_text(json.dumps(data))
    res = _check(project)
    assert any("baseline" in w for w in _warns(res)), res.stdout + res.stderr


# ── execution evidence ───────────────────────────────────────────────────────


def test_exit5_with_sealed_oracle_warns_and_rc_unchanged(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    res = _check(project, {"HARNESS_CHECK_CMD": f"{PYTEST} tests/nothing_here; exit 0"})
    assert res.returncode == 0
    assert any("not executed" in w and ORACLE in w for w in _warns(res)), res.stdout + res.stderr


def test_conftest_deselection_warns(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    project.write("conftest.py", 'collect_ignore_glob = ["tests/acceptance/*"]\n')
    res = _check(project)
    warns = _warns(res)
    assert any("not executed" in w for w in warns), res.stdout + res.stderr
    assert any("selection" in w and "conftest.py" in w for w in warns), res.stdout + res.stderr


def test_addopts_k_deselection_warns(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    project.write("pytest.ini", "[pytest]\naddopts = -k 'not test_ok'\n")
    res = _check(project)
    warns = _warns(res)
    assert any("not executed" in w for w in warns), res.stdout + res.stderr
    assert any("selection" in w and "pytest.ini" in w for w in warns), res.stdout + res.stderr


def test_hostile_env_does_not_deselect(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    res = _check(project, {"PYTEST_ADDOPTS": "-k nothing"})
    assert not any("not executed" in w for w in _warns(res)), res.stdout + res.stderr


def test_skipped_and_xfail_oracle_of_done_leaf_warn(project: Project) -> None:
    _done(project)
    text = (
        "import pytest\n\n@pytest.mark.skip\ndef test_a() -> None:\n    pass\n\n"
        "@pytest.mark.xfail\ndef test_b() -> None:\n    assert False\n"
    )
    _oracle(project, text)
    _baseline(project)
    res = _check(project)
    warns = _warns(res)
    assert any("skipped" in w and "test_a" in w for w in warns), res.stdout + res.stderr
    assert any("skipped" in w and "test_b" in w for w in warns), res.stdout + res.stderr


def test_in_progress_failing_oracle_executed_is_fine(project: Project) -> None:
    _oracle(project, FAILING)
    _baseline(project)
    res = _check(project)
    assert not any("not executed" in w for w in _warns(res)), res.stdout + res.stderr
    assert not any("did not pass" in w for w in _warns(res)), res.stdout + res.stderr


def test_missing_sealed_oracle_file_warns(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    (project.root / ORACLE).unlink()
    res = _check(project)
    assert any("missing" in w and ORACLE in w for w in _warns(res)), res.stdout + res.stderr


# ── C adapter ────────────────────────────────────────────────────────────────


def _c_oracle(project: Project) -> str:
    rel = f"tests/acceptance/test_{LEAF_ID}.c"
    text = "int main(void) { return 0; }\n"
    _oracle(project, text, rel)
    return rel


def _evidence(project: Project, clog: str) -> subprocess.CompletedProcess[str]:
    log = project.root / ".git" / "clog"
    log.write_text(clog)
    return _py(project, "evidence", "--clog", str(log))


def test_c_empty_tests_warns(project: Project) -> None:
    _done(project)
    _c_oracle(project)
    _baseline(project)
    res = _evidence(project, "")
    assert any("not executed" in w for w in _warns(res)), res.stdout + res.stderr


def test_c_stale_binary_source_warns_and_fresh_is_ok(project: Project) -> None:
    _done(project)
    rel = _c_oracle(project)
    _baseline(project)
    res = _evidence(project, f"{rel}\t{'0' * 64}\t0\n")
    assert any("stale" in w for w in _warns(res)), res.stdout + res.stderr
    sha = hashlib.sha256((project.root / rel).read_bytes()).hexdigest()
    res = _evidence(project, f"{rel}\t{sha}\t0\n")
    assert _oracle_warns(res) == [], res.stdout + res.stderr


def test_c_without_log_cannot_establish(project: Project) -> None:
    _done(project)
    _c_oracle(project)
    _baseline(project)
    res = _py(project, "evidence")
    assert any("cannot establish" in w for w in _warns(res)), res.stdout + res.stderr


def test_c_template_writes_the_run_log() -> None:
    from conftest import TOOLKIT

    text = (TOOLKIT / "templates-c" / "Makefile").read_text()
    assert "HARNESS_ORACLE_CLOG" in text


# ── re-gate third run ────────────────────────────────────────────────────────


def test_expect_red_omits_only_the_validated_set(project: Project) -> None:
    # The gate's third run passes its validated set T to _oracle.py itself
    # (review_with_codex.sh third_run_evidence): only T may be missing.
    _done(project)
    _oracle(project)
    other = "tests/acceptance/test_1.1.2.py"
    project.write(other, PASSING)
    _baseline(project)
    junit = project.root / ".git" / "empty.xml"
    junit.write_text('<testsuites><testsuite name="pytest" tests="0"/></testsuites>')
    res = _py(project, "evidence", "--junit", str(junit), "--expect-red", ORACLE)
    assert not any("not executed" in w for w in _warns(res)), res.stdout + res.stderr
    res = _py(project, "evidence", "--junit", str(junit), "--expect-red", other)
    assert any("not executed" in w and ORACLE in w for w in _warns(res)), res.stdout


def test_third_run_wiring_passes_the_validated_set() -> None:
    from conftest import TOOLKIT

    text = (TOOLKIT / "lib" / "review_with_codex.sh").read_text()
    # C and Python branches, each with and without its evidence file.
    assert text.count('third_run_evidence "$log" "$label"') == 4


def test_launcher_never_takes_expect_red_from_env(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    empty = f"{PYTEST} tests/unit_none; exit 0"
    res = _check(
        project, {"HARNESS_CHECK_CMD": empty, "HARNESS_EXPECT_RED": ORACLE}, "--assume", "x"
    )
    warns = _warns(res)
    assert any("HARNESS_EXPECT_RED" in w for w in warns), res.stdout + res.stderr
    assert any("not executed" in w and ORACLE in w for w in warns), res.stdout + res.stderr


def test_inherited_expect_red_is_ignored_and_reported(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    empty = f"{PYTEST} tests/unit_none; exit 0"
    res = _check(project, {"HARNESS_CHECK_CMD": empty, "HARNESS_EXPECT_RED": ORACLE})
    warns = _warns(res)
    assert any("HARNESS_EXPECT_RED" in w for w in warns), res.stdout + res.stderr
    assert any("not executed" in w and ORACLE in w for w in warns), res.stdout + res.stderr


def test_collection_error_is_not_execution(project: Project) -> None:
    _oracle(project, "import does_not_exist_xyz\n" + FAILING)
    _baseline(project)
    res = _check(project)
    assert any("not executed" in w for w in _warns(res)), res.stdout + res.stderr


def test_inactive_check_is_not_instrumented(project: Project) -> None:
    res = _check(project, {"HARNESS_CHECK_CMD": 'echo "addopts=[$PYTEST_ADDOPTS]"'})
    assert "addopts=[]" in res.stdout, res.stdout + res.stderr


# ── plugins ──────────────────────────────────────────────────────────────────


def _plugin(project: Project, body: str) -> None:
    site = project.root / ".venv/lib/python3.12/site-packages"
    project.write(
        str((site / "fakeplug-1.0.dist-info/entry_points.txt").relative_to(project.root)),
        "[pytest11]\nfakeplug = fakeplug_mod\n",
    )
    project.write(str((site / "fakeplug_mod.py").relative_to(project.root)), body)


def test_plugin_code_change_under_same_name_is_detected(project: Project) -> None:
    _done(project)
    _oracle(project)
    _plugin(project, "x = 1\n")
    _baseline(project)
    res = _py(project, "evidence")
    assert _selection_warns(res) == [], res.stdout + res.stderr
    _plugin(project, "x = 2\n")
    res = _py(project, "evidence")
    assert any("plugin:fakeplug" in w for w in _warns(res)), res.stdout + res.stderr


def test_unresolvable_plugin_warns(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    site = project.root / ".venv/lib/python3.12/site-packages"
    project.write(
        str((site / "ghost-1.0.dist-info/entry_points.txt").relative_to(project.root)),
        "[pytest11]\nghost = ghost_missing\n",
    )
    res = _py(project, "evidence")
    assert any("ghost" in w and "unresolved" in w for w in _warns(res)), res.stdout + res.stderr


# ── red evidence at the plan gate ────────────────────────────────────────────


# A real targeted pytest run per call: heavy (#68).
@pytest.mark.e2e
def test_red_record_and_code_gate_check(project: Project) -> None:
    _oracle(project, FAILING)
    _baseline(project)
    _py(project, "red-drop", LEAF_ID)
    res = _py(project, "red-record", LEAF_ID)
    assert res.returncode == 0, res.stdout + res.stderr
    rec = json.loads((project.root / f".git/harness-oracle/red_{LEAF_ID}.json").read_text())
    assert rec["collected"] and rec["failing"] == rec["collected"]
    assert _warns(_py(project, "red-check", LEAF_ID)) == []
    # Declared files may change.
    project.write("src/pkg/domain/user.py", "x = 1\n")
    assert _warns(_py(project, "red-check", LEAF_ID)) == []
    # Selection inputs may not.
    project.write("conftest.py", "# changed\n")
    assert any("selection" in w for w in _warns(_py(project, "red-check", LEAF_ID)))
    (project.root / "conftest.py").unlink()
    # The oracle may not.
    _oracle(project, FAILING + "\n")
    assert any("oracle" in w for w in _warns(_py(project, "red-check", LEAF_ID)))


def test_code_gate_without_record_warns(project: Project) -> None:
    _oracle(project, FAILING)
    _baseline(project)  # in_progress without a record: exempt until its next plan gate
    assert _warns(_py(project, "red-check", LEAF_ID)) == []
    _py(project, "red-drop", LEAF_ID)  # the plan gate ran again (and rejected)
    res = _py(project, "red-check", LEAF_ID)
    assert res.returncode == 0
    assert any("no red evidence" in w for w in _warns(res)), res.stdout + res.stderr


# A real targeted pytest run per call: heavy (#68).
@pytest.mark.e2e
def test_red_record_of_passing_oracle_warns(project: Project) -> None:
    _oracle(project, PASSING)
    _baseline(project)
    _py(project, "red-drop", LEAF_ID)
    res = _py(project, "red-record", LEAF_ID)
    assert res.returncode == 0
    assert any("did not fail" in w for w in _warns(res)), res.stdout + res.stderr
    # The code gate does not accept that record as red evidence either.
    res = _py(project, "red-check", LEAF_ID)
    assert any("collected and failing" in w for w in _warns(res)), res.stdout + res.stderr


# A real targeted pytest run per call: heavy (#68).
@pytest.mark.e2e
def test_rerun_replaces_the_record(project: Project) -> None:
    _oracle(project, FAILING)
    _baseline(project)
    _py(project, "red-record", LEAF_ID)
    path = project.root / f".git/harness-oracle/red_{LEAF_ID}.json"
    first = json.loads(path.read_text())["run_id"]
    _py(project, "red-record", LEAF_ID)
    assert json.loads(path.read_text())["run_id"] != first


def test_in_progress_leaf_exempt_in_baseline(project: Project) -> None:
    _oracle(project, FAILING)
    project.commit_all("pre-rule")
    _py(project, "baseline-create", "--from", "HEAD")
    _py(project, "baseline-seal")
    data = json.loads((project.root / "progress/oracle_baseline.json").read_text())
    assert LEAF_ID in data["exempt_in_progress"]
    assert _warns(_py(project, "red-check", LEAF_ID)) == []


# ── contract flow ────────────────────────────────────────────────────────────


def _review(project: Project, verdict: str, digest: str) -> None:
    project.write(
        "progress/oracle_contract_review.md",
        f"# Contract\n\n**Verdict:** {verdict}\n\ndiff: {digest}\n",
    )


def test_contract_flow_reseals_bound_to_diff(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    project.write("pytest.ini", "[pytest]\nmarkers = slow\n")
    assert any("selection" in w for w in _warns(_py(project, "evidence")))
    res = _py(project, "contract-propose")
    assert res.returncode == 0, res.stdout + res.stderr
    digest = json.loads((project.root / "progress/oracle_contract.json").read_text())["diff"]
    _review(project, "APPROVED", digest)
    res = _py(project, "contract-approve", digest)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _selection_warns(_py(project, "evidence")) == []


def test_contract_change_after_approval_invalidates(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    project.write("pytest.ini", "[pytest]\nmarkers = slow\n")
    _py(project, "contract-propose")
    digest = json.loads((project.root / "progress/oracle_contract.json").read_text())["diff"]
    _review(project, "APPROVED", digest)
    project.write("pytest.ini", "[pytest]\naddopts = -k nothing\n")
    res = _py(project, "contract-approve", digest)
    assert res.returncode == 0
    assert any("refused" in w for w in _warns(res)), res.stdout + res.stderr
    assert any("selection" in w for w in _warns(_py(project, "evidence")))


def test_contract_tampered_proposal_is_refused(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    project.write("pytest.ini", "[pytest]\nmarkers = slow\n")
    _py(project, "contract-propose")
    path = project.root / "progress/oracle_contract.json"
    prop = json.loads(path.read_text())
    _review(project, "APPROVED", prop["diff"])
    prop["to"]["pytest.ini"] = "0" * 64  # swap what gets sealed, keep the approved hash
    path.write_text(json.dumps(prop))
    res = _py(project, "contract-approve", prop["diff"])
    assert any("refused" in w for w in _warns(res)), res.stdout + res.stderr
    assert any("selection" in w for w in _warns(_py(project, "evidence")))


def test_contract_rejected_or_other_hash_refused(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    project.write("pytest.ini", "[pytest]\nmarkers = slow\n")
    _py(project, "contract-propose")
    digest = json.loads((project.root / "progress/oracle_contract.json").read_text())["diff"]
    _review(project, "CHANGES_REQUESTED", digest)
    res = _py(project, "contract-approve", digest)
    assert res.returncode == 0 and any("refused" in w for w in _warns(res))
    _review(project, "APPROVED", "0" * 64)
    res = _py(project, "contract-approve", digest)
    assert res.returncode == 0 and any("refused" in w for w in _warns(res))
    assert any("selection" in w for w in _warns(_py(project, "evidence")))


# ── guard ────────────────────────────────────────────────────────────────────


def _guard(project: Project, rel: str) -> subprocess.CompletedProcess[str]:
    from conftest import write_payload

    return project.run("guard_write.sh", write_payload(project.root / rel))


def test_guard_warns_on_selection_input_edit_when_active(project: Project) -> None:
    _done(project)
    _oracle(project)
    _baseline(project)
    for rel in ("conftest.py", "tests/conftest.py", "pytest.ini", "Makefile", "package.json"):
        res = _guard(project, rel)
        assert res.returncode == 0, (rel, res.stdout, res.stderr)
        assert "selection input" in res.stdout, (rel, res.stdout)


def test_guard_silent_without_baseline(project: Project) -> None:
    res = _guard(project, "conftest.py")
    assert res.returncode == 0
    assert "selection input" not in res.stdout


def test_baseline_and_contract_review_are_codex_owned(project: Project) -> None:
    for rel in ("progress/oracle_baseline.json", "progress/oracle_contract_review.md"):
        res = _guard(project, rel)
        assert res.returncode == 2, (rel, res.stdout, res.stderr)


def test_claude_cannot_run_the_oracle_sealers(project: Project) -> None:
    from conftest import bash_payload

    for cmd in (
        "python3 harness/_oracle.py baseline-seal",
        "python3 harness/_oracle.py contract-approve abc",
    ):
        res = project.run("guard_bash.sh", bash_payload(cmd))
        assert res.returncode == 2, (cmd, res.stdout, res.stderr)


def test_variable_make_include_is_resolved_or_reported(project: Project) -> None:
    project.write("Makefile", "CFG := extra.mk\ninclude $(CFG)\ncheck:\n\t@true\n")
    project.write("extra.mk", "X := 1\n")
    inv = json.loads(_py(project, "inventory").stdout)
    assert "extra.mk" in inv, inv


def test_project_plugin_module_change_is_detected(project: Project) -> None:
    _done(project)
    _oracle(project)
    project.write("conftest.py", 'pytest_plugins = ["myplug"]\n')
    project.write("myplug.py", "x = 1\n")
    _baseline(project)
    assert _selection_warns(_py(project, "evidence")) == []
    project.write("myplug.py", "x = 2\n")
    res = _py(project, "evidence")
    assert any("plugin-module:myplug" in w for w in _warns(res)), res.stdout + res.stderr
    res = _guard(project, "myplug.py")
    assert "selection input" in res.stdout, res.stdout


def test_multiline_pytest_plugins_is_inventoried(project: Project) -> None:
    project.write("conftest.py", 'pytest_plugins = (\n    "myplug",\n)\n')
    project.write("myplug.py", "x = 1\n")
    inv = json.loads(_py(project, "inventory").stdout)
    assert "plugin-module:myplug" in inv, inv
    project.write("conftest.py", "pytest_plugins = make_list()\n")
    inv = json.loads(_py(project, "inventory").stdout)
    assert any(v == "UNRESOLVED" for k, v in inv.items() if k.startswith("plugin-module")), inv


def test_goal_conditional_include_is_inventoried(project: Project) -> None:
    project.write(
        "Makefile",
        "SEL := sel.mk\nifeq ($(MAKECMDGOALS),check)\ninclude $(SEL)\nendif\ncheck:\n\t@true\n",
    )
    project.write("sel.mk", "X := 1\n")
    inv = json.loads(_py(project, "inventory").stdout)
    assert "sel.mk" in inv, inv


# A real targeted pytest run per call: heavy (#68).
@pytest.mark.e2e
def test_red_record_with_a_collection_error_is_not_red_evidence(project: Project) -> None:
    _oracle(project, FAILING)
    broken = f"tests/acceptance/test_{LEAF_ID}.extra.py"
    project.write(broken, "import does_not_exist_xyz\n")
    _baseline(project)
    _py(project, "red-drop", LEAF_ID)
    # acceptance_files() matches the exact stem only: use a second exact-stem file type.
    rec_path = project.root / f".git/harness-oracle/red_{LEAF_ID}.json"
    _py(project, "red-record", LEAF_ID)
    rec = json.loads(rec_path.read_text())
    rec["errors"] = [f"{broken}::x"]
    rec_path.write_text(json.dumps(rec))
    res = _py(project, "red-check", LEAF_ID)
    assert any("collected and failing" in w for w in _warns(res)), res.stdout + res.stderr


def test_missing_make_include_is_reported_unresolved(project: Project) -> None:
    project.write("Makefile", "CFG := gone.mk\ninclude $(CFG)\ncheck:\n\t@true\n")
    inv = json.loads(_py(project, "inventory").stdout)
    assert any(k.startswith("make-include:") and v == "UNRESOLVED" for k, v in inv.items()), inv


def test_missing_include_named_like_the_dummy_goal_is_unresolved(project: Project) -> None:
    project.write("Makefile", "CFG := __harness_none__\ninclude $(CFG)\ncheck:\n\t@true\n")
    inv = json.loads(_py(project, "inventory").stdout)
    assert any(k.startswith("make-include:") and v == "UNRESOLVED" for k, v in inv.items()), inv
