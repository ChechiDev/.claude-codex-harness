"""Seals of the approved plan, so an approved plan cannot change silently.

Once Codex approves the roadmap or a phase breakdown, the harness records the
sha256 of what was approved; `make check` (via `_guard.py integrity`) fails if it
changes afterwards. Any change needs a new Codex approval (which re-seals).

  progress/plan_roadmap.sha256   PLAN.md (minus the generated index) + the header
                                 (title + goal) of every phase file
  progress/plan_phase_NN.sha256  the phase file (tasks) + every subtask spec

`- status:` lines are excluded: status moves through the cycle on its own. A
subtask's own plan gate (PLAN_APPROVED) re-seals that one spec; while that gate
is still open, a change to the in_progress spec is only a warning.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import _harness

PLAN_DIR = Path("docs/plan")
INDEX_RE = re.compile(r"<!-- plan:index:start -->.*?<!-- plan:index:end -->", re.DOTALL)
RE_PHASE_FILE = re.compile(r"^phase-(\d{2})-[a-z0-9][a-z0-9-]*\.md$")
RE_STATUS = re.compile(r"^- status:.*$\n?", re.MULTILINE)

Entries = dict[str, str]


def roadmap_manifest(root: Path) -> Path:
    return root / "progress" / "plan_roadmap.sha256"


def phase_manifest(root: Path, pid: str) -> Path:
    return root / "progress" / f"plan_phase_{pid}.sha256"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _phase_files(root: Path) -> dict[str, Path]:
    found = {}
    for path in sorted((root / PLAN_DIR).glob("phase-*.md")):
        m = RE_PHASE_FILE.match(path.name)
        if m:
            found[m.group(1)] = path
    return found


def _header(text: str) -> str:
    """The phase title + goal: everything before the first task heading (trimmed,
    so adding the first task does not change it)."""
    return re.split(r"^## ", text, maxsplit=1, flags=re.MULTILINE)[0].rstrip() + "\n"


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def roadmap_entries(root: Path) -> Entries:
    entries: Entries = {}
    plan_md = root / PLAN_DIR / "PLAN.md"
    if plan_md.is_file():
        entries[_rel(root, plan_md)] = _digest(INDEX_RE.sub("", plan_md.read_text("utf-8")))
    for path in _phase_files(root).values():
        entries[_rel(root, path)] = _digest(_header(path.read_text("utf-8")))
    return entries


def phase_entries(root: Path, pid: str) -> Entries:
    path = _phase_files(root).get(pid)
    if path is None:
        return {}
    files = [path, *sorted(path.with_suffix("").glob("*.md"), key=_spec_key)]
    return {_rel(root, f): _digest(RE_STATUS.sub("", f.read_text("utf-8"))) for f in files}


def _spec_key(path: Path) -> tuple[int, ...]:
    head = path.name.split("-", 1)[0]
    return tuple(int(p) for p in head.split(".") if p.isdigit())


def read(manifest: Path) -> Entries:
    entries: Entries = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, rel = line.split(maxsplit=1)
            entries[rel] = digest
    return entries


def write(manifest: Path, entries: Entries) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(f"{d}  {rel}\n" for rel, d in entries.items()), "utf-8")


def reseal_spec(root: Path, spec: str, pid: str) -> bool:
    """Refresh one spec's digest in its phase seal (after its plan gate approved it)."""
    manifest = phase_manifest(root, pid)
    if not manifest.is_file():
        return False
    entries = read(manifest)
    current = phase_entries(root, pid)
    if spec in current:
        entries[spec] = current[spec]
        write(manifest, entries)
    return True


def _diff(sealed: Entries, current: Entries, what: str) -> list[tuple[str, str]]:
    """(path, message) for every path changed, added or removed since the seal."""
    out = []
    for rel in sorted(sealed.keys() | current.keys()):
        if rel not in current:
            out.append((rel, f"{rel} removed after {what} was approved"))
        elif rel not in sealed:
            out.append((rel, f"{rel} added after {what} was approved"))
        elif sealed[rel] != current[rel]:
            out.append((rel, f"{rel} changed after {what} was approved"))
    return out


def _open_plan_gate_spec(root: Path) -> str | None:
    """Spec of the in_progress subtask while its plan gate is not yet approved."""
    leaf = _harness.current_leaf_id()
    if leaf is None:
        return None
    if _harness.read_verdict(root / f"progress/plan_review_{leaf}.md") == "PLAN_APPROVED":
        return None
    return next((str(s["spec"]) for s in _harness.leaves() if s["id"] == leaf), None)


def verify(root: Path) -> tuple[list[str], list[str]]:
    """(problems, warnings) of the plan seals against docs/plan/ as it is now."""
    problems: list[str] = []
    warnings: list[str] = []
    gate = " (re-run its Codex gate: ./harness/review_with_codex.sh {})"
    if roadmap_manifest(root).is_file():
        for _, msg in _diff(read(roadmap_manifest(root)), roadmap_entries(root), "the roadmap"):
            problems.append(msg + gate.format("roadmap"))
    elif _harness.read_verdict(root / _harness.ROADMAP_REVIEW) == "ROADMAP_APPROVED":
        warnings.append(
            "the roadmap is approved but not sealed (human: review_with_codex.sh seal roadmap)"
        )
    open_spec = _open_plan_gate_spec(root)
    for pid in _phase_files(root):
        manifest = phase_manifest(root, pid)
        if not manifest.is_file():
            if _harness.read_verdict(root / _harness.phase_review(pid)) == "PHASE_APPROVED":
                warnings.append(
                    f"phase {pid} is approved but not sealed "
                    f"(human: review_with_codex.sh seal phase {pid})"
                )
            continue
        for rel, msg in _diff(read(manifest), phase_entries(root, pid), f"phase {pid}"):
            if rel == open_spec:
                warnings.append(f"{msg}: its subtask is still in its plan gate")
            else:
                problems.append(msg + gate.format(f"phase {pid}"))
    return problems, warnings
