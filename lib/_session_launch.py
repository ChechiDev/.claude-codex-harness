#!/usr/bin/env python3
"""Launch command of a parallel Claude session (issue #101).

The model and effort of a session that executes a task or subtask come from
harness.toml [sessions]: `model`/`effort` (default sonnet/medium), escalated to
`high_risk_model`/`high_risk_effort` (default opus/high) when any `files:` of the
unit matches a `high_risk_paths` glob, or when any of its specs is rated `- risk: high`
(issue #106): declared in the spec, or sealed by its phase/task/issue gate (a floor, as
in _review_risk.py). For a task (N.M) or an issue (ISS-n) the files and specs are those
of all its subtasks. An invalid rating or an unknown surface fails closed, like the
review gates; every rating is checked before any escalation.
The plan, specs and seals are read in the cwd (the checkout where `make plan` runs),
never in --worktree, the same as the files: of #101.
A leaf whose rating cannot be read (feature_list.json without its `spec` or its owner
`phase`/issue `id`, as in plans older than docs/plan) launches as high risk with a WARN
that names the missing key: never refused, never downgraded.

Usage: python3 _session_launch.py <id> [--worktree PATH] [--prompt TEXT] [--exec | --hook PROG]
  <id>        subtask (N.M.K, ISS-n.k), task (N.M) or issue (ISS-n)
  --worktree  the session's worktree: the command starts with `cd <worktree> &&`.
              It must exist; the launcher never creates worktrees.
  --prompt    the session's first prompt (default: names the id and its title)
  --exec      run claude now, in the worktree, without a shell
  --hook      run PROG with the printed command line as its only argument (a
              terminal/pane hook that opens the session somewhere else)

Without --exec/--hook it prints the command on stdout, every word shell-quoted, so
a task title or a prompt never reaches the shell as code. The reason for the
model goes to stderr. An invalid harness.toml or an unknown id fails closed.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import shlex
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import _config
import _harness
from _status import say

PERMISSION_MODE = "auto"
# Task N.M, subtask N.M.K, issue ISS-n, issue subtask ISS-n.k: nothing else is printed.
RE_ID = re.compile(r"(?:\d+\.\d+(?:\.\d+)?|ISS-\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str  # subtask | task | issue
    title: str
    files: tuple[str, ...]
    specs: tuple[tuple[str, str], ...] = ()  # (spec path, owner phase NN or issue ISS-n)
    gaps: tuple[str, ...] = ()  # leaves whose rating cannot be read (no spec / no owner)


@dataclass(frozen=True)
class Choice:
    model: str
    effort: str
    why: str | None  # what escalated, if anything
    warnings: tuple[str, ...] = ()


def _glob_regex(glob: str) -> re.Pattern[str]:
    """`*` and `?` stay within one path segment, `**` crosses segments."""
    out = ""
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif glob[i] == "*":
            out += "[^/]*"
            i += 1
        elif glob[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return re.compile(out, re.DOTALL)


def _normalize(rel: str) -> str:
    """The project-relative path, `.`/`..` resolved lexically. Raises ValueError when it
    leaves the project: a path the globs cannot see must not dodge the escalation."""
    if any(unicodedata.category(c).startswith("C") for c in rel):
        raise ValueError(f"files: {_one_line(rel)!r} has control characters")
    path = posixpath.normpath(rel)
    if path.startswith("/") or path == ".." or path.startswith("../"):
        raise ValueError(f"files: {rel!r} is outside the project")
    return path


def matches(path: str, glob: str) -> bool:
    """The file, or a directory that contains it, matches the glob (`lib` covers lib/x)."""
    regex = _glob_regex(glob)
    parts = path.split("/")
    return any(regex.fullmatch("/".join(parts[:n])) for n in range(len(parts), 0, -1))


def resolve(unit: Unit, cfg: _config.Config, root: Path | None = None) -> Choice:
    """The session's model and effort. Every rating of the unit is checked first, so a
    path match or a high spec never hides an invalid one. Raises ValueError on a rating
    that cannot be trusted (the launcher fails closed)."""
    high_specs = _high_risk_specs(unit, cfg, Path.cwd() if root is None else root)
    high = (cfg.session_high_risk_model, cfg.session_high_risk_effort)
    # A rating that cannot be read (feature_list.json without `spec` or the owner, as
    # in plans older than docs/plan) is treated as high: launched, never downgraded.
    warnings = tuple(f"{gap}: launched as high risk" for gap in unit.gaps)
    for path in unit.files:
        for glob in cfg.session_high_risk_paths:
            if matches(path, glob):
                return Choice(*high, f"{path} matches {glob}", warnings)
    if high_specs:
        return Choice(*high, high_specs[0], warnings)
    if unit.gaps:
        return Choice(*high, f"unreadable rating: {unit.gaps[0]}", warnings)
    return Choice(cfg.session_model, cfg.session_effort, None)


def _high_risk_specs(unit: Unit, cfg: _config.Config, root: Path) -> list[str]:
    """Why each spec of the unit rated `risk: high` is (declared, or sealed by its gate).
    Raises ValueError on an invalid rating or an unknown surface, as its gates do."""
    import _plan_seal  # lazy: both import _review_risk, which imports this module
    import _review_risk

    out: list[str] = []
    for spec, owner in unit.specs:
        try:
            declared = _review_risk.spec_rating(root, spec)
            sealed = _plan_seal.sealed_rating(root, spec, owner) if owner else None
        except _review_risk.RatingError as exc:
            raise ValueError(_one_line(str(exc))) from exc
        except (OSError, ValueError) as exc:  # an unreadable seal: fail closed, no traceback
            raise ValueError(f"{spec}: its seal is unreadable: {_one_line(str(exc))}") from exc
        rating = declared if sealed is None else declared.merge(sealed)
        unknown = sorted(set(rating.surfaces) - set(cfg.review_surfaces))
        if unknown:
            raise ValueError(
                f"{spec}: unknown surface(s) {', '.join(unknown)} (known: "
                f"{', '.join(sorted(cfg.review_surfaces))}; harness.toml "
                "[review.surfaces.<name>] adds one)"
            )
        if declared.risk == "high":
            out.append(f"{spec} declares risk high")
        elif rating.risk == "high":
            out.append(f"{spec}: risk high sealed by its phase/task/issue gate")
    return out


def _subtasks() -> list[_harness.Item]:
    """Every subtask record as feature_list.json has it (no defaults filled in), with
    the phase or issue that owns it under `_owner`."""
    data = _harness.plan_data()
    out: list[_harness.Item] = []
    for task in data.get("features", []):
        owner = _text(task.get("phase"))
        out += [{**sub, "_owner": owner} for sub in task.get("subtasks") or []]
    for issue in data.get("issues", []):
        owner = _text(issue.get("id"))
        out += [{**sub, "_owner": owner} for sub in issue.get("subtasks") or []]
    return out


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def unit(item_id: str) -> Unit:
    """The subtask, task or issue <item_id> from feature_list.json. Raises ValueError."""
    if not RE_ID.fullmatch(item_id):
        raise ValueError(f"{_one_line(item_id)!r} is not a subtask, task or issue id")
    subs = _subtasks()
    leaf = next((sub for sub in subs if str(sub.get("id")) == item_id), None)
    if leaf is not None:
        title = str(leaf.get("title", ""))
        return Unit(item_id, "subtask", title, _files([leaf]), *_specs([leaf]))
    owned = [sub for sub in subs if _harness.parse_id(str(sub.get("id")))[1] == item_id]
    is_issue = bool(_harness.RE_ISSUE_ID.match(item_id))
    known = _harness._issue(item_id) if is_issue else _harness._task(item_id)
    if known is None:
        raise ValueError(f"{item_id} is not a subtask, task or issue in feature_list.json")
    kind = "issue" if is_issue else "task"
    return Unit(item_id, kind, str(known.get("title", "")), _files(owned), *_specs(owned))


def _files(leaves: list[_harness.Item]) -> tuple[str, ...]:
    """The declared files; a missing or malformed `files` fails closed (sync_plan always
    writes the key, so its absence means the file was not produced by the harness)."""
    out: list[str] = []
    for leaf in leaves:
        files = leaf.get("files")
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise ValueError(f"{_one_line(str(leaf.get('id')))}: files: is not a list of paths")
        out += [_normalize(f) for f in files]
    return tuple(out)


def _specs(
    leaves: list[_harness.Item],
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """The specs that rate the leaves, and the leaves whose rating cannot be read.
    sync_plan always writes `spec` and the owner (task `phase`, issue `id`): without
    them the leaf is a gap, which the launcher treats as high risk. A spec path that
    leaves the project fails closed."""
    out: list[tuple[str, str]] = []
    gaps: list[str] = []
    for leaf in leaves:
        lid = _one_line(str(leaf.get("id")))
        spec = leaf.get("spec")
        owner = _text(leaf.get("_owner"))
        if not isinstance(spec, str) or not spec:
            gaps.append(f"{lid}: feature_list.json has no `spec` path")
            continue
        if not owner:
            gaps.append(f"{lid}: feature_list.json has no owning `phase`/issue `id`")
        try:
            out.append((_normalize(spec), owner))
        except ValueError as exc:
            raise ValueError(f"{lid}: spec {exc}") from exc
    return tuple(out), tuple(gaps)


def _one_line(text: str) -> str:
    """Drop control and format characters (escape sequences, newlines) from plan text."""
    cleaned = "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)
    return " ".join(cleaned.split())


def default_prompt(target: Unit) -> str:
    return (
        f"Work on {target.kind} {target.id}: {_one_line(target.title)}. "
        "Follow the harness workflow (CLAUDE.md)."
    )


def argv(choice: Choice, prompt: str) -> list[str]:
    return [
        "claude",
        "--model",
        choice.model,
        "--effort",
        choice.effort,
        "--permission-mode",
        PERMISSION_MODE,
        prompt,
    ]


def command_line(words: list[str], worktree: Path | None) -> str:
    line = shlex.join(words)
    return line if worktree is None else f"cd {shlex.quote(str(worktree))} && {line}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness/session_launch.sh",
        description="Print (or run) the launch command of a parallel Claude session.",
    )
    parser.add_argument("id")
    parser.add_argument("--worktree")
    parser.add_argument("--prompt")
    run = parser.add_mutually_exclusive_group()
    run.add_argument("--exec", action="store_true", dest="exec_")
    run.add_argument("--hook")
    return parser


def _fail(message: str) -> int:
    say("FAIL", message, sys.stderr)
    return 1


def main(args: list[str]) -> int:
    opts = _parser().parse_args(args)
    if not _harness.FEATURES_FILE.exists():
        return _fail("No feature_list.json in the cwd (run: make plan)")
    try:
        cfg = _config.load(Path.cwd())
        target = unit(opts.id)
        choice = resolve(target, cfg)
    except (_config.ConfigError, ValueError) as exc:
        return _fail(str(exc))
    worktree: Path | None = None
    if opts.worktree is not None:
        worktree = Path(opts.worktree).resolve()
        if not worktree.is_dir():
            return _fail(f"worktree {_one_line(str(worktree))!r} does not exist (create it first)")
    prompt = default_prompt(target) if opts.prompt is None else opts.prompt
    if not prompt.strip() or prompt.lstrip().startswith("-"):
        return _fail("the prompt must be non-empty and must not start with '-'")
    why = choice.why or "default"
    for warning in choice.warnings:
        say("WARN", warning, sys.stderr)
    say("OK", f"{target.kind} {target.id}: {choice.model}/{choice.effort} ({why})", sys.stderr)
    words = argv(choice, prompt)
    line = command_line(words, worktree)
    if opts.exec_:
        if worktree is not None:
            os.chdir(worktree)
        os.execvp(words[0], words)
    if opts.hook:
        return subprocess.run([opts.hook, line], check=False).returncode
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
