#!/usr/bin/env python3
"""The issues lane on GitHub: queue, claim, release and resume (network commands).

The queue lives on GitHub so every machine and teammate sees the same one:
open issues labelled `priority:now|next|backlog` and not `status:in-progress`.
Only this script sets or changes those labels. `make check` never calls it.

Usage: python3 _issue.py <command> [args]
Commands:
  queue <n> [now|next|backlog]  validate issue #n and (re)queue it (default next)
  start <n>      claim issue #n and create its branch + header (rules below)
  release <n>    give a claimed issue back before any work (claim token required)
  cleanup <n>    drop our claim (tags + claim file) once the issue PR is open
  resume [<n>]   back to the task a merged `now` issue interrupted
  new            write progress/issue_draft.md from the issue template
  publish        publish the DRAFT_APPROVED, sealed draft (after the user's OK)

Start rules: `next`/`backlog` need no open task (a task branch, local or on
origin, whose every tip is not yet merged into origin/main, by merge, fast
forward, squash, or a PR into main whose head is that tip). `now` must run from
the open task branch it interrupts. Always: a clean tree and no in_progress leaf
on any local branch. The claim is exclusive through a unique annotated tag
`claim/ISS-<n>` pushed without force; its token is persisted first in
`git rev-parse --git-path harness-claim-ISS-<n>`, so every step can be retried.

Environment: HARNESS_GH overrides the gh binary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import _config
import _harness
from _attribution import RE_ATTRIBUTION
from _status import say

GH = os.environ.get("HARNESS_GH", "gh")
TYPE_LABELS = {"bug": "fix", "refactor": "refactor", "enhancement": "feat", "documentation": "docs"}
PRIORITIES = ("now", "next", "backlog")
IN_PROGRESS = "status:in-progress"
ISSUES_DIR = Path("docs/plan/issues")
FEATURES = "feature_list.json"
BASE = "origin/main"
DRAFT = Path("progress/issue_draft.md")
DRAFT_REVIEW = Path("progress/issue_draft_review.md")
DRAFT_SEAL = Path("progress/issue_draft.sha256")
TEMPLATE = Path(__file__).resolve().parent / "issue_template.md"
RE_VERDICT = re.compile(r"^\*\*Verdict:\*\*[ \t]*(\w+)[ \t]*$", re.MULTILINE)

Item = dict[str, Any]


class IssueError(Exception):
    """A refusal: printed as [FAIL] and exit 1."""


# ── processes ────────────────────────────────────────────────────────────────


def _run(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def git(*args: str) -> str | None:
    res = _run("git", *args)
    return res.stdout if res.returncode == 0 else None


def _git_ok(*args: str) -> None:
    res = _run("git", *args)
    if res.returncode != 0:
        raise IssueError(f"git {' '.join(args)} failed: {res.stderr.strip()}")


def gh(*args: str) -> str:
    res = _run(GH, *args)
    if res.returncode != 0:
        raise IssueError(f"gh {' '.join(args[:2])} failed: {res.stderr.strip()}")
    return res.stdout


def _one_line(text: object) -> str:
    """Untrusted GitHub text for a single header line: no newline or control char."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())


def _view(n: int) -> Item:
    fields = "number,title,state,labels,assignees,body,url"
    data: Item = json.loads(gh("issue", "view", str(n), "--json", fields))
    data["labels"] = [lb["name"] for lb in data.get("labels", [])]
    data["assignees"] = [a["login"] for a in data.get("assignees", [])]
    data["title"] = _one_line(data.get("title", ""))
    url = _one_line(data.get("url", ""))
    data["url"] = url if re.fullmatch(r"https://\S+", url) else ""
    return data


def _me() -> str:
    return gh("api", "user", "--jq", ".login").strip()


# ── labels ───────────────────────────────────────────────────────────────────


