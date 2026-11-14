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
  issue re-gate  (issue #30) a fresh ISSUE_APPROVED that changes a subtask's list
                 supersedes its record: every affected record of the issue is
                 validated first, then archived as <leaf>.superseded-<k>.json and
                 removed (`supersede`). The next fresh PLAN_APPROVED rebuilds it from
                 the archives, newest first per path (`seal` with `fresh`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
    _write_bytes(path, text.encode("utf-8"))


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def archives(root: Path, leaf: str) -> list[tuple[int, Path]]:
    """The superseded records of <leaf>, oldest (k = 1) first."""
    pattern = re.compile(rf"^{re.escape(leaf)}\.superseded-([1-9]\d*)\.json$")
    folder = record_path(root, leaf).parent
    found = [(int(m[1]), p) for p in folder.glob("*.json") if (m := pattern.match(p.name))]
    return sorted(found)


def _leaf(leaf: str) -> dict[str, Any] | None:
    return next((s for s in _harness.leaves() if s["id"] == leaf), None)


def write_snapshot(root: Path, iid: str, lists: dict[str, list[str]] | None = None) -> None:
    """Freeze the `migrates:` of every subtask of <iid> (at its ISSUE_APPROVED seal);
    `lists` when the caller already read them (the supersede step)."""
    if lists is None:
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


def seal(root: Path, leaf: str, own: Path, fresh: dict[str, str] | None = None) -> Problems:
    """Re-seal the oracles <leaf> migrated (call at its PLAN_APPROVED, before its own seal).
    `fresh`: the migrated-file hashes of a valid plan ticket (see _supersede.py)."""
    declared, problems = _declared(root, leaf)
    if problems:
        return problems
    if not record_path(root, leaf).exists() and archives(root, leaf):
        problems = _from_archives(root, leaf, declared, own, fresh)
        if problems or not declared:
            return problems
    elif not declared:
        return []
    if not record_path(root, leaf).exists():
        problems = _new_record(root, leaf, declared, own)
        if problems:
            return problems
    record, problems = _check_record(root, leaf, declared)
    if problems:
        return problems
    _rewrite_owners(root, record)
    return []


# ── issue #30: supersede at the issue gate, rebuild at the plan gate ─────────


def _read_record(root: Path, leaf: str) -> tuple[Any, Problems]:
    try:
        record = json.loads(record_path(root, leaf).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, [f"{leaf}: unreadable migration record (re-run its plan gate)"]
    if not isinstance(record, dict) or not record.get("complete") or "paths" not in record:
        return None, [f"{leaf}: incomplete migration record (re-run its plan gate)"]
    return record, []


def _rel_owners(root: Path, manifests: dict[Path, dict[str, str]], rel: str) -> list[str]:
    return sorted(m.relative_to(root).as_posix() for m in _owners(manifests, rel))


def _unchanged(root: Path, manifests: dict[Path, dict[str, str]], rel: str, entry: Any) -> bool:
    """`rel` still holds `entry`'s new hash: the file, the same owners, every owner entry."""
    target = root / rel
    return (
        target.is_file()
        and _sha256(target) == entry["new"]
        and _rel_owners(root, manifests, rel) == sorted(entry["owners"])
        and all(manifests[root / o].get(rel) == entry["new"] for o in entry["owners"])
    )


def _validate_old(
    root: Path, leaf: str, record: Any, manifests: dict[Path, dict[str, str]]
) -> Problems:
    problems: Problems = []
    for rel, entry in record["paths"].items():
        target = root / rel
        if not target.is_file() or _sha256(target) != entry["new"]:
            problems.append(f"{leaf}: {rel} changed since its migration record")
        elif _rel_owners(root, manifests, rel) != sorted(entry["owners"]):
            problems.append(f"{leaf}: {rel}: its owners changed since its migration record")
        else:
            digests = [manifests[root / o].get(rel) for o in entry["owners"]]
            if entry["old"] in digests:
                return [f"{leaf}: interrupted migration seal; finish it before re-gating the issue"]
            if any(d != entry["new"] for d in digests):
                problems.append(f"{leaf}: {rel}: an owner manifest holds an unexpected digest")
    return problems


def _archive(root: Path, leaf: str) -> None:
    """Archive the active record (reused when the newest archive has its bytes), then
    remove it: a crash at any point is finished by re-running the gate's seal."""
    active = record_path(root, leaf)
    data = active.read_bytes()
    found = archives(root, leaf)
    if not found or found[-1][1].read_bytes() != data:
        k = found[-1][0] + 1 if found else 1
        _write_bytes(active.with_name(f"{leaf}.superseded-{k}.json"), data)
    active.unlink()


def supersede(root: Path, leaves: list[str], lists: dict[str, list[str]]) -> Problems:
    """At a fresh ISSUE_APPROVED: archive every record of the issue's `leaves` whose paths
    differ from the list just approved; nothing changes unless every one of them
    validates. Decides only from the caller's in-memory read (never feature_list.json)."""
    manifests = _manifests(root)
    problems: Problems = []
    stale: list[str] = []
    for leaf in leaves:
        if not record_path(root, leaf).exists():
            continue
        record, bad = _read_record(root, leaf)
        if bad:
            problems += bad
            continue
        if sorted(record["paths"]) == sorted(lists.get(leaf, [])):
            continue
        bad = _validate_old(root, leaf, record, manifests)
        problems += bad
        stale.append(leaf)
    if problems:
        return problems
    for leaf in stale:
        _archive(root, leaf)
    return []


def _from_archives(
    root: Path, leaf: str, declared: list[str], own: Path, fresh: dict[str, str] | None
) -> Problems:
    """Superseded record: rebuild only on a fresh plan ticket whose migrated-file hashes
    every declared file still has."""
    if fresh is None or any(
        not (root / rel).is_file() or fresh.get(rel) != _sha256(root / rel) for rel in declared
    ):
        return [f"{leaf}: its migration record was superseded; re-run its plan gate"]
    return _rebuild(root, leaf, declared, own)


def _rebuild(root: Path, leaf: str, declared: list[str], own: Path) -> Problems:
    """A new record from the archives of <leaf>: each declared path found in one (newest
    first) keeps its original old hash; paths of the newest archive no longer declared
    must be untouched; other declared paths are checked as in `_new_record`."""
    manifests = _manifests(root)
    olds = [json.loads(p.read_text(encoding="utf-8")) for _, p in reversed(archives(root, leaf))]
    problems: Problems = []
    paths: dict[str, Any] = {}
    for rel in sorted(declared):
        entry = next((a["paths"][rel] for a in olds if rel in a["paths"]), None)
        if entry is None:
            continue
        owners = sorted(entry["owners"])
        if _rel_owners(root, manifests, rel) != owners or any(
            manifests[root / o].get(rel) != entry["new"] for o in owners
        ):
            problems.append(f"{rel} changed since the superseded migration record of {leaf}")
            continue
        paths[rel] = {"old": entry["old"], "new": _sha256(root / rel), "owners": owners}
    for rel, entry in olds[0]["paths"].items():
        if rel not in declared and not _unchanged(root, manifests, rel, entry):
            problems.append(f"{rel} was dropped from the migrates: of {leaf} but changed")
    added = set(declared) - set(paths)
    changed = _changed(root, manifests, own) - set(paths)
    problems += [
        f"{rel} changed but is not in the approved migrates: of {leaf}"
        for rel in sorted(changed - added)
        if not any(rel in p for p in problems)
    ]
    problems += [f"{rel} is in migrates: but was not changed" for rel in sorted(added - changed)]
    if problems:
        return problems
    for rel in sorted(added):
        found = _owners(manifests, rel)
        paths[rel] = {
            "old": manifests[found[0]][rel],
            "new": _sha256(root / rel),
            "owners": [m.relative_to(root).as_posix() for m in found],
        }
    if paths:
        record = {"leaf": leaf, "complete": True, "paths": dict(sorted(paths.items()))}
        _write_atomic(record_path(root, leaf), json.dumps(record, indent=2) + "\n")
    return []
