#!/usr/bin/env python3
"""Shared harness utilities. Single source of state parsing.

Usage: python3 _harness.py <command> [args]
Commands:
  in-progress-id      print the id of the single in_progress feature (empty if none)
  feature-name <id>   print the 'name' of feature <id>
  done-ids            print the ids in done status (one per line)
  validate            validate invariants; exit != 0 with [FAIL] messages on failure
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FEATURES_FILE = Path("feature_list.json")
VALID = {"pending", "in_progress", "done", "blocked"}


def _load() -> list[dict]:
    data = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
    return data["features"]


def in_progress_id() -> int:
    ids = [f["id"] for f in _load() if f["status"] == "in_progress"]
    if ids:
        print(ids[0])
    return 0


def feature_name(fid: str) -> int:
    for f in _load():
        if str(f["id"]) == str(fid):
            print(f.get("name", ""))
            return 0
    return 1


def done_ids() -> int:
    for f in _load():
        if f["status"] == "done":
            print(f["id"])
    return 0


def validate() -> int:
    features = _load()
    rc = 0
    in_progress = [f for f in features if f["status"] == "in_progress"]
    if len(in_progress) > 1:
        print(f"[FAIL]  {len(in_progress)} features in in_progress (max 1)")
        rc = 1
    for f in features:
        if f["status"] not in VALID:
            print(f"[FAIL]  Invalid status in feature {f['id']}: {f['status']}")
            rc = 1
    if rc == 0:
        print(f"[OK]    feature_list.json valid ({len(features)} features)")
    return rc


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if not FEATURES_FILE.exists():
        print("[FAIL]  No feature_list.json in the cwd", file=sys.stderr)
        return 1
    cmd, *rest = argv
    dispatch = {
        "in-progress-id": lambda: in_progress_id(),
        "feature-name": lambda: feature_name(rest[0]) if rest else 2,
        "done-ids": lambda: done_ids(),
        "validate": lambda: validate(),
    }
    fn = dispatch.get(cmd)
    if fn is None:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 2
    return fn()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
