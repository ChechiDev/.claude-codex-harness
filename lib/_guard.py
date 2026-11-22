#!/usr/bin/env python3
"""Harness guard decisions: PreToolUse hooks + the check.sh integrity backstop.

Usage: python3 _guard.py <command> [args]
Commands:
  write        PreToolUse (Edit|Write|MultiEdit|NotebookEdit); hook JSON on stdin
  bash         PreToolUse (Bash); hook JSON on stdin
  integrity    backstop for check.sh: src/ scope vs plan + sealed acceptance tests
               + sealed (approved) roadmap and phase breakdowns
  seal <id>         record progress/acceptance_<id>.sha256 (needs PLAN_APPROVED) and
                    re-seal its spec in the phase seal
  seal roadmap      record progress/plan_roadmap.sha256 (needs ROADMAP_APPROVED)
  seal task <N.M>   record progress/plan_task_N.M.sha256 (needs TASK_APPROVED)
  seal phase <NN>   record progress/plan_phase_<NN>.sha256 (needs PHASE_APPROVED)
  seal issue <ISS-n>  record progress/plan_issue_<ISS-n>.sha256 (needs ISSUE_APPROVED)
  seal draft        record progress/issue_draft.sha256 (needs DRAFT_APPROVED)
  fingerprint  print a hash of the git working-tree state, the files that drive the
               check (ignored or not) and the toolkit: the green-record key
  setup-pending  exit 1 if the setup branch (harness.toml [git] setup_branch) has
                 changes main does not contain yet (squash merges count as merged)
  migrates-ready <id>  exit 1 unless every oracle in <id>'s `migrates:` exists, is
                 sealed and untouched, and the list is the one frozen at its issue gate
  commit-message <file> <N.M.K>  exit 1 unless <file> is a close-commit message for
                 subtask <N.M.K>: subject ending in [N.M.K], no agent attribution

Guards exit 0 to allow and 2 to block (reason on stderr). The Bash guard is a
best-effort shell parser: it cannot see every way a program may write a file,
which is why `integrity` (run by `make check` and the Stop hook) re-verifies the
outcome from disk and git, independently of how the write happened.
"""

from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cache, partial
from pathlib import Path
from typing import Any

import _config
import _harness
import _migrate
import _plan_seal
import _pywrites
import _seal_required
import _shell
import _supersede
import _upgrade
import _verdict
from _attribution import RE_ATTRIBUTION
from _status import say

BLOCK = 2
ROOT = Path(os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()))
# Project config (human-owned): [scope] src_dirs lists the production-code dirs,
# relative to the project root. Without it, any src/ or include/ segment counts.
CONFIG_FILE = _config.CONFIG_FILE
ConfigError = _config.ConfigError
DEFAULT_SRC_DIRS = ("src", "include")
# Roots besides the source dirs whose deletion destroys protected state.
STATE_ROOTS = ("tests/acceptance", "progress", ".harness")
SKIP_DIRS = {"node_modules", "site-packages"}
# Gate verdicts (roadmap, phase, plan, code), their history, the task PR
# narrative and every seal under progress/.
RE_CODEX_OWNED = re.compile(
    r"^(progress/(plan_review|review|phase_review|issue_review|task_review|task_close"
    r"|phase_close)_[^/]+\.md"
    # Legacy record of a phase broken down whole (issue #62), sealed in its phase seal.
    r"|progress/task_flow_legacy_[^/]+\.json"
    r"|progress/(roadmap_review|issue_draft_review|legacy_inventory_review"
    r"|oracle_contract_review|config_contract_review)\.md"
    # Sealed I4 baseline (issue #65): written only by the human-run _oracle.py.
    r"|progress/(oracle|contract)_baseline\.json"
    # Sealed legacy inventory (M0b): written only by the human-run legacy_inventory.sh.
    r"|progress/legacy_inventory\.json"
    r"|progress/review_history/[^/]+\.md|progress/pr_[^/]+\.md"
    r"|progress/issue_migrations_[^/]+\.json|progress/oracle_migrations/[^/]+\.json"
    r"|progress/[^/]+\.sha256|progress/close_[^/]+\.msg|\.git/harness-[^/]+(/.*)?"
    # Migration record and backups (M0a): written only by the human-run migrator.
    r"|\.harness(/.*)?)$"
)

# Harness-owned entries (I3a): the links install.sh creates plus the local settings,
# matched lexically (links NOT followed). A fixed list: expected_links() raises in
# the per-skill shape, so the guard never calls it.
HARNESS_ENTRIES = (*_upgrade.LINKS, ".claude/settings.local.json")
HARNESS_GLOBAL = ("skills", "CLAUDE.md")  # machine-level links under $CLAUDE_HOME
TOOLKIT = _upgrade.TOOLKIT  # the checkout that supplies the running guard
# The supersede tickets (issue #30) share the mechanism: codex-owned in every worktree.
VERDICT_SLOTS = ("harness-verdict-slot", "harness-verdict-partial", "harness-supersede-ticket")

MSG_HARNESS = (
    "BLOCKED: '{target}' is harness-owned (toolkit links, hook settings or the toolkit files "
    "behind them). Claude never changes it: ask the human; changes go through the toolkit "
    "repository."
)
MSG_ACCEPTANCE = (
    "BLOCKED: tests/acceptance/ is the independent oracle written by Codex and is READ-ONLY "
    "for Claude. If you think an acceptance test is wrong, stop and report it as a blocker in "
    "progress/current.md — do not edit it."
)
MSG_CODEX_OWNED = (
    "BLOCKED: '{rel}' is owned by Codex (gate verdicts, acceptance seals) or by the harness. "
    "Claude never writes it; run the corresponding ./harness/review_with_codex.sh gate instead."
)
MSG_CONFIG = (
    f"BLOCKED: {CONFIG_FILE} is the human-owned harness config (which dirs hold production "
    "code). Claude never edits it: ask the human to change it."
)
MSG_NO_LEAF = (
    "BLOCKED: there is no in_progress feature in feature_list.json. The leader must pick and "
    "plan a feature before implementing."
)
MSG_NO_PLAN = (
    "BLOCKED: feature {id} has no plan approved by Codex ({review} = PLAN_APPROVED). The leader "
    "must pass the plan gate before the implementer touches src/."
)
MSG_NO_FILES = (
    "BLOCKED: subtask {id} declares no 'files:' in docs/plan/. Micro-task rule: the leader must "
    "list the exact files (1-3) this subtask will create/modify, run 'make plan', and re-pass "
    "the plan gate before any src/ write."
)
MSG_OUT_OF_SCOPE = (
    "BLOCKED: '{rel}' is NOT in the declared 'files:' of subtask {id}. Allowed files:\n{files}\n"
    "If this file is genuinely needed, STOP: the leader must add it to docs/plan/ (keep the "
    "subtask micro — 1-3 files), run 'make plan', and re-pass the plan gate. Do not implement "
    "anything outside the plan."
)
MSG_SHELL_WRITE = (
    "BLOCKED: this Bash command would modify '{target}' ({kind}). Protected paths are never "
    "written from the shell: use the Edit/Write tools for declared src/ files (so the plan scope "
    "is enforced); tests/acceptance/ and the Codex verdicts are read-only for Claude."
)
MSG_SHELL_OPAQUE = (
    "BLOCKED: '{cmd}' writes files the harness cannot inspect (patches, xargs, interpreters "
    "touching protected paths). Use the Edit/Write tools so the plan scope is enforced."
)
MSG_SEAL = (
    "BLOCKED: sealing (acceptance tests, the approved roadmap, a phase, the legacy inventory "
    "or the attempt ledger) is done by the Codex "
    "gate itself (or by the human in manual mode), never by Claude."
)
MSG_ATTRIBUTION = (
    "BLOCKED: the commit/PR contains agent attribution (Co-Authored-By / 'Generated with' / "
    "mention of Claude/Codex). Present it as the human author's work, with no attribution."
)
MSG_COMMIT = (
    "BLOCKED: Claude does not commit in the harness. The single per-subtask commit is made by "
    "the close (./harness/review_with_codex.sh doc <N.M.K>). If you really need a manual "
    "commit outside the flow, the human must start Claude Code with HARNESS_ALLOW_COMMIT=1."
)
# Issue #61 item 5: a manual commit (HARNESS_ALLOW_COMMIT=1) needs a green record of the
# tree being committed, the same check as the PR barrier of #55. WARN for now; it moves
# to BLOCK in the WARN->FAIL calendar. HARNESS_ALLOW_COMMIT=force is the human valve.
GREEN_RECORD = "harness-check.ok"  # git-path of the record run_check.sh --record writes
FORCE_LOG = "harness-commit-force.log"  # git-path of the force audit log
MSG_WARN_COMMIT_RECORD = (
    "[WARN] HARNESS_ALLOW_COMMIT=1 commit (#61): {problem}. Get one first with "
    "`bash harness/run_check.sh --record` (a green check of an unchanged tree), then commit "
    "that same state. Allowed for now (WARN); it becomes a BLOCK in the WARN->FAIL calendar. "
    "The human valve HARNESS_ALLOW_COMMIT=force skips this check and logs its use."
)
MSG_WARN_COMMIT_FORCE = (
    "[WARN] HARNESS_ALLOW_COMMIT=force: this commit skips the green record check (#61); "
    "its use is logged in {log}."
)

# I8: operations accepted today that create, move or publish commits outside the
# harness step. WARN first (plan v5 0.5): allowed, with one note per operation.
MSG_WARN_COMMIT = (
    "[WARN] '{op}' creates or moves commits outside the harness close (I8): the single "
    "per-subtask commit is the close's (./harness/review_with_codex.sh doc <N.M.K>). "
    "Allowed for now (WARN, plan 0.5); make check reports every commit on the branch "
    "that the close did not make."
)
MSG_WARN_PUBLISH = (
    "[WARN] '{op}' publishes outside the harness step (I8): the harness pushes the branch "
    "and opens the PR itself (./harness/review_with_codex.sh pr <id>), and only the human "
    "merges. Allowed for now (WARN, plan 0.5)."
)
# git subcommands that create or move commits, and the options with which they do not.
COMMIT_OPS: dict[str, frozenset[str]] = {
    "commit-tree": frozenset(),
    "update-ref": frozenset(),
    "revert": frozenset({"--abort", "--quit", "-n", "--no-commit"}),
    "cherry-pick": frozenset({"--abort", "--quit", "-n", "--no-commit"}),
    "pull": frozenset(),
    "merge": frozenset({"--abort", "--quit"}),
    "rebase": frozenset({"--abort", "--quit", "--edit-todo", "--show-current-patch"}),
}
PUSH_DRY_RUN = frozenset({"-n", "--dry-run"})
# Where git reads its config from: global options and variables (I8 aliases).
GIT_WHERE = "\0where"  # key in the inline config map: the location options
GIT_WHERE_OPTS = frozenset({"-C", "--git-dir", "--work-tree"})
# Wrapper options that run the command in another directory or project.
WRAPPER_CHDIR = frozenset({"--chdir", "-D", "--directory", "--project"})
GIT_LOCATION_VARS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG", "HOME", "XDG_CONFIG_HOME",
)  # fmt: skip
# A same-line write to a git config file changes the aliases of the git calls after it.
RE_GIT_CONFIG_FILE = re.compile(r"(^|/)(\.git/config(\.worktree)?|\.gitconfig|git/config)$")
RE_GIT_TEXT = re.compile(r"\bgit\b")  # fallback for a script the guard cannot follow
# git config options that take the next word as their value.
GIT_CONFIG_VALUE_OPTS = frozenset(
    {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value", "--url"}
)


# Gate controls (issue #90): the gates read them from harness.toml and ignore the
# environment; an inline assignment next to a gate call is refused outright.
RE_CONTROL_ASSIGN = re.compile(
    r"\b(HARNESS_CODEX_SANDBOX|HARNESS_CODEX_MODE|HARNESS_CODEX_NETWORK|HARNESS_GH|"
    r"HARNESS_AUTO_PR)\+?="
)
RE_GATE_CALL = re.compile(r"review_with_codex\.sh|\bmake\b")
MSG_CONTROL = (
    "BLOCKED: {name} steers how the harness gates run Codex or publish the PR; the gates "
    "ignore it from the environment. The human sets it in harness.toml ([codex] "
    "mode/sandbox/network, [pr] auto/gh)."
)


# Exporting a shell function hands it to every child Bash, the harness scripts
# included, where it could replace a command of a decision-making check.
RE_EXPORT_FUNCTION = re.compile(
    r"BASH_FUNC_|\bexport\s+(?:-\w*\s+)*-\w*f|\b(?:declare|typeset|local)\s+(?:-\w*\s+)*-\w*(?:fx|xf)"
)
# A Bash startup file runs in every child Bash before its first line.
RE_STARTUP = re.compile(r"\bBASH_ENV\b")
MSG_STARTUP = (
    "BLOCKED: BASH_ENV is not allowed: the startup file it names runs inside the "
    "harness scripts before their first line."
)
MSG_EXPORT_FUNCTION = (
    "BLOCKED: exporting shell functions (export -f, declare -fx, BASH_FUNC_*) is not "
    "allowed: they reach the harness scripts and could replace a command of the check."
)


MSG_EXPECT_RED = (
    "BLOCKED: HARNESS_EXPECT_RED is set only by the issue gate for its third run "
    "(review_with_codex.sh). Run `make check` without it."
)


MSG_ASSUME = (
    "BLOCKED: HARNESS_ASSUME_APPROVED is set only by the roadmap/phase/issue gate for its "
    "counterfactual run (review_with_codex.sh). Run `make check` without it."
)


MSG_PLANNER_SCOPE = (
    "BLOCKED: the claude-planner subagent may write only docs/plan/** and CLAUDE.md "
    "(target: {target}). Return the request to the leader instead."
)

# Per-agent write scopes (#63): an agent listed here may write only inside its
# project-relative prefixes / exact files; every other path is blocked for it.
# Claude Code sets `agent_type` in the hook payload for calls made by a subagent.
AGENT_WRITE_SCOPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "claude-planner": (("docs/plan/",), ("CLAUDE.md",)),
}


class Blocked(Exception):
    """A guard decision to block, carrying the message Claude reads."""


@cache
def config() -> _config.Config:
    """The project's harness.toml (raises ConfigError: callers fail closed)."""
    return _config.load(ROOT)


def src_dirs() -> tuple[str, ...] | None:
    """Configured source dirs (root-relative), or None to use the src/ include/ default."""
    return config().src_dirs


