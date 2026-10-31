#!/usr/bin/env python3
"""One-time migration of an installed project to the shared, symlinked files.

Projects installed before lib/harness.mk and lib/WORKFLOW.md existed have the
harness targets copied into their Makefile and the workflow rules copied into
their CLAUDE.md, so later harness changes never reached them. After this
migration they reach every project through the harness/ symlink.

Usage: python3 _upgrade.py migrate <project>     (install.sh <project> --migrate)

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

import re
import subprocess
import sys
from pathlib import Path

from _status import say

HARNESS_MK = Path(__file__).resolve().parent / "harness.mk"
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
    custom = [name for name, b in blocks.items() if _recipe(lines, b) != shared[name]]
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


def _migrate_makefile(root: Path) -> bool:
    path = root / "Makefile"
    old = path.read_text("utf-8")
    new = migrated_makefile(old)
    if new is None:
        return False
    path.write_text(new, "utf-8")
    try:
        _verify(root)
    except MigrationError:
        path.write_text(old, "utf-8")
        raise
    return True


def _migrate_docs(root: Path) -> list[str]:
    changed = []
    claude = root / "CLAUDE.md"
    if claude.is_file() and IMPORT not in claude.read_text("utf-8").splitlines():
        claude.write_text(f"{IMPORT}\n\n" + claude.read_text("utf-8"), "utf-8")
        changed.append("CLAUDE.md")
    agents = root / "AGENTS.md"
    if agents.is_file() and "harness/WORKFLOW.md" not in agents.read_text("utf-8"):
        agents.write_text(agents.read_text("utf-8").rstrip("\n") + "\n" + POINTER, "utf-8")
        changed.append("AGENTS.md")
    readme = root / ISSUES_DIR
    if not readme.is_file():
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(ISSUES_README.read_text("utf-8"), "utf-8")
        changed.append(ISSUES_DIR.as_posix())
    return changed


def migrate(root: Path) -> int:
    if not (root / "harness" / "harness.mk").is_file():
        raise MigrationError("harness/ is not linked in the project: run install.sh first")
    if not (root / "Makefile").is_file():
        raise MigrationError("the project has no Makefile")
    changed = ["Makefile"] if _migrate_makefile(root) else []
    changed += _migrate_docs(root)
    if changed:
        say("OK", f"migrated to the shared harness files: {', '.join(changed)}")
    else:
        say("OK", "already migrated: nothing to change")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "migrate":
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return migrate(Path(argv[1]).resolve())
    except MigrationError as exc:
        say("FAIL", f"migration refused, nothing changed: {exc}", sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