def issue_type(labels: list[str]) -> str:
    """fix | refactor | feat | docs, from exactly one known type label."""
    found = [TYPE_LABELS[lb] for lb in labels if lb in TYPE_LABELS]
    if len(found) != 1:
        known = ", ".join(TYPE_LABELS)
        raise IssueError(
            f"the issue needs exactly one type label ({known}); it has {len(found)}: "
            "fix the labels on GitHub and re-run"
        )
    return found[0]


def _priorities(labels: list[str]) -> list[str]:
    return [lb.split(":", 1)[1] for lb in labels if lb.startswith("priority:")]


def _priority(labels: list[str]) -> str | None:
    """The queue priority: exactly one valid priority:* label, else None."""
    found = _priorities(labels)
    return found[0] if len(found) == 1 and found[0] in PRIORITIES else None


def _ensure_labels(*names: str) -> None:
    for name in names:
        gh("label", "create", name, "--force")


def queue(n: int, priority: str = "next") -> int:
    if priority not in PRIORITIES:
        raise IssueError(f"PRIORITY must be one of {', '.join(PRIORITIES)}, not '{priority}'")
    issue = _view(n)
    if issue["state"] != "OPEN":
        raise IssueError(f"issue #{n} is {issue['state'].lower()}")
    issue_type(issue["labels"])
    if IN_PROGRESS in issue["labels"] or issue["assignees"]:
        holder = ", ".join(issue["assignees"]) or "claimed"
        raise IssueError(f"issue #{n} is already in progress ({holder})")
    if _remote_token(n) is not None:  # GitHub may lag behind a claim whose update failed
        raise IssueError(f"issue #{n} is claimed (claim/ISS-{n}): release it first")
    _ensure_labels(f"priority:{priority}")
    edits = ["--add-label", f"priority:{priority}"]
    for old in issue["labels"]:
        if old.startswith("priority:") and old != f"priority:{priority}":
            edits += ["--remove-label", old]
    gh("issue", "edit", str(n), *edits)
    say("OK", f"issue #{n} queued with priority {priority}")
    return 0


# ── task state from git (no network besides the explicit fetch) ─────────────


def _plan_at(ref: str | None) -> Item:
    raw = Path(FEATURES).read_text("utf-8") if ref is None else git("show", f"{ref}:{FEATURES}")
    try:
        data: Item = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data


def _task_branches() -> dict[str, str]:
    """Task id -> branch name, for every task in the plan of the tree and of origin/main."""
    cfg = _config.load(Path.cwd())
    out = {}
    for data in (_plan_at(None), _plan_at(BASE)):
        for task in data.get("features", []):
            name = _config.render(
                cfg.branch, str(task["id"]), str(task.get("title", "")), str(task.get("ticket", ""))
            )
            out[str(task["id"])] = name
    return out


def _tips(branch: str) -> list[str]:
    tips = []
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        sha = git("rev-parse", "-q", "--verify", ref)
        if sha:
            tips.append(sha.strip())
    return tips


def _merged_by_pr(branch: str, tip: str) -> bool:
    try:
        out = gh(
            "pr",
            "list",
            "--head",
            branch,
            "--base",
            "main",
            "--state",
            "merged",
            "--json",
            "headRefOid",
        )
    except IssueError:
        return False
    return any(pr.get("headRefOid") == tip for pr in json.loads(out or "[]"))


def open_task_branches() -> list[str]:
    """Existing task branches with a tip not yet merged into origin/main."""
    out = []
    for branch in sorted(set(_task_branches().values())):
        tips = _tips(branch)
        if tips and not all(
            _harness.branch_merged(t, BASE) or _merged_by_pr(branch, t) for t in tips
        ):
            out.append(branch)
    return out