def _protected_roots() -> tuple[str, ...]:
    """Roots whose deletion (or deletion of an ancestor) destroys protected state."""
    return (*(src_dirs() or DEFAULT_SRC_DIRS), *STATE_ROOTS)


def _mentions_protected(command: str) -> bool:
    dirs = "|".join(re.escape(d) + "/" for d in src_dirs() or DEFAULT_SRC_DIRS)
    # `<>`: a perl mode glued to the path, open(F, ">progress/x")
    return re.search(rf"(^|[\s'\"(=/<>`])({dirs}|{RE_MENTIONS_STATE})", command) is not None


def _mentions_harness(command: str) -> bool:
    """The command text names a harness-owned path (interpreter writes: opaque targets)."""
    entries = "|".join(re.escape(e) for e in HARNESS_ENTRIES)
    home = os.path.abspath(_upgrade._claude_home())
    globs = "|".join(re.escape(f"{home}/{g}") for g in HARNESS_GLOBAL)
    root = re.escape(f"{ROOT}/")
    lexical = rf"(^|[\s'\"(=<>`]|\./|{root})({entries})($|[/\s'\")`])"
    if re.search(lexical, command) or re.search(globs, command):
        return True
    return not ROOT.is_relative_to(TOOLKIT) and str(TOOLKIT) in command


# ── Path classification ──────────────────────────────────────────────────────


def resolve(raw: str, cwd: Path) -> Path:
    """Absolute, symlink-resolved, ..-free path (works for not-yet-existing files)."""
    path = Path(os.path.expanduser(raw))
    return Path(os.path.realpath(path if path.is_absolute() else cwd / path))


def project_rel(path: Path) -> str | None:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _segment_index(parts: tuple[str, ...], needle: tuple[str, ...]) -> int:
    """Index where `needle` starts in `parts` outside tool/hidden dirs, or -1."""
    for i in range(len(parts) - len(needle) + 1):
        if any(p.startswith(".") or p in SKIP_DIRS for p in parts[:i]):
            return -1
        if parts[i : i + len(needle)] == needle:
            return i
    return -1


def classify(rel: str) -> str | None:
    """'acceptance' | 'codex-owned' | 'src' | None for a project-relative path."""
    if RE_CODEX_OWNED.match(rel):
        return "codex-owned"
    if rel == CONFIG_FILE:
        return "config"
    parts = tuple(Path(rel).parts)
    if _segment_index(parts, ("tests", "acceptance")) >= 0:
        return "acceptance"
    dirs = src_dirs()
    if dirs is not None:
        return "src" if any(rel == d or rel.startswith(f"{d}/") for d in dirs) else None
    # include/ holds C public headers: same micro-task scope as src/.
    if any(_segment_index(parts, (d,)) >= 0 for d in DEFAULT_SRC_DIRS):
        return "src"
    return None


def protected_kind(path: Path, destructive: bool = False) -> str | None:
    """Why `path` is protected, or None. Destructive ops also protect ancestors."""
    rel = project_rel(path)
    if rel is None:
        if destructive and ROOT.is_relative_to(path):
            return "ancestor"
        return "codex-owned" if in_verdict_slot(path) else None
    kind = classify(rel) if rel != "." else None
    if kind or not destructive:
        return kind
    for root in _protected_roots():
        if (ROOT / root).is_relative_to(path):
            return f"ancestor of {root}/"
    return None


@cache
def verdict_slots() -> tuple[Path, ...]:
    """Resolved verdict slot dirs of this checkout (outside the root in a linked worktree)."""
    slots = []
    for name in VERDICT_SLOTS:
        raw = _git_out("rev-parse", "--git-path", name)
        if raw is not None and raw.strip():
            slots.append(resolve(raw.strip(), ROOT))
    return tuple(slots)


def in_verdict_slot(path: Path) -> bool:
    """`path` (resolved) is a verdict slot dir or lies under one."""
    return any(path.is_relative_to(slot) for slot in verdict_slots())


def _lexical(raw: str, cwd: Path) -> Path:
    """Absolute, ..-free path WITHOUT following links (the shell's view of `raw`)."""
    path = Path(os.path.expanduser(raw))
    return Path(os.path.normpath(path if path.is_absolute() else cwd / path))


def _lstat_spellings(raw: str, cwd: Path) -> set[Path]:
    """The lexical path, and the same with every component but the last resolved (lstat)."""
    lex = _lexical(raw, cwd)
    return {lex, Path(os.path.realpath(lex.parent)) / lex.name}


def _harness_bases() -> list[Path]:
    home = Path(os.path.abspath(_upgrade._claude_home()))
    homes = {home, Path(os.path.realpath(home))}
    return [ROOT / e for e in HARNESS_ENTRIES] + [h / g for h in homes for g in HARNESS_GLOBAL]


def harness_kind(raw: str, cwd: Path, destructive: bool = False) -> bool:
    """True when `raw` names a harness-owned path (I3a). Lexical: a harness entry or
    under one (links not followed), and destructive ops also on an ancestor (.claude/,
    $CLAUDE_HOME). Resolved: the local settings through an alias, or anything inside
    the toolkit unless the project itself lies inside it."""
    spellings = _lstat_spellings(raw, cwd)
    for base in _harness_bases():
        for path in spellings:
            if path.is_relative_to(base) or (destructive and base.is_relative_to(path)):
                return True
    real = resolve(raw, cwd)
    if real == resolve(".claude/settings.local.json", ROOT):
        return True
    return real.is_relative_to(TOOLKIT) and not ROOT.is_relative_to(TOOLKIT)


# ── Write guard ──────────────────────────────────────────────────────────────


def _tool_input(payload: object) -> dict[str, Any]:
    """The payload's tool_input; raises (the guard then blocks) if it is malformed (I9)."""
    if not isinstance(payload, dict):
        raise ValueError("the hook payload is not a JSON object")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise ValueError("the hook payload has no tool_input object")
    return tool_input


def guard_write(payload: object) -> None:
    tool_input = _tool_input(payload)
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        raise ValueError("the write payload has no file_path/notebook_path string")
    if harness_kind(raw, ROOT):
        raise Blocked(MSG_HARNESS.format(target=raw))
    path = resolve(raw, ROOT)
    rel = project_rel(path)
    _check_agent_scope(payload, raw, rel)
    if rel is None:
        if in_verdict_slot(path):
            raise Blocked(MSG_CODEX_OWNED.format(rel=raw))
        return
    kind = classify(rel)
    if kind == "acceptance":
        raise Blocked(MSG_ACCEPTANCE)
    if kind == "codex-owned":
        raise Blocked(MSG_CODEX_OWNED.format(rel=rel))
    if kind == "config":
        raise Blocked(MSG_CONFIG)
    if kind == "src":
        _check_src_scope(rel)
    _warn_selection_input(rel)


def _warn_selection_input(rel: str) -> None:
    """WARN (issues #65/#66, plan 0.5): an edit to a test selection input while the I4
    baseline exists, or to a lint/type/build config input while the I5 baseline exists.
    Allowed, with one note to Claude and the user; blocking is I4-fail/I5-fail."""
    import _contract
    import _oracle

    notes: list[str] = []
    base = _oracle.baseline()[0]
    if base is not None and _oracle.selection_input(rel, base.get("selection")):
        notes.append(
            f"[WARN] '{rel}' is a test selection input (it decides which tests the check "
            f"runs): a change outside the contract flow is reported by every check — "
            f"{_oracle.FIX_CONTRACT}"
        )
    sealed = _contract.baseline()[0]
    if sealed is not None and _contract.config_input(rel, sealed.get("inputs")):
        notes.append(
            f"[WARN] '{rel}' is a lint/type/build config input (sealed verification "
            f"contract, issue #66): a change outside the contract flow is reported by every "
            f"check — {_contract.FIX}"
        )
    if not notes:
        return
    _emit_warning("\n".join(notes))


def _check_agent_scope(payload: object, raw: str, rel: str | None) -> None:
    """Restrict a scoped subagent (AGENT_WRITE_SCOPES) to its own paths; the
    other rules still apply on top. `rel` is the resolved project path."""
    agent = payload.get("agent_type") if isinstance(payload, dict) else None
    if not isinstance(agent, str) or agent not in AGENT_WRITE_SCOPES:
        return
    prefixes, exact = AGENT_WRITE_SCOPES[agent]
    if rel is not None and (rel in exact or any(rel.startswith(p) for p in prefixes)):
        return
    raise Blocked(MSG_PLANNER_SCOPE.format(target=raw))


# Per-agent Bash allowlists (#63): the whole command must be one of these
# make targets, alone or joined by `&&` (no other shell syntax at all).
AGENT_BASH_TARGETS: dict[str, frozenset[str]] = {
    "claude-planner": frozenset({"plan", "check"}),
}
RE_MAKE_TARGET = re.compile(r"make ([a-z]+)")
MSG_PLANNER_BASH = (
    "BLOCKED: the claude-planner subagent may run only `make plan` and `make check` "
    "(command: {command!r}). Return the request to the leader instead."
)


def _check_agent_bash(payload: object, command: str) -> None:
    agent = payload.get("agent_type") if isinstance(payload, dict) else None
    if not isinstance(agent, str) or agent not in AGENT_BASH_TARGETS:
        return
    allowed = AGENT_BASH_TARGETS[agent]
    parts = command.strip().split(" && ")
    for part in parts:
        m = RE_MAKE_TARGET.fullmatch(part.strip(" "))
        if m is None or m.group(1) not in allowed:
            raise Blocked(MSG_PLANNER_BASH.format(command=command))


def _current_leaf() -> str | None:
    try:
        return _harness.current_leaf_id()
    except (OSError, ValueError, KeyError):
        return None


def _check_src_scope(rel: str) -> None:
    leaf = _current_leaf()
    if not leaf:
        raise Blocked(MSG_NO_LEAF)
    review = f"progress/plan_review_{leaf}.md"
    if _harness.read_verdict(ROOT / review) != "PLAN_APPROVED":
        raise Blocked(MSG_NO_PLAN.format(id=leaf, review=review))
    files = _harness.leaf_files(leaf) or []
    if not files:
        raise Blocked(MSG_NO_FILES.format(id=leaf))
    if rel not in files:
        listing = "\n".join(f"  - {f}" for f in files)
        raise Blocked(MSG_OUT_OF_SCOPE.format(rel=rel, id=leaf, files=listing))


# ── Bash guard: tokenizer ────────────────────────────────────────────────────


@dataclass
class Frame:
    """One shell's state while the analyser walks a call (I3b): cwd and variables."""

    cwd: Path
    vars: dict[str, str | None] = field(default_factory=dict)  # None: unresolved
    exported: set[str] = field(default_factory=set)
    tainted: bool = False  # after `source`: every variable is unresolved
    cwd_dynamic: bool = False  # after a `cd` to an unresolved directory

    def lookup(self, name: str) -> str | None:
        if self.tainted:
            return None
        if name in self.vars:
            return self.vars[name]
        if name == "PWD":
            return None if self.cwd_dynamic else str(self.cwd)
        return os.environ.get(name) if name in ENV_VARS else None

    def child(self, everything: bool) -> Frame:
        """A substitution (`everything`: all variables) or a nested shell (exported only)."""
        names = self.vars if everything else {n for n in self.vars if n in self.exported}
        return Frame(
            self.cwd,
            {n: self.vars[n] for n in names},
            set(self.exported),
            self.tainted,
            self.cwd_dynamic,
        )


# D2: the only variables resolved from the guard's environment ($PWD tracks `cd`).
ENV_VARS = frozenset({"HOME", "CLAUDE_PROJECT_DIR", "CLAUDE_HOME"})
DECLARERS = frozenset({"export", "declare", "typeset", "local", "readonly"})


WRAPPERS = {"builtin", "nohup"}  # no options: the next word is the command
# I8: wrappers whose options are skipped before the command they run. Per wrapper:
# (options taking the next word as their value, options with no value, operands that
# precede the command). Any other option counts as unknown (see _skip_flags).
OPTION_WRAPPERS: dict[str, tuple[frozenset[str], frozenset[str], int]] = {
    "command": (frozenset(), frozenset({"-p"}), 0),
    "exec": (frozenset({"-a"}), frozenset({"-c", "-l"}), 0),
    "time": (
        frozenset({"-f", "--format", "-o", "--output"}),
        frozenset({"-p", "--portability", "-a", "--append", "-v", "--verbose", "-q", "--quiet"}),
        0,
    ),
    "nice": (frozenset({"-n", "--adjustment"}), frozenset(), 0),
    "stdbuf": (
        frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), frozenset(), 0
    ),
    "sudo": (
        frozenset(
            {
                "-u", "--user", "-g", "--group", "-C", "--close-from", "-D", "--chdir",
                "-h", "--host", "-p", "--prompt", "-r", "--role", "-t", "--type",
                "-U", "--other-user", "-T", "--command-timeout",
            }
        ),
        frozenset(
            {
                "-E", "--preserve-env", "-H", "--set-home", "-n", "--non-interactive",
                "-S", "--stdin", "-b", "--background", "-i", "--login", "-s", "--shell",
                "-k", "--reset-timestamp", "-P", "--preserve-groups", "-A", "--askpass",
            }
        ),
        0,
    ),
    "timeout": (
        frozenset({"-k", "--kill-after", "-s", "--signal"}),
        frozenset({"--foreground", "--preserve-status", "-v", "--verbose"}),
        1,
    ),
    "setsid": (frozenset(), frozenset({"-c", "--ctty", "-f", "--fork", "-w", "--wait"}), 0),
    "ionice": (
        frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid",
                   "-u", "--uid"}),
        frozenset({"-t", "--ignore"}),
        0,
    ),
    "flock": (
        frozenset({"-w", "--timeout", "--wait", "-E", "--conflict-exit-code"}),
        frozenset(
            {
                "-s", "--shared", "-x", "-e", "--exclusive", "-u", "--unlock", "-n", "--nb",
                "--nonblock", "-o", "--close", "-F", "--no-fork", "--verbose",
            }
        ),
        1,
    ),
}  # fmt: skip
# Options after which a wrapper runs nothing (it prints or checks instead).
NO_RUN_OPTIONS = {"command": frozenset({"-v", "-V"})}
SCRIPT_VALUE_FLAGS = frozenset(
    {"-E", "--echo", "-I", "--log-in", "-O", "--log-out", "-B", "--log-io", "-T",
     "--log-timing", "-m", "--logging-format", "-o", "--output-limit"}
)  # fmt: skip
RE_NICE_ADJUST = re.compile(r"^-\d+$")  # `nice -5 cmd`: the historical adjustment form
RE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


