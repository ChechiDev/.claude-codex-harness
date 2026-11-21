"""M0a (#14), block 2: versioned templates, stored bases, ownership modes
(`whole` / `fragments`), first-time registration and the record written by install.

Plan 0.6 (last paragraph), 0.7. Managed files: Makefile, CLAUDE.md, AGENTS.md
(fragments: only the harness-owned parts) and docs/plan/issues/README.md (whole)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

SETS = ("templates", "templates-c")
LINKS = {
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
    "harness": "lib",
}
README = "docs/plan/issues/README.md"
IMPORT = "@harness/WORKFLOW.md"
OLD_REV = "54ca858"  # Makefile before harness.mk (tests/test_harness_mk.py)


# ── helpers ──────────────────────────────────────────────────────────────────


def _git_show(rev: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=TOOLKIT, text=True, capture_output=True, check=True
    ).stdout


def _revs_without_import(path: str) -> list[str]:
    """Every toolkit commit whose copy of `path` predates the shared WORKFLOW.md."""
    revs = subprocess.run(
        ["git", "log", "--format=%h", "--", path],
        cwd=TOOLKIT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()
    return [r for r in revs if "harness/WORKFLOW.md" not in _git_show(r, path)]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _manifest(tset: str) -> dict[str, Any]:
    return tomllib.loads((TOOLKIT / tset / "MANIFEST.toml").read_text())


def _env(tmp_path: Path) -> dict[str, str]:
    empty = tmp_path / "empty.gitconfig"
    empty.touch()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "HARNESS_"))}
    return {
        **env,
        "CLAUDE_HOME": str(tmp_path / "claude-home"),
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }


def _install(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        env=_env(tmp_path),
        stdin=subprocess.DEVNULL,
    )


def _project(
    tmp_path: Path,
    makefile: str | None = None,
    claude: str = "# Project instructions for Claude\n\n## Project context\nx\n",
    agents: str = "# AGENTS.md — Navigation map for agents\n",
) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Makefile").write_text(
        makefile if makefile is not None else _git_show(OLD_REV, "templates/Makefile")
    )
    (root / "CLAUDE.md").write_text(claude)
    (root / "AGENTS.md").write_text(agents)
    for rel, target in LINKS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).symlink_to(TOOLKIT / target)
    (tmp_path / "claude-home").mkdir(exist_ok=True)
    return root


def _current_project(tmp_path: Path, tset: str = "templates") -> Path:
    """A project with today's templates copied but no record (installed before M0a)."""
    root = _project(
        tmp_path,
        makefile=(TOOLKIT / tset / "Makefile").read_text(),
        claude=(TOOLKIT / tset / "CLAUDE.md").read_text(),
        agents=(TOOLKIT / tset / "AGENTS.md").read_text(),
    )
    (root / README).parent.mkdir(parents=True)
    (root / README).write_text((TOOLKIT / tset / README).read_text())
    return root


def _files(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink() and ".harness" not in p.parts
    }


def _apply(root: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return _install(root, tmp_path, "--migrate", "--apply", "--yes")


def _record(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / ".harness/templates.json").read_text())
    return data


# ── manifest and bases ───────────────────────────────────────────────────────


@pytest.mark.parametrize("tset", SETS)
def test_every_released_version_has_a_matching_base(tset: str) -> None:
    manifest = _manifest(tset)
    assert {t["path"] for t in manifest["template"]} == {
        "Makefile",
        "CLAUDE.md",
        "AGENTS.md",
        README,
    }
    for tpl in manifest["template"]:
        assert tpl["mode"] in ("whole", "fragments"), tpl["id"]
        for ver in tpl["version"]:
            base = TOOLKIT / tset / "_bases" / tpl["id"] / ver["version"]
            assert base.is_file(), base
            assert _sha(base.read_text()) == ver["sha256"], base
        assert tpl["current"] in {v["version"] for v in tpl["version"]}


@pytest.mark.parametrize("tset", SETS)
def test_the_current_whole_base_is_the_shipped_template(tset: str) -> None:
    tpl = next(t for t in _manifest(tset)["template"] if t["path"] == README)
    assert tpl["mode"] == "whole"
    base = TOOLKIT / tset / "_bases" / tpl["id"] / tpl["current"]
    assert base.read_text() == (TOOLKIT / tset / README).read_text()


# ── fresh install records from the start ─────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("tset", "args"), [("templates", ["--lang", "python"]), ("templates-c", ["--c", "demo"])]
)
def test_a_fresh_install_writes_the_record(tmp_path: Path, tset: str, args: list[str]) -> None:
    dest = tmp_path / "fresh"
    res = _install(dest, tmp_path, *args)
    assert (dest / ".harness/templates.json").is_file(), res.stdout + res.stderr
    rec = _record(dest)
    assert rec["set"] == tset
    assert rec["migration_level"] == 0
    assert set(rec["templates"]) == {"Makefile", "CLAUDE.md", "AGENTS.md", README}
    # the record matches the files it describes: a dry-run right after has nothing to do
    dry = _install(dest, tmp_path, "--migrate")
    assert dry.returncode == 0 and "nothing to change" in dry.stdout + dry.stderr


