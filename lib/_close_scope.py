#!/usr/bin/env python3
"""Explicit manifest of a close commit (Factory 1B).

The close of a subtask used to commit with `git add -A`, so anything else in the
work tree rode along. This module computes which changed paths belong to the
close of one subtask and reports the rest as a block with its reason. It never
widens the scope: a foreign path is the human's to move out, not ours to absorb.

Allowed for subtask X (N.M.K or ISS-n.k), owner O (task N.M or issue ISS-n):
  production   the spec's `files:` (normalized; a path with `..` is never allowed)
  unit tests   anything under tests/unit/ (the implementer's, not tied to one id)
  oracle       tests/acceptance/test_<id>.<ext> + progress/acceptance_<id>.sha256,
               for X and every id in its `migrates:`
  plan         the leaf spec, docs/plan/PLAN.md, feature_list.json and, for an
               issue, its header docs/plan/issues/ISS-n-<slug>.md
  reference    docs/reference/X-<slug>.md
  progress     review_X.md, plan_review_X.md, impl_X.md, close_X.msg,
               review_history/X.md, current.md, history.md, pr_O.md
  concept docs docs/architecture.md, docs/conventions.md (documenter.md step 1)
  chain        the harness bookkeeping of the chain roadmap -> phase NN | issue ISS-n
               -> leaf, by exact name (issue #40): the gate verdicts (roadmap_review,
               phase_review_NN | issue_review_ISS-n) and their review_history
               (roadmap, phase-NN | ISS-n), the plan seals (plan_roadmap,
               plan_phase_NN | plan_issue_ISS-n, issue_migrations_ISS-n.json), the
               verdict receipts verdict_<verdict>.sha256 of every verdict of the chain,
               the phase file docs/plan/phase-NN-<slug>.md found on disk, and every
               path listed in the approved seal plan_phase_NN | plan_issue_ISS-n.
               The first close of a project carries the approved roadmap and phase.
  legacy spec  a leaf without `spec` gets the one on disk by convention:
               docs/plan/phase-NN-<slug>/<id>-<slug>.md (or the issue's folder).

Paths are relative to the project (cwd), which may be a subdirectory of the
repository: a change outside it is reported as `:/<repo path>` and always blocks.

Usage (cwd = project root):
  python3 _close_scope.py check <id>   exit 0 in scope, 1 blocked (reasons on stderr)
  python3 _close_scope.py files <id>   same, and when in scope prints the allowed
                                       paths NUL-terminated on stdout (for `git add`)
  exit 2: unknown id / git failure.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import _harness
import _plan_seal

RE_LEAF_ID = re.compile(r"^(?:\d+\.\d+\.\d+|ISS-\d+\.\d+)$")
STAGED_IGNORED = {" ", "?", "!"}
FIXED = frozenset(
    {
        "docs/architecture.md",
        "docs/conventions.md",
        "docs/plan/PLAN.md",
        "feature_list.json",
        "progress/current.md",
        "progress/history.md",
    }
)


@dataclass(frozen=True)
class Change:
    """One entry of `git status --porcelain=v1 -z`."""

    path: str
    orig: str | None
    index: str
    worktree: str

    def paths(self) -> tuple[str, ...]:
        """A rename changes its source too; a copy leaves its source untouched."""
        renamed = self.orig is not None and "R" in (self.index, self.worktree)
        return (self.path, self.orig) if renamed and self.orig else (self.path,)

    @property
    def staged(self) -> bool:
        return self.index not in STAGED_IGNORED


@dataclass(frozen=True)
class ScopeReport:
    sid: str
    allowed: tuple[str, ...]
    disallowed: tuple[str, ...]
    staged_foreign: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.disallowed and not self.staged_foreign

    def explain(self) -> list[str]:
        lines = [
            f"{path!r} changed but is outside the close manifest of {self.sid} "
            "(not in files:, its oracle, plan, reference or progress): "
            "move it out of the work tree or plan it in a subtask"
            for path in self.disallowed
        ]
        lines += [
            f"{path!r} is already staged and is foreign to {self.sid}: "
            f"unstage it (git restore --staged -- {path!r})"
            for path in self.staged_foreign
        ]
        return lines


def parse_porcelain(raw: bytes) -> list[Change]:
    """Parse `git status --porcelain=v1 -z` (renames/copies carry their source next)."""
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    out: list[Change] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2] != " ":
            raise ValueError(f"malformed git status entry: {entry!r}")
        x, y, path = entry[0], entry[1], entry[3:]
        orig = None
        if x in "RC" or y in "RC":
            if i >= len(fields) or not fields[i]:
                raise ValueError(f"rename without source: {entry!r}")
            orig = fields[i]
            i += 1
        out.append(Change(path, orig, x, y))
    return out


def _safe(rel: str) -> str | None:
    """A declared path as git prints it, or None if it is absolute or climbs out."""
    if not rel or PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
        return None
    return _harness._normalize(rel)


def _oracle_patterns(ident: str) -> list[str]:
    esc = re.escape(ident)
    return [rf"tests/acceptance/test_{esc}\.[^/.]+", rf"progress/acceptance_{esc}\.sha256"]


def _patterns(sid: str, owner: str, kind: str, migrates: Iterable[str]) -> re.Pattern[str]:
    esc, own = re.escape(sid), re.escape(owner)
    parts = [
        r"tests/unit/.+",
        rf"docs/reference/{esc}-[^/]+\.md",
        rf"progress/(?:review|plan_review|impl)_{esc}\.md",
        rf"progress/close_{esc}\.msg",
        rf"progress/review_history/{esc}\.md",
        rf"progress/pr_{own}\.md",
        *_oracle_patterns(sid),
    ]
    if kind == "issue":
        parts.append(rf"docs/plan/issues/{own}-[^/]+\.md")
    for mid in migrates:
        if RE_LEAF_ID.match(mid):
            parts += _oracle_patterns(mid)
    return re.compile("|".join(f"(?:{p})" for p in parts), re.DOTALL)


def _chain(sid: str, kind: str, owner: str, leaf: dict[str, Any], root: Path | None) -> set[str]:
    """Exact names of the harness bookkeeping of roadmap -> phase | issue -> leaf."""
    verdicts = ["roadmap_review.md", f"plan_review_{sid}.md", f"review_{sid}.md"]
    out = {"progress/plan_roadmap.sha256", "progress/review_history/roadmap.md"}
    folder: Path | None = None
    seal = ""
    if kind == "issue":
        verdicts.append(f"issue_review_{owner}.md")
        out |= {
            f"progress/plan_issue_{owner}.sha256",
            f"progress/issue_migrations_{owner}.json",
            f"progress/review_history/{owner}.md",
        }
        seal = f"progress/plan_issue_{owner}.sha256"
        header = _plan_seal._issue_files(root).get(owner) if root else None
        folder = header.with_suffix("") if header else None
    else:
        pid = str(leaf.get("phase") or "")
        if re.fullmatch(r"\d{2}", pid):
            verdicts.append(f"phase_review_{pid}.md")
            seal = f"progress/plan_phase_{pid}.sha256"
            out |= {f"progress/plan_phase_{pid}.sha256", f"progress/review_history/phase-{pid}.md"}
            phase_file = _plan_seal._phase_files(root).get(pid) if root else None
            if phase_file is not None and root is not None:
                out.add(phase_file.relative_to(root).as_posix())
                folder = phase_file.with_suffix("")
    out |= _sealed(root, seal, folder)
    out |= {f"progress/{v}" for v in verdicts}
    out |= {f"progress/verdict_{v}.sha256" for v in verdicts}
    if not leaf.get("spec") and folder is not None and root is not None:
        out |= {p.relative_to(root).as_posix() for p in folder.glob(f"{glob_escape(sid)}-*.md")}
    return out


def _sealed(root: Path | None, seal: str, folder: Path | None) -> set[str]:
    """The approved specs of the leaf's phase or issue, as its seal lists them: only
    files directly inside that plan folder (docs/plan/phase-NN-<slug>/<id>-<slug>.md),
    so a tampered seal cannot widen the manifest to any other path."""
    if root is None or folder is None or not seal or not (root / seal).is_file():
        return set()
    try:
        paths = _plan_seal.read(root / seal)
    except (OSError, ValueError):
        return set()
    base = folder.relative_to(root).as_posix()
    spec = re.compile(rf"{re.escape(base)}/(?:ISS-)?\d+(?:\.\d+)+-[a-z0-9][a-z0-9-]*\.md")
    return {rel for rel in paths if _safe(rel) == rel and spec.fullmatch(rel)}


def glob_escape(text: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", text)


class Manifest:
    """The allow-list of one subtask's close."""

    def __init__(self, sid: str, leaf: dict[str, Any], root: Path | None = None) -> None:
        if not RE_LEAF_ID.match(sid):
            raise ValueError(f"not a subtask id: {sid!r}")
        kind, owner = _harness.parse_id(sid)
        declared = [str(f) for f in leaf.get("files") or []]
        spec = str(leaf.get("spec") or "")
        exact = {_safe(rel) for rel in [*declared, spec]}
        self.exact = (exact - {None}) | FIXED | _chain(sid, kind, owner, leaf, root)
        migrates = [str(m) for m in leaf.get("migrates") or []]
        self.pattern = _patterns(sid, owner, kind, migrates)

    def allows(self, path: str) -> bool:
        return path in self.exact or self.pattern.fullmatch(path) is not None


