#!/usr/bin/env python3
"""Sealed verification contract (issue #66, I5) — WARN rollout (plan 0.5).

"Done" means lint, types and build pass; this module seals what decides how they run:

  - ruff/mypy config: pyproject.toml [tool.ruff]/[tool.mypy], ruff.toml, .ruff.toml,
    mypy.ini, .mypy.ini, setup.cfg [mypy*] (every location), ruff `extend` targets;
  - flake8 config (#126): .flake8, [flake8*] of setup.cfg / tox.ini, pyproject.toml
    [tool.flake8] (flake8-pyproject), every location;
  - config-path flags in the recipes (--config, --config-file, --rcfile, --settings);
  - every makefile make reads (includes resolved by make itself);
  - the recipes and prerequisites of lint, type, build, verify and the %.o rule, and
    every variable feeding them (origin, flavor, value), transitively;
  - RUFF_*/MYPY* environment variables.

  check                         launcher: compare with the sealed I5 baseline.
  inventory                     the inputs as JSON (name -> digest).
  baseline-create --from <rev>  human: write progress/contract_baseline.json.
  baseline-seal                 human/Codex, after review: seal it.
  contract-propose [--reason R] a change proposal bound to its diff hash.
  contract-review               Codex reviews it (review_with_codex.sh contract).
  contract-approve <hash>       human OK: reseal when proposal, verdict, approval
                                and the current inputs share the hash.

Without a sealed baseline nothing is active (a note says so). Every finding is a
[WARN] with its remedy and exit 0 (FAIL is the I5-fail issue).

A version-1 baseline (sealed before #126) has no flake8 inputs: they are derived from
its sealed `from` commit, so unchanged flake8 config (or none) adds no warning. Any
contract approval reseals it as version 2.
"""

from __future__ import annotations

import configparser
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _oracle  # noqa: E402
from _status import say  # noqa: E402

Inventory = dict[str, str]

ROOT = _oracle.ROOT
BASELINE = "progress/contract_baseline.json"
BASELINE_SEAL = "progress/contract_baseline.sha256"
PROPOSAL = "progress/config_contract.json"
REVIEW = "progress/config_contract_review.md"
CONFIG_NAMES = frozenset(
    {"pyproject.toml", "ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", "setup.cfg"}
)
MAKE_NAMES = frozenset({"Makefile", "GNUmakefile", "makefile"})
FLAKE8_NAMES = (".flake8", "pyproject.toml", "setup.cfg", "tox.ini")
VERSION = 2  # 2: the inventory covers flake8 config (#126)
RE_FLAKE8_SECTION = re.compile(r"^\s*\[flake8(?::[^\]]*)?\]", re.M)
TARGETS = ("lint", "type", "build", "verify", "%.o")
TAG = "verification contract"
FIX = (
    "fix: restore it, or approve the change: python3 harness/_contract.py contract-propose "
    "[--reason R], ./harness/review_with_codex.sh contract, then the human runs "
    "python3 harness/_contract.py contract-approve <hash>"
)
RE_VAR = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_.-]*)[)}:]")
RE_CFG_FLAG = re.compile(
    r"(?:--config-file|--config|--rcfile|--settings(?:-path)?)[= ]+([^\s;|&)]+)"
)
RE_ORIGIN = re.compile(
    r"^# (default|environment(?: override)?|makefile|command line|override|automatic)"
)
RE_VARDEF = re.compile(r"^(define )?([^#\s:=]+) (:?:=|\+=|\?=|=)(?: (.*))?$")
RE_RULE = re.compile(r"^([^#\s:][^:]*?):{1,2}(?:\s+(.*))?$")
TEMPLATE_VARS = ("CC", "CFLAGS", "CSTD", "WARN", "EXTRA_CFLAGS")
SAFE_ENV = frozenset({"PATH", "HOME", "SHELL", "MAKE", "MAKEFLAGS", "MAKELEVEL", "CURDIR"})
OVERRIDE_ORIGINS = ("environment", "environment override", "command line", "override")


def warn(message: str) -> None:
    say("WARN", f"{TAG}: {message}")


def _sha(data: str) -> str:
    return _oracle._sha_bytes(data.encode())


# ── inventory ────────────────────────────────────────────────────────────────


