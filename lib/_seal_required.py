#!/usr/bin/env python3
"""Required seals derived from project state (I1, plan item 5).

check_seals and _plan_seal.verify compare the seals that exist. This module derives
the seals that must exist, so removing one never looks healthy. New gaps are warnings
first (plan 0.5); only tampering with a seal that exists is a problem.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import _harness
import _legacy

Item = dict[str, Any]

DRAFT = "progress/issue_draft.md"
DRAFT_REVIEW = "progress/issue_draft_review.md"
DRAFT_SEAL = "progress/issue_draft.sha256"
SEAL_CMD = "./harness/review_with_codex.sh seal"


class Exemptions:
    """Legacy items whose gap is not reported (plan 0.6), only while the inventory verifies."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.status, self.reason = _legacy.verify(root)
        self.exempt: set[str] = set()
        self.gap_found = False

    def __call__(self, item_id: str) -> bool:
        """Asked only for an item with a seal gap: whether that gap is legacy."""
        self.gap_found = True
        if self.status != "OK" or not _legacy.is_legacy_unchanged(self.root, item_id):
            return False
        self.exempt.add(item_id)
        return True


@dataclass
class Findings:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_rel(leaf: str) -> str:
    return f"progress/acceptance_{leaf}.sha256"


def read_manifest(manifest: Path) -> tuple[dict[str, str], list[str]]:
    """({path: digest}, malformed-line problems) of a `<sha256>  <path>` seal file."""
    entries: dict[str, str] = {}
    problems: list[str] = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            problems.append(
                f"{manifest.name} line {number} is malformed: expected '<sha256>  <path>'"
            )
            continue
        entries[parts[1]] = parts[0]
    return entries, problems


def unlisted_tests(root: Path, leaf: str, manifest: Path) -> list[str]:
    """Current acceptance tests of a leaf that its readable seal does not list."""
    entries, malformed = read_manifest(manifest)
    if malformed:
        return []
    current = [p.relative_to(root).as_posix() for p in _harness.acceptance_files(root, leaf)]
    return [rel for rel in current if rel not in entries]


def _manifests(root: Path) -> list[tuple[str, Path]]:
    found = sorted((root / "progress").glob("acceptance_*.sha256"))
    return [(p.name.removeprefix("acceptance_").removesuffix(".sha256"), p) for p in found]


def has_unlisted_tests(root: Path) -> bool:
    return any(unlisted_tests(root, leaf, manifest) for leaf, manifest in _manifests(root))


def _check_completeness(root: Path, out: Findings, exempt: Exemptions) -> None:
    for leaf, manifest in _manifests(root):
        unlisted = unlisted_tests(root, leaf, manifest)
        if unlisted and exempt(leaf):
            continue
        for rel in unlisted:
            out.warnings.append(
                f"{rel} is not in its seal ({manifest.name}) (human: {SEAL_CMD} {leaf})"
            )


def _check_leaf(root: Path, leaf: Item, out: Findings, exempt: Exemptions) -> None:
    sid = str(leaf["id"])
    finished = bool(
        leaf["status"] == "done" or (leaf["status"] == "baseline" and _harness.complete(leaf))
    )
    approved = _harness.read_verdict(root / f"progress/plan_review_{sid}.md") == "PLAN_APPROVED"
    tests = _harness.acceptance_files(root, sid)
    if (root / manifest_rel(sid)).exists() or not (finished or (approved and tests)):
        return
    if exempt(sid):
        return
    if tests:
        out.warnings.append(f"acceptance tests of {sid} are not sealed (human: {SEAL_CMD} {sid})")
    else:
        out.warnings.append(
            f"leaf {sid} is {leaf['status']} with no acceptance test "
            f"(add tests/acceptance/test_{sid}.py, then human: {SEAL_CMD} {sid})"
        )


def _check_draft(root: Path, out: Findings, exempt: Exemptions) -> None:
    seal = root / DRAFT_SEAL
    if not seal.is_file():
        approved = _harness.read_verdict(root / DRAFT_REVIEW) == "DRAFT_APPROVED"
        if approved and not exempt(_legacy.DRAFT_ID):
            out.warnings.append(
                f"the issue draft is approved but not sealed (human: {SEAL_CMD} draft)"
            )
        return
    entries, malformed = read_manifest(seal)
    out.problems.extend(malformed)
    if malformed:
        return
    draft = root / DRAFT
    if not draft.is_file() or entries.get(DRAFT) != _sha256(draft):
        out.problems.append(
            f"{DRAFT} no longer matches its seal ({seal.name}): "
            "re-run its Codex review (./harness/review_with_codex.sh issue-draft)"
        )


def check(root: Path, exempt: Exemptions) -> Findings:
    """Every gap between the seals the state requires and the seals on disk."""
    out = Findings()
    if exempt.status == "FAIL":
        out.problems.append(f"legacy inventory is not valid, nothing is exempt: {exempt.reason}")
    for leaf in _harness.leaves():
        _check_leaf(root, leaf, out, exempt)
    _check_completeness(root, out, exempt)
    _check_draft(root, out, exempt)
    if exempt.status == "WARN" and exempt.gap_found:
        out.warnings.append(
            "legacy inventory not created "
            "(human: bash harness/legacy_inventory.sh create --at <commit>)"
        )
    return out
