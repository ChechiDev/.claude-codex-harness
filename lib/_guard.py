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
  fingerprint  print a hash of the git working-tree state (Stop-hook cache key)

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
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import _harness
import _plan_seal
from _status import say

BLOCK = 2
ROOT = Path(os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()))
# Roots whose deletion (or deletion of an ancestor) destroys protected state.
PROTECTED_ROOTS = ("src", "include", "tests/acceptance", "progress")
SKIP_DIRS = {"node_modules", "site-packages"}
# Gate verdicts (roadmap, phase, plan, code) and every seal under progress/.
RE_CODEX_OWNED = re.compile(
    r"^(progress/(plan_review|review|phase_review)_[^/]+\.md|progress/roadmap_review\.md"
    r"|progress/[^/]+\.sha256|\.git/harness-[^/]+)$"
)
RE_ATTRIBUTION = re.compile(r"co-authored-by|generated with|🤖|claude code|codex|anthropic", re.I)

MSG_ACCEPTANCE = (
    "BLOCKED: tests/acceptance/ is the independent oracle written by Codex and is READ-ONLY "
    "for Claude. If you think an acceptance test is wrong, stop and report it as a blocker in "
    "progress/current.md — do not edit it."
)
MSG_CODEX_OWNED = (
    "BLOCKED: '{rel}' is owned by Codex (gate verdicts, acceptance seals) or by the harness. "
    "Claude never writes it; run the corresponding ./harness/review_with_codex.sh gate instead."
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
    "BLOCKED: sealing (acceptance tests, the approved roadmap or a phase) is done by the Codex "
    "gate itself (or by the human in manual mode), never by Claude."
)
MSG_ATTRIBUTION = (
    "BLOCKED: the commit/PR contains agent attribution (Co-Authored-By / 'Generated with' / "
    "mention of Claude/Codex). Present it as the human author's work, with no attribution."
)
MSG_COMMIT = (
    "BLOCKED: Claude does not commit in the harness. The single per-subtask commit is made by "
    "Codex at close (./harness/review_with_codex.sh doc <N.M.K>). If you really need a manual "
    "commit outside the flow, the human must start Claude Code with HARNESS_ALLOW_COMMIT=1."
)


class Blocked(Exception):
    """A guard decision to block, carrying the message Claude reads."""


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
    parts = tuple(Path(rel).parts)
    if _segment_index(parts, ("tests", "acceptance")) >= 0:
        return "acceptance"
    # include/ holds C public headers: same micro-task scope as src/.
    if _segment_index(parts, ("src",)) >= 0 or _segment_index(parts, ("include",)) >= 0:
        return "src"
    return None


def protected_kind(path: Path, destructive: bool = False) -> str | None:
    """Why `path` is protected, or None. Destructive ops also protect ancestors."""
    rel = project_rel(path)
    if rel is None:
        return "ancestor" if destructive and ROOT.is_relative_to(path) else None
    kind = classify(rel) if rel != "." else None
    if kind or not destructive:
        return kind
    for root in PROTECTED_ROOTS:
        if (ROOT / root).is_relative_to(path):
            return f"ancestor of {root}/"
    return None


# ── Write guard ──────────────────────────────────────────────────────────────


def guard_write(payload: dict[str, Any]) -> None:
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not raw:
        return
    rel = project_rel(resolve(str(raw), ROOT))
    if rel is None:
        return
    kind = classify(rel)
    if kind == "acceptance":
        raise Blocked(MSG_ACCEPTANCE)
    if kind == "codex-owned":
        raise Blocked(MSG_CODEX_OWNED.format(rel=rel))
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


