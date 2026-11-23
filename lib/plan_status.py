#!/usr/bin/env python3
"""`make status`: the whole plan at a glance — what is planned, done, in progress,
pending or blocked, every Codex gate verdict, and the next step to take.

Read-only and live: it reads feature_list.json (run `make plan` after editing
docs/plan/) and the verdict files in progress/ as they are now.

Issues lane: the issues planned on this branch (docs/plan/issues/) get their own
block, apart from the phases and their counters, and the shared queue is read
from GitHub (open issues labelled priority:* and not status:in-progress). Without
gh or network the queue line says so and the rest prints as usual.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import _config
import _gate_run
import _harness
import _pr
import _resume
import _stack
from _status import Kind, paint

Item = dict[str, Any]
OUT = sys.stdout
GH = os.environ.get("HARNESS_GH", "gh")
PRIORITIES = ("now", "next", "backlog")
TAGS: dict[str, tuple[str, Kind | None]] = {
    "done": ("[DONE]", "OK"),
    "baseline": ("[BASE]", "OK"),
    "in_progress": ("[WIP]", "WARN"),
    "blocked": ("[BLOCK]", "FAIL"),
    "pending": ("[TODO]", None),
}
GATE = "./harness/review_with_codex.sh"


def _verdict(path: Path) -> str:
    return _harness.read_verdict(path)


def _gate_state(path: Path, approved: str) -> str:
    verdict = _verdict(path)
    if verdict == approved:
        return paint(verdict, "OK", OUT)
    if verdict:
        return paint(f"{verdict} ({path})", "FAIL", OUT)
    return "pending Codex review"


def _subs(data: Item, pid: str) -> list[Item]:
    return [s for t in data["features"] if t["phase"] == pid for s in t["subtasks"]]


def _complete(sub: Item, pid: str) -> bool:
    return _harness.complete({**sub, "phase": pid})


def _complete_ids(data: Item) -> set[str]:
    return {s["id"] for t in data["features"] for s in t["subtasks"] if _complete(s, t["phase"])}


def _phase_line(data: Item, phase: Item) -> str:
    pid, subs = phase["id"], _subs(data, phase["id"])
    head = f"Phase {pid} — {phase['title']}"
    if not phase["tasks"]:
        return f"{head} · not broken down yet"
    done = sum(_complete(s, pid) for s in subs)
    if subs and done == len(subs):
        state = paint("done", "OK", OUT)
    else:
        state = _gate_state(_harness.phase_review(pid), "PHASE_APPROVED")
    line = f"{head} · {state} · {done}/{len(subs)} done"
    if subs and done == len(subs) and _shows_task_flow(pid):
        line += f" · close: {_verdict(_harness.phase_close(pid)) or '—'}"
    return line


def _shows_task_flow(pid: str) -> bool:
    """Task-flow columns only for a phase sealed tasks-only: every other phase prints
    exactly as before #62."""
    sealed = _harness.phase_seal(pid).is_file()
    return sealed and _harness.phase_flow(pid) == _harness.TASK_FLOW


def _task_line(task: Item) -> str:
    line = f"  {task['id']} {task['title']} · {task['status'].replace('_', ' ')}"
    if _shows_task_flow(str(task["phase"])):
        gate = _verdict(_harness.task_review(task["id"])) or "—"
        close = _verdict(_harness.task_close(task["id"])) or "—"
        line += f" · task gate: {gate} · close: {close}"
    return line


def _sub_line(sub: Item) -> str:
    label, kind = TAGS.get(sub["status"], ("[?]", None))
    line = f"    {paint(label, kind, OUT)}{' ' * (8 - len(label))}{sub['id']} {sub['title']}"
    if sub["status"] == "in_progress":
        plan = _verdict(Path(f"progress/plan_review_{sub['id']}.md")) or "—"
        code = _verdict(Path(f"progress/review_{sub['id']}.md")) or "—"
        line += f" · plan: {plan} · code: {code}"
    return line


