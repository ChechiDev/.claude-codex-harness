#!/usr/bin/env python3
"""Oracle execution integrity (issue #65, I4b) — WARN rollout (plan 0.5).

Sealed oracles (tests/acceptance/, sealed in progress/acceptance_<leaf>.sha256) only
prove something if the normal check selects and executes them. This module:

  evidence [--junit F] [--clog F] [--expect-red a,b]
      after a launcher run: every sealed oracle of a done/in_progress/baseline leaf
      was executed (pytest junit xunit1 nodes; the C adapter's run log), done ones
      passed and were not skipped/xfailed, C sources are not stale; and the
      selection inputs match the sealed I4 baseline.
  inventory                     the selection inputs as JSON (name -> digest).
  baseline-create --from <rev>  human: write progress/oracle_baseline.json.
  baseline-seal                 human/Codex, after review: seal it.
  red-record <leaf>             plan gate, after Codex wrote the oracle: run it
                                alone and record it collected and failing.
  red-drop <leaf>               plan gate start / rejection: drop the old record.
  red-check <leaf>              code gate: the record exists and neither the
                                oracle nor the selection inputs moved since.
  contract-propose              a selection change proposal bound to its diff hash.
  contract-review               Codex reviews the proposal (review_with_codex.sh contract).
  contract-approve <hash>       human OK: reseal the baseline when proposal, verdict,
                                approval and the current inputs share the hash.

Without a sealed baseline none of the checks is active (a note says so). Every
finding is a [WARN] with its remedy; the exit code never changes (FAIL is the
I4-fail issue). Only the contract and baseline commands return non-zero, on refusal.
"""

from __future__ import annotations

import ast
import configparser
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness  # noqa: E402
from _status import say  # noqa: E402

ROOT = Path(os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()))
BASELINE = "progress/oracle_baseline.json"
BASELINE_SEAL = "progress/oracle_baseline.sha256"
PROPOSAL = "progress/oracle_contract.json"
CONTRACT_REVIEW = "progress/oracle_contract_review.md"
LIVE = ("done", "in_progress", "baseline")
MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
INI_FILES = ("pytest.ini", ".pytest.ini")
PRUNE = frozenset(
    {".git", ".venv", "venv", "node_modules", "harness", ".claude", "__pycache__", ".harness"}
)
SELECTION_FILES = frozenset(
    {*MAKEFILES, *INI_FILES, "pyproject.toml", "setup.cfg", "tox.ini", "package.json"}
)
FIX_CONTRACT = (
    "fix: revert it, or propose it (python3 harness/_oracle.py contract-propose; "
    "./harness/review_with_codex.sh contract; the human approves with "
    "python3 harness/_oracle.py contract-approve <hash>)"
)
RE_DUMMY_GOAL = re.compile(
    r"^make(\[\d+\])?: \*\*\* No rule to make target '__harness_none__'\.\s+Stop\.$"
)
RE_INCLUDE = re.compile(r"^\s*-?(?:include|sinclude)\s+(.+)$")
RE_VERDICT = re.compile(r"\*\*Verdict:\*\*\s*([A-Z_]+)")
RE_DIFF = re.compile(r"^diff:\s*([0-9a-f]{64})\s*$", re.M)

Inventory = dict[str, str]


def warn(message: str) -> None:
    say("WARN", message)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _git(*args: str) -> str | None:
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)
    return res.stdout.decode("utf-8", "surrogateescape") if res.returncode == 0 else None


def _git_path(name: str) -> Path:
    out = _git("rev-parse", "--git-path", name)
    path = Path(out.strip()) if out else Path(".git") / name
    return path if path.is_absolute() else ROOT / path


# ── selection inputs ─────────────────────────────────────────────────────────


def _walk(name: str) -> list[Path]:
    found: list[Path] = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in PRUNE)
        if name in files:
            found.append(Path(base) / name)
    return found


