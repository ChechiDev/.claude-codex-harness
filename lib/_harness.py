#!/usr/bin/env python3
"""Shared harness utilities. Single source of state parsing.

Reads the derived feature_list.json (generated from docs/plan/ by sync_plan.py):
phases → tasks (N.M) → subtasks (N.M.K). The unit of work is the LEAF: a
subtask. Gates, docs, and commits operate at leaf granularity.

Plan rules (validate): the roadmap (every phase, title + goal) is approved by
Codex before any phase is broken down; phases are broken down one at a time
(phase N+1 only once phase N is done); a subtask starts only once its phase
breakdown is approved and its dependencies are done.

Usage: python3 _harness.py <command> [args]
Commands:
  in-progress-id         print the id of the single in_progress leaf (empty if none)
  feature-name <id>      print the title of the phase/task/subtask <id>
  done-ids               print the leaf ids in done status (one per line)
  declared-files <id>    print the files declared for leaf <id> (one per line)
  spec <id>              print the spec path of subtask <id>
  phase-of <id>          print the phase id of subtask <id>
  roadmap-ready          exit 0 if there are phases for the roadmap gate to review
  phase-ready <NN>       exit 0 if phase NN can go through the phase gate
  subtask-ready <id>     exit 0 if subtask <id> can go through the plan gate
  verdict <file>         print the verdict of a Codex review file (empty if none)
  branch <N.M>           print the task branch (harness.toml [git] branch)
  commit-prefix <N.M.K>  print the close-commit subject prefix (may be empty)
  validate               validate invariants; exit != 0 with [FAIL] messages on failure
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import _config
from _status import say

FEATURES_FILE = Path("feature_list.json")
ROADMAP_REVIEW = Path("progress/roadmap_review.md")
VALID = {"pending", "in_progress", "done", "blocked"}
STARTED = {"in_progress", "done"}
# Only the explicit verdict line counts: a substring match would let a
# CHANGES_REQUESTED review that merely mentions "PLAN_APPROVED" open the gate.
RE_VERDICT = re.compile(
    r"^\*\*Verdict:\*\*[ \t]*"
    r"(ROADMAP_APPROVED|PHASE_APPROVED|PLAN_APPROVED|APPROVED|CHANGES_REQUESTED)[ \t]*$",
    re.MULTILINE,
)

Item = dict[str, Any]


def phase_review(pid: str) -> Path:
    return Path(f"progress/phase_review_{pid}.md")


def plan_data() -> Item:
    data: Item = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
    return data


def _tasks() -> list[Item]:
    features: list[Item] = plan_data()["features"]
    return features


def phases() -> list[Item]:
    found: list[Item] = plan_data().get("phases", [])
    return found


def leaves(data: Item | None = None) -> list[Item]:
    """Return the atomic work units: every subtask of every task (of <data> if given)."""
    tasks: list[Item] = _tasks() if data is None else data.get("features", [])
    return [
        {
            "id": s["id"],
            "status": s.get("status", "pending"),
            "name": s.get("title", ""),
            "files": s.get("files", []),
            "depends": s.get("depends", []),
            "phase": t.get("phase", ""),
            "spec": s.get("spec", ""),
        }
        for t in tasks
        for s in t.get("subtasks") or []
    ]


def _all_items() -> list[Item]:
    """Every phase, task and subtask (for name lookup by any id)."""
    out: list[Item] = [{"id": p["id"], "name": p.get("title", "")} for p in phases()]
    for t in _tasks():
        out.append({"id": t["id"], "name": t.get("title", "")})
        for s in t.get("subtasks") or []:
            out.append({"id": s["id"], "name": s.get("title", "")})
    return out


def _leaf(fid: str) -> Item | None:
    return next((leaf for leaf in leaves() if str(leaf["id"]) == str(fid)), None)


def current_leaf_id() -> str | None:
    ids = [str(leaf["id"]) for leaf in leaves() if leaf["status"] == "in_progress"]
    return ids[0] if ids else None


def leaf_files(fid: str) -> list[str] | None:
    """Declared files of leaf <fid>, normalized (no ./ or a/./b); None if unknown id."""
    leaf = _leaf(fid)
    return None if leaf is None else [_normalize(f) for f in leaf.get("files", [])]


def _normalize(rel: str) -> str:
    parts = [p for p in Path(rel).parts if p != "."]
    return "/".join(parts)


def _task(tid: str) -> Item | None:
    return next((t for t in _tasks() if str(t["id"]) == str(tid)), None)


def _render(pattern: str, tid: str, cfg: _config.Config) -> str:
    """Fill a [git] pattern for task <tid>. Raises ValueError with the reason."""
    task = _task(tid)
    if task is None:
        raise ValueError(f"task {tid} does not exist in docs/plan/")
    ticket = str(task.get("ticket", ""))
    if cfg.needs_ticket and not ticket:
        raise ValueError(
            f"task {tid} has no '- ticket:' line in its phase file "
            "(harness.toml [git] uses {ticket})"
        )
    if ticket and not _config.RE_TICKET.match(ticket):
        raise ValueError(f"task {tid}: ticket '{ticket}' may only use letters, digits, - and _")
    return _config.render(pattern, tid, str(task.get("title", "")), ticket)


def task_branch(tid: str, cfg: _config.Config) -> str:
    """The branch task <tid> lives on. Raises ValueError with the reason."""
    name = _render(cfg.branch, tid, cfg)
    if not _config.valid_branch(name):
        raise ValueError(f"task {tid}: '{name}' is not a valid branch name")
    return name


def read_verdict(path: Path) -> str:
    """The verdict token of a Codex review file, or "" if missing/unparseable."""
    try:
        match = RE_VERDICT.search(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return match.group(1) if match else ""


# ── plan rules ───────────────────────────────────────────────────────────────


def _phase_leaves(pid: str) -> list[Item]:
    return [leaf for leaf in leaves() if leaf["phase"] == pid]


def _broken_down(phase: Item) -> bool:
    return bool(phase.get("tasks"))


def _done(pid: str) -> bool:
    subs = _phase_leaves(pid)
    return bool(subs) and all(s["status"] == "done" for s in subs)


def _roadmap_problems() -> list[str]:
    if read_verdict(ROADMAP_REVIEW) == "ROADMAP_APPROVED":
        return []
    return [
        f"phase {p['id']} is broken down but the roadmap is not ROADMAP_APPROVED ({ROADMAP_REVIEW})"
        for p in phases()
        if _broken_down(p)
    ]


def _progression_problems() -> list[str]:
    problems = []
    ordered = phases()
    for i, phase in enumerate(ordered):
        if not _broken_down(phase):
            continue
        for earlier in ordered[:i]:
            if not _done(earlier["id"]):
                problems.append(
                    f"phase {phase['id']} is broken down but phase {earlier['id']} is not "
                    "done (one phase at a time)"
                )
                break
    return problems


def _start_problems(leaf: Item, status: dict[str, str]) -> list[str]:
    problems = []
    pid = leaf["phase"]
    if read_verdict(phase_review(pid)) != "PHASE_APPROVED":
        problems.append(
            f"{leaf['id']} is {leaf['status']} but phase {pid} is not PHASE_APPROVED "
            f"({phase_review(pid)})"
        )
    for dep in leaf.get("depends", []):
        if status.get(dep) != "done":
            problems.append(
                f"{leaf['id']} is {leaf['status']} but depends on {dep} ({status.get(dep)})"
            )
    return problems


def config_problems() -> list[str]:
    try:
        cfg = _config.load(Path.cwd())
    except _config.ConfigError as exc:
        return [str(exc)]
    if not cfg.git_configured:
        return []
    problems = []
    for task in _tasks():
        try:
            task_branch(str(task["id"]), cfg)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def plan_problems() -> list[str]:
    units = leaves()
    status = {str(leaf["id"]): str(leaf["status"]) for leaf in units}
    problems = _roadmap_problems() + _progression_problems()
    for leaf in units:
        if leaf["status"] in STARTED:
            problems += _start_problems(leaf, status)
    return problems


def roadmap_ready_problems() -> list[str]:
    if phases():
        return []
    return ["no phases in docs/plan/ yet: write PLAN.md and one phase-NN-<slug>.md per phase"]


def phase_ready_problems(pid: str) -> list[str]:
    phase = next((p for p in phases() if p["id"] == pid), None)
    if phase is None:
        return [f"phase {pid} does not exist in docs/plan/"]
    problems = []
    if read_verdict(ROADMAP_REVIEW) != "ROADMAP_APPROVED":
        problems.append(f"the roadmap is not ROADMAP_APPROVED ({ROADMAP_REVIEW})")
    if not _phase_leaves(pid):
        problems.append(f"phase {pid} has no subtasks yet: break it down first")
    return problems + [p for p in _progression_problems() if p.startswith(f"phase {pid} ")]


def subtask_ready_problems(fid: str) -> list[str]:
    leaf = _leaf(fid)
    if leaf is None:
        return [f"subtask {fid} does not exist in docs/plan/"]
    status = {str(item["id"]): str(item["status"]) for item in leaves()}
    return _start_problems({**leaf, "status": "starting"}, status)


# ── commands ─────────────────────────────────────────────────────────────────


def _print(value: object) -> int:
    if value in (None, ""):
        return 1
    print(value)
    return 0


def _report(problems: list[str]) -> int:
    for problem in problems:
        say("FAIL", problem)
    return 1 if problems else 0


def feature_name(fid: str) -> int:
    return _print(next((i["name"] for i in _all_items() if str(i["id"]) == str(fid)), None))


def done_ids() -> int:
    for leaf in leaves():
        if leaf["status"] == "done":
            print(leaf["id"])
    return 0


def declared_files(fid: str) -> int:
    files = leaf_files(fid)
    if files is None:
        return 1
    for f in files:
        print(f)
    return 0


def in_progress_id() -> int:
    fid = current_leaf_id()
    if fid:
        print(fid)
    return 0


def verdict(path: str) -> int:
    print(read_verdict(Path(path)))
    return 0


def _leaf_field(fid: str, key: str) -> int:
    leaf = _leaf(fid)
    return _print(leaf.get(key) if leaf else None)


def _git_command(render: Callable[[_config.Config], str]) -> int:
    try:
        print(render(_config.load(Path.cwd())))
    except (_config.ConfigError, ValueError) as exc:
        say("FAIL", str(exc), sys.stderr)
        return 1
    return 0


def branch(tid: str) -> int:
    return _git_command(lambda cfg: task_branch(tid, cfg))


def commit_prefix(sid: str) -> int:
    tid = sid.rsplit(".", 1)[0]
    return _git_command(lambda cfg: _render(cfg.commit_prefix, tid, cfg))


def validate() -> int:
    units = leaves()
    problems = []
    in_progress = [leaf["id"] for leaf in units if leaf["status"] == "in_progress"]
    if len(in_progress) > 1:
        problems.append(
            f"{len(in_progress)} leaves in in_progress (max 1): {', '.join(in_progress)}"
        )
    problems += [
        f"Invalid status in {leaf['id']}: {leaf['status']}"
        for leaf in units
        if leaf["status"] not in VALID
    ]
    problems += plan_problems() + config_problems()
    if not problems:
        say("OK", f"feature_list.json valid ({len(units)} work units)")
    return _report(problems)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "verdict":
        return verdict(argv[1]) if len(argv) > 1 else 2
    if not FEATURES_FILE.exists():
        say("FAIL", "No feature_list.json in the cwd (run: make plan)", sys.stderr)
        return 1
    cmd, *rest = argv
    arg = rest[0] if rest else ""
    dispatch: dict[str, Callable[[], int]] = {
        "in-progress-id": in_progress_id,
        "feature-name": lambda: feature_name(arg),
        "done-ids": done_ids,
        "declared-files": lambda: declared_files(arg),
        "spec": lambda: _leaf_field(arg, "spec"),
        "phase-of": lambda: _leaf_field(arg, "phase"),
        "roadmap-ready": lambda: _report(roadmap_ready_problems()),
        "phase-ready": lambda: _report(phase_ready_problems(arg)),
        "subtask-ready": lambda: _report(subtask_ready_problems(arg)),
        "validate": validate,
        "branch": lambda: branch(arg),
        "commit-prefix": lambda: commit_prefix(arg),
    }
    fn = dispatch.get(cmd)
    if fn is None:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 2
    if cmd not in ("in-progress-id", "done-ids", "validate", "roadmap-ready") and not arg:
        return 2
    return fn()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
