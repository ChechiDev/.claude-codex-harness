#!/usr/bin/env python3
"""Shared harness utilities. Single source of state parsing.

Reads the derived feature_list.json (generated from docs/plan/ by sync_plan.py):
phases → tasks (N.M) → subtasks (N.M.K). The unit of work is the LEAF: a
subtask. Gates, docs, and commits operate at leaf granularity.

Plan rules (validate): the roadmap (every phase, title + goal) is approved by
Codex before any phase is broken down; phases are broken down one at a time
(phase N+1 only once phase N is done); a subtask starts only once its phase
breakdown is approved and its dependencies are done.

Task flow (issue #62): a phase sealed without subtask specs is broken down task by
task. A subtask of it starts only once its task is TASK_APPROVED (task gate); a task
starts only once the previous task is TASK_CLOSE_APPROVED (task-close gate), and the
next phase is broken down only once this one is PHASE_CLOSE_APPROVED. A phase sealed
with its specs (broken down whole) is legacy: no task gate, and only the tasks not in
its legacy record (progress/task_flow_legacy_NN.json, written once by `make plan`)
need a task-close. An approved phase with no seal at all keeps the pre-#62 rules.

`baseline` is work that existed before the harness: it counts as done for
progression and dependencies once its phase is PHASE_APPROVED, and needs no
code review nor reference doc (it is not in done-ids). Its status line is part
of the phase seal (_plan_seal.py), so nothing turns baseline after approval.

Issues lane: GitHub issues are planned under docs/plan/issues/ (feature_list.json
`issues`), with subtask ids ISS-<n>.<k>. They join the leaf list (one in_progress
across both lanes) but never count in phase progression: an issue subtask starts
once its issue is ISSUE_APPROVED and its dependencies are done. An issue lives on
the branch <type>/ISS-<n>-<slug>, whatever the [git] task template says.

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
  issue-ready <ISS-n>    exit 0 if issue ISS-n can go through the issue gate
  task-ready <N.M>       exit 0 if task N.M can go through the task gate (issue #62)
  task-close-ready <N.M> exit 0 if task N.M can go through the task-close gate
  phase-close-ready <NN> exit 0 if phase NN can go through the phase-close gate
  close-commits <N.M|NN> print the close commits of a task or phase, oldest first
  close-sources <N.M|NN> print the specs and reports a close gate reads
  close-missing <N.M|NN> print the subtasks with no close commit (gaps of a close)
  close-ready <N.M.K>    exit 0 if subtask N.M.K can be closed (not done, code APPROVED)
  issue-of <id>          print the issue id of issue subtask <id> (exit 1 for a task leaf)
  issue-field <ISS-n> <key>  print a field of issue ISS-n (type, priority, interrupts…)
  verdict <file>         print the verdict of a Codex review file (empty if none)
  branch <N.M|ISS-n>     print the task branch (harness.toml [git] branch) or issue branch
  branch-of <id>         print the branch of the task or issue that leaf <id> belongs to
  commit-prefix <N.M.K>  print the close-commit subject prefix (may be empty)
  validate               validate invariants; exit != 0 with [FAIL] messages on failure
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import _config
from _status import say

FEATURES_FILE = Path("feature_list.json")
ROADMAP_REVIEW = Path("progress/roadmap_review.md")
VALID = {"pending", "in_progress", "done", "blocked", "baseline"}
STARTED = {"in_progress", "done"}
# Only the explicit verdict line counts: a substring match would let a
# CHANGES_REQUESTED review that merely mentions "PLAN_APPROVED" open the gate.
RE_VERDICT = re.compile(
    r"^\*\*Verdict:\*\*[ \t]*"
    r"(ROADMAP_APPROVED|PHASE_APPROVED|ISSUE_APPROVED|DRAFT_APPROVED|PLAN_APPROVED"
    r"|TASK_APPROVED|TASK_CLOSE_APPROVED|PHASE_CLOSE_APPROVED|STACK_APPROVED|RELEASES_APPROVED|APPROVED"
    r"|CHANGES_REQUESTED)[ \t]*$",
    re.MULTILINE,
)

RE_ISSUE_ID = re.compile(r"^ISS-\d+$")

Item = dict[str, Any]


def phase_review(pid: str) -> Path:
    return Path(f"progress/phase_review_{pid}.md")


def issue_review(iid: str) -> Path:
    return Path(f"progress/issue_review_{iid}.md")


def task_review(tid: str) -> Path:
    return Path(f"progress/task_review_{tid}.md")


def task_close(tid: str) -> Path:
    return Path(f"progress/task_close_{tid}.md")


def phase_close(pid: str) -> Path:
    return Path(f"progress/phase_close_{pid}.md")


def legacy_record(pid: str) -> Path:
    return Path(f"progress/task_flow_legacy_{pid}.json")


def phase_seal(pid: str) -> Path:
    return Path(f"progress/plan_phase_{pid}.sha256")


def parse_id(item_id: str) -> tuple[str, str]:
    """The owner of a leaf: ("task", "2.3") for 2.3.1, ("issue", "ISS-14") for ISS-14.1."""
    owner = item_id.rsplit(".", 1)[0]
    return ("issue" if RE_ISSUE_ID.match(owner) else "task"), owner


def plan_data() -> Item:
    data: Item = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
    return data


def _tasks() -> list[Item]:
    features: list[Item] = plan_data()["features"]
    return features


def phases() -> list[Item]:
    found: list[Item] = plan_data().get("phases", [])
    return found


def issues(data: Item | None = None) -> list[Item]:
    found: list[Item] = (plan_data() if data is None else data).get("issues", [])
    return found


def _leaf_item(sub: Item, phase: str, issue: str) -> Item:
    return {
        "id": sub["id"],
        "status": sub.get("status", "pending"),
        "name": sub.get("title", ""),
        "files": sub.get("files", []),
        "depends": sub.get("depends", []),
        "phase": phase,
        "issue": issue,
        "spec": sub.get("spec", ""),
        "migrates": sub.get("migrates", []),
    }


def leaves(data: Item | None = None) -> list[Item]:
    """Return the atomic work units: every subtask of every task, then of every issue."""
    source = plan_data() if data is None else data
    out = [
        _leaf_item(s, str(t.get("phase", "")), "")
        for t in source.get("features", [])
        for s in t.get("subtasks") or []
    ]
    out += [_leaf_item(s, "", str(i["id"])) for i in issues(source) for s in i["subtasks"]]
    return out


def _all_items() -> list[Item]:
    """Every phase, task, issue and subtask (for name lookup by any id)."""
    out: list[Item] = [{"id": p["id"], "name": p.get("title", "")} for p in phases()]
    for owner in [*_tasks(), *issues()]:
        out.append({"id": owner["id"], "name": owner.get("title", "")})
        for s in owner.get("subtasks") or []:
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


def _issue(iid: str) -> Item | None:
    return next((i for i in issues() if str(i["id"]) == str(iid)), None)


def issue_branch(iid: str) -> str:
    """The branch issue <iid> lives on: <type>/<iid>-<slug>. Raises ValueError."""
    issue = _issue(iid)
    if issue is None:
        raise ValueError(f"issue {iid} does not exist in docs/plan/issues/")
    name = f"{issue['type']}/{iid}-{_config.issue_slug(str(issue.get('title', '')))}"
    if not _config.valid_branch(name):
        raise ValueError(f"issue {iid}: '{name}' is not a valid branch name")
    return name


def task_branch(tid: str, cfg: _config.Config) -> str:
    """The branch task (or issue) <tid> lives on. Raises ValueError with the reason."""
    if RE_ISSUE_ID.match(tid):
        return issue_branch(tid)
    name = _render(cfg.branch, tid, cfg)
    if not _config.valid_branch(name):
        raise ValueError(f"task {tid}: '{name}' is not a valid branch name")
    return name


def _git(cwd: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)


def branch_merged(tip: str, base: str, cwd: Path | None = None) -> bool:
    """<tip> reached <base>: merge commit or fast forward (ancestor), squash (base has the
    tip's version of every file the tip changed), or nothing to merge (no changed file)."""
    if _git(cwd, "merge-base", "--is-ancestor", tip, base).returncode == 0:
        return True
    fork = _git(cwd, "merge-base", base, tip)
    if fork.returncode != 0:
        return False
    changed = _git(cwd, "diff", "-z", "--name-only", fork.stdout.strip(), tip)
    if changed.returncode != 0:
        return False
    files = [f":(top){f}" for f in changed.stdout.split("\0") if f]
    if not files:
        return True
    return _git(cwd, "diff", "--quiet", tip, base, "--", *files).returncode == 0


ASSUME_VAR = "HARNESS_ASSUME_APPROVED"


def assumed(kind: str, ident: str = "") -> bool:
    """True when the gate's counterfactual run assumes this exact target approved
    (HARNESS_ASSUME_APPROVED=roadmap | phase:NN | task:N.M | issue:ISS-n | stack), issues
    #27, #115. Issue #125: a comma-separated list, the gate's own target and the pending
    re-gates below it (_plan_seal.cascade); each entry must match exactly."""
    target = f"{kind}:{ident}" if ident else kind
    return target in os.environ.get(ASSUME_VAR, "").split(",")


def read_verdict(path: Path) -> str:
    """The verdict token of a Codex review file, or "" if missing/unparseable."""
    try:
        match = RE_VERDICT.search(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return match.group(1) if match else ""


# ── task flow (issue #62) ────────────────────────────────────────────────────

LEGACY, UNSEALED, TASK_FLOW = "legacy", "unsealed", "task"


def sealed_specs(manifest: Path) -> bool:
    """The phase seal holds subtask specs (docs/plan/phase-NN-<slug>/...): the phase
    was broken down whole, before the task flow."""
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    rels = [line.split(maxsplit=1)[1] for line in lines if len(line.split(maxsplit=1)) == 2]
    return any(rel.startswith("docs/plan/phase-") and rel.count("/") >= 3 for rel in rels)


def phase_flow(pid: str) -> str:
    """legacy (sealed with its specs) | unsealed (approved, never sealed, has specs and
    no task gate: pre-#62 rules) | task (the task flow)."""
    manifest = phase_seal(pid)
    if manifest.is_file():
        return LEGACY if sealed_specs(manifest) else TASK_FLOW
    approved = read_verdict(phase_review(pid)) == "PHASE_APPROVED"
    tids = [str(t["id"]) for t in _tasks() if t.get("phase") == pid]
    gated = any(task_review(t).exists() for t in tids)
    if approved and _phase_leaves(pid) and not gated:
        return UNSEALED
    return TASK_FLOW


def legacy_done(pid: str) -> list[str] | None:
    """The tasks of a legacy phase already done when `make plan` first saw it."""
    try:
        data = json.loads(legacy_record(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    done = data.get("tasks_done") if isinstance(data, dict) else None
    return [str(t) for t in done] if isinstance(done, list) else None


def phase_tasks(pid: str) -> list[Item]:
    return [t for t in _tasks() if t.get("phase") == pid]


def task_approved(tid: str, pid: str) -> bool:
    if phase_flow(pid) != TASK_FLOW:
        return True
    return read_verdict(task_review(tid)) == "TASK_APPROVED" or assumed("task", tid)


def task_exempt(tid: str, pid: str) -> bool:
    """No task-close gate for this task: pre-#62 rules, or done before the task flow."""
    flow = phase_flow(pid)
    if flow == UNSEALED:
        return True
    if flow == LEGACY:
        done = legacy_done(pid)
        return done is None or tid in done
    return False


def task_closed(tid: str, pid: str) -> bool:
    return read_verdict(task_close(tid)) == "TASK_CLOSE_APPROVED" or task_exempt(tid, pid)


def phase_exempt(pid: str) -> bool:
    flow = phase_flow(pid)
    if flow == UNSEALED:
        return True
    if flow == LEGACY:
        done = legacy_done(pid)
        return done is None or all(str(t["id"]) in done for t in phase_tasks(pid))
    return False


def phase_closed(pid: str) -> bool:
    return read_verdict(phase_close(pid)) == "PHASE_CLOSE_APPROVED" or phase_exempt(pid)


def _task_leaves(tid: str) -> list[Item]:
    return [leaf for leaf in leaves() if parse_id(str(leaf["id"])) == ("task", tid)]


def _task_order_problems() -> list[str]:
    problems = []
    for phase in phases():
        pid = str(phase["id"])
        ordered = phase_tasks(pid)
        flow = phase_flow(pid)
        for prev, task in zip(ordered, ordered[1:], strict=False):
            tid, before = str(task["id"]), str(prev["id"])
            subs = _task_leaves(tid)
            started = any(leaf["status"] in STARTED for leaf in subs)
            if flow == TASK_FLOW and subs and not started and not task_closed(before, pid):
                problems.append(
                    f"task {tid} has subtask specs before its kickoff: task {before} is not "
                    f"TASK_CLOSE_APPROVED ({task_close(before)})"
                )
            elif started and not task_closed(before, pid):
                problems.append(
                    f"task {tid} started but task {before} is not TASK_CLOSE_APPROVED "
                    f"({task_close(before)})"
                )
    return problems


def task_ready_problems(tid: str) -> list[str]:
    task = _task(tid)
    if task is None:
        return [f"task {tid} does not exist in docs/plan/"]
    pid = str(task["phase"])
    if read_verdict(phase_review(pid)) != "PHASE_APPROVED":
        return [f"phase {pid} is not PHASE_APPROVED ({phase_review(pid)})"]
    if phase_flow(pid) != TASK_FLOW:
        return [
            f"task {tid}: phase {pid} was broken down whole (sealed with its specs): "
            "it has no task gate"
        ]
    problems = []
    if not _task_leaves(tid):
        problems.append(f"task {tid} has no subtask specs yet: write them at its kickoff")
    ordered = [str(t["id"]) for t in phase_tasks(pid)]
    index = ordered.index(tid)
    if index > 0 and not task_closed(ordered[index - 1], pid):
        before = ordered[index - 1]
        problems.append(f"task {before} is not TASK_CLOSE_APPROVED ({task_close(before)})")
    return problems + _declaration_problems(_task_leaves(tid))


def _declaration_problems(units: list[Item]) -> list[str]:
    """The kickoff's changelog declaration (releases, docs/design/releases.md §3): only
    an activated project's task and issue gates ask for it."""
    import _changelog  # lazy: keeps the plan rules importable on their own

    return _changelog.missing_declarations(Path.cwd(), units)


def task_close_ready_problems(tid: str) -> list[str]:
    task = _task(tid)
    if task is None:
        return [f"task {tid} does not exist in docs/plan/"]
    pid = str(task["phase"])
    subs = _task_leaves(tid)
    if not subs:
        return [f"task {tid} has no subtasks yet"]
    if task_exempt(tid, pid):
        return [
            f"task {tid} was done before the task flow ({legacy_record(pid)}) or its phase "
            "keeps the pre-task-flow rules: it has no task-close gate"
        ]
    open_ids = [str(s["id"]) for s in subs if not complete(s)]
    if open_ids:
        return [f"task {tid} is not finished: {', '.join(open_ids)} not done"]
    return []


def phase_close_ready_problems(pid: str) -> list[str]:
    if not any(p["id"] == pid for p in phases()):
        return [f"phase {pid} does not exist in docs/plan/"]
    if phase_exempt(pid):
        return [f"phase {pid} was done before the task flow ({legacy_record(pid)}): no gate"]
    if not _done(pid):
        return [f"phase {pid} is not finished: every subtask must be done"]
    return [
        f"task {t['id']} is not TASK_CLOSE_APPROVED ({task_close(str(t['id']))})"
        for t in phase_tasks(pid)
        if not task_closed(str(t["id"]), pid)
    ]


def _target_leaves(target: str) -> list[Item]:
    return _task_leaves(target) if "." in target else _phase_leaves(target)


def close_sources(target: str) -> int:
    """The only files a task-close / phase-close gate reads besides the diff: the
    specs of its subtasks and their implementation and code-review reports."""
    found = _target_leaves(target)
    if not found:
        return 1
    for leaf in found:
        sid = str(leaf["id"])
        print(leaf["spec"])
        for report in (Path(f"progress/impl_{sid}.md"), Path(f"progress/review_{sid}.md")):
            if report.is_file():
                print(report.as_posix())
    return 0


def missing_close_commits(target: str) -> list[str]:
    """The subtasks of a task (N.M) or phase (NN) with no `[N.M.K]`-tagged close
    commit (fail closed: a git error counts as missing). A close gate counts each
    as a gap."""
    missing = []
    for leaf in _target_leaves(target):
        sid = str(leaf["id"])
        if leaf["status"] == "baseline":
            continue  # no close by design
        found = tagged_commits(sid)
        if not found:
            missing.append(sid)
    return missing


def tagged_commits(sid: str) -> list[str]:
    """Commits, oldest first, whose SUBJECT carries `[sid]` (a close commit); a tag
    in the body does not count. Empty on any git error (fail closed)."""
    res = _git(None, "log", "--reverse", "--format=%H%x00%s")
    if res.returncode != 0:
        return []
    tag = f"[{sid}]"
    rows = (line.split("\0", 1) for line in res.stdout.splitlines() if "\0" in line)
    return [sha for sha, subject in rows if tag in subject]


def close_missing(target: str) -> int:
    for sid in missing_close_commits(target):
        print(sid)
    return 0


def close_commits(target: str) -> int:
    """The close commits of a task's (N.M) or a phase's (NN) subtasks, oldest first:
    the single commit of each close carries its `[N.M.K]` tag in the message."""
    ids = [str(leaf["id"]) for leaf in _target_leaves(target)]
    if not ids:
        return 1
    found: list[str] = []
    for sid in ids:
        found += [sha for sha in tagged_commits(sid) if sha not in found]
    order = _git(None, "log", "--reverse", "--format=%H")
    rank = {sha: i for i, sha in enumerate(order.stdout.split())}
    print("\n".join(sorted(found, key=lambda sha: rank.get(sha, 0))))
    return 0


# ── plan rules ───────────────────────────────────────────────────────────────


def acceptance_files(root: Path, leaf: str) -> list[Path]:
    """The acceptance tests of a leaf: exact stem, since the glob alone also matches
    longer ids (test_1.1.1.* finds test_1.1.1.2.py)."""
    folder = root / "tests" / "acceptance"
    found = folder.glob(f"test_{glob.escape(leaf)}.*")
    return sorted(p for p in found if p.is_file() and p.stem == f"test_{leaf}")


def _phase_leaves(pid: str) -> list[Item]:
    return [leaf for leaf in leaves() if leaf["phase"] == pid]


def _broken_down(phase: Item) -> bool:
    return bool(phase.get("tasks"))


def complete(leaf: Item) -> bool:
    """Done, or baseline in a phase Codex approved (unapproved baseline is a claim)."""
    if leaf["status"] == "baseline" and leaf.get("issue"):
        return False  # issues have no baseline: their work is new by definition
    if leaf["status"] == "baseline":
        pid = str(leaf["phase"])
        tid = parse_id(str(leaf["id"]))[1]
        approved = read_verdict(phase_review(pid)) == "PHASE_APPROVED"
        return approved and task_approved(tid, pid)
    return bool(leaf["status"] == "done")


def _complete_ids() -> set[str]:
    return {str(leaf["id"]) for leaf in leaves() if complete(leaf)}


def _done(pid: str) -> bool:
    subs = _phase_leaves(pid)
    return bool(subs) and all(complete(s) for s in subs)


def _roadmap_problems() -> list[str]:
    if read_verdict(ROADMAP_REVIEW) == "ROADMAP_APPROVED" or assumed("roadmap"):
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
            if not phase_closed(str(earlier["id"])):
                problems.append(
                    f"phase {phase['id']} is broken down but phase {earlier['id']} is not "
                    f"PHASE_CLOSE_APPROVED ({phase_close(str(earlier['id']))})"
                )
                break
    return problems


def _gate_problems(leaf: Item) -> list[str]:
    """The breakdown gate a started leaf needs: its phase's, or its issue's."""
    if leaf.get("issue"):
        iid = str(leaf["issue"])
        if read_verdict(issue_review(iid)) == "ISSUE_APPROVED" or assumed("issue", iid):
            return []
        return [
            f"{leaf['id']} is {leaf['status']} but issue {iid} is not ISSUE_APPROVED "
            f"({issue_review(iid)})"
        ]
    pid = leaf["phase"]
    if read_verdict(phase_review(pid)) == "PHASE_APPROVED" or assumed("phase", pid):
        tid = parse_id(str(leaf["id"]))[1]
        if task_approved(tid, pid):
            return []
        return [
            f"{leaf['id']} is {leaf['status']} but task {tid} is not TASK_APPROVED "
            f"({task_review(tid)})"
        ]
    return [
        f"{leaf['id']} is {leaf['status']} but phase {pid} is not PHASE_APPROVED "
        f"({phase_review(pid)})"
    ]


def _start_problems(leaf: Item, status: dict[str, str], finished: set[str]) -> list[str]:
    problems = _gate_problems(leaf)
    for dep in leaf.get("depends", []):
        if dep not in finished:
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
        subs = task.get("subtasks") or []
        if not task.get("ticket") and subs and all(s.get("status") == "baseline" for s in subs):
            continue  # never goes through the cycle, so it never gets a branch
        try:
            task_branch(str(task["id"]), cfg)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def plan_problems() -> list[str]:
    units = leaves()
    status = {str(leaf["id"]): str(leaf["status"]) for leaf in units}
    finished = _complete_ids()
    problems = _roadmap_problems() + _progression_problems() + _task_order_problems()
    for leaf in units:
        if leaf["status"] in STARTED:
            problems += _start_problems(leaf, status, finished)
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
    if not phase.get("tasks"):
        problems.append(f"phase {pid} has no tasks yet: break it down first")
    progression = [p for p in _progression_problems() if p.startswith(f"phase {pid} ")]
    return problems + progression + _whole_phase_declarations(pid)


def _whole_phase_declarations(pid: str) -> list[str]:
    """A phase broken down whole has no task gate: its phase gate asks for the kickoff's
    changelog declaration instead (releases, review rel1rev #5)."""
    leaves_ = _phase_leaves(pid)
    if not leaves_:
        return []
    if phase_flow(pid) == TASK_FLOW:
        tids = [str(t["id"]) for t in _tasks() if t.get("phase") == pid]
        if phase_seal(pid).is_file() or any(task_review(t).exists() for t in tids):
            return []  # the task flow: each task gate asks for its own specs
    return _declaration_problems(leaves_)


def issue_ready_problems(iid: str) -> list[str]:
    issue = _issue(iid)
    if issue is None:
        return [f"issue {iid} does not exist in docs/plan/issues/"]
    if not issue.get("subtasks"):
        return [f"issue {iid} has no subtasks yet: break it down first"]
    return _declaration_problems([leaf for leaf in leaves() if leaf["issue"] == iid])


def subtask_ready_problems(fid: str) -> list[str]:
    leaf = _leaf(fid)
    if leaf is None:
        return [f"subtask {fid} does not exist in docs/plan/"]
    status = {str(item["id"]): str(item["status"]) for item in leaves()}
    return _start_problems({**leaf, "status": "starting"}, status, _complete_ids())


def close_ready_problems(fid: str) -> list[str]:
    """The close (doc gate) runs once, after an APPROVED code review."""
    leaf = _leaf(fid)
    if leaf is None:
        return [f"subtask {fid} does not exist in docs/plan/"]
    if leaf["status"] == "done":
        owner = fid.rsplit(".", 1)[0]  # task N.M or issue ISS-n
        return [
            f"{fid} is already closed (status: done): nothing to document or commit. "
            f"If only its PR is missing, retry with: ./harness/review_with_codex.sh pr {owner}"
        ]
    review = Path(f"progress/review_{fid}.md")
    if read_verdict(review) != "APPROVED":
        return [f"{fid} has no APPROVED code review ({review}): run the code gate first"]
    return []


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


def branch_of(fid: str) -> int:
    return branch(parse_id(fid)[1])


def issue_of(fid: str) -> int:
    kind, owner = parse_id(fid)
    return _print(owner if kind == "issue" and _leaf(fid) else None)


def issue_field(iid: str, key: str) -> int:
    issue = _issue(iid)
    return _print(issue.get(key) if issue else None)


def commit_prefix(sid: str) -> int:
    kind, tid = parse_id(sid)
    if kind == "issue":
        return 0  # issues have no ticket: their id is already in the [ISS-n.k] subject tag
    return _git_command(lambda cfg: _render(cfg.commit_prefix, tid, cfg))


FILES_LIMIT = 3
TEST_ONLY = "Test-only verification (no production behaviour change)"


def _test_only(leaf: Item) -> bool:
    """The spec declares the versioned test-only exception (#23): empty `files:` is fine."""
    spec = str(leaf.get("spec") or "")
    try:
        return bool(spec) and TEST_ONLY in Path(spec).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False


def _legacy_unchanged(item_id: str) -> bool:
    import _legacy  # lazy: _legacy imports this module

    return _legacy.is_legacy_unchanged(Path.cwd(), item_id)


def files_limit_warnings(units: list[Item]) -> list[str]:
    """I16 (#81, WARN): a subtask declares 1-3 `files:`. Done/baseline and unchanged
    legacy leaves (plan 0.6) are exempt; an empty list is fine for a test-only spec."""
    out = []
    for leaf in units:
        count = len(leaf.get("files") or [])
        if 1 <= count <= FILES_LIMIT or leaf["status"] in ("done", "baseline"):
            continue
        if count == 0 and _test_only(leaf):
            continue
        if _legacy_unchanged(str(leaf["id"])):
            continue
        out.append(f"{leaf['id']} declares {count} files in files: (expected 1-{FILES_LIMIT})")
    return out


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
    import _changelog  # lazy, as _review_risk
    import _review_risk  # lazy: _review_risk imports this module

    problems += _review_risk.problems(units, Path.cwd())
    changelog, changelog_warnings = _changelog.check(Path.cwd(), plan_data(), units)
    problems += changelog
    warnings = files_limit_warnings(units) + _review_risk.warnings(units, Path.cwd())
    for warning in warnings + changelog_warnings:
        say("WARN", warning)
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
    arg2 = rest[1] if len(rest) > 1 else ""
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
        "issue-ready": lambda: _report(issue_ready_problems(arg)),
        "close-ready": lambda: _report(close_ready_problems(arg)),
        "task-ready": lambda: _report(task_ready_problems(arg)),
        "task-close-ready": lambda: _report(task_close_ready_problems(arg)),
        "phase-close-ready": lambda: _report(phase_close_ready_problems(arg)),
        "close-commits": lambda: close_commits(arg),
        "close-sources": lambda: close_sources(arg),
        "close-missing": lambda: close_missing(arg),
        "issue-of": lambda: issue_of(arg),
        "issue-field": lambda: issue_field(arg, arg2),
        "branch-of": lambda: branch_of(arg),
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
