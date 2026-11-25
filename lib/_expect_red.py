"""Tolerated test files of an issue re-gate (issue #31).

When the issue gate's counterfactual run (#27) stays red, the gate asks this module
for the set T of test files whose failure the approval under review expects:

  - the acceptance files of every `in_progress` subtask of the issue whose plan
    verdict is PLAN_APPROVED and whose code verdict is not APPROVED;
  - every `migrates:` entry of those subtasks, only when every entry is eligible now
    (the file exists, a manifest of another subtask seals it and it still matches
    that seal). The comparison with the previously approved snapshot is skipped: a
    newly declared entry always differs until this gate approves it.

Issue #125 extends the third run to every re-gate (stack, roadmap, phase, task):
there T is the acceptance files of the open (in-progress, plan-approved, not
code-approved) subtasks, no `migrates:`. It also lets a changed test configuration
file through when it is in the `files:` of an open subtask (never a conftest.py),
and computes the cascade of the counterfactual run (_plan_seal.cascade).

Usage: _expect_red.py <ISS-n>   (cwd = project root)
       _expect_red.py leaf      T of the open subtasks (same exit codes)
       _expect_red.py cascade <target>  the pending re-gates below <target>, comma
          separated on one line (possibly empty); exit 2 for a malformed target
       _expect_red.py sealed <target>  exit 0 when <target> was sealed before (a
          re-gate), 1 otherwise (a first approval), 2 for a malformed target
       _expect_red.py config-ok  exit 0 with the changed test configuration files
          (one per line, possibly none) when each is in an open subtask's `files:`
          and none is a conftest.py; exit 1 with one line per refusal otherwise
       _expect_red.py c-makefile-ok  exit 0 when the C test list can be overridden exactly
  exit 0  T on stdout, one path per line (possibly empty)
  exit 3  some `migrates:` entry is ineligible or some path is unsafe: one line
          per problem on stdout, no T
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import _harness
import _migrate
import _plan_seal

# Paths the gate can pass to pytest/make exactly: no whitespace, quotes, globs or `$`.
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/+-]+$")


def _open(root: Path, leaf: dict[str, Any]) -> bool:
    sid = str(leaf["id"])
    progress = root / "progress"
    return (
        leaf["status"] == "in_progress"
        and _harness.read_verdict(progress / f"plan_review_{sid}.md") == "PLAN_APPROVED"
        and _harness.read_verdict(progress / f"review_{sid}.md") != "APPROVED"
    )


def _ignored(root: Path, paths: list[Path]) -> set[Path]:
    """The paths git ignores (build products such as a C test binary)."""
    if not paths:
        return set()
    res = subprocess.run(
        ["git", "check-ignore", "--stdin"], cwd=root, text=True, capture_output=True,
        input="".join(f"{p.relative_to(root).as_posix()}\n" for p in paths), check=False,
    )  # fmt: skip
    return {root / line for line in res.stdout.splitlines() if line}


def ineligible(root: Path, leaf: str, entries: list[str]) -> list[str]:
    """Each entry that cannot be migrated now, with the reason."""
    manifests = _migrate._manifests(root)
    own = root / "progress" / f"acceptance_{leaf}.sha256"
    out = []
    for rel in entries:
        target = root / rel
        owners = [m for m in _migrate._owners(manifests, rel) if m != own]
        if not target.is_file():
            out.append(f"{leaf}: migrates: entry {rel} does not exist")
        elif not owners:
            out.append(f"{leaf}: migrates: entry {rel} is not sealed by another subtask")
        elif any(manifests[m][rel] != _migrate._sha256(target) for m in owners):
            out.append(f"{leaf}: migrates: entry {rel} no longer matches its seal")
    return out


def tolerated(root: Path, iid: str | None) -> tuple[list[str], list[str]]:
    """(T, ineligible entries). T is empty whenever an entry is ineligible. With no
    issue (issue #125), the open subtasks of any kind and only their own oracles."""
    if iid is None:
        leaves = [x for x in _harness.leaves() if _open(root, x)]
    else:
        leaves = [x for x in _harness.leaves() if x.get("issue") == iid and _open(root, x)]
    files: set[str] = set()
    problems: list[str] = []
    for leaf in leaves:
        sid = str(leaf["id"])
        own = _harness.acceptance_files(root, sid)
        skip = _ignored(root, own)
        files.update(p.relative_to(root).as_posix() for p in own if p not in skip)
        entries = [str(e) for e in leaf.get("migrates") or []] if iid is not None else []
        problems += ineligible(root, sid, entries)
        files.update(entries)
    problems += [
        f"{rel}: path the gate cannot exclude exactly"
        for rel in sorted(files)
        if not SAFE_PATH.match(rel)
    ]
    if problems:
        return [], problems
    return sorted(files), []


# ── changed test configuration (issue #125, B1-config) ───────────────────────

# What can change which tests run or how: the third run refuses any change of these
# against HEAD, except a file the open subtask declares in `files:` (its own change,
# which the reviewer then reads). A conftest.py can run code in every test: never.
CONFIG_PATHS = (
    "Makefile", "GNUmakefile", "makefile", "pyproject.toml", "setup.cfg", "tox.ini",
    "pytest.ini", ":(glob)**/conftest.py",
)  # fmt: skip
NOT_CONFIG = (":(exclude,glob)**/.venv/**", ":(exclude,glob)**/venv/**",
              ":(exclude,glob)**/node_modules/**")  # fmt: skip


def _git_out(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}  # fmt: skip
    return subprocess.run(["git", *args], cwd=root, env=env, text=True, capture_output=True,
                          check=False)  # fmt: skip


def changed_config(root: Path) -> list[str] | None:
    """Test configuration files that differ from HEAD: changed, new, deleted or ignored
    ones (git status), and tracked ones whose content is not their HEAD blob (index
    flags such as assume-unchanged hide them from git status). None if git fails."""
    status = _git_out(root, "status", "--porcelain", "-z", "--ignored", "--untracked-files=all",
                      "--", *CONFIG_PATHS, *NOT_CONFIG)  # fmt: skip
    tracked = _git_out(root, "ls-files", "-z", "--", *CONFIG_PATHS)
    if status.returncode != 0 or tracked.returncode != 0:
        return None
    found: set[str] = set()
    records = iter(status.stdout.split("\0"))
    for record in records:
        if len(record) < 4:
            continue
        found.add(record[3:])
        if "R" in record[:2] or "C" in record[:2]:  # the next record is the source path
            found.add(next(records, ""))
    for rel in filter(None, tracked.stdout.split("\0")):
        want = _git_out(root, "rev-parse", "-q", "--verify", f"HEAD:./{rel}")
        have = _git_out(root, "hash-object", "--", rel)
        if want.returncode != 0 or have.returncode != 0 or want.stdout != have.stdout:
            found.add(rel)
    return sorted(f for f in found if f)


def config_ok(root: Path) -> tuple[list[str], list[str]]:
    """(changed configuration files allowed, refusals)."""
    changed = changed_config(root)
    if changed is None:
        return [], ["git status failed: the test configuration cannot be compared with HEAD"]
    declared: set[str] = set()
    for leaf in _harness.leaves():
        if _open(root, leaf):
            declared.update(_harness.leaf_files(str(leaf["id"])) or [])
    problems = []
    for rel in changed:
        if Path(rel).name == "conftest.py":
            problems.append(f"{rel}: a changed conftest.py is never tolerated")
        elif rel not in declared:
            problems.append(f"{rel}: changed, and not in the files: of the in-progress subtask")
    return ([], problems) if problems else (changed, [])


# A C Makefile whose test list the gate can override exactly: TESTS assigned once,
# globally, with `:=`; nothing else assigns it, no MAKECMDGOALS logic, and no include
# but harness/harness.mk (an included file could assign TESTS out of sight).
RE_TESTS_ASSIGN = re.compile(r"\bTESTS\s*(?:::?=|:::=|\+=|\?=|!=|=)|\bdefine\s+TESTS\b")
RE_GLOBAL_TESTS = re.compile(r"^TESTS\s*:=")
RE_INCLUDE = re.compile(r"^\s*-?s?include\b")


def c_makefile_ok(text: str) -> bool:
    lines = text.splitlines()
    assigns = [ln for ln in lines if RE_TESTS_ASSIGN.search(ln)]
    includes = [ln.strip() for ln in lines if RE_INCLUDE.match(ln)]
    return (
        len(assigns) == 1
        and RE_GLOBAL_TESTS.match(assigns[0]) is not None
        and "MAKECMDGOALS" not in text
        and all(ln == "include harness/harness.mk" for ln in includes)
    )


# Where the expanded test list may appear in the dry run of verify (anything else,
# a condition on it above all, means no third run):
RE_FOR = re.compile(r"\bfor\s+\w+\s+in\s+$")
RE_EMPTY = re.compile(r"\[\s+-z\s+\"$")
# a linter's plain arguments: words only, no expansion, quote, pipe or redirection.
RE_ARGS = re.compile(r"^\s*([A-Za-z0-9_./+-]+)(?:\s+[A-Za-z0-9_./+=,:-]+)*\s+$")
# Only these linters may take the list as arguments (their failures on T files show
# in the first two runs, which the reviewer accounts for).
LINTERS = {"clang-format", "cppcheck", "clang-tidy", "norminette"}


def _whitelisted(full: str, every: str, kept: str) -> bool:
    for match in re.finditer(re.escape(every), full):
        line_start = full.rfind("\n", 0, match.start()) + 1
        before = full[line_start : match.start()]
        after = full[match.end() :].split("\n", 1)[0]
        if RE_FOR.search(before) and after.startswith(";"):
            continue
        if RE_EMPTY.search(before) and after.startswith('" ]'):
            continue
        segment = re.split(r"[;&|(`{]", before)[-1]
        args = RE_ARGS.match(segment)
        if args and args[1] in LINTERS and re.match(r"\s*(?:;|\\$|$)", after):
            continue
        return False
    return True


def same_recipes(full: str, reduced: str, files: list[str], every: str, kept: str) -> bool:
    """`make -n check` with the full and the reduced TESTS. T's paths may appear in
    the full dry run only inside the expanded test list: replacing every occurrence
    of that list by the reduced one must give the reduced dry run exactly, and leave
    no path of T behind (a `$(filter …)` of TESTS, for one, fails closed)."""
    if not every or not _whitelisted(full, every, kept):
        return False
    swapped = full.replace(every, kept)
    if swapped != reduced:
        return False
    return not any(
        re.search(rf"(?<![\w./-])(?:\./)?{re.escape(f)}(?![\w./-])", swapped) for f in files
    )


def _cmd_cascade(target: str) -> int:
    try:
        print(",".join(_plan_seal.cascade(Path.cwd(), target)))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def _cmd_sealed(target: str) -> int:
    try:
        return 0 if _plan_seal.sealed(Path.cwd(), target) else 1
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2


def _cmd_config_ok() -> int:
    allowed, refused = config_ok(Path.cwd())
    for line in refused or allowed:
        print(line)
    return 1 if refused else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["same-recipes"] and len(argv) == 6:
        full, reduced, tfile, every, kept = (Path(a).read_text(encoding="utf-8") for a in argv[1:])
        ok = same_recipes(full, reduced, tfile.split(), every.strip("\n"), kept.strip("\n"))
        return 0 if ok else 1
    if argv[:1] == ["c-makefile-ok"]:
        path = Path("Makefile")
        return 0 if path.is_file() and c_makefile_ok(path.read_text(encoding="utf-8")) else 1
    if argv[:1] in (["cascade"], ["sealed"]) and len(argv) == 2:
        return (_cmd_cascade if argv[0] == "cascade" else _cmd_sealed)(argv[1])
    if argv == ["config-ok"]:
        return _cmd_config_ok()
    if argv == ["leaf"]:
        iid: str | None = None
    elif len(argv) == 1 and _harness.RE_ISSUE_ID.match(argv[0]):
        iid = argv[0]
    else:
        print(__doc__, file=sys.stderr)
        return 2
    files, problems = tolerated(Path.cwd(), iid)
    for line in problems or files:
        print(line)
    return 3 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