def _in_progress_elsewhere() -> list[str]:
    """Leaves left in_progress in a commit of any local branch."""
    found = []
    refs = git("for-each-ref", "refs/heads", "--format=%(refname:short)") or ""
    for branch in refs.split():
        data = _plan_at(branch)
        for owner in [*data.get("features", []), *data.get("issues", [])]:
            for sub in owner.get("subtasks") or []:
                if sub.get("status") == "in_progress":
                    found.append(f"{sub['id']} on {branch}")
    return found


def _fetch() -> None:
    res = _run("git", "fetch", "-q", "origin")
    if res.returncode != 0:
        raise IssueError(f"cannot reach origin: {res.stderr.strip()}")


def _require_clean() -> None:
    """Before changing branch: no change at all (untracked files included, so nothing can
    be overwritten) and no leaf left in_progress on any local branch."""
    if (git("status", "--porcelain") or "").strip():
        raise IssueError("the working tree has uncommitted changes: finish or park them first")
    busy = _in_progress_elsewhere()
    if busy:
        raise IssueError(f"a subtask is in_progress ({', '.join(busy)}): close it first")


def _current_branch() -> str:
    return (git("branch", "--show-current") or "").strip()


def interrupted_branch(priority: str) -> str | None:
    """The task branch a `now` start interrupts (None if no task is open). Raises."""
    open_branches = open_task_branches()
    if not open_branches:
        return None
    if priority != "now":
        raise IssueError(
            f"a task is open ({', '.join(open_branches)}): a '{priority}' issue starts once "
            "it is merged into main (PRIORITY=now interrupts it between subtasks)"
        )
    current = _current_branch()
    if current not in open_branches:
        raise IssueError(
            f"run it from the open task branch it interrupts ({', '.join(open_branches)}), "
            f"not from '{current}'"
        )
    return current


# ── claim ────────────────────────────────────────────────────────────────────


def _claim_file(n: int) -> Path:
    rel = (git("rev-parse", "--git-path", f"harness-claim-ISS-{n}") or "").strip()
    return Path(rel or f".git/harness-claim-ISS-{n}")


def _load_claim(n: int) -> Item | None:
    try:
        data: Item = json.loads(_claim_file(n).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data


def _save_claim(n: int, claim: Item) -> None:
    path = _claim_file(n)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(claim, indent=2), "utf-8")
    os.replace(tmp, path)


def _remote_token(n: int) -> str | None:
    """The token of the remote claim tag, or None when there is none."""
    tag = f"refs/tags/claim/ISS-{n}"
    listed = _run("git", "ls-remote", "origin", tag)
    if listed.returncode != 0:  # never mistake an unreachable origin for "no claim"
        raise IssueError(f"cannot reach origin: {listed.stderr.strip()}")
    if not listed.stdout.strip():
        return None
    _git_ok("fetch", "-q", "origin", f"+{tag}:refs/harness/claim/ISS-{n}")
    body = git("cat-file", "-p", f"refs/harness/claim/ISS-{n}") or ""
    return body.strip().splitlines()[-1].strip() if body.strip() else ""


def reconcile(n: int) -> Item | None:
    """Step 0 of start/release: our live claim, or None. Raises on a foreign claim.

    A local claim that is being released is kept even when the remote tag is gone (the
    tag is the release's last remote step): `release` then finishes from its steps."""
    local = _load_claim(n)
    remote = _remote_token(n)
    if remote is None:
        if local and local.get("releasing"):
            return local
        # A claim that never landed: drop what is left of it locally.
        _run("git", "tag", "-d", f"claim/ISS-{n}")
        if local:
            _claim_file(n).unlink(missing_ok=True)
        return None
    if local and local.get("token") == remote:
        return local
    if local:
        _claim_file(n).unlink(missing_ok=True)
    owner = remote.split(" ", 1)[0] if remote else "someone else"
    raise IssueError(f"issue #{n} is already claimed by {owner}")


