#!/usr/bin/env python3
"""Generate feature_list.json and the PLAN.md index from docs/plan/ (source of truth).

Layout (the leader writes it; Codex reviews it):

  docs/plan/PLAN.md                               vision, goals, scope, architecture
                                                  + a generated index (between markers)
  docs/plan/phase-NN-<slug>.md                    phase title + goal + its tasks (## N.M)
  docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md     one spec per subtask (micro-task)

Status is written ONLY in the subtask specs. Task and phase status, feature_list.json
and the PLAN.md index are derived here — never hand-edit them. `baseline` marks
work that existed before the harness: it derives like `done` (see _harness.py).

Usage:
  python3 sync_plan.py            write feature_list.json + the PLAN.md index
  python3 sync_plan.py --check    exit 1 if either is stale (writes nothing)

Phase file (docs/plan/phase-01-users.md):

  # Phase 01 — Users
  - goal: <one sentence>

  ## 1.1 <task title>
  - ticket: GUAK-042                   (optional; required if harness.toml [git] uses {ticket})
  - layer: domain
  - goal: <one sentence>

Subtask spec (docs/plan/phase-01-users/1.1.1-email-value-object.md):

  # 1.1.1 Email value object
  - status: pending                    (pending | in_progress | done | blocked | baseline)
  - layer: domain
  - depends: none                      (or: 1.1.2, 1.2.1)
  - files:
    - src/app/domain/email.py          # crea: Email VO (comment optional)
  - acceptance:
    - AC1 | <observable criterion> | tests/acceptance/test_1.1.1.py::test_ac1_<slug>

  ## Description
  <free text: not parsed>
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from _status import say

PLAN_DIR = Path("docs/plan")
PLAN_MD = PLAN_DIR / "PLAN.md"
OUT_FILE = Path("feature_list.json")
INDEX_START = "<!-- plan:index:start -->"
INDEX_END = "<!-- plan:index:end -->"

RE_PHASE_FILE = re.compile(r"^phase-(\d{2})-([a-z0-9][a-z0-9-]*)\.md$")
RE_PHASE_DIR = re.compile(r"^phase-\d{2}-[a-z0-9][a-z0-9-]*$")
RE_SPEC_FILE = re.compile(r"^(\d+\.\d+\.\d+)-[a-z0-9][a-z0-9-]*\.md$")
RE_PHASE_HEADING = re.compile(r"^#\s+Phase\s+(\d+)\s*[—-]\s*(.+?)\s*$")
RE_TASK = re.compile(r"^##\s+(\d+)\.(\d+)\s+(.+?)\s*$")
RE_SPEC_HEADING = re.compile(r"^#\s+(\d+\.\d+\.\d+)\s+(.+?)\s*$")
RE_META = re.compile(r"^-\s+(\w+):\s*(.*?)\s*$")
RE_AC = re.compile(r"^\s+-\s+(AC\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*$")
RE_ITEM = re.compile(r"^\s+-\s+(\S.*)$")
VALID_STATUS = ("pending", "in_progress", "done", "blocked", "baseline")
COMPLETE = ("done", "baseline")
LIST_KEYS = ("files", "acceptance")

Item = dict[str, Any]


def _id_key(item_id: str) -> tuple[int, ...]:
    return tuple(int(part) for part in item_id.split("."))


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """(heading, body lines) per '#'/'##' heading; text before the first heading is dropped."""
    sections: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        if raw.startswith("#"):
            sections.append((raw, []))
        elif sections:
            sections[-1][1].append(raw)
    return sections


def _meta(body: Iterable[str]) -> Item:
    """`- key: value` lines; `files:` / `acceptance:` open an indented list block."""
    meta: Item = {}
    block: str | None = None
    for raw in body:
        m = RE_META.match(raw)
        if m:
            key, val = m.group(1).lower(), m.group(2)
            block = key if key in LIST_KEYS and val == "" else None
            meta.setdefault(key, [] if block else val)
            continue
        if block is None or not raw.strip():
            continue
        if not raw[:1].isspace():
            block = None  # a non-indented line ends the block
        else:
            meta[block].append(raw)
    return meta


def _files(lines: list[str]) -> list[str]:
    # "src/x.py  # crea: ..." -> "src/x.py"
    out = []
    for raw in lines:
        m = RE_ITEM.match(raw)
        if m:
            out.append(m.group(1).split("#", 1)[0].strip())
    return out


def _acceptance(lines: list[str]) -> list[Item]:
    out = []
    for raw in lines:
        m = RE_AC.match(raw)
        if m:
            out.append({"id": m.group(1), "criterion": m.group(2), "test": m.group(3)})
    return out


def _depends(value: str) -> list[str]:
    if value.strip().lower() in ("", "none", "-"):
        return []
    return [d for d in re.split(r"[,\s]+", value.strip()) if d]


def _derive(statuses: list[str]) -> str:
    """Task/phase status from its subtasks' statuses."""
    if not statuses:
        return "pending"
    if all(s in COMPLETE for s in statuses):
        return "done"
    if any(s in ("in_progress", *COMPLETE) for s in statuses):
        return "in_progress"
    if "blocked" in statuses:
        return "blocked"
    return "pending"