def _stack_lines() -> list[str]:
    """The `Stack` line (issue #115), only in a project that uses the stack step."""
    state = _stack.label(Path("."))
    if state is None:
        return []
    kind: Kind = "OK" if state == _stack.APPROVED else "WARN"
    return [f"Stack      {paint(state, kind, OUT)}"]


def _tree(data: Item) -> list[str]:
    roadmap = f"Roadmap    {_gate_state(_harness.ROADMAP_REVIEW, 'ROADMAP_APPROVED')}"
    lines = [*_stack_lines(), roadmap, ""]
    for phase in data["phases"]:
        lines.append(_phase_line(data, phase))
        for task in (t for t in data["features"] if t["phase"] == phase["id"]):
            lines.append(_task_line(task))
            lines += [_sub_line(s) for s in task["subtasks"]]
    return lines


def _summary(data: Item) -> str:
    subs = [s for t in data["features"] for s in t["subtasks"]]
    n = {st: sum(s["status"] == st for s in subs) for st in TAGS}
    base = f" · {n['baseline']} baseline" if n["baseline"] else ""
    return (
        f"{n['done']} done{base} · {n['in_progress']} in progress · {n['pending']} pending"
        f" · {n['blocked']} blocked  ({len(subs)} subtasks)"
    )


# ── issues lane ──────────────────────────────────────────────────────────────


def _issue_line(issue: Item) -> str:
    subs = issue["subtasks"]
    head = f"{issue['id']} {issue['title']}"
    tail = f"{issue['type']} · priority {issue['priority']}"
    if not subs:
        return f"{head} · planning · {tail}"
    done = sum(s["status"] == "done" for s in subs)
    if done == len(subs):
        state = paint("done", "OK", OUT)
    else:
        state = _gate_state(_harness.issue_review(issue["id"]), "ISSUE_APPROVED")
    return f"{head} · {state} · {done}/{len(subs)} done · {tail}"


def _issues_tree(data: Item) -> list[str]:
    issues: list[Item] = data.get("issues", [])
    if not issues:
        return []
    lines = ["", "Issues"]
    for issue in issues:
        lines.append(f"  {_issue_line(issue)}")
        lines += [_sub_line(s) for s in issue["subtasks"]]
    subs = [s for i in issues for s in i["subtasks"]]
    done = sum(s["status"] == "done" for s in subs)
    plural = "s" * (len(issues) != 1)
    lines.append(f"{len(issues)} issue{plural} · {done}/{len(subs)} issue subtasks done")
    return lines


def github_queue() -> list[Item] | None:
    """Queued issues on GitHub (now, next, backlog), or None when gh is unreachable."""
    cmd = [GH, "issue", "list", "--state", "open", "--limit", "200"]
    cmd += ["--json", "number,title,labels,assignees"]
    try:
        res = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=15)
        found = json.loads(res.stdout) if res.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if not isinstance(found, list):
        return None
    queue = []
    for issue in found:
        labels = [lb["name"] for lb in issue.get("labels", [])]
        prio = [lb.split(":", 1)[1] for lb in labels if lb.startswith("priority:")]
        if prio and prio[0] in PRIORITIES and "status:in-progress" not in labels:
            queue.append({"number": issue["number"], "title": issue["title"], "priority": prio[0]})
    return sorted(queue, key=lambda i: (PRIORITIES.index(i["priority"]), i["number"]))


def _queue_lines(data: Item, queue: list[Item] | None) -> list[str]:
    if queue is None:
        if not data.get("issues"):
            return []  # a project that does not use issues: no noise
        return ["", "Issue queue (GitHub) unavailable (gh not reachable)"]
    if not queue and not data.get("issues"):
        return []
    lines = ["", "Issue queue (GitHub)"]
    lines += [f"  #{i['number']} {i['title']} · {i['priority']}" for i in queue] or ["  empty"]
    return lines


