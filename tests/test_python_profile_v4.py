"""Python profile v4: the canonical profile for NEW Python projects.

Fast contract tests on the shipped files: skills/py-style/assets/pyproject.toml (read
by py-init and lib/scaffold_python.sh), templates/Makefile, the pre-commit config and
root conftest.py the scaffold provisions, the project CI assets (skills/github) and the
skills that document them. The real behaviour on an installed project (coverage
contract, deptry offline, hooks, the issue gate) is in test_python_profile_v4_e2e.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT, Project, bash_payload

PYPROJECT = TOOLKIT / "skills" / "py-style" / "assets" / "pyproject.toml"
PRECOMMIT = TOOLKIT / "skills" / "github" / "assets" / ".pre-commit-config.yaml"
ROOT_CONFTEST = TOOLKIT / "skills" / "py-pytest" / "assets" / "root_conftest.py"
MAKEFILE = TOOLKIT / "templates" / "Makefile"
GH = TOOLKIT / "skills" / "github"
CI = GH / "assets" / "ci.yml"
E2E = GH / "assets" / "e2e.yml"
SCAFFOLD = TOOLKIT / "lib" / "scaffold_python.sh"
RUFF = TOOLKIT / ".venv" / "bin" / "ruff"

# fmt: off
SELECT = [
    "E4", "E7", "E9", "F", "W", "I", "N", "A", "ICN", "UP", "FURB", "PTH", "SIM", "C4",
    "PIE", "RET", "PERF", "B", "BLE", "RUF", "PGH", "DTZ", "TRY", "LOG", "G", "ANN", "TC",
    "TID", "SLF", "ARG", "ERA", "S", "PT", "C90", "PLR0911", "PLR0912", "PLR0913",
    "PLR0915", "PLR1702",
]
# fmt: on
EXACT = ["PLR0911", "PLR0912", "PLR0913", "PLR0915", "PLR1702"]
SELECTION = "not integration and not e2e"


def _profile() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _ruff(profile: dict[str, Any]) -> dict[str, Any]:
    ruff: dict[str, Any] = profile["tool"]["ruff"]
    return ruff


def _dev() -> list[str]:
    dev: list[str] = _profile()["dependency-groups"]["dev"]
    return dev


def _pinned_ruff() -> str:
    pins = [d for d in _dev() if re.fullmatch(r"ruff==\d+\.\d+\.\d+", d)]
    assert len(pins) == 1, _dev()
    return pins[0].split("==")[1]


# ── 1. Ruff ──────────────────────────────────────────────────────────────────


def test_ruff_selects_exactly_the_profile_rules() -> None:
    lint = _ruff(_profile())["lint"]
    assert lint["select"] == SELECT
    assert lint["preview"] is True
    assert lint["explicit-preview-rules"] is True
    assert _ruff(_profile())["format"]["preview"] is False


def test_ruff_limits() -> None:
    ruff = _ruff(_profile())
    assert ruff["line-length"] == 88
    assert ruff["lint"]["mccabe"]["max-complexity"] == 10
    pylint = ruff["lint"]["pylint"]
    for key, value in {
        "max-args": 5, "max-branches": 10, "max-statements": 40, "max-nested-blocks": 4,
    }.items():  # fmt: skip
        assert pylint[key] == value, key


def test_ruff_ignores_only_try003_and_ann401_with_a_reason() -> None:
    assert _ruff(_profile())["lint"]["ignore"] == ["TRY003", "ANN401"]
    text = PYPROJECT.read_text(encoding="utf-8")
    for code in ("TRY003", "ANN401"):
        line = next(ln for ln in text.splitlines() if f'"{code}"' in ln and "ignore" not in ln)
        assert "#" in line, f"{code} is ignored without a comment: {line!r}"


def test_tests_relax_only_the_listed_rules() -> None:
    ignores = _ruff(_profile())["lint"]["per-file-ignores"]
    assert ignores == {"tests/**/*.py": ["ANN", "S101", "ARG", "SLF001", "PLR09", "C901"]}


def test_ruff_has_no_target_version_and_mypy_keeps_python_version() -> None:
    profile = _profile()
    assert "target-version" not in _ruff(profile)
    assert profile["project"]["requires-python"] == ">=3.12"
    assert profile["tool"]["mypy"]["python_version"] == "3.12"
    assert profile["tool"]["mypy"]["strict"] is True


def test_type_checking_knows_the_pydantic_runtime_bases() -> None:
    tc = _ruff(_profile())["lint"]["flake8-type-checking"]
    assert tc["runtime-evaluated-base-classes"] == [
        "pydantic.BaseModel",
        "pydantic_settings.BaseSettings",
    ]
    assert tc["runtime-evaluated-decorators"] == ["pydantic.validate_call"]


def test_ruff_is_pinned_to_the_tested_version() -> None:
    # Updating Ruff is a deliberate change: bump the pin, then re-run these tests.
    out = subprocess.run([str(RUFF), "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["ruff", _pinned_ruff()]


def _effective(tmp_path: Path) -> tuple[str, str]:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT.read_text(encoding="utf-8"))
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text('"""Package."""\n')
    res = subprocess.run(
        [str(RUFF), "check", "--show-settings", "."],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return res.stdout, res.stderr


def _in_family(code: str, prefix: str) -> bool:
    """`code` belongs to the selector `prefix` (S selects S101, not SIM101 or SLF001)."""
    return code.startswith(prefix) and not code[len(prefix)].isalpha()


def _expected_rules() -> set[str]:
    """Every stable rule of each selected family (preview rules only when named exactly,
    as explicit-preview-rules says), minus the ignored ones, for the installed Ruff."""
    out = subprocess.run(
        [str(RUFF), "rule", "--all", "--output-format", "json"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    expected = set(EXACT)
    for rule in json.loads(out):
        code = rule["code"]
        stable = "Stable" in rule["status"] and not rule["preview"]
        if stable and any(_in_family(code, p) for p in SELECT if p not in EXACT):
            expected.add(code)
    return expected - {"TRY003", "ANN401"}


def test_every_selected_rule_takes_effect(tmp_path: Path) -> None:
    out, _ = _effective(tmp_path)
    block = out.split("linter.rules.enabled = [", 1)[1].split("]\n", 1)[0]
    enabled = set(re.findall(r"\(([A-Z]+[0-9]+)\)", block))
    assert enabled == _expected_rules()
    assert "linter.preview = enabled" in out
    assert "formatter.preview = disabled" in out
    assert "linter.line_length = 88" in out


def test_ruff_reports_no_formatter_conflict(tmp_path: Path) -> None:
    _effective(tmp_path)
    for args in (["check", "."], ["format", "--check", "."]):
        res = subprocess.run(
            [str(RUFF), *args], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert "conflict" not in res.stderr.lower(), res.stderr
        assert "warning" not in res.stderr.lower(), res.stderr


def test_the_root_conftest_passes_the_profile_lint(tmp_path: Path) -> None:
    _effective(tmp_path)
    (tmp_path / "conftest.py").write_text(ROOT_CONFTEST.read_text(encoding="utf-8"))
    for args in (["check", "conftest.py"], ["format", "--check", "conftest.py"]):
        res = subprocess.run(
            [str(RUFF), *args], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert res.returncode == 0, res.stdout + res.stderr


# ── 2. Tools ─────────────────────────────────────────────────────────────────


def test_dev_group_has_the_profile_tools_and_nothing_async() -> None:
    names = {re.split(r"[=<>!~\[;]", d, maxsplit=1)[0] for d in _dev()}
    assert names == {
        "ruff", "mypy", "pytest", "pytest-cov", "pytest-mock", "deptry", "hypothesis",
        "pre-commit",
    }  # fmt: skip
    groups = _profile()["dependency-groups"]
    assert set(groups) == {"dev"}


def test_pytest_asyncio_is_optional() -> None:
    profile = _profile()
    assert "asyncio_mode" not in profile["tool"]["pytest"]["ini_options"]
    assert "pytest-asyncio" not in str(profile["dependency-groups"])


def test_markers_integration_and_e2e_are_registered() -> None:
    ini = _profile()["tool"]["pytest"]["ini_options"]
    names = {m.split(":", 1)[0] for m in ini["markers"]}
    assert {"integration", "e2e"} <= names
    assert "--strict-markers" in ini["addopts"]


# Use of flake8 as a tool: its command, config file, CI job, dependency. The Ruff
# rule families (flake8-bugbear…) are only names and stay allowed.
FLAKE8_USE = re.compile(r"flake8(?!-[a-z])")


@pytest.mark.parametrize("root", ["skills", "templates"])
def test_no_flake8_tool_is_left(root: str) -> None:
    hits = []
    for path in sorted((TOOLKIT / root).rglob("*")):
        if path.is_file():
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if FLAKE8_USE.search(line):
                    hits.append(f"{path.relative_to(TOOLKIT)}:{n}: {line.strip()}")
    assert not (TOOLKIT / "skills" / "py-init" / "assets" / ".flake8").exists()
    assert hits == []


# ── 3. Coverage ──────────────────────────────────────────────────────────────


def test_coverage_threshold_has_a_single_source() -> None:
    cov = _profile()["tool"]["coverage"]
    assert cov["run"]["branch"] is True
    assert cov["report"]["fail_under"] == 80
    for path in [MAKEFILE, CI, E2E, *GH.rglob("*.md")]:
        assert "--cov-fail-under" not in path.read_text(encoding="utf-8"), path


def _make(*args: str, root: Path | None = None) -> list[str]:
    res = subprocess.run(
        ["make", "-n", "--no-print-directory", "-f", str(MAKEFILE), *args],
        cwd=root or TOOLKIT, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return res.stdout.splitlines()


def test_the_test_selection_is_defined_once_in_the_makefile() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert text.count(SELECTION) == 1
    assert f"TEST_SELECT := {SELECTION}" in text.splitlines()
    assert SELECTION not in CI.read_text(encoding="utf-8")
    recipe = "\n".join(_make("test"))
    assert f'-m "{SELECTION}"' in recipe
    assert "coverage run --rcfile=pyproject.toml -m pytest" in recipe


def test_the_threshold_switch_disables_only_the_threshold() -> None:
    recipe = "\n".join(_make("test"))
    assert "HARNESS_NO_COV_THRESHOLD" in recipe
    off = "coverage report --rcfile=pyproject.toml --fail-under=0"
    assert off in recipe
    assert "coverage report --rcfile=pyproject.toml" in recipe.replace(off, "")
    # The threshold comes from pyproject.toml only, never an inherited COVERAGE_RCFILE.
    calls = re.findall(r"coverage (?:run|report|xml)[^;|]*", recipe)
    assert calls
    assert all("--rcfile=pyproject.toml" in c for c in calls), calls


def test_ci_runs_make_test_once_with_xml_and_no_cov_flags() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert ci.count("make test COVERAGE_XML=coverage.xml") == 1
    assert "--cov" not in ci
    assert "pytest-coverage-comment" in ci


# ── workflows: jobs, needs, selection by marker ──────────────────────────────


def _jobs(text: str) -> dict[str, str]:
    """Top-level jobs of a workflow (name -> body), parsed by indentation."""
    body = text.split("\njobs:\n", 1)[1]
    jobs: dict[str, str] = {}
    name = ""
    for line in body.splitlines():
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            name = match.group(1)
            jobs[name] = ""
        elif name and (line.startswith("    ") or not line.strip()):
            jobs[name] += line + "\n"
        elif line.strip() and not line.startswith("#"):
            name = ""
    return jobs


def _needs(job: str) -> list[str]:
    match = re.search(r"^    needs:\s*\[([^\]]*)\]", job, re.M)
    if match:
        return [n.strip() for n in match.group(1).split(",") if n.strip()]
    match = re.search(r"^    needs:\s*(\S+)\s*$", job, re.M)
    return [match.group(1)] if match else []


@pytest.mark.parametrize("path", sorted((GH / "assets").glob("*.yml")), ids=lambda p: p.name)
def test_no_workflow_has_a_dangling_needs(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "\njobs:\n" not in text:
        return
    jobs = _jobs(text)
    assert jobs, path
    for name, body in jobs.items():
        for dep in _needs(body):
            assert dep in jobs, f"{path.name}: job {name} needs {dep}, which is not a job here"


def test_the_service_backed_integration_job_lives_in_ci() -> None:
    assert not (GH / "assets" / "integration-with-services.yml").exists()
    jobs = _jobs(CI.read_text(encoding="utf-8"))
    assert set(jobs) == {"lint", "type", "deps", "test", "audit", "integration"}
    integration = jobs["integration"]
    assert "services:" in integration
    assert "postgres" in integration
    assert "redis" in integration
    assert "make test-integration" in integration
    assert "tests/integration" not in integration  # selected by marker, not by path


def test_integration_and_e2e_select_by_marker() -> None:
    assert any("-m integration" in ln for ln in _make("test-integration"))
    assert any("-m e2e" in ln for ln in _make("test-e2e"))
    e2e = E2E.read_text(encoding="utf-8")
    assert "workflow_dispatch" in e2e
    assert "make test-e2e" in e2e
    assert "tests/e2e" not in e2e


# ── 4. Makefile ──────────────────────────────────────────────────────────────


def _rule(name: str) -> str:
    match = re.search(rf"^{name}:(.*)$", MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert match, name
    return match.group(1).strip()


def test_verify_is_lint_type_test_deps() -> None:
    assert _rule("verify").split() == ["lint", "type", "test", "deps"]
    assert any("deptry src" in ln for ln in _make("deps"))


def test_deptry_has_no_standing_ignores() -> None:
    # The one tolerance (unused pydantic in an empty scaffold) lives in `make deps`,
    # scoped to DEP002 of those two packages; the profile itself ignores nothing.
    assert "deptry" not in _profile().get("tool", {})
    deps = "\n".join(_make("deps"))
    strict, _, scaffold = deps.partition("\nelse")
    assert "--per-rule-ignores" not in strict  # with a module in src/: fully strict
    assert "uv run deptry src --per-rule-ignores 'DEP002=pydantic|pydantic-settings'" in scaffold
    assert "expected until src/ imports pydantic" in deps


# A fake `uv` for `make deps`: deptry "fails" (DEP002 of the unused pydantic) unless it
# runs with the scaffold ignores. So the tolerance is visible as make's exit code.
FAKE_UV = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$(dirname "$0")/calls"
case "$*" in *--per-rule-ignores*) exit 0 ;; esac
echo "src: DEP002 'pydantic' defined as a dependency but not used"; exit 1
"""