def _make_db_files() -> list[Path] | None:
    """MAKEFILE_LIST as make itself resolves it (variables, includes), or None."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("MAKE", "GNUMAKE", "PYTEST_")) and k not in ("MFLAGS", "TESTS")
    }
    try:
        res = subprocess.run(
            # As the real run parses it: MAKECMDGOALS=check (a goal conditional must
            # not hide an include), with a dummy goal so no recipe runs.
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
    # The dummy goal always fails ("No rule to make target"); any other make error (a
    # missing include, a parse error) means the list may be incomplete.
    for line in res.stderr.splitlines():
        if RE_DUMMY_GOAL.match(line):
            continue
        if "No such file or directory" in line or "***" in line:
            return None
    for line in res.stdout.splitlines():
        match = re.match(r"^MAKEFILE_LIST :?= (.*)$", line)
        if match:
            return [ROOT / w for w in match.group(1).split() if (ROOT / w).is_file()]
    return None


def _lexical_includes(path: Path) -> tuple[list[Path], list[str]]:
    """(included files, variable include words) of one makefile, read as text."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], []
    found: list[Path] = []
    words: list[str] = []
    for line in lines:
        match = RE_INCLUDE.match(line)
        for word in match.group(1).split() if match else []:
            if word.startswith("harness/"):
                continue
            if "$" in word:
                words.append(word)
            else:
                found.extend(sorted(p for p in ROOT.glob(word) if p.is_file()))
    return found, words


def _makefiles() -> tuple[list[Path], list[str]]:
    """(makefiles, unresolved include words): what make reads (its database) plus the
    lexical includes; the toolkit's harness/harness.mk is covered by the toolkit. A
    variable include counts as unresolved only when make's database is unreadable."""
    queue = [ROOT / m for m in MAKEFILES if (ROOT / m).is_file()]
    db = _make_db_files() if queue else []
    queue.extend(db or [])
    unresolved: list[str] = [] if db is not None else ["(make database unreadable)"]
    seen: list[Path] = []
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.append(path)
        found, words = _lexical_includes(path)
        queue.extend(found)
        if db is None:
            unresolved.extend(words)
    inside = [p for p in seen if p.resolve().is_relative_to(ROOT)]
    files = [p for p in inside if not p.relative_to(ROOT).as_posix().startswith("harness/")]
    return files, unresolved


def _section(path: Path, section: str) -> str | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return _sha_file(path)
    if not parser.has_section(section):
        return None
    return _sha_bytes(_canon(dict(parser.items(section))).encode())


def _site_packages() -> list[Path]:
    return sorted(p for p in ROOT.glob(".venv/lib/*/site-packages") if p.is_dir())


def _module_file(site: Path, module: str) -> Path | None:
    base = site.joinpath(*module.split("."))
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _plugins() -> Inventory:
    """pytest11 entry points of the project venv: dist, version, module path and
    content hash (a plugin changed under the same name moves its digest)."""
    out: Inventory = {}
    for site in _site_packages():
        for eps in sorted(site.glob("*.dist-info/entry_points.txt")):
            dist, _, version = eps.parent.name.removesuffix(".dist-info").partition("-")
            parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
            parser.optionxform = str  # type: ignore[assignment,method-assign]
            try:
                parser.read(eps, encoding="utf-8")
            except (configparser.Error, OSError, UnicodeDecodeError):
                out[f"plugin:{dist}"] = "UNRESOLVED"
                continue
            if not parser.has_section("pytest11"):
                continue
            for name, target in parser.items("pytest11"):
                module = target.split(":", 1)[0].strip()
                path = _module_file(site, module)
                if path is None:
                    out[f"plugin:{name}"] = "UNRESOLVED"
                    continue
                rel = path.relative_to(ROOT).as_posix()
                out[f"plugin:{name}"] = f"{dist}=={version} {rel} {_sha_file(path)}"
    return out


RE_PLUGINS_VAR = re.compile(r"pytest_plugins\s*=\s*(.+)")
RE_P_FLAG = re.compile(r"(?:^|\s)-p\s*([A-Za-z_][\w.]*)")
RE_MODNAME = re.compile(r"['\"]([A-Za-z_][\w.]*)['\"]")