# Flags of `env` / `uv run` that take the next word as their value (the `--flag=value`
# form is a single word). Any other flag is unknown: it may hide a value, so a command
# behind it that names a sealer is blocked (fail-closed, see BashAnalyzer.run).
ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})
ENV_FLAGS = frozenset({"-i", "--ignore-environment", "-0", "--null", "-v", "--debug", "-"})
UV_RUN_VALUE_FLAGS = frozenset(
    {
        "-p", "--python", "-w", "--with", "--with-editable", "--with-requirements",
        "--project", "--directory", "--package", "--extra", "--group", "--only-group",
        "--no-group", "--env-file", "--index", "--default-index", "--index-url",
        "--extra-index-url", "-f", "--find-links", "--cache-dir", "--config-file",
        "--python-preference", "--color",
    }
)  # fmt: skip
UV_RUN_FLAGS = frozenset(
    {
        "--no-project", "--isolated", "--frozen", "--locked", "--no-sync", "--all-extras",
        "--no-dev", "--dev", "-q", "--quiet", "-v", "--verbose", "--offline", "--no-cache",
        "--active", "--no-env-file",
    }
)  # fmt: skip
OPAQUE_FLAGS = frozenset({"-S", "--split-string"})  # env: the value is the command line
SEALER_NAMES = (
    r"(_legacy|_guard|_verdict|_oracle|_contract|_gate_run|legacy_inventory|review_with_codex)\b"
)
RE_SEALER_NAME = re.compile(r"(^|[\s/'\"=])" + SEALER_NAMES)
RE_SEALER_ANYWHERE = re.compile(SEALER_NAMES)


def _skip_flags(
    words: list[str], i: int, value_flags: frozenset[str], flags: frozenset[str]
) -> tuple[int, bool]:
    """Skip the flags of a wrapper from words[i]; True when one of them is unknown."""
    unknown = False
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "--":
            return i + 1, unknown
        flag = words[i].split("=", 1)[0]
        unknown |= flag in OPAQUE_FLAGS or words[i].startswith("-S")  # value is a command
        if "=" not in words[i] and flag in value_flags:
            i += 2
        else:
            unknown |= flag not in value_flags and flag not in flags
            i += 1
    return i, unknown


def strip_wrappers_checked(words: list[str]) -> tuple[list[str], bool]:
    """Drop env assignments and transparent wrappers (env, command, uv run, …).
    Also says whether a wrapper flag was unknown (its value may have been taken as the
    command). `uv run -m mod` / `--module mod` becomes `python -m mod`."""
    i, unknown = 0, False
    while i < len(words):
        word = words[i]
        name = os.path.basename(word)
        if RE_ASSIGNMENT.match(word) or name in WRAPPERS:
            i += 1
        elif name == "env":
            i, unsure = _skip_flags(words, i + 1, ENV_VALUE_FLAGS, ENV_FLAGS)
            unknown |= unsure
        elif name == "uv" and words[i + 1 : i + 2] == ["run"]:
            i, unsure = _skip_flags(
                words, i + 2, UV_RUN_VALUE_FLAGS, UV_RUN_FLAGS | {"-m", "--module"}
            )
            unknown |= unsure
            if words[i - 1 : i] in (["-m"], ["--module"]):
                return ["python", "-m", *words[i:]], unknown
        elif name in OPTION_WRAPPERS:
            body = _wrapper_body(name, words[i + 1 :])
            if body is not None:  # `flock … -c CMD` / `script -c CMD`: a shell string
                return ["sh", "-c", body], unknown
            start = i + 1
            i, unsure = _wrapper_options(name, words, start)
            unknown |= unsure
            if NO_RUN_OPTIONS.get(name, frozenset()) & set(words[start:i]):
                return [], unknown  # `command -v git`: prints, runs nothing
        elif name == "script":
            body = _wrapper_body(name, words[i + 1 :])
            return (["sh", "-c", body] if body is not None else words[i:]), unknown
        else:
            break
    return words[i:], unknown


def _wrapper_options(name: str, words: list[str], i: int) -> tuple[int, bool]:
    """Skip a wrapper's options and leading operands from words[i] (I8)."""
    value_flags, flags, operands = OPTION_WRAPPERS[name]
    unknown = False
    while i < len(words) and name == "nice" and RE_NICE_ADJUST.match(words[i]):
        i += 1
    letters = {f[1] for f in value_flags if len(f) == 2}  # short options taking a value
    while i < len(words) and words[i].startswith("-") and words[i] != "--":
        word = words[i]
        flag = word.split("=", 1)[0]
        if word.startswith("--") or len(word) == 2:
            unknown |= flag not in value_flags | flags
            i += 2 if "=" not in word and flag in value_flags else 1
            continue
        # a bundle (`-vk 1`, `-o0`, `-Eu dev`): the first value option takes the rest
        # of the word, or the next word when it ends the bundle
        at = next((n for n, ch in enumerate(word[1:], 2) if ch in letters), None)
        unknown |= any(f"-{ch}" not in value_flags | flags for ch in word[1 : at or None])
        i += 2 if at is not None and at == len(word) else 1
    i += 1 if i < len(words) and words[i] == "--" else 0
    if name == "flock" and i == len(words) - 1 and words[i].isdigit():
        return len(words), unknown  # `flock FD`: locks a descriptor, runs nothing
    return min(i + operands, len(words)), unknown


def _wrapper_body(name: str, args: list[str]) -> str | None:
    """The command string of `flock [opts] FILE -c CMD` and `script [opts] -c CMD`."""
    if name not in ("flock", "script"):
        return None
    value_flags = OPTION_WRAPPERS["flock"][0] if name == "flock" else SCRIPT_VALUE_FLAGS
    letters = {f[1] for f in value_flags if len(f) == 2}  # short options taking a value
    operand, i = False, 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg.startswith("--"):
            body, i = _long_option(args, i, value_flags)
            if body is not None:
                return body
            continue
        if len(arg) > 1 and arg[0] == "-":  # a short-option bundle
            kind, rest = _short_bundle(arg, letters)
            if kind == "c":
                return rest if rest else (args[i + 1] if i + 1 < len(args) else None)
            i += 2 if kind == "value" and not rest else 1
            continue
        if not arg.startswith("-"):
            if operand or name == "script":
                break  # flock: the command after FILE; script: its log file
            operand = True
        i += 1
    return None


def _long_option(args: list[str], i: int, value_flags: frozenset[str]) -> tuple[str | None, int]:
    """(the command of `--command CMD` / `--command=CMD`, else None; the next index) for
    the long option args[i]. `--timeout 5`: the next word is its value, not an operand."""
    arg = args[i]
    if arg == "--command":
        return (args[i + 1] if i + 1 < len(args) else None), i + 2
    if arg.startswith("--command="):
        return arg.split("=", 1)[1], i + 1
    return None, i + (2 if "=" not in arg and arg in value_flags else 1)


def _short_bundle(arg: str, value_letters: set[str]) -> tuple[str, str]:
    """(kind, attached text) of a short-option bundle such as `-qc'cmd'` or `-w5`:
    kind "c" (the command option), "value" (an option taking a value) or "flags"."""
    for idx, ch in enumerate(arg[1:], start=2):
        if ch == "c":
            return "c", arg[idx:]
        if ch in value_letters:
            return "value", arg[idx:]
    return "flags", ""


def strip_wrappers(words: list[str]) -> list[str]:
    """Drop env assignments and transparent wrappers (env, command, uv run, …)."""
    return strip_wrappers_checked(words)[0]


# ── Bash guard: analysis ─────────────────────────────────────────────────────


@dataclass
class Findings:
    commits: bool = False
    publishes: bool = False  # merge / PR: attribution applies
    mentions_protected: bool = False
    warnings: dict[str, str] = field(default_factory=dict)  # I8: one per operation

    def merge(self, other: Findings) -> None:
        self.commits |= other.commits
        self.publishes |= other.publishes
        self.warnings.update(other.warnings)


GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
ANY_ARG_VERBS = {
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "truncate",
    "touch",
    "mkdir",
    "chmod",
    "chown",
    "chgrp",
    "tee",
    "mv",
}
DESTRUCTIVE_VERBS = {"rm", "rmdir", "shred", "mv"}
DEST_ARG_VERBS = {"cp", "install", "ln", "rsync"}
INPLACE_VERBS = {"sed", "perl"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
INTERPRETERS = re.compile(r"^(python[0-9.]*|perl|ruby|node|deno|php)$")
RE_INPLACE_FLAG = re.compile(r"^(-[a-zA-Z]*i.*|--in-place.*)$")
RE_MENTIONS_STATE = (
    r"tests/acceptance|progress/(plan_review|review|phase_review|roadmap_review|acceptance_|plan_"
    r"|pr_|legacy_inventory|issue_review|issue_draft_review|verdict_|review_history/"
    r"|task_review|task_close|phase_close|task_flow_legacy"
    r"|issue_migrations_|oracle_migrations/|close_|[^/\s'\"]*\.sha256)|\.harness\b"
    r"|\.git/harness-"
)
RE_WRITE_CALL = re.compile(
    r"\b(write|write_text|write_bytes|unlink|remove|rename|replace|rmtree|truncate|chmod|"
    r"makedirs|mkdir|touch|copy|copyfile|copy2|move|symlink|rmdir)\b"
    r"|open\([^)]*,\s*['\"][rbt]*[wax+][rbtwax+]*['\"]"  # a whole mode string: 'w', 'rb+'
    r"|open\([^)]*,\s*['\"](?:>>?|\+<)"  # perl: open(F, ">path"), open(F, '>', $p)
    r"|\.open\(|\bmode\s*="  # Path(p).open(...), open(p, mode=…)
    # Node fs (also the Sync and promise forms)
    r"|\b(writeFile|appendFile|createWriteStream|rm|unlink|rename|copyFile|cp|symlink|mkdir"
    r"|chmod|truncate|rmdir)(Sync)?\s*\("
)
# Unmatched glob targets (I3b B) are matched lexically against these templates, one
# fnmatch pattern per component; `**` spans any number of components. Keep in sync with
# RE_CODEX_OWNED / classify / HARNESS_ENTRIES.
PROTECTED_TEMPLATES = (
    *(
        f"progress/{n}_*.md"
        for n in (
            "plan_review",
            "review",
            "phase_review",
            "issue_review",
            "task_review",
            "task_close",
            "phase_close",
        )
    ),
    "progress/task_flow_legacy_*.json",
    "progress/roadmap_review.md",
    "progress/issue_draft_review.md",
    "progress/legacy_inventory_review.md",
    "progress/legacy_inventory.json",
    "progress/oracle_contract_review.md",
    "progress/oracle_baseline.json",
    "progress/config_contract_review.md",
    "progress/contract_baseline.json",
    "progress/review_history/*.md",
    "progress/pr_*.md",
    "progress/issue_migrations_*.json",
    "progress/oracle_migrations/*.json",
    "progress/*.sha256",
    "progress/close_*.msg",
    ".git/harness-*/**",
    ".harness/**",
    "**/tests/acceptance/**",
)

SEALERS: dict[str, frozenset[str]] = {
    "review_with_codex.sh": frozenset({"seal"}),
    "_guard.py": frozenset({"seal"}),
    "legacy_inventory.sh": frozenset({"create", "review", "seal"}),
    "_legacy.py": frozenset({"create", "review", "seal"}),
    "_verdict.py": frozenset({"begin", "commit", "abort", "receipt"}),
    # Code-gate attempt records (issue #39): written only by review_with_codex.sh code.
    "_gate_run.py": frozenset({"record", "approve", "drop"}),
    # I4b (issue #65): the baseline, its contract reseal and the plan gate's red record.
    "_oracle.py": frozenset(
        {
            "baseline-create",
            "baseline-seal",
            "contract-review",
            "contract-approve",
            "red-record",
            "red-drop",
        }
    ),
    # I5 (issue #66): the verification contract baseline and its reseal.
    "_contract.py": frozenset(
        {"baseline-create", "baseline-seal", "contract-review", "contract-approve"}
    ),
    # Attempt ledger (issue #46): an attempt is begun/ended only by the harness step, so
    # Claude cannot reset the budget. `budget` and the read-only next action stay open.
    "_resume.py": frozenset({"begin", "end"}),
}


PY_NOVALUE_FLAGS = frozenset("bBdEhiIOPqsSuvVx?R")
PY_VALUE_FLAGS = frozenset("XW")
PY_LONG_VALUE_FLAGS = frozenset({"--check-hash-based-pycs"})
PY_LONG_FLAGS = frozenset({"--help", "--version", "--help-env", "--help-xoptions", "--help-all"})


def _py_short(arg: str) -> tuple[str, str]:
    """Classify one combined short-flag token (`-IBm_legacy`).

    Returns (kind, rest): kind is "m"/"c" (rest = attached value, may be empty),
    "value" (an X/W flag; rest = attached value), "flags" or "unknown"."""
    for j, ch in enumerate(arg[1:], start=1):
        if ch in ("m", "c"):
            return ch, arg[j + 1 :]
        if ch in PY_VALUE_FLAGS:
            return "value", arg[j + 1 :]
        if ch not in PY_NOVALUE_FLAGS:
            return "unknown", ""
    return "flags", ""


def _py_long(arg: str) -> int:
    """Tokens a long python option consumes, or 0 if it is unknown."""
    key = arg.split("=", 1)[0]
    if key in PY_LONG_VALUE_FLAGS:
        return 1 if "=" in arg else 2
    return 1 if key in PY_LONG_FLAGS else 0


def _python_module(args: list[str]) -> tuple[str | None, list[str], bool]:
    """Parse python's options as CPython does: return (module of -m, its args, unsure).

    Short flags may be combined (`-IBm_legacy`); `m` takes the rest of the token or the
    next one; `X`/`W` take a value likewise. An unknown flag makes the result unsure."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-", "--") or not arg.startswith("-"):
            return None, [], False  # a script, stdin, or end of options
        if arg.startswith("--"):
            step = _py_long(arg)
            if not step:
                return None, [], True
            i += step
            continue
        kind, rest = _py_short(arg)
        if kind == "unknown":
            return None, [], True
        if kind in ("m", "c"):
            if not rest and i + 1 >= len(args):
                return None, [], False
            value, tail = (rest, args[i + 1 :]) if rest else (args[i + 1], args[i + 2 :])
            return (value, tail, False) if kind == "m" else (None, [], False)
        i += 2 if kind == "value" and not rest else 1
    return None, [], False


def _is_seal(name: str, args: list[str]) -> bool:
    """A sealing command, run directly, through a shell/python or as `python -m`: `seal` of
    review_with_codex.sh or _guard.py, and `create|review|seal` of the legacy
    inventory (legacy_inventory.sh / _legacy.py; `verify` stays allowed)."""
    if name in SEALERS:
        return bool(args) and args[0] in SEALERS[name]
    if name.startswith("python") and INTERPRETERS.match(name):
        module, tail, unsure = _python_module(args)  # python -IBm_legacy seal
        if unsure and RE_SEALER_ANYWHERE.search(" ".join(args)):  # also -m_legacy
            return True  # fail-closed: an unknown python flag may hide the module form
        script = f"{module.rpartition('.')[2]}.py" if module is not None else ""
        if script in SEALERS:  # also a dotted name: python -m lib._verdict
            return bool(tail) and tail[0] in SEALERS[script]
    if name in SHELLS or INTERPRETERS.match(name):
        return any(
            i + 1 < len(args) and args[i + 1] in SEALERS[os.path.basename(a)]
            for i, a in enumerate(args)
            if os.path.basename(a) in SEALERS
        )
    return False


RE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
XARGS_OPERAND = "__XARGS_OPERAND__"  # never assigned: an operand xargs reads from stdin
XARGS_VALUE_FLAGS = frozenset({"-n", "-L", "-s", "-d", "-E", "-P", "-a"})
XARGS_LONG_VALUE_FLAGS = frozenset(
    {"--arg-file", "--delimiter", "--max-args", "--max-lines", "--max-chars", "--max-procs",
     "--eof", "--process-slot-var"}
)  # fmt: skip


def _inplace_files(args: list[str]) -> list[str]:
    """The files `sed -i` / `perl -i` edit: the operands, minus the script. The script is
    the value of -e/-f (or --expression/--file), or else the first operand."""
    files: list[str] = []
    script_given, i = False, 0
    while i < len(args):
        arg = args[i]
        if arg in ("-e", "-f", "--expression", "--file"):
            script_given, i = True, i + 2
            continue
        if arg.startswith(("--expression=", "--file=")):
            script_given, i = True, i + 1
            continue
        if re.match(r"-[a-zA-Z]*[ef]", arg):
            # a bundle ending in e/f (`-pie`) takes the next word; `-eSCRIPT` carries it.
            # `-ie` (GNU sed: suffix "e") is read as a script too: stricter, never looser.
            script_given = True
            i += 2 if re.fullmatch(r"-[a-zA-Z]*[ef]", arg) else 1
            continue
        if not arg.startswith("-"):
            files.append(arg)
        i += 1
    return files if script_given else files[1:]


def _shell_body(args: list[str]) -> int | None:
    """Index of the command string of `bash|sh|… [options] -c… string`, or None.
    `c` may sit in any short-flag bundle (`-lc`, `-ec`, `-xc`, `-e -c`)."""
    has_c, i = False, 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
        elif arg.startswith("--"):
            i += 1
        elif len(arg) > 1 and arg[0] in "-+":
            has_c |= arg[0] == "-" and "c" in arg[1:]
            i += 1
        else:
            break
    return i if has_c and i < len(args) else None


def _xargs_options(args: list[str]) -> tuple[int, str | None]:
    """(index of the verb, replacement string of -I/-i/--replace or None)."""
    i, replace = 0, None
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        if arg == "--":
            return i + 1, replace
        if arg.startswith("-I"):
            replace = arg[2:] or (args[i + 1] if i + 1 < len(args) else "")
            i += 1 if arg[2:] else 2
        elif arg.startswith("-i") or arg.startswith("--replace"):
            replace = (arg[2:] if arg.startswith("-i") else arg.partition("=")[2]) or "{}"
            i += 1
        elif arg in XARGS_VALUE_FLAGS or (arg in XARGS_LONG_VALUE_FLAGS and "=" not in arg):
            i += 2
        else:
            i += 1
    return i, replace


# ── Unmatched globs: lexical matching against protected / harness names (I3b B) ──


def _glob_tokens(pattern: str) -> list[str]:
    tokens, i = [], 0
    while i < len(pattern):
        if pattern[i] == "[":
            end = pattern.find("]", i + 2)
            if end > 0:
                tokens.append(pattern[i : end + 1])
                i = end + 1
                continue
        tokens.append(pattern[i])
        i += 1
    return tokens


def _token_meet(a: str, b: str) -> bool:
    if a == "?" or b == "?" or (a.startswith("[") and b.startswith("[") and len(a) > 1):
        return True
    if a.startswith("[") and len(a) > 1:
        return fnmatch.fnmatchcase(b, a)
    if b.startswith("[") and len(b) > 1:
        return fnmatch.fnmatchcase(a, b)
    return a == b


def _glob_meet(a: str, b: str) -> bool:
    """Some string matches both fnmatch patterns `a` and `b` (one path component)."""
    ta, tb = _glob_tokens(a), _glob_tokens(b)

    @cache
    def go(i: int, j: int) -> bool:
        if i == len(ta) and j == len(tb):
            return True
        if i < len(ta) and ta[i] == "*":
            return go(i + 1, j) or (j < len(tb) and go(i, j + 1))
        if j < len(tb) and tb[j] == "*":
            return go(i, j + 1) or (i < len(ta) and go(i + 1, j))
        if i == len(ta) or j == len(tb):
            return False
        return _token_meet(ta[i], tb[j]) and go(i + 1, j + 1)

    return go(0, 0)


def _path_meet(parts: tuple[str, ...], template: tuple[str, ...]) -> bool:
    """A path matching the pattern components `parts` can match `template` (`**` in the
    template spans any number of components)."""

    @cache
    def go(i: int, j: int) -> bool:
        if j == len(template):
            return i == len(parts)
        if template[j] == "**":
            return go(i, j + 1) or (i < len(parts) and go(i + 1, j))
        return i < len(parts) and _glob_meet(parts[i], template[j]) and go(i + 1, j + 1)

    return go(0, 0)


@cache
def _template_parts(template: str) -> tuple[str, ...]:
    return tuple(template.split("/"))


def _protected_templates() -> tuple[str, ...]:
    dirs = src_dirs()
    sources = (
        tuple(f"{d}/**" for d in dirs)
        if dirs is not None
        else tuple(f"**/{d}/**" for d in DEFAULT_SRC_DIRS)
    )
    return (*PROTECTED_TEMPLATES, CONFIG_FILE, *sources)


def _harness_templates() -> tuple[str, ...]:
    return tuple(f"{e}/**" for e in HARNESS_ENTRIES)


def _executed_harness(command: str, depth: int = 0) -> list[str]:
    """Spellings of harness paths the line runs as a command or as a script operand
    of a shell/interpreter, including inside `$(…)`."""
    found: list[str] = []
    if depth > 4:
        return found
    for cmd in _shell.split(command):
        for word in (*cmd.words, *cmd.out_targets, *cmd.in_targets):
            for part in word.parts:
                if part.kind in (_shell.CMD, _shell.PROC):
                    found += _executed_harness(part.text, depth + 1)
        words = strip_wrappers([w.raw for w in cmd.words if not RE_ASSIGNMENT.match(w.raw)])
        if not words:
            continue
        name = os.path.basename(words[0])
        if name in SHELLS or INTERPRETERS.match(name):
            script = _script_operand(name, words[1:])
            runs = [script] if script is not None else []
        else:
            runs = [words[0]]
        found += [w for w in runs if _mentions_harness(w)]
    return found


def _script_operand(name: str, args: list[str]) -> str | None:
    """The script file a shell or interpreter runs, past its options; None for
    `-c`/`-m`/stdin or options the guard cannot parse."""
    if name in SHELLS:
        return _shell_script(args)
    if name.startswith("python"):
        return _python_script(args)
    return args[0] if args and not args[0].startswith("-") else None


def _shell_script(args: list[str]) -> str | None:
    if _shell_body(args) is not None:
        return None
    i = 0
    while i < len(args) and args[i][:1] in "-+" and args[i] != "--":
        if args[i].startswith("-") and "n" in args[i][1:] and not args[i].startswith("--"):
            return None  # -n: syntax check only, nothing runs
        i += 2 if args[i] in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file") else 1
    i += 1 if i < len(args) and args[i] == "--" else 0
    return args[i] if i < len(args) else None


def _python_script(args: list[str]) -> str | None:
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-h", "-?", "--help", "--help-all", "-V", "--version"):
            return None  # prints and exits: the script never runs
        if arg == "--" or not arg.startswith("-"):
            script = i + 1 if arg == "--" else i
            return args[script] if script < len(args) else None
        step = _py_long(arg) if arg.startswith("--") else 0
        kind, rest = ("long", "") if step else _py_short(arg)
        if arg == "-" or kind in ("c", "m", "unknown") or (arg.startswith("--") and not step):
            return None
        i += step or (2 if kind == "value" and not rest else 1)
    return None


def _mask_executed(text: str, executed: list[str]) -> str:
    """Blank one whole-word occurrence of `text` per executed harness path. Any other
    occurrence (an argument, a string) still counts as a mention."""
    for spelled in executed:
        text = re.sub(rf"(?<![\w./-]){re.escape(spelled)}(?![\w./-])", " ", text, count=1)
    return text


class BashAnalyzer:
    """Walks the simple commands of one Bash call, tracking `cd` and variables per shell
    frame (I3b), and raises Blocked."""

    def __init__(self, command: str) -> None:
        self.raw = command
        self.frame = Frame(ROOT)
        self.found = Findings()
        self._dynamic: set[str] = set()  # spellings of the current command's dynamic words
        self._stdin: list[str] = []  # here-doc / here-string bodies of the current command
        self._stdin_expands: list[bool] = []  # per body: Bash expands `$…` in it
        self._stdin_code: str | None = None  # the stdin code _python_code returned
        # I8: git config set earlier on this line (key -> value; None: unknown value)
        self._git_config: dict[str, str | None] = {}
        # I8: a script read for its commits keeps going past a blocked write
        self.lenient = False
        self._wrapped_cwd = False  # the current command runs where a wrapper put it
        self._git_config_files: set[str] = set()  # `git config -f FILE` writes (I8)

    @property
    def cwd(self) -> Path:
        return self.frame.cwd

    @cwd.setter
    def cwd(self, value: Path) -> None:
        self.frame.cwd = value

    def run(self, command: str | None = None, depth: int = 0) -> Findings:
        if depth > 4:
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd="deeply nested shell"))
        commands, unsure = _shell.split_checked(self.raw if command is None else command)
        if unsure or (command is not None and _shell.RE_UNSURE_BODY.search(command)):
            self._lenient_call(self._unfollowed)
        for simple in commands:
            self._lenient_call(partial(self._simple, simple, depth))
        return self.found

    def _lenient_call(self, call: Callable[[], None]) -> None:
        """Run one analysis step; a lenient analyser skips a step's write block."""
        try:
            call()
        except Blocked:
            if not self.lenient:
                raise

    def _unfollowed(self) -> None:
        """Fail closed like D1: a construct the lexer may misread, on a call that names
        a protected or harness path."""
        what = "shell construct the guard cannot follow"
        if _mentions_harness(_mask_executed(self.raw, _executed_harness(self.raw))):
            raise Blocked(MSG_HARNESS.format(target=what))
        if _mentions_protected(self.raw):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=what))

    def _nested(self, body: str, depth: int, everything: bool, cwd: Path | None = None) -> None:
        """Analyse `body` in its own frame: its `cd` and assignments do not leak."""
        saved, dynamic, stdin = self.frame, self._dynamic, (self._stdin, self._stdin_expands)
        self.frame = saved.child(everything)
        if cwd is not None:
            self.frame.cwd, self.frame.cwd_dynamic = cwd, False
        try:
            self.run(body, depth + 1)
        finally:
            self.frame, self._dynamic, (self._stdin, self._stdin_expands) = saved, dynamic, stdin

    def _word(self, word: _shell.Word, depth: int) -> tuple[str, bool]:
        """(text, dynamic): the word expanded in the current frame. Substitutions are
        analysed as nested commands; a dynamic word keeps its spelling."""
        text: list[str] = []
        dynamic = False
        for part in word.parts:
            if part.kind in (_shell.LIT, _shell.QUOTED):
                text.append(part.text)
            elif part.kind == _shell.VAR:
                value = self.frame.lookup(part.text) if part.text else None
                dynamic |= value is None
                text.append("" if value is None else value)
            else:  # CMD / PROC: substitutions see every variable of the parent (C)
                self._nested(part.text, depth, everything=True)
                dynamic = True
        return (word.raw, True) if dynamic else ("".join(text), False)

    def _words(
        self, simple: _shell.Command, depth: int, frame: Frame
    ) -> tuple[list[str], set[str], dict[str, str | None]]:
        """(words, dynamic spellings, prefix assignments). Prefix assignments go to a
        temporary frame that the rest of the command (and its children) sees."""
        words: list[str] = []
        dynamic: set[str] = set()
        prefix: dict[str, str | None] = {}
        for word in simple.words:
            text, is_dynamic = self._word(word, depth)
            if word.is_proc:  # <(…) / >(…) operand: a nested command, not a file
                continue
            if not words and RE_ASSIGNMENT.match(word.raw):
                name, _, value = (word.raw if is_dynamic else text).partition("=")
                prefix[name] = None if is_dynamic else value
                if self.frame is frame:  # later prefix words see it (`D=x F=$D/y cmd`)
                    self.frame = frame.child(everything=True)
                self.frame.vars[name] = prefix[name]
                self.frame.exported.add(name)
            else:
                words.append(text)
                if is_dynamic:
                    dynamic.add(text)
        return words, dynamic, prefix

    def _targets(self, simple: _shell.Command, depth: int) -> list[tuple[str, bool]]:
        """Expanded output targets; `> >(cmd)` is analysed as a command, not a file."""
        for word in simple.in_targets:
            self._word(word, depth)
        targets = [(word, self._word(word, depth)) for word in simple.out_targets]
        return [expanded for word, expanded in targets if not word.is_proc]

    def _simple(self, simple: _shell.Command, depth: int) -> None:
        frame = self.frame
        words, dynamic, prefix = self._words(simple, depth, frame)
        targets = self._targets(simple, depth)
        if not words:  # a plain assignment statement: it stays in this shell
            self.frame = frame
            frame.vars.update(prefix)
        self._dynamic = dynamic | {t for t, d in targets if d}
        self._stdin = simple.stdin
        self._stdin_expands = simple.stdin_expands
        for body, expands in zip(simple.stdin, simple.stdin_expands, strict=True):
            for inner in _shell.substitutions(body) if expands else ():
                self._nested(inner, depth, everything=True)  # Bash runs them first
        try:
            for target, _ in targets:
                self._check(target, destructive=False)
            if words:
                stripped, unsure = strip_wrappers_checked(words)
                self._wrapped_cwd = any(  # I8: a wrapper moved the command elsewhere
                    w.split("=", 1)[0] in WRAPPER_CHDIR or w.startswith("-C")
                    for w in words[: len(words) - len(stripped)]
                )
                if unsure and RE_SEALER_NAME.search(" ".join(words)):
                    raise Blocked(MSG_SEAL)  # fail-closed: an unknown wrapper flag may hide it
                if stripped:
                    self._command(stripped, depth)
        finally:
            if self.frame is not frame:  # prefix assignments end with the command
                frame.cwd, frame.cwd_dynamic = self.frame.cwd, self.frame.cwd_dynamic
                self.frame = frame

    # ── targets ──────────────────────────────────────────────────────────────

    def _check(self, raw: str, destructive: bool) -> None:
        if RE_GIT_CONFIG_FILE.search(raw):
            self._git_config["include.path"] = None  # its aliases are unknown from here on
        relative = not os.path.isabs(os.path.expanduser(raw))
        if raw in self._dynamic or (self.frame.cwd_dynamic and relative):
            self._unresolved(raw)
            return
        for spelled in self._expand(raw):
            kind = protected_kind(resolve(spelled, self.cwd), destructive)
            if kind:
                raise Blocked(MSG_SHELL_WRITE.format(target=raw, kind=kind))
            if harness_kind(spelled, self.cwd, destructive):
                raise Blocked(MSG_HARNESS.format(target=raw))
        if any(ch in raw for ch in "*?[") and not glob.glob(raw, root_dir=self.cwd):
            self._unmatched_glob(raw)

    def _unresolved(self, raw: str, text: str | None = None) -> None:
        """D1: a target the guard cannot resolve blocks only when the call (or the
        interpreter code) names a harness or protected path. Running a harness script
        (`./harness/x.sh`, `python3 harness/x.py`) does not count as naming it."""
        text = self.raw if text is None else text
        if _mentions_harness(_mask_executed(text, _executed_harness(self.raw))):
            raise Blocked(MSG_HARNESS.format(target=raw))
        if _mentions_protected(text):
            raise Blocked(MSG_SHELL_WRITE.format(target=raw, kind="unresolved target"))

    def _unmatched_glob(self, raw: str) -> None:
        """B: a pattern with no match blocks when it could name a protected or harness path."""
        lexical = _lexical(raw, self.cwd)
        rel = project_rel(lexical)
        if rel is not None:
            parts = tuple(Path(rel).parts)
            if any(_path_meet(parts, _template_parts(t)) for t in _harness_templates()):
                raise Blocked(MSG_HARNESS.format(target=raw))
            if any(_path_meet(parts, _template_parts(t)) for t in _protected_templates()):
                raise Blocked(
                    MSG_SHELL_WRITE.format(target=raw, kind="pattern of a protected path")
                )
        home = Path(os.path.abspath(_upgrade._claude_home()))
        if lexical.is_relative_to(home):
            parts = tuple(lexical.relative_to(home).parts)
            for name in HARNESS_GLOBAL:
                if _path_meet(parts, (name, "**")):
                    raise Blocked(MSG_HARNESS.format(target=raw))

    def _expand(self, raw: str) -> Iterator[str]:
        """The spellings `raw` stands for: its glob matches, or itself."""
        if any(ch in raw for ch in "*?["):
            matches = glob.glob(raw, root_dir=self.cwd)
            if matches:
                yield from matches
                return
        yield raw

    # ── commands ─────────────────────────────────────────────────────────────

    def _command(self, words: list[str], depth: int) -> None:
        name, args = os.path.basename(words[0]), words[1:]
        handler = {
            "cd": self._cd,
            "git": self._git,
            "gh": self._gh,
            "find": self._find,
            "xargs": self._xargs,
            "dd": self._dd,
        }.get(name)
        self.found.mentions_protected |= any(self._is_protected(a) for a in args)
        body = _shell_body(args) if name in SHELLS else None
        self._scripts(name, words, body, depth)
        if handler:
            handler(args, depth)
        elif body is not None:
            if args[body] in self._dynamic:
                self._unresolved(args[body])
            self._nested(args[body], depth, everything=False)
        elif name == "eval":
            self.run(" ".join(args), depth + 1)
        elif name in ("patch",):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=name))
        elif _is_seal(name, args):
            raise Blocked(MSG_SEAL)
        elif name in DECLARERS or name in ("unset", "read", "printf", "for", "select"):
            self._variables(name, args)
            self._file_verbs(name, args)
        elif name in ("source", "."):
            self.frame.tainted = True
        else:
            self._file_verbs(name, args)

    def _scripts(self, name: str, words: list[str], body: int | None, depth: int) -> None:
        """I8: the project shell script this command runs, if any."""
        args = words[1:]
        if name in SHELLS and body is None and (script := _shell_script(args)) is not None:
            self._script_file(script, depth)
        elif name in ("source", ".") and args:
            self._script_file(args[0], depth, sourced=True)
        elif "/" in words[0] and name not in SHELLS and not INTERPRETERS.match(name):
            self._script_file(words[0], depth, direct=True)

    def _variables(self, name: str, args: list[str]) -> None:
        """Track declarations and the builtins that set a variable at runtime."""
        frame = self.frame
        if name in DECLARERS:
            self._declare(name, args)
        elif name == "unset":
            for arg in args:
                frame.vars.pop(arg, None)
        elif name == "read":
            for arg in (a for a in args if RE_NAME.fullmatch(a)):
                frame.vars[arg] = None
        elif name == "printf" and "-v" in args[:-1]:
            frame.vars[args[args.index("-v") + 1]] = None
        elif name in ("for", "select") and args and RE_NAME.fullmatch(args[0]):
            frame.vars[args[0]] = None

    def _declare(self, name: str, args: list[str]) -> None:
        """export / declare / typeset / local / readonly NAME[=value] (-x exports)."""
        export = name == "export" or any(a.startswith("-") and "x" in a for a in args)
        for arg in (a for a in args if not a.startswith("-")):
            var, eq, value = arg.partition("=")
            if not RE_NAME.fullmatch(var):
                continue
            if eq:
                self.frame.vars[var] = None if arg in self._dynamic else value
            if export:
                self.frame.exported.add(var)

    def _is_protected(self, arg: str) -> bool:
        return not arg.startswith("-") and any(
            protected_kind(resolve(p, self.cwd), destructive=True)
            or harness_kind(p, self.cwd, destructive=True)
            for p in self._expand(arg)
        )

    def _file_verbs(self, name: str, args: list[str]) -> None:
        operands = [a for a in args if not a.startswith("-")]
        if name in INPLACE_VERBS and any(RE_INPLACE_FLAG.match(a) for a in args):
            for arg in _inplace_files(args):
                self._check(arg, destructive=False)
        elif name in ANY_ARG_VERBS:
            for arg in operands:
                self._check(arg, destructive=name in DESTRUCTIVE_VERBS)
        elif name in DEST_ARG_VERBS:
            for target in self._destinations(name, args):
                self._check(target, destructive=False)
        elif INTERPRETERS.match(name):
            self._interpreter(name, args)

    def _interpreter(self, name: str, args: list[str]) -> None:
        """E: Python code is parsed and its write calls checked; anything else (other
        interpreters, `-m`, code that does not parse) keeps the fail-closed regex rule."""
        code = self._python_code(args) if name.startswith("python") else None
        dynamic = False
        if code is not None and code is self._stdin_code:
            code, dynamic = self._expand_body(code)
        writes = _pywrites.analyze(code) if code is not None else None
        if code is not None and writes is not None:
            for write in writes:
                if dynamic and write.writes is not False:
                    # Bash rewrote the code before Python ran: no target is trusted
                    write = _pywrites.Write(write.op, None, write.writes, write.destructive)
                self._python_write(name, write, code)
            return
        if RE_WRITE_CALL.search(self.raw):
            if _mentions_harness(self.raw):
                raise Blocked(MSG_HARNESS.format(target=name))
            if _mentions_protected(self.raw):
                raise Blocked(MSG_SHELL_OPAQUE.format(cmd=name))

    def _python_write(self, name: str, write: _pywrites.Write, code: str) -> None:
        if write.writes is False:
            return  # a read
        if write.target is None:
            # the call and the code: a target may arrive as an argument (sys.argv)
            self._unresolved(f"{name}: {write.op}(…)", f"{self.raw}\n{code}")
            return
        kind = protected_kind(resolve(write.target, self.cwd), write.destructive)
        if kind:
            raise Blocked(MSG_SHELL_WRITE.format(target=write.target, kind=kind))
        if harness_kind(write.target, self.cwd, write.destructive):
            raise Blocked(MSG_HARNESS.format(target=write.target))

    def _python_code(self, args: list[str]) -> str | None:
        """The code python runs: `-c` text, stdin (`-` or no script) from a here-doc or
        here-string, or a literal script file inside the project. None: not analysable."""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "-":
                break
            if arg == "--" or not arg.startswith("-"):
                script = args[i + 1] if arg == "--" and i + 1 < len(args) else arg
                return self._script(script)
            if arg.startswith("--"):
                step = _py_long(arg)
                if not step:
                    return None
                i += step
                continue
            kind, rest = _py_short(arg)
            if kind == "c":
                return rest or (args[i + 1] if i + 1 < len(args) else None)
            if kind in ("m", "unknown"):
                return None
            i += 2 if kind == "value" and not rest else 1
        if not self._stdin:
            return None
        self._stdin_code = "\n".join(self._stdin)
        return self._stdin_code

    def _expand_body(self, code: str) -> tuple[str, bool]:
        """Expand an unquoted here-doc / here-string the way Bash does before Python
        reads it: `$NAME`/`${NAME}` from the frame. Anything else (`$(…)`, backticks,
        `${X%y}`, an unknown name) leaves the body dynamic."""
        if not any(self._stdin_expands):
            return code, False
        dynamic = "`" in code or "$(" in code or re.search(r"\$[0-9@*#?$!-]", code) is not None

        def sub(match: re.Match[str]) -> str:
            nonlocal dynamic
            value = self.frame.lookup(match.group(1) or match.group(2))
            if value is None:
                dynamic = True
                return match.group(0)
            return value

        code = re.sub(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)", sub, code)
        return code, dynamic or "${" in code

    def _script(self, raw: str) -> str | None:
        if raw in self._dynamic:
            return None
        path = resolve(raw, self.cwd)
        if project_rel(path) is None or not path.is_file():
            return None
        try:
            return path.read_text(errors="replace")
        except OSError:
            return None

    def _destinations(self, name: str, args: list[str]) -> list[str]:
        """The destination of cp/install/ln/rsync, plus `dir/<source name>` for each
        source when it is a directory (`-t`, a trailing `/` or an existing dir).
        A copy whose names cannot be known (any rsync, a `src/.` contents copy) into a
        dir that directly holds a harness entry is blocked outright (fail-closed)."""
        dest, sources, is_dir = _destination(args, target_flag=name != "rsync")
        if not dest:
            return []
        if is_dir or dest.endswith("/") or resolve(dest, self.cwd).is_dir():
            opaque = name == "rsync" or any(s == "." or s.endswith("/.") for s in sources)
            if opaque and self._holds_harness(dest):
                raise Blocked(MSG_HARNESS.format(target=dest))
            base = dest.rstrip("/") or "/"
            names = (os.path.basename(s.rstrip("/")) for s in sources)
            return [dest, *(f"{base}/{n}" for n in names if n)]
        return [dest]

    def _holds_harness(self, raw: str) -> bool:
        """`raw` (as spelled, or through a directory alias) is the directory that
        directly contains a harness entry."""
        spellings = _lstat_spellings(raw, self.cwd) | {resolve(raw, self.cwd)}
        return any(base.parent in spellings for base in _harness_bases())

    def _cd(self, args: list[str], depth: int) -> None:
        target = args[0] if args else str(Path.home())
        if target in self._dynamic:
            self.frame.cwd_dynamic = True
            return
        if os.path.isabs(os.path.expanduser(target)):
            self.frame.cwd_dynamic = False
        self.cwd = resolve(target, self.cwd)

    def _dd(self, args: list[str], depth: int) -> None:
        for arg in args:
            if arg.startswith("of="):
                self._check(arg[3:], destructive=False)

    def _find(self, args: list[str], depth: int) -> None:
        if not {"-delete", "-exec", "-execdir", "-ok", "-okdir"} & set(args):
            return
        roots = []
        for arg in args:
            if arg.startswith(("-", "(", "!")):
                break
            roots.append(arg)
        for root in roots or ["."]:
            self._check(root, destructive=True)
        i = 0
        while i < len(args):
            if args[i] in ("-exec", "-execdir", "-ok", "-okdir"):
                j = i + 1
                while j < len(args) and args[j] not in (";", "+"):
                    j += 1
                self._run_verb(strip_wrappers(args[i + 1 : j]), depth)
                i = j
            i += 1

    def _run_verb(self, words: list[str], depth: int) -> None:
        """I8: the command xargs / find -exec runs, for its commits and publications."""
        if not words:
            return
        verb, verb_args = os.path.basename(words[0]), words[1:]
        if verb == "git":
            i = self._git_globals(verb_args)[0]
            if i >= len(verb_args) or "{" in verb_args[i]:
                self.found.commits = True  # fail closed: the subcommand comes from input
                return
            self._git(verb_args, depth)
        elif verb == "gh":
            self._gh(verb_args, depth)
        elif verb in SHELLS and (body := _shell_body(verb_args)) is not None:
            self._nested(verb_args[body], depth, everything=False)
        else:
            self._scripts(verb, words, None, depth)  # a project script it runs

    def _xargs(self, args: list[str], depth: int) -> None:
        i, replace = _xargs_options(args)
        words = strip_wrappers(args[i:]) or ["echo"]
        verb, verb_args = os.path.basename(words[0]), words[1:]
        body = _shell_body(verb_args) if verb in SHELLS else None
        if body is None:
            self._run_verb(words, depth)  # git / gh / a project script (I8)
        if body is not None:  # C: the shell body runs with operands we cannot know
            text = verb_args[body]
            if replace:
                text = text.replace(replace, "$" + XARGS_OPERAND)
            self._nested(text, depth, everything=False)
            return
        writes = verb in ANY_ARG_VERBS | DEST_ARG_VERBS | INPLACE_VERBS | SHELLS
        if writes and _mentions_harness(self.raw):
            raise Blocked(MSG_HARNESS.format(target=f"xargs {verb}"))
        # xargs takes its operands from stdin, so judge by what the whole line touches.
        if writes and (self.found.mentions_protected or _mentions_protected(self.raw)):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=f"xargs {verb}"))

    def _gh(self, args: list[str], depth: int) -> None:
        if args[:1] == ["pr"] and args[1:2] in (["create"], ["edit"], ["merge"]):
            self.found.publishes = True
            if args[1] != "edit":
                self._warn(f"gh pr {args[1]}", MSG_WARN_PUBLISH)

    def _warn(self, op: str, template: str) -> None:
        self.found.warnings.setdefault(op, template.format(op=op))

    def _git(self, args: list[str], depth: int) -> None:
        i, inline = self._git_globals(args)
        if i >= len(args):
            return
        sub, rest = args[i], args[i + 1 :]
        for _ in range(8):  # an alias may name another alias
            if sub in _git_builtins():
                break  # git ignores an alias that shadows a git command
            alias = self._alias(sub, inline)
            if alias is None:  # fail closed: an alias whose value the guard cannot know
                self.found.commits = True
                return
            if not alias:
                break
            if alias.startswith("!"):  # git runs it from the top of the work tree
                top = self._git_top(inline)
                if top is None:
                    self.found.commits = True  # fail closed: where it runs is not knowable
                    return
                self._nested(alias[1:], depth, everything=False, cwd=top)
                return
            alias_words = _split_alias(alias)
            if not alias_words:
                return
            sub, rest = alias_words[0], alias_words[1:] + rest
        self._git_sub(sub, rest)

    def _git_top(self, inline: dict[str, str | None]) -> Path | None:
        """The work tree top git would run a `!` alias from; None if not knowable."""
        where = inline.get(GIT_WHERE, "")
        overrides = {n: self.frame.vars[n] for n in GIT_LOCATION_VARS if n in self.frame.vars}
        if (
            where is None
            or None in overrides.values()
            or self.frame.cwd_dynamic
            or self._wrapped_cwd
            or not self.cwd.is_dir()
        ):
            return None
        env = {**os.environ, **{n: v for n, v in overrides.items() if v is not None}}
        res = subprocess.run(
            ["git", *(where.split("\0") if where else []), "rev-parse", "--show-toplevel"],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        return Path(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else None

    def _git_globals(self, args: list[str]) -> tuple[int, dict[str, str | None]]:
        """(index of the subcommand, config given by -c / --config-env) (I8). The
        options that pick another repository go to `inline` under GIT_WHERE."""
        inline: dict[str, str | None] = {}
        where: list[str] = []
        i = 0
        while i < len(args) and args[i].startswith("-"):
            opt = args[i]
            if opt in ("-c", "--config-env") and i + 1 < len(args):
                self._git_option(opt, args[i + 1], inline)
            elif opt.startswith("-c") and len(opt) > 2:  # `-cname=value`: one word
                self._git_option("-c", opt[2:], inline)
            elif opt.startswith("--config-env="):
                self._git_option("--config-env", opt.split("=", 1)[1], inline)
            elif opt.split("=", 1)[0] in GIT_WHERE_OPTS:
                value = args[i + 1 : i + 2] if "=" not in opt else []
                if any(w in self._dynamic for w in (opt, *value)):
                    inline[GIT_WHERE] = None
                where += [opt, *value]
            i += 2 if opt in GIT_VALUE_OPTS else 1
        if where and GIT_WHERE not in inline:
            inline[GIT_WHERE] = "\0".join(where)
        return i, inline

    def _git_option(self, opt: str, value: str, inline: dict[str, str | None]) -> None:
        key, has_value, val = value.partition("=")
        if opt == "--config-env":  # the value is the named environment variable's
            inline[key.lower()] = self.frame.lookup(val) if has_value else None
        else:
            inline[key.lower()] = None if value in self._dynamic else (val if has_value else "")

    def _env_config(self) -> dict[str, str | None] | None:
        """Config git takes from GIT_CONFIG_PARAMETERS / GIT_CONFIG_COUNT (frame first,
        then the hook's environment). None: present but not knowable."""

        def get(name: str) -> tuple[bool, str | None]:
            if name in self.frame.vars:
                return True, self.frame.vars[name]
            if self.frame.tainted:
                return name.startswith("GIT_CONFIG"), None
            return (name in os.environ), os.environ.get(name)

        out: dict[str, str | None] = {}
        present, count = get("GIT_CONFIG_COUNT")
        if present:
            if count is None or not count.strip().isdigit():
                return None
            for n in range(int(count)):
                (_, key), (_, val) = get(f"GIT_CONFIG_KEY_{n}"), get(f"GIT_CONFIG_VALUE_{n}")
                if key is None:
                    return None
                out[key.lower()] = val
        present, params = get("GIT_CONFIG_PARAMETERS")  # wins over GIT_CONFIG_COUNT
        if present:
            parsed = _config_parameters(params)
            if parsed is None:
                return None
            out.update(parsed)
        return out

    def _alias(self, sub: str, inline: dict[str, str | None]) -> str | None:
        """The alias `sub` stands for ("" if none); None when its source is unknowable:
        a dynamic value, an include, or a config file written earlier on the line."""
        env = self._env_config()
        if env is None:
            return None
        key = f"alias.{sub}".lower()
        for source in (inline, env, self._git_config):
            if any(k.startswith("include") for k in source):
                return None
            if key in source:
                return source[key]
        where = inline.get(GIT_WHERE, "")
        overrides = {n: self.frame.vars[n] for n in GIT_LOCATION_VARS if n in self.frame.vars}
        unknowable = (
            where is None
            or None in overrides.values()
            or self.frame.cwd_dynamic
            or self._wrapped_cwd
            or not self.cwd.is_dir()
            # a config file written on this line may be the one the variables select
            or bool(self._git_config_files and overrides)
        )
        if unknowable:
            return None  # the config git would read is not knowable
        env_over = {n: v for n, v in overrides.items() if v is not None}
        return _git_alias(sub, self.cwd, where.split("\0") if where else [], env_over)

    def _git_config_write(self, rest: list[str]) -> None:
        """Track `git config [set|--add|…] alias.X VALUE` for the git calls after it."""
        args = rest[1:] if rest[:1] in (["set"], ["--add"]) else rest
        operands: list[str] = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith("-"):
                if arg in (
                    "--unset",
                    "--unset-all",
                    "--get",
                    "--get-all",
                    "--list",
                    "-l",
                    "--get-regexp",
                    "--edit",
                    "-e",
                    "--remove-section",
                    "--rename-section",
                ):
                    if arg in ("--edit", "-e"):
                        self._git_config["include.path"] = None  # opaque edit
                    return
                name, _, attached = arg.partition("=")
                target = attached or (args[i + 1] if i + 1 < len(args) else "")
                if name == "--blob" or (
                    name in ("-f", "--file") and not RE_GIT_CONFIG_FILE.search(target)
                ):
                    if name != "--blob":  # git reads it if a variable names it later
                        self._git_config_files.add(str(resolve(target, self.cwd)))
                    return  # read-only, or a file git does not read for this repo
                i += 2 if arg in GIT_CONFIG_VALUE_OPTS else 1
                continue
            operands.append(arg)
            i += 1
        if len(operands) >= 2:
            key = operands[0].lower()
            if key.startswith(("alias.", "include")):
                self._git_config[key] = None if operands[1] in self._dynamic else operands[1]

    def _commit_op(self, sub: str, rest: list[str]) -> bool:
        """I8: commit / commit-producing / publishing subcommands; True when handled."""
        if sub == "commit":
            self.found.commits = True
        elif sub in COMMIT_OPS:
            self.found.publishes |= sub == "merge"
            if not COMMIT_OPS[sub] & set(rest):
                self._warn(f"git {sub}", MSG_WARN_COMMIT)
        elif sub == "push":
            modes = [a for a in rest if a in PUSH_DRY_RUN or a == "--no-dry-run"]
            if not modes or modes[-1] == "--no-dry-run":  # the last one wins
                self._warn("git push", MSG_WARN_PUBLISH)
        elif sub == "config":
            self._git_config_write(rest)
        else:
            return False
        return True

    def _git_sub(self, sub: str, rest: list[str]) -> None:
        if self._commit_op(sub, rest):
            return
        if sub in ("apply", "am"):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=f"git {sub}"))
        elif sub in ("checkout", "restore", "rm", "mv", "clean"):
            paths = [a for a in rest if not a.startswith("-")]
            if sub == "clean" and not paths:
                paths = ["."]
            for path in paths:
                if sub != "checkout" or "--" in rest or (self.cwd / path).exists():
                    self._check(path, destructive=True)

    def _script_file(
        self, raw: str, depth: int, direct: bool = False, sourced: bool = False
    ) -> None:
        """I8: a project shell script run by this call (`bash f.sh`, `./f.sh`,
        `source f.sh`) is read for its commits and publications. Its file writes are
        left to the integrity backstop, as before (shell-script writes are I3c)."""
        if raw in self._dynamic or _harness_script(resolve(raw, self.cwd)):
            return  # the harness's own scripts make the close commit and the PR
        text = self._script(raw)
        if text is None or "\0" in text:
            return  # not a file in the project, or a binary
        first = text.split("\n", 1)[0]
        if direct and first.startswith("#!") and not re.search(r"\b(ba|z|k|da)?sh\b", first):
            return  # another interpreter: not a shell script
        child = BashAnalyzer(text)
        child.frame = self.frame.child(everything=sourced)
        child._git_config = dict(self._git_config)
        child._git_config_files = set(self._git_config_files)
        child.lenient = True
        try:
            found = child.run(None, depth + 1)
        except Exception:  # too deep, or a script the lexer cannot read
            if RE_GIT_TEXT.search(text):
                self.found.commits = True  # fail closed: the script could not be followed
            return
        self.found.merge(found)
        # its `git config alias.…` writes persist for the git calls after it
        self._git_config.update(child._git_config)
        self._git_config_files |= child._git_config_files