def _deps_with_fake_uv(root: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = root / "fakebin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    return subprocess.run(
        ["make", "--no-print-directory", "-f", str(MAKEFILE), "deps"], cwd=root,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True, text=True, check=False,
    )  # fmt: skip


def _package(root: Path, *files: str) -> Path:
    pkg = root / "src" / "demo"
    pkg.mkdir(parents=True)
    for rel in ("__init__.py", *files):
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text("")
    return pkg


@pytest.mark.parametrize(
    "files",
    [(), ("sub/__init__.py",), ("__init__.pyi",), ("__pycache__/__init__.cpython-312.pyc",)],
    ids=["init-only", "nested-init", "init-stub", "bytecode"],
)
def test_deps_tolerates_pydantic_while_src_has_no_module(
    tmp_path: Path, files: tuple[str, ...]
) -> None:
    _package(tmp_path, *files)
    res = _deps_with_fake_uv(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "expected until src/ imports pydantic" in res.stdout


def test_a_directory_named_like_a_module_is_not_a_module(tmp_path: Path) -> None:
    pkg = _package(tmp_path)
    (pkg / "odd.py").mkdir()
    res = _deps_with_fake_uv(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize(
    "files",
    [("models.py",), ("sub/__init__.py", "sub/models.py"), ("api.pyi",), ("ns/models.py",)],
    ids=["module", "nested-module", "stub", "namespace-package"],
)
def test_deps_is_fully_strict_with_any_module(tmp_path: Path, files: tuple[str, ...]) -> None:
    _package(tmp_path, *files)
    res = _deps_with_fake_uv(tmp_path)
    assert res.returncode != 0, res.stdout
    assert "--per-rule-ignores" not in (tmp_path / "fakebin" / "calls").read_text()


def test_deps_is_fully_strict_through_a_symlinked_package(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere" / "demo"
    real.mkdir(parents=True)
    (real / "__init__.py").write_text("")
    (real / "models.py").write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo").symlink_to(real)
    res = _deps_with_fake_uv(tmp_path)
    assert res.returncode != 0, res.stdout
    assert "--per-rule-ignores" not in (tmp_path / "fakebin" / "calls").read_text()


def test_fix_formats_and_fixes_only_safe_fixes() -> None:
    assert _make("fix") == [
        "uv run ruff check --fix -- .",
        "uv run ruff format -- .",
    ]
    assert "--unsafe-fixes" not in MAKEFILE.read_text(encoding="utf-8")


def test_audit_is_outside_verify_and_uses_pip_audit() -> None:
    assert "audit" not in _rule("verify")
    recipe = "\n".join(_make("audit"))
    assert "uvx pip-audit" in recipe
    assert "uv export" in recipe


def test_lint_stays_check_only_with_lint_files() -> None:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    assert "LINT_FILES ?= ." in lines
    assert "LINT_EXTS := .py .pyi" in lines
    assert _make("lint") == ["uv run ruff check -- .", "uv run ruff format --check -- ."]
    post_write = (TOOLKIT / "lib" / "post_write.sh").read_text(encoding="utf-8")
    assert "--fix" not in post_write
    assert "make fix" not in post_write


def test_status_has_a_fallback_without_the_harness_link(tmp_path: Path) -> None:
    # A CI checkout has no harness/ symlink: the project's own targets must still run.
    (tmp_path / "Makefile").write_text(MAKEFILE.read_text(encoding="utf-8"))
    out = subprocess.run(
        ["make", "-s", "--no-print-directory", "-p", "-n", "env-check"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    ).stdout  # fmt: skip
    status = next(ln for ln in out.splitlines() if ln.startswith("STATUS :="))
    assert "harness/status.sh" not in status


# ── 4. pre-commit (opt-in) ───────────────────────────────────────────────────


def _hooks() -> list[dict[str, str]]:
    """The local hooks of the pre-commit config (a tiny YAML subset, no PyYAML)."""
    hooks: list[dict[str, str]] = []
    for line in PRECOMMIT.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(- )?([a-z_]+):\s*(.*)$", line)
        if not match or line.lstrip().startswith("#"):
            continue
        if match.group(1) and match.group(2) == "id":
            hooks.append({})
        if hooks:
            hooks[-1][match.group(2)] = match.group(3).strip()
    return hooks


def test_precommit_hooks_are_local_check_only_and_always_run() -> None:
    text = PRECOMMIT.read_text(encoding="utf-8")
    assert "repo: local" in text
    hooks = _hooks()
    assert [h["entry"] for h in hooks] == ["make lint", "make type"]
    for hook in hooks:
        assert hook["language"] == "system"
        assert hook["pass_filenames"] == "false"
        assert hook["always_run"] == "true"
        assert hook["stages"] == "[pre-commit]"
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for forbidden in ("make check", "make test", "make verify", "pytest", "--fix", "make fix"):
        assert forbidden not in code


def test_make_hooks_refuses_to_overwrite_a_foreign_hook() -> None:
    recipe = "\n".join(_make("hooks"))
    assert "pre-commit install" in recipe
    assert "FORCE" in MAKEFILE.read_text(encoding="utf-8")
    assert "--overwrite" in recipe


# ── scaffold: provisions the profile files, never overwrites ─────────────────


def _scaffold(dest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCAFFOLD), str(dest), "demo"], capture_output=True, text=True, check=False
    )


def test_the_scaffold_provisions_precommit_and_the_root_conftest(tmp_path: Path) -> None:
    res = _scaffold(tmp_path)
    assert res.returncode == 0, res.stderr
    assert (tmp_path / ".pre-commit-config.yaml").read_text() == PRECOMMIT.read_text()
    assert (tmp_path / "conftest.py").read_text() == ROOT_CONFTEST.read_text()


def test_the_scaffold_keeps_existing_precommit_and_conftest(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tmp_path / "conftest.py").write_text("# mine\n")
    res = _scaffold(tmp_path)
    assert res.returncode == 0, res.stderr
    assert (tmp_path / ".pre-commit-config.yaml").read_text() == "repos: []\n"
    assert (tmp_path / "conftest.py").read_text() == "# mine\n"
    assert ".pre-commit-config.yaml" in res.stdout and "conftest.py" in res.stdout


def test_the_scaffold_declares_pydantic_at_runtime(tmp_path: Path) -> None:
    # The user's decision: every project uses Pydantic, so the profile declares it.
    assert _scaffold(tmp_path).returncode == 0
    data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert data["project"]["dependencies"] == ["pydantic", "pydantic-settings"]
    assert data["tool"]["mypy"]["packages"] == ["demo"]


# ── 5. The issue gate's third run disables the threshold only ───────────────


def test_the_issue_gate_python_run_sets_the_threshold_switch() -> None:
    text = (TOOLKIT / "lib" / "review_with_codex.sh").read_text(encoding="utf-8")
    python_run = text.split('elif [ "$origin" = "undefined" ] && [ -f pyproject.toml ]; then', 1)[1]
    python_run = python_run.split("  else\n", 1)[0]
    assert 'HARNESS_NO_COV_THRESHOLD=1 PYTEST_ADDOPTS="$opts" make check' in python_run
    assert text.count("HARNESS_NO_COV_THRESHOLD") == 1


def _clean_env_drops(var: str) -> bool:
    script = f'. "{TOOLKIT}/lib/_clean_env.sh"; printf "%s\\n" "${{CLEAN_ENV[@]}}"'
    env = {**os.environ, var: "1"}
    out = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return any(a == "-u" and b == var for a, b in zip(out, out[1:], strict=False))


def test_clean_env_drops_the_threshold_switch() -> None:
    """Stop, close and the gates' first runs never inherit HARNESS_NO_COV_THRESHOLD:
    only the issue gate's third run sets it, after CLEAN_ENV."""
    assert _clean_env_drops("HARNESS_EXPECT_RED")  # the same pattern
    assert _clean_env_drops("HARNESS_NO_COV_THRESHOLD")


@pytest.mark.parametrize(
    "cmd",
    [
        "HARNESS_NO_COV_THRESHOLD=1 make check",
        "export HARNESS_NO_COV_THRESHOLD=1",
        "env HARNESS_NO_COV_THRESHOLD=1 make test",
        "make check HARNESS_NO_COV_THRESHOLD=1",
    ],
)
def test_the_bash_guard_blocks_the_threshold_switch(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 2, (cmd, res.stdout, res.stderr)
    assert "HARNESS_NO_COV_THRESHOLD" in res.stderr


# ── 4. consumers: the skills say what the profile does ──────────────────────


def _skill(*parts: str) -> str:
    return (TOOLKIT / "skills" / Path(*parts)).read_text(encoding="utf-8")


def test_py_style_documents_the_profile() -> None:
    text = _skill("py-style", "SKILL.md")
    assert "Max 79" not in text
    assert "88" in text
    assert "runtime-evaluated-base-classes" in text
    assert "# noqa: " in text  # reasoned exceptions, with examples
    for code in ("ERA001", "S603", "BLE001"):
        assert code in text, code
    assert "import-linter" in text  # optional, documented only
    assert "import-linter" not in PYPROJECT.read_text(encoding="utf-8")


def test_py_pytest_documents_selection_and_async() -> None:
    text = _skill("py-pytest", "SKILL.md")
    assert 'asyncio_mode = "auto"' in text  # documented for async projects…
    assert "optional" in text.lower()  # …as an opt-in
    assert SELECTION in text
    assert "make test-integration" in text
    assert "make test-e2e" in text
    assert "--cov-fail-under" not in text


def test_py_init_has_no_flake8_and_lists_the_new_assets() -> None:
    text = _skill("py-init", "SKILL.md")
    assert ".pre-commit-config.yaml" in text
    assert "root_conftest.py" in text
    readme = _skill("py-init", "assets", "README.md")
    assert "make verify" in readme
    assert "make test-integration" in readme


def test_the_migration_guide_exists_and_names_what_is_copied() -> None:
    text = (TOOLKIT / "docs" / "python-profile.md").read_text(encoding="utf-8")
    assert "symlink" in text
    for copied in ("pyproject.toml", "Makefile", "conftest.py", ".pre-commit-config.yaml"):
        assert copied in text, copied
    assert "HARNESS_NO_COV_THRESHOLD" in text
    assert "import-linter" in text
    assert "pytest-asyncio" in text
