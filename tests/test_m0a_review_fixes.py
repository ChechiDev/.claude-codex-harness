"""M0a (#14): fixes from the Codex branch review, round 1 (codex_verdict_m0a_review1.md).

P1 rollback is all-or-nothing (missing backup part, failure mid-restore, link drift);
P1 the backup path never crosses a link (.harness or .harness/migrations);
P1 apply changes nothing when info/exclude cannot be written;
P2 a record missing a manifest entry is refused, not taken as a first registration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_m0a_migration import _env, _install, _migrations, _project, _snapshot, _upgrade
from test_m0a_protection import _existing_repo
from test_m0a_protection import _install as _install_git

HARNESS_FILES = ("Makefile", "CLAUDE.md", "AGENTS.md", "docs/plan/issues/README.md")


def _files(root: Path) -> dict[str, str | None]:
    """Content of every harness-owned file (None = absent) and of the record."""
    out: dict[str, str | None] = {}
    for rel in (*HARNESS_FILES, ".harness/templates.json"):
        path = root / rel
        out[rel] = path.read_text() if path.is_file() else None
    return out


def _applied(tmp_path: Path) -> tuple[Path, str]:
    root = _project(tmp_path)
    res = _install(root, "--migrate", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    return root, _migrations(root)[0].name


# ── P1: rollback is all-or-nothing ───────────────────────────────────────────


@pytest.mark.e2e
def test_rollback_with_a_missing_backup_part_changes_nothing(tmp_path: Path) -> None:
    root, mig = _applied(tmp_path)
    (root / ".harness/migrations" / mig / "files" / "AGENTS.md").unlink()
    applied = _files(root)
    for args in ((), ("--apply", "--yes")):
        res = _install(root, "--rollback", mig, *args)
        assert res.returncode != 0, args
        assert "AGENTS.md" in res.stdout + res.stderr
        assert _files(root) == applied, args


@pytest.mark.e2e
def test_a_failure_mid_rollback_restores_the_applied_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, mig = _applied(tmp_path)
    for k, v in _env(root).items():
        monkeypatch.setenv(k, v)
    applied = _files(root)
    up = _upgrade()
    real = up.shutil.copy2
    calls: list[Any] = []

    def flaky(src: Any, dst: Any, *a: Any, **kw: Any) -> Any:
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("injected failure on the 2nd restore")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(up.shutil, "copy2", flaky)
    assert up.main(["rollback", str(root), mig, "--apply", "--yes"]) != 0
    assert _files(root) == applied
    manifest = json.loads((root / ".harness/migrations" / mig / "manifest.json").read_text())
    assert manifest["status"] == "applied"  # still undoable later


@pytest.mark.e2e
def test_a_link_change_during_rollback_restores_the_applied_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, mig = _applied(tmp_path)
    for k, v in _env(root).items():
        monkeypatch.setenv(k, v)
    applied = _files(root)
    up = _upgrade()
    real = up.link_snapshot
    seen: list[int] = []

    def drifting(*a: Any, **kw: Any) -> Any:
        snap = dict(real(*a, **kw))
        seen.append(1)
        if len(seen) == 2:
            snap["project:harness"] = ("link", "/somewhere/else")
        return snap

    monkeypatch.setattr(up, "link_snapshot", drifting)
    assert up.main(["rollback", str(root), mig, "--apply", "--yes"]) != 0
    assert _files(root) == applied


# ── P1: the backup path never crosses a link ─────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize("linked", [".harness", ".harness/migrations"])
def test_a_linked_backup_dir_is_refused_and_nothing_is_written(tmp_path: Path, linked: str) -> None:
    root = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / linked).parent.mkdir(parents=True, exist_ok=True)
    (root / linked).symlink_to(outside)
    before = _snapshot(root)
    res = _install(root, "--migrate", "--apply", "--yes")
    assert res.returncode != 0
    assert "link" in (res.stdout + res.stderr).lower()
    assert list(outside.iterdir()) == []
    assert _snapshot(root) == before


# ── P1: apply changes nothing when info/exclude cannot be written ────────────


@pytest.mark.e2e
def test_an_unwritable_exclude_stops_apply_before_any_file(tmp_path: Path) -> None:
    root = _existing_repo(tmp_path)
    exclude = root / ".git/info/exclude"
    exclude.unlink(missing_ok=True)
    exclude.mkdir(parents=True)  # a directory: writing the entry fails
    before = _files(root)
    res = _install_git(root, tmp_path, "--migrate", "--apply", "--yes")
    assert res.returncode != 0
    assert "exclude" in (res.stdout + res.stderr).lower()
    assert _files(root) == before
    assert not (root / ".harness/migrations").exists() or not list(
        (root / ".harness/migrations").iterdir()
    )


# ── P2: an incomplete record is refused ──────────────────────────────────────


@pytest.mark.e2e
def test_a_record_missing_a_manifest_entry_is_refused(tmp_path: Path) -> None:
    root, _ = _applied(tmp_path)
    record_path = root / ".harness/templates.json"
    record = json.loads(record_path.read_text())
    record["templates"].pop("CLAUDE.md")
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    before = _snapshot(root)
    res = _install(root, "--migrate")
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert "CLAUDE.md" in out and "missing" in out
    assert _snapshot(root) == before
    assert os.path.exists(record_path)


# ── round 2 (codex_verdict_m0a_review2.md) ───────────────────────────────────


def _fail_manifest_write(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """Make writing a manifest that carries `status` fail, however it is written
    (directly or through a temp file)."""
    real_text, real_bytes = Path.write_text, Path.write_bytes

    def text(self: Path, data: str, *a: Any, **kw: Any) -> int:
        if "manifest" in self.name and f'"{status}"' in data:
            raise OSError(f"injected failure writing the {status} manifest")
        return real_text(self, data, *a, **kw)

    def raw(self: Path, data: bytes, *a: Any, **kw: Any) -> int:
        if "manifest" in self.name and f'"{status}"'.encode() in data:
            raise OSError(f"injected failure writing the {status} manifest")
        return real_bytes(self, data, *a, **kw)

    monkeypatch.setattr(Path, "write_text", text)
    monkeypatch.setattr(Path, "write_bytes", raw)


def test_a_failed_final_manifest_write_undoes_the_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    for k, v in _env(root).items():
        monkeypatch.setenv(k, v)
    before = _files(root)
    up = _upgrade()
    _fail_manifest_write(monkeypatch, "applied")
    assert up.main(["migrate", str(root), "--apply", "--yes"]) != 0
    monkeypatch.undo()
    assert _files(root) == before
    for mig in _migrations(root):
        manifest = json.loads((mig / "manifest.json").read_text())
        assert manifest["status"] != "applied"


@pytest.mark.e2e
def test_a_failed_final_manifest_write_undoes_the_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, mig = _applied(tmp_path)
    for k, v in _env(root).items():
        monkeypatch.setenv(k, v)
    applied = _files(root)
    up = _upgrade()
    _fail_manifest_write(monkeypatch, "rolled_back")
    assert up.main(["rollback", str(root), mig, "--apply", "--yes"]) != 0
    monkeypatch.undo()
    assert _files(root) == applied
    manifest = json.loads((root / ".harness/migrations" / mig / "manifest.json").read_text())
    assert manifest["status"] == "applied"  # still undoable later


@pytest.mark.e2e
def test_a_linked_migration_dir_is_refused_by_rollback(tmp_path: Path) -> None:
    root, mig = _applied(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".harness/migrations" / mig).rename(outside / mig)
    (root / ".harness/migrations" / mig).symlink_to(outside / mig)
    applied = _files(root)
    manifest_before = (outside / mig / "manifest.json").read_text()
    for args in ((), ("--apply", "--yes")):
        res = _install(root, "--rollback", mig, *args)
        assert res.returncode != 0, args
        assert "link" in (res.stdout + res.stderr).lower(), args
        assert _files(root) == applied, args
    assert (outside / mig / "manifest.json").read_text() == manifest_before
