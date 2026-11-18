#!/usr/bin/env python3
"""One-time migration of an installed project to the shared, symlinked files.

Projects installed before lib/harness.mk and lib/WORKFLOW.md existed have the
harness targets copied into their Makefile and the workflow rules copied into
their CLAUDE.md, so later harness changes never reached them. After this
migration they reach every project through the harness/ symlink.

Usage: python3 _upgrade.py migrate <project> [--apply [--yes]]
       python3 _upgrade.py rollback <project> <migration> [--apply [--yes]]
       (install.sh <project> --migrate | --rollback <migration> [--apply] [--yes])

Without --apply it is a dry-run: it prints the diff and writes nothing. It never
creates, deletes or retargets a link: the expected links must already exist. An
applied migration backs up every file under .harness/migrations/<id>/ and writes
all files or none; --rollback <id> restores them and deletes the ones it created.
--apply and --yes are for the human (Claude never runs them).

Makefile: each harness-owned target (see harness.mk) defined in the project
Makefile is removed when its recipe is identical to harness.mk's; any customised
one stops the migration with nothing changed (move the custom part into a
project target and re-run). Then the guard + `include harness/harness.mk` go at
the end, and `make -pn` must show every harness target coming from harness.mk
and every other target from the project Makefile, or the Makefile is restored.
CLAUDE.md gains `@harness/WORKFLOW.md`, AGENTS.md a pointer to it, and
docs/plan/issues/README.md turns the issues lane on; nothing else
of the project is touched. Running it again changes nothing.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _templates
from _status import say

TOOLKIT = Path(__file__).resolve().parents[1]
HARNESS_MK = TOOLKIT / "lib" / "harness.mk"
# Links install.sh creates (directory shape); verified by the migration, never written.
LINKS = {
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
    "harness": "lib",
}
RECORD = Path(".harness/templates.json")
MIGRATIONS = Path(".harness/migrations")
MIGRATION_LEVEL_RECORDED = 0  # level a migration by this toolkit leaves the project at
# Raised only by the FAIL-promotion PR of a rule that needs a migration (plan 0.7).
REQUIRED_MIGRATION_LEVEL = 0
ALLOWED = {"Makefile", "CLAUDE.md", "AGENTS.md", "docs/plan/issues/README.md"}
# docs/plan/issues/ turns the issues lane on (make status asks GitHub for the queue).
ISSUES_README = Path(__file__).resolve().parents[1] / "templates/docs/plan/issues/README.md"
ISSUES_DIR = Path("docs/plan/issues/README.md")
INCLUDE = "include harness/harness.mk"
# Where harness/ is missing (a CI checkout, a new machine) only the harness targets
# fail; the project's own targets still run (an $(error) here would stop them all).
GUARD = (
    "\n# Harness targets, shared by every project (keep this block last). harness/ is a\n"
    "# per-machine symlink: where it is missing (a CI checkout, a new machine) only these\n"
    "# targets fail; the project's own targets above still run.\n"
    "ifneq ($(wildcard harness/harness.mk),)\n"
    f"{INCLUDE}\n"
    "else\n"
    ".PHONY: plan status check issue issue-start issue-new\n"
    "plan status check issue issue-start issue-new:\n"
    '\t@echo "harness/ is missing on this machine: run install.sh <project> first" >&2; exit 1\n'
    "endif\n"
)
# The first shape of that block: it stopped every target, so it is replaced.
LEGACY_GUARD = (
    "\n# Harness targets, shared by every project (keep this block last).\n"
    "ifeq ($(wildcard harness/harness.mk),)\n"
    "$(error harness/ is missing on this machine: run install.sh <project> first)\n"
    "endif\n"
    f"{INCLUDE}\n"
)
IMPORT = "@harness/WORKFLOW.md"
POINTER = (
    "\n> Shared workflow, the same in every project (leader role, gates, issues lane):\n"
    "> `harness/WORKFLOW.md`.\n"
)
RE_RULE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?!=)")


class MigrationError(Exception):
    """Refused: nothing was changed."""


def _blocks(lines: list[str]) -> dict[str, tuple[int, int, int]]:
    """target -> (first comment line, rule line, end) of each rule with its recipe."""
    found: dict[str, tuple[int, int, int]] = {}
    for i, line in enumerate(lines):
        m = RE_RULE.match(line)
        if not m or line.startswith(".") or "%" in m.group(1):
            continue
        end = i + 1
        while end < len(lines) and lines[end].startswith("\t"):
            end += 1
        first = i
        while first > 0 and lines[first - 1].startswith("#"):
            first -= 1
        found[m.group(1)] = (first, i, end)
    return found


def _recipe(lines: list[str], block: tuple[int, int, int]) -> list[str]:
    return [line.rstrip() for line in lines[block[1] : block[2]]]


# Earlier harness.mk recipes, still recognised (exactly) as not customised: a
# project Makefile copied before a change must migrate after it.
LEGACY_RECIPES: dict[str, tuple[tuple[str, ...], ...]] = {
    # Before the per-repo flock (issue #52).
    "check": (
        (
            "check:",
            "\t@rc=0; \\",
            "\t$(MAKE) --no-print-directory -k verify || rc=1; \\",
            "\t./harness/check.sh || rc=1; \\",
            "\texit $$rc",
        ),
    ),
}


def _is_shared(name: str, recipe: list[str], current: list[str]) -> bool:
    return recipe == current or tuple(recipe) in LEGACY_RECIPES.get(name, ())


def _harness_recipes() -> dict[str, list[str]]:
    lines = HARNESS_MK.read_text("utf-8").splitlines()
    return {name: _recipe(lines, b) for name, b in _blocks(lines).items()}


def migrated_makefile(text: str) -> str | None:
    """The migrated Makefile text, or None when it is already migrated. Raises."""
    if LEGACY_GUARD in text:
        text = text.replace(LEGACY_GUARD, GUARD)
        if text.splitlines() and not any(
            name in _harness_recipes() for name in _blocks(text.splitlines())
        ):
            return text
    lines = text.splitlines()
    shared = _harness_recipes()
    blocks = {name: b for name, b in _blocks(lines).items() if name in shared}
    custom = [
        name for name, b in blocks.items() if not _is_shared(name, _recipe(lines, b), shared[name])
    ]
    if custom:
        raise MigrationError(
            "; ".join(f"{name}: customised in Makefile" for name in sorted(custom))
            + " (move the custom part into a project target, then re-run)"
        )
    if INCLUDE in lines and not blocks:
        return None
    drop: set[int] = set()
    for first, _, end in blocks.values():
        stop = end + 1 if end < len(lines) and not lines[end].strip() else end
        drop |= set(range(first, stop))
    kept = [line for i, line in enumerate(lines) if i not in drop]
    body = "\n".join(kept).rstrip("\n") + "\n"
    return body if INCLUDE in kept else body + GUARD


def _origins(root: Path) -> dict[str, str]:
    out = subprocess.run(
        ["make", "-pn", "-f", "Makefile", "__harness_none__"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    found: dict[str, str] = {}
    current = None
    for line in out.splitlines():
        rule = re.match(r"^([A-Za-z0-9_.-]+):", line)
        if rule:
            current = rule.group(1)
            continue
        src = re.match(r"^#\s+recipe to execute \(from '([^']+)', line \d+\):", line)
        if src and current:
            found[current] = src.group(1)
    return found


def _verify(root: Path) -> None:
    origins = _origins(root)
    wrong = [t for t in _harness_recipes() if origins.get(t) != "harness/harness.mk"]
    wrong += [t for t, src in origins.items() if src not in ("Makefile", "harness/harness.mk")]
    if wrong:
        raise MigrationError(f"make -pn shows unexpected definitions: {', '.join(sorted(wrong))}")


STATES = ("installed", "absent", "nofile")


def _check_entry(tset: str, rel: str, entry: object) -> str | None:
    """Why one record entry is invalid, or None."""
    tpl = next((t for t in _templates.load(tset) if t.path == rel), None)
    if tpl is None:
        return f"{rel}: not a managed file"
    if not isinstance(entry, dict):
        return f"{rel}: entry is not an object"
    if entry.get("id") != tpl.id or entry.get("mode") != tpl.mode:
        return f"{rel}: id/mode do not match the manifest ({tpl.id}, {tpl.mode})"
    state = entry.get("state")
    if state not in STATES:
        return f"{rel}: unknown state {state!r}"
    return _check_installed(rel, tpl, entry) if state == "installed" else None


def _check_installed(rel: str, tpl: _templates.Template, entry: dict[str, Any]) -> str | None:
    version = entry.get("version")
    if not isinstance(version, str) or version not in tpl.versions:
        return f"{rel}: unknown version {version!r}"
    if entry.get("sha256") != _templates.sha256(tpl.versions[version]):
        return f"{rel}: sha256 does not match version {version}"
    return None


def _read_record(root: Path) -> dict[str, Any] | None:
    """The validated record, None when absent. Raises MigrationError when invalid."""
    path = root / RECORD
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MigrationError(f"{RECORD} is not valid JSON ({exc}); resolve by hand") from exc
    problems: list[str] = []
    if not isinstance(data, dict):
        raise MigrationError(f"{RECORD} is not a JSON object; resolve by hand")
    level, tset, entries = data.get("migration_level"), data.get("set"), data.get("templates")
    if not isinstance(level, int) or isinstance(level, bool) or level < 0:
        problems.append(f"migration_level {level!r} is not a non-negative integer")
    if tset not in _templates.SETS:
        problems.append(f"set {tset!r} is not one of {', '.join(_templates.SETS)}")
    if not isinstance(entries, dict):
        problems.append("templates is not an object")
    elif tset in _templates.SETS:
        problems += [p for rel, e in entries.items() if (p := _check_entry(str(tset), rel, e))]
        problems += [
            f"{t.path}: missing from the record"
            for t in _templates.load(str(tset))
            if t.path not in entries
        ]
    if problems:
        raise MigrationError(f"{RECORD} is invalid: " + "; ".join(problems) + "; resolve by hand")
    return data


# ── backups out of git (user decision A: .git/info/exclude) ──────────────────

IGNORE_ENTRY = ".harness/migrations/"


def _git(root: Path, *args: str) -> str | None:
    res = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=False
    )
    return res.stdout.strip() if res.returncode == 0 else None


def exclude_target(root: Path) -> tuple[Path, str] | None:
    """(info/exclude file, anchored entry) for the repo holding root, None outside git."""
    top, git_path = (
        _git(root, "rev-parse", "--show-toplevel"),
        _git(root, "rev-parse", "--git-path", "info/exclude"),
    )
    if not top or not git_path:
        return None
    rel = root.resolve().relative_to(Path(top).resolve()).as_posix()
    prefix = "/" if rel == "." else f"/{rel}/"
    path = Path(git_path) if Path(git_path).is_absolute() else root / git_path
    return path.resolve(), prefix + IGNORE_ENTRY


def _exclude_plan(root: Path) -> str:
    target = exclude_target(root)
    if target is None:
        return (
            f"not a git repo: backups in {IGNORE_ENTRY} are not ignored; if you run git init "
            f"later, add {IGNORE_ENTRY} to .git/info/exclude before git add"
        )
    path, entry = target
    present = path.is_file() and entry in path.read_text("utf-8").splitlines()
    return f"{entry} already in {path}" if present else f"add {entry} to {path} (local, git-only)"


def _add_exclude(root: Path) -> None:
    target = exclude_target(root)
    if target is None:
        return
    path, entry = target
    text = path.read_text("utf-8") if path.is_file() else ""
    if entry in text.splitlines():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if not text or text.endswith("\n") else "\n"
    path.write_text(f"{text}{sep}{entry}\n", "utf-8")


# ── migration level: WARN only in M0a (plan 0.7) ─────────────────────────────


def check_level(root: Path) -> int:
    """Report the project's migration level; never fails (WARN only)."""
    hint = f"bash {TOOLKIT}/install.sh {root} --migrate (dry-run first)"
    try:
        record = _read_record(root)
    except MigrationError as exc:
        say("WARN", f"{exc} -- see {hint}")
        return 0
    if record is None:
        say("WARN", f"no {RECORD}: register the copied harness files with {hint}")
        return 0
    level = int(record["migration_level"])
    if level < REQUIRED_MIGRATION_LEVEL:
        say("WARN", f"migration level {level} < required {REQUIRED_MIGRATION_LEVEL}: run {hint}")
    else:
        say("OK", f"migration level {level} (required {REQUIRED_MIGRATION_LEVEL})")
    return 0


