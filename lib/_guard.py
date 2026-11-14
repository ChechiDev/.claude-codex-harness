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
  seal phase <NN>   record progress/plan_phase_<NN>.sha256 (needs PHASE_APPROVED)
  seal issue <ISS-n>  record progress/plan_issue_<ISS-n>.sha256 (needs ISSUE_APPROVED)
  seal draft        record progress/issue_draft.sha256 (needs DRAFT_APPROVED)
  fingerprint  print a hash of the git working-tree state (Stop-hook cache key)
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

import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import _config
import _harness
import _migrate
import _plan_seal
import _seal_required
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
    r"^(progress/(plan_review|review|phase_review|issue_review)_[^/]+\.md"
    r"|progress/(roadmap_review|issue_draft_review|legacy_inventory_review)\.md"
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
    "BLOCKED: sealing (acceptance tests, the approved roadmap, a phase or the legacy inventory) "
    "is done by the Codex "
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


MSG_ASSUME = (
    "BLOCKED: HARNESS_ASSUME_APPROVED is set only by the roadmap/phase/issue gate for its "
    "counterfactual run (review_with_codex.sh). Run `make check` without it."
)


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
    return re.search(rf"(^|[\s'\"(=/<>])({dirs}|{RE_MENTIONS_STATE})", command) is not None


def _mentions_harness(command: str) -> bool:
    """The command text names a harness-owned path (interpreter writes: opaque targets)."""
    entries = "|".join(re.escape(e) for e in HARNESS_ENTRIES)
    home = os.path.abspath(_upgrade._claude_home())
    globs = "|".join(re.escape(f"{home}/{g}") for g in HARNESS_GLOBAL)
    root = re.escape(f"{ROOT}/")
    lexical = rf"(^|[\s'\"(=<>]|\./|{root})({entries})($|[/\s'\")])"
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
class SimpleCommand:
    words: list[str] = field(default_factory=list)
    out_targets: list[str] = field(default_factory=list)


def _lexer(command: str) -> shlex.shlex:
    lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return lex


def split_commands(command: str) -> list[SimpleCommand]:
    """Split a shell line into simple commands, collecting output-redirect targets."""
    try:
        tokens = list(_lexer(command))
    except ValueError:  # unbalanced quotes: fall back to plain splitting
        tokens = command.split()
    out: list[SimpleCommand] = [SimpleCommand()]
    pending_redirect: str | None = None
    for tok in tokens:
        if pending_redirect is not None:
            if ">" in pending_redirect and not (pending_redirect.endswith("&") and tok.isdigit()):
                out[-1].out_targets.append(tok)
            pending_redirect = None
        elif tok and set(tok) <= set("();<>|&\n"):
            if "<" in tok or ">" in tok:
                pending_redirect = tok
            else:
                out.append(SimpleCommand())
        else:
            out[-1].words.append(tok)
    return [c for c in out if c.words or c.out_targets]


WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "sudo", "nice", "stdbuf"}
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
SEALER_NAMES = r"(_legacy|_guard|_verdict|legacy_inventory|review_with_codex)\b"
RE_SEALER_NAME = re.compile(r"(^|[\s/'\"=])" + SEALER_NAMES)
RE_SEALER_ANYWHERE = re.compile(SEALER_NAMES)


def _skip_flags(
    words: list[str], i: int, value_flags: frozenset[str], flags: frozenset[str]
) -> tuple[int, bool]:
    """Skip the flags of a wrapper from words[i]; True when one of them is unknown."""
    unknown = False
    while i < len(words) and words[i].startswith("-"):
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
        elif name == "timeout" and i + 1 < len(words):
            i += 2
        else:
            break
    return words[i:], unknown


def strip_wrappers(words: list[str]) -> list[str]:
    """Drop env assignments and transparent wrappers (env, command, uv run, …)."""
    return strip_wrappers_checked(words)[0]


# ── Bash guard: analysis ─────────────────────────────────────────────────────


@dataclass
class Findings:
    commits: bool = False
    publishes: bool = False  # merge / PR: attribution applies
    mentions_protected: bool = False


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
    r"|pr_|legacy_inventory|issue_review|issue_draft_review|verdict_)|\.harness\b"
)
RE_WRITE_CALL = re.compile(
    r"\b(write|write_text|write_bytes|unlink|remove|rename|replace|rmtree|truncate|chmod|"
    r"makedirs|mkdir|touch|copy|copyfile|copy2|move|symlink|rmdir)\b"
    r"|open\([^)]*,\s*['\"][rbt]*[wax+][rbtwax+]*['\"]"  # a whole mode string: 'w', 'rb+'
    r"|open\([^)]*,\s*['\"](?:>>?|\+<)"  # perl: open(F, ">path"), open(F, '>', $p)
)

