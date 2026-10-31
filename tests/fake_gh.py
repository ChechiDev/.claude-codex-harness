#!/usr/bin/env python3
"""A tiny stand-in for the gh CLI: the subset the issues lane uses, state in a JSON file.

State ($FAKE_GH_STATE): {"me": "alice", "issues": {"14": {...}}, "labels": [],
"prs": [{"head": "...", "base": "main", "state": "merged", "headRefOid": "..."}],
"next": 15, "calls": [[...argv...]]}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

STATE = Path(os.environ["FAKE_GH_STATE"])


def _opts(args: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            out.setdefault(args[i], []).append(args[i + 1])
            i += 2
        else:
            i += 1
    return out


def _view(state: dict[str, Any], n: str) -> int:
    issue = state["issues"].get(n)
    if issue is None:
        print(f"no issue {n}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "number": int(n),
                "title": issue["title"],
                "state": issue.get("state", "OPEN"),
                "labels": [{"name": lb} for lb in issue.get("labels", [])],
                "assignees": [{"login": a} for a in issue.get("assignees", [])],
                "body": issue.get("body", ""),
                "url": f"https://github.com/acme/app/issues/{n}",
            }
        )
    )
    return 0


def _edit(state: dict[str, Any], n: str, opts: dict[str, list[str]]) -> int:
    issue = state["issues"][n]
    if os.environ.get("FAKE_GH_FAIL_EDIT"):
        print("network down", file=sys.stderr)
        return 1
    labels = [lb for lb in issue.get("labels", []) if lb not in opts.get("--remove-label", [])]
    labels += [lb for lb in opts.get("--add-label", []) if lb not in labels]
    issue["labels"] = labels
    me = state["me"]
    assignees = issue.setdefault("assignees", [])
    if "@me" in opts.get("--add-assignee", []) and me not in assignees:
        assignees.append(me)
    if "@me" in opts.get("--remove-assignee", []) and me in assignees:
        assignees.remove(me)
    if os.environ.get("FAKE_GH_EDIT_LOST"):  # applied on GitHub, answer lost on the way
        print("connection reset", file=sys.stderr)
        return 1
    return 0


def _create(state: dict[str, Any], opts: dict[str, list[str]]) -> int:
    n = str(state.get("next", 1))
    state["next"] = int(n) + 1
    state["issues"][n] = {
        "title": opts["--title"][0],
        "body": Path(opts["--body-file"][0]).read_text(),
        "labels": opts.get("--label", []),
    }
    if os.environ.get("FAKE_GH_CREATE_LOST"):  # created on GitHub, answer lost on the way
        print("connection reset", file=sys.stderr)
        return 1
    print(f"https://github.com/acme/app/issues/{n}")
    return 0


def _pr_list(state: dict[str, Any], opts: dict[str, list[str]]) -> int:
    prs = [
        {"headRefOid": pr["headRefOid"]}
        for pr in state.get("prs", [])
        if pr["head"] == opts["--head"][0]
        and pr["base"] == opts.get("--base", ["main"])[0]
        and pr["state"] == opts.get("--state", ["open"])[0]
    ]
    print(json.dumps(prs))
    return 0


def _list(state: dict[str, Any], opts: dict[str, list[str]]) -> int:
    wanted = opts.get("--state", ["open"])[0]
    if "--search" in opts and os.environ.get("FAKE_GH_SEARCH_EMPTY"):
        print("[]")  # GitHub's search index lags behind a just-created issue
        return 0
    token = opts.get("--search", [""])[0].split(" in:body")[0].strip('"')
    newest = sorted(state["issues"].items(), key=lambda kv: -int(kv[0]))  # gh: newest first
    print(
        json.dumps(
            [
                {
                    "number": int(n),
                    "title": issue["title"],
                    "url": f"https://github.com/acme/app/issues/{n}",
                    "body": issue.get("body", ""),
                    "labels": [{"name": lb} for lb in issue.get("labels", [])],
                    "assignees": [{"login": a} for a in issue.get("assignees", [])],
                }
                for n, issue in newest
                if (wanted == "all" or issue.get("state", "OPEN") == "OPEN")
                and token in issue.get("body", "")
            ][: int(opts.get("--limit", ["30"])[0])]  # gh's default limit is 30
        )
    )
    return 0


def _api_issues(state: dict[str, Any], argv: list[str]) -> int:
    """`gh api --paginate repos/{owner}/{repo}/issues?state=all&creator=<me>&since=…`:
    every page at once, filtered by creator (issues created here default to `me`)."""
    query = argv[-1].split("?", 1)[1] if "?" in argv[-1] else ""
    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
    creator = params.get("creator", "")
    found = [
        {
            "number": int(n),
            "html_url": f"https://github.com/acme/app/issues/{n}",
            "body": issue.get("body", ""),
        }
        for n, issue in state["issues"].items()
        if not creator or issue.get("author", state["me"]) == creator
    ]
    print(json.dumps(found))
    return 0


def main(argv: list[str]) -> int:
    state: dict[str, Any] = json.loads(STATE.read_text())
    state.setdefault("calls", []).append(argv)
    rc = 0
    if argv[:2] == ["issue", "view"]:
        rc = _view(state, argv[2])
    elif argv[:2] == ["issue", "edit"]:
        rc = _edit(state, argv[2], _opts(argv[3:]))
    elif argv[:2] == ["issue", "create"]:
        rc = _create(state, _opts(argv[2:]))
    elif argv[:2] == ["issue", "list"]:
        rc = _list(state, _opts(argv[2:]))
    elif argv[:2] == ["label", "create"]:
        state.setdefault("labels", []).append(argv[2])
    elif argv[:2] == ["pr", "list"]:
        rc = _pr_list(state, _opts(argv[2:]))
    elif argv[:2] == ["api", "user"]:
        print(state["me"])
    elif argv[:1] == ["api"] and "/issues" in argv[-1]:
        rc = _api_issues(state, argv)
    else:
        print(f"fake gh: unsupported {argv}", file=sys.stderr)
        rc = 1
    STATE.write_text(json.dumps(state))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