def strip_wrappers(words: list[str]) -> list[str]:
    """Drop env assignments and transparent wrappers (env, command, uv run, …)."""
    i = 0
    while i < len(words):
        word = words[i]
        name = os.path.basename(word)
        if RE_ASSIGNMENT.match(word) or name in WRAPPERS:
            i += 1
        elif name == "env" or (name == "uv" and words[i + 1 : i + 2] == ["run"]):
            i += 1 if name == "env" else 2
            while i < len(words) and words[i].startswith("-"):
                i += 1
        elif name == "timeout" and i + 1 < len(words):
            i += 2
        else:
            break
    return words[i:]


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
RE_MENTIONS_PROTECTED = re.compile(
    r"(^|[\s'\"(=/])(src/|include/|tests/acceptance"
    r"|progress/(plan_review|review|phase_review|roadmap_review|acceptance_|plan_))"
)
RE_WRITE_CALL = re.compile(
    r"\b(write|write_text|write_bytes|unlink|remove|rename|replace|rmtree|truncate|chmod|"
    r"makedirs|mkdir|touch|copy|copyfile|copy2|move|symlink|rmdir)\b"
    r"|open\([^)]*,\s*['\"][^'\"]*[wax+]"
)


def _is_seal(name: str, args: list[str]) -> bool:
    """`review_with_codex.sh seal …` or `_guard.py seal …`, run directly or via python."""
    if name in ("review_with_codex.sh", "_guard.py"):
        return args[:1] == ["seal"]
    if INTERPRETERS.match(name):
        scripts = [i for i, a in enumerate(args) if os.path.basename(a) == "_guard.py"]
        return any(args[i + 1 : i + 2] == ["seal"] for i in scripts)
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
            words = strip_wrappers(simple.words)
            if words:
                self._command(words, depth)
        return self.found

    def _check(self, raw: str, destructive: bool) -> None:
        for path in self._expand(raw):
            kind = protected_kind(path, destructive)
            if kind:
                raise Blocked(MSG_SHELL_WRITE.format(target=raw, kind=kind))

    def _expand(self, raw: str) -> Iterator[Path]:
        if any(ch in raw for ch in "*?["):
            matches = glob.glob(raw, root_dir=self.cwd)
            if matches:
                yield from (resolve(m, self.cwd) for m in matches)
                return
        yield resolve(raw, self.cwd)

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
            protected_kind(p, destructive=True) for p in self._expand(arg)
        )

    def _file_verbs(self, name: str, args: list[str]) -> None:
        operands = [a for a in args if not a.startswith("-")]
        if name in ANY_ARG_VERBS or (
            name in INPLACE_VERBS and any(RE_INPLACE_FLAG.match(a) for a in args)
        ):
            for arg in operands:
                self._check(arg, destructive=name in DESTRUCTIVE_VERBS)
        elif name in DEST_ARG_VERBS:
            self._check(_destination(args), destructive=False)
        elif (
            INTERPRETERS.match(name)
            and RE_MENTIONS_PROTECTED.search(self.raw)
            and RE_WRITE_CALL.search(self.raw)
        ):
            raise Blocked(MSG_SHELL_OPAQUE.format(cmd=name))

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
        if writes and (self.found.mentions_protected or RE_MENTIONS_PROTECTED.search(self.raw)):
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


def _destination(args: list[str]) -> str:
    for i, arg in enumerate(args):
        if arg in ("-t", "--target-directory") and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--target-directory="):
            return arg.split("=", 1)[1]
    operands = [a for a in args if not a.startswith("-")]
    return operands[-1] if operands else ""