def _with_fragment(tpl: _templates.Template, text: str) -> str:
    """`text` with the current version of an absent fragment added."""
    if tpl.id == "claude-import":
        return f"{IMPORT}\n\n" + text
    if tpl.id == "agents-pointer":
        return text.rstrip("\n") + "\n" + POINTER
    raise MigrationError(f"{tpl.path}: no rule to add fragment {tpl.id}")


def build_record(root: Path, tset: str, texts: dict[str, str]) -> dict[str, Any]:
    """The record describing `texts` (rel path -> content; missing = file absent)."""
    entries: dict[str, Any] = {}
    for tpl in _templates.load(tset):
        if tpl.path in texts:
            text = texts[tpl.path]
            if tpl.mode == "whole":
                ver = next((v for v, b in tpl.versions.items() if b == text), None)
                state = _templates.State("installed", ver) if ver else _templates.State("stop")
            else:
                ver = _templates.fragment_version(tpl, text)
                state = _templates.State("installed" if ver else "absent", ver)
        else:
            state = _templates.State("absent" if tpl.mode == "whole" else "nofile")
        entries[tpl.path] = _templates.record_entry(tpl, state)
    return {"migration_level": MIGRATION_LEVEL_RECORDED, "set": tset, "templates": entries}


def _plan(root: Path) -> dict[str, str]:
    """Every harness-owned file the migration would write, with its new text."""
    record = _read_record(root)
    tset = str(record["set"]) if record else _templates.detect_set(root)
    templates = _templates.load(tset)
    recorded = record["templates"] if record else {}
    states = {t.path: _templates.classify(t, root, recorded.get(t.path)) for t in templates}
    problems = [s.problem for s in states.values() if s.kind == "stop"]
    if problems:
        raise MigrationError("\n".join(problems) + "\nnothing was changed; resolve by hand")
    planned: dict[str, str] = {}
    new = migrated_makefile((root / "Makefile").read_text("utf-8"))
    if new is not None:
        planned["Makefile"] = new
    for tpl in templates:
        state, path = states[tpl.path], root / tpl.path
        if tpl.mode == "whole" and (state.kind == "absent" or state.version != tpl.current):
            planned[tpl.path] = tpl.current_text
        elif tpl.mode == "fragments" and state.kind == "absent" and tpl.path != "Makefile":
            planned[tpl.path] = _with_fragment(tpl, path.read_text("utf-8"))
    texts = {
        t.path: planned[t.path] if t.path in planned else (root / t.path).read_text("utf-8")
        for t in templates
        if t.path in planned or (root / t.path).is_file()
    }
    new_record = build_record(root, tset, texts)
    if record:
        new_record["migration_level"] = record.get("migration_level", 0)
    if new_record != record:
        planned[RECORD.as_posix()] = json.dumps(new_record, indent=2) + "\n"
    return planned