def _push_claim(n: int, claim: Item) -> None:
    tag = f"claim/ISS-{n}"
    _git_ok("tag", "-a", "-f", tag, "-m", claim["token"], BASE)
    res = _run("git", "push", "-q", "origin", f"refs/tags/{tag}")
    if res.returncode != 0:
        _run("git", "tag", "-d", tag)
        if _remote_token(n) == claim["token"]:
            return  # an earlier interrupted push of ours landed
        _claim_file(n).unlink(missing_ok=True)
        raise IssueError(f"issue #{n} was claimed by someone else first")


def _step(claim: Item, name: str) -> bool:
    """True when step <name> still has to run (see _done)."""
    return name not in claim.setdefault("steps", [])


def _done(n: int, claim: Item, name: str) -> None:
    claim["steps"].append(name)
    _save_claim(n, claim)


# ── start ────────────────────────────────────────────────────────────────────


def _header(issue: Item, kind: str, priority: str, interrupts: str | None) -> str:
    lines = [
        f"# ISS-{issue['number']} {issue['title']}",
        f"- issue: {issue['url']}",
        f"- type: {kind}",
        "- origin: ",
        f"- priority: {priority}",
    ]
    if interrupts:
        lines.append(f"- interrupts: {interrupts}")
    # The body is untrusted GitHub text: it goes in its own section, after a heading,
    # so nothing in it can be parsed as header metadata.
    body = (issue.get("body") or "").strip()
    lines += ["- goal: ", "", "## Issue body (imported from GitHub)", "", body, ""]
    return "\n".join(lines)


def _names(issue: Item, kind: str) -> tuple[str, Path]:
    slug = _config.issue_slug(str(issue["title"]))
    n = issue["number"]
    return f"{kind}/ISS-{n}-{slug}", ISSUES_DIR / f"ISS-{n}-{slug}.md"


def _check_startable(issue: Item, me: str) -> str:
    n = issue["number"]
    if issue["state"] != "OPEN":
        raise IssueError(f"issue #{n} is {issue['state'].lower()}")
    if not _priorities(issue["labels"]):
        raise IssueError(f"issue #{n} is not queued: run make issue N={n} first")
    priority = _priority(issue["labels"])
    if priority is None:
        raise IssueError(
            f"issue #{n} needs exactly one priority label (now, next or backlog): "
            f"re-queue it with make issue N={n} PRIORITY=…"
        )
    others = [a for a in issue["assignees"] if a != me]
    if IN_PROGRESS in issue["labels"] or others:
        raise IssueError(f"issue #{n} is already in progress ({', '.join(others) or 'claimed'})")
    return priority


def _branch_tip(branch: str) -> str | None:
    sha = git("rev-parse", "-q", "--verify", f"refs/heads/{branch}")
    return sha.strip() if sha else None


def _fresh_claim(n: int, issue: Item, me: str) -> Item:
    """A new claim, persisted before anything else. It freezes the names and the issue
    data the header is built from, so retries and release never re-derive them from
    GitHub (a title or label edited meanwhile changes nothing of this claim)."""
    kind = issue_type(issue["labels"])
    branch, header = _names(issue, kind)
    priority = _check_startable(issue, me)
    _require_clean()
    if _branch_tip(branch):
        raise IssueError(f"a branch named {branch} already exists: rename or delete it first")
    interrupts = interrupted_branch(priority)
    claim: Item = {
        "token": f"{me} {os.uname().nodename} {uuid.uuid4()}",
        "priority": priority,
        "start_branch": _current_branch(),
        "interrupts": interrupts,
        "branch": branch,  # the branch this claim creates, from this base
        "header": header.as_posix(),
        "base": (git("rev-parse", BASE) or "").strip(),
        "issue": {k: issue.get(k, "") for k in ("number", "title", "url", "body")} | {"kind": kind},
        "steps": [],
    }
    _save_claim(n, claim)
    return claim


