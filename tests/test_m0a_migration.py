"""M0a (#14), block 1: `install.sh --migrate` never touches links, previews by default
and applies all-or-nothing, with a rollback that also removes the files it created.

Plan 0.7 / 0.8 / 0.11 (d). Later blocks cover template versions, fragments,
first registration, the migration level and the `.harness/` guard."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import TOOLKIT

# The links install.sh creates today (directory shape of .claude/skills).
EXPECTED_LINKS = {
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
    "harness": "lib",
}
OLD_MAKEFILE_REV = "54ca858"  # template Makefile before harness.mk (tests/test_harness_mk.py)


def _old_makefile() -> str:
    return subprocess.run(
        ["git", "show", f"{OLD_MAKEFILE_REV}:templates/Makefile"],
        cwd=TOOLKIT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def _project(tmp_path: Path, links: dict[str, str] | None = None) -> Path:
    """A pre-migration project with the full expected link set."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Makefile").write_text(_old_makefile())
    (root / "CLAUDE.md").write_text("# Project instructions for Claude\n\n## Project context\nx\n")
    (root / "AGENTS.md").write_text("# AGENTS.md — Navigation map for agents\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('user code')\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text("def test_x() -> None: ...\n")
    for rel, target in (links if links is not None else EXPECTED_LINKS).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).symlink_to(TOOLKIT / target)
    (tmp_path / "claude-home").mkdir()
    return root