class PlanParser:
    """Reads docs/plan/ into phases + tasks (with nested subtasks), collecting errors."""

    def __init__(self, plan_dir: Path) -> None:
        self.plan_dir = plan_dir
        self.errors: list[str] = []
        self.phases: list[Item] = []
        self.tasks: list[Item] = []

    def parse(self) -> None:
        phase_files = []
        for path in sorted(self.plan_dir.iterdir()):
            m = RE_PHASE_FILE.match(path.name)
            if m and path.is_file():
                phase_files.append((m.group(1), path))
            elif (
                path.is_dir()
                and RE_PHASE_DIR.match(path.name)
                and not path.with_suffix(".md").is_file()
            ):
                self.errors.append(
                    f"{path.name}/: spec folder without {self.plan_dir}/{path.name}.md"
                )
        for pid, path in phase_files:
            self._phase(pid, path)
        self._check_ids()

    def _phase(self, pid: str, path: Path) -> None:
        sections = _sections(path.read_text(encoding="utf-8"))
        head = RE_PHASE_HEADING.match(sections[0][0]) if sections else None
        if head is None:
            self.errors.append(f"{path.name}: first line must be '# Phase {pid} — <title>'")
            return
        if int(head.group(1)) != int(pid):
            self.errors.append(f"{path.name}: heading says phase {int(head.group(1)):02d}")
        phase: Item = {
            "id": pid,
            "title": head.group(2),
            "goal": _meta(sections[0][1]).get("goal", ""),
            "file": str(path),
            "status": "pending",
            "tasks": [],
        }
        tasks = [self._task(pid, heading, body) for heading, body in sections[1:]]
        by_id = {t["id"]: t for t in tasks if t is not None}
        self._specs(pid, path.with_suffix(""), by_id)
        statuses = []
        for task in by_id.values():
            task["status"] = _derive([s["status"] for s in task["subtasks"]])
            statuses += [s["status"] for s in task["subtasks"]]
            phase["tasks"].append(task["id"])
            self.tasks.append(task)
        phase["status"] = _derive(statuses)
        self.phases.append(phase)

    def _task(self, pid: str, heading: str, body: list[str]) -> Item | None:
        m = RE_TASK.match(heading)
        if m is None:
            return None
        tid = f"{m.group(1)}.{m.group(2)}"
        if int(m.group(1)) != int(pid):
            self.errors.append(f"task {tid} belongs to phase {int(m.group(1)):02d}, not {pid}")
        meta = _meta(body)
        task: Item = {
            "id": tid,
            "title": m.group(3),
            "phase": pid,
            "layer": meta.get("layer", ""),
            "goal": meta.get("goal", ""),
            "status": "pending",
            "subtasks": [],
        }
        if meta.get("ticket"):  # only when set: older feature_list.json files stay in sync
            task["ticket"] = meta["ticket"]
        return task

    def _specs(self, pid: str, folder: Path, tasks: dict[str, Item]) -> None:
        if not folder.is_dir():
            return
        specs = []
        for path in folder.iterdir():
            m = RE_SPEC_FILE.match(path.name)
            if m is None:
                self.errors.append(f"{folder.name}/{path.name}: not a <N.M.K>-<slug>.md spec")
                continue
            specs.append((m.group(1), path))
        for sid, path in sorted(specs, key=lambda s: _id_key(s[0])):
            sub = self._spec(sid, path)
            if sub is None:
                continue
            task = tasks.get(sid.rsplit(".", 1)[0])
            if task is None:
                tid = sid.rsplit(".", 1)[0]
                self.errors.append(f"{sid}: task {tid} does not exist in phase {pid}")
                continue
            task["subtasks"].append(sub)

    def _spec(self, sid: str, path: Path) -> Item | None:
        sections = _sections(path.read_text(encoding="utf-8"))
        head = RE_SPEC_HEADING.match(sections[0][0]) if sections else None
        if head is None or head.group(1) != sid:
            found = head.group(1) if head else "none"
            self.errors.append(f"{path.name}: heading id {found} does not match the file name")
            return None
        meta = _meta(sections[0][1])
        status = str(meta.get("status", "pending")).strip().lower().replace(" ", "_")
        if status not in VALID_STATUS:
            self.errors.append(f"{sid}: invalid status '{meta.get('status')}'")
        return {
            "id": sid,
            "title": head.group(2),
            "status": status,
            "layer": meta.get("layer", ""),
            "depends": _depends(str(meta.get("depends", ""))),
            "files": _files(meta.get("files", [])),
            "acceptance": _acceptance(meta.get("acceptance", [])),
            "spec": str(path),
        }

    def _check_ids(self) -> None:
        subs = [s for t in self.tasks for s in t["subtasks"]]
        ids = [s["id"] for s in subs]
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            self.errors.append(f"{dup}: duplicate subtask id")
        for sub in subs:
            for dep in sub["depends"]:
                if dep not in ids:
                    self.errors.append(f"{sub['id']}: depends on unknown subtask {dep}")


