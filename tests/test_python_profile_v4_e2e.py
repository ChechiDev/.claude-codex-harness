"""Python profile v4 on a real installed project (install.sh --python demo).

The coverage contract of `make test` (a-e), test selection by marker, deptry offline,
`make fix`, `make hooks` and the installed hook through the harness close commit, the
TC/Pydantic regression and the issue gate's third run. Fast contract checks on the
shipped files are in test_python_profile_v4.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import DECLARED, LEAF_ID, TOOLKIT, Project, bind_code_approval
from i0_projects import FIXTURE_DEPTRY, Installed, install_project, isolated_env, uv_cache
from test_regate_deadlock import IID, _prompt
from test_regate_deadlock import _commit as _commit_all
from test_regate_inflight import ORACLE1, OWN, _break_oracle, _declare_migrates, _inflight, _third

pytestmark = pytest.mark.e2e

COVERED = 'def greet() -> str:\n    return "hi"\n'
HALF = (
    'def greet() -> str:\n    return "hi"\n\n\n'
    'def wave(n: int) -> str:\n    if n > 1:\n        return "waves"\n'
    '    if n < 0:\n        return "none"\n    return "wave"\n'
)
TEST_GREET = (
    'from demo.greet import greet\n\n\ndef test_greet() -> None:\n    assert greet() == "hi"\n'
)
MARKED = (
    "import pytest\n\nfrom demo.greet import greet\n\n\n@pytest.mark.{marker}\n"
    'def test_{marker}() -> None:\n    assert greet() == "hi"\n'
)


def _project(tmp: Path) -> Installed:
    """An installed Python project with the profile exactly as shipped (80%, deptry
    with no ignores): the shared fixtures relax both for the gate tests."""
    project = install_project(tmp, "python")
    pyproject = project.root / "pyproject.toml"
    text = pyproject.read_text()
    shipped = re.sub(r"fail_under = \d+", "fail_under = 80", text).replace(FIXTURE_DEPTRY, "")
    assert "[tool.deptry" not in shipped
    if shipped != text:
        pyproject.write_text(shipped)
        _git(project, "commit", "-qam", "profile: as shipped")
    return project


def _git(project: Installed, *args: str) -> str:
    res = project.run("git", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def _write(project: Installed, rel: str, text: str) -> None:
    path = project.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _make(
    project: Installed, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=project.root, env={**project.env, **(env or {})},
        text=True, capture_output=True, check=False, timeout=600,
    )  # fmt: skip


def _out(res: subprocess.CompletedProcess[str]) -> str:
    return res.stdout + res.stderr


# ── coverage contract ─────────────────────────────────────────────────────────


def test_a_the_empty_scaffold_verifies_green_and_leaves_the_tree_clean(tmp_path: Path) -> None:
    project = _project(tmp_path)
    res = _make(project, "verify")
    assert res.returncode == 0, _out(res)
    assert "no tests selected" in _out(res)
    assert "Coverage failure" not in _out(res)
    assert _git(project, "status", "--porcelain") == ""


def test_a_covered_suite_is_green_and_measured(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", COVERED)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    res = _make(project, "test")
    assert res.returncode == 0, _out(res)
    assert re.search(r"TOTAL\s.*100%", res.stdout), res.stdout
    assert _git(project, "status", "--porcelain", "--ignored", "--", ".coverage").startswith(
        "!! "
    )  # measured, and git-ignored


def test_b_a_suite_under_the_threshold_is_red(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", HALF)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    res = _make(project, "test")
    assert res.returncode != 0, _out(res)
    assert "fail-under=80" in _out(res) or "less than fail-under" in _out(res), _out(res)


@pytest.mark.parametrize(
    "env",
    [{"COVERAGE_RCFILE": "/dev/null"}, {"COVERAGE_RCFILE": "setup.cfg"}],
    ids=["rcfile-null", "rcfile-other"],
)
def test_b_the_environment_cannot_replace_the_threshold(
    tmp_path: Path, env: dict[str, str]
) -> None:
    """Coverage reads only pyproject.toml: an inherited COVERAGE_RCFILE (defaults, or
    another file with no threshold) never turns an under-covered suite green."""
    project = _project(tmp_path)
    _write(project, "setup.cfg", "[coverage:report]\nfail_under = 0\n")
    _write(project, "src/demo/greet.py", HALF)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    res = _make(project, "test", env=env)
    assert res.returncode != 0, _out(res)
    assert "fail-under=80" in _out(res) or "less than fail-under" in _out(res), _out(res)


def test_c_a_collection_error_is_red(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "tests/unit/test_broken.py", "from demo.missing import x\n")
    res = _make(project, "test")
    assert res.returncode != 0, _out(res)
    assert "ModuleNotFoundError" in _out(res)


def test_d_a_suite_deselected_by_mistake_is_red(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", COVERED)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    res = _make(project, "test", env={"PYTEST_ADDOPTS": "-k nomatch"})
    assert res.returncode != 0, _out(res)
    assert "deselected" in _out(res)


def test_d_only_integration_and_e2e_tests_is_green(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", COVERED)
    _write(project, "tests/integration/test_db.py", MARKED.format(marker="integration"))
    _write(project, "tests/e2e/test_flow.py", MARKED.format(marker="e2e"))
    res = _make(project, "test")
    assert res.returncode == 0, _out(res)
    assert "no tests selected" in _out(res)
    integration = _make(project, "test-integration")
    assert integration.returncode == 0, _out(integration)
    assert "1 passed" in _out(integration)
    e2e = _make(project, "test-e2e")
    assert e2e.returncode == 0, _out(e2e)
    assert "1 passed" in _out(e2e)


@pytest.mark.parametrize("where", ["integration", "e2e"])
def test_an_unmarked_test_under_integration_or_e2e_is_red(tmp_path: Path, where: str) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", COVERED)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    _write(project, f"tests/{where}/test_x.py", TEST_GREET.replace("test_greet", "test_x"))
    res = _make(project, "test")
    assert res.returncode != 0, _out(res)
    assert f"tests/{where}/test_x.py" in _out(res)
    assert f"@pytest.mark.{where}" in _out(res)


def test_a_marked_test_outside_its_dir_goes_to_its_own_job(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", COVERED)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    _write(project, "tests/unit/test_db.py", MARKED.format(marker="integration"))
    res = _make(project, "test")
    assert res.returncode == 0, _out(res)
    assert "1 passed, 1 deselected" in _out(res)
    integration = _make(project, "test-integration")
    assert integration.returncode == 0, _out(integration)
    assert "1 passed, 1 deselected" in _out(integration)


def test_e_the_switch_measures_without_enforcing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", HALF)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    res = _make(project, "test", env={"HARNESS_NO_COV_THRESHOLD": "1"})
    assert res.returncode == 0, _out(res)
    assert re.search(r"TOTAL\s", res.stdout), res.stdout  # still measured
    assert "HARNESS_NO_COV_THRESHOLD" in _out(res)  # and it says the threshold is off


def test_ci_xml_report_is_written(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", COVERED)
    _write(project, "tests/unit/test_greet.py", TEST_GREET)
    res = _make(project, "test", "COVERAGE_XML=coverage.xml")
    assert res.returncode == 0, _out(res)
    assert (project.root / "coverage.xml").is_file()


# ── deptry, for real, with no network ────────────────────────────────────────


def _offline(
    project: Installed, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """`make` with uv offline, no proxy and (where unshare works) no network at all."""
    extra = env or {}
    env = {k: v for k, v in project.env.items() if "proxy" not in k.lower()}
    env |= {"UV_OFFLINE": "1", "UV_CACHE_DIR": uv_cache(), **extra}
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env[var] = "http://127.0.0.1:9"  # any network attempt fails fast
    prefix: list[str] = []
    if (
        shutil.which("unshare")
        and subprocess.run(["unshare", "-rn", "true"], capture_output=True, check=False).returncode
        == 0
    ):
        prefix = ["unshare", "-rn"]
    return subprocess.run(
        [*prefix, "make", "--no-print-directory", *args],
        cwd=project.root, env=env, text=True, capture_output=True, check=False, timeout=600,
    )  # fmt: skip


def test_verify_runs_the_real_deptry_offline(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert (project.root / "uv.lock").is_file()
    res = _offline(project, "verify")
    assert res.returncode == 0, _out(res)
    assert "deptry" in _out(res)
    # The real deptry, not a shim: an undeclared import is reported.
    _write(project, "src/demo/web.py", "import requests\n\nrequests.get\n")
    deps = _offline(project, "deps")
    assert deps.returncode != 0, _out(deps)
    assert "DEP001" in _out(deps)


# ── deptry and the declared pydantic: tolerated only in an empty scaffold ────

SCAFFOLD_WARNING = "expected until src/ imports pydantic"
PYDANTIC_MODULE = (
    '"""Settings."""\n\nfrom pydantic import BaseModel\nfrom pydantic_settings import '
    'BaseSettings\n\n\nclass Point(BaseModel):\n    """A point."""\n\n    x: int\n\n\n'
    'class Settings(BaseSettings):\n    """Settings."""\n\n    debug: bool = False\n'
)


def test_deps_in_the_empty_scaffold_tolerates_the_unused_pydantic(tmp_path: Path) -> None:
    project = _project(tmp_path)
    res = _offline(project, "deps")
    assert res.returncode == 0, _out(res)
    assert SCAFFOLD_WARNING in _out(res)
    assert "DEP002" in _out(res)


def test_deps_with_a_module_that_does_not_import_pydantic_is_red(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/greet.py", COVERED)
    res = _offline(project, "deps")
    assert res.returncode != 0, _out(res)
    assert "DEP002" in _out(res)
    assert "'pydantic'" in _out(res)
    assert SCAFFOLD_WARNING not in _out(res)


def test_deps_with_a_module_that_imports_pydantic_is_green(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/settings.py", PYDANTIC_MODULE)
    res = _offline(project, "deps")
    assert res.returncode == 0, _out(res)
    assert SCAFFOLD_WARNING not in _out(res)
    assert "DEP002" not in _out(res)


def test_deps_is_fully_strict_once_src_has_a_module(tmp_path: Path) -> None:
    """Strict means strict: a module that uses pydantic but not pydantic-settings
    leaves the latter unused (DEP002), with no tolerance."""
    project = _project(tmp_path)
    _write(project, "src/demo/point.py", '"""Point."""\n\nfrom pydantic import BaseModel\n\n\n'
           'class Point(BaseModel):\n    """A point."""\n\n    x: int\n')  # fmt: skip
    res = _offline(project, "deps")
    assert res.returncode != 0, _out(res)
    assert "'pydantic-settings'" in _out(res)
    assert SCAFFOLD_WARNING not in _out(res)


def test_deps_in_the_empty_scaffold_still_fails_any_other_finding(tmp_path: Path) -> None:
    # src/ holds only __init__.py, but it imports an undeclared package: DEP001.
    project = _project(tmp_path)
    init = project.root / "src" / "demo" / "__init__.py"
    init.write_text(init.read_text() + "\nimport requests  # noqa: F401\n")
    res = _offline(project, "deps")
    assert res.returncode != 0, _out(res)
    assert "DEP001" in _out(res)


def test_deps_in_the_empty_scaffold_still_fails_another_unused_dependency(
    tmp_path: Path,
) -> None:
    # Only pydantic/pydantic-settings are tolerated: any other declared-but-unused
    # dependency is DEP002 (UV_FROZEN: the lock is not refreshed, offline).
    project = _project(tmp_path)
    pyproject = project.root / "pyproject.toml"
    text = pyproject.read_text()
    assert '"pydantic-settings",' in text or '"pydantic-settings"]' in text
    pyproject.write_text(text.replace('"pydantic-settings"', '"pydantic-settings", "attrs"', 1))
    res = _offline(project, "deps", env={"UV_FROZEN": "1"})
    assert res.returncode != 0, _out(res)
    assert "DEP002" in _out(res)
    assert "'attrs'" in _out(res)


# ── make fix ─────────────────────────────────────────────────────────────────


def test_make_fix_applies_safe_fixes_and_formats(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "src/demo/messy.py", "import sys\nimport os\nX=[os.sep,sys.platform]\n")
    assert _make(project, "lint").returncode != 0
    res = _make(project, "fix")
    assert res.returncode == 0, _out(res)
    lint = _make(project, "lint")
    assert lint.returncode == 0, _out(lint)
    assert "X = [os.sep, sys.platform]" in (project.root / "src/demo/messy.py").read_text()


# ── make hooks (opt-in) ──────────────────────────────────────────────────────


def _hook_env(tmp: Path) -> dict[str, str]:
    return {"PRE_COMMIT_HOME": str(tmp / "pre-commit-home")}


def _hook_path(project: Installed) -> Path:
    return project.root / _git(project, "rev-parse", "--git-path", "hooks/pre-commit").strip()


def test_make_hooks_keeps_a_foreign_hook_unless_forced(tmp_path: Path) -> None:
    project = _project(tmp_path)
    env = _hook_env(tmp_path)
    hook = _hook_path(project)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    hook.chmod(0o755)
    res = _make(project, "hooks", env=env)
    assert res.returncode != 0, _out(res)
    assert "FORCE=1" in _out(res)
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    forced = _make(project, "hooks", "FORCE=1", env=env)
    assert forced.returncode == 0, _out(forced)
    assert "pre-commit" in hook.read_text()
    assert not hook.with_name("pre-commit.legacy").exists()
    again = _make(project, "hooks", env=env)  # its own hook: reinstalling is fine
    assert again.returncode == 0, _out(again)


def _commit(env: dict[str, str], root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "commit", "-qm", "docs: note", *args],
        cwd=root, env=env, text=True, capture_output=True, check=False, timeout=600,
    )  # fmt: skip


def test_the_hook_blocks_a_docs_only_commit_while_the_tree_is_red(tmp_path: Path) -> None:
    project = _project(tmp_path)
    env = {**project.env, **_hook_env(tmp_path)}
    assert _make(project, "hooks", env=_hook_env(tmp_path)).returncode == 0
    _write(project, "src/demo/bad.py", "import os\n")
    _git(project, "add", "src/demo/bad.py")
    _git(project, "commit", "-qm", "wip: bad", "--no-verify")
    _write(project, "NOTES.md", "# notes\n")
    _git(project, "add", "NOTES.md")
    head = _git(project, "rev-parse", "HEAD")
    res = _commit(env, project.root)
    assert res.returncode != 0, _out(res)  # always_run: no .py staged, still checked
    assert "src/demo/bad.py" in _out(res), _out(res)
    assert _git(project, "rev-parse", "HEAD") == head
    _write(project, "src/demo/bad.py", '"""Fixed."""\n')
    _git(project, "add", "src/demo/bad.py")
    ok = _commit(env, project.root)
    assert ok.returncode == 0, _out(ok)


# ── the installed hook through the harness close commit (#107) ───────────────

SUBJECT = f"feat(domain): add user entity [{LEAF_ID}]"
MSG = f"progress/close_{LEAF_ID}.msg"
GOOD = '"""User entity."""\n\n\nclass User:\n    """A user."""\n'


@pytest.fixture
def hooked(project: Project, tmp_path: Path) -> Project:
    """The conftest project with the Python profile and its pre-commit hook installed."""
    root = project.root
    env = isolated_env(tmp_path)
    env["PRE_COMMIT_HOME"] = str(tmp_path / "pre-commit-home")
    project.env.update({k: env[k] for k in ("UV_OFFLINE", "UV_CACHE_DIR", "PRE_COMMIT_HOME")})
    res = subprocess.run(
        ["bash", str(TOOLKIT / "lib/scaffold_python.sh"), str(root), "pkg"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert res.returncode == 0, res.stderr
    shutil.copy(TOOLKIT / "templates" / "Makefile", root / "Makefile")
    project.write("src/pkg/domain/__init__.py", "")
    project.write(DECLARED, GOOD)
    project.write(".gitignore", "harness\n.claude/\n.venv/\n.coverage\n__pycache__/\n"
                  ".ruff_cache/\n.mypy_cache/\n.pytest_cache/\n")  # fmt: skip
    run_env = {**os.environ, **project.env}
    for argv in (["uv", "sync", "--quiet"], ["make", "-s", "hooks"]):
        res = subprocess.run(argv, cwd=root, env=run_env, text=True, capture_output=True,
                             check=False)  # fmt: skip
        assert res.returncode == 0, argv + [res.stdout, res.stderr]
    project.commit_all("python profile")
    return project


def _close(project: Project) -> subprocess.CompletedProcess[str]:
    return project.run(
        "review_with_codex.sh", None, "close", LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )


def _ready(project: Project, body: str) -> None:
    project.set_verdict("code", "APPROVED")
    project.write(DECLARED, body)
    bind_code_approval(project.root, LEAF_ID)
    project.write(MSG, SUBJECT + "\n")


def _head(project: Project) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%H %s"], cwd=project.root, text=True,
        capture_output=True, check=True,
    ).stdout  # fmt: skip


def test_the_close_commit_passes_the_installed_hook(hooked: Project) -> None:
    _ready(hooked, GOOD + "\n\nUSERS: list[User] = []\n")
    res = _close(hooked)
    assert res.returncode == 0, _out(res)
    assert _head(hooked).endswith(SUBJECT + "\n")
    assert not (hooked.root / MSG).exists()


def test_a_hook_failure_keeps_the_close_message_and_the_retry_commits(hooked: Project) -> None:
    before = _head(hooked)
    _ready(hooked, GOOD + "import os\n")  # F401: the hook's lint fails
    res = _close(hooked)
    assert res.returncode != 0, _out(res)
    assert _head(hooked) == before
    assert (hooked.root / MSG).read_text() == SUBJECT + "\n"  # kept, as in #107
    _ready(hooked, GOOD + "\n\nUSERS: list[User] = []\n")  # fixed and re-approved
    retry = _close(hooked)
    assert retry.returncode == 0, _out(retry)
    assert _head(hooked).endswith(SUBJECT + "\n")
    assert not (hooked.root / MSG).exists()


# ── TC + Pydantic: runtime-evaluated bases need no TYPE_CHECKING block ──────


def test_a_pydantic_model_with_an_imported_annotation_passes_and_runs(tmp_path: Path) -> None:
    # pydantic and pydantic-settings are in the profile's dependencies: no `uv add`,
    # no network (the installed project synced them from the offline cache).
    project = _project(tmp_path)
    _write(project, "src/demo/money.py", '"""Money."""\n\nfrom decimal import Decimal\n\n'
           "Amount = Decimal\n")  # fmt: skip
    _write(
        project,
        "src/demo/models.py",
        '"""Models."""\n\nfrom pydantic import BaseModel\nfrom pydantic_settings import '
        "BaseSettings\n\nfrom demo.money import Amount\n\n\nclass Order(BaseModel):\n"
        '    """An order."""\n\n    total: Amount\n\n\nclass Settings(BaseSettings):\n'
        '    """Settings."""\n\n    limit: Amount = Amount("10")\n',
    )
    lint = _make(project, "lint")
    assert lint.returncode == 0, _out(lint)
    assert "TC00" not in _out(lint)
    typed = _make(project, "type")
    assert typed.returncode == 0, _out(typed)
    run = project.run(
        "uv", "run", "python", "-c",
        "from demo.models import Order, Settings; "
        "print(Order(total='1.5').total, Settings().limit)",
    )  # fmt: skip
    assert run.returncode == 0, _out(run)
    assert run.stdout.split() == ["1.5", "10"]
    deps = _offline(project, "deps")
    assert deps.returncode == 0, _out(deps)
    assert SCAFFOLD_WARNING not in _out(deps)


# ── the issue gate's third run: the threshold off, the measure kept ──────────


def test_the_issue_gate_third_run_is_not_red_for_coverage(tmp_path: Path) -> None:
    project = _inflight(tmp_path)
    pyproject = project.root / "pyproject.toml"
    pyproject.write_text(re.sub(r"fail_under = \d+", "fail_under = 80", pyproject.read_text()))
    # An independent passing test that covers little: once T is excluded, the
    # remaining run measures far below 80%.
    _write(project, "src/demo/extra.py", HALF.replace("greet", "hello"))
    _write(
        project,
        "tests/unit/test_extra.py",
        "from demo.extra import hello\n\n\ndef test_hello() -> None:\n"
        '    """Covers one function."""\n    assert hello() == "hi"\n',
    )
    _commit_all(project, "an independent test")
    _break_oracle(project)
    _declare_migrates(project, ORACLE1)
    prompt = _prompt(project, tmp_path, "issue", IID)
    tolerated, rc3, log3 = _third(prompt)
    assert tolerated == {OWN, ORACLE1}
    assert rc3 == 0, log3
    assert re.search(r"TOTAL\s", log3), log3  # measured
    assert "HARNESS_NO_COV_THRESHOLD" in log3