def _git_alias(sub: str) -> str:
    res = subprocess.run(
        ["git", "config", "--get", f"alias.{sub}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout.strip() if res.returncode == 0 else ""


def guard_bash(payload: dict[str, Any]) -> None:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command.strip():
        return
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


def check_scope() -> int:
    changed = changed_paths()
    if changed is None:
        say("WARN", "src/ scope check skipped (not a git repo or no commits yet)")
        return 0
    leaf = _current_leaf()
    allowed = set(_harness.leaf_files(leaf) or []) if leaf else set()
    stray = [p for p in changed if classify(p) == "src" and p not in allowed]
    for path in stray:
        owner = f"subtask {leaf}" if leaf else "no in_progress subtask"
        say("FAIL", f"{path} changed outside the declared files: ({owner})")
    if not stray:
        say("OK", "src/ changes are within the in_progress subtask's declared files")
    return 1 if stray else 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_seals() -> int:
    rc = 0
    manifests = sorted((ROOT / "progress").glob("acceptance_*.sha256"))
    for manifest in manifests:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, rel = line.split(maxsplit=1)
            target = ROOT / rel
            if not target.is_file() or _sha256(target) != digest:
                say(
                    "FAIL",
                    f"{rel} no longer matches its seal ({manifest.name}): "
                    "acceptance tests are Codex-owned and must not change",
                )
                rc = 1
    leaf = _current_leaf()
    if (
        leaf
        and _harness.read_verdict(ROOT / f"progress/plan_review_{leaf}.md") == "PLAN_APPROVED"
        and _acceptance_files(leaf)
        and not (ROOT / _manifest_rel(leaf)).exists()
    ):
        say(
            "WARN",
            f"acceptance tests of {leaf} are not sealed "
            f"(human: ./harness/review_with_codex.sh seal {leaf})",
        )
    if rc == 0:
        say("OK", f"sealed acceptance tests intact ({len(manifests)} seal(s))")
    return rc


def check_plan_seals() -> int:
    problems, warnings = _plan_seal.verify(ROOT)
    for msg in problems:
        say("FAIL", msg)
    for msg in warnings:
        say("WARN", msg)
    if not problems:
        say("OK", "approved plan intact (roadmap and phase seals)")
    return 1 if problems else 0


def integrity() -> int:
    return check_scope() | check_seals() | check_plan_seals()


def _acceptance_files(leaf: str) -> list[Path]:
    return sorted((ROOT / "tests" / "acceptance").glob(f"test_{glob.escape(leaf)}.*"))


def _manifest_rel(leaf: str) -> str:
    return f"progress/acceptance_{leaf}.sha256"


def seal(leaf: str) -> int:
    if _harness.read_verdict(ROOT / f"progress/plan_review_{leaf}.md") != "PLAN_APPROVED":
        say("FAIL", f"cannot seal {leaf}: its plan review is not PLAN_APPROVED", sys.stderr)
        return 1
    files = _acceptance_files(leaf)
    if not files:
        say("FAIL", f"cannot seal {leaf}: no tests/acceptance/test_{leaf}.*", sys.stderr)
        return 1
    lines = [f"{_sha256(f)}  {f.relative_to(ROOT).as_posix()}\n" for f in files]
    (ROOT / _manifest_rel(leaf)).write_text("".join(lines), encoding="utf-8")
    say("OK", f"sealed {len(files)} acceptance file(s) -> {_manifest_rel(leaf)}")
    spec, phase = _leaf_spec(leaf)
    if spec and _plan_seal.reseal_spec(ROOT, spec, phase):
        say("OK", f"re-sealed its approved spec {spec}")
    return 0


def _leaf_spec(leaf: str) -> tuple[str, str]:
    item = next((s for s in _harness.leaves() if s["id"] == leaf), None)
    return (str(item["spec"]), str(item["phase"])) if item else ("", "")


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


def seal_command(args: list[str]) -> int:
    if args == ["roadmap"]:
        return seal_roadmap()
    if len(args) == 2 and args[0] == "phase":
        return seal_phase(args[1])
    if len(args) == 1:
        return seal(args[0])
    print("usage: _guard.py seal <N.M.K> | roadmap | phase <NN>", file=sys.stderr)
    return 2


def fingerprint() -> int:
    """Hash HEAD + status + diff + untracked contents; exit 1 outside a git repo."""
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
    print(digest.hexdigest())
    return 0


# ── Entry point ──────────────────────────────────────────────────────────────


def _run_guard(kind: str) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # not a hook payload: nothing to guard
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


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    cmd, *rest = argv or [""]
    if cmd in ("write", "bash"):
        return _run_guard(cmd)
    if cmd == "integrity":
        return integrity()
    if cmd == "seal":
        return seal_command(rest)
    if cmd == "fingerprint":
        return fingerprint()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