def _git_line(*args: str) -> str | None:
    try:
        res = subprocess.run(["git", *args], text=True, capture_output=True, check=False)
    except OSError:
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def _done_issue_next(issue: Item) -> str | None:
    """A finished issue: merge its PR (on its branch), then resume an interrupted task."""
    branch = _git_line("branch", "--show-current") or ""
    on_issue = not branch or branch.split("/", 1)[-1].startswith(f"{issue['id']}-")
    resume = issue.get("interrupts")
    if on_issue:
        step = f"issue {issue['id']} is done: merge its PR (Closes #{issue['number']})"
        return f"{step}, then resume task {resume}: harness/issue.sh resume" if resume else step
    if resume and _git_line("merge-base", "--is-ancestor", "HEAD", str(resume)) is None:
        return f"resume task {resume}: harness/issue.sh resume"
    return None


def _issue_gate_next(iid: str) -> str | None:
    verdict = _verdict(_harness.issue_review(iid))
    if verdict != "ISSUE_APPROVED" and (capped := _capped("issue", iid)):
        return capped
    if verdict == "CHANGES_REQUESTED":
        return f"fix the breakdown of issue {iid} ({_harness.issue_review(iid)}), then issue gate"
    if verdict != "ISSUE_APPROVED":
        return f"issue gate: {GATE} issue {iid}"
    return _seal_failed("issue", iid, f"issue {iid}")


def _issue_next(data: Item, issue: Item) -> str | None:
    iid, subs = issue["id"], issue["subtasks"]
    if not subs:
        return f"break down issue {iid} into subtask specs"
    current = next((s for s in subs if s["status"] == "in_progress"), None)
    # Never work under an approved breakdown its gate failed to seal (issue #46).
    seal = _seal_failed("issue", iid, f"issue {iid}")
    if seal or current:
        return seal or _leaf_next(str(current["id"] if current else ""))
    if all(s["status"] == "done" for s in subs):
        return _done_issue_next(issue)
    gate = _issue_gate_next(iid)
    if gate:
        return gate
    finished = _complete_ids(data) | {s["id"] for s in subs if s["status"] == "done"}
    ready = [s for s in subs if s["status"] == "pending" and set(s["depends"]) <= finished]
    if ready:
        return f"start subtask {ready[0]['id']} (leader: in_progress + make plan)"
    return f"resolve blocked subtask(s) of issue {iid}"


def _lane_next(data: Item, queue: list[Item] | None) -> str | None:
    """Issue steps that come before the phase flow: an issue planned on this branch,
    then a `now` issue waiting in the queue (it may interrupt a task, but only between
    subtasks). A task subtask in progress is left to the phase flow, exactly as before."""
    if any(s["status"] == "in_progress" for t in data["features"] for s in t["subtasks"]):
        return None
    for issue in data.get("issues", []):
        step = _issue_next(data, issue)
        if step:
            return step
    urgent = [i for i in queue or [] if i["priority"] == "now"]
    if urgent:
        n = urgent[0]["number"]
        return f"start issue #{n} (now): make issue-start N={n}"
    return None


# ── next step ────────────────────────────────────────────────────────────────


def _capped(gate: str, fid: str) -> str | None:
    """`needs human ...` once a gate hit [review] max_rounds (issue #42) or used its
    attempt budget (issue #46): the gate itself would refuse to launch."""
    try:
        capped = _pr.needs_human(gate, fid) if _pr.capped(gate, fid) else None
    except _config.ConfigError as exc:
        return f"fix {_config.CONFIG_FILE}: {exc}"
    return capped or _budget(gate, fid)


def _budget(step: str, fid: str) -> str | None:
    """`needs human (N failed attempts) ...` once a step used its budget (issue #46)."""
    try:
        return _resume.needs_human(step, fid)
    except _config.ConfigError as exc:
        return f"fix {_config.CONFIG_FILE}: {exc}"
    except _resume.LedgerError as exc:
        return f"needs human: {exc}"


def _manual() -> bool:
    try:
        return _config.load(Path.cwd()).codex_mode == "manual"
    except _config.ConfigError:
        return False  # _capped and _budget report it


def _approval(sid: str) -> str:
    """fresh | stale | missing | unknown: does the bound code approval cover the tree?"""
    runs = _resume.runs_dir()
    if runs is None:
        return "unknown"
    path = runs / f"code.approved_{sid}.json"
    if not path.is_file():
        return "missing"
    try:
        stale = _gate_run.is_stale(
            _gate_run.load_attempt(path), _gate_run.current_attempt(sid, 0, "")
        )
    except (ValueError, OSError):
        return "stale"
    return "stale" if stale else "fresh"