def register(root: Path, tset: str) -> int:
    """Fresh install: record what install.sh copied (no backup: nothing existed)."""
    path = root / RECORD
    if path.exists():
        say("OK", f"{RECORD} kept")
        return 0
    templates = _templates.load(tset)
    problems = [
        s.problem for t in templates if (s := _templates.classify(t, root, None)).kind == "stop"
    ]
    if problems:
        say("WARN", "record not written: " + "; ".join(problems) + " (run --migrate)")
        return 0
    texts = {
        t.path: (root / t.path).read_text("utf-8") for t in templates if (root / t.path).is_file()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_record(root, tset, texts), indent=2) + "\n", "utf-8")
    return 0


# ── links: verified, never written (plan 0.7, 0.11 d) ────────────────────────


def expected_links(root: Path) -> dict[str, Path]:
    """rel link path -> toolkit path it must resolve to. The single place the T2-S
    resolver will extend to the per-skill shape (Track 2); today: directory shape."""
    if (root / ".claude/skills").is_dir() and not (root / ".claude/skills").is_symlink():
        raise MigrationError(
            ".claude/skills is a real directory (per-skill shape): not supported until the "
            "T2-S resolver exists; nothing was changed"
        )
    return {rel: TOOLKIT / target for rel, target in LINKS.items()}