def _make_db() -> str | None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKE", "GNUMAKE"))}
    env.pop("MFLAGS", None)
    try:
        res = subprocess.run(
            ["make", "-pn", "--no-print-directory", "__harness_none__", "MAKECMDGOALS=check"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout


def _recipe(lines: list[str], start: int) -> tuple[list[str], int]:
    """The recipe lines after a rule line, and the index past them."""
    recipe: list[str] = []
    j = start
    while j < len(lines) and lines[j] and not re.match(r"^[^#\t]", lines[j]):
        if lines[j].startswith("\t"):
            recipe.append(lines[j])
        elif lines[j].startswith("#  recipe to execute (built-in)"):
            recipe.append("(built-in)")
        j += 1
    return recipe, j


def _keep_rule(
    rules: dict[str, tuple[str, str]], target: str, deps: str, recipe: list[str]
) -> None:
    if target not in TARGETS or "(built-in)" in recipe:
        return
    if target == "%.o" and deps != "%.c":
        return
    if target not in rules or recipe:
        rules[target] = (deps, "\n".join(recipe))


def _parse_db(db: str) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]], set[str]]:
    """(variables name -> (origin, 'op value'), rules target -> (prereqs, recipe), phony)."""
    variables: dict[str, tuple[str, str]] = {}
    rules: dict[str, tuple[str, str]] = {}
    phony: set[str] = set()
    lines = db.splitlines()
    origin = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith("\t"):
            origin = ""
            continue
        m = RE_ORIGIN.match(line)
        if m:
            origin = m.group(1)
            continue
        vm = RE_VARDEF.match(line)
        if vm and origin:
            variables[vm.group(2)] = (origin, f"{vm.group(3)} {vm.group(4) or ''}")
        origin = ""
        rm = None if vm else RE_RULE.match(line)
        if rm:
            target, deps = rm.group(1).strip(), (rm.group(2) or "").strip()
            recipe, i = _recipe(lines, i)
            if target == ".PHONY":
                phony.update(deps.split())
            _keep_rule(rules, target, deps, recipe)
    return variables, rules, phony


def _pyproject(inv: Inventory, rel: str, path: Path) -> None:
    try:
        tool = tomllib.loads(path.read_text(encoding="utf-8")).get("tool", {})
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        inv[rel] = _oracle._sha_file(path)
        return
    for key in ("ruff", "mypy"):
        if isinstance(tool, dict) and key in tool:
            inv[f"{rel}[tool.{key}]"] = _sha(_oracle._canon(tool[key]))
            if key == "ruff":
                _ruff_extend(inv, path, tool[key])


def _setup_cfg(inv: Inventory, rel: str, path: Path) -> None:
    parser = configparser.RawConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        inv[rel] = _oracle._sha_file(path)
        return
    for sec in parser.sections():
        if sec == "mypy" or sec.startswith("mypy-"):
            inv[f"{rel}[{sec}]"] = _sha(_oracle._canon(dict(parser.items(sec))))