def _plan_next(sid: str, plan: str) -> str:
    if capped := _capped("plan", sid):
        return capped
    reason = _resume.last_failure_reason("plan", sid)
    if plan == "CHANGES_REQUESTED":  # the requested changes come first, a crash or not
        why = f" (last attempt: {reason})" if reason else ""
        return f"fix the spec of {sid} (progress/plan_review_{sid}.md), then plan gate{why}"
    if reason:
        return f"plan gate again (last attempt: {reason}): {GATE} plan {sid}"
    return f"plan gate: {GATE} plan {sid}"


def _seal_next(sid: str) -> str | None:
    """A plan gate (auto mode) that approved but did not seal: a seal failure since its
    last outcome (an older seal file may cover the old tests), or no seal at all after
    a failed attempt. Run it again so it seals. With no ledger evidence nothing changes
    (as before #46); manual mode records no attempt, the human seals there."""
    if error := _resume.ledger_error("plan", sid):  # its seal history cannot be trusted
        return f"needs human: {error}"
    reason = _resume.seal_failure("plan", sid)
    if reason is None and not Path(f"progress/acceptance_{sid}.sha256").is_file():
        reason = _resume.last_failure_reason("plan", sid)
    if reason is None:
        return None
    if _manual():  # the evidence predates a switch to manual mode: the human seals
        return f"needs human: seal the approved plan of {sid} (human only): {GATE} seal {sid}"
    return _budget("plan", sid) or (
        f"finish the seal of {sid} (PLAN_APPROVED, last attempt: {reason}): "
        f"plan gate again: {GATE} plan {sid}"
    )


def _code_next(sid: str, code: str) -> str:
    if capped := _capped("code", sid):
        return capped
    reason = _resume.last_failure_reason("code", sid)
    if reason and reason.startswith("check red"):
        return (
            f"fix {sid}: the check was red at its last code gate ({reason}), "
            f"then code gate: {GATE} code {sid}"
        )
    if code == "CHANGES_REQUESTED":  # the requested changes come first, a crash or not
        why = f" (last attempt: {reason})" if reason else ""
        return f"fix {sid} (progress/review_{sid}.md), then code gate{why}"
    if reason:
        return f"code gate again (last attempt: {reason}): {GATE} code {sid}"
    return f"implement {sid} (implementer), then code gate: {GATE} code {sid}"


def _close_next(sid: str) -> str:
    if _resume.last_failure_reason("code", sid):  # a later code attempt failed
        return _code_next(sid, "APPROVED")
    state = _approval(sid) if _resume.has_attempts("code", sid) else "unknown"
    if state in ("stale", "missing"):  # only with ledger evidence (else as before #46)
        why = "no longer covers the tree" if state == "stale" else "has no gate record"
        return _budget("code", sid) or (
            f"the code approval of {sid} {why}: code gate again: {GATE} code {sid}"
        )
    if budget := _budget("close", sid):
        return budget
    msg = Path(f"progress/close_{sid}.msg")
    reason = _resume.last_failure_reason("close", sid)
    # The documenter wrote the message, then the commit failed (or never ran): with a
    # failed close, or with an approval that still covers the tree (`doc` refuses
    # then), the step is to commit it.
    if msg.is_file() and (reason or _approval(sid) == "fresh"):
        why = f", last attempt: {reason}" if reason else ""
        return f"retry the close of {sid} ({msg} is waiting{why}): {GATE} close {sid}"
    why = f" (last attempt: {reason})" if reason else ""
    return f"close: {GATE} doc {sid}{why}"


