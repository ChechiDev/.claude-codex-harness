"""Seals of the approved plan, so an approved plan cannot change silently.

Once Codex approves the roadmap or a phase breakdown, the harness records the
sha256 of what was approved; `make check` (via `_guard.py integrity`) fails if it
changes afterwards. Any change needs a new Codex approval (which re-seals).

  progress/plan_roadmap.sha256   PLAN.md (minus the generated index) + the header
                                 (title + goal) of every phase file
  progress/plan_phase_NN.sha256  the phase file (tasks); for a phase broken down whole
                                 (legacy, issue #62) also every subtask spec and its
                                 legacy record progress/task_flow_legacy_NN.json
  progress/plan_task_N.M.sha256  task flow: the subtask specs of task N.M (task gate)
  progress/plan_issue_ISS-n.sha256  the issue header + every issue subtask spec

`- status:` lines are excluded: status moves through the cycle on its own.
`- status: baseline` is the exception: it is part of what Codex approved, so a
subtask cannot become (or stop being) baseline after the approval. A
subtask's own plan gate (PLAN_APPROVED) re-seals that one spec; while that gate
is still open, a change to the in_progress spec is only a warning.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import _harness

PLAN_DIR = Path("docs/plan")
INDEX_RE = re.compile(r"<!-- plan:index:start -->.*?<!-- plan:index:end -->", re.DOTALL)
RE_PHASE_FILE = re.compile(r"^phase-(\d{2})-[a-z0-9][a-z0-9-]*\.md$")
RE_ISSUE_FILE = re.compile(r"^(ISS-\d+)-[a-z0-9][a-z0-9-]*\.md$")
RE_STATUS = re.compile(r"^- status:(?![ \t]*baseline[ \t]*$).*$\n?", re.MULTILINE | re.IGNORECASE)

Entries = dict[str, str]


def roadmap_manifest(root: Path) -> Path:
    return root / "progress" / "plan_roadmap.sha256"


def phase_manifest(root: Path, pid: str) -> Path:
    return root / "progress" / f"plan_phase_{pid}.sha256"


def task_manifest(root: Path, tid: str) -> Path:
    return root / "progress" / f"plan_task_{tid}.sha256"


def legacy_record(root: Path, pid: str) -> Path:
    return root / "progress" / f"task_flow_legacy_{pid}.json"


def issue_manifest(root: Path, iid: str) -> Path:
    return root / "progress" / f"plan_issue_{iid}.sha256"


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


def _task_manifests(root: Path, pid: str) -> list[Path]:
    return sorted((root / "progress").glob(f"plan_task_{int(pid)}.*.sha256"))


def whole(root: Path, pid: str) -> bool:
    """The phase seal covers its specs: it was sealed with them (legacy), or it is
    sealed now for the first time with specs and no task seal (issue #62)."""
    manifest = phase_manifest(root, pid)
    if manifest.is_file():
        return _harness.sealed_specs(manifest)
    return not _task_manifests(root, pid)


def phase_entries(root: Path, pid: str) -> Entries:
    path = _phase_files(root).get(pid)
    if path is None:
        return {}
    files = [path]
    if whole(root, pid):
        files += sorted(path.with_suffix("").glob("*.md"), key=_spec_key)
    entries = {_rel(root, f): _digest(RE_STATUS.sub("", f.read_text("utf-8"))) for f in files}
    record = legacy_record(root, pid)
    if record.is_file() and len(files) > 1:
        entries[_rel(root, record)] = _digest(record.read_text("utf-8"))
    return entries


def _task_of_spec(path: Path) -> str:
    return path.name.split("-", 1)[0].rsplit(".", 1)[0]


def task_entries(root: Path, tid: str) -> Entries:
    path = _phase_files(root).get(f"{int(tid.split('.')[0]):02d}")
    if path is None:
        return {}
    specs = [f for f in path.with_suffix("").glob("*.md") if _task_of_spec(f) == tid]
    return {
        _rel(root, f): _digest(RE_STATUS.sub("", f.read_text("utf-8")))
        for f in sorted(specs, key=_spec_key)
    }


def record_legacy(root: Path, data: dict[str, object]) -> list[str]:
    """Write, once, the legacy record of every phase sealed with its specs: the tasks
    already done now (issue #62). It goes into the phase seal, so editing or removing
    it fails `make check`; a seal that already lists it is never rewritten."""
    written = []
    tasks = data.get("features")
    for pid in _phase_files(root):
        manifest = phase_manifest(root, pid)
        record = legacy_record(root, pid)
        rel = _rel(root, record)
        if not manifest.is_file() or not _harness.sealed_specs(manifest):
            continue
        sealed = read(manifest)
        if rel in sealed or record.exists():
            continue
        done = [
            str(t["id"])
            for t in (tasks if isinstance(tasks, list) else [])
            if t.get("phase") == pid
            and t.get("subtasks")
            and all(s.get("status") in ("done", "baseline") for s in t["subtasks"])
        ]
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False
        )
        body = {
            "phase": pid,
            "tasks_done": done,
            "commit": head.stdout.strip() if head.returncode == 0 else "",
            "recorded_by": "make plan (issue #62: tasks exempt from the task-close gate)",
        }
        record.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        sealed[rel] = _digest(record.read_text("utf-8"))
        write(manifest, sealed)
        written.append(rel)
    return written


