"""Issue #92: the Python template declares LINT_FILES/LINT_EXTS so the post-write
hook (#76) lints only the written file; `make lint` alone is unchanged."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from conftest import TOOLKIT

TEMPLATE = TOOLKIT / "templates" / "Makefile"
FULL_LINT = ["uv run ruff check -- .", "uv run ruff format --check -- ."]


def _dry_lint(root: Path, *args: str) -> list[str]:
    res = subprocess.run(
        ["make", "-n", "--no-print-directory", "lint", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return res.stdout.splitlines()


def _template_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(TEMPLATE, root / "Makefile")
    return root


def test_the_template_declares_lint_files_and_lint_exts() -> None:
    lines = TEMPLATE.read_text().splitlines()
    assert "LINT_FILES ?= ." in lines
    assert "LINT_EXTS := .py .pyi" in lines


def test_make_lint_without_arguments_runs_the_full_lint(tmp_path: Path) -> None:
    assert _dry_lint(_template_project(tmp_path)) == FULL_LINT


def test_make_lint_with_lint_files_lints_only_that_file(tmp_path: Path) -> None:
    assert _dry_lint(_template_project(tmp_path), "LINT_FILES=src/x.py") == [
        "uv run ruff check -- src/x.py",
        "uv run ruff format --check -- src/x.py",
    ]


def test_the_c_template_does_not_declare_lint_files() -> None:
    """C lint (clang-format/cppcheck/clang-tidy) is not file-scoped: full lint (#92)."""
    text = (TOOLKIT / "templates-c" / "Makefile").read_text()
    assert "LINT_FILES" not in text


@pytest.mark.e2e
def test_scoped_lint_on_an_installed_project_ignores_other_files(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    (tmp_path / "claude-home").mkdir()
    # `uv run X` -> X from the toolkit venv; other uv commands are no-ops (no network).
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "uv").write_text('#!/bin/sh\n[ "$1" = run ] || exit 0\nshift\nexec "$@"\n')
    (shim / "uv").chmod(0o755)
    path = os.pathsep.join([str(shim), str(TOOLKIT / ".venv" / "bin"), os.environ["PATH"]])
    env = {"CLAUDE_HOME": str(tmp_path / "claude-home"), "HOME": str(tmp_path), "PATH": path}
    res = subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(root), "--python", "demo"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    pkg = root / "src" / "demo"
    (pkg / "good.py").write_text('"""Good."""\n\nX = 1\n')
    (pkg / "bad.py").write_text("import os\n")  # unused import: F401
    good = (pkg / "good.py").relative_to(root).as_posix()

    def lint(*args: str) -> int:
        return subprocess.run(
            ["make", "--no-print-directory", "lint", *args],
            cwd=root,
            capture_output=True,
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
        ).returncode

    assert lint() != 0
    assert lint(f"LINT_FILES={good}") == 0


# ── shipped through the migration (plan 0.7): exact old recipe -> new ────────

OLD_LINT = "lint:\n\tuv run ruff check .\n\tuv run ruff format --check .\n"


def _run_install(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "claude-home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    env["CLAUDE_HOME"] = str(home)
    return subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def _installed_before_92(tmp_path: Path, lint: str = OLD_LINT) -> Path:
    """A project installed by today's toolkit, its Makefile as the pre-#92 template."""
    root = tmp_path / "proj"
    res = _run_install(root, tmp_path, "--lang", "python")
    assert res.returncode == 0, res.stdout + res.stderr
    mk = root / "Makefile"
    text = mk.read_text()
    start = text.index("# Scoped lint (#76)")
    end = text.index("\ntype:")
    mk.write_text(text[:start] + lint + text[end:])
    return root


def _load_upgrade() -> ModuleType:
    sys.path.insert(0, str(TOOLKIT / "lib"))
    spec = importlib.util.spec_from_file_location("_upgrade_92", TOOLKIT / "lib/_upgrade.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


up = _load_upgrade()
HEAD = "# project\n.PHONY: lint\n\n"
TAIL = "\ntype:\n\tuv run mypy src\n\ninclude harness/harness.mk\n"


def _migrated(text: str, python_lint: bool = True) -> str | None:
    out: str | None = up.migrated_makefile(text, python_lint=python_lint)
    return out


def test_the_migration_upgrades_the_exact_old_lint_recipe() -> None:
    new = _migrated(HEAD + OLD_LINT + TAIL)
    assert new is not None
    assert "uv run ruff check -- $(LINT_FILES)" in new and "LINT_FILES ?= ." in new
    assert _migrated(new) is None


@pytest.mark.parametrize(
    "lint",
    [
        OLD_LINT.replace("check .", "check src"),  # changed command
        OLD_LINT + "\tuv run vulture src\n",  # extra command
        OLD_LINT + "\nlint:\n\techo override\n",  # a second lint rule
        "LINT_EXTS := .py\n\n" + OLD_LINT,  # the project's own lint variables
        "LINT_FILES ?= src\n\n" + OLD_LINT,
    ],
)
def test_a_customised_lint_recipe_is_never_rewritten(lint: str) -> None:
    assert _migrated(HEAD + lint + TAIL) is None
    assert up.lint_note(HEAD + lint + TAIL) is not None


def test_the_c_set_never_gets_the_python_lint() -> None:
    assert _migrated(HEAD + OLD_LINT + TAIL, python_lint=False) is None


def test_the_legacy_guard_path_also_upgrades_the_lint() -> None:
    new = _migrated(HEAD + OLD_LINT + up.LEGACY_GUARD)
    assert new is not None and "$(LINT_FILES)" in new and up.GUARD in new


def test_no_note_when_the_lint_is_scoped() -> None:
    scoped = "LINT_FILES ?= .\nlint:\n\truff check $(LINT_FILES)\n"
    assert up.lint_note(scoped) is None
    assert up.lint_note("LINT_FILES ?= .\nlint:\n\truff check .\n") is not None
    assert up.lint_note("build:\n\ttrue\n") is None


@pytest.mark.e2e
def test_migration_dry_run_apply_and_rollback_on_an_installed_project(tmp_path: Path) -> None:
    root = _installed_before_92(tmp_path)
    before = (root / "Makefile").read_text()
    dry = _run_install(root, tmp_path, "--migrate")
    out = dry.stdout + dry.stderr
    assert dry.returncode == 0, out
    assert "+\tuv run ruff check -- $(LINT_FILES)" in out and "would change: Makefile" in out
    assert (root / "Makefile").read_text() == before
    res = _run_install(root, tmp_path, "--migrate", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (root / "Makefile").read_text() == TEMPLATE.read_text()
    again = _run_install(root, tmp_path, "--migrate")
    assert "nothing to change" in again.stdout + again.stderr
    backup = next((root / ".harness" / "migrations").iterdir()).name
    rb = _run_install(root, tmp_path, "--rollback", backup, "--apply", "--yes")
    assert rb.returncode == 0, rb.stdout + rb.stderr
    assert (root / "Makefile").read_text() == before


@pytest.mark.e2e
def test_a_customised_lint_recipe_is_left_alone_with_a_warning(tmp_path: Path) -> None:
    root = _installed_before_92(tmp_path, OLD_LINT.replace("check .", "check src"))
    before = (root / "Makefile").read_text()
    dry = _run_install(root, tmp_path, "--migrate")
    out = dry.stdout + dry.stderr
    assert dry.returncode == 0, out
    assert "nothing to change" in out
    assert "LINT_FILES" in out  # the warning says how to get the scoped lint
    assert (root / "Makefile").read_text() == before


def test_the_migrated_lint_is_the_shipped_template_lint() -> None:
    assert up.LINT in TEMPLATE.read_text()
