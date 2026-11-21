"""Sealed verification contract (issue #66, I5) — WARN rollout.

"Done" means lint, types and build pass, but the project controls what runs them. The
config graph of lint/type/build (ruff/mypy config files and sections, config-path
flags, included makefiles, the recipes, their variables and the prerequisites of
verify) is sealed in a separate I5 baseline. Drift warns through the launcher, also
under HARNESS_CHECK_CMD; an approved contract (proposal bound to the diff, Codex
review, human OK) reseals it. Every finding is a [WARN] with a remedy and exit 0.
"""

from __future__ import annotations

import json
import os
import subprocess

from conftest import TOOLKIT, Project, write_payload

PY_MAKEFILE = (TOOLKIT / "templates" / "Makefile").read_text()
C_MAKEFILE = (TOOLKIT / "templates-c" / "Makefile").read_text()
PYPROJECT = (
    '[project]\nname = "p"\n\n[tool.ruff]\nline-length = 100\n\n[tool.mypy]\nstrict = true\n'
)
BASELINE = "progress/contract_baseline.json"
PROPOSAL = "progress/config_contract.json"
REVIEW = "progress/config_contract_review.md"


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HARNESS_", "PYTEST_", "MAKE", "RUFF_", "MYPY"))
        and k not in ("CC", "CFLAGS", "EXTRA_CFLAGS", "MFLAGS")
    }
    return {**base, **(extra or {})}


def _py(
    project: Project, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(project.root / "harness" / "_contract.py"), *args],
        cwd=project.root,
        text=True,
        capture_output=True,
        env=_env({"CLAUDE_PROJECT_DIR": str(project.root), **(env or {})}),
        check=False,
    )


def _python_project(project: Project) -> None:
    project.write("Makefile", PY_MAKEFILE)
    project.write("pyproject.toml", PYPROJECT)


def _c_project(project: Project) -> None:
    project.write("Makefile", C_MAKEFILE)
    project.write("src/a.c", "int a(void) { return 1; }\n")


def _baseline(project: Project) -> None:
    project.commit_all("pre-rule")
    res = _py(project, "baseline-create", "--from", "HEAD")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _warns(res) == [], res.stdout + res.stderr
    res = _py(project, "baseline-seal")
    assert res.returncode == 0, res.stdout + res.stderr


def _launch(
    project: Project, env: dict[str, str] | None = None, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(project.root / "harness" / "run_check.sh"), *args],
        cwd=project.root,
        text=True,
        capture_output=True,
        env=_env(
            {"CLAUDE_PROJECT_DIR": str(project.root), "HARNESS_CHECK_CMD": "true", **(env or {})}
        ),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _warns(res: subprocess.CompletedProcess[str]) -> list[str]:
    return [ln for ln in (res.stdout + res.stderr).splitlines() if ln.startswith("[WARN]")]


def _contract_warns(res: subprocess.CompletedProcess[str]) -> list[str]:
    return [w for w in _warns(res) if "verification contract" in w]


# ── activation ───────────────────────────────────────────────────────────────


def test_without_baseline_nothing_is_active(project: Project) -> None:
    _python_project(project)
    project.commit_all("x")
    project.write("pyproject.toml", PYPROJECT.replace("strict = true", "strict = false"))
    res = _launch(project)
    assert res.returncode == 0
    assert _contract_warns(res) == [], res.stdout + res.stderr
    assert "I5 inactive" in res.stdout + res.stderr


