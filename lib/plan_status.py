#!/usr/bin/env python3
"""`make status`: the whole plan at a glance — what is planned, done, in progress,
pending or blocked, every Codex gate verdict, and the next step to take.

Read-only and live: it reads feature_list.json (run `make plan` after editing
docs/plan/) and the verdict files in progress/ as they are now.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import _harness
from _status import Kind, paint

Item = dict[str, Any]
OUT = sys.stdout
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


# ── next step ────────────────────────────────────────────────────────────────


def _leaf_next(sid: str) -> str:
    plan = _verdict(Path(f"progress/plan_review_{sid}.md"))
    code = _verdict(Path(f"progress/review_{sid}.md"))
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
    if verdict == "CHANGES_REQUESTED":
        return f"fix the breakdown of phase {pid} ({_harness.phase_review(pid)}), then phase gate"
    if verdict != "PHASE_APPROVED":
        return f"phase gate: {GATE} phase {pid}"
    return _phase_next(data, pid)


def next_step(data: Item) -> str:
    early = _roadmap_next(data)
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
    if data["phases"]:
        lines = [*_tree(data), "─" * 60, _summary(data)]
    else:
        lines = ["No phases planned yet."]
    lines += ["", paint(f"Next: {next_step(data)}", "SKIP", OUT)]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