def check_links(root: Path) -> None:
    wrong = []
    for rel, target in expected_links(root).items():
        path = root / rel
        if not path.is_symlink():
            wrong.append(f"{rel}: missing (expected a link to {target})")
        elif path.resolve() != target.resolve():
            wrong.append(f"{rel}: points to {os.readlink(path)} (expected {target})")
    if wrong:
        raise MigrationError(
            "; ".join(wrong) + f" -- fix the links first (bash {TOOLKIT}/install.sh {root}),"
            " the migration never changes links"
        )


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOME") or Path.home() / ".claude")


def link_snapshot(root: Path) -> dict[str, tuple[str, str]]:
    """Type + exact (unresolved) target of every link in the project and ~/.claude."""
    snap: dict[str, tuple[str, str]] = {}
    for label, base in (("project", root), ("claude_home", _claude_home())):
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for name in dirnames + filenames:
                path = Path(dirpath) / name
                if path.is_symlink():
                    snap[f"{label}:{path.relative_to(base).as_posix()}"] = (
                        "link",
                        os.readlink(path),
                    )
    return snap


# ── transaction ──────────────────────────────────────────────────────────────


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_target(root: Path, rel: str) -> Path:
    """Allow-listed path whose own entry and parents inside the project are not links."""
    if rel not in ALLOWED and not rel.startswith(".harness/"):
        raise MigrationError(f"{rel}: not a harness-owned file")
    path = root / rel
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise MigrationError(f"{rel}: {part.relative_to(root)} is a link; refusing to write")
    return path