def _destination(args: list[str], target_flag: bool = True) -> tuple[str, list[str], bool]:
    """(destination, source operands, destination given as a target directory).
    `target_flag`: `-t`/`--target-directory` name the destination (cp, install, ln;
    not rsync, where `-t` preserves times)."""
    for i, arg in enumerate(args if target_flag else []):
        if arg in ("-t", "--target-directory") and i + 1 < len(args):
            rest = args[:i] + args[i + 2 :]
            return args[i + 1], [a for a in rest if not a.startswith("-")], True
        if arg.startswith("--target-directory="):
            return arg.split("=", 1)[1], [a for a in args if not a.startswith("-")], True
    operands = [a for a in args if not a.startswith("-")]
    return (operands[-1], operands[:-1], False) if operands else ("", [], False)


def _harness_script(path: Path) -> bool:
    """A script of the installed toolkit (harness/ link) in a project that is not the
    toolkit checkout itself: it is the harness, not project code."""
    return path.parent == TOOLKIT / "lib" and not ROOT.is_relative_to(TOOLKIT)


def _split_alias(alias: str) -> list[str]:
    """An alias's words; [] when its quoting is broken: git refuses such an alias
    (split_cmdline fails on an unclosed quote) and runs nothing."""
    try:
        return shlex.split(alias)
    except ValueError:
        return []


