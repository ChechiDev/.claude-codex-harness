#!/usr/bin/env python3
"""C0 migration rehearsal driver: runs INSIDE the C0 container, on a copy.

Usage: python3 /toolkit/compat/_rehearse.py ROOT [--check-cmd CMD]

Steps, in order; the first one that fails stops the rehearsal:
  snapshot          every file git sees (tracked + untracked, not ignored) with its
                    sha256, every link of the project and CLAUDE_HOME, info/exclude
  check-before      CMD (default `make check`, as `compat run`; no -s: MAKEFLAGS would
                    silence nested make output a project test may assert on); red here means
                    the rehearsal proves nothing: verdict inconclusive
  dry-run           `_upgrade.py migrate ROOT`: must succeed and change nothing
  apply             `--apply --yes`: links identical, record at the current versions
                    and migration level, exclude entry present
  check-after       CMD again: must stay green
  rollback-dry-run  `rollback ROOT <migration>`: must succeed and change nothing
  rollback          `--apply --yes`: files and links back to the snapshot, except the
                    declared ones (ROLLBACK_EXCEPTIONS and the exclude entry)

Prints a JSON report; exit 0 pass, 1 fail, 2 inconclusive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

TOOLKIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT / "lib"))

import _templates  # noqa: E402
import _upgrade  # noqa: E402

UPGRADE = TOOLKIT / "lib" / "_upgrade.py"
DEFAULT_CHECK = "make check"
# What a rollback is allowed to leave behind (path prefixes of the files snapshot).
ROLLBACK_EXCEPTIONS: tuple[str, ...] = (_upgrade.MIGRATIONS.as_posix() + "/",)
RECORD_STATES = ("installed", "nofile")
OUTPUT_KEEP = 20000
EXIT = {"pass": 0, "fail": 1, "inconclusive": 2}
RE_ROLLBACK = re.compile(r"--rollback (\S+?)\)")

Snapshot = dict[str, Any]


# ── evidence ─────────────────────────────────────────────────────────────────


def _git_files(root: Path) -> list[str] | None:
    res = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z", "."],
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        return None
    return [p for p in res.stdout.decode().split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        out += [(Path(dirpath) / n).relative_to(root).as_posix() for n in filenames]
    return out


def _exclude_lines(root: Path) -> list[str]:
    target = _upgrade.exclude_target(root)
    if target is None or not target[0].is_file():
        return []
    return target[0].read_text("utf-8").splitlines()


def snapshot(root: Path) -> Snapshot:
    """Files git sees (sha256), links of the project and CLAUDE_HOME, info/exclude."""
    rels = _git_files(root)
    files: dict[str, str] = {}
    for rel in _walk_files(root) if rels is None else rels:
        path = root / rel
        if path.is_file() and not path.is_symlink():
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    links = {k: list(v) for k, v in _upgrade.link_snapshot(root).items()}
    return {"files": files, "links": links, "exclude": _exclude_lines(root)}


def compare(s0: Snapshot, s1: Snapshot, allow: tuple[str, ...] = ()) -> list[str]:
    """Every file or link difference between two snapshots, except paths under `allow`."""
    diffs: list[str] = []
    for kind in ("files", "links"):
        a, b = s0.get(kind, {}), s1.get(kind, {})
        for key in sorted(set(a) | set(b)):
            if kind == "files" and key.startswith(allow):
                continue
            if key not in b:
                diffs.append(f"{kind} {key}: removed")
            elif key not in a:
                diffs.append(f"{kind} {key}: added")
            elif a[key] != b[key]:
                diffs.append(f"{kind} {key}: changed ({a[key]} -> {b[key]})")
    return diffs


def _exclude_diff(s0: Snapshot, s1: Snapshot, allowed: set[str]) -> list[str]:
    before, after = s0.get("exclude", []), s1.get("exclude", [])
    diffs = [f"info/exclude: removed {line!r}" for line in before if line not in after]
    extra = [line for line in after if line not in before and line not in allowed]
    return diffs + [f"info/exclude: added {line!r}" for line in extra]


def record_problems(record: dict[str, Any]) -> list[str]:
    """The record a migration must leave: the toolkit's migration level and every
    template of its set at the CURRENT version, with that version's sha256."""
    problems: list[str] = []
    if record.get("migration_level") != _upgrade.MIGRATION_LEVEL_RECORDED:
        problems.append(f"migration_level {record.get('migration_level')!r}")
    tset = record.get("set")
    if tset not in _templates.SETS:
        return [*problems, f"set {tset!r}: unknown"]
    entries = record.get("templates")
    if not isinstance(entries, dict):
        return [*problems, "templates: not an object"]
    for tpl in _templates.load(str(tset)):
        entry = entries.get(tpl.path)
        if not isinstance(entry, dict):
            problems.append(f"{tpl.path}: missing from the record")
        elif entry.get("state") not in RECORD_STATES:
            problems.append(f"{tpl.path}: state {entry.get('state')!r}")
        elif entry["state"] == "installed":
            if entry.get("version") != tpl.current:
                problems.append(f"{tpl.path}: version {entry.get('version')!r} != {tpl.current}")
            if entry.get("sha256") != _templates.sha256(tpl.current_text):
                problems.append(f"{tpl.path}: sha256 is not the current version's")
    return problems


