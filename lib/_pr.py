#!/usr/bin/env python3
"""Task pull request: review history, the PR body and when a task's PR is due.

A task N.M lives on one branch with one close commit per subtask. When its last
subtask closes, the harness pushes the branch and opens one PR for the task.
Codex writes the narrative (progress/pr_<N.M>.md) in that last close; this
module adds the mechanical parts (subtasks, files, branch) and renders the
template (harness.toml [git] pr_template, or pr_template.md next to this file).

Every gate verdict is also appended to progress/review_history/<target>.md, so
the rounds a gate rejected survive the next verdict that overwrites the file.

Usage: python3 _pr.py <command> [args]
Commands:
  record <gate> <id> <verdict-file>   append a gate round to the review history
  round-cap <gate> <id>   fail if the gate hit harness.toml [review] max_rounds on <id>
  last-of-task <N.M.K>    print the task id if every other subtask of it is complete
  pr-due <N.M.K>          print the task id if the task is complete and auto_pr is on
  title <N.M|ISS-n>       print the PR title
  body <N.M|ISS-n> <base> print the PR body against base branch <base>

An issue ISS-n (docs/plan/issues/) gets the same PR as a task, titled with its
type, linking the GitHub issue, and ending with `Closes #n`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import _config
import _harness
from _status import say

HISTORY_DIR = Path("progress/review_history")
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "pr_template.md"
# Narrative sections Codex writes in progress/pr_<N.M>.md, by heading.
NARRATIVE = {
    "overview": "overview",
    "approach": "approach",
    "decisions": "decisions",
    "issues resolved": "issues_resolved",
    "issues open": "issues_open",
    "follow-ups": "follow_ups",
    "branch notes": "branch_notes",
    "where to start reviewing": "review_order",
}
EMPTY = "Nothing recorded for this task."
# Not listed under Files: harness bookkeeping, harness files and the project's own
# docs (the task's spec folder goes under Documentation instead). README.md stays.
RE_BOOKKEEPING = re.compile(
    r"^(progress/|docs/|harness/|\.claude/"
    r"|(feature_list\.json|harness\.toml|AGENTS\.md|CLAUDE\.md|CHECKPOINTS\.md)$)"
    r"|\.sha256$"
)
STATUS = {"A": "Added", "M": "Modified", "D": "Deleted", "R": "Renamed", "C": "Copied"}
# A conventional-commit type already at the start of an issue's GitHub title
# ("refactor: ...", "fix(parsing): ...", "chore!: ..."): the PR title adds its own.
RE_TYPE_PREFIX = re.compile(
    r"^(feat|fix|docs|refactor|test|chore|perf|build|ci|style|revert)(\([^)]*\))?!?:\s*",
    re.IGNORECASE,
)


# ── review history ───────────────────────────────────────────────────────────


def history_file(gate: str, fid: str) -> Path:
    name = {"roadmap": "roadmap", "phase": f"phase-{fid}", "issue-draft": "issue-draft"}.get(
        gate, fid
    )
    return HISTORY_DIR / f"{name}.md"


def record(gate: str, fid: str, verdict_file: str) -> int:
    source = Path(verdict_file)
    if not source.is_file():
        say("FAIL", f"no verdict file to record: {verdict_file}", sys.stderr)
        return 1
    text = source.read_text(encoding="utf-8")
    path = history_file(gate, fid)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.is_file() else f"# Review history: {fid}\n"
    if gate == "phase":
        old = old.replace(f"# Review history: {fid}\n", f"# Review history: phase-{fid}\n", 1)
    rounds = len(re.findall(rf"^## Round \d+: {re.escape(gate)} gate,", old, re.MULTILINE))
    verdict = _harness.read_verdict(source) or "NO VERDICT"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    demoted = re.sub(r"^#", "###", text.strip(), flags=re.MULTILINE)
    entry = f"\n## Round {rounds + 1}: {gate} gate, {verdict} ({stamp})\n\n{demoted}\n"
    path.write_text(old.rstrip("\n") + "\n" + entry, encoding="utf-8")
    return 0


def rejected_rounds(gate: str, fid: str) -> int:
    """CHANGES_REQUESTED rounds of <gate> on <fid> since its last approval (or ever)."""
    path = history_file(gate, fid)
    if not path.is_file():
        return 0
    heads = re.findall(
        rf"^## Round \d+: {re.escape(gate)} gate, (\S+)", path.read_text(encoding="utf-8"), re.M
    )
    streak = 0
    for verdict in heads:
        if verdict == "CHANGES_REQUESTED":
            streak += 1
        elif verdict.endswith("APPROVED"):
            streak = 0
    return streak


def capped(gate: str, fid: str) -> int | None:
    """The cap when <gate> on <fid> reached it (the human must step in), else None.
    Raises ConfigError for an untrustworthy harness.toml."""
    cap = _config.load(Path.cwd()).max_rounds
    return cap if cap and rejected_rounds(gate, fid) >= cap else None


def needs_human(gate: str, fid: str) -> str:
    return (
        f"needs human ({rejected_rounds(gate, fid)} rounds): the {gate} gate on {fid} was rejected "
        f"that many times in a row ({history_file(gate, fid)}); the human decides, then "
        f"raises [review] max_rounds in {_config.CONFIG_FILE} (0 = no cap) to resume"
    )


def round_cap(gate: str, fid: str) -> int:
    try:
        if capped(gate, fid) is None:
            return 0
        say("FAIL", f"{needs_human(gate, fid)}; the review was not launched", sys.stderr)
    except _config.ConfigError as exc:
        say("FAIL", str(exc), sys.stderr)
    return 1


# ── task completion ──────────────────────────────────────────────────────────


def _task_of(sid: str) -> str:
    return sid.rsplit(".", 1)[0]


def _task_leaves(tid: str) -> list[_harness.Item]:
    return [leaf for leaf in _harness.leaves() if _task_of(str(leaf["id"])) == tid]


def last_of_task(sid: str) -> int:
    tid = _task_of(sid)
    others = [leaf for leaf in _task_leaves(tid) if str(leaf["id"]) != sid]
    if all(_harness.complete(leaf) for leaf in others):
        print(tid)
        return 0
    return 1


def pr_due(sid: str) -> int:
    try:
        cfg = _config.load(Path.cwd())
    except _config.ConfigError as exc:
        say("FAIL", str(exc), sys.stderr)
        return 1
    tid = _task_of(sid)
    leaves = _task_leaves(tid)
    if os.environ.get("HARNESS_AUTO_PR", "1") == "0" or not cfg.auto_pr or not leaves:
        return 1
    if not all(_harness.complete(leaf) for leaf in leaves):
        return 1
    print(tid)
    return 0


# ── title and body ───────────────────────────────────────────────────────────


def _owner(tid: str) -> _harness.Item | None:
    """The task N.M, or the issue ISS-n (the issues lane opens one PR per issue)."""
    if _harness.RE_ISSUE_ID.match(tid):
        return _harness._issue(tid)
    return _harness._task(tid)


def title(tid: str) -> int:
    owner = _owner(tid)
    if owner is None:
        return 1
    kind = str(owner.get("type") or "feat")  # an issue's type: fix | refactor | feat | docs
    text = RE_TYPE_PREFIX.sub("", str(owner.get("title", "")), count=1)
    print(f"{kind}: {text.lower()} [{tid}]")
    return 0


def _git(*args: str) -> str:
    res = subprocess.run(["git", *args], text=True, capture_output=True, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def _merge_base(base: str) -> str:
    return _git("merge-base", "HEAD", base) or _git("merge-base", "HEAD", f"origin/{base}")


def narrative(tid: str) -> dict[str, str]:
    path = Path(f"progress/pr_{tid}.md")
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    parts = re.split(r"^## +(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    for heading, body in zip(parts[1::2], parts[2::2], strict=True):
        key = NARRATIVE.get(heading.strip().lower())
        if key and body.strip():
            found[key] = body.strip()
    return found


def _first(paths: list[Path]) -> str:
    return f"`{paths[0].as_posix()}`" if paths else "-"


def subtasks_table(tid: str, merge_base: str) -> str:
    rows = ["| Subtask | Commit | Spec | Reviews | Reference |", "|---|---|---|---|---|"]
    span = f"{merge_base}..HEAD" if merge_base else "HEAD"
    for leaf in _task_leaves(tid):
        sid = str(leaf["id"])
        sha = _git("log", "-1", "--format=%h", "-F", f"--grep=[{sid}]", span)
        reviews = [
            Path(f"progress/plan_review_{sid}.md"),
            Path(f"progress/review_{sid}.md"),
            history_file("code", sid),
        ]
        found = ", ".join(f"`{p.as_posix()}`" for p in reviews if p.is_file()) or "-"
        ref = _first(sorted(Path("docs/reference").glob(f"{sid}-*.md")))
        spec = f"`{leaf['spec']}`" if leaf.get("spec") else "-"
        name = f"{sid} {leaf['name']}"
        rows.append(f"| {name} | {f'`{sha}`' if sha else '-'} | {spec} | {found} | {ref} |")
    return "\n".join(rows)


def files_table(merge_base: str) -> str:
    if not merge_base:
        return "The base branch was not found, so the file list could not be computed."
    rows = ["| Status | File |", "|---|---|"]
    for line in _git("diff", "--name-status", merge_base, "HEAD").splitlines():
        status, *paths = line.split("\t")
        if not paths or RE_BOOKKEEPING.search(paths[-1]):
            continue
        label = STATUS.get(status[:1], status)
        rows.append(f"| {label} | {' -> '.join(f'`{p}`' for p in paths)} |")
    return "\n".join(rows) if len(rows) > 2 else "No code, test or build files changed."


def documentation_section(tid: str) -> str:
    specs = [Path(str(leaf["spec"])) for leaf in _task_leaves(tid) if leaf.get("spec")]
    folders = sorted({spec.parent.as_posix() for spec in specs})
    issue = _harness._issue(tid) if tid.startswith("ISS-") else None
    if issue is not None:
        lines = [f"GitHub issue: {issue.get('issue') or '-'}"]
        lines.append(f"Issue header: `{issue['file']}`")
        if folders:
            lines.append("Specs of this issue: " + ", ".join(f"`{f}/`" for f in folders))
        return "\n\n".join(lines)
    if not folders:
        return EMPTY
    return "Specs of this task: " + ", ".join(f"`{folder}/`" for folder in folders)


def _related_tasks(tid: str) -> tuple[list[str], list[str]]:
    """Tasks this one depends on, and tasks waiting for it (via subtask depends)."""
    mine = {str(leaf["id"]) for leaf in _task_leaves(tid)}
    needs: set[str] = set()
    unblocks: set[str] = set()
    for leaf in _harness.leaves():
        owner = _task_of(str(leaf["id"]))
        deps = {str(d) for d in leaf.get("depends") or []}
        if owner == tid:
            needs |= {_task_of(d) for d in deps - mine}
        elif deps & mine:
            unblocks.add(owner)
    return sorted(needs), sorted(unblocks)


def branch_section(tid: str, base: str, notes: str) -> str:
    head = _git("rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
    needs, unblocks = _related_tasks(tid)
    lines = [f"Merges `{head}` into `{base}`."]
    if needs:
        lines.append(f"It builds on task {', '.join(needs)}, so that has to be merged first.")
    if unblocks:
        lines.append(f"Merging it unblocks task {', '.join(unblocks)}.")
    if notes:
        lines.append(notes)
    issue = _harness._issue(tid) if tid.startswith("ISS-") else None
    if issue is not None:
        lines.append(f"Closes #{issue['number']}")  # GitHub closes the issue on merge
    return "\n\n".join(lines)


def _template() -> str:
    cfg = _config.load(Path.cwd())
    path = Path(cfg.pr_template) if cfg.pr_template else DEFAULT_TEMPLATE
    if not path.is_file():
        raise _config.ConfigError(f"{_config.CONFIG_FILE}: pr_template not found: {path}")
    return path.read_text(encoding="utf-8")


def body(tid: str, base: str) -> int:
    task = _owner(tid)
    if task is None:
        say("FAIL", f"task or issue {tid} does not exist in docs/plan/", sys.stderr)
        return 1
    try:
        template = _template()
    except _config.ConfigError as exc:
        say("FAIL", str(exc), sys.stderr)
        return 1
    story = narrative(tid)
    merge_base = _merge_base(base)
    values = {key: story.get(key, EMPTY) for key in NARRATIVE.values()}
    values["overview"] = story.get("overview") or str(task.get("goal") or EMPTY)
    values |= {
        "task": tid,
        "title": str(task.get("title", "")),
        "subtasks": subtasks_table(tid, merge_base),
        "files": files_table(merge_base),
        "documentation": documentation_section(tid),
        "branch": branch_section(tid, base, story.get("branch_notes", "")),
    }
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    print(text.rstrip("\n"))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, *args = argv
    if cmd == "record" and len(args) == 3:
        return record(*args)
    if cmd == "body" and len(args) == 2:
        return body(*args)
    if cmd == "round-cap" and len(args) == 2:
        return round_cap(*args)
    single = {"last-of-task": last_of_task, "pr-due": pr_due, "title": title}
    if cmd in single and len(args) == 1:
        return single[cmd](args[0])
    print(f"Unknown command or arguments: {' '.join(argv)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