def _check_backup_dir(root: Path, name: str | None = None) -> None:
    """.harness/, .harness/migrations/ and the migration's own dir must be real dirs
    (or absent): a link there would read or write backups outside the project."""
    rels = [MIGRATIONS.parent, MIGRATIONS] + ([MIGRATIONS / name] if name else [])
    for rel in rels:
        if (root / rel).is_symlink():
            raise MigrationError(f"{rel.as_posix()} is a link; refusing (nothing was changed)")


def _confirm(yes: bool, what: str) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise MigrationError("no terminal to confirm: re-run with --yes (human only)")
    if input(f"{what} [y/N] ").strip().lower() != "y":
        raise MigrationError("not confirmed")


def _diff(root: Path, planned: dict[str, str]) -> str:
    out: list[str] = []
    for rel, new in planned.items():
        path = root / rel
        old = path.read_text("utf-8") if path.is_file() else ""
        out += difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            "/dev/null" if not path.exists() else f"a/{rel}",
            f"b/{rel}",
        )
    return "".join(out)


def _write_manifest(backup: Path, manifest: dict[str, Any]) -> None:
    """Atomic: the old manifest stays intact if the write fails."""
    tmp = backup / ".manifest.json.m0a-tmp"
    try:
        tmp.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
        os.replace(tmp, backup / "manifest.json")
    finally:
        tmp.unlink(missing_ok=True)


