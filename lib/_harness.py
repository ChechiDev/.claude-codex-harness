#!/usr/bin/env python3
"""Shared harness utilities. Single source of state parsing.

Reads the derived feature_list.json (generated from docs/plan/ by sync_plan.py).
The unit of work is the LEAF: a subtask (N.M.K), or a task (N.M) when it has no
subtasks. Gates, docs, and commits operate at leaf granularity.

Usage: python3 _harness.py <command> [args]
Commands:
  in-progress-id      print the id of the single in_progress leaf (empty if none)
  feature-name <id>   print the title of the task/subtask <id>
  done-ids            print the leaf ids in done status (one per line)
  declared-files <id> print the files declared for leaf <id> (one per line)
  validate            validate invariants; exit != 0 with [FAIL] messages on failure
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FEATURES_FILE = Path("feature_list.json")
VALID = {"pending", "in_progress", "done", "blocked"}


def _tasks() -> list[dict]:
    data = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
    return data["features"]


def _leaves() -> list[dict]:
    """Yield the atomic work units: subtasks, or the task itself if it has none."""
    out: list[dict] = []
    for t in _tasks():
        subs = t.get("subtasks") or []
        if subs:
            for s in subs:
                out.append({"id": s["id"], "status": s.get("status", "pending"),
                            "name": s.get("title", ""), "files": s.get("files", [])})
        else:
            out.append({"id": t["id"], "status": t.get("status", "pending"),
                        "name": t.get("title", ""), "files": t.get("files", [])})
    return out


def _all_items() -> list[dict]:
    """Every task and subtask (for name lookup by any id)."""
    out: list[dict] = []
    for t in _tasks():
        out.append({"id": t["id"], "name": t.get("title", "")})
        for s in t.get("subtasks") or []:
            out.append({"id": s["id"], "name": s.get("title", "")})
    return out


def in_progress_id() -> int:
    ids = [leaf["id"] for leaf in _leaves() if leaf["status"] == "in_progress"]
    if ids:
        print(ids[0])
    return 0


def feature_name(fid: str) -> int:
    for item in _all_items():
        if str(item["id"]) == str(fid):
            print(item["name"])
            return 0
    return 1


def done_ids() -> int:
    for leaf in _leaves():
        if leaf["status"] == "done":
            print(leaf["id"])
    return 0


def declared_files(fid: str) -> int:
    for leaf in _leaves():
        if str(leaf["id"]) == str(fid):
            for f in leaf.get("files", []):
                print(f)
            return 0
    return 1


def validate() -> int:
    leaves = _leaves()
    rc = 0
    in_progress = [leaf for leaf in leaves if leaf["status"] == "in_progress"]
    if len(in_progress) > 1:
        ids = ", ".join(leaf["id"] for leaf in in_progress)
        print(f"[FAIL]  {len(in_progress)} leaves in in_progress (max 1): {ids}")
        rc = 1
    for leaf in leaves:
        if leaf["status"] not in VALID:
            print(f"[FAIL]  Invalid status in {leaf['id']}: {leaf['status']}")
            rc = 1
    if rc == 0:
        print(f"[OK]    feature_list.json valid ({len(leaves)} work units)")
    return rc


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if not FEATURES_FILE.exists():
        print("[FAIL]  No feature_list.json in the cwd (run: make plan)", file=sys.stderr)
        return 1
    cmd, *rest = argv
    dispatch = {
        "in-progress-id": lambda: in_progress_id(),
        "feature-name": lambda: feature_name(rest[0]) if rest else 2,
        "done-ids": lambda: done_ids(),
        "declared-files": lambda: declared_files(rest[0]) if rest else 2,
        "validate": lambda: validate(),
    }
    fn = dispatch.get(cmd)
    if fn is None:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 2
    return fn()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