def _status(root: Path) -> bytes:
    res = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return res.stdout


def evaluate(
    sid: str, leaf: dict[str, Any], changes: list[Change], root: Path | None = None
) -> ScopeReport:
    manifest = Manifest(sid, leaf, root)
    allowed: set[str] = set()
    disallowed: set[str] = set()
    staged: set[str] = set()
    for change in changes:
        for path in change.paths():
            if manifest.allows(path):
                allowed.add(path)
                continue
            disallowed.add(path)
            if change.staged:
                staged.add(path)
    return ScopeReport(
        sid, tuple(sorted(allowed)), tuple(sorted(disallowed)), tuple(sorted(staged))
    )


def check(root: Path, sid: str, leaf: dict[str, Any]) -> ScopeReport:
    """The close scope of subtask <sid> against the work tree and index of <root>."""
    Manifest(sid, leaf)  # validate the id before touching git
    prefix = _prefix(root)
    changes = [_relative(c, prefix) for c in parse_porcelain(_status(root))]
    return evaluate(sid, leaf, changes, root)


def _prefix(root: Path) -> str:
    """The project's path inside its repository ('' at the top, else 'sub/dir/')."""
    res = subprocess.run(
        ["git", "rev-parse", "--show-prefix"], cwd=root, capture_output=True, check=True
    )
    return res.stdout.decode("utf-8", "surrogateescape").strip()


