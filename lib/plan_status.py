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
import _harness
import _pr
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
    return f"{head} · {state} · {done}/{len(subs)} done"


def _sub_line(sub: Item) -> str:
    label, kind = TAGS.get(sub["status"], ("[?]", None))
    line = f"    {paint(label, kind, OUT)}{' ' * (8 - len(label))}{sub['id']} {sub['title']}"
    if sub["status"] == "in_progress":
        plan = _verdict(Path(f"progress/plan_review_{sub['id']}.md")) or "—"
        code = _verdict(Path(f"progress/review_{sub['id']}.md")) or "—"
        line += f" · plan: {plan} · code: {code}"
    return line


def _tree(data: Item) -> list[str]:
    lines = [f"Roadmap    {_gate_state(_harness.ROADMAP_REVIEW, 'ROADMAP_APPROVED')}", ""]
    for phase in data["phases"]:
        lines.append(_phase_line(data, phase))
        for task in (t for t in data["features"] if t["phase"] == phase["id"]):
            lines.append(f"  {task['id']} {task['title']} · {task['status'].replace('_', ' ')}")
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
    return None


def _issue_next(data: Item, issue: Item) -> str | None:
    iid, subs = issue["id"], issue["subtasks"]
    if not subs:
        return f"break down issue {iid} into subtask specs"
    current = next((s for s in subs if s["status"] == "in_progress"), None)
    if current:
        return _leaf_next(current["id"])
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
    """`needs human (N rounds) ...` once a gate hit [review] max_rounds (issue #42)."""
    try:
        return _pr.needs_human(gate, fid) if _pr.capped(gate, fid) else None
    except _config.ConfigError as exc:
        return f"fix {_config.CONFIG_FILE}: {exc}"


def _leaf_next(sid: str) -> str:
    plan = _verdict(Path(f"progress/plan_review_{sid}.md"))
    code = _verdict(Path(f"progress/review_{sid}.md"))
    gate = ("plan", plan) if plan != "PLAN_APPROVED" else ("code", code)
    if gate[1] not in ("PLAN_APPROVED", "APPROVED") and (capped := _capped(gate[0], sid)):
        return capped
    if plan == "CHANGES_REQUESTED":
        return f"fix the spec of {sid} (progress/plan_review_{sid}.md), then plan gate"
    if plan != "PLAN_APPROVED":
        return f"plan gate: {GATE} plan {sid}"
    if code == "CHANGES_REQUESTED":
        return f"fix {sid} (progress/review_{sid}.md), then code gate"
    if code != "APPROVED":
        return f"implement {sid} (implementer), then code gate: {GATE} code {sid}"
    return f"close: {GATE} doc {sid}"


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
    return None


def _current_phase(data: Item) -> Item | None:
    """The first phase that is not done (phases are worked one at a time)."""
    phases: list[Item] = data["phases"]
    for phase in phases:
        subs = _subs(data, phase["id"])
        if not subs or not all(_complete(s, phase["id"]) for s in subs):
            return phase
    return None


def _breakdown_next(data: Item, pid: str) -> str:
    if not _subs(data, pid):
        return f"break down phase {pid} into tasks and subtask specs"
    verdict = _verdict(_harness.phase_review(pid))
    if verdict != "PHASE_APPROVED" and (capped := _capped("phase", pid)):
        return capped
    if verdict == "CHANGES_REQUESTED":
        return f"fix the breakdown of phase {pid} ({_harness.phase_review(pid)}), then phase gate"
    if verdict != "PHASE_APPROVED":
        return f"phase gate: {GATE} phase {pid}"
    return _phase_next(data, pid)


def next_step(data: Item, queue: list[Item] | None = None) -> str:
    early = _lane_next(data, queue) or _roadmap_next(data)
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
        lines = ["No phases planned yet."]
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