# ── first registration ───────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize("tset", SETS)
def test_a_current_project_without_record_only_gets_registered(tmp_path: Path, tset: str) -> None:
    root = _current_project(tmp_path, tset)
    files = _files(root)
    dry = _install(root, tmp_path, "--migrate")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert ".harness/templates.json" in dry.stdout
    res = _apply(root, tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _files(root) == files  # only the record is new
    assert _record(root)["set"] == tset


@pytest.mark.e2e
@pytest.mark.parametrize(
    "path",
    [
        "templates/CLAUDE.md",
        "templates/AGENTS.md",
        "templates-c/CLAUDE.md",
        "templates-c/AGENTS.md",
    ],
)
def test_pre_workflow_templates_register_as_fragment_absent(tmp_path: Path, path: str) -> None:
    revs = _revs_without_import(path)
    assert revs, path
    for rev in revs:
        case = tmp_path / rev
        case.mkdir()
        name = path.split("/")[1]
        kw = (
            {"claude": _git_show(rev, path)}
            if name == "CLAUDE.md"
            else {"agents": _git_show(rev, path)}
        )
        root = _project(case, **kw)
        res = _install(root, case, "--migrate")
        assert res.returncode == 0, f"{rev}: {res.stdout + res.stderr}"
        assert "harness/WORKFLOW.md" in res.stdout, rev


@pytest.mark.e2e
def test_an_absent_readme_is_registered_created_and_rolled_back(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _apply(root, tmp_path).returncode == 0
    assert (root / README).is_file()
    assert README in _record(root)["templates"]
    mig = sorted((root / ".harness/migrations").iterdir())[0].name
    assert _install(root, tmp_path, "--rollback", mig, "--apply", "--yes").returncode == 0
    assert not (root / README).exists()


@pytest.mark.e2e
def test_unknown_provenance_stops_first_registration(tmp_path: Path) -> None:
    root = _current_project(tmp_path)
    (root / README).write_text("# my own issues notes\n")
    files = _files(root)
    res = _install(root, tmp_path, "--migrate")
    assert res.returncode != 0
    assert README in res.stdout + res.stderr and "unknown" in (res.stdout + res.stderr).lower()
    assert _files(root) == files and not (root / ".harness").exists()


# ── whole: customised → 3-way stop ───────────────────────────────────────────


@pytest.mark.e2e
def test_a_customised_whole_file_stops_with_a_three_way_diff(tmp_path: Path) -> None:
    root = _current_project(tmp_path)
    assert _apply(root, tmp_path).returncode == 0  # registered at the current version
    (root / README).write_text((root / README).read_text() + "\nlocal rule\n")
    files = _files(root)
    res = _install(root, tmp_path, "--migrate")
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "<<<<<<<" in out and "|||||||" in out and ">>>>>>>" in out
    assert _files(root) == files


# ── fragments: project parts free, harness parts guarded ─────────────────────


@pytest.mark.e2e
def test_project_text_and_targets_never_stop_it(tmp_path: Path) -> None:
    root = _current_project(tmp_path)
    makefile = (root / "Makefile").read_text()
    (root / "Makefile").write_text(makefile + "\ndocker:\n\tdocker compose up\n")
    (root / "CLAUDE.md").write_text((root / "CLAUDE.md").read_text() + "\n## Our notes\nmine\n")
    (root / "AGENTS.md").write_text((root / "AGENTS.md").read_text() + "\nlocal map\n")
    assert _apply(root, tmp_path).returncode == 0
    (root / "CLAUDE.md").write_text((root / "CLAUDE.md").read_text() + "more notes\n")
    res = _install(root, tmp_path, "--migrate")
    assert res.returncode == 0, res.stdout + res.stderr


EDITS = {
    "CLAUDE.md": (IMPORT, "@workflow.md"),
    "AGENTS.md": ("`harness/WORKFLOW.md`", "`docs/workflow.md`"),
    "Makefile": ("include harness/harness.mk", "include harness/other.mk"),
}


@pytest.mark.e2e
@pytest.mark.parametrize("name", sorted(EDITS))
def test_a_modified_fragment_after_registration_stops_without_duplicate(
    tmp_path: Path, name: str
) -> None:
    root = _current_project(tmp_path)
    assert _apply(root, tmp_path).returncode == 0
    old, new = EDITS[name]
    text = (root / name).read_text()
    assert old in text
    (root / name).write_text(text.replace(old, new))
    files = _files(root)
    res = _apply(root, tmp_path)
    assert res.returncode != 0
    assert name in res.stdout + res.stderr and "modified" in (res.stdout + res.stderr).lower()
    assert _files(root) == files


@pytest.mark.e2e
@pytest.mark.parametrize("name", ["AGENTS.md", "Makefile"])
def test_a_partial_signature_stops_first_registration(tmp_path: Path, name: str) -> None:
    root = _current_project(tmp_path)
    old, new = EDITS[name]
    (root / name).write_text((root / name).read_text().replace(old, new))
    files = _files(root)
    res = _install(root, tmp_path, "--migrate")
    assert res.returncode != 0
    assert name in res.stdout + res.stderr
    assert _files(root) == files and not (root / ".harness").exists()


@pytest.mark.e2e
def test_a_fully_rewritten_import_is_the_documented_residual_limit(tmp_path: Path) -> None:
    root = _current_project(tmp_path)
    (root / "CLAUDE.md").write_text(
        (root / "CLAUDE.md").read_text().replace(IMPORT, "@workflow.md")
    )
    files = _files(root)
    res = _install(root, tmp_path, "--migrate")
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"+{IMPORT}" in res.stdout  # the dry-run shows what would be added
    assert _files(root) == files
