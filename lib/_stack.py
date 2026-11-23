#!/usr/bin/env python3
"""Stack step (issue #115): docs/stack.md, its gate and its place before the discovery.

The leader asks the user for the project's stack, one question at a time (project
type and language first; they decide which categories follow), the claude-planner
writes docs/stack.md, Codex reviews it (`review_with_codex.sh stack` →
progress/stack_review.md, STACK_APPROVED | CHANGES_REQUESTED) and the harness seals
it (progress/stack.sha256). Any later change to docs/stack.md voids the approval.

Who is held to it (`required`): every project but an existing one, with or
without docs/stack.md. An existing project (its roadmap or plan approved with no
stack approval nor seal: planned before the stack step) only gets a WARN, never a
FAIL, also when it has no docs/stack.md at all.

Order: while the stack is not approved and intact, the roadmap gate refuses to run
and, once the discovery has written a phase (docs/plan/phase-*.md), `make check`
fails. Before that, a pending stack is not a failure, so the startup protocol runs.

Dependency drift (WARN only): the first-level `[project] dependencies` of
pyproject.toml against the `## Key dependencies` list of docs/stack.md. Other
manifests are not read (best effort, a follow-up).

Usage: python3 _stack.py <command>
  ready          exit 0 if docs/stack.md is filled in for the stack gate
  roadmap-ready  exit 0 unless the roadmap gate must wait for the stack
  check          the `make check` rules (FAIL/WARN lines); exit 1 on a FAIL
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tomllib
from pathlib import Path

import _harness
from _status import Kind, say

STACK_DOC = Path("docs/stack.md")
STACK_REVIEW = Path("progress/stack_review.md")
STACK_SEAL = Path("progress/stack.sha256")
ROADMAP_SEAL = Path("progress/plan_roadmap.sha256")
APPROVED = "STACK_APPROVED"
REJECTED = "CHANGES_REQUESTED"
GATE = "./harness/review_with_codex.sh"
SECTIONS = (
    "Project type",
    "Architecture",
    "Persistence",
    "Framework",
    "Toolchain",
    "Deploy",
    "Key dependencies",
)
DEPS_SECTION = "Key dependencies"
PLACEHOLDER = "‹"
RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_ITEM = re.compile(r"^- +(?P<key>[^:]+?) *:(?P<value>.*)$")
RE_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
NO_DEPS = {"none", "not-applicable"}

# States of the stack (state()).
OK_STATE = "approved"  # STACK_APPROVED and the seal matches docs/stack.md
CHANGED = "changed"  # sealed, then docs/stack.md changed: the approval is void
UNSEALED = "unsealed"  # STACK_APPROVED, no seal yet (human: seal stack)
MISSING = "missing"  # no docs/stack.md
DRAFT = "draft"  # docs/stack.md still has placeholders or a missing category
UNREVIEWED = "unreviewed"  # filled in, no verdict yet
REJECTED_STATE = "rejected"  # CHANGES_REQUESTED


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _sections(text: str) -> dict[str, list[str]]:
    """{heading: its non-blank lines} of the `## ` sections, HTML comments dropped."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in RE_COMMENT.sub("", text).splitlines():
        if line.startswith("## "):
            current = out.setdefault(line[3:].strip(), [])
        elif current is not None and line.strip():
            current.append(line.strip())
    return out


def ready_problems(root: Path) -> list[str]:
    """Why docs/stack.md cannot go through the stack gate yet (empty: it can)."""
    text = _text(root / STACK_DOC)
    if text is None:
        return [f"no {STACK_DOC}: the stack step writes it first (claude-planner)"]
    problems = []
    if PLACEHOLDER in RE_COMMENT.sub("", text):
        problems.append(f"{STACK_DOC} still has template placeholders (‹…›): fill in every one")
    found = _sections(text)
    for name in SECTIONS:
        if name not in found:
            problems.append(f"{STACK_DOC} has no `## {name}` section")
        elif not found[name]:
            problems.append(f"{STACK_DOC} `## {name}` is empty (a decision and why)")
    return problems