def _leaf_next(sid: str) -> str:
    """The next step of the single in_progress leaf, from its artifacts (issue #46)."""
    plan = _verdict(Path(f"progress/plan_review_{sid}.md"))
    if plan != "PLAN_APPROVED":
        return _plan_next(sid, plan)
    if seal := _seal_next(sid):
        return seal
    code = _verdict(Path(f"progress/review_{sid}.md"))
    if code != "APPROVED":
        return _code_next(sid, code)
    if error := _resume.ledger_error("code", sid):  # its history cannot be trusted
        return f"needs human: {error}"
    return _close_next(sid)


def _pending_close(leaves: list[Item], active: str) -> str | None:
    """Evidence of an interrupted close: the spec says done (the documenter's edit),
    no close commit, and the ledger says the close failed."""
    for leaf in leaves:
        sid = str(leaf["id"])
        error = _resume.ledger_error("close", sid) if leaf["status"] == "done" else None
        if error and not _harness.tagged_commits(sid):
            return f"needs human: {error}; {sid} says done but has no close commit"
        reason = _resume.last_failure_reason("close", sid)
        if not (leaf["status"] == "done" and reason) or _harness.tagged_commits(sid):
            continue
        msg = Path(f"progress/close_{sid}.msg")
        if active:
            return f"needs human: the close of {sid} failed ({reason}){active}"
        if _approval(sid) != "fresh":  # a retry would only be refused again
            return (
                f"needs human: the close of {sid} failed ({reason}) and its code approval no "
                "longer covers the tree, while its spec already says done"
            )
        if not msg.is_file():  # the documenter stopped after `status: done`, before its message
            return (
                f"needs human: the close of {sid} stopped before its commit ({reason}): its spec "
                "says done but no close commit and no message exist"
            )
        return _budget("close", sid) or (
            f"retry the close of {sid} ({msg} is waiting, last attempt: {reason}): "
            f"{GATE} close {sid}"
        )
    return None


def _pending_pr(leaves: list[Item], active: str) -> str | None:
    for tid in _resume.failed_prs():
        owned = [leaf for leaf in leaves if str(leaf["id"]).rsplit(".", 1)[0] == tid]
        if not owned or not all(_harness.complete(leaf) for leaf in owned):
            continue
        reason = _resume.last_failure_reason("pr", tid) or "unreadable ledger"
        if active:
            return f"needs human: the PR of {tid} did not go through ({reason}){active}"
        return _budget("pr", tid) or (
            f"retry the PR of {tid} (last attempt: {reason}): {GATE} pr {tid}"
        )
    return None


def _resume_next(data: Item) -> str | None:
    """Steps that come before everything else (issue #46): a broken invariant, a close
    whose commit failed after the spec said done, a PR that did not go through."""
    if run := _resume.running():  # a gate, close or PR still at work: never a second one
        return _resume.wait_text(run)
    if _resume.index_stale():  # the specs moved on: derive nothing from the old index
        return _resume.STALE_INDEX
    leaves = _harness.leaves(data)
    started = [str(leaf["id"]) for leaf in leaves if leaf["status"] == "in_progress"]
    if len(started) > 1:
        return (
            f"needs human: {len(started)} subtasks are in_progress ({', '.join(started)}); "
            "exactly one may be: the human fixes their specs, then make plan"
        )
    active = f"; {started[0]} is in_progress meanwhile" if started else ""
    return _pending_close(leaves, active) or _pending_pr(leaves, active)


def _close_gate_next(gate: str, fid: str, path: Path, approved: str) -> str:
    verdict = _verdict(path)
    if verdict != approved and (capped := _capped(gate, fid)):
        return capped
    what = "task" if gate == "task-close" else "phase"
    if verdict == "CHANGES_REQUESTED":
        return (
            f"fix the gaps of {what} {fid} ({path}): new subtask(s) for them, "
            f"then {gate} gate: {GATE} {gate} {fid}"
        )
    return f"{gate} gate: {GATE} {gate} {fid}"


def _task_gate_next(tid: str) -> str | None:
    path = _harness.task_review(tid)
    verdict = _verdict(path)
    if verdict == "TASK_APPROVED":
        return _seal_failed("task", tid, f"task {tid}")
    if capped := _capped("task", tid):
        return capped
    if verdict == "CHANGES_REQUESTED":
        return f"fix the breakdown of task {tid} ({path}), then task gate: {GATE} task {tid}"
    return f"task gate: {GATE} task {tid} (then the user's OK)"


