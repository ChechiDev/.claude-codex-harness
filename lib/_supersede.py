"""One-use supersede tickets (issue #30): the evidence of a fresh gate approval.

When `_verdict.py commit` accepts an ISSUE_APPROVED or a PLAN_APPROVED verdict, it
writes $(git rev-parse --git-path harness-supersede-ticket)/<id> (temp file + rename)
with digests of what the reviewer saw at that commit:

  issue  {"verdict": sha256 of the verdict, "entries": digest of the issue entries
          (without the migration snapshot), "subtasks": digest of its subtask ids,
          "lists": digest of the `migrates:` lists}
  plan   {"verdict": ..., "entries": digest of the spec and acceptance files,
          "files": {path: sha256} of every file in the leaf's `migrates:`}

The gate's next step (`seal issue` / `seal <leaf>`) consumes it. Only a ticket whose
verdict hash matches the current verdict counts; anything else is ignored and removed.
The ticket directory is codex-owned in every worktree (_guard.VERDICT_SLOTS).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import _harness
import _plan_seal

TICKET_DIR = "harness-supersede-ticket"
RE_VERDICT_FILE = re.compile(r"^(?:issue_review_(ISS-\d+)|plan_review_(.+))\.md$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True).encode("utf-8"))


def text_digest(path: Path) -> str:
    """As _plan_seal digests a sealed file."""
    return _sha(path.read_text(encoding="utf-8").encode("utf-8"))


def ticket_dir() -> Path | None:
    res = subprocess.run(
        ["git", "rev-parse", "--git-path", TICKET_DIR],
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return None
    return Path(res.stdout.strip()).resolve()


def verdict_hash(path: Path) -> str | None:
    return _sha(path.read_bytes()) if path.is_file() else None


# ── what the reviewer saw ────────────────────────────────────────────────────


def issue_subtasks(iid: str) -> tuple[list[str], dict[str, list[str]]]:
    """One read of feature_list.json: the subtask ids of <iid> and their `migrates:`
    lists, as the snapshot freezes them."""
    items = [s for s in _harness.leaves() if s.get("issue") == iid]
    ids = [str(s["id"]) for s in items]
    return ids, {str(s["id"]): list(s["migrates"]) for s in items if s.get("migrates")}


def issue_entries(root: Path, iid: str) -> dict[str, str]:
    """The issue seal entries without the migration snapshot."""
    snapshot = f"progress/issue_migrations_{iid}.json"
    return {k: v for k, v in _plan_seal.issue_entries(root, iid).items() if k != snapshot}


def issue_digests(
    entries: dict[str, str], subtasks: list[str], lists: dict[str, list[str]]
) -> dict[str, str]:
    """What an issue ticket binds: the issue entries, its subtask ids, their lists."""
    return {
        "entries": digest(entries),
        "subtasks": digest(sorted(subtasks)),
        "lists": digest(lists),
    }


def _leaf(leaf: str) -> dict[str, Any] | None:
    return next((s for s in _harness.leaves() if s["id"] == leaf), None)


def plan_entries(root: Path, leaf: str) -> str:
    """Digest of the spec and the acceptance files of <leaf>."""
    item = _leaf(leaf)
    files = [root / str(item["spec"])] if item and item.get("spec") else []
    files += _harness.acceptance_files(root, leaf)
    return digest(
        {f.relative_to(root).as_posix(): _sha(f.read_bytes()) if f.is_file() else "" for f in files}
    )


def migrated_files(root: Path, leaf: str) -> dict[str, str]:
    item = _leaf(leaf)
    paths = list(item.get("migrates") or []) if item else []
    return {rel: _sha((root / rel).read_bytes()) if (root / rel).is_file() else "" for rel in paths}


# ── tickets ──────────────────────────────────────────────────────────────────


def _path(item: str) -> Path | None:
    folder = ticket_dir()
    return None if folder is None else folder / item


def write_after_commit(root: Path, verdict_rel: str, token: str) -> None:
    """Called by `_verdict.py commit` once a verdict is final."""
    match = RE_VERDICT_FILE.match(Path(verdict_rel).name)
    if match is None or token not in ("ISSUE_APPROVED", "PLAN_APPROVED"):
        return
    item = match[1] or match[2]
    vhash = verdict_hash(root / verdict_rel)
    path = _path(item)
    if vhash is None or path is None:
        return
    if token == "ISSUE_APPROVED" and match[1]:
        subtasks, lists = issue_subtasks(item)
        entries = issue_entries(root, item)
        ticket: dict[str, Any] = {"verdict": vhash, **issue_digests(entries, subtasks, lists)}
    elif token == "PLAN_APPROVED" and match[2]:
        ticket = {
            "verdict": vhash,
            "entries": plan_entries(root, item),
            "files": migrated_files(root, item),
        }
    else:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(ticket, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read(item: str, verdict: Path) -> dict[str, Any] | None:
    """The ticket of <item> when it belongs to the current verdict; a ticket of another
    verdict (or an unreadable one) is removed and ignored."""
    path = _path(item)
    if path is None or not path.is_file():
        return None
    try:
        ticket: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ticket = {}
    if ticket.get("verdict") != verdict_hash(verdict):
        remove(item)
        return None
    return ticket


def remove(item: str) -> None:
    path = _path(item)
    if path is not None:
        path.unlink(missing_ok=True)