def _spec_key(path: Path) -> tuple[int, ...]:
    head = path.name.removeprefix("ISS-").split("-", 1)[0]
    return tuple(int(p) for p in head.split(".") if p.isdigit())


def _issue_files(root: Path) -> dict[str, Path]:
    found = {}
    for path in sorted((root / PLAN_DIR / "issues").glob("ISS-*.md")):
        m = RE_ISSUE_FILE.match(path.name)
        if m:
            found[m.group(1)] = path
    return found


def issue_entries(root: Path, iid: str) -> Entries:
    path = _issue_files(root).get(iid)
    if path is None:
        return {}
    files = [path, *sorted(path.with_suffix("").glob("*.md"), key=_spec_key)]
    entries = {_rel(root, f): _digest(RE_STATUS.sub("", f.read_text("utf-8"))) for f in files}
    snapshot = root / "progress" / f"issue_migrations_{iid}.json"  # see _migrate.py
    if snapshot.is_file():
        entries[_rel(root, snapshot)] = _digest(snapshot.read_text("utf-8"))
    return entries


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


def reseal_spec(root: Path, spec: str, owner: str) -> bool:
    """Refresh one spec's digest in its phase (NN) or issue (ISS-n) seal, after its
    plan gate approved it."""
    is_issue = owner.startswith("ISS-")
    manifest = issue_manifest(root, owner) if is_issue else phase_manifest(root, owner)
    if not is_issue:
        tid = _task_of_spec(Path(spec))
        if task_manifest(root, tid).is_file():
            manifest = task_manifest(root, tid)
    if not manifest.is_file():
        return False
    entries = read(manifest)
    if is_issue:
        current = issue_entries(root, owner)
    elif manifest.name.startswith("plan_task_"):
        current = task_entries(root, _task_of_spec(Path(spec)))
    else:
        current = phase_entries(root, owner)
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


def approved_without_plan_file(root: Path) -> list[str]:
    """Approved phases and issues of feature_list.json whose plan file is gone: `verify`
    walks the plan files, so it never sees them."""
    found = []
    files = _phase_files(root)
    for phase in _harness.phases():
        pid = str(phase["id"])
        verdict = _harness.read_verdict(root / _harness.phase_review(pid))
        if pid not in files and verdict == "PHASE_APPROVED":
            found.append(f"phase {pid}")
    issue_files = _issue_files(root)
    for issue in _harness.issues():
        iid = str(issue["id"])
        verdict = _harness.read_verdict(root / _harness.issue_review(iid))
        if iid not in issue_files and verdict == "ISSUE_APPROVED":
            found.append(f"issue {iid}")
    return found


def _roadmap_problems(root: Path, gate: str) -> list[str]:
    if _harness.assumed("roadmap"):
        return []
    diffs = _diff(read(roadmap_manifest(root)), roadmap_entries(root), "the roadmap")
    return [msg + gate.format("roadmap") for _, msg in diffs]