def _abandon(n: int, claim: Item, reason: str) -> None:
    """Give a just-won claim back before GitHub or the branch were touched. Raises."""
    if _remote_token(n) == claim["token"]:
        _git_ok("push", "-q", "origin", f":refs/tags/claim/ISS-{n}")
    _run("git", "tag", "-d", f"claim/ISS-{n}")
    _claim_file(n).unlink(missing_ok=True)
    raise IssueError(f"{reason} (the claim was given back)")


def _confirm_startable(n: int, claim: Item, issue: Item) -> None:
    """The issue may change between reading it and winning the tag (closed, taken by
    someone else, reprioritised): re-check it as it is now before touching GitHub."""
    me = claim["token"].split(" ", 1)[0]
    others = [a for a in issue["assignees"] if a != me]
    if issue["state"] != "OPEN" or others or IN_PROGRESS in issue["labels"]:
        _abandon(n, claim, f"issue #{n} was closed or taken meanwhile")
    priority = _priority(issue["labels"])
    if priority is None:
        _abandon(n, claim, f"issue #{n} was taken out of the queue meanwhile")
    if priority == claim["priority"]:
        return
    try:
        claim["interrupts"] = interrupted_branch(str(priority))
    except IssueError as exc:
        _abandon(n, claim, f"its priority changed to {priority}: {exc}")
    claim["priority"] = priority
    _save_claim(n, claim)


def _claim_on_github(n: int, claim: Item) -> None:
    """Assign the issue to us and mark it in progress. We hold the tag, so if it already
    shows us assigned and in progress, an earlier edit of ours landed (its answer was
    lost): nothing left to do. Otherwise re-check it as it is now, then edit."""
    now = _view(n)  # as it is now, after winning the tag
    me = claim["token"].split(" ", 1)[0]
    if IN_PROGRESS in now["labels"] and now["assignees"] == [me]:
        return
    _confirm_startable(n, claim, now)
    _ensure_labels(IN_PROGRESS)
    edits = ["--add-assignee", "@me", "--add-label", IN_PROGRESS]
    for label in now["labels"]:
        if label.startswith("priority:"):
            edits += ["--remove-label", label]
    gh("issue", "edit", str(n), *edits)


def _owns_branch(branch: str, claim: Item) -> bool:
    """The branch was created by this claim: its reflog carries the claim token."""
    log = git("reflog", "show", "--format=%gs", f"refs/heads/{branch}") or ""
    return f"harness claim {claim['token']}" in log.splitlines()


def _create_branch(n: int, claim: Item, branch: str) -> None:
    if not _step(claim, "branch"):
        return
    if _branch_tip(branch) is None:
        # Create-only (empty old value) and atomic, with the token in the reflog.
        made = _run(
            "git",
            "update-ref",
            "--create-reflog",
            "-m",
            f"harness claim {claim['token']}",
            f"refs/heads/{branch}",
            claim["base"],
            "",
        )
        if made.returncode != 0:
            raise IssueError(f"a branch named {branch} already exists and is not this claim's")
    elif not _owns_branch(branch, claim):
        raise IssueError(f"a branch named {branch} already exists and is not this claim's")
    _git_ok("switch", "-q", branch)
    _done(n, claim, "branch")


def _create_header(n: int, claim: Item) -> None:
    if not _step(claim, "header"):
        return
    issue, header = claim["issue"], Path(claim["header"])
    text = _header(issue, issue["kind"], claim["priority"], claim.get("interrupts"))
    claim["header_sha"] = hashlib.sha256(text.encode()).hexdigest()
    if header.is_file() and header.read_text("utf-8") != text:
        raise IssueError(f"{header} exists with other content: it was not written by start")
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(text, "utf-8")
    _done(n, claim, "header")