def _config_files(inv: Inventory) -> None:
    for name in sorted(CONFIG_NAMES):
        for path in _oracle._walk(name):
            rel = path.relative_to(ROOT).as_posix()
            if name == "pyproject.toml":
                _pyproject(inv, rel, path)
            elif name == "setup.cfg":
                _setup_cfg(inv, rel, path)
            else:
                inv[rel] = _oracle._sha_file(path)
                if "ruff" in name:
                    with contextlib.suppress(tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
                        _ruff_extend(inv, path, tomllib.loads(path.read_text(encoding="utf-8")))


def _ruff_extend(inv: Inventory, path: Path, cfg: Any) -> None:
    ext = cfg.get("extend") if isinstance(cfg, dict) else None
    if not isinstance(ext, str):
        return
    target = path.parent / ext
    key = f"ruff-extend:{target.resolve()}"
    _hash_ref(inv, "ruff-extend", target)
    if target.is_file() and key not in _SEEN_EXTEND:
        _SEEN_EXTEND.add(key)
        with contextlib.suppress(tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
            _ruff_extend(inv, target, tomllib.loads(target.read_text(encoding="utf-8")))


def _hash_ref(inv: Inventory, kind: str, path: Path) -> None:
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = str(path)
    inv[f"{kind}:{rel}"] = _oracle._sha_file(path) if path.is_file() else "UNRESOLVED"


def _make_inputs(inv: Inventory) -> None:
    if not any((ROOT / m).is_file() for m in MAKE_NAMES):
        return
    makefiles, unresolved = _oracle._makefiles()
    for path in makefiles:
        inv[f"make:{path.relative_to(ROOT).as_posix()}"] = _oracle._sha_file(path)
    for word in unresolved:
        inv[f"make-include:{word}"] = "UNRESOLVED"
    db = _make_db()
    if db is None:
        inv["make-database"] = "UNRESOLVED"
        return
    variables, rules, phony = _parse_db(db)
    pending = _targets(inv, rules, phony, variables)
    # Variables written by the templates feed the recipes through others.
    pending.extend(v for v in TEMPLATE_VARS if v in variables)
    _variables(inv, variables, pending)
    for key in sorted(os.environ):
        if key.startswith(("RUFF_", "MYPY")):
            inv[f"env:{key}"] = _sha(os.environ[key])


def _expand(text: str, variables: dict[str, tuple[str, str]], depth: int = 0) -> str:
    """Substitute $(VAR) by its raw value (a few levels): enough for config paths."""
    if depth > 5:
        return text

    def sub(m: re.Match[str]) -> str:
        val = variables.get(m.group(1))
        return _expand(val[1].split(" ", 1)[-1], variables, depth + 1) if val else m.group(0)

    return re.sub(r"\$[({]([A-Za-z_][A-Za-z0-9_.-]*)[)}]", sub, text)


def _targets(
    inv: Inventory,
    rules: dict[str, tuple[str, str]],
    phony: set[str],
    variables: dict[str, tuple[str, str]],
) -> list[str]:
    """Seal the rules; return the variable names their recipes reference."""
    pending: list[str] = []
    for target in TARGETS:
        if target not in rules:
            continue
        deps, recipe = rules[target]
        kept = " ".join(d for d in deps.split() if d in phony or d in TARGETS or d == "%.c")
        inv[f"target:{target}"] = _sha(f"{kept}\n{recipe}")
        inv[f"target:{target}:prereqs"] = kept
        text = recipe.replace("$$", "")
        pending.extend(RE_VAR.findall(text))
        for match in RE_CFG_FLAG.finditer(_expand(text, variables)):
            word = match.group(1).strip("'\"")
            if "$" in word:
                inv[f"config-path:{word}"] = "UNRESOLVED"
            else:
                _hash_ref(inv, "config-path", ROOT / word)
    return pending


def _variables(inv: Inventory, variables: dict[str, tuple[str, str]], pending: list[str]) -> None:
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name in SAFE_ENV or name not in variables:
            continue
        seen.add(name)
        origin, value = variables[name]
        if origin == "automatic":
            continue
        # A simple (:=) makefile variable is printed expanded ($(shell find …) lists
        # files): its definition is covered by the makefile's hash instead.
        shown = value.split(" ", 1)[0] if origin == "makefile" and value.startswith(":") else value
        inv[f"var:{name}"] = _sha(f"{origin}|{shown}")
        inv[f"var:{name}:origin"] = origin
        pending.extend(RE_VAR.findall(value.replace("$$", "")))


def _flake8_entries(rel: str, data: bytes) -> Inventory:
    """The flake8 inputs of one config file (`rel`'s basename is in FLAKE8_NAMES)."""
    name = Path(rel).name
    if name == ".flake8":
        return {rel: _oracle._sha_bytes(data)}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if name == "pyproject.toml":
        # Unreadable: the whole file is already sealed by _pyproject.
        with contextlib.suppress(tomllib.TOMLDecodeError):
            tool = tomllib.loads(text).get("tool", {}) if text is not None else {}
            if isinstance(tool, dict) and "flake8" in tool:
                return {f"{rel}[tool.flake8]": _sha(_oracle._canon(tool["flake8"]))}
        return {}
    parser = configparser.RawConfigParser()
    try:
        if text is None:
            raise configparser.Error("not UTF-8")
        parser.read_string(text, source=rel)
    except configparser.Error:
        # setup.cfg is then sealed whole by _setup_cfg; tox.ini only if it has flake8.
        flake8 = name == "tox.ini" and RE_FLAKE8_SECTION.search(data.decode("utf-8", "replace"))
        return {rel: _oracle._sha_bytes(data)} if flake8 else {}
    return {
        f"{rel}[{sec}]": _sha(_oracle._canon(dict(parser.items(sec))))
        for sec in parser.sections()
        if sec == "flake8" or sec.startswith("flake8:")
    }


def _flake8_files(inv: Inventory) -> None:
    for base, dirs, files in os.walk(ROOT):  # one pass, pruned as _oracle._walk
        dirs[:] = sorted(d for d in dirs if d not in _oracle.PRUNE)
        for name in sorted(set(files).intersection(FLAKE8_NAMES)):
            path = Path(base) / name
            with contextlib.suppress(OSError):
                inv.update(_flake8_entries(path.relative_to(ROOT).as_posix(), path.read_bytes()))


def _tree_at(commit: str) -> dict[str, tuple[str, str]]:
    """path -> (mode, blob) of the files tracked at `commit`, below ROOT."""
    listing = _oracle._git("ls-tree", "-r", "-z", commit) or ""
    tree: dict[str, tuple[str, str]] = {}
    for entry in listing.split("\0"):
        meta, _, rel = entry.partition("\t")
        fields = meta.split()
        if rel and len(fields) == 3 and fields[1] == "blob":
            tree[rel] = (fields[0], fields[2])
    return tree


def _resolve_at(tree: dict[str, tuple[str, str]], rel: str) -> str | None:
    """`rel` with every symlink in its path resolved inside the commit, as the live read
    resolves it; None when a link leaves ROOT (unknown then: fail closed)."""
    for _ in range(40):
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            mode, sha = tree.get("/".join(parts[:i]), ("", ""))
            if mode == "120000":
                target = _oracle._git("cat-file", "blob", sha) or ""
                rel = os.path.normpath(
                    os.path.join(os.path.dirname("/".join(parts[:i])), target, *parts[i:])
                )
                if not target or os.path.isabs(target) or rel == ".." or rel.startswith("../"):
                    return None
                break
        else:
            return rel
    return None


def _blob_at(tree: dict[str, tuple[str, str]], rel: str) -> bytes | None:
    """The content `rel` reads as at that commit, or None when a link leaves ROOT.
    Raises FileNotFoundError for a broken link (the live read fails and adds nothing)."""
    resolved = _resolve_at(tree, rel)
    if resolved is None:
        return None
    if resolved not in tree:
        raise FileNotFoundError(resolved)
    blob = _oracle._git("cat-file", "blob", tree[resolved][1])
    if blob is None:
        raise FileNotFoundError(resolved)
    return blob.encode("utf-8", "surrogateescape")


def _live_flake8(rel: str) -> bool:
    try:
        return bool(_flake8_entries(rel, (ROOT / rel).read_bytes()))
    except OSError:
        return False


def _flake8_at(commit: str) -> Inventory:
    """The flake8 inputs as of `commit` (tracked files, same pruning), or {} if unreadable:
    then the current flake8 inputs read as added (fail closed)."""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        return {}
    tree = _tree_at(commit)
    inv: Inventory = {}
    for rel in tree:
        parts = rel.split("/")
        if parts[-1] not in FLAKE8_NAMES or _oracle.PRUNE.intersection(parts[:-1]):
            continue
        try:
            data = _blob_at(tree, rel)
        except FileNotFoundError:
            continue
        if data is not None:
            inv.update(_flake8_entries(rel, data))
        elif parts[-1] == ".flake8" or _live_flake8(rel):
            # A shared file (setup.cfg…) without flake8 today stays silent (residual gap).
            inv[rel] = "UNRESOLVED"
    return inv


def sealed_inputs(base: dict[str, Any]) -> Inventory:
    """The sealed inputs; a version-1 baseline gets its flake8 side from its commit."""
    inputs: Inventory = dict(base.get("inputs") or {})
    version, commit = base.get("version"), base.get("from")
    if isinstance(version, int) and version >= VERSION or not isinstance(commit, str):
        return inputs
    # A sealed v1 key keeps its sealed value over a derived one of the same name.
    return {**_flake8_at(commit), **inputs}


_SEEN_EXTEND: set[str] = set()


def inventory() -> Inventory:
    inv: Inventory = {}
    _SEEN_EXTEND.clear()
    _config_files(inv)
    _flake8_files(inv)
    _make_inputs(inv)
    return inv


def config_input(rel: str, known: Inventory | None = None) -> bool:
    """`rel` decides how lint/type/build run (the guard warns on Claude's edit)."""
    name = Path(rel).name
    if name in CONFIG_NAMES or name in MAKE_NAMES or name == ".flake8" or rel.endswith(".mk"):
        return True
    if name == "tox.ini":
        with contextlib.suppress(OSError):
            if RE_FLAKE8_SECTION.search((ROOT / rel).read_text("utf-8", "replace")):
                return True
        base = baseline()[0]
        sealed = {**(known or {}), **(sealed_inputs(base) if base else {})}
        if any(k == rel or k.startswith(f"{rel}[") for k in sealed):
            return True
    return any(k.split(":", 1)[-1] == rel for k in (known or {}) if ":" in k)


def _changes(base: Inventory, now: Inventory) -> list[str]:
    return sorted(k for k in set(base) | set(now) if base.get(k) != now.get(k))


def _label(key: str) -> str:
    return (
        key.split(":", 1)[1] if key.startswith(("make:", "config-path:", "ruff-extend:")) else key
    )


# ── baseline ─────────────────────────────────────────────────────────────────


def _load(rel: str) -> dict[str, Any] | None:
    try:
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def baseline() -> tuple[dict[str, Any] | None, bool]:
    """(baseline, intact). None = not created: I5 is inactive."""
    path = ROOT / BASELINE
    if not path.is_file():
        if (ROOT / BASELINE_SEAL).is_file():
            return {"inputs": {}}, False  # sealed but deleted: fail closed
        return None, False
    data = _load(BASELINE) or {"inputs": {}}
    seal = ROOT / BASELINE_SEAL
    want = seal.read_text(encoding="utf-8").split(maxsplit=1) if seal.is_file() else []
    return data, bool(want) and want[0] == _oracle._sha_file(path)


def _write_baseline(data: dict[str, Any]) -> None:
    path = ROOT / BASELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _seal() -> None:
    digest = _oracle._sha_file(ROOT / BASELINE)
    (ROOT / BASELINE_SEAL).write_text(f"{digest}  {BASELINE}\n", encoding="utf-8")


def baseline_create(args: list[str]) -> int:
    if len(args) != 2 or args[0] != "--from":
        print("usage: _contract.py baseline-create --from <pre-rule commit>", file=sys.stderr)
        return 2
    commit = _oracle._git("rev-parse", "--verify", "-q", f"{args[1]}^{{commit}}")
    if commit is None:
        say("FAIL", f"baseline-create: {args[1]} is not a commit", sys.stderr)
        return 1
    commit = commit.strip()
    inv = inventory()
    files = {
        k.split("[", 1)[0].split(":", 1)[-1]
        for k in inv
        if not k.startswith(("var:", "target:", "env:"))
    }
    for rel in sorted(f for f in files if (ROOT / f).is_file()):
        blob = _oracle._git("rev-parse", "-q", "--verify", f"{commit}:{rel}")
        have = _oracle._git("hash-object", "--", rel)
        if blob is None or have is None or blob.strip() != have.strip():
            warn(f"baseline-create: {rel} differs from {commit[:12]}: review it before sealing")
    tree = _oracle._git("ls-tree", "-r", "--name-only", commit) or ""
    for rel in tree.splitlines():
        name = Path(rel).name
        watched = (
            name in CONFIG_NAMES
            or name in MAKE_NAMES
            or name == ".flake8"  # a tox.ini is watched only through its [flake8] key
            or rel.endswith(".mk")
        )
        if watched and not (ROOT / rel).exists():
            warn(f"baseline-create: {rel} existed at {commit[:12]} and is gone: review it")
    for key, origin in inv.items():
        if key.endswith(":origin") and origin in OVERRIDE_ORIGINS:
            warn(f"baseline-create: {key[:-7]} comes from the {origin}: unset it before sealing")
    data = {
        "version": VERSION,
        "from": commit,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": inv,
    }
    _write_baseline(data)
    say("OK", f"wrote {BASELINE} from {commit[:12]}: have Codex review it, then baseline-seal")
    return 0


def baseline_seal(_: list[str]) -> int:
    if _load(BASELINE) is None:
        say("FAIL", f"baseline-seal: no readable {BASELINE}", sys.stderr)
        return 1
    _seal()
    say("OK", f"sealed {BASELINE} -> {BASELINE_SEAL}: I5 WARN checks are active")
    return 0


# ── check (launcher) ─────────────────────────────────────────────────────────


def check(_: list[str]) -> int:
    base, intact = baseline()
    if base is None:
        print(
            "note: I5 inactive: no sealed I5 baseline (progress/contract_baseline.json) — the "
            "human creates it: python3 harness/_contract.py baseline-create --from <pre-rule "
            "commit>, Codex reviews it, then python3 harness/_contract.py baseline-seal"
        )
        return 0
    if not intact:
        warn(
            f"the I5 baseline {BASELINE} does not match its seal {BASELINE_SEAL} (missing or "
            f"changed outside the contract flow) — fix: git checkout -- {BASELINE} "
            f"{BASELINE_SEAL}, or reseal through contract-approve"
        )
    sealed = sealed_inputs(base)
    now = inventory()
    for key in sorted(now):
        if now[key] == "UNRESOLVED":
            warn(f"{_label(key)}: unresolved, so its content is not covered (fail closed) — {FIX}")
        elif key.endswith(":origin") and now[key] in OVERRIDE_ORIGINS:
            warn(f"{key[:-7]} is overridden from the {now[key]} — fix: unset it")
        elif key.endswith(":origin") and key[4:-7] in os.environ:
            # The makefile wins here, but an unaccounted override fails closed.
            warn(
                f"{key[:-7]} is also set in the environment (unaccounted override) — "
                f"fix: unset {key[4:-7]}"
            )
    for key in _changes(sealed, now):
        if key.endswith((":origin", ":prereqs")) and key.rsplit(":", 1)[0] in _changes(sealed, now):
            continue
        state = "added" if key not in sealed else "removed" if key not in now else "changed"
        extra = ""
        if key.startswith("target:"):
            was, is_ = sealed.get(key + ":prereqs", "-"), now.get(key + ":prereqs", "-")
            extra = f" (prerequisites: {was} -> {is_})"
        warn(f"{_label(key)} {state} since the sealed I5 baseline{extra} — {FIX}")
    return 0


# ── contract flow ────────────────────────────────────────────────────────────


def _digest(base: Inventory, now: Inventory, reason: str) -> str:
    return _sha(_oracle._canon({"from": base, "to": now, "reason": reason}))


def contract_propose(args: list[str]) -> int:
    reason = ""
    if args[:1] == ["--reason"] and len(args) == 2:
        reason = args[1]
    elif args:
        print("usage: _contract.py contract-propose [--reason R]", file=sys.stderr)
        return 2
    base, _ = baseline()
    if base is None:
        return check([])
    sealed = sealed_inputs(base)
    now = inventory()
    changes = _changes(sealed, now)
    if not changes:
        say("OK", "contract: lint/type/build inputs match the I5 baseline; nothing to propose")
        return 0
    digest = _digest(sealed, now, reason)
    data = {
        "diff": digest,
        "from": sealed,
        "to": now,
        "reason": reason,
        "changes": sorted({_label(k).split("[", 1)[0] for k in changes}),
        "keys": changes,
    }
    (ROOT / PROPOSAL).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    say(
        "OK", f"{TAG} proposal {digest} -> {PROPOSAL}: next ./harness/review_with_codex.sh contract"
    )
    return 0


def contract_review(_: list[str]) -> int:
    prop = _load(PROPOSAL)
    if prop is None:
        warn(f"no {PROPOSAL} — fix: python3 harness/_contract.py contract-propose")
        return 0
    digest = str(prop.get("diff", ""))
    base = baseline()[0]
    since = str((base or {}).get("from") or "HEAD")
    files = [f for f in prop.get("changes", []) if not f.startswith(("var:", "target:", "env:"))]
    diff = (_oracle._git("diff", since, "--", *files) or "") if files else ""
    for rel in files:
        path = ROOT / rel
        if path.is_file() and path.stat().st_size < 50_000:
            diff += f"\n--- current content of {rel} ---\n{path.read_text('utf-8', 'replace')}"
    db = _make_db() or ""
    diff += "\n--- make database (variables and rules as make reads them) ---\n"
    diff += "\n".join(ln for ln in db.splitlines() if not ln.startswith("#"))[:40_000]
    prompt = (
        f"You review a {TAG} change of a harness project: the config of lint, type checking "
        "and build (ruff/mypy config, Makefile recipes, prerequisites, variables, flags). "
        "APPROVE a change that only tightens the checks without needing a reason. A change "
        "that may relax any check (ignored rules, lower strictness, fewer warnings, a "
        "removed prerequisite of verify, a different compiler or flags) gets APPROVED only "
        "if the proposal's `reason` justifies it; otherwise CHANGES_REQUESTED.\n"
        f"Proposal ({PROPOSAL}, diff hash {digest}):\n{json.dumps(prop, indent=2)}\n"
        f"git diff HEAD of the files:\n{diff}\n"
        "Answer with a line `**Verdict:** APPROVED` or `**Verdict:** CHANGES_REQUESTED` "
        "and your reasons."
    )
    if os.environ.get("HARNESS_CODEX_MODE") == "manual":
        print(prompt)
        print(f"--- write {REVIEW} with the verdict and the line `diff: {digest}` ---")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "last.md"
        res = subprocess.run(
            ["codex", "exec", "--sandbox", "read-only", "-o", str(out), "-"],
            input=prompt.encode(),
            cwd=ROOT,
            check=False,
        )
        text = out.read_text(encoding="utf-8") if out.is_file() else ""
    verdict = _oracle._review_of(text)[0]
    if res.returncode != 0 or verdict not in ("APPROVED", "CHANGES_REQUESTED"):
        warn(
            "the reviewer gave no valid verdict; nothing recorded — fix: run "
            "./harness/review_with_codex.sh contract again"
        )
        return 0
    (ROOT / REVIEW).write_text(
        f"# Verification contract review\n\n**Verdict:** {verdict}\n\ndiff: {digest}\n\n{text}\n",
        encoding="utf-8",
    )
    say(
        "OK",
        f"{TAG} review {verdict} for {digest}: the human approves with "
        f"python3 harness/_contract.py contract-approve {digest}",
    )
    return 0


def contract_approve(args: list[str]) -> int:
    if len(args) != 1:
        return 2
    want = args[0]
    base, _ = baseline()
    prop = _load(PROPOSAL)
    review = ROOT / REVIEW
    problems: list[str] = []
    now = inventory()
    if base is None or prop is None:
        problems.append(f"no I5 baseline or no {PROPOSAL}")
    else:
        frm, to = prop.get("from") or {}, prop.get("to") or {}
        if prop.get("diff") != want or _digest(frm, to, str(prop.get("reason", ""))) != want:
            problems.append("the proposal does not hash to this diff (edited after review?)")
        if frm != sealed_inputs(base) or to != now:
            problems.append("the inputs or the baseline changed after the proposal")
        verdict, digest = _oracle._review_of(review.read_text("utf-8") if review.is_file() else "")
        if verdict != "APPROVED" or digest != want:
            problems.append(f"{REVIEW} does not approve this diff hash")
    if problems or base is None:
        for problem in problems:
            warn(
                f"contract-approve {want[:12]}: refused, nothing resealed: {problem} — fix: "
                "python3 harness/_contract.py contract-propose, then "
                "./harness/review_with_codex.sh contract"
            )
        return 0
    base["inputs"] = now
    base["version"] = VERSION
    base.setdefault("contracts", []).append(
        {"diff": want, "approved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    )
    _write_baseline(base)
    _seal()
    (ROOT / PROPOSAL).unlink(missing_ok=True)
    say("OK", f"{TAG} {want[:12]} approved: I5 baseline resealed")
    return 0


COMMANDS = {
    "check": check,
    "inventory": lambda _: print(json.dumps(inventory(), indent=2, sort_keys=True)) or 0,
    "baseline-create": baseline_create,
    "baseline-seal": baseline_seal,
    "contract-propose": contract_propose,
    "contract-review": contract_review,
    "contract-approve": contract_approve,
}


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    cmd, *rest = argv or [""]
    command = COMMANDS.get(cmd)
    if command is None:
        print(__doc__, file=sys.stderr)
        return 2
    return int(command(rest))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