def verify(root: Path, exempt: Callable[[str], bool] | None = None) -> tuple[list[str], list[str]]:
    """(problems, warnings) of the plan seals against docs/plan/ as it is now. `exempt`
    tells whether a missing seal of that item (roadmap, NN, ISS-n) is legacy (plan 0.6)."""
    is_exempt = exempt or (lambda _: False)
    problems: list[str] = []
    warnings: list[str] = []
    gate = " (re-run its Codex gate: ./harness/review_with_codex.sh {})"
    # The counterfactual run of a re-gate (issue #27) reads the reviewed target's seal
    # as matching the current specs; every other seal is checked as usual.
    if roadmap_manifest(root).is_file():
        problems.extend(_roadmap_problems(root, gate))
    elif _harness.read_verdict(
        root / _harness.ROADMAP_REVIEW
    ) == "ROADMAP_APPROVED" and not is_exempt("roadmap"):
        warnings.append(
            "the roadmap is approved but not sealed (human: review_with_codex.sh seal roadmap)"
        )
    open_spec = _open_plan_gate_spec(root)
    for pid in _phase_files(root):
        manifest = phase_manifest(root, pid)
        if not manifest.is_file():
            approved = _harness.read_verdict(root / _harness.phase_review(pid))
            if approved == "PHASE_APPROVED" and not is_exempt(pid):
                warnings.append(
                    f"phase {pid} is approved but not sealed "
                    f"(human: review_with_codex.sh seal phase {pid})"
                )
            continue
        if _harness.assumed("phase", pid):
            continue
        for rel, msg in _diff(read(manifest), phase_entries(root, pid), f"phase {pid}"):
            if rel == open_spec:
                warnings.append(f"{msg}: its subtask is still in its plan gate")
            else:
                problems.append(msg + gate.format(f"phase {pid}"))
    _verify_tasks(root, open_spec, problems, warnings)
    _verify_issues(root, open_spec, problems, warnings, is_exempt)
    return problems, warnings


def _verify_tasks(
    root: Path, open_spec: str | None, problems: list[str], warnings: list[str]
) -> None:
    gate = " (re-run its Codex gate: ./harness/review_with_codex.sh task {})"
    for review in sorted((root / "progress").glob("task_review_*.md")):
        tid = review.name.removeprefix("task_review_").removesuffix(".md")
        approved = _harness.read_verdict(review) == "TASK_APPROVED"
        if approved and not task_manifest(root, tid).is_file():
            problems.append(
                f"task {tid} is TASK_APPROVED but not sealed: its specs are unprotected "
                f"(human: review_with_codex.sh seal task {tid}, or re-run its gate)"
            )
    for manifest in sorted((root / "progress").glob("plan_task_*.sha256")):
        tid = manifest.name.removeprefix("plan_task_").removesuffix(".sha256")
        if _harness.assumed("task", tid):
            continue
        for rel, msg in _diff(read(manifest), task_entries(root, tid), f"task {tid}"):
            if rel == open_spec:
                warnings.append(f"{msg}: its subtask is still in its plan gate")
            else:
                problems.append(msg + gate.format(tid))


def _verify_issues(
    root: Path,
    open_spec: str | None,
    problems: list[str],
    warnings: list[str],
    is_exempt: Callable[[str], bool],
) -> None:
    gate = " (re-run its Codex gate: ./harness/review_with_codex.sh issue {})"
    for iid in _issue_files(root):
        manifest = issue_manifest(root, iid)
        if not manifest.is_file():
            approved = _harness.read_verdict(root / _harness.issue_review(iid))
            if approved == "ISSUE_APPROVED" and not is_exempt(iid):
                warnings.append(
                    f"issue {iid} is approved but not sealed "
                    f"(human: review_with_codex.sh seal issue {iid})"
                )
            continue
        if _harness.assumed("issue", iid):
            continue
        for rel, msg in _diff(read(manifest), issue_entries(root, iid), f"issue {iid}"):
            if rel == open_spec:
                warnings.append(f"{msg}: its subtask is still in its plan gate")
            else:
                problems.append(msg + gate.format(iid))