def start(n: int) -> int:
    _fetch()
    claim = reconcile(n) or _fresh_claim(n, _view(n), _me())
    if claim.get("releasing"):
        raise IssueError(f"a release of issue #{n} is in progress: finish `release` first")
    if _step(claim, "tag"):
        _push_claim(n, claim)
        _done(n, claim, "tag")
    if _step(claim, "github"):
        _claim_on_github(n, claim)
        _done(n, claim, "github")
    _create_branch(n, claim, claim["branch"])
    _create_header(n, claim)
    say("OK", f"issue #{n} claimed: branch {claim['branch']}, header {claim['header']}")
    return 0


# ── release ──────────────────────────────────────────────────────────────────


def _check_releasable(claim: Item, branch: str) -> None:
    header = Path(claim.get("header", ""))
    changed = (git("status", "--porcelain", "--untracked-files=all") or "").splitlines()
    extra = [line[3:] for line in changed if line[3:] != claim.get("header")]
    if extra:
        raise IssueError(f"release would lose work: {', '.join(extra)} (nothing was changed)")
    if header.is_file():
        digest = hashlib.sha256(header.read_bytes()).hexdigest()
        if digest != claim.get("header_sha"):
            raise IssueError(f"release would lose work: {header} was edited (nothing was changed)")
    tip = _branch_tip(branch)
    if tip and tip != claim.get("base"):  # compared with the claimed base, not origin/main
        raise IssueError(f"release would lose work: {branch} has commits (nothing was changed)")


def _release_branch(n: int, claim: Item, branch: str) -> None:
    """Remove the generated header (tree and index) and the branch; re-checked for work
    on every attempt, since someone may have written something between two attempts."""
    if not _step(claim, "rel_branch"):
        return
    _check_releasable(claim, branch)
    if claim.get("header"):
        _run("git", "rm", "-q", "--cached", "--ignore-unmatch", "--", claim["header"])
        Path(claim["header"]).unlink(missing_ok=True)
    if _current_branch() == branch:
        _git_ok("switch", "-q", claim["start_branch"])
    if _branch_tip(branch) and _owns_branch(branch, claim):  # never delete someone else's
        _git_ok("branch", "-q", "-D", branch)
    if (git("status", "--porcelain") or "").strip():
        raise IssueError("release left changes in the working tree: check `git status`")
    _done(n, claim, "rel_branch")


def _release_github(n: int, claim: Item) -> None:
    if _step(claim, "rel_github"):
        gh(
            "issue",
            "edit",
            str(n),
            "--remove-assignee",
            "@me",
            "--remove-label",
            IN_PROGRESS,
            "--add-label",
            f"priority:{claim['priority']}",
        )
        _done(n, claim, "rel_github")


def release(n: int) -> int:
    """Undo a claim in reverse order, each step recorded so a rerun resumes it; the
    remote tag (the claim itself) goes last, then the local tag and claim file."""
    claim = reconcile(n)
    if claim is None:
        raise IssueError(f"issue #{n} is not claimed from this working copy")
    branch = str(claim["branch"])  # the claimed name, whatever the issue says now
    if not claim.get("releasing"):
        _check_releasable(claim, branch)  # refuse before marking anything
        claim["releasing"] = True
        _save_claim(n, claim)
    _release_branch(n, claim, branch)
    _release_github(n, claim)
    if _step(claim, "rel_tag"):
        if _remote_token(n) is not None:
            _git_ok("push", "-q", "origin", f":refs/tags/claim/ISS-{n}")
        _done(n, claim, "rel_tag")
    _run("git", "tag", "-d", f"claim/ISS-{n}")
    _claim_file(n).unlink(missing_ok=True)
    say("OK", f"issue #{n} released and queued again with priority {claim['priority']}")
    return 0


# ── cleanup (after the issue PR is open) ────────────────────────────────────


def cleanup(n: int) -> int:
    """Drop the claim once its PR is open: remote tag, local tag, claim file. Only our
    own claim (token check in reconcile); a missing claim is already clean."""
    claim = reconcile(n)
    if claim is not None and _remote_token(n) is not None:
        _git_ok("push", "-q", "origin", f":refs/tags/claim/ISS-{n}")
    _run("git", "tag", "-d", f"claim/ISS-{n}")
    _claim_file(n).unlink(missing_ok=True)
    say("OK", f"issue #{n}: claim cleaned up (its PR is open)")
    return 0