def _project_name() -> str:
    try:
        name = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("project", "")
    except (OSError, ValueError):
        name = ""
    return name if name and "‹" not in name else Path.cwd().name


def build(parser: PlanParser) -> Item:
    return {
        "project": _project_name(),
        "generated_from": "docs/plan/ (do not hand-edit; run make plan)",
        "rules": {
            "one_subtask_at_a_time": True,
            "phase_by_phase": True,
            "require_roadmap_approval": True,
            "require_phase_approval": True,
            "require_plan_approval": True,
            "require_code_approval": True,
            "require_make_check_to_close": True,
            "valid_status": list(VALID_STATUS),
        },
        "phases": parser.phases,
        "features": parser.tasks,
    }


# ── PLAN.md index ────────────────────────────────────────────────────────────

MARK = {"done": "[x]", "baseline": "[x]", "in_progress": "[~]", "blocked": "[!]", "pending": "[ ]"}
SUFFIX = {"in_progress": " — in progress", "blocked": " — blocked", "baseline": " — baseline"}


def _subtask_line(sub: Item) -> str:
    spec = Path(sub["spec"])
    rel_spec = spec.relative_to(PLAN_DIR).as_posix()
    line = f"  - {MARK[sub['status']]} [{sub['id']} {sub['title']}]({rel_spec})"
    if sub["status"] == "done":
        sid = sub["id"]
        line += (
            f" · [plan review](../../progress/plan_review_{sid}.md)"
            f" · [code review](../../progress/review_{sid}.md)"
            f" · [reference](../reference/{spec.name})"
        )
    return line + SUFFIX.get(sub["status"], "")


def _phase_block(phase: Item, tasks: list[Item]) -> list[str]:
    subs = [s for t in tasks for s in t["subtasks"]]
    done = sum(s["status"] in COMPLETE for s in subs)
    progress = f"{done}/{len(subs)} done" if tasks else "not broken down yet"
    lines = [f"### Phase {phase['id']} — {phase['title']} · {progress}"]
    if phase["goal"]:
        lines.append(phase["goal"])
    lines.append("")
    for task in tasks:
        lines.append(f"- **{task['id']} {task['title']}** — {task['status'].replace('_', ' ')}")
        lines += [_subtask_line(s) for s in task["subtasks"]]
    if tasks:
        lines.append("")
    return lines


def render_index(data: Item) -> str:
    lines = [
        INDEX_START,
        "<!-- generated by `make plan` from docs/plan/ — do not edit by hand -->",
        "## Plan index",
        "",
    ]
    subs = [s for t in data["features"] for s in t["subtasks"]]
    if not data["phases"]:
        lines += ["No phases planned yet.", ""]
    else:
        count = {st: sum(s["status"] == st for s in subs) for st in VALID_STATUS}
        done = count["done"] + count["baseline"]
        base = f" ({count['baseline']} baseline)" if count["baseline"] else ""
        lines += [
            f"{done} of {len(subs)} subtasks done{base} · {count['in_progress']} in progress"
            f" · {count['pending']} pending · {count['blocked']} blocked",
            "",
        ]
    for phase in data["phases"]:
        tasks = [t for t in data["features"] if t["phase"] == phase["id"]]
        lines += _phase_block(phase, tasks)
    lines.append(INDEX_END)
    return "\n".join(lines)


def with_index(text: str, index: str) -> str:
    start, end = text.find(INDEX_START), text.find(INDEX_END)
    if start != -1 and end > start:
        return text[:start] + index + text[end + len(INDEX_END) :]
    return text.rstrip("\n") + "\n\n" + index + "\n"


# ── entry point ──────────────────────────────────────────────────────────────


def _outputs(data: Item) -> dict[Path, str]:
    outputs = {OUT_FILE: json.dumps(data, ensure_ascii=False, indent=2) + "\n"}
    if PLAN_MD.is_file():
        outputs[PLAN_MD] = with_index(PLAN_MD.read_text(encoding="utf-8"), render_index(data))
    return outputs


def main(argv: list[str]) -> int:
    if not PLAN_DIR.is_dir():
        say("FAIL", f"{PLAN_DIR} does not exist (is this a harness project?)", sys.stderr)
        return 1
    parser = PlanParser(PLAN_DIR)
    parser.parse()
    if parser.errors:
        for error in parser.errors:
            say("FAIL", f"docs/plan: {error}")
        return 1
    data = build(parser)
    outputs = _outputs(data)

    if "--check" in argv:
        stale = [p for p, text in outputs.items() if _read(p) != text]
        for path in stale:
            name = "PLAN.md index" if path == PLAN_MD else str(path)
            say("FAIL", f"{name} is stale vs docs/plan/ — run: make plan")
        if not stale:
            say("OK", "feature_list.json and the PLAN.md index are in sync with docs/plan/")
        return 1 if stale else 0

    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8")
    subs = sum(len(t["subtasks"]) for t in data["features"])
    say(
        "OK",
        f"plan synced: {len(data['phases'])} phases · {len(data['features'])} tasks"
        f" · {subs} subtasks",
    )
    return 0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