def test_unchanged_project_gives_no_warning_python_and_c(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    res = _launch(project)
    assert _contract_warns(res) == [], res.stdout + res.stderr
    (project.root / "pyproject.toml").unlink()
    _c_project(project)
    project.commit_all("c")
    res = _py(project, "baseline-create", "--from", "HEAD")
    assert _warns(res) == [], res.stdout + res.stderr
    _py(project, "baseline-seal")
    res = _launch(project)
    assert _contract_warns(res) == [], res.stdout + res.stderr


def test_baseline_tamper_warns(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    path = project.root / BASELINE
    data = json.loads(path.read_text())
    data["inputs"] = {}
    path.write_text(json.dumps(data))
    res = _launch(project)
    assert any("baseline" in w and "seal" in w for w in _contract_warns(res)), res.stderr


# ── python config relaxations ────────────────────────────────────────────────


def test_relaxing_ruff_or_mypy_through_any_file_warns(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    cases = {
        "pyproject.toml": PYPROJECT.replace("strict = true", "strict = false"),
        "ruff.toml": "[lint]\nignore = ['ALL']\n",
        ".ruff.toml": "line-length = 300\n",
        "src/pkg/ruff.toml": "[lint]\nignore = ['E']\n",
        "mypy.ini": "[mypy]\nignore_errors = True\n",
        ".mypy.ini": "[mypy]\nignore_errors = True\n",
        "setup.cfg": "[mypy]\nignore_errors = True\n",
    }
    for rel, text in cases.items():
        original = (project.root / rel).read_text() if (project.root / rel).is_file() else None
        project.write(rel, text)
        res = _launch(project)
        assert res.returncode == 0
        warns = _contract_warns(res)
        assert any(rel in w for w in warns), (rel, res.stdout + res.stderr)
        if original is None:
            (project.root / rel).unlink()
        else:
            project.write(rel, original)
    assert _contract_warns(_launch(project)) == []


def test_config_path_flag_is_sealed(project: Project) -> None:
    _python_project(project)
    project.write(
        "Makefile",
        PY_MAKEFILE.replace("uv run mypy src", "uv run mypy --config-file cfg/m.ini src"),
    )
    project.write("cfg/m.ini", "[mypy]\nstrict = True\n")
    _baseline(project)
    project.write("cfg/m.ini", "[mypy]\nignore_errors = True\n")
    warns = _contract_warns(_launch(project))
    assert any("cfg/m.ini" in w for w in warns), warns


def test_removing_lint_from_verify_warns(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write("Makefile", PY_MAKEFILE.replace("verify: lint type test", "verify: type test"))
    warns = _contract_warns(_launch(project))
    assert any("verify" in w for w in warns), warns


def test_redefining_the_lint_recipe_warns(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write(
        "Makefile", PY_MAKEFILE.replace("uv run ruff check .", "uv run ruff check . || true")
    )
    warns = _contract_warns(_launch(project))
    assert any("lint" in w for w in warns), warns


def test_included_makefile_is_sealed(project: Project) -> None:
    _python_project(project)
    project.write("extra.mk", "X := 1\n")
    project.write("Makefile", "include extra.mk\n" + PY_MAKEFILE)
    _baseline(project)
    project.write("extra.mk", "lint:\n\t@true\n")
    warns = _contract_warns(_launch(project))
    assert any("extra.mk" in w for w in warns), warns


# ── C build inputs ───────────────────────────────────────────────────────────


def test_changing_c_build_variables_in_the_makefile_warns(project: Project) -> None:
    _c_project(project)
    _baseline(project)
    for old, new in (
        ("CSTD    := -std=c11", "CSTD    := -std=gnu11"),
        ("WARN    := -Wall -Wextra -Werror -pedantic", "WARN    := -w"),
        ("CC      ?= gcc", "CC      := tcc"),
        ("SRC   := $(shell find src", "SRC   := $(shell find nothing"),
    ):
        project.write("Makefile", C_MAKEFILE.replace(old, new))
        warns = _contract_warns(_launch(project))
        assert warns, (old, new)
    project.write("Makefile", C_MAKEFILE)
    assert _contract_warns(_launch(project)) == []


def test_c_overrides_from_env_and_make_flags_warn(project: Project) -> None:
    _c_project(project)
    _baseline(project)
    for env in ({"EXTRA_CFLAGS": "-w"}, {"CC": "tcc"}, {"CFLAGS": "-w"}):
        res = _launch(project, env)
        assert res.returncode == 0
        assert _contract_warns(res), (env, res.stdout + res.stderr)
    # make-level overrides are rejected by the launcher (dropped, with a warning)
    res = _launch(project, {"MAKEFLAGS": "WARN=-w"})
    assert any("MAKEFLAGS" in w for w in _warns(res)), res.stderr
    res = _launch(project, None, "WARN=-w", "-f", "other.mk")
    assert any("arguments ignored" in w for w in _warns(res)), res.stderr


def test_harness_check_cmd_does_not_skip_the_contract_check(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write("mypy.ini", "[mypy]\nignore_errors = True\n")
    res = _launch(project, {"HARNESS_CHECK_CMD": "true"}, "--decision")
    assert res.returncode == 0
    assert any("mypy.ini" in w for w in _contract_warns(res)), res.stderr


# ── approved change flow ─────────────────────────────────────────────────────


def _review(project: Project, verdict: str = "APPROVED") -> str:
    prop = json.loads((project.root / PROPOSAL).read_text())
    project.write(REVIEW, f"# Review\n\n**Verdict:** {verdict}\n\ndiff: {prop['diff']}\n")
    return str(prop["diff"])


def test_tightening_passes_the_flow_without_a_reason(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write("pyproject.toml", PYPROJECT.replace("line-length = 100", "line-length = 88"))
    res = _py(project, "contract-propose")
    assert res.returncode == 0, res.stdout + res.stderr
    digest = _review(project)
    res = _py(project, "contract-approve", digest)
    assert res.returncode == 0
    assert _warns(res) == [], res.stdout + res.stderr
    assert _contract_warns(_launch(project)) == []


def test_relaxation_reason_is_part_of_the_proposal(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write("mypy.ini", "[mypy]\nignore_errors = True\n")
    res = _py(project, "contract-propose", "--reason", "legacy module, see #9")
    assert res.returncode == 0
    prop = json.loads((project.root / PROPOSAL).read_text())
    assert prop["reason"] == "legacy module, see #9"
    assert "mypy.ini" in prop["changes"]


def test_proposal_changed_after_approval_is_rejected(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write("mypy.ini", "[mypy]\nstrict = True\n")
    _py(project, "contract-propose")
    digest = _review(project)
    project.write("mypy.ini", "[mypy]\nignore_errors = True\n")
    res = _py(project, "contract-approve", digest)
    assert res.returncode == 0
    assert any("refused" in w for w in _warns(res)), res.stdout + res.stderr
    assert any("mypy.ini" in w for w in _contract_warns(_launch(project)))


def test_missing_review_or_seal_is_reported(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write("mypy.ini", "[mypy]\nstrict = True\n")
    _py(project, "contract-propose")
    prop = json.loads((project.root / PROPOSAL).read_text())
    res = _py(project, "contract-approve", prop["diff"])
    assert any("refused" in w for w in _warns(res))
    (project.root / "progress/contract_baseline.sha256").unlink()
    res = _launch(project)
    assert any("seal" in w for w in _contract_warns(res)), res.stdout + res.stderr


def test_contract_review_is_reached_through_the_oracle_entry_point(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    project.write("mypy.ini", "[mypy]\nstrict = True\n")
    _py(project, "contract-propose")
    res = subprocess.run(
        ["python3", str(project.root / "harness" / "_oracle.py"), "contract-review"],
        cwd=project.root,
        text=True,
        capture_output=True,
        env=_env({"CLAUDE_PROJECT_DIR": str(project.root), "HARNESS_CODEX_MODE": "manual"}),
        check=False,
    )
    assert "verification contract" in res.stdout, res.stdout + res.stderr
    assert "relax" in res.stdout


# ── guard ────────────────────────────────────────────────────────────────────


def test_guard_warns_on_claude_editing_config_when_active(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    for rel in ("pyproject.toml", "ruff.toml", "mypy.ini", "setup.cfg", "Makefile", "x.mk"):
        res = project.run("guard_write.sh", write_payload(project.root / rel))
        assert res.returncode == 0, (rel, res.stdout, res.stderr)
        assert "lint/type/build" in res.stdout, (rel, res.stdout)
        json.loads(res.stdout)  # one hook JSON object, even when I4b also warns


def test_guard_silent_without_baseline(project: Project) -> None:
    res = project.run("guard_write.sh", write_payload(project.root / "mypy.ini"))
    assert res.returncode == 0
    assert "lint/type/build" not in res.stdout


def test_baseline_and_review_are_codex_owned_and_sealers_blocked(project: Project) -> None:
    for rel in (BASELINE, REVIEW):
        res = project.run("guard_write.sh", write_payload(project.root / rel))
        assert res.returncode == 2, (rel, res.stdout)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "python3 harness/_contract.py contract-approve abc"},
    }
    res = project.run("guard_bash.sh", payload)
    assert res.returncode == 2, res.stdout + res.stderr


# ── Codex review follow-ups ──────────────────────────────────────────────────


def test_deleting_the_baseline_but_not_its_seal_warns(project: Project) -> None:
    _python_project(project)
    _baseline(project)
    (project.root / BASELINE).unlink()
    warns = _contract_warns(_launch(project))
    assert any("seal" in w for w in warns), warns


def test_config_path_through_a_variable_is_sealed(project: Project) -> None:
    _python_project(project)
    mk = "MYPY_CFG := cfg/m.ini\n" + PY_MAKEFILE.replace(
        "uv run mypy src", "uv run mypy --config-file $(MYPY_CFG) src"
    )
    project.write("Makefile", mk)
    project.write("cfg/m.ini", "[mypy]\nstrict = True\n")
    _baseline(project)
    project.write("cfg/m.ini", "[mypy]\nignore_errors = True\n")
    warns = _contract_warns(_launch(project))
    assert any("cfg/m.ini" in w for w in warns), warns


def test_adding_a_c_source_file_does_not_warn(project: Project) -> None:
    _c_project(project)
    _baseline(project)
    project.write("src/b.c", "int b(void) { return 2; }\n")
    assert _contract_warns(_launch(project)) == []