# ── resume ───────────────────────────────────────────────────────────────────


def resume(n: int | None = None) -> int:
    _fetch()
    _require_clean()
    merged_issues = [i for i in _plan_at(BASE).get("issues", []) if i.get("interrupts")]
    local = [i for i in _plan_at(None).get("issues", []) if i.get("interrupts")]
    if n is not None:
        merged_issues = [i for i in merged_issues if i["number"] == n]
        local = [i for i in local if i["number"] == n]
    pending = [
        i
        for i in merged_issues
        if _run("git", "merge-base", "--is-ancestor", BASE, i["interrupts"]).returncode != 0
    ]
    if not pending:
        if local and not merged_issues:
            raise IssueError(
                f"{local[0]['id']} is not merged into main yet: resume after the merge"
            )
        raise IssueError("no interrupted task to resume")
    branch = str(pending[0]["interrupts"])
    _git_ok("switch", "-q", branch)
    res = _run("git", "merge", "--no-edit", BASE)
    if res.returncode != 0:
        raise IssueError(
            f"merging {BASE} into {branch} conflicts: the merge is left in progress for the user"
        )
    say("OK", f"resumed task branch {branch} (updated from {BASE})")
    return 0


# ── new issues: draft and publish ────────────────────────────────────────────


def new() -> int:
    if DRAFT.exists():
        raise IssueError(f"{DRAFT} already exists")
    front = "---\ntitle: \nlabels: []\npriority: next\n---\n"
    DRAFT.parent.mkdir(parents=True, exist_ok=True)
    DRAFT.write_text(front + TEMPLATE.read_text("utf-8"), "utf-8")
    say("OK", f"draft written: {DRAFT} (then: review_with_codex.sh issue-draft)")
    return 0


