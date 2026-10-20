#!/usr/bin/env python3
"""Generate feature_list.json (derived, read-only index) from docs/plan/phase-*.md.

Under model A2, docs/plan/ is the single source of truth for the plan and the
status. This script mirrors it into feature_list.json so the harness scripts can
read JSON without parsing markdown.

DO NOT hand-edit feature_list.json. Edit docs/plan/phase-*.md and regenerate.

Usage:
  python3 sync_plan.py            # write feature_list.json from docs/plan/
  python3 sync_plan.py --check    # exit != 0 if feature_list.json is stale

Phase file format (docs/plan/phase-NN-<name>.md):

  # Phase 01 — <phase name>

  ## 1.1 <task title>
  - status: pending
  - layer: application
  - description: <one sentence>
  - acceptance:
    - AC1 | <observable criterion> | tests/acceptance/test_1.1.py::test_ac1_<slug>
    - AC2 | <observable criterion> | tests/acceptance/test_1.1.py::test_ac2_<slug>

  ### 1.1.1 <subtask title>
  - status: pending
  - layer: application
  - files:
    - src/domain/value_objects/email.py   # crea: clase Email (inline comment opcional)
    - src/domain/value_objects/__init__.py # modifica: export
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLAN_DIR = Path("docs/plan")
OUT_FILE = Path("feature_list.json")

RE_TASK = re.compile(r"^##\s+(\d+\.\d+)\s+(.*)$")
RE_SUBTASK = re.compile(r"^###\s+(\d+\.\d+\.\d+)\s+(.*)$")
RE_META = re.compile(r"^-\s+(\w+):\s*(.*)$")
RE_AC = re.compile(r"^\s*-\s+(AC\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*$")
RE_LIST_ITEM = re.compile(r"^\s+-\s+(\S.*)$")  # indented list item (files block)
VALID_STATUS = {"pending", "in_progress", "done", "blocked"}


def _norm_status(value: str) -> str:
    v = value.strip().lower().replace(" ", "_")
    return v if v in VALID_STATUS else "pending"


def parse_phase(path: Path) -> list[dict]:
    """Parse one phase file into a list of task dicts (with nested subtasks)."""
    tasks: list[dict] = []
    task: dict | None = None
    sub: dict | None = None
    in_acceptance = False
    in_files = False
    files_target: dict | None = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        m = RE_TASK.match(raw)
        if m:
            task = {"id": m.group(1), "title": m.group(2).strip(), "layer": "",
                    "description": "", "acceptance": [], "files": [],
                    "subtasks": [], "status": "pending"}
            tasks.append(task)
            sub = None
            in_acceptance = in_files = False
            continue

        m = RE_SUBTASK.match(raw)
        if m and task is not None:
            sub = {"id": m.group(1), "title": m.group(2).strip(), "layer": "",
                   "files": [], "status": "pending"}
            task["subtasks"].append(sub)
            in_acceptance = in_files = False
            continue

        if in_files:
            fm = RE_LIST_ITEM.match(raw)
            if fm and files_target is not None:
                # Strip optional inline comment: "src/x.py  # crea: ..." -> "src/x.py"
                files_target["files"].append(fm.group(1).split("#", 1)[0].strip())
                continue
            if raw.strip():
                in_files = False  # block ended

        if in_acceptance:
            m = RE_AC.match(raw)
            if m and task is not None:
                task["acceptance"].append({"id": m.group(1), "criterion": m.group(2), "test": m.group(3)})
                continue
            if raw.strip() and not raw.lstrip().startswith("- "):
                in_acceptance = False  # block ended

        m = RE_META.match(raw)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            target = sub if sub is not None else task
            if target is None:
                continue
            if key == "acceptance" and val == "":
                in_acceptance, in_files = True, False
            elif key == "files" and val == "":
                in_files, in_acceptance = True, False
                files_target = target
                target.setdefault("files", [])
            elif key == "status":
                target["status"] = _norm_status(val)
            elif key in ("layer", "description", "title"):
                target[key] = val

    return tasks


def build() -> dict:
    project = "‹project›"
    if OUT_FILE.exists():
        try:
            project = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("project", project)
        except Exception:
            pass

    features: list[dict] = []
    for path in sorted(PLAN_DIR.glob("phase-*.md")):
        features.extend(parse_phase(path))

    return {
        "project": project,
        "generated_from": "docs/plan/ (do not hand-edit; run harness/sync_plan.py)",
        "rules": {
            "one_feature_at_a_time": True,
            "require_plan_approval": True,
            "require_code_approval": True,
            "require_make_check_to_close": True,
            "valid_status": sorted(VALID_STATUS),
        },
        "features": features,
    }


def main(argv: list[str]) -> int:
    if not PLAN_DIR.is_dir():
        print(f"[FAIL]  {PLAN_DIR} does not exist (is this a harness project?)", file=sys.stderr)
        return 1
    data = build()
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    if "--check" in argv:
        current = OUT_FILE.read_text(encoding="utf-8") if OUT_FILE.exists() else ""
        if current != rendered:
            print("[FAIL]  feature_list.json is stale vs docs/plan/ — run: make plan", file=sys.stderr)
            return 1
        print("[OK]    feature_list.json is in sync with docs/plan/")
        return 0

    OUT_FILE.write_text(rendered, encoding="utf-8")
    print(f"[OK]    feature_list.json generated from docs/plan/ ({len(data['features'])} tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