SEALERS: dict[str, frozenset[str]] = {
    "review_with_codex.sh": frozenset({"seal"}),
    "_guard.py": frozenset({"seal"}),
    "legacy_inventory.sh": frozenset({"create", "review", "seal"}),
    "_legacy.py": frozenset({"create", "review", "seal"}),
    "_verdict.py": frozenset({"begin", "commit", "abort", "receipt"}),
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


class BashAnalyzer:
    """Walks the simple commands of one Bash call, tracking `cd`, and raises Blocked."""

    def __init__(self, command: str) -> None:
        self.raw = command
        self.cwd = ROOT
        self.found = Findings()

    def run(self, command: str | None = None, depth: int = 0) -> Findings:
        if depth > 4:
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd="deeply nested shell"))
        for simple in split_commands(self.raw if command is None else command):
            for target in simple.out_targets:
                self._check(target, destructive=False)
            words, unsure = strip_wrappers_checked(simple.words)
            if unsure and RE_SEALER_NAME.search(" ".join(simple.words)):
                raise Blocked(MSG_SEAL)  # fail-closed: an unknown wrapper flag may hide it
            if words:
                self._command(words, depth)
        return self.found

    def _check(self, raw: str, destructive: bool) -> None:
        for spelled in self._expand(raw):
            kind = protected_kind(resolve(spelled, self.cwd), destructive)
            if kind:
                raise Blocked(MSG_SHELL_WRITE.format(target=raw, kind=kind))
            if harness_kind(spelled, self.cwd, destructive):
                raise Blocked(MSG_HARNESS.format(target=raw))

    def _expand(self, raw: str) -> Iterator[str]:
        """The spellings `raw` stands for: its glob matches, or itself."""
        if any(ch in raw for ch in "*?["):
            matches = glob.glob(raw, root_dir=self.cwd)
            if matches:
                yield from matches
                return
        yield raw

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
        if handler:
            handler(args, depth)
        elif name in SHELLS and "-c" in args[:-1]:
            self.run(args[args.index("-c") + 1], depth + 1)
        elif name == "eval":
            self.run(" ".join(args), depth + 1)
        elif name in ("patch",):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=name))
        elif _is_seal(name, args):
            raise Blocked(MSG_SEAL)
        else:
            self._file_verbs(name, args)

    def _is_protected(self, arg: str) -> bool:
        return not arg.startswith("-") and any(
            protected_kind(resolve(p, self.cwd), destructive=True)
            or harness_kind(p, self.cwd, destructive=True)
            for p in self._expand(arg)
        )

    def _file_verbs(self, name: str, args: list[str]) -> None:
        operands = [a for a in args if not a.startswith("-")]
        if name in ANY_ARG_VERBS or (
            name in INPLACE_VERBS and any(RE_INPLACE_FLAG.match(a) for a in args)
        ):
            for arg in operands:
                self._check(arg, destructive=name in DESTRUCTIVE_VERBS)
        elif name in DEST_ARG_VERBS:
            for target in self._destinations(name, args):
                self._check(target, destructive=False)
        elif INTERPRETERS.match(name) and RE_WRITE_CALL.search(self.raw):
            if _mentions_harness(self.raw):
                raise Blocked(MSG_HARNESS.format(target=name))
            if _mentions_protected(self.raw):
                raise Blocked(MSG_SHELL_OPAQUE.format(cmd=name))

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
        self.cwd = resolve(args[0], self.cwd) if args else Path.home()

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

    def _xargs(self, args: list[str], depth: int) -> None:
        verb = next((os.path.basename(a) for a in args if not a.startswith("-")), "echo")
        writes = verb in ANY_ARG_VERBS | DEST_ARG_VERBS | INPLACE_VERBS
        # xargs takes its operands from stdin, so judge by what the whole line touches.
        if writes and (self.found.mentions_protected or _mentions_protected(self.raw)):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=f"xargs {verb}"))

    def _gh(self, args: list[str], depth: int) -> None:
        if args[:1] == ["pr"] and args[1:2] in (["create"], ["edit"]):
            self.found.publishes = True

    def _git(self, args: list[str], depth: int) -> None:
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in GIT_VALUE_OPTS else 1
        if i >= len(args):
            return
        sub, rest = args[i], args[i + 1 :]
        alias = _git_alias(sub)
        if alias and alias.startswith("!"):
            self.run(alias[1:], depth + 1)
            return
        if alias:
            alias_words = shlex.split(alias)
            sub, rest = alias_words[0], alias_words[1:] + rest
        self._git_sub(sub, rest)

    def _git_sub(self, sub: str, rest: list[str]) -> None:
        if sub == "commit":
            self.found.commits = True
        elif sub == "merge":
            self.found.publishes = True
        elif sub in ("apply", "am"):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=f"git {sub}"))
        elif sub in ("checkout", "restore", "rm", "mv", "clean"):
            paths = [a for a in rest if not a.startswith("-")]
            if sub == "clean" and not paths:
                paths = ["."]
            for path in paths:
                if sub != "checkout" or "--" in rest or (self.cwd / path).exists():
                    self._check(path, destructive=True)


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