# ── steps ────────────────────────────────────────────────────────────────────


class Rehearsal:
    def __init__(self, root: Path, check_cmd: str) -> None:
        self.root = root
        self.check_cmd = check_cmd
        self.steps: list[dict[str, Any]] = []
        self.s0: Snapshot = {}
        self.final: Snapshot | None = None
        self.migration = ""
        self.record: dict[str, Any] | None = None
        self.changed: list[str] = []
        target = _upgrade.exclude_target(root)
        self.exclude_entry = target[1] if target else ""

    def _step(self, name: str, ok: bool, output: str) -> bool:
        self.steps.append({"name": name, "ok": ok, "output": output[-OUTPUT_KEEP:]})
        return ok

    def _run(self, argv: list[str]) -> tuple[int, str]:
        res = subprocess.run(
            argv,
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        return res.returncode, res.stdout + res.stderr

    def _upgrade(self, *args: str) -> tuple[int, str]:
        return self._run([sys.executable, str(UPGRADE), *args])

    def _unchanged(self, name: str, before: Snapshot, code: int, out: str) -> bool:
        diffs = compare(before, after := snapshot(self.root))
        diffs += _exclude_diff(before, after, set())
        if diffs:
            out += "\n[FAIL] changed something:\n" + "\n".join(diffs)
        return self._step(name, code == 0 and not diffs, out)

    def take_snapshot(self) -> bool:
        self.s0 = snapshot(self.root)
        files, links = len(self.s0["files"]), len(self.s0["links"])
        return self._step("snapshot", True, f"{files} files, {links} links")

    def check(self, name: str) -> bool:
        code, out = self._run(["sh", "-c", self.check_cmd])
        return self._step(name, code == 0, f"$ {self.check_cmd} (exit {code})\n{out}")

    def dry_run(self) -> bool:
        before = snapshot(self.root)
        code, out = self._upgrade("migrate", str(self.root))
        return self._unchanged("dry-run", before, code, out)

    def _record_problems(self) -> list[str]:
        path = self.root / _upgrade.RECORD
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            return [f"record {_upgrade.RECORD}: {exc}"]
        if not isinstance(data, dict):
            return [f"record {_upgrade.RECORD}: not a record object"]
        self.record = data
        return [f"record {p}" for p in record_problems(data)]

    def apply(self) -> bool:
        before = snapshot(self.root)
        code, out = self._upgrade("migrate", str(self.root), "--apply", "--yes")
        after = snapshot(self.root)
        found = RE_ROLLBACK.search(out)
        self.migration = found.group(1) if found else ""
        a, b = before["files"], after["files"]
        self.changed = sorted(
            rel
            for rel in set(a) | set(b)
            if a.get(rel) != b.get(rel) and not rel.startswith(ROLLBACK_EXCEPTIONS)
        )
        problems = [d for d in compare(before, after) if d.startswith("links ")]
        problems += [] if self.migration else ["no migration id in the output"]
        problems += self._record_problems()
        if self.exclude_entry and self.exclude_entry not in after["exclude"]:
            problems.append(f"info/exclude lacks {self.exclude_entry}")
        if problems:
            out += "\n[FAIL] " + "\n[FAIL] ".join(problems)
        return self._step("apply", code == 0 and not problems, out)

    def rollback_dry_run(self) -> bool:
        before = snapshot(self.root)
        code, out = self._upgrade("rollback", str(self.root), self.migration)
        return self._unchanged("rollback-dry-run", before, code, out)

    def rollback(self) -> bool:
        code, out = self._upgrade("rollback", str(self.root), self.migration, "--apply", "--yes")
        final = self.final = snapshot(self.root)
        diffs = compare(self.s0, final, ROLLBACK_EXCEPTIONS)
        diffs += _exclude_diff(self.s0, final, {self.exclude_entry})
        if diffs:
            out += "\n[FAIL] not back to the snapshot:\n" + "\n".join(diffs)
        return self._step("rollback", code == 0 and not diffs, out)

    def run(self) -> str:
        if not self.take_snapshot() or not self.check("check-before"):
            return "inconclusive"
        sequence: list[Callable[[], bool]] = [
            self.dry_run,
            self.apply,
            lambda: self.check("check-after"),
            self.rollback_dry_run,
            self.rollback,
        ]
        return "pass" if all(step() for step in sequence) else "fail"

    def report(self, verdict: str) -> dict[str, Any]:
        return {
            "verdict": verdict,
            "toolkit": str(TOOLKIT),
            "root": str(self.root),
            "claude_home": str(_upgrade._claude_home()),
            "check_cmd": self.check_cmd,
            "migration": self.migration,
            "record": self.record,
            "exclude_entry": self.exclude_entry,
            "changed_by_apply": self.changed,
            "initial": self.s0 or None,
            "final": self.final,
            "steps": self.steps,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="_rehearse.py")
    parser.add_argument("root", type=Path)
    parser.add_argument("--check-cmd", default=DEFAULT_CHECK)
    args = parser.parse_args(argv)
    rehearsal = Rehearsal(args.root.resolve(), args.check_cmd)
    try:
        verdict = rehearsal.run()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        rehearsal.steps.append({"name": "error", "ok": False, "output": f"[FAIL] {exc}"})
        verdict = "fail"
    print(json.dumps(rehearsal.report(verdict), indent=2))
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
