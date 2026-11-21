"""I3a (C): the make check backstop for links, local settings and the templates record."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT, Project
from i3a_helpers import LINKS, installed

INSTALL_HINT = "install.sh"


def _integrity(project: Project) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_guard.py"), "integrity"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(project.root), **project.env},
    )


def _warns(out: str, needle: str) -> list[str]:
    """WARN lines naming `needle` as a whole path (not `harness` inside `harness/x.sh`)."""
    token = re.compile(rf"(?<![\w./]){re.escape(needle)}(?![\w/])")
    return [ln for ln in out.splitlines() if "WARN" in ln and token.search(ln)]


@pytest.fixture
def ready(project: Project, tmp_path: Path) -> Project:
    return installed(project, tmp_path)


def _baseline_rc(project: Project) -> int:
    return _integrity(project).returncode


# ── Links ───────────────────────────────────────────────────────────────────


def test_correct_link_set_has_no_new_warn(ready: Project) -> None:
    res = _integrity(ready)
    for rel in LINKS:
        assert not _warns(res.stdout, rel), res.stdout
    assert not _warns(res.stdout, "settings.local.json"), res.stdout
    assert not _warns(res.stdout, "templates.json"), res.stdout


@pytest.mark.parametrize("rel", [".claude/skills", ".claude/settings.json"])
def test_missing_link_warns(ready: Project, rel: str) -> None:
    rc = _baseline_rc(ready)
    (ready.root / rel).unlink()
    res = _integrity(ready)
    hits = _warns(res.stdout, rel)
    assert len(hits) == 1, res.stdout
    assert INSTALL_HINT in hits[0]
    assert res.returncode == rc


@pytest.mark.parametrize("rel", list(LINKS))
def test_retargeted_link_warns(ready: Project, tmp_path: Path, rel: str) -> None:
    rc = _baseline_rc(ready)
    elsewhere = tmp_path / "elsewhere" / rel.replace("/", "_")
    elsewhere.mkdir(parents=True)
    path = ready.root / rel
    path.unlink()
    path.symlink_to(elsewhere)
    res = _integrity(ready)
    hits = _warns(res.stdout, rel)
    assert len(hits) == 1, res.stdout
    assert INSTALL_HINT in hits[0]
    assert res.returncode == rc


@pytest.mark.parametrize("rel", [".claude/skills", ".claude/settings.json"])
def test_real_file_instead_of_link_warns(ready: Project, rel: str) -> None:
    rc = _baseline_rc(ready)
    path = ready.root / rel
    path.unlink()
    path.write_text("{}\n", encoding="utf-8")
    res = _integrity(ready)
    assert len(_warns(res.stdout, rel)) == 1, res.stdout
    assert res.returncode == rc


def test_per_skill_shape_warns_not_checkable(ready: Project) -> None:
    rc = _baseline_rc(ready)
    (ready.root / ".claude/skills").unlink()
    (ready.root / ".claude/skills/docs").mkdir(parents=True)
    res = _integrity(ready)
    hits = _warns(res.stdout, "T2-S")
    assert len(hits) == 1, res.stdout
    assert "Traceback" not in res.stderr
    assert res.returncode == rc


# ── settings.local.json ─────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "content",
    [
        '{"disableAllHooks": true}',
        '{"disableAllHooks": 1}',
        '{"hooks": {}}',
        "{not json",
        "[]",
    ],
)
def test_local_settings_warn(ready: Project, content: str) -> None:
    rc = _baseline_rc(ready)
    ready.write(".claude/settings.local.json", content)
    res = _integrity(ready)
    assert len(_warns(res.stdout, "settings.local.json")) == 1, res.stdout
    assert res.returncode == rc


@pytest.mark.parametrize(
    "content", [None, '{"permissions": {"allow": []}}', '{"disableAllHooks": false}']
)
def test_local_settings_ok(ready: Project, content: str | None) -> None:
    if content is not None:
        ready.write(".claude/settings.local.json", content)
    res = _integrity(ready)
    assert not _warns(res.stdout, "settings.local.json"), res.stdout


# ── .harness/templates.json ─────────────────────────────────────────────────


def test_invalid_templates_record_warns(ready: Project) -> None:
    rc = _baseline_rc(ready)
    ready.write(".harness/templates.json", "{not json")
    res = _integrity(ready)
    assert len(_warns(res.stdout, "templates.json")) == 1, res.stdout
    assert res.returncode == rc


def test_templates_record_directory_warns(ready: Project) -> None:
    rc = _baseline_rc(ready)
    (ready.root / ".harness/templates.json").mkdir(parents=True)
    res = _integrity(ready)
    assert len(_warns(res.stdout, "templates.json")) == 1, res.stdout
    assert res.returncode == rc


def test_templates_record_broken_link_warns(ready: Project, tmp_path: Path) -> None:
    rc = _baseline_rc(ready)
    (ready.root / ".harness").mkdir()
    (ready.root / ".harness/templates.json").symlink_to(tmp_path / "nowhere.json")
    res = _integrity(ready)
    assert len(_warns(res.stdout, "templates.json")) == 1, res.stdout
    assert res.returncode == rc


def test_absent_templates_record_ok(ready: Project) -> None:
    assert not _warns(_integrity(ready).stdout, "templates.json")


# ── Unchanged behaviour (characterization): check.sh base-file failures ─────


@pytest.mark.e2e
@pytest.mark.parametrize("rel", [".claude/agents", ".claude/codex"])
@pytest.mark.parametrize("broken", [False, True])
def test_missing_base_links_still_fail_check(
    ready: Project, tmp_path: Path, rel: str, broken: bool
) -> None:
    path = ready.root / rel
    path.unlink()
    if broken:
        path.symlink_to(tmp_path / "gone")
    res = subprocess.run(
        ["bash", str(TOOLKIT / "lib/check.sh")],
        cwd=ready.root,
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(ready.root), **ready.env},
    )
    assert res.returncode == 1, res.stdout + res.stderr