def sealed_matches(root: Path) -> bool:
    try:
        recorded = (root / STACK_SEAL).read_text(encoding="utf-8").split()
        current = hashlib.sha256((root / STACK_DOC).read_bytes()).hexdigest()
    except OSError:
        return False
    return recorded == [current, STACK_DOC.as_posix()]


def state(root: Path) -> str:
    if _harness.assumed("stack"):  # the stack gate's counterfactual run (issue #27)
        return OK_STATE
    verdict = _harness.read_verdict(root / STACK_REVIEW)
    if verdict == APPROVED and (root / STACK_SEAL).exists():
        return OK_STATE if sealed_matches(root) else CHANGED
    if not (root / STACK_DOC).is_file():
        return MISSING
    if verdict == APPROVED:
        return UNSEALED
    if verdict == REJECTED:
        return REJECTED_STATE
    return DRAFT if ready_problems(root) else UNREVIEWED


def in_use(root: Path) -> bool:
    """The project has stack artifacts: docs/stack.md, a stack verdict or a seal."""
    return any((root / p).exists() for p in (STACK_DOC, STACK_REVIEW, STACK_SEAL))


def existing_project(root: Path) -> bool:
    """Planned before the stack step: its roadmap or plan (a phase) approved, and no
    stack seal nor stack approval (once approved, a project is held to its stack)."""
    if (root / STACK_SEAL).exists() or _harness.read_verdict(root / STACK_REVIEW) == APPROVED:
        return False
    roadmap = _harness.read_verdict(root / _harness.ROADMAP_REVIEW) == "ROADMAP_APPROVED"
    if roadmap or (root / ROADMAP_SEAL).exists():
        return True
    # An approved plan without a roadmap verdict (pre-roadmap or exempt legacy): a
    # phase approval or a phase seal.
    progress = root / "progress"
    if any(progress.glob("plan_phase_*.sha256")):
        return True
    return any(
        _harness.read_verdict(path) == "PHASE_APPROVED"
        for path in progress.glob("phase_review_*.md")
    )


def required(root: Path) -> bool:
    """The stack must be approved and intact before the discovery and the roadmap:
    every project but an existing one (issue #115), with or without docs/stack.md."""
    return not existing_project(root)


def discovery_started(root: Path) -> bool:
    return any((root / "docs" / "plan").glob("phase-*.md"))


def label(root: Path) -> str | None:
    """The `Stack` line of `make status`, or None when the project does not use it."""
    if not in_use(root) and existing_project(root):
        return None
    return {
        OK_STATE: APPROVED,
        CHANGED: f"changed since {APPROVED} ({STACK_SEAL})",
        UNSEALED: f"{APPROVED}, not sealed",
        MISSING: f"no {STACK_DOC}",
        DRAFT: f"{STACK_DOC} not filled in yet",
        UNREVIEWED: "pending Codex review",
        REJECTED_STATE: f"{REJECTED} ({STACK_REVIEW})",
    }[state(root)]


# ── dependency drift (WARN only) ─────────────────────────────────────────────


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def stack_dependencies(text: str) -> set[str]:
    names = set()
    for line in _sections(text).get(DEPS_SECTION, []):
        item = RE_ITEM.match(line)
        if item and PLACEHOLDER not in item["key"]:
            names.add(normalize(item["key"].strip("` ")))
    return names - NO_DEPS