def _task_flow_next(data: Item, pid: str) -> str:
    """Task flow (issue #62): kickoff, task gate, subtasks, task-close; phase-close."""
    tasks = [t for t in data["features"] if t["phase"] == pid]
    previous = None
    for task in tasks:
        tid, subs = str(task["id"]), task["subtasks"]
        if subs and all(_complete(s, pid) for s in subs):
            if previous and not _harness.task_closed(previous, pid):
                path = _harness.task_close(previous)
                return _close_gate_next("task-close", previous, path, "TASK_CLOSE_APPROVED")
            previous = tid
            continue
        if previous and not _harness.task_closed(previous, pid):
            path = _harness.task_close(previous)
            return _close_gate_next("task-close", previous, path, "TASK_CLOSE_APPROVED")
        if not subs:
            after = f" (review what task {previous} delivered first)" if previous else ""
            return (
                f"kickoff task {tid}{after}: one question at a time if the plan no longer "
                "fits, then write its subtask specs + make plan (claude-planner)"
            )
        if _harness.task_approved(tid, pid):
            gate = _seal_failed("task", tid, f"task {tid}")
        else:
            gate = _task_gate_next(tid)
        return gate or _phase_next(data, pid)
    if previous and not _harness.task_closed(previous, pid):
        path = _harness.task_close(previous)
        return _close_gate_next("task-close", previous, path, "TASK_CLOSE_APPROVED")
    path = _harness.phase_close(pid)
    return _close_gate_next("phase-close", pid, path, "PHASE_CLOSE_APPROVED")


def _phase_next(data: Item, pid: str) -> str:
    subs = _subs(data, pid)
    current = next((s for s in subs if s["status"] == "in_progress"), None)
    if current:
        return _leaf_next(current["id"])
    finished = _complete_ids(data)
    ready = [s for s in subs if s["status"] == "pending" and set(s["depends"]) <= finished]
    if ready:
        return f"start subtask {ready[0]['id']} (leader: in_progress + make plan)"
    blocked = [s["id"] for s in subs if s["status"] == "blocked"]
    return f"resolve blocked subtask(s): {', '.join(blocked) or 'see dependencies'}"


def _seal_failed(gate: str, fid: str, what: str) -> str | None:
    """An approved plan-level gate whose last attempt failed at the seal (issue #46):
    run the gate again so it seals (auto mode; manual mode records no attempt)."""
    if error := _resume.ledger_error(gate, fid):  # its seal history cannot be trusted
        return f"needs human: {error}"
    reason = _resume.seal_failure(gate, fid)
    if not reason:
        return None
    single = gate in ("roadmap", "stack")  # gates that take no id
    cmd = f"{GATE} {gate}" if single else f"{GATE} {gate} {fid}"
    if _manual():  # manual mode: only the human seals
        target = gate if single else f"{gate} {fid}"
        return f"needs human: seal {what} (human only): {GATE} seal {target}"
    return _budget(gate, fid) or (
        f"finish the seal of {what} (last attempt: {reason}): {gate} gate again: {cmd}"
    )


STACK_STEP = (
    "ask the leader to define the stack (stack step: one question at a time -> "
    "docs/stack.md), then stack gate"
)
STACK_NEXT = {
    _stack.CHANGED: f"docs/stack.md changed since STACK_APPROVED: stack gate again: {GATE} stack",
    _stack.REJECTED_STATE: f"fix the stack ({_stack.STACK_REVIEW}), then stack gate: {GATE} stack",
    _stack.MISSING: STACK_STEP,
    _stack.DRAFT: STACK_STEP,
    _stack.UNREVIEWED: f"stack gate: {GATE} stack",
}


def _stack_next() -> str | None:
    """The stack step (issue #115): before the discovery, and again whenever
    docs/stack.md changed after its approval. None when it does not apply."""
    root = Path(".")
    state = _stack.state(root) if _stack.required(root) else _stack.OK_STATE
    if state == _stack.OK_STATE:
        return None
    if state == _stack.UNSEALED:
        return _seal_failed("stack", "stack", "the stack") or (
            f"needs human: seal the stack (human only): {GATE} seal stack"
        )
    return _capped("stack", "stack") or STACK_NEXT[state]