def _config_parameters(value: str | None) -> dict[str, str | None] | None:
    """GIT_CONFIG_PARAMETERS (`'key'='value' …`) as a dict; None if not parseable."""
    if value is None:
        return None
    try:
        items = shlex.split(value)
    except ValueError:
        return None
    out: dict[str, str | None] = {}
    for item in items:
        key, _, val = item.partition("=")
        out[key.lower()] = val
    return out


@cache
def _git_builtins() -> frozenset[str]:
    """git's commands (built in or git-* programs): git runs them before an alias."""
    res = subprocess.run(
        ["git", "--list-cmds=main,others"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return frozenset(res.stdout.split()) if res.returncode == 0 else frozenset()


def _git_alias(
    sub: str,
    cwd: Path = ROOT,
    where: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """alias.<sub> as git would read it there (cwd, -C/--git-dir, GIT_DIR/HOME…)."""
    res = subprocess.run(
        ["git", *(where or []), "config", "--get", f"alias.{sub}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    if res.returncode == 0:
        return res.stdout.strip()
    if res.returncode == 1:  # the alias is not set
        return ""
    raise RuntimeError(f"git config alias.{sub} failed: {res.stderr.strip()}")  # fail closed (I9)


def guard_bash(payload: object) -> None:
    command = _tool_input(payload).get("command")
    if not isinstance(command, str):
        raise ValueError("the Bash payload has no command string")
    _check_agent_bash(payload, command)
    if not command.strip():
        return
    _precheck(command)
    found = BashAnalyzer(command).run()
    if (found.commits or found.publishes) and RE_ATTRIBUTION.search(command):
        raise Blocked(MSG_ATTRIBUTION)
    notes = list(found.warnings.values())
    if found.commits:
        note = _commit_valve()
        notes = [note, *notes] if note else notes
    if notes:
        _emit_warning("\n".join(notes))


def _commit_valve() -> str | None:
    """A commit passes only with HARNESS_ALLOW_COMMIT set to 1 or force in the hook's
    environment. Returns the WARN it carries: with 1, a missing or stale green record
    (issue #61 item 5); with force, that the check was skipped (and the use is logged)."""
    allow = os.environ.get("HARNESS_ALLOW_COMMIT")
    if allow == "force":
        return MSG_WARN_COMMIT_FORCE.format(log=_log_forced_commit())
    if allow != "1":
        raise Blocked(MSG_COMMIT)
    problem = green_record_problem()
    return MSG_WARN_COMMIT_RECORD.format(problem=problem) if problem else None


def _git_path(name: str) -> Path | None:
    rel = _git_out("rev-parse", "--git-path", name)
    return ROOT / rel.strip() if rel and rel.strip() else None


def _index_flags_clear() -> bool:
    """No tracked file marked assume-unchanged or skip-worktree: those flags hide edits
    from status and the fingerprint (issue #55). An unreadable index counts as flagged."""
    flags = _git_out("ls-files", "-v")
    return flags is not None and not re.search(r"^([a-z]|S)", flags, re.MULTILINE)


def green_record_problem() -> str | None:
    """Why the green record does not vouch for the current tree, or None when it does:
    the PR barrier of #55 (record == fingerprint, index flags clear)."""
    record = _git_path(GREEN_RECORD)
    shown = f".git/{GREEN_RECORD}"
    fp = working_tree_fingerprint()
    if fp is None or record is None:
        return (
            f"the tree's fingerprint cannot be computed, so no green record ({shown}) "
            "vouches for it"
        )
    if not _index_flags_clear():
        return (
            "tracked files are marked assume-unchanged or skip-worktree, so no green record "
            f"({shown}) can vouch for this tree"
        )
    try:
        recorded = record.read_bytes().decode("utf-8", "replace").strip()
    except OSError:
        return f"there is no green record ({shown}) for this tree"
    if recorded != fp:
        return f"the green record ({shown}) does not match the tree being committed"
    return None


def _log_forced_commit() -> str:
    """Append one line (UTC time, HEAD, fingerprint; never the command) to the force log.
    Returns where it went, or why it could not be written: the valve is the human's, so a
    failed log never blocks the commit, it shows in the WARN instead."""
    path = _git_path(FORCE_LOG)
    if path is None:
        return "nowhere (not a git repository)"
    head = _git_out("rev-parse", "-q", "--verify", "HEAD")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = (
        f"{stamp}\tHEAD={(head or '').strip() or 'none'}"
        f"\tfingerprint={working_tree_fingerprint() or 'unknown'}\n"
    )
    try:
        with path.open("a", encoding="utf-8") as log:
            log.write(line)
    except OSError as exc:
        return f".git/{FORCE_LOG} — NOT logged ({exc.strerror or exc})"
    return f".git/{FORCE_LOG}"


def _precheck(command: str) -> None:
    """Line-level rules checked before the shell analysis."""
    if "HARNESS_ASSUME_APPROVED" in command:  # best effort (issue #27)
        raise Blocked(MSG_ASSUME)
    if "HARNESS_EXPECT_RED" in command:  # best effort (issue #31)
        raise Blocked(MSG_EXPECT_RED)
    control = RE_CONTROL_ASSIGN.search(command)
    if control and RE_GATE_CALL.search(command):  # best effort (issue #90)
        raise Blocked(MSG_CONTROL.format(name=control.group(1)))
    if RE_STARTUP.search(command):  # best effort (issue #31)
        raise Blocked(MSG_STARTUP)
    if RE_EXPORT_FUNCTION.search(command):  # best effort (issue #31)
        raise Blocked(MSG_EXPORT_FUNCTION)


def _emit_warning(msg: str) -> None:
    """A PreToolUse WARN: the call is allowed; Claude and the user both read `msg`."""
    print(
        json.dumps(
            {
                "systemMessage": msg,
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg},
            }
        )
    )


# ── Integrity backstop (check.sh) ────────────────────────────────────────────


def _git_out(*args: str) -> str | None:
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)
    return res.stdout.decode("utf-8", "surrogateescape") if res.returncode == 0 else None


def changed_paths() -> list[str] | None:
    """Project-relative paths changed vs HEAD (incl. untracked); None without history."""
    top = _git_out("rev-parse", "--show-toplevel")
    if top is None or _git_out("rev-parse", "--verify", "-q", "HEAD") is None:
        return None
    status = _git_out("status", "--porcelain=v1", "-z", "-uall", "--", ".") or ""
    entries = status.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" and i < len(entries):  # rename/copy: next entry is the origin
            paths.append(entries[i])
            i += 1
    base = Path(top.strip())
    rels = (project_rel(Path(os.path.realpath(base / p))) for p in paths)
    return sorted({r for r in rels if r is not None})


def _committed_status() -> dict[str, str]:
    """Leaf id -> status as recorded in HEAD's feature_list.json (empty if absent)."""
    raw = _git_out("show", "HEAD:./feature_list.json")
    try:
        return {
            str(leaf["id"]): leaf["status"] for leaf in _harness.leaves(json.loads(raw or "{}"))
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}


def _closing_leaves() -> list[str]:
    """Leaves in the close window: done in the tree, not yet in HEAD, code APPROVED.

    The documenter marks the leaf done and runs make check before its commit;
    once that commit lands the leaf is done in HEAD and its files close again.
    """
    try:
        current = _harness.leaves()
    except (OSError, ValueError, KeyError):
        return []
    committed = _committed_status()
    return [
        str(leaf["id"])
        for leaf in current
        if leaf["status"] == "done"
        and committed.get(str(leaf["id"])) != "done"
        and _harness.read_verdict(ROOT / f"progress/review_{leaf['id']}.md") == "APPROVED"
    ]


def check_scope() -> int:
    changed = changed_paths()
    if changed is None:
        say("WARN", "src/ scope check skipped (not a git repo or no commits yet)")
        return 0
    leaf = _current_leaf()
    owners = ([leaf] if leaf else []) + _closing_leaves()
    allowed = {f for fid in owners for f in _harness.leaf_files(fid) or []}
    try:
        src_dirs()  # validate even when no changed path reaches the src/ test
        stray = [p for p in changed if classify(p) == "src" and p not in allowed]
    except ConfigError as exc:
        say("FAIL", str(exc))
        return 1
    for path in stray:
        owner = f"subtask {', '.join(owners)}" if owners else "no in_progress subtask"
        say("FAIL", f"{path} changed outside the declared files: ({owner})")
    if not stray:
        say("OK", "src/ changes are within the in_progress/closing subtask's declared files")
    return 1 if stray else 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_seals() -> int:
    rc = 0
    manifests = sorted((ROOT / "progress").glob("acceptance_*.sha256"))
    for manifest in manifests:
        entries, malformed = _seal_required.read_manifest(manifest)
        for problem in malformed:
            say("FAIL", problem)
            rc = 1
        for rel, digest in entries.items():
            target = ROOT / rel
            if not target.is_file() or _sha256(target) != digest:
                say(
                    "FAIL",
                    f"{rel} no longer matches its seal ({manifest.name}): "
                    "acceptance tests are Codex-owned and must not change",
                )
                rc = 1
    if rc == 0 and not _seal_required.has_unlisted_tests(ROOT):
        say("OK", f"sealed acceptance tests intact ({len(manifests)} seal(s))")
    return rc


def check_plan_seals(exempt: _seal_required.Exemptions) -> int:
    problems, warnings = _plan_seal.verify(ROOT, exempt)
    for msg in problems:
        say("FAIL", msg)
    for msg in warnings:
        say("WARN", msg)
    if not problems:
        say("OK", "approved plan intact (roadmap and phase seals)")
    return 1 if problems else 0


def _branch_to_check(leaf: str) -> str | None:
    """The branch <leaf> must be on, or None when it is not enforced. Raises ValueError."""
    try:
        cfg = config()
    except ConfigError:
        return None  # check_scope already reports it
    kind, owner = _harness.parse_id(leaf)
    if kind == "task" and not cfg.git_configured:
        return None
    if _git_out("rev-parse", "--git-dir") is None:
        say("WARN", f"{kind} branch check skipped (not a git repo)")
        return None
    return _harness.task_branch(owner, cfg)


def check_branch() -> int:
    """The in_progress subtask must be on its branch: an issue subtask always (its
    branch is created by `make issue-start`), a task subtask only with [git]."""
    leaf = _current_leaf()
    if not leaf:
        return 0
    kind = _harness.parse_id(leaf)[0]
    try:
        expected = _branch_to_check(leaf)
    except ValueError as exc:
        say("FAIL", str(exc))
        return 1
    if expected is None:
        return 0
    current = (_git_out("branch", "--show-current") or "").strip() or "a detached HEAD"
    rule = "<type>/ISS-<n>-<slug>" if kind == "issue" else "harness.toml [git] branch"
    if current != expected:
        say(
            "FAIL",
            f"subtask {leaf} is in_progress on '{current}', but its {kind} branch is "
            f"'{expected}' ({rule})",
        )
        return 1
    say("OK", f"subtask {leaf} is on its {kind} branch {expected}")
    return 0


def check_required_seals(exempt: _seal_required.Exemptions) -> int:
    """Seals the state requires (I1): gaps WARN first (plan 0.5)."""
    findings = _seal_required.check(ROOT, exempt)
    for msg in findings.problems:
        say("FAIL", msg)
    for msg in findings.warnings:
        say("WARN", msg)
    return 1 if findings.problems else 0


def check_verdict_receipts(warnings: list[str]) -> int:
    """Verdict receipts (I2): WARN only, the exit code never changes."""
    for msg in warnings:
        say("WARN", msg)
    return 0


# Base links whose absence check.sh / the project Makefile already FAIL (unchanged).
BASE_LINKS = frozenset({"harness", ".claude/agents", ".claude/codex"})


def _link_warnings() -> list[str]:
    """Link set vs what install.sh creates (I3a, the check_links() comparison)."""
    skills = ROOT / ".claude/skills"
    if skills.is_dir() and not skills.is_symlink():
        return [
            ".claude/skills is a real directory (per-skill shape): links not checkable until T2-S"
        ]
    fix = f"run bash {TOOLKIT}/install.sh {ROOT}"
    found = []
    for rel, target in _upgrade.expected_links(ROOT).items():
        path = ROOT / rel
        if rel in BASE_LINKS and not path.exists():
            continue  # already a FAIL elsewhere
        if not path.is_symlink():
            state = "missing" if not os.path.lexists(path) else "not a symlink"
            found.append(f"{rel}: {state} (expected a link to {target}); {fix}")
        elif path.resolve() != target.resolve():
            found.append(f"{rel}: points to {os.readlink(path)} (expected {target}); {fix}")
    return found


def _local_settings_warnings() -> list[str]:
    path = ROOT / ".claude/settings.local.json"
    if not os.path.lexists(path):
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        return ["settings.local.json (.claude/): cannot verify local settings (not a JSON object)"]
    if data.get("disableAllHooks") or "hooks" in data:
        return [
            "settings.local.json (.claude/) disables or overrides the harness hooks "
            "(disableAllHooks / hooks): remove those keys"
        ]
    return []


def _templates_record_warnings() -> list[str]:
    path = ROOT / _upgrade.RECORD
    if not os.path.lexists(path):
        return []
    if path.is_symlink() or not path.is_file():
        return ["templates.json record (.harness/) is not a regular file; resolve by hand"]
    try:
        _upgrade._read_record(ROOT)
    except _upgrade.MigrationError as exc:
        return [f"templates.json record: {exc}"]
    return []


def check_harness_state() -> int:
    """Links, local settings and templates record (I3a): WARN only (plan 0.5)."""
    for msg in _link_warnings() + _local_settings_warnings() + _templates_record_warnings():
        say("WARN", msg)
    return 0


# I8 outcome check: a close commit's subject ends with its leaf id, and the commit is
# the one that marks that leaf done in feature_list.json.
RE_CLOSE_SUBJECT = re.compile(r"\[(\d+\.\d+\.\d+|ISS-\d+\.\d+)\]\s*$")


def _base_ref() -> str | None:
    """The branch the work merges into (origin/HEAD, else main), remote first."""
    head = _git_out("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD") or ""
    base = head.strip().removeprefix("origin/") or "main"
    for ref in (f"origin/{base}", base):
        if _git_out("rev-parse", "--verify", "-q", f"{ref}^{{commit}}") is not None:
            return ref
    return None


def _leaf_done(rev: str, leaf: str) -> bool:
    raw = _git_out("show", f"{rev}:./feature_list.json")
    try:
        return any(
            str(item["id"]) == leaf and item["status"] == "done"
            for item in _harness.leaves(json.loads(raw or "{}"))
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def _is_close(sha: str, parent: str | None, subject: str) -> bool:
    match = RE_CLOSE_SUBJECT.search(subject)
    if not match:
        return False
    leaf = match.group(1)
    return _leaf_done(sha, leaf) and not (parent and _leaf_done(parent, leaf))


def stray_commits() -> list[str]:
    """Commits on HEAD past the base that the harness close did not make: no
    `[<leaf>]` subject that marks that leaf done, or a merge of a non-base branch.
    A merge that only brings the base in is not reported."""
    base = _base_ref()
    if base is None:
        return []
    listing = _git_out("log", "-z", "--format=%H %P%x1f%s", f"{base}..HEAD") or ""
    found = []
    for record in filter(None, listing.split("\0")):
        head, _, subject = record.partition("\x1f")
        sha, *parents = head.split()
        if len(parents) > 1:
            if all(
                _git_out("merge-base", "--is-ancestor", p, base) is not None for p in parents[1:]
            ):
                continue
        elif _is_close(sha, parents[0] if parents else None, subject):
            continue
        found.append(f"{sha[:10]} {subject!r}")
    return found


def check_commits() -> int:
    """I8 outcome check vs git: WARN only (plan 0.5), the exit code never changes."""
    for commit in stray_commits():
        say(
            "WARN",
            f"commit {commit} on this branch was not made by the harness close (I8). "
            f"If the human made it on purpose, nothing to do; otherwise review it "
            f"(git show {commit.split()[0]}) and revert it.",
        )
    return 0


def integrity() -> int:
    exempt = _seal_required.Exemptions(ROOT)
    # Before the required seals: a receipt gap must count for their legacy inventory WARN.
    receipts = _verdict.receipt_warnings(ROOT, exempt)
    rc = (
        check_scope()
        | check_branch()
        | check_seals()
        | check_plan_seals(exempt)
        | check_required_seals(exempt)
        | check_verdict_receipts(receipts)
        | check_harness_state()
        | check_commits()
    )
    if exempt.exempt:
        say("OK", f"{len(exempt.exempt)} legacy item(s) exempt (legacy_inventory)")
    return rc


def setup_pending() -> int:
    """Exit 1 while the setup branch holds changes main does not have yet."""
    try:
        branch = config().setup_branch
    except ConfigError as exc:
        say("FAIL", str(exc))
        return 1
    if _git_out("rev-parse", "-q", "--verify", f"refs/heads/{branch}") is None:
        return 0  # never created, or deleted after its merge
    if _harness.branch_merged(branch, "main", ROOT):
        return 0  # merged (merge commit, fast forward or squash) or empty
    say(
        "FAIL",
        f"the setup branch '{branch}' has changes main does not contain yet: merge it "
        "(PR / squash) before the first subtask, or delete it if it is obsolete",
    )
    return 1


def _acceptance_files(leaf: str) -> list[Path]:
    return _harness.acceptance_files(ROOT, leaf)


def _manifest_rel(leaf: str) -> str:
    return f"progress/acceptance_{leaf}.sha256"


def _is_approved_baseline(leaf: str) -> bool:
    item = next((x for x in _harness.leaves() if str(x["id"]) == leaf), None)
    return item is not None and item["status"] == "baseline" and _harness.complete(item)


def seal(leaf: str) -> int:
    baseline = _is_approved_baseline(leaf)
    plan = _harness.read_verdict(ROOT / f"progress/plan_review_{leaf}.md")
    if plan != "PLAN_APPROVED" and not baseline:
        say("FAIL", f"cannot seal {leaf}: its plan review is not PLAN_APPROVED", sys.stderr)
        return 1
    files = _acceptance_files(leaf)
    if not files:
        say("FAIL", f"cannot seal {leaf}: no tests/acceptance/test_{leaf}.*", sys.stderr)
        return 1
    ticket = _supersede.read(leaf, ROOT / f"progress/plan_review_{leaf}.md")
    fresh = None
    if ticket is not None:
        if ticket.get("entries") == _supersede.plan_entries(ROOT, leaf):
            fresh = dict(ticket.get("files") or {})
        else:
            _supersede.remove(leaf)  # spec or acceptance changed after the commit
    problems = _migrate.seal(ROOT, leaf, ROOT / _manifest_rel(leaf), fresh)
    for problem in problems:
        say("FAIL", f"cannot seal {leaf}: {problem}", sys.stderr)
    if problems:
        return 1
    lines = [f"{_sha256(f)}  {f.relative_to(ROOT).as_posix()}\n" for f in files]
    (ROOT / _manifest_rel(leaf)).write_text("".join(lines), encoding="utf-8")
    say("OK", f"sealed {len(files)} acceptance file(s) -> {_manifest_rel(leaf)}")
    if ticket is not None:
        _supersede.remove(leaf)
    if baseline:
        return 0  # no plan gate approved a spec change: its phase seal stays as it is
    spec, phase = _leaf_spec(leaf)
    if spec and _plan_seal.reseal_spec(ROOT, spec, phase):
        say("OK", f"re-sealed its approved spec {spec}")
    return 0


def _leaf_spec(leaf: str) -> tuple[str, str]:
    """(spec, owner) of a leaf: the owner is its phase (NN) or its issue (ISS-n)."""
    item = next((s for s in _harness.leaves() if s["id"] == leaf), None)
    if item is None:
        return ("", "")
    return str(item["spec"]), str(item.get("issue") or item["phase"])


def seal_roadmap() -> int:
    if _harness.read_verdict(ROOT / _harness.ROADMAP_REVIEW) != "ROADMAP_APPROVED":
        say("FAIL", "cannot seal the roadmap: it is not ROADMAP_APPROVED", sys.stderr)
        return 1
    entries = _plan_seal.roadmap_entries(ROOT)
    _plan_seal.write(_plan_seal.roadmap_manifest(ROOT), entries)
    say("OK", f"sealed the approved roadmap ({len(entries)} file(s))")
    return 0


def seal_phase(pid: str) -> int:
    if _harness.read_verdict(ROOT / _harness.phase_review(pid)) != "PHASE_APPROVED":
        say("FAIL", f"cannot seal phase {pid}: it is not PHASE_APPROVED", sys.stderr)
        return 1
    entries = _plan_seal.phase_entries(ROOT, pid)
    if not entries:
        say("FAIL", f"cannot seal phase {pid}: no docs/plan/phase-{pid}-*.md", sys.stderr)
        return 1
    _plan_seal.write(_plan_seal.phase_manifest(ROOT, pid), entries)
    say("OK", f"sealed the approved phase {pid} ({len(entries)} file(s))")
    return 0


def seal_task(tid: str) -> int:
    """Task gate (issue #62): seal the subtask specs of task N.M it approved."""
    if _harness.read_verdict(ROOT / _harness.task_review(tid)) != "TASK_APPROVED":
        say("FAIL", f"cannot seal task {tid}: it is not TASK_APPROVED", sys.stderr)
        return 1
    entries = _plan_seal.task_entries(ROOT, tid)
    if not entries:
        say("FAIL", f"cannot seal task {tid}: it has no subtask specs", sys.stderr)
        return 1
    _plan_seal.write(_plan_seal.task_manifest(ROOT, tid), entries)
    say("OK", f"sealed the approved task {tid} ({len(entries)} spec(s))")
    return 0


def seal_issue(iid: str) -> int:
    if _harness.read_verdict(ROOT / _harness.issue_review(iid)) != "ISSUE_APPROVED":
        say("FAIL", f"cannot seal issue {iid}: it is not ISSUE_APPROVED", sys.stderr)
        return 1
    if not _plan_seal.issue_entries(ROOT, iid):
        say("FAIL", f"cannot seal issue {iid}: no docs/plan/issues/{iid}-*.md", sys.stderr)
        return 1
    ticket = _supersede.read(iid, ROOT / _harness.issue_review(iid))
    lists = None
    if ticket is not None:  # a fresh gate approval (issue #30): read everything once
        leaves, lists = _supersede.issue_subtasks(iid)
        base = _supersede.issue_entries(ROOT, iid)
        bound = _supersede.issue_digests(base, leaves, lists)
        if {k: ticket.get(k) for k in bound} != bound:
            _supersede.remove(iid)
            say("FAIL", f"issue {iid} changed after its approval; re-run its gate", sys.stderr)
            return 1
        problems = _migrate.supersede(ROOT, leaves, lists)
        for problem in problems:
            say("FAIL", f"cannot seal issue {iid}: {problem}", sys.stderr)
        if problems:
            return 1
    _migrate.write_snapshot(ROOT, iid, lists)  # freeze its migrates: lists with the breakdown
    if lists is None:
        entries = _plan_seal.issue_entries(ROOT, iid)
    else:
        snapshot = _migrate.snapshot_path(ROOT, iid)
        entries = {**base, snapshot.relative_to(ROOT).as_posix(): _supersede.text_digest(snapshot)}
    _plan_seal.write(_plan_seal.issue_manifest(ROOT, iid), entries)
    if ticket is not None:
        _supersede.remove(iid)  # only once the seal is written
    say("OK", f"sealed the approved issue {iid} ({len(entries)} file(s))")
    return 0


ISSUE_DRAFT = "progress/issue_draft.md"
ISSUE_DRAFT_REVIEW = "progress/issue_draft_review.md"
ISSUE_DRAFT_SEAL = "progress/issue_draft.sha256"


def seal_draft() -> int:
    if _harness.read_verdict(ROOT / ISSUE_DRAFT_REVIEW) != "DRAFT_APPROVED":
        say("FAIL", "cannot seal the issue draft: it is not DRAFT_APPROVED", sys.stderr)
        return 1
    draft = ROOT / ISSUE_DRAFT
    if not draft.is_file():
        say("FAIL", f"cannot seal the issue draft: no {ISSUE_DRAFT}", sys.stderr)
        return 1
    (ROOT / ISSUE_DRAFT_SEAL).write_text(f"{_sha256(draft)}  {ISSUE_DRAFT}\n", encoding="utf-8")
    say("OK", f"sealed the approved issue draft -> {ISSUE_DRAFT_SEAL}")
    return 0


def seal_command(args: list[str]) -> int:
    if args in (["roadmap"], ["draft"]):
        return seal_roadmap() if args == ["roadmap"] else seal_draft()
    owned = {"phase": seal_phase, "issue": seal_issue, "task": seal_task}
    if len(args) == 2 and args[0] in owned:
        return owned[args[0]](args[1])
    if len(args) == 1:
        return seal(args[0])
    print(
        "usage: _guard.py seal <N.M.K|ISS-n.k> | roadmap | phase <NN> | task <N.M> | issue <ISS-n>"
        " | draft",
        file=sys.stderr,
    )
    return 2


# The toolkit code `make check` runs, reached through the project's links. Git tracks a
# link as its target path only, so their contents go into the fingerprint explicitly.
TOOLKIT_LINKS = ("harness", ".claude/settings.json")
# Files that drive the check (issue #61 item 4), hashed by content whether git tracks,
# ignores or never sees them: an ignored harness.toml or Makefile changes what the
# check runs without moving the status or the diff. A fixed list (user decision); a
# directory (docs/plan) is hashed whole.
CHECK_INPUTS = (
    "Makefile",
    "GNUmakefile",
    "makefile",
    "harness.toml",
    "feature_list.json",
    "docs/plan",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "conftest.py",
)


def _fs_bytes(text: str) -> bytes:
    """A path-bearing string as bytes: names git and the OS allow may hold bytes that
    are not UTF-8 (decoded with surrogateescape), and must hash, not raise."""
    return text.encode("utf-8", "surrogateescape")


def _hash_tree(digest: Any, name: str, path: Path, seen: frozenset[str]) -> None:
    """Hash a file, or every entry of a directory in name order, following links into
    directories. A directory already on the current path (a link loop) is hashed as a
    link to its target instead of walked. Raises OSError on anything unreadable
    (a dangling link, a directory it cannot list, a file it cannot read)."""
    real = os.path.realpath(path)
    if path.is_symlink() and not path.exists():
        raise OSError(f"dangling link: {path}")
    if path.is_file():
        digest.update(_fs_bytes(f"f\0{name}\0{_sha256(path)}\0"))
    elif path.is_dir():
        if real in seen:
            digest.update(_fs_bytes(f"loop\0{name}\0{os.readlink(path)}\0"))
            return
        digest.update(_fs_bytes(f"d\0{name}\0"))
        for entry in sorted(os.listdir(path)):
            _hash_tree(digest, f"{name}/{entry}", path / entry, seen | {real})
    else:
        raise OSError(f"not a file or directory: {path}")


def _paths_part(digest: Any, section: str, names: tuple[str, ...]) -> bool:
    """Hash each of `names` (a file or a whole directory, links followed; bytecode
    included, as Python may run a .pyc instead of its source), or mark it absent.
    False when any part cannot be read: no fingerprint then, never a stale green."""
    digest.update(f"{section}\0".encode())
    for name in names:
        path = ROOT / name
        try:
            if _absent(path):
                digest.update(_fs_bytes(f"absent\0{name}\0"))
                continue
            _hash_tree(digest, name, path, frozenset())
        except OSError:
            return False
    return True


def _absent(path: Path) -> bool:
    """True only when nothing is there (no entry, or a parent that is a file). An entry
    that cannot be looked at (an untraversable parent) raises: it is unreadable, not
    absent."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return True
    return False


def working_tree_fingerprint() -> str | None:
    """Hash HEAD + status + diff + untracked contents + the check inputs + the toolkit
    behind the links; None outside a git repo or when a part cannot be read."""
    status = _git_out("status", "--porcelain=v1", "-z", "-uall")
    if status is None:
        return None
    digest = hashlib.sha256()
    head = _git_out("rev-parse", "-q", "--verify", "HEAD") or "no-head"
    diff = (
        _git_out("diff", "HEAD", "--binary")
        if head != "no-head"
        else (_git_out("diff", "--cached", "--binary") or "") + (_git_out("diff", "--binary") or "")
    )
    for part in (head, status, diff or ""):
        digest.update(part.encode("utf-8", "surrogateescape"))
    untracked = (_git_out("ls-files", "-o", "--exclude-standard", "-z") or "").split("\0")
    try:
        for rel in filter(None, untracked):
            path = ROOT / rel
            if path.is_file():
                digest.update(_fs_bytes(rel) + _sha256(path).encode())
    except OSError:  # an unreadable untracked file: no fingerprint, never a stale green
        return None
    if not _paths_part(digest, "check-inputs", CHECK_INPUTS):
        return None
    if not _paths_part(digest, "toolkit", TOOLKIT_LINKS):
        return None
    return digest.hexdigest()


def fingerprint() -> int:
    """Print the working-tree fingerprint; exit 1 when it cannot be computed."""
    fp = working_tree_fingerprint()
    if fp is None:
        return 1
    print(fp)
    return 0


# ── Entry point ──────────────────────────────────────────────────────────────


def _run_guard(kind: str) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = None  # fail closed (I9): an unreadable payload is a guard error
    try:
        (guard_write if kind == "write" else guard_bash)(payload)
    except Blocked as blocked:
        print(blocked, file=sys.stderr)
        return BLOCK
    except Exception as exc:  # fail closed: a broken guard must not silently allow
        print(
            f"BLOCKED: harness guard error ({type(exc).__name__}: {exc}). "
            "Report it; do not work around it.",
            file=sys.stderr,
        )
        return BLOCK
    return 0


def migrates_ready(leaf: str) -> int:
    """Plan-gate precondition of a subtask that declares `migrates:` (see _migrate.py)."""
    problems = _migrate.ready_problems(ROOT, leaf)
    for problem in problems:
        say("FAIL", problem, sys.stderr)
    return 1 if problems else 0


def commit_message(path: str, leaf: str) -> int:
    """Validate the documenter's close message before the harness commits it."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        say("FAIL", f"no close message at {path}: the close did not finish", sys.stderr)
        return 1
    subject = text.strip().splitlines()[0] if text.strip() else ""
    problems = []
    if not subject.endswith(f"[{leaf}]"):
        problems.append(f"the subject must end with [{leaf}]: {subject!r}")
    if RE_ATTRIBUTION.search(text):
        problems.append("it contains agent attribution")
    for problem in problems:
        say("FAIL", f"close message {path}: {problem}", sys.stderr)
    return 1 if problems else 0


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "write": lambda _: _run_guard("write"),
    "bash": lambda _: _run_guard("bash"),
    "integrity": lambda _: integrity(),
    "seal": seal_command,
    "fingerprint": lambda _: fingerprint(),
    "setup-pending": lambda _: setup_pending(),
    "commit-message": lambda rest: commit_message(*rest) if len(rest) == 2 else _usage(),
    "migrates-ready": lambda rest: migrates_ready(rest[0]) if len(rest) == 1 else _usage(),
}


def _usage() -> int:
    print(__doc__, file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    cmd, *rest = argv or [""]
    command = COMMANDS.get(cmd)
    return command(rest) if command else _usage()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