def _rebase(path: str, prefix: str) -> str:
    """A repository path relative to the project; outside it, ':/<path>' (never allowed)."""
    if not prefix:
        return path
    return path[len(prefix) :] if path.startswith(prefix) else f":/{path}"


def _relative(change: Change, prefix: str) -> Change:
    orig = _rebase(change.orig, prefix) if change.orig is not None else None
    return Change(_rebase(change.path, prefix), orig, change.index, change.worktree)


def stageable(root: Path, paths: Iterable[str]) -> list[str]:
    """The paths `git add` can name: on disk or still in the index (the source of a
    rename already staged is in neither, and naming it would fail the pathspec)."""
    wanted = list(paths)
    res = subprocess.run(
        ["git", "ls-files", "-z", "--", *wanted],
        cwd=root,
        capture_output=True,
        check=True,
        env={**os.environ, "GIT_LITERAL_PATHSPECS": "1"},
    )
    indexed = set(res.stdout.decode("utf-8", "surrogateescape").split("\0"))
    return [p for p in wanted if p in indexed or os.path.lexists(root / p)]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("check", "files"):
        print("usage: _close_scope.py check|files <N.M.K|ISS-n.k>", file=sys.stderr)
        return 2
    sid = argv[1]
    leaf = _harness._leaf(sid) if RE_LEAF_ID.match(sid) else None
    if leaf is None:
        print(f"[FAIL] subtask {sid} does not exist in docs/plan/", file=sys.stderr)
        return 2
    try:
        report = check(Path.cwd(), sid, leaf)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"[FAIL] cannot compute the close scope of {sid}: {exc}", file=sys.stderr)
        return 2
    for line in report.explain():
        print(f"[FAIL] {line}", file=sys.stderr)
    if not report.ok:
        return 1
    if argv[0] == "files":
        try:
            paths = stageable(Path.cwd(), report.allowed)
        except subprocess.CalledProcessError as exc:
            print(f"[FAIL] cannot list the close manifest of {sid}: {exc}", file=sys.stderr)
            return 2
        out = "".join(f"{path}\0" for path in paths)
        sys.stdout.buffer.write(out.encode("utf-8", "surrogateescape"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