def _git_alias(sub: str) -> str:
    res = subprocess.run(
        ["git", "config", "--get", f"alias.{sub}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
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
    if not command.strip():
        return
    if "HARNESS_ASSUME_APPROVED" in command:  # best effort (issue #27)
        raise Blocked(MSG_ASSUME)
    found = BashAnalyzer(command).run()
    if (found.commits or found.publishes) and RE_ATTRIBUTION.search(command):
        raise Blocked(MSG_ATTRIBUTION)
    if found.commits and os.environ.get("HARNESS_ALLOW_COMMIT") != "1":
        raise Blocked(MSG_COMMIT)


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
    if args == ["roadmap"]:
        return seal_roadmap()
    if args == ["draft"]:
        return seal_draft()
    if len(args) == 2 and args[0] == "phase":
        return seal_phase(args[1])
    if len(args) == 2 and args[0] == "issue":
        return seal_issue(args[1])
    if len(args) == 1:
        return seal(args[0])
    print(
        "usage: _guard.py seal <N.M.K|ISS-n.k> | roadmap | phase <NN> | issue <ISS-n> | draft",
        file=sys.stderr,
    )
    return 2


# The toolkit code `make check` runs, reached through the project's links. Git tracks a
# link as its target path only, so their contents go into the fingerprint explicitly.
TOOLKIT_LINKS = ("harness", ".claude/settings.json")


def _hash_tree(digest: Any, name: str, path: Path, seen: frozenset[str]) -> None:
    """Hash a file, or every entry of a directory in name order, following links into
    directories. A directory already on the current path (a link loop) is hashed as a
    link to its target instead of walked. Raises OSError on anything unreadable
    (a dangling link, a directory it cannot list, a file it cannot read)."""
    real = os.path.realpath(path)
    if path.is_symlink() and not path.exists():
        raise OSError(f"dangling link: {path}")
    if path.is_file():
        digest.update(f"f\0{name}\0{_sha256(path)}\0".encode())
    elif path.is_dir():
        if real in seen:
            digest.update(f"loop\0{name}\0{os.readlink(path)}\0".encode())
            return
        digest.update(f"d\0{name}\0".encode())
        for entry in sorted(os.listdir(path)):
            _hash_tree(digest, f"{name}/{entry}", path / entry, seen | {real})
    else:
        raise OSError(f"not a file or directory: {path}")


def _toolkit_part(digest: Any) -> bool:
    """Hash the toolkit behind TOOLKIT_LINKS, bytecode included (Python may run a .pyc
    instead of its source). False when any part cannot be read: no fingerprint then,
    so the Stop runs the full check. Never a stale green."""
    for link in TOOLKIT_LINKS:
        path = ROOT / link
        if not path.exists() and not path.is_symlink():
            digest.update(f"absent\0{link}\0".encode())
            continue
        try:
            _hash_tree(digest, link, path, frozenset())
        except OSError:
            return False
    return True


def fingerprint() -> int:
    """Hash HEAD + status + diff + untracked contents + the toolkit behind the links;
    exit 1 outside a git repo or when the toolkit part cannot be computed."""
    status = _git_out("status", "--porcelain=v1", "-z", "-uall")
    if status is None:
        return 1
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
    for rel in filter(None, untracked):
        path = ROOT / rel
        if path.is_file():
            digest.update(rel.encode() + _sha256(path).encode())
    if not _toolkit_part(digest):
        return 1
    print(digest.hexdigest())
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