def _roadmap_next(data: Item) -> str | None:
    """The step before any phase work, or None once the roadmap is approved."""
    if not data["phases"]:
        return "ask the leader to plan the project (discovery → PLAN.md + every phase)"
    roadmap = _verdict(_harness.ROADMAP_REVIEW)
    if roadmap != "ROADMAP_APPROVED" and (capped := _capped("roadmap", "roadmap")):
        return capped
    if roadmap == "CHANGES_REQUESTED":
        return f"fix the roadmap ({_harness.ROADMAP_REVIEW}), then roadmap gate: {GATE} roadmap"
    if roadmap != "ROADMAP_APPROVED":
        return f"roadmap gate: {GATE} roadmap"
    return _seal_failed("roadmap", "roadmap", "the roadmap")


def _current_phase(data: Item) -> Item | None:
    """The first phase that is not done (phases are worked one at a time)."""
    phases: list[Item] = data["phases"]
    for phase in phases:
        subs = _subs(data, phase["id"])
        if not subs or not all(_complete(s, phase["id"]) for s in subs):
            return phase
        if not _harness.phase_closed(str(phase["id"])):
            return phase
    return None


def _unbroken_next(data: Item, pid: str, flow: str) -> str | None:
    """The breakdown step of a phase not broken down yet, or None."""
    phase = next(p for p in data["phases"] if p["id"] == pid)
    if flow == _harness.TASK_FLOW and not phase["tasks"]:
        return (
            f"break down phase {pid} into tasks (tasks only: titles, goals, layers, order; "
            "no subtask specs), then phase gate"
        )
    if flow != _harness.TASK_FLOW and not _subs(data, pid):
        return f"break down phase {pid} into tasks and subtask specs"
    return None


def _breakdown_next(data: Item, pid: str) -> str:
    flow = _harness.phase_flow(pid)
    if unbroken := _unbroken_next(data, pid, flow):
        return unbroken
    verdict = _verdict(_harness.phase_review(pid))
    if verdict != "PHASE_APPROVED" and (capped := _capped("phase", pid)):
        return capped
    if verdict == "CHANGES_REQUESTED":
        return f"fix the breakdown of phase {pid} ({_harness.phase_review(pid)}), then phase gate"
    if verdict != "PHASE_APPROVED":
        return f"phase gate: {GATE} phase {pid}"
    if seal := _seal_failed("phase", pid, f"phase {pid}"):
        return seal
    task_flow = flow == _harness.TASK_FLOW or not _harness.phase_exempt(pid)
    return _task_flow_next(data, pid) if task_flow else _phase_next(data, pid)


def next_step(data: Item, queue: list[Item] | None = None) -> str:
    early = _resume_next(data) or _stack_next() or _lane_next(data, queue) or _roadmap_next(data)
    if early:
        return early
    phase = _current_phase(data)
    return _breakdown_next(data, phase["id"]) if phase else "all planned work is done"


def main() -> int:
    if not _harness.FEATURES_FILE.exists():
        print("No feature_list.json (run: make plan)", file=sys.stderr)
        return 1
    data = _harness.plan_data()
    data.setdefault("phases", [])
    data.setdefault("features", [])
    if data["phases"]:
        lines = [*_tree(data), "─" * 60, _summary(data)]
    else:
        lines = [*_stack_lines(), "No phases planned yet."]
    # Only a project that uses the issues lane (docs/plan/issues/, shipped with new
    # projects and added by --migrate) asks GitHub: the others stay offline as before.
    lane = (_harness.FEATURES_FILE.parent / "docs/plan/issues").is_dir()
    queue = github_queue() if lane else None
    lines += _issues_tree(data) + (_queue_lines(data, queue) if lane else [])
    lines += ["", paint(f"Next: {next_step(data, queue)}", "SKIP", OUT)]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