def _front_matter(text: str) -> tuple[Item, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if m is None:
        raise IssueError(f"{DRAFT} has no front matter (--- title/labels/priority ---)")
    meta: Item = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()  # no inline comments: a title may contain "#"
    meta["labels"] = [
        lb.strip() for lb in meta.get("labels", "").strip("[]").split(",") if lb.strip()
    ]
    return meta, m.group(2)


def _check_draft() -> tuple[Item, str]:
    verdict = RE_VERDICT.search(DRAFT_REVIEW.read_text("utf-8")) if DRAFT_REVIEW.is_file() else None
    if verdict is None or verdict.group(1) != "DRAFT_APPROVED":
        raise IssueError("the draft is not DRAFT_APPROVED (review_with_codex.sh issue-draft)")
    sealed = DRAFT_SEAL.read_text("utf-8").split()[0] if DRAFT_SEAL.is_file() else ""
    if sealed != hashlib.sha256(DRAFT.read_bytes()).hexdigest():
        raise IssueError("the draft changed after its approval: re-run its Codex review")
    text = DRAFT.read_text("utf-8")
    if RE_ATTRIBUTION.search(text):
        raise IssueError("the draft contains agent attribution")
    meta, body = _front_matter(text)
    issue_type(meta["labels"])
    if meta.get("priority") not in PRIORITIES:
        raise IssueError(f"the draft priority must be one of {', '.join(PRIORITIES)}")
    missing = [h for h in _template_sections() if h not in body]
    if missing or not meta.get("title"):
        raise IssueError(f"the draft is incomplete: {', '.join(missing) or 'title'}")
    return meta, body


def _template_sections() -> list[str]:
    return [line for line in TEMPLATE.read_text("utf-8").splitlines() if line.startswith("## ")]


def _publish_marker() -> Path:
    rel = (git("rev-parse", "--git-path", "harness-publish") or "").strip()
    return Path(rel or ".git/harness-publish")


def _pages(out: str) -> list[Item]:
    """`gh api --paginate` prints one JSON array per page, back to back."""
    items: list[Item] = []
    decoder, pos, text = json.JSONDecoder(), 0, out.strip()
    while pos < len(text):
        page, pos = decoder.raw_decode(text, pos)
        items += page if isinstance(page, list) else []
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return items


def _published(attempt: Item) -> str | None:
    """The URL of the issue an earlier attempt created, if any: every issue this user
    created since the attempt started (all pages of the REST listing, open and closed,
    no search-index lag), matched on the attempt token locally. Complete, so an empty
    answer means that attempt created nothing."""
    path = (
        "repos/{owner}/{repo}/issues?state=all&per_page=100"
        f"&creator={attempt['creator']}&since={attempt['since']}"
    )
    found = [
        i
        for i in _pages(gh("api", "--paginate", path))
        if attempt["token"] in str(i.get("body") or "")
    ]
    return str(found[0]["html_url"]) if found else None


def _finish_publish(url: str, note: str) -> int:
    for path in (DRAFT, DRAFT_REVIEW, DRAFT_SEAL, _publish_marker()):
        path.unlink(missing_ok=True)
    say("OK", f"issue {note}: {url}")
    return 0


def publish() -> int:
    """Create the approved draft on GitHub. The body carries a hidden attempt token
    (an HTML comment, invisible on GitHub), persisted first: a retry after an attempt
    whose outcome was lost finds the issue it created instead of opening a second one."""
    meta, body = _check_draft()
    marker = _publish_marker()
    try:
        attempt: Item = json.loads(marker.read_text("utf-8"))
    except (OSError, ValueError):
        attempt = {}
    if attempt:
        url = _published(attempt)
        if url:
            return _finish_publish(url, "already published by an earlier attempt")
    else:
        since = datetime.now(UTC) - timedelta(minutes=5)  # margin for clock skew
        attempt = {
            "token": f"harness-publish-{uuid.uuid4()}",
            "creator": _me(),
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        marker.write_text(json.dumps(attempt), "utf-8")
    token = attempt["token"]
    priority = f"priority:{meta['priority']}"
    _ensure_labels(priority)
    body_file = DRAFT.with_name("issue_draft.body.md")
    # the reviewed body as is (it ends with a newline), then only the attempt marker
    sep = "" if body.endswith("\n") else "\n"
    body_file.write_text(f"{body}{sep}<!-- {token} -->\n", "utf-8")
    args = ["issue", "create", "--title", meta["title"], "--body-file", str(body_file)]
    for label in [*meta["labels"], priority]:
        args += ["--label", label]
    try:
        url = gh(*args).strip().splitlines()[-1]
    finally:
        body_file.unlink(missing_ok=True)
    return _finish_publish(url, f"published (queued with {priority})")


# ── entry point ──────────────────────────────────────────────────────────────


def _number(raw: str) -> int:
    value = raw.removeprefix("ISS-").removeprefix("#")
    if not value.isdigit():
        raise IssueError(f"not an issue number: '{raw}'")
    return int(value)


def _dispatch(cmd: str, args: list[str]) -> int:
    if cmd == "queue" and args:
        return queue(_number(args[0]), args[1] if len(args) > 1 and args[1] else "next")
    if cmd in ("start", "release", "cleanup") and len(args) == 1:
        fn = {"start": start, "release": release, "cleanup": cleanup}[cmd]
        return fn(_number(args[0]))
    if cmd == "resume" and len(args) <= 1:
        return resume(_number(args[0]) if args and args[0] else None)
    if cmd in ("new", "publish") and not args:
        return new() if cmd == "new" else publish()
    print(__doc__, file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    try:
        return _dispatch(argv[0], argv[1:])
    except IssueError as exc:
        say("FAIL", str(exc), sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