def _env(root: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    env["CLAUDE_HOME"] = str(root.parent / "claude-home")
    return env


def _install(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        env=_env(root),
        stdin=subprocess.DEVNULL,
    )


def _snapshot(root: Path) -> dict[str, tuple[str, str, int]]:
    """Every path under root (and the fake ~/.claude): kind, content/target, mtime."""
    out: dict[str, tuple[str, str, int]] = {}
    for base in (root, root.parent / "claude-home"):
        for path in sorted(base.rglob("*")):
            rel = str(path.relative_to(root.parent))
            st = path.lstat()
            if path.is_symlink():
                out[rel] = ("link", os.readlink(path), st.st_mtime_ns)
            elif path.is_dir():
                out[rel] = ("dir", "", st.st_mtime_ns)
            else:
                out[rel] = ("file", path.read_text(errors="replace"), st.st_mtime_ns)
    return out


def _migrations(root: Path) -> list[Path]:
    base = root / ".harness" / "migrations"
    return sorted(base.iterdir()) if base.is_dir() else []


def _upgrade() -> ModuleType:
    """lib/_upgrade.py loaded in-process, to inject failures mid-transaction."""
    sys.path.insert(0, str(TOOLKIT / "lib"))
    spec = importlib.util.spec_from_file_location("_upgrade_m0a", TOOLKIT / "lib/_upgrade.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── links are never touched ──────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize("args", [("--migrate",), ("--migrate", "--apply", "--yes")])
def test_migrate_never_retargets_a_wrong_link(tmp_path: Path, args: tuple[str, ...]) -> None:
    wrong = dict(EXPECTED_LINKS, **{".claude/agents": "codex"})
    root = _project(tmp_path, wrong)
    before = _snapshot(root)
    res = _install(root, *args)
    assert res.returncode != 0
    assert ".claude/agents" in res.stdout + res.stderr
    assert _snapshot(root) == before  # link still wrong, nothing written


@pytest.mark.e2e
def test_migrate_stops_on_a_missing_link(tmp_path: Path) -> None:
    root = _project(tmp_path, {k: v for k, v in EXPECTED_LINKS.items() if k != ".claude/codex"})
    before = _snapshot(root)
    res = _install(root, "--migrate", "--apply", "--yes")
    assert res.returncode != 0
    assert ".claude/codex" in res.stdout + res.stderr
    assert _snapshot(root) == before


@pytest.mark.e2e
def test_migrate_stops_on_the_per_skill_shape(tmp_path: Path) -> None:
    root = _project(tmp_path, {k: v for k, v in EXPECTED_LINKS.items() if k != ".claude/skills"})
    (root / ".claude/skills").mkdir()
    (root / ".claude/skills/py-style").symlink_to(TOOLKIT / "skills/py-style")
    before = _snapshot(root)
    res = _install(root, "--migrate", "--apply", "--yes")
    assert res.returncode != 0
    assert "not supported" in res.stdout + res.stderr
    assert _snapshot(root) == before


# ── dry-run by default ───────────────────────────────────────────────────────


@pytest.mark.e2e
def test_migrate_is_a_dry_run_by_default(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _snapshot(root)
    res = _install(root, "--migrate")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _snapshot(root) == before
    assert "--- a/Makefile" in res.stdout and "+++ b/Makefile" in res.stdout
    assert "+@harness/WORKFLOW.md" in res.stdout
    assert "--apply" in res.stdout  # says how to write it


@pytest.mark.e2e
def test_apply_without_a_tty_needs_yes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _snapshot(root)
    res = _install(root, "--migrate", "--apply")
    assert res.returncode != 0
    assert "--yes" in res.stdout + res.stderr
    assert _snapshot(root) == before


@pytest.mark.e2e
def test_apply_writes_only_harness_files_and_keeps_links(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _snapshot(root)
    res = _install(root, "--migrate", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    after = _snapshot(root)
    links = {k: v for k, v in before.items() if v[0] == "link"}
    assert {k: v[:2] for k, v in links.items()} == {k: after[k][:2] for k in links}
    for rel in ("proj/src/app.py", "proj/tests/test_app.py"):
        assert after[rel] == before[rel]
    assert "include harness/harness.mk" in (root / "Makefile").read_text()
    assert (root / "docs/plan/issues/README.md").is_file()
    assert len(_migrations(root)) == 1


# ── all-or-nothing ───────────────────────────────────────────────────────────


def test_a_failure_mid_apply_restores_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    for k, v in _env(root).items():
        monkeypatch.setenv(k, v)
    before = _snapshot(root)
    up = _upgrade()
    real = os.replace
    calls: list[Any] = []

    def flaky(src: Any, dst: Any) -> None:
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("injected failure on the 2nd file")
        real(src, dst)

    monkeypatch.setattr(up.os, "replace", flaky)
    code = up.main(["migrate", str(root), "--apply", "--yes"])
    assert code != 0
    after = _snapshot(root)
    for rel, val in before.items():
        if val[0] == "file":
            assert after[rel][1] == val[1], rel  # content restored
    assert not (root / "docs/plan/issues/README.md").exists()
    assert len(_migrations(root)) == 1  # the backup is kept for inspection


def test_a_link_change_during_apply_fails_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    for k, v in _env(root).items():
        monkeypatch.setenv(k, v)
    before = _snapshot(root)
    up = _upgrade()
    real = up.link_snapshot
    seen: list[int] = []

    def drifting(*a: Any, **kw: Any) -> Any:
        snap = dict(real(*a, **kw))
        seen.append(1)
        if len(seen) == 2:
            snap["harness"] = ("link", "/somewhere/else")
        return snap

    monkeypatch.setattr(up, "link_snapshot", drifting)
    assert up.main(["migrate", str(root), "--apply", "--yes"]) != 0
    after = _snapshot(root)
    for rel, val in before.items():
        if val[0] == "file":
            assert after[rel][1] == val[1], rel
    assert not (root / "docs/plan/issues/README.md").exists()


# ── rollback ─────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_rollback_restores_files_and_removes_created_ones(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _snapshot(root)
    assert _install(root, "--migrate", "--apply", "--yes").returncode == 0
    mig = _migrations(root)[0].name

    dry = _install(root, "--rollback", mig)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert (root / "docs/plan/issues/README.md").is_file()  # dry-run: nothing undone

    res = _install(root, "--rollback", mig, "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    after = _snapshot(root)
    for rel, val in before.items():
        if val[0] in ("file", "link"):
            assert after[rel][:2] == val[:2], rel
    assert not (root / "docs/plan/issues/README.md").exists()
    assert not (root / ".harness/templates.json").exists()


@pytest.mark.e2e
def test_rollback_stops_when_a_created_file_was_edited(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _install(root, "--migrate", "--apply", "--yes").returncode == 0
    mig = _migrations(root)[0].name
    readme = root / "docs/plan/issues/README.md"
    readme.write_text(readme.read_text() + "\nlocal note\n")
    before = _snapshot(root)
    res = _install(root, "--rollback", mig, "--apply", "--yes")
    assert res.returncode != 0
    assert "docs/plan/issues/README.md" in res.stdout + res.stderr
    assert _snapshot(root) == before
