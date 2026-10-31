"""Oracle migration: an issue subtask rewriting sealed acceptance tests of other subtasks.

Every acceptance manifest (progress/acceptance_<id>.sha256) must keep matching its
files, so an issue that changes behaviour covered by another subtask's oracle has
one sanctioned path, enforced here:

  issue gate     the approved `migrates:` lists are frozen in
                 progress/issue_migrations_<ISS-n>.json (listed in the issue seal)
  plan gate      before Codex runs: every declared file exists, is sealed by at least
                 one manifest and still matches it (`migrates-ready`)
  PLAN_APPROVED  the changed sealed files must be exactly the declared ones; the
                 complete record progress/oracle_migrations/<leaf>.json (old and new
                 hash of each file) is written atomically first, then every owner
                 manifest is rewritten (temp file + rename). A retry finishes an
                 interrupted seal only from a complete record whose new hashes still
                 match the files.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import _harness

Problems = list[str]


def snapshot_path(root: Path, iid: str) -> Path:
    return root / "progress" / f"issue_migrations_{iid}.json"


def record_path(root: Path, leaf: str) -> Path:
    return root / "progress" / "oracle_migrations" / f"{leaf}.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _leaf(leaf: str) -> dict[str, Any] | None:
    return next((s for s in _harness.leaves() if s["id"] == leaf), None)


def write_snapshot(root: Path, iid: str) -> None:
    """Freeze the `migrates:` of every subtask of <iid> (at its ISSUE_APPROVED seal)."""
    lists = {
        str(s["id"]): list(s["migrates"])
        for s in _harness.leaves()
        if s.get("issue") == iid and s.get("migrates")
    }
    _write_atomic(snapshot_path(root, iid), json.dumps(lists, indent=2, sort_keys=True) + "\n")


def _approved(root: Path, leaf: str) -> list[str] | None:
    """The frozen list of <leaf>, or None when its issue has no snapshot."""
    owner = _harness.parse_id(leaf)[1]
    path = snapshot_path(root, owner)
    if not path.is_file():
        return None
    lists: dict[str, list[str]] = json.loads(path.read_text(encoding="utf-8"))
    return lists.get(leaf, [])


def _manifests(root: Path) -> dict[Path, dict[str, str]]:
    """Every acceptance manifest -> {path: digest}."""
    out: dict[Path, dict[str, str]] = {}
    for manifest in sorted((root / "progress").glob("acceptance_*.sha256")):
        entries = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, rel = line.split(maxsplit=1)
                entries[rel] = digest
        out[manifest] = entries
    return out


def _owners(manifests: dict[Path, dict[str, str]], rel: str) -> list[Path]:
    return [m for m, entries in manifests.items() if rel in entries]


def _declared(root: Path, leaf: str) -> tuple[list[str], Problems]:
    item = _leaf(leaf)
    declared = list(item["migrates"]) if item else []
    approved = _approved(root, leaf)
    if declared and approved is None:
        return declared, [f"{leaf}: migrates: without an approved issue snapshot"]
    if approved is not None and sorted(declared) != sorted(approved):
        return declared, [f"{leaf}: migrates: differs from the list approved at the issue gate"]
    return declared, []


def ready_problems(root: Path, leaf: str) -> Problems:
    """Plan-gate precondition: the declared oracles exist, are sealed and untouched."""
    declared, problems = _declared(root, leaf)
    manifests = _manifests(root)
    for rel in declared:
        target = root / rel
        owners = _owners(manifests, rel)
        if not target.is_file():
            problems.append(f"{leaf}: {rel} does not exist")
        elif not owners:
            problems.append(f"{leaf}: {rel} is not sealed by any acceptance manifest")
        elif any(manifests[m][rel] != _sha256(target) for m in owners):
            problems.append(f"{leaf}: {rel} no longer matches its seal")
    return problems


def _changed(root: Path, manifests: dict[Path, dict[str, str]], own: Path) -> set[str]:
    return {
        rel
        for manifest, entries in manifests.items()
        if manifest != own
        for rel, digest in entries.items()
        if not (root / rel).is_file() or _sha256(root / rel) != digest
    }


def _new_record(root: Path, leaf: str, declared: list[str], own: Path) -> Problems:
    manifests = _manifests(root)
    changed = _changed(root, manifests, own)
    problems = [
        f"{rel} changed but is not in the approved migrates: of {leaf}"
        for rel in sorted(changed - set(declared))
    ]
    problems += [
        f"{rel} is in migrates: but was not changed" for rel in sorted(set(declared) - changed)
    ]
    if problems:
        return problems
    paths = {
        rel: {
            "old": next(manifests[m][rel] for m in _owners(manifests, rel)),
            "new": _sha256(root / rel),
            "owners": [m.relative_to(root).as_posix() for m in _owners(manifests, rel)],
        }
        for rel in sorted(declared)
    }
    record = {"leaf": leaf, "complete": True, "paths": paths}
    _write_atomic(record_path(root, leaf), json.dumps(record, indent=2) + "\n")
    return []


def _check_record(root: Path, leaf: str, declared: list[str]) -> tuple[Any, Problems]:
    try:
        record = json.loads(record_path(root, leaf).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, [f"{leaf}: unreadable migration record (re-run its plan gate)"]
    if not record.get("complete") or "paths" not in record:
        return None, [f"{leaf}: incomplete migration record (re-run its plan gate)"]
    if sorted(record["paths"]) != sorted(declared):
        return None, [f"{leaf}: the migration record does not match its approved migrates:"]
    problems = [
        f"{rel} no longer matches the migration record of {leaf} (re-run its plan gate)"
        for rel, entry in record["paths"].items()
        if not (root / rel).is_file() or _sha256(root / rel) != entry["new"]
    ]
    return record, problems


def _rewrite_owners(root: Path, record: Any) -> None:
    for rel, entry in record["paths"].items():
        for owner in entry["owners"]:
            manifest = root / owner
            lines = manifest.read_text(encoding="utf-8").splitlines()
            out = [
                f"{entry['new']}  {rel}" if line.split(maxsplit=1)[1:] == [rel] else line
                for line in lines
                if line.strip()
            ]
            _write_atomic(manifest, "\n".join(out) + "\n")


def seal(root: Path, leaf: str, own: Path) -> Problems:
    """Re-seal the oracles <leaf> migrated (call at its PLAN_APPROVED, before its own seal)."""
    declared, problems = _declared(root, leaf)
    if problems or not declared:
        return problems
    if not record_path(root, leaf).exists():
        problems = _new_record(root, leaf, declared, own)
        if problems:
            return problems
    record, problems = _check_record(root, leaf, declared)
    if problems:
        return problems
    _rewrite_owners(root, record)
    return []