def _python_plugins(text: str) -> tuple[set[str], bool]:
    """(names, complete) of a module's top-level `pytest_plugins` assignments, parsed as
    Python (multiline lists too). complete=False: a non-literal value or a syntax error."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set(), "pytest_plugins" not in text
    names: set[str] = set()
    complete = True
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
            continue
        value = getattr(node, "value", None)
        try:
            lit = ast.literal_eval(value) if value is not None else None
        except ValueError:
            complete = False
            continue
        items = [lit] if isinstance(lit, str) else lit if isinstance(lit, (list, tuple)) else []
        if not items and lit:
            complete = False
        names.update(i for i in items if isinstance(i, str))
    return names, complete


def _named_plugins(texts: list[tuple[str, bool]]) -> tuple[set[str], bool]:
    """Module names loaded by `pytest_plugins` (Python sources) or `-p name` (config and
    makefiles); complete=False when a declaration cannot be read."""
    names: set[str] = set()
    complete = True
    for text, is_python in texts:
        if is_python:
            found, ok = _python_plugins(text)
            names |= found
            complete &= ok
        else:
            for match in RE_PLUGINS_VAR.finditer(text):
                names.update(RE_MODNAME.findall(match.group(1)))
        names.update(n for n in RE_P_FLAG.findall(text) if not n.startswith("no"))
    return names, complete


def _plugin_modules(inv: Inventory, sources: list[Path]) -> None:
    texts: list[tuple[str, bool]] = []
    for path in sources:
        try:
            texts.append((path.read_text(encoding="utf-8", errors="replace"), path.suffix == ".py"))
        except OSError:
            continue
    names, complete = _named_plugins(texts)
    if not complete:
        inv["plugin-module:(pytest_plugins not a literal)"] = "UNRESOLVED"
    for name in sorted(names):
        bases = [ROOT, ROOT / "src", *_site_packages()]
        found = next((f for b in bases if (f := _module_file(b, name)) is not None), None)
        if found is None:
            inv[f"plugin-module:{name}"] = "UNRESOLVED"
        else:
            rel = found.relative_to(ROOT).as_posix()
            inv[f"plugin-module:{name}"] = f"{rel} {_sha_file(found)}"


def inventory() -> Inventory:
    inv: Inventory = {}
    makefiles, unresolved = _makefiles()
    for path in makefiles:
        inv[path.relative_to(ROOT).as_posix()] = _sha_file(path)
    for word in unresolved:
        inv[f"make-include:{word}"] = "UNRESOLVED"
    conftests = _walk("conftest.py")
    for path in conftests:
        inv[path.relative_to(ROOT).as_posix()] = _sha_file(path)
    for name in INI_FILES:
        if (ROOT / name).is_file():
            inv[name] = _sha_file(ROOT / name)
    _config_inputs(inv)
    inv.update(_plugins())
    configs = [ROOT / n for n in (*INI_FILES, "pyproject.toml", "setup.cfg", "tox.ini")]
    _plugin_modules(inv, [*conftests, *(p for p in configs if p.is_file()), *makefiles])
    return inv


def _config_inputs(inv: Inventory) -> None:
    """The pytest sections of the shared config files and the npm test scripts."""
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        try:
            tool = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {})
            pytest_cfg = tool.get("pytest") if isinstance(tool, dict) else None
            if pytest_cfg is not None:
                inv["pyproject.toml[tool.pytest]"] = _sha_bytes(_canon(pytest_cfg).encode())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            inv["pyproject.toml"] = _sha_file(pyproject)
    for name, section in (("setup.cfg", "tool:pytest"), ("tox.ini", "pytest")):
        if (ROOT / name).is_file():
            digest = _section(ROOT / name, section)
            if digest is not None:
                inv[f"{name}[{section}]"] = digest
    package = ROOT / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
            tests = {k: v for k, v in scripts.items() if re.match(r"^(pre|post)?test", k)}
            if tests:
                inv["package.json[scripts.test]"] = _sha_bytes(_canon(tests).encode())
        except (ValueError, AttributeError):
            inv["package.json"] = _sha_file(package)


def selection_input(rel: str, known: Inventory | None = None) -> bool:
    """`rel` (project-relative) is a test selection input the guard reports: a known
    name, or a file the sealed inventory resolved (an included makefile, a plugin)."""
    name = Path(rel).name
    if name == "conftest.py" or rel in SELECTION_FILES or rel.endswith(".mk"):
        return True
    return any(key == rel or rel in value.split(" ") for key, value in (known or {}).items())


def _inventory_digest(inv: Inventory) -> str:
    return _sha_bytes(_canon(inv).encode())


def _selection_changes(base: Inventory, now: Inventory) -> list[str]:
    return sorted(k for k in set(base) | set(now) if base.get(k) != now.get(k))


# ── oracles ──────────────────────────────────────────────────────────────────


def oracles() -> list[tuple[str, str, str, str]]:
    """(leaf, status, rel, sealed sha) of every sealed oracle of a live leaf."""
    out: list[tuple[str, str, str, str]] = []
    try:
        leaves = _harness.leaves()
    except (OSError, ValueError, KeyError):
        return out
    for leaf in leaves:
        if leaf.get("status") not in LIVE:
            continue
        manifest = ROOT / f"progress/acceptance_{leaf['id']}.sha256"
        if not manifest.is_file():
            continue
        for line in manifest.read_text(encoding="utf-8").splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                out.append((str(leaf["id"]), str(leaf["status"]), parts[1], parts[0]))
    return out


# ── baseline ─────────────────────────────────────────────────────────────────


def _load(rel: str) -> dict[str, Any] | None:
    try:
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def baseline() -> tuple[dict[str, Any] | None, bool]:
    """(baseline, intact). None = not created or not sealed: I4b is inactive."""
    seal = ROOT / BASELINE_SEAL
    path = ROOT / BASELINE
    if not seal.is_file() or not path.is_file():
        return None, False
    data = _load(BASELINE)
    if data is None:
        return {"selection": {}, "exempt_in_progress": []}, False
    want = seal.read_text(encoding="utf-8").split(maxsplit=1)
    return data, bool(want) and want[0] == _sha_file(path)


def _inactive_note() -> None:
    print(
        "note: I4b inactive: no sealed I4 baseline (progress/oracle_baseline.json) — the "
        "human creates it: python3 harness/_oracle.py baseline-create --from <pre-rule commit>, "
        "Codex reviews it, then python3 harness/_oracle.py baseline-seal"
    )


def _active() -> dict[str, Any] | None:
    data, intact = baseline()
    if data is None:
        _inactive_note()
        return None
    if not intact:
        warn(
            f"the I4 baseline {BASELINE} does not match its seal {BASELINE_SEAL}: it changed "
            "outside the contract flow — fix: restore it from git (git checkout -- "
            f"{BASELINE}) or reseal through contract-approve"
        )
    return data


def _write_baseline(data: dict[str, Any]) -> None:
    path = ROOT / BASELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _seal_baseline() -> None:
    (ROOT / BASELINE_SEAL).write_text(
        f"{_sha_file(ROOT / BASELINE)}  {BASELINE}\n", encoding="utf-8"
    )


def baseline_create(args: list[str]) -> int:
    if len(args) != 2 or args[0] != "--from":
        print("usage: _oracle.py baseline-create --from <pre-rule commit>", file=sys.stderr)
        return 2
    commit = _git("rev-parse", "--verify", "-q", f"{args[1]}^{{commit}}")
    if commit is None:
        say("FAIL", f"baseline-create: {args[1]} is not a commit", sys.stderr)
        return 1
    commit = commit.strip()
    inv = inventory()
    for key in sorted(inv):
        rel = key.split("[", 1)[0]
        if key.startswith("plugin:") or not (ROOT / rel).is_file():
            continue
        blob = _git("rev-parse", "-q", "--verify", f"{commit}:{rel}")
        have = _git("hash-object", "--", rel)
        if blob is None or have is None or blob.strip() != have.strip():
            warn(f"baseline-create: {rel} differs from {commit[:12]}: review it before sealing")
    records = _git_path("harness-oracle")
    live = oracles()
    data = {
        "version": 1,
        "from": commit,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selection": inv,
        "oracles": {rel: sha for _, status, rel, sha in live if status in ("done", "baseline")},
        "exempt_in_progress": sorted(
            {
                leaf
                for leaf, status, _, _ in live
                if status == "in_progress" and not (records / f"red_{leaf}.json").is_file()
            }
        ),
    }
    _write_baseline(data)
    say("OK", f"wrote {BASELINE} from {commit[:12]}: have Codex review it, then baseline-seal")
    return 0


def baseline_seal(_: list[str]) -> int:
    if _load(BASELINE) is None:
        say("FAIL", f"baseline-seal: no readable {BASELINE}", sys.stderr)
        return 1
    _seal_baseline()
    say("OK", f"sealed {BASELINE} -> {BASELINE_SEAL}: I4b WARN checks are active")
    return 0


# ── execution evidence ───────────────────────────────────────────────────────


def _junit_cases(path: Path) -> dict[str, list[tuple[str, str]]] | None:
    """file -> [(test name, outcome)], outcome passed|failed|error|skipped. A collection
    error is an `error` node named after the module: never a collected, failing test."""
    try:
        tree = ET.parse(path)  # noqa: S314 - the project's own pytest output
    except (OSError, ET.ParseError):
        return None
    cases: dict[str, list[tuple[str, str]]] = {}
    for case in tree.iter("testcase"):
        rel = (case.get("file") or "").replace(os.sep, "/").removeprefix("./")
        outcome = "passed"
        for child in case:
            if child.tag == "failure":
                outcome = "failed"
            elif child.tag == "error" and outcome != "failed":
                outcome = "error"
            elif child.tag == "skipped" and outcome == "passed":
                outcome = "skipped"
        cases.setdefault(rel, []).append((case.get("name") or "?", outcome))
    return cases


def _clog(path: Path) -> dict[str, tuple[str, str]] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    out: dict[str, tuple[str, str]] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) == 3:
            out[parts[0].removeprefix("./")] = (parts[1], parts[2])
    return out


def _check_python(rel: str, status: str, cases: dict[str, list[tuple[str, str]]] | None) -> None:
    if cases is None:
        warn(
            f"oracle {rel}: cannot establish that it ran (no pytest junit report from the "
            "check) — fix: let harness/run_check.sh run pytest (no --junitxml in the Makefile)"
        )
        return
    got = [c for f, cs in cases.items() if f and (rel == f or rel.endswith(f"/{f}")) for c in cs]
    ran = [c for c in got if c[1] in ("passed", "failed")]
    if not got or not ran:
        warn(
            f"oracle {rel}: not executed by the normal check (deselected, ignored, all "
            "skipped or a collection error) — fix: restore the test selection "
            "(Makefile test, conftest.py, pytest "
            "config) so it runs"
        )
    if status != "done":
        return
    for name, outcome in got:
        if outcome == "skipped":
            warn(
                f"oracle {rel}::{name} of a done leaf was skipped or xfailed — fix: remove the "
                "skip/xfail (oracles are Codex-owned; report it as a blocker)"
            )
        elif outcome in ("failed", "error"):
            warn(f"oracle {rel}::{name} of a done leaf did not pass — fix: make it pass")


def _check_c(rel: str, status: str, log: dict[str, tuple[str, str]] | None) -> None:
    if log is None:
        warn(
            f"oracle {rel}: cannot establish that it ran (the C adapter wrote no run log) — "
            "fix: add the HARNESS_ORACLE_CLOG line of templates-c/Makefile to the test target"
        )
        return
    entry = log.get(rel)
    if entry is None:
        warn(
            f"oracle {rel}: not executed by the normal check (missing from TESTS) — fix: "
            "keep it in the Makefile's TESTS"
        )
        return
    sha, rc = entry
    target = ROOT / rel
    if not target.is_file() or sha != _sha_file(target):
        warn(
            f"oracle {rel}: the binary that ran was built from a stale source — fix: "
            "rebuild it from the sealed source in the same run (make clean check)"
        )
    elif status == "done" and rc != "0":
        warn(f"oracle {rel} of a done leaf did not pass (exit {rc}) — fix: make it pass")


def _evidence_args(args: list[str]) -> tuple[Path | None, Path | None, set[str]]:
    junit: Path | None = None
    clog: Path | None = None
    expect: set[str] = set()
    it = iter(args)
    for arg in it:
        if arg == "--junit":
            junit = Path(next(it, ""))
        elif arg == "--clog":
            clog = Path(next(it, ""))
        elif arg == "--expect-red":
            expect = {p.strip().removeprefix("./") for p in next(it, "").split(",") if p.strip()}
    return junit, clog, expect


def evidence(args: list[str]) -> int:
    junit, clog, expect = _evidence_args(args)
    base = _active()
    if base is None:
        return 0
    _report_selection(base.get("selection") or {}, inventory(), "the sealed I4 baseline")
    cases = _junit_cases(junit) if junit is not None and junit.is_file() else None
    log = _clog(clog) if clog is not None and clog.is_file() else None
    for _leaf, status, rel, _sha in oracles():
        if rel in expect:
            continue  # the re-gate third run's validated expected-red set
        if not (ROOT / rel).is_file():
            warn(f"oracle {rel}: sealed but missing — fix: restore it (git checkout -- {rel})")
            continue
        if rel.endswith(".py"):
            _check_python(rel, status, cases)
        elif rel.endswith(".c"):
            _check_c(rel, status, log)
        else:
            warn(
                f"oracle {rel}: cannot establish that it ran (no adapter for this file type) "
                "— fix: report it; the harness has no execution adapter for it"
            )
    return 0


def _report_selection(base: Inventory, now: Inventory, against: str) -> None:
    for key in sorted(now):
        if now[key] == "UNRESOLVED":
            warn(
                f"selection input {key}: unresolved (no file found, so its content is not "
                "covered) — fix: reinstall the plugin (uv sync), or name the file literally"
            )
    for key in _selection_changes(base, now):
        state = "added" if key not in base else "removed" if key not in now else "changed"
        warn(f"selection input {key} {state} since {against} — {FIX_CONTRACT}")


# ── red evidence at the plan gate ────────────────────────────────────────────


def _red_path(leaf: str) -> Path:
    return _git_path("harness-oracle") / f"red_{leaf}.json"


def _regated(leaf: str) -> Path:
    return _git_path("harness-oracle") / f"regated_{leaf}"


def red_drop(args: list[str]) -> int:
    """A plan gate started: the old record goes, and a baseline exemption ends."""
    if len(args) == 1:
        _red_path(args[0]).unlink(missing_ok=True)
        _regated(args[0]).parent.mkdir(parents=True, exist_ok=True)
        _regated(args[0]).write_text("", encoding="utf-8")
    return 0


def _pytest_cmd() -> list[str]:
    return shlex.split(os.environ.get("HARNESS_PYTEST_CMD") or "uv run pytest")


def red_record(args: list[str]) -> int:
    if len(args) not in (1, 2):
        return 2
    leaf = args[0]
    check_rc = args[1] if len(args) == 2 else None
    path = _red_path(leaf)
    path.unlink(missing_ok=True)  # a rerun replaces it; never reused
    files = [p.relative_to(ROOT).as_posix() for p in _harness.acceptance_files(ROOT, leaf)]
    record: dict[str, Any] = {
        "leaf": leaf,
        "run_id": uuid.uuid4().hex,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "check_rc": check_rc,
        "oracles": {rel: _sha_file(ROOT / rel) for rel in files},
        "selection": _inventory_digest(inventory()),
        "collected": [],
        "failing": [],
        "adapter": "pytest",
    }
    py = [rel for rel in files if rel.endswith(".py")]
    if len(py) != len(files):
        record["adapter"] = "unsupported"
        warn(
            f"red evidence of {leaf}: cannot establish a targeted red run for non-Python "
            "oracles — fix: none yet (C red evidence comes with the FAIL issue)"
        )
    if py:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
        with tempfile.TemporaryDirectory() as tmp:
            junit = Path(tmp) / "red.xml"
            cmd = [
                *_pytest_cmd(),
                "-p",
                "no:cacheprovider",
                f"--rootdir={ROOT}",
                "-o",
                "junit_family=xunit1",
                f"--junitxml={junit}",
                *py,
            ]
            res = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, check=False)
            record["rc"] = res.returncode
            cases = _junit_cases(junit) or {}
        for rel in py:
            for name, outcome in cases.get(rel, []):
                if outcome == "error":
                    record.setdefault("errors", []).append(f"{rel}::{name}")
                    continue
                node = f"{rel}::{name}"
                record["collected"].append(node)
                if outcome == "failed":
                    record["failing"].append(node)
        if not record["collected"] or record["failing"] != record["collected"]:
            warn(
                f"red evidence of {leaf}: the new oracle did not fail before implementation "
                f"({len(record['failing'])}/{len(record['collected'])} collected nodes failed) "
                "— fix: Codex rewrites the oracle so it is red now"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    say("OK", f"red evidence of {leaf}: {len(record['collected'])} node(s) -> {path}")
    return 0


def red_check(args: list[str]) -> int:
    if len(args) != 1:
        return 2
    leaf = args[0]
    base = _active()
    if base is None:
        return 0
    if leaf in (base.get("exempt_in_progress") or []) and not _regated(leaf).exists():
        print(
            f"note: {leaf} is exempt from red evidence in the I4 baseline (until its "
            "next plan gate)"
        )
        return 0
    try:
        rec = json.loads(_red_path(leaf).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        warn(
            f"no red evidence for {leaf}: the plan gate did not record its oracle collected "
            f"and failing — fix: ./harness/review_with_codex.sh plan {leaf}"
        )
        return 0
    collected = rec.get("collected") or []
    covered = {str(n).split("::", 1)[0] for n in collected}
    oracle_files = set((rec.get("oracles") or {}).keys())
    if (
        rec.get("adapter") != "pytest"
        or not collected
        or rec.get("failing") != collected
        or rec.get("errors")
        or rec.get("rc") != 1
        or covered != oracle_files
    ):
        warn(
            f"red evidence of {leaf} does not show its oracle collected and failing "
            f"(adapter {rec.get('adapter')}, {len(rec.get('failing') or [])}/{len(collected)} "
            f"failing) — fix: ./harness/review_with_codex.sh plan {leaf}"
        )
    files = [p.relative_to(ROOT).as_posix() for p in _harness.acceptance_files(ROOT, leaf)]
    now = {rel: _sha_file(ROOT / rel) for rel in files}
    if now != rec.get("oracles"):
        warn(
            f"oracle of {leaf} changed since its red record (plan gate) — fix: restore it, "
            f"or re-run ./harness/review_with_codex.sh plan {leaf}"
        )
    if _inventory_digest(inventory()) != rec.get("selection"):
        warn(f"selection inputs changed since the red record of {leaf} — {FIX_CONTRACT}")
    return 0


# ── contract flow ────────────────────────────────────────────────────────────


def _proposal_digest(base: Inventory, now: Inventory) -> str:
    return _sha_bytes(_canon({"from": base, "to": now}).encode())


def contract_propose(_: list[str]) -> int:
    base = _active()
    if base is None:
        return 0
    selection = base.get("selection") or {}
    now = inventory()
    changes = _selection_changes(selection, now)
    if not changes:
        say("OK", "contract: the selection inputs match the baseline; nothing to propose")
        return 0
    digest = _proposal_digest(selection, now)
    data = {"diff": digest, "from": selection, "to": now, "changes": changes}
    (ROOT / PROPOSAL).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    say(
        "OK",
        f"contract proposal {digest} ({len(changes)} input(s)) -> {PROPOSAL}: next "
        "./harness/review_with_codex.sh contract",
    )
    return 0


def _review_of(text: str) -> tuple[str, str]:
    verdict = RE_VERDICT.search(text)
    diff = RE_DIFF.search(text)
    return (verdict.group(1) if verdict else "", diff.group(1) if diff else "")


def contract_review(_: list[str]) -> int:
    # The I5 verification contract (issue #66) shares this entry point
    # (review_with_codex.sh contract).
    import _contract

    config = (ROOT / _contract.PROPOSAL).is_file()
    if config:
        _contract.contract_review([])
    prop = _load(PROPOSAL)
    if prop is None and config:
        return 0
    if prop is None:
        warn(f"contract: no {PROPOSAL} — fix: python3 harness/_oracle.py contract-propose")
        return 0
    digest = str(prop.get("diff", ""))
    changed = sorted({k.split("[", 1)[0] for k in prop.get("changes", []) if ":" not in k})
    diff = (_git("diff", "HEAD", "--", *changed) or "") if changed else ""
    prompt = (
        "You review a change to the test selection inputs of a harness project (what the "
        "normal check selects and runs). APPROVE only if no sealed oracle "
        "(tests/acceptance/) can be deselected, skipped, xfailed or weakened by it.\n"
        f"Proposal ({PROPOSAL}, diff hash {digest}):\n{json.dumps(prop, indent=2)}\n"
        f"git diff HEAD of the files:\n{diff}\n"
        "Answer with a line `**Verdict:** APPROVED` or `**Verdict:** CHANGES_REQUESTED` "
        "and your reasons."
    )
    if os.environ.get("HARNESS_CODEX_MODE") == "manual":
        print(prompt)
        print(f"--- write {CONTRACT_REVIEW} with the verdict and the line `diff: {digest}` ---")
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
    verdict = _review_of(text)[0]
    if res.returncode != 0 or verdict not in ("APPROVED", "CHANGES_REQUESTED"):
        warn(
            "contract: the reviewer gave no valid verdict; nothing recorded — fix: run "
            "./harness/review_with_codex.sh contract again"
        )
        return 0
    (ROOT / CONTRACT_REVIEW).write_text(
        f"# Selection contract review\n\n**Verdict:** {verdict}\n\ndiff: {digest}\n\n{text}\n",
        encoding="utf-8",
    )
    say(
        "OK",
        f"contract review {verdict} for {digest}: the human approves with "
        f"python3 harness/_oracle.py contract-approve {digest}",
    )
    return 0


def contract_approve(args: list[str]) -> int:
    if len(args) != 1:
        return 2
    want = args[0]
    base, _ = baseline()
    prop = _load(PROPOSAL)
    review = ROOT / CONTRACT_REVIEW
    problems: list[str] = []
    now = inventory()
    if base is None or prop is None:
        problems.append(f"no sealed baseline or no {PROPOSAL}")
    else:
        frm, to = prop.get("from"), prop.get("to")
        if prop.get("diff") != want or _proposal_digest(frm or {}, to or {}) != want:
            problems.append("the proposal does not hash to this diff (edited after review?)")
        if frm != (base.get("selection") or {}) or to != now:
            problems.append("the selection inputs or the baseline changed after the proposal")
        verdict, digest = _review_of(review.read_text("utf-8") if review.is_file() else "")
        if verdict != "APPROVED" or digest != want:
            problems.append(f"{CONTRACT_REVIEW} does not approve this diff hash")
    if problems or base is None or prop is None:
        for problem in problems:
            warn(
                f"contract-approve {want[:12]}: refused, nothing resealed: {problem} — fix: "
                "python3 harness/_oracle.py contract-propose, then "
                "./harness/review_with_codex.sh contract"
            )
        return 0
    base["selection"] = now
    base.setdefault("contracts", []).append(
        {"diff": want, "approved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    )
    _write_baseline(base)
    _seal_baseline()
    (ROOT / PROPOSAL).unlink(missing_ok=True)
    say("OK", f"contract {want[:12]} approved: baseline resealed")
    return 0


COMMANDS = {
    "evidence": evidence,
    "inventory": lambda _: print(json.dumps(inventory(), indent=2, sort_keys=True)) or 0,
    "baseline-create": baseline_create,
    "baseline-seal": baseline_seal,
    "red-record": red_record,
    "red-drop": red_drop,
    "red-check": red_check,
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