def _requirement_names(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    names = set()
    for value in values:
        if isinstance(value, str) and (match := RE_REQUIREMENT.match(value)):
            names.add(normalize(match.group(1)))
    return names


def pyproject_dependencies(path: Path) -> tuple[set[str], set[str]]:
    """(first-level runtime dependencies, every declared one). Raises ValueError."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    runtime = _requirement_names(project.get("dependencies"))
    declared = set(runtime)
    for table in (project.get("optional-dependencies"), data.get("dependency-groups")):
        if isinstance(table, dict):
            for values in table.values():
                declared |= _requirement_names(values)
    return runtime, declared


def drift_warnings(root: Path) -> list[str]:
    manifest = root / "pyproject.toml"
    text = _text(root / STACK_DOC)
    if text is None or not manifest.is_file():
        return []
    try:
        runtime, declared = pyproject_dependencies(manifest)
    except ValueError as exc:
        return [f"pyproject.toml is unreadable, its dependency check was skipped: {exc}"]
    listed = stack_dependencies(text)
    out = [
        f"dependency drift: {name} is a [project] dependency in pyproject.toml but "
        f"{STACK_DOC} does not list it under `## {DEPS_SECTION}`"
        for name in sorted(runtime - listed)
    ]
    out += [
        f"dependency drift: {STACK_DOC} lists {name} under `## {DEPS_SECTION}` but "
        "pyproject.toml does not declare it"
        for name in sorted(listed - declared)
    ]
    return out


# ── commands ─────────────────────────────────────────────────────────────────


def _pending(st: str) -> str:
    return {
        CHANGED: (
            f"{STACK_DOC} changed since {APPROVED} ({STACK_SEAL}): its approval is void, "
            f"stack gate again: {GATE} stack"
        ),
        UNSEALED: f"{STACK_DOC} is {APPROVED} but not sealed (human: {GATE} seal stack)",
    }.get(st, f"{STACK_DOC} is not {APPROVED} yet ({st}): stack step, then {GATE} stack")


def check(root: Path) -> int:
    st = state(root)
    rc = 0
    if st == OK_STATE:
        say("OK", f"{STACK_DOC} is {APPROVED} and intact")
    elif existing_project(root) and not in_use(root):
        say(
            "WARN",
            f"no {STACK_DOC}: this project was planned before the stack step (issue "
            "#115); run the stack step to record its stack (WARN only)",
        )
        return 0
    elif existing_project(root):
        say(
            "WARN",
            f"{_pending(st)}. The roadmap or plan was approved before the stack step, so "
            "this only warns",
        )
    elif discovery_started(root):
        say(
            "FAIL",
            f"the discovery started (docs/plan/phase-*.md) but {_pending(st)}; the "
            "discovery and the roadmap wait for the stack",
        )
        rc = 1
    else:
        kind: Kind = "WARN" if st in (CHANGED, UNSEALED) else "SKIP"
        say(kind, f"{_pending(st)}; the discovery waits for it")
    if st not in (MISSING, DRAFT):
        for message in drift_warnings(root):
            say("WARN", message)
    return rc


def roadmap_ready(root: Path) -> int:
    st = state(root)
    if st == OK_STATE or not required(root):
        return 0
    say("FAIL", f"the roadmap gate waits for the stack: {_pending(st)}", sys.stderr)
    return 1


def ready(root: Path) -> int:
    problems = ready_problems(root)
    for problem in problems:
        say("FAIL", f"stack gate: {problem}", sys.stderr)
    return 1 if problems else 0


def seal(root: Path) -> int:
    """Seal the approved docs/stack.md (the stack gate, or the human in manual mode)."""
    if _harness.read_verdict(root / STACK_REVIEW) != APPROVED:
        say("FAIL", f"cannot seal the stack: it is not {APPROVED}", sys.stderr)
        return 1
    doc = root / STACK_DOC
    if not doc.is_file():
        say("FAIL", f"cannot seal the stack: no {STACK_DOC}", sys.stderr)
        return 1
    digest = hashlib.sha256(doc.read_bytes()).hexdigest()
    (root / STACK_SEAL).write_text(f"{digest}  {STACK_DOC.as_posix()}\n", encoding="utf-8")
    say("OK", f"sealed the approved stack -> {STACK_SEAL}")
    return 0


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    commands = {"ready": ready, "roadmap-ready": roadmap_ready, "check": check}
    if len(argv) != 1 or argv[0] not in commands:
        print("usage: _stack.py ready | roadmap-ready | check", file=sys.stderr)
        return 2
    return commands[argv[0]](root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
