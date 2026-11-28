#!/usr/bin/env python3
"""The release plan (releases, delivery 1: docs/design/releases.md §5).

docs/releases.md is written by the claude-planner in the release discovery (after
the roadmap, in an activated project only; asked, never blocking), reviewed by Codex
(`review_with_codex.sh releases` → progress/releases_review.md, RELEASES_APPROVED |
CHANGES_REQUESTED), approved by the user and sealed (progress/releases.sha256: the
digest of docs/releases.md and of the roadmap seal it was approved with).

No execution gate depends on it: a change only warns (`make check`, `make status`).
Only publishing (delivery 2) will require an approved, current plan.

Usage: python3 _releases.py ready | seal | check
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import _changelog
import _harness
from _status import say

DOC = Path("docs/releases.md")
REVIEW = Path("progress/releases_review.md")
SEAL = Path("progress/releases.sha256")
ROADMAP_SEAL = Path("progress/plan_roadmap.sha256")
APPROVED = "RELEASES_APPROVED"
REJECTED = "CHANGES_REQUESTED"
GATE = "./harness/review_with_codex.sh"
PLACEHOLDER = "‹"
SECTIONS = ("Planned releases", "Compatibility", "Patches and early releases")
FIELDS = ("versioning", "version source", "audience")
NO_ROADMAP = "none"
RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_FIELD = re.compile(r"^- +(?P<key>[a-z ]+?) *:(?P<value>.*)$")

OK_STATE = "approved"
CHANGED = "changed"
UNSEALED = "unsealed"
MISSING = "missing"
DRAFT = "draft"
UNREVIEWED = "unreviewed"
REJECTED_STATE = "rejected"


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """({header field: value}, {## section: its non-blank lines}), comments dropped."""
    fields: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in RE_COMMENT.sub("", text).splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None:
            current += [line.strip()] if line.strip() else []
        elif match := RE_FIELD.match(line):
            fields[match["key"]] = match["value"].strip()
    return fields, sections


def _applies(value: str | None) -> tuple[bool, list[str]]:
    if value == "yes":
        return True, []
    if value is not None and re.fullmatch(r"no:\s*\S.*", value):
        return False, []
    return False, [f"{DOC}: `- applies:` must be `yes` or `no: <why>`"]


def _source_problems(value: str, python: bool) -> list[str]:
    wanted = "pyproject.toml" if python else "VERSION"
    words = value.split()
    if words and words[0] == wanted:
        return []
    stack = "a Python project (pyproject.toml)" if python else "a project without pyproject.toml"
    return [f"{DOC}: `- version source:` of {stack} is `{wanted}` (one source of the version)"]


def ready_problems_text(text: str, python: bool) -> list[str]:
    """Why this release plan cannot go through the releases gate (empty: it can)."""
    if PLACEHOLDER in RE_COMMENT.sub("", text):
        return [f"{DOC} still has template placeholders (‹…›): fill in every one"]
    fields, sections = _parse(text)
    applies, problems = _applies(fields.get("applies"))
    if not applies:
        return problems
    for key in FIELDS:
        if not fields.get(key):
            problems.append(f"{DOC} has no `- {key}:` line")
    if fields.get("version source"):
        problems += _source_problems(fields["version source"], python)
    for name in SECTIONS:
        if not sections.get(name):
            problems.append(f"{DOC} has no filled-in `## {name}` section")
    return problems


def ready_problems(root: Path) -> list[str]:
    text = _text(root / DOC)
    if text is None:
        return [f"no {DOC}: the release discovery writes it first (claude-planner)"]
    return ready_problems_text(text, (root / "pyproject.toml").is_file())


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return NO_ROADMAP


def _sealed(root: Path) -> list[str]:
    return (_text(root / SEAL) or "").splitlines()


def sealed_matches(root: Path) -> bool:
    lines = _sealed(root)
    return bool(lines) and lines[0] == f"{_digest(root / DOC)}  {DOC.as_posix()}"


def roadmap_changed(root: Path) -> bool:
    lines = _sealed(root)
    return len(lines) > 1 and lines[1].split()[0] != _digest(root / ROADMAP_SEAL)


def state(root: Path) -> str:
    verdict = _harness.read_verdict(root / REVIEW)
    if verdict == APPROVED and (root / SEAL).exists():
        return OK_STATE if sealed_matches(root) else CHANGED
    if not (root / DOC).is_file():
        return MISSING
    if verdict == APPROVED:
        return UNSEALED
    if verdict == REJECTED:
        return REJECTED_STATE
    return DRAFT if ready_problems(root) else UNREVIEWED


LABELS = {
    OK_STATE: APPROVED,
    CHANGED: f"changed since {APPROVED} ({SEAL}): releases gate again",
    UNSEALED: f"{APPROVED}, not sealed",
    MISSING: f"no {DOC}",
    DRAFT: f"{DOC} not filled in yet",
    UNREVIEWED: "pending Codex review",
    REJECTED_STATE: f"{REJECTED} ({REVIEW})",
}


def label(root: Path) -> str | None:
    """The `Releases` line of `make status` (None: the changelog is not activated)."""
    return LABELS[state(root)] if _changelog.activated(root) else None


def discovery_due(root: Path) -> bool:
    """The release discovery is asked for: activated, roadmap approved, no plan yet."""
    roadmap = _harness.read_verdict(root / _harness.ROADMAP_REVIEW) == "ROADMAP_APPROVED"
    return roadmap and _changelog.activated(root) and state(root) in (MISSING, DRAFT)


def _incompatible_warnings(root: Path) -> list[str]:
    text = _text(root / DOC) or ""
    frags, _ = _changelog.read_fragments(root)
    return [
        f"incompatible change in {_changelog.FRAGMENTS}/{f.ident}.md that {DOC} does not "
        f"mention ({f.ident}): review the release plan (version policy)"
        for f in frags
        if f.incompatible and not re.search(rf"(?<![\w.-]){re.escape(f.ident)}(?![\w-])", text)
    ]


def warnings(root: Path) -> list[str]:
    """The review triggers that can be checked (WARN only), activated projects only."""
    st = state(root)
    out = []
    if st == CHANGED:
        out.append(
            f"{DOC} changed since {APPROVED}: its approval is void; releases gate "
            f"again ({GATE} releases) and the user's OK (WARN only)"
        )
    elif st == UNSEALED:
        out.append(f"{DOC} is {APPROVED} but not sealed (human: {GATE} seal releases)")
    if st in (OK_STATE, CHANGED) and roadmap_changed(root):
        out.append(
            f"the roadmap changed since {DOC} was approved (scope or phases): review "
            "the release plan"
        )
    if st in (OK_STATE, CHANGED, UNSEALED):
        out += _incompatible_warnings(root)
    return out


# ── commands ─────────────────────────────────────────────────────────────────


def ready(root: Path) -> int:
    problems = ready_problems(root)
    for problem in problems:
        say("FAIL", f"releases gate: {problem}", sys.stderr)
    return 1 if problems else 0


def seal(root: Path) -> int:
    """Seal the approved docs/releases.md with the roadmap seal it was approved with."""
    if _harness.read_verdict(root / REVIEW) != APPROVED:
        say("FAIL", f"cannot seal the release plan: it is not {APPROVED}", sys.stderr)
        return 1
    if not (root / DOC).is_file():
        say("FAIL", f"cannot seal the release plan: no {DOC}", sys.stderr)
        return 1
    text = (
        f"{_digest(root / DOC)}  {DOC.as_posix()}\n"
        f"{_digest(root / ROADMAP_SEAL)}  {ROADMAP_SEAL.name}\n"
    )
    (root / SEAL).write_text(text, encoding="utf-8")
    say("OK", f"sealed the approved release plan -> {SEAL}")
    return 0


def check(root: Path) -> int:
    for message in warnings(root) if _changelog.activated(root) else []:
        say("WARN", message)
    return 0


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    commands = {"ready": ready, "seal": seal, "check": check}
    if len(argv) != 1 or argv[0] not in commands:
        print("usage: _releases.py ready | seal | check", file=sys.stderr)
        return 2
    return commands[argv[0]](root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
