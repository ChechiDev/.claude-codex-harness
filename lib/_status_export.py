#!/usr/bin/env python3
"""`make status-export`: a read-only, static export of the harness status (issue #47).

Per subtask: its status (blocked included), the plan and code gate verdicts with
links to their evidence in progress/, the code-gate attempts recorded under
.git/harness-gate-runs/ (check result, log tail, HEAD, time) and their timings.

  _status_export.py [--format html|json] [--out FILE] [--transcripts DIR]

HTML (default) is one static page: no scripts, forms or buttons, every imported
text (titles, log tails) escaped. It writes only to stdout or --out: never to
harness state, so it decides nothing and gates nothing.

Cost (optional, --transcripts): input/output tokens summed from local JSONL
transcripts whose lines carry `gitBranch` equal to the subtask's branch and a
`message.usage`. The transcript format is undocumented: anything not understood
is skipped, and a subtask with no matching usage shows cost "unknown".
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True  # read only: not even __pycache__ in the toolkit

import _config  # noqa: E402
import _harness  # noqa: E402

Item = dict[str, Any]
SCHEMA = 1
UNKNOWN = "unknown"
_ATTEMPT_FIELDS: dict[str, type] = {
    "check_rc": int,
    "log_tail": str,
    "head": str,
    "created_at": str,
}


# ── gate evidence ───────────────────────────────────────────────────────────


def _gate(path: Path, warnings: list[str]) -> Item:
    try:
        verdict = _harness.read_verdict(path)
    except ValueError as exc:  # undecodable evidence: report it, keep the rest
        warnings.append(f"unreadable gate evidence {path.as_posix()}: {exc}")
        verdict = ""
    return {"verdict": verdict, "evidence": path.as_posix() if path.is_file() else None}


def _runs_dir() -> Path | None:
    res = subprocess.run(
        ["git", "rev-parse", "--git-path", "harness-gate-runs"],
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return None
    path = Path(res.stdout.strip())
    return path if path.is_dir() else None


def _load_attempt(path: Path) -> Item:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    out: Item = {}
    for name, typ in _ATTEMPT_FIELDS.items():
        val = raw.get(name)
        if type(val) is not typ:
            raise ValueError(f"field {name!r}")
        out[name] = val
    return out


def _attempts(runs: Path | None, sid: str) -> tuple[list[Item], bool, list[str]]:
    """(attempts sorted by time, an approved attempt exists, warnings)."""
    if runs is None:
        return [], False, []
    found: list[Item] = []
    approved, warnings = False, []
    for path in sorted(runs.glob(f"code.*_{sid}.json")):
        tag = path.name[len("code.") : -len(f"_{sid}.json")]
        if not (tag.isdigit() or tag == "approved"):
            continue
        try:
            rec = _load_attempt(path)
        except (OSError, ValueError) as exc:
            warnings.append(f"unreadable gate-run record {path.name}: {exc}")
            continue
        approved = approved or tag == "approved"
        found.append({"tag": tag, **rec})
    found.sort(key=lambda a: str(a["created_at"]))
    return found, approved, warnings


# ── cost (optional, best effort) ────────────────────────────────────────────


def _transcript_lines(tdir: Path) -> Iterator[Item]:
    for path in sorted(tdir.rglob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def _usage(obj: Item) -> tuple[str, int, int] | None:
    branch, msg = obj.get("gitBranch"), obj.get("message")
    if not isinstance(branch, str) or not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
    if type(tin) is not int or type(tout) is not int:
        return None
    return branch, tin, tout


def branch_costs(tdir: Path | None) -> dict[str, tuple[int, int]]:
    costs: dict[str, tuple[int, int]] = {}
    if tdir is None or not tdir.is_dir():
        return costs
    for obj in _transcript_lines(tdir):
        found = _usage(obj)
        if found is None:
            continue
        branch, tin, tout = found
        a, b = costs.get(branch, (0, 0))
        costs[branch] = (a + tin, b + tout)
    return costs


def _branch(leaf: Item, cfg: _config.Config | None) -> str | None:
    owner = str(leaf["issue"]) or _harness.parse_id(str(leaf["id"]))[1]
    try:
        if leaf["issue"]:
            return _harness.issue_branch(owner)
        return None if cfg is None else _harness.task_branch(owner, cfg)
    except ValueError:
        return None


def _cost(branch: str | None, costs: dict[str, tuple[int, int]]) -> Item | str:
    if branch is None or branch not in costs:
        return UNKNOWN
    tin, tout = costs[branch]
    return {"input_tokens": tin, "output_tokens": tout}


# ── model ───────────────────────────────────────────────────────────────────


def build(transcripts: Path | None) -> Item:
    data = _harness.plan_data()
    runs = _runs_dir()
    costs = branch_costs(transcripts)
    try:
        cfg: _config.Config | None = _config.load(Path.cwd())
    except _config.ConfigError:
        cfg = None
    out: list[Item] = []
    for leaf in _harness.leaves(data):
        sid = str(leaf["id"])
        attempts, approved, warnings = _attempts(runs, sid)
        gates = {
            "plan": _gate(Path(f"progress/plan_review_{sid}.md"), warnings),
            "code": _gate(Path(f"progress/review_{sid}.md"), warnings),
        }
        times = [str(a["created_at"]) for a in attempts]
        out.append(
            {
                "id": sid,
                "title": leaf["name"],
                "status": leaf["status"],
                "phase": leaf["phase"],
                "issue": leaf["issue"],
                "gates": gates,
                "attempts": attempts,
                "approved": approved,
                "timing": {
                    "first_attempt": times[0] if times else None,
                    "last_attempt": times[-1] if times else None,
                },
                "cost": _cost(_branch(leaf, cfg), costs),
                "warnings": warnings,
            }
        )
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "leaves": out,
    }


# ── HTML view ───────────────────────────────────────────────────────────────

_e = html.escape

_CSS = (
    "body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}"
    "td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}"
    "pre{white-space:pre-wrap;max-width:60em;margin:0}"
)


def _gate_cell(gate: Item, base: Path | None) -> str:
    verdict = _e(gate["verdict"] or "—")
    if gate["evidence"]:
        href = str(gate["evidence"])
        if base is not None:  # links resolve against the page, not the project root
            href = Path(os.path.relpath(Path.cwd() / href, base)).as_posix()
        return f'<a href="{_e(href)}">{verdict}</a>'
    return verdict


def _cost_cell(cost: Item | str) -> str:
    if isinstance(cost, str):
        return _e(cost)
    return f"in {cost['input_tokens']} · out {cost['output_tokens']}"


def _attempts_cell(leaf: Item) -> str:
    parts = []
    for a in leaf["attempts"]:
        head = f"#{_e(a['tag'])} rc={a['check_rc']} {_e(a['created_at'])} {_e(a['head'][:12])}"
        tail = f"<details><summary>log</summary><pre>{_e(a['log_tail'])}</pre></details>"
        parts.append(f"<div>{head}{tail}</div>")
    parts += [f"<div>[WARN] {_e(w)}</div>" for w in leaf["warnings"]]
    return "".join(parts) or "—"


def render_html(model: Item, base: Path | None = None) -> str:
    rows = []
    for leaf in model["leaves"]:
        rows.append(
            "<tr>"
            f"<td>{_e(leaf['id'])}</td><td>{_e(leaf['title'])}</td>"
            f"<td>{_e(leaf['status'])}</td>"
            f"<td>{_gate_cell(leaf['gates']['plan'], base)}</td>"
            f"<td>{_gate_cell(leaf['gates']['code'], base)}</td>"
            f"<td>{'yes' if leaf['approved'] else 'no'}</td>"
            f"<td>{_e(leaf['timing']['first_attempt'] or '—')}</td>"
            f"<td>{_e(leaf['timing']['last_attempt'] or '—')}</td>"
            f"<td>{_attempts_cell(leaf)}</td>"
            f"<td>{_cost_cell(leaf['cost'])}</td>"
            "</tr>"
        )
    head = "".join(
        f"<th>{h}</th>"
        for h in (
            "Subtask",
            "Title",
            "Status",
            "Plan gate",
            "Code gate",
            "Approved",
            "First attempt",
            "Last attempt",
            "Attempts",
            "Cost (tokens)",
        )
    )
    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'\">"
        f"<title>Harness status</title><style>{_CSS}</style></head><body>"
        f"<h1>Harness status</h1><p>Read-only export · generated {_e(model['generated_at'])}</p>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        "</body></html>\n"
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

# Harness state an export must never overwrite (relative to the project root).
PROTECTED_FILES = ("feature_list.json", "harness.toml")
PROTECTED_DIRS = ("progress", "docs", "harness", ".git")
TOOLKIT = Path(__file__).resolve().parent.parent


def _git_dirs() -> list[Path]:
    """The repo's git dir and common dir (outside the tree in a linked worktree)."""
    res = subprocess.run(
        ["git", "rev-parse", "--git-dir", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        return []
    return [Path(line).resolve() for line in res.stdout.splitlines() if line.strip()]


def out_refusal(out: Path) -> str | None:
    """Why `out` may not be written, or None. Aliases (symlinks, hard links) are
    refused outright: their real target cannot be told from the path."""
    if out.is_symlink():
        return f"{out} is a symlink"
    if out.exists() and (not out.is_file() or out.stat().st_nlink > 1):
        return f"{out} is not a plain file with a single link"
    target = out.resolve()
    if target.is_relative_to(TOOLKIT):
        return f"{out} is inside the harness toolkit"
    if any(target.is_relative_to(d) for d in _git_dirs()):
        return f"{out} is inside the git dir"
    # Both the resolved and the lexical path: harness/ is a symlink to the toolkit.
    for root, path in (
        (Path.cwd().resolve(), target),
        (Path(os.path.abspath(Path.cwd())), Path(os.path.abspath(out))),
    ):
        if not path.is_relative_to(root):
            continue
        rel = path.relative_to(root)
        if rel.as_posix() in PROTECTED_FILES or (rel.parts and rel.parts[0] in PROTECTED_DIRS):
            return f"{out} is harness state"
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="_status_export.py", description="read-only status export")
    ap.add_argument("--format", choices=("html", "json"), default="html")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--transcripts", type=Path)
    args = ap.parse_args(argv)
    refusal = None if args.out is None else out_refusal(args.out)
    if refusal is not None:
        print(f"[FAIL] status export: --out refused: {refusal}", file=sys.stderr)
        return 2
    try:
        model = build(args.transcripts)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[FAIL] status export: {exc} (run `make plan` first?)", file=sys.stderr)
        return 1
    base = None if args.out is None else args.out.resolve().parent
    text = json.dumps(model, indent=2) + "\n" if args.format == "json" else render_html(model, base)
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