def _restore(root: Path, backup: Path, entries: dict[str, Any], done: list[str]) -> None:
    for rel in reversed(done):
        path = root / rel
        if entries[rel]["existed"]:
            shutil.copy2(backup / "files" / rel, path)
        elif path.exists():
            path.unlink()
    for d in sorted(entries.get("__dirs__", []), reverse=True):
        with contextlib.suppress(OSError):
            (root / d).rmdir()


def _apply(root: Path, planned: dict[str, str], name: str) -> Path:
    """Write every planned file or none; returns the backup dir (kept)."""
    targets = {rel: _safe_target(root, rel) for rel in planned}
    before = link_snapshot(root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = root / MIGRATIONS / f"{stamp}-{name}"
    created_dirs = [
        p.relative_to(root).as_posix()
        for rel, t in targets.items()
        for p in reversed(t.parents)
        if root in p.parents and not p.exists() and not rel.startswith(".harness/")
    ]
    backup.mkdir(parents=True)
    entries: dict[str, Any] = {}
    for rel, path in targets.items():
        entries[rel] = {"existed": path.is_file(), "new_sha256": _sha(planned[rel].encode())}
        if path.is_file():
            (backup / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup / "files" / rel)
    entries["__dirs__"] = sorted(set(created_dirs))
    manifest = {"migration": name, "status": "applying", "entries": entries}
    _write_manifest(backup, manifest)
    done: list[str] = []
    tmps: list[Path] = []
    try:
        for rel, path in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.m0a-tmp")
            tmp.write_text(planned[rel], "utf-8")
            tmps.append(tmp)
        for rel, path in targets.items():
            os.replace(path.with_name(f".{path.name}.m0a-tmp"), path)
            done.append(rel)
        if "Makefile" in planned:
            _verify(root)
        if link_snapshot(root) != before:
            raise MigrationError("links changed during the migration")
        _write_manifest(backup, {**manifest, "status": "applied"})
    except (OSError, MigrationError) as exc:
        for tmp in tmps:
            tmp.unlink(missing_ok=True)
        _restore(root, backup, entries, done)
        with contextlib.suppress(OSError):  # stays "applying" (never "applied") if this fails
            _write_manifest(backup, {**manifest, "status": "failed"})
        raise MigrationError(f"{exc}; every file restored (backup kept in {backup})") from exc
    return backup


def migrate(root: Path, apply: bool = False, yes: bool = False) -> int:
    check_links(root)
    _check_backup_dir(root)
    if not (root / "Makefile").is_file():
        raise MigrationError("the project has no Makefile")
    planned = _plan(root)
    if not planned:
        say("OK", "already migrated: nothing to change")
        return 0
    if not apply:
        print(_diff(root, planned), end="")
        say("OK", f"dry-run, nothing written; would change: {', '.join(planned)}")
        say("OK", f"backups: {_exclude_plan(root)}")
        print(f"to write it: bash {TOOLKIT}/install.sh {root} --migrate --apply")
        return 0
    _confirm(yes, f"write {', '.join(planned)}?")
    note = _exclude_plan(root)
    try:  # before any project file: backups never appear unignored
        _add_exclude(root)
    except OSError as exc:
        raise MigrationError(
            f"cannot write the info/exclude entry ({exc}); nothing changed"
        ) from exc
    backup = _apply(root, planned, "templates")
    say("OK" if not note.startswith("not a git") else "WARN", f"backups: {note}")
    say("OK", f"migrated: {', '.join(planned)} (rollback: --rollback {backup.name})")
    return 0


def _check_backup_parts(backup: Path, entries: dict[str, Any], files: list[str]) -> None:
    """Every backed-up file is present and readable before anything is undone."""
    missing = []
    for rel in files:
        if not entries[rel]["existed"]:
            continue
        try:
            (backup / "files" / rel).read_bytes()
        except OSError:
            missing.append(rel)
    if missing:
        raise MigrationError(
            f"backup incomplete or unreadable: {', '.join(missing)}; nothing undone"
        )


def _put_back(root: Path, applied: dict[str, bytes | None]) -> None:
    """Write the applied state back (file contents in memory; None = was absent)."""
    for rel, data in applied.items():
        path = root / rel
        if data is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.m0a-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


def _restore_all_or_nothing(root: Path, backup: Path, manifest: dict[str, Any]) -> None:
    """Restore every file and mark the manifest rolled_back, or put the applied
    state back (the manifest stays "applied": the rollback can be retried)."""
    entries: dict[str, Any] = manifest["entries"]
    files = [rel for rel in entries if rel != "__dirs__"]
    applied = {rel: (root / rel).read_bytes() if (root / rel).is_file() else None for rel in files}
    before = link_snapshot(root)
    try:
        _restore(root, backup, entries, files)
        if link_snapshot(root) != before:
            raise MigrationError("links changed during the rollback")
        _write_manifest(backup, {**manifest, "status": "rolled_back"})
    except (OSError, MigrationError) as exc:
        _put_back(root, applied)
        raise MigrationError(f"{exc}; the migrated files were put back, nothing undone") from exc


def rollback(root: Path, name: str, apply: bool = False, yes: bool = False) -> int:
    backup = root / MIGRATIONS / name
    if "/" not in name:
        _check_backup_dir(root, name)
    if "/" in name or not (backup / "manifest.json").is_file():
        raise MigrationError(f"no migration named {name!r} in {MIGRATIONS}")
    manifest = json.loads((backup / "manifest.json").read_text("utf-8"))
    if manifest.get("status") != "applied":
        raise MigrationError(f"{name} is {manifest.get('status')!r}, not applied")
    entries: dict[str, Any] = manifest["entries"]
    files = [rel for rel in entries if rel != "__dirs__"]
    _check_backup_parts(backup, entries, files)
    edited = []
    for rel in files:
        path = _safe_target(root, rel)
        if not path.is_file() or _sha(path.read_bytes()) != entries[rel]["new_sha256"]:
            edited.append(rel)
    if edited:
        raise MigrationError(
            f"changed since the migration: {', '.join(edited)}; resolve by hand, nothing undone"
        )
    if not apply:
        for rel in files:
            say("OK", f"would {'restore' if entries[rel]['existed'] else 'delete'} {rel}")
        print(f"to undo it: bash {TOOLKIT}/install.sh {root} --rollback {name} --apply")
        return 0
    _confirm(yes, f"undo {name}?")
    _restore_all_or_nothing(root, backup, manifest)
    say("OK", f"rolled back {name}: {', '.join(files)}")
    return 0


def main(argv: list[str]) -> int:
    flags = {a for a in argv if a in ("--apply", "--yes")}
    args = [a for a in argv if a not in flags]
    apply, yes = "--apply" in flags, "--yes" in flags
    try:
        if len(args) == 2 and args[0] == "migrate":
            return migrate(Path(args[1]).resolve(), apply, yes)
        if len(args) == 2 and args[0] == "level":
            return check_level(Path(args[1]).resolve())
        if len(args) == 3 and args[0] == "register" and args[2] in _templates.SETS:
            return register(Path(args[1]).resolve(), args[2])
        if len(args) == 3 and args[0] == "rollback":
            return rollback(Path(args[1]).resolve(), args[2], apply, yes)
    except MigrationError as exc:
        say("FAIL", f"refused: {exc}", sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
