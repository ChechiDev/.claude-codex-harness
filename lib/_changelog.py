#!/usr/bin/env python3
"""CHANGELOG and changelog fragments (releases, delivery 1: docs/design/releases.md).

A project is **activated** when it has .harness/changelog.json (a new install writes
it; a started project gets it from `install.sh <project> --changelog`), or when its
base branch has it (a task branch in flight at the activation). Without it nothing
here applies: `make check` and `make status` only warn with the activation command.

Activated:
  - each subtask spec written at a kickoff declares `- changelog:` and
    `- incompatible:`; the task and issue gates refuse to run without them
    (missing_declarations, called by _harness.py task-ready / issue-ready);
  - the last close of a task or issue writes changelog.d/<N.M | ISS-n>.md (Codex,
    documenter): `make check` fails a close that finishes one without it, and only
    warns for one finished earlier (problems / warnings, called by _harness.py validate);
  - `consolidate` moves the fragments into the `## [Unreleased]` section of
    CHANGELOG.md, idempotently (a hidden marker per fragment).

Claude never writes the record, nor (activated) CHANGELOG.md and changelog.d/, and
never runs `consolidate --apply` nor the activation (the guard blocks them).

Usage (cwd = project root; the subcommand first, its flags after it):
  python3 _changelog.py due <N.M.K|ISS-n.k>       exit 0 + what to write when that close
                                                  finishes its task or issue, else exit 1
  python3 _changelog.py fragment <N.M.K|ISS-n.k>  exit 1 + why when that close still owes
                                                  its fragment (missing/invalid), else exit 0
  python3 _changelog.py consolidate [--apply]     dry-run unless --apply
  python3 _changelog.py install <project> <templates|templates-c>   (install.sh, new install)
  python3 _changelog.py activate <project> [--apply [--yes]]        (install.sh --changelog)
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _status import say

RECORD = Path(".harness/changelog.json")
CHANGELOG = Path("CHANGELOG.md")
FRAGMENTS = Path("changelog.d")
README = "README.md"
RELEASES = Path("docs/releases.md")
TOOLKIT = Path(__file__).resolve().parent.parent
KINDS = ("added", "changed", "fixed", "removed", "security")
SECTIONS = tuple(k.capitalize() for k in KINDS)
INCOMPATIBLE = "Incompatible"
ORDER = (INCOMPATIBLE, "Added", "Changed", "Removed", "Fixed", "Security")
BASE_REFS = ("refs/remotes/origin/HEAD", "refs/remotes/origin/main", "refs/heads/main")
BRANCH = "chore/harness-changelog"
SUBJECT = "chore: activate the harness changelog"
RE_ID = re.compile(r"^(?:\d+\.\d+|ISS-\d+)$")
RE_FIELD = re.compile(r"^- (changelog|incompatible|id|none):(.*)$")
RE_UNRELEASED = re.compile(r"^## +\[?unreleased\]?\s*$", re.I)
RE_MARKER = re.compile(r"<!-- changelog\.d/(\S+?)\.md(?: ([0-9a-f]{12}))? -->")
RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_NO_CHECKS = re.compile(r"no checks reported", re.I)  # gh pr checks, a repo without CI

Item = dict[str, Any]


class ChangelogError(Exception):
    """A changelog command cannot go on; the message says why."""


@dataclass(frozen=True)
class Declaration:
    kinds: tuple[str, ...] = ()
    none: str = ""
    incompatible: str = ""  # what breaks; empty when compatible


@dataclass
class Fragment:
    ident: str
    incompatible: str = ""
    none: str = ""
    entries: dict[str, list[str]] = field(default_factory=dict)
    digest: str = ""  # of the file it was read from (the consolidation marker)


# ── activation state ──────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _load(text: str, where: str) -> Item:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ChangelogError(f"{where} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("finished", []), list):
        raise ChangelogError(f"{where} is not a changelog activation record")
    return data


def record(root: Path) -> Item | None:
    """The activation record of the work tree, else of the base branch (None: not
    activated). Raises ChangelogError when it is unreadable."""
    path = root / RECORD
    if path.is_file():
        return _load(path.read_text(encoding="utf-8"), str(RECORD))
    for ref in BASE_REFS:
        res = _git(root, "show", f"{ref}:./{RECORD.as_posix()}")
        if res.returncode == 0:
            return _load(res.stdout, f"{ref}:{RECORD}")
    return None


def activated(root: Path) -> bool:
    try:
        return record(root) is not None
    except ChangelogError:
        return True  # an unreadable record is reported by problems(), never ignored


def activation_command(root: Path) -> str:
    return f"bash {TOOLKIT}/install.sh {root} --changelog"


def not_activated_warning(root: Path) -> str:
    return (
        f"changelog not activated: the human runs {activation_command(root)} "
        "(dry-run; then --apply). Claude never runs it"
    )


# ── declarations (kickoff) ────────────────────────────────────────────────────


def _header_fields(text: str) -> dict[str, list[str]]:
    """`- key: value` lines of the part before the first `## ` heading."""
    out: dict[str, list[str]] = {}
    for line in RE_COMMENT.sub("", text).splitlines():
        if line.startswith("## "):
            break
        if match := RE_FIELD.match(line):
            out.setdefault(match[1], []).append(match[2].strip())
    return out


def _reason(value: str, key: str) -> str:
    match = re.fullmatch(rf"{key}:\s*(\S.*)", value)
    if match is None:
        raise ValueError(f"`{value}`: `{key}` needs its reason (`{key}: <why>`)")
    return match[1].strip()


def parse_incompatible(value: str) -> str:
    if value == "no":
        return ""
    if value.startswith("yes"):
        return _reason(value, "yes")
    raise ValueError(f"`- incompatible: {value}`: use `no` or `yes: <what breaks for users>`")


def _kinds(value: str) -> tuple[str, ...]:
    kinds = tuple(k.strip().lower() for k in value.split(","))
    unknown = [k for k in kinds if k not in KINDS]
    if unknown or not kinds or len(set(kinds)) != len(kinds):
        raise ValueError(
            f"`- changelog: {value}`: use {' | '.join(KINDS)} (comma-separated) or `none: <why>`"
        )
    return kinds


def parse_declaration(text: str) -> Declaration | None:
    """The `- changelog:` / `- incompatible:` declaration of a spec (None: absent).
    Raises ValueError when it is malformed or half there."""
    found = _header_fields(text)
    changelog, incompatible = found.get("changelog", []), found.get("incompatible", [])
    if not changelog and not incompatible:
        return None
    if len(changelog) != 1 or len(incompatible) != 1:
        raise ValueError("needs exactly one `- changelog:` and one `- incompatible:` line")
    value = changelog[0]
    breaks = parse_incompatible(incompatible[0])
    if value.startswith("none"):
        return Declaration(none=_reason(value, "none"), incompatible=breaks)
    return Declaration(kinds=_kinds(value), incompatible=breaks)


def _spec_text(root: Path, leaf: Item) -> str | None:
    spec = str(leaf.get("spec") or "")
    try:
        return (root / spec).read_text(encoding="utf-8") if spec else None
    except (OSError, UnicodeDecodeError):
        return None


def declaration_problems(root: Path, leaves: Iterable[Item]) -> list[str]:
    """Malformed declarations (an activated project's `make check`)."""
    out = []
    for leaf in leaves:
        text = _spec_text(root, leaf)
        try:
            if text is not None:
                parse_declaration(text)
        except ValueError as exc:
            out.append(f"{leaf['id']}: changelog declaration in {leaf['spec']}: {exc}")
    return out


def _first_commit(root: Path, ref: str, path: str) -> tuple[str, int] | None:
    """(sha, committer time) of the commit that first added `path` in `ref`'s history."""
    res = _git(root, "log", "--diff-filter=A", "--format=%H %ct", ref, "--", path)
    rows = res.stdout.split("\n") if res.returncode == 0 else []
    last = [r for r in rows if r.strip()][-1:]
    if not last:
        return None
    sha, _, when = last[0].partition(" ")
    return (sha, int(when)) if when.isdigit() else None


def activation_commit(root: Path) -> tuple[str, int] | None:
    """The commit that added the record: in HEAD's history, else on the base branch."""
    for ref in ("HEAD", *BASE_REFS):
        if found := _first_commit(root, ref, RECORD.as_posix()):
            return found
    return None


def approved_before_activation(root: Path, spec: str, act: tuple[str, int] | None) -> bool:
    """The spec was committed before the activation commit and not on top of it (a task
    in flight at the activation): it was approved without the changelog declaration."""
    added = _first_commit(root, "HEAD", spec) if act and spec else None
    if act is None or added is None or added[1] > act[1]:
        return False
    return _git(root, "merge-base", "--is-ancestor", act[0], added[0]).returncode != 0


def missing_declarations(root: Path, leaves: Iterable[Item]) -> list[str]:
    """Why the task or issue gate cannot run yet (activated projects only). A closed spec
    and one approved before the activation are never re-gated for it (§3)."""
    if not activated(root):
        return []
    out = []
    act: tuple[str, int] | None = None
    looked = False
    for leaf in leaves:
        if leaf.get("status") in ("baseline", "done"):
            continue
        if not looked:
            act, looked = activation_commit(root), True
        if approved_before_activation(root, str(leaf.get("spec") or ""), act):
            continue
        text = _spec_text(root, leaf) or ""
        try:
            decl = parse_declaration(text)
        except ValueError as exc:
            out.append(f"{leaf['id']}: changelog declaration: {exc}")
            continue
        if decl is None:
            out.append(
                f"{leaf['id']}: no changelog declaration: its spec needs `- changelog: "
                f"{'|'.join(KINDS)} | none: <why>` and `- incompatible: no | yes: <what>` "
                "(the project's changelog is activated)"
            )
    return out


# ── fragments ─────────────────────────────────────────────────────────────────


def _fragment_header(frag: Fragment, line: str, seen: set[str]) -> None:
    match = RE_FIELD.match(line)
    if match is None or match[1] == "changelog" or match[1] in seen:
        raise ValueError(f"unexpected line before the sections: `{line}`")
    key, value = match[1], match[2].strip()
    seen.add(key)
    if key == "id" and value != frag.ident:
        raise ValueError(f"`- id: {value}` does not match the file name ({frag.ident})")
    if key == "incompatible":
        frag.incompatible = parse_incompatible(value)
    if key == "none":
        frag.none = _reason(f"none: {value}", "none")


def _fragment_body(frag: Fragment, lines: list[str]) -> None:
    current: list[str] | None = None
    for line in lines:
        if line.startswith("### "):
            name = line[4:].strip()
            if name not in SECTIONS or name in frag.entries:
                raise ValueError(f"`{line}`: use each of {', '.join(SECTIONS)} once at most")
            current = frag.entries[name] = []
        elif line.startswith("- ") and current is not None and line[2:].strip():
            current.append(line[2:].strip())
        elif line.startswith("  ") and current:
            current[-1] += " " + line.strip()
        elif line.strip():
            raise ValueError(f"unexpected line: `{line}`")


def parse_fragment(text: str, ident: str) -> Fragment:
    """changelog.d/<ident>.md (docs/design/releases.md §3). Raises ValueError."""
    frag = Fragment(ident)
    lines = RE_COMMENT.sub("", text).splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("### ")), len(lines))
    seen: set[str] = set()
    for line in lines[:start]:
        if line.strip():
            _fragment_header(frag, line, seen)
    if "id" not in seen or "incompatible" not in seen:
        raise ValueError("needs `- id: <N.M | ISS-n>` and `- incompatible: no | yes: <what>`")
    _fragment_body(frag, lines[start:])
    if any(not items for items in frag.entries.values()):
        raise ValueError("a section has no `- ` entry")
    if bool(frag.none) == bool(frag.entries):
        raise ValueError("needs either entries under `### <Section>` or `- none: <why>`")
    return frag


def fragment_paths(root: Path) -> list[Path]:
    folder = root / FRAGMENTS
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.glob("*.md") if p.name != README), key=_path_key)


def _key(ident: str) -> tuple[int, ...]:
    issue = ident.startswith("ISS-")
    return (int(issue), *(int(n) for n in re.findall(r"\d+", ident)))


def _path_key(path: Path) -> tuple[int, ...]:
    return _key(path.stem) if RE_ID.match(path.stem) else (9, 0)


def snapshot(root: Path) -> dict[Path, bytes]:
    """The bytes of every fragment file, read once (what a consolidation consumes)."""
    out = {}
    for path in fragment_paths(root):
        with contextlib.suppress(OSError):
            out[path] = path.read_bytes()
    return out


def read_fragments(
    root: Path, files: dict[Path, bytes] | None = None
) -> tuple[list[Fragment], list[str]]:
    """(valid fragments, problems) of changelog.d/ (or of a snapshot of it)."""
    frags, problems = [], []
    for path, data in (snapshot(root) if files is None else files).items():
        rel = f"{FRAGMENTS}/{path.name}"
        if not RE_ID.match(path.stem):
            problems.append(f"{rel}: the name must be <N.M>.md or <ISS-n>.md")
            continue
        try:
            frag = parse_fragment(data.decode("utf-8"), path.stem)
            frag.digest = hashlib.sha256(data).hexdigest()[:12]
            frags.append(frag)
        except (UnicodeDecodeError, ValueError) as exc:
            problems.append(f"{rel}: invalid fragment: {exc}")
    return frags, problems


def markers(root: Path) -> dict[str, set[str]]:
    """{fragment id: the digests it was consolidated with} from CHANGELOG.md."""
    try:
        text = (root / CHANGELOG).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, set[str]] = {}
    for ident, digest in RE_MARKER.findall(text):
        out.setdefault(ident, set()).add(digest)
    return out


def consolidated(root: Path) -> set[str]:
    return set(markers(root))


# ── make check: the fragment of the last close ────────────────────────────────


def _owners(data: Item) -> dict[str, list[Item]]:
    """{task N.M | issue ISS-n: its subtasks} of a feature_list.json."""
    out = {str(t["id"]): list(t.get("subtasks") or []) for t in data.get("features", [])}
    out.update({str(i["id"]): list(i.get("subtasks") or []) for i in data.get("issues", [])})
    return out


def delivered(subs: list[Item]) -> bool:
    """Complete with work of its own: every subtask done or baseline, one done at least."""
    states = [str(s.get("status")) for s in subs]
    return bool(states) and "done" in states and set(states) <= {"done", "baseline"}


def _head_delivered(root: Path) -> set[str] | None:
    res = _git(root, "show", "HEAD:./feature_list.json")
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return set()
    return {oid for oid, subs in _owners(data).items() if delivered(subs)}


def _kind(owner: str) -> str:
    return "issue" if owner.startswith("ISS-") else "task"


def _fragment_rules(root: Path, data: Item, rec: Item) -> tuple[list[str], list[str]]:
    frags, problems = read_fragments(root)
    have = {f.ident for f in frags} | consolidated(root)
    bad = {p.stem for p in fragment_paths(root)} - {f.ident for f in frags}
    finished = {str(x) for x in rec.get("finished", [])}
    owners = _owners(data)
    before = _head_delivered(root)
    warnings = [
        f"{FRAGMENTS}/{f.ident}.md: no task or issue {f.ident} in the plan"
        for f in frags
        if f.ident not in owners
    ]
    for owner, subs in owners.items():
        if owner in have | bad | finished or not delivered(subs):
            continue
        rel = f"{FRAGMENTS}/{owner}.md"
        if before is not None and owner not in before:
            problems.append(
                f"the close that finishes {_kind(owner)} {owner} must write {rel} "
                "(Codex, documenter: python3 harness/_changelog.py due <id>)"
            )
        else:
            warnings.append(f"{_kind(owner)} {owner} was finished without its fragment {rel}")
    return problems, warnings


def check(root: Path, data: Item, leaves: Iterable[Item]) -> tuple[list[str], list[str]]:
    """(FAIL problems, WARN messages) of an activated project; nothing otherwise."""
    try:
        rec = record(root)
    except ChangelogError as exc:
        return [str(exc)], []
    if rec is None:
        return [], []
    problems = declaration_problems(root, leaves)
    more, warnings = _fragment_rules(root, data, rec)
    import _releases  # lazy: _releases imports this module

    return problems + more, warnings + _releases.warnings(root)


# ── the documenter: is a fragment due with this close? ────────────────────────


def _union(root: Path, subs: list[Item], spec_of: dict[str, str]) -> list[str]:
    kinds: list[str] = []
    nones, breaks, missing = [], [], []
    for sub in subs:
        text = _spec_text(root, {"spec": spec_of.get(str(sub["id"]), "")}) or ""
        try:
            decl = parse_declaration(text)
        except ValueError:
            decl = None
        if decl is None:
            missing.append(str(sub["id"]))
            continue
        kinds += [k for k in decl.kinds if k not in kinds]
        nones += [f"{sub['id']}: {decl.none}"] if decl.none else []
        breaks += [f"{sub['id']}: {decl.incompatible}"] if decl.incompatible else []
    ordered = [k for k in KINDS if k in kinds]
    lines = [f"declared: {', '.join(ordered) if ordered else 'nothing user-visible'}"]
    lines += [f"declared none: {n}" for n in nones]
    lines.append(f"declared incompatible: {'; '.join(breaks) if breaks else 'no'}")
    if missing:
        lines.append(
            f"no declaration in {', '.join(missing)} (approved before the activation): "
            "write the fragment from what was delivered"
        )
    return lines


def owed(root: Path, sid: str) -> str | None:
    """Why the close of `sid` still owes its task's or issue's fragment: it finishes it
    in the work tree (not in HEAD), and changelog.d/<owner>.md is missing or invalid
    and not consolidated. None when nothing is owed (the doc repair, rel1rev #4)."""
    if not activated(root):
        return None
    data = json.loads((root / "feature_list.json").read_text(encoding="utf-8"))
    owner = sid.rsplit(".", 1)[0]
    subs = _owners(data).get(owner, [])
    finished = {str(x) for x in (record(root) or {}).get("finished", [])}
    before = _head_delivered(root)
    if owner in finished or not delivered(subs) or before is None or owner in before:
        return None
    if owner in consolidated(root):
        return None
    rel = f"{FRAGMENTS}/{owner}.md"
    try:
        parse_fragment((root / rel).read_bytes().decode("utf-8"), owner)
    except FileNotFoundError:
        return f"{rel} is missing: the close that finishes {_kind(owner)} {owner} writes it"
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return f"{rel}: invalid fragment: {exc}"
    return None


def _owed_command(root: Path, sid: str) -> int:
    """`fragment <id>`: 0 nothing owed, 1 owed (printed), 2 cannot tell (fail closed)."""
    try:
        why = owed(root, sid)
    except (OSError, ValueError) as exc:
        say("FAIL", f"cannot tell whether {sid} owes its fragment: {exc}", sys.stderr)
        return 2
    if why:
        print(why)
    return 1 if why else 0


def due(root: Path, sid: str) -> int:
    if not activated(root):
        return 1
    data = json.loads((root / "feature_list.json").read_text(encoding="utf-8"))
    owner = sid.rsplit(".", 1)[0]
    subs = _owners(data).get(owner, [])
    rec = record(root) or {}
    if not subs or owner in {str(x) for x in rec.get("finished", [])}:
        return 1
    rest = [s for s in subs if str(s["id"]) != sid]
    if any(str(s.get("status")) not in ("done", "baseline") for s in rest):
        return 1
    spec_of = {str(s["id"]): str(s.get("spec") or "") for s in subs}
    print(f"this close finishes {_kind(owner)} {owner}: write {FRAGMENTS}/{owner}.md")
    print("\n".join(_union(root, subs, spec_of)))
    return 0


# ── consolidation ─────────────────────────────────────────────────────────────


def _new_lines(frags: list[Fragment]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for frag in frags:
        if frag.incompatible:
            out.setdefault(INCOMPATIBLE, []).append(f"- {frag.incompatible} ({frag.ident})")
        for name, items in frag.entries.items():
            out.setdefault(name, []).extend(f"- {item} ({frag.ident})" for item in items)
    return out


def _unreleased_bounds(lines: list[str]) -> tuple[list[str], int, int]:
    """(lines with an `## [Unreleased]` heading, its body start, its body end)."""
    head = next((i for i, line in enumerate(lines) if RE_UNRELEASED.match(line)), None)
    if head is None:
        at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        while at > 0 and not lines[at - 1].strip():
            at -= 1
        lines = [*lines[:at], "", "## [Unreleased]", "", *lines[at:]]
        head = at + 1
    end = next((i for i in range(head + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines, head + 1, end


def _split_body(body: list[str]) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """(preamble, {### section: its lines}, markers) of the Unreleased body."""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    markers: list[str] = []
    current = preamble
    for line in body:
        if RE_MARKER.fullmatch(line.strip()):
            markers.append(line.strip())
        elif line.startswith("### "):
            current = sections.setdefault(line[4:].strip(), [])
        else:
            current.append(line)
    return preamble, sections, markers


def _rank(name: str) -> int:
    return ORDER.index(name) if name in ORDER else -1


def _place(names: list[str], name: str) -> None:
    later = [i for i, n in enumerate(names) if _rank(n) > _rank(name) >= 0]
    names.insert(later[0] if later else len(names), name)


def _trim(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return lines


def merge(text: str, frags: list[Fragment]) -> str:
    """CHANGELOG.md with `frags` added to its Unreleased section (text as is when none)."""
    if not frags:
        return text
    lines, start, end = _unreleased_bounds(text.splitlines())
    preamble, sections, found = _split_body(lines[start:end])
    names = list(sections)
    for name, new in _new_lines(frags).items():
        if name not in sections:
            _place(names, name)
            sections[name] = []
        sections[name] = [*_trim(sections[name]), *new]
    body = [""] + (_trim(preamble) + [""] if _trim(preamble) else [])
    for name in names:
        body += [f"### {name}", *_trim(sections[name]), ""]
    body += [*found, *(f"<!-- changelog.d/{f.ident}.md {f.digest} -->" for f in frags)]
    rest = lines[end:]
    out = [*lines[:start], *body, *([""] if rest else []), *rest]
    return "\n".join(out).rstrip("\n") + "\n"


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def consolidate(root: Path, apply: bool) -> int:
    if not activated(root):
        say("FAIL", f"nothing to consolidate: {not_activated_warning(root)}", sys.stderr)
        return 1
    files = snapshot(root)
    frags, problems = read_fragments(root, files)
    for problem in problems:
        say("FAIL", f"{problem}: nothing consolidated", sys.stderr)
    if problems:
        return 1
    path = root / CHANGELOG
    text = path.read_text(encoding="utf-8") if path.is_file() else _template("templates")
    done = markers(root)
    changed = [f.ident for f in frags if f.ident in done and f.digest not in done[f.ident] | {""}]
    for ident in changed:
        say(
            "FAIL",
            f"{FRAGMENTS}/{ident}.md changed after it was consolidated into "
            f"{CHANGELOG}: merge it by hand; nothing consolidated",
            sys.stderr,
        )
    if changed:
        return 1
    new = [f for f in frags if f.ident not in done]
    result = merge(text, new)
    if not apply:
        print(result, end="")
        names = ", ".join(f.ident for f in new) or "none"
        say(
            "SKIP",
            f"dry-run: fragments to add: {names}. To write it: "
            "python3 harness/_changelog.py consolidate --apply (not Claude)",
        )
        return 0
    if result != text or not path.is_file():
        _write_atomic(path, result)
    _drop_consumed(files)
    say("OK", f"{CHANGELOG}: consolidated {len(new)} fragment(s)")
    return 0


def _drop_consumed(files: dict[Path, bytes]) -> None:
    """Delete exactly the fragments this run read. Each is first claimed atomically
    (renamed aside), so a close writing the same path meanwhile creates a new file that
    is never touched; a claimed file whose bytes differ from what was read is put back
    (or kept aside when a new one already took its place): no loss."""
    for path, data in files.items():
        claimed = path.with_name(f".{path.name}.consolidating")
        try:
            os.rename(path, claimed)
        except OSError:
            continue
        if claimed.read_bytes() == data:
            claimed.unlink()
            continue
        if path.exists():
            say(
                "WARN",
                f"{FRAGMENTS}/{path.name} changed while consolidating: the older "
                f"copy is kept as {FRAGMENTS}/{claimed.name}",
            )
        else:
            os.rename(claimed, path)
            say(
                "WARN",
                f"{FRAGMENTS}/{path.name} changed while consolidating: kept for the next run",
            )


# ── new install ───────────────────────────────────────────────────────────────


def _template(tset: str) -> str:
    return (TOOLKIT / tset / "CHANGELOG.md").read_text(encoding="utf-8")


def _record_text(since: str | None, finished: list[str]) -> str:
    data = {
        "version": 1,
        "since": since,
        "activated": datetime.date.today().isoformat(),
        "finished": finished,
    }
    return json.dumps(data, indent=2) + "\n"


def _project_files(tset: str, note: str | None) -> dict[str, str]:
    """{project path: content} the changelog adds (an existing path is never touched)."""
    changelog = _template(tset)
    if note:
        changelog = changelog.replace("## [Unreleased]", f"{note}\n\n## [Unreleased]", 1)
    return {
        CHANGELOG.as_posix(): changelog,
        f"{FRAGMENTS}/{README}": (TOOLKIT / tset / "changelog.d" / README).read_text("utf-8"),
        RELEASES.as_posix(): (TOOLKIT / tset / "releases.md").read_text(encoding="utf-8"),
    }


def _put(base: Path, files: dict[str, str]) -> list[str]:
    written = []
    for rel, content in files.items():
        path = base / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


def install(project: Path, tset: str) -> int:
    """A new install: the changelog files (never over existing ones) + the record."""
    written = _put(project, _project_files(tset, None))
    if not (project / RECORD).exists():
        written += _put(project, {RECORD.as_posix(): _record_text(None, [])})
    print(" ".join(written))
    return 0


# ── activation of a started project (install.sh --changelog) ──────────────────


@dataclass
class Activation:
    project: Path
    top: Path
    prefix: str  # the project inside the repository ("" or "sub/dir/")
    base: str  # the base ref (origin/main or main)
    sha: str
    remote: bool
    tset: str = "templates"
    finished: list[str] = field(default_factory=list)
    kept_changelog: bool = False
    files: dict[str, str] = field(default_factory=dict)


def _must(root: Path, *args: str) -> str:
    res = _git(root, *args)
    if res.returncode != 0:
        raise ChangelogError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def _base(top: Path) -> tuple[str, bool]:
    remote = _git(top, "remote", "get-url", "origin").returncode == 0
    if remote:
        _must(top, "fetch", "-q", "origin")
        head = _git(top, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
        if head.returncode == 0 and head.stdout.strip():
            return head.stdout.strip(), True
        if _git(top, "rev-parse", "-q", "--verify", "refs/remotes/origin/main").returncode == 0:
            return "origin/main", True
    if _git(top, "rev-parse", "-q", "--verify", "refs/heads/main").returncode == 0:
        return "main", remote
    raise ChangelogError("no base branch (origin/HEAD, origin/main or main)")


def _show(act: Activation, rel: str) -> str | None:
    res = _git(act.top, "show", f"{act.base}:{act.prefix}{rel}")
    return res.stdout if res.returncode == 0 else None


def _finished(act: Activation) -> list[str]:
    text = _show(act, "feature_list.json")
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    owners = _owners(data)
    states = [(o, [str(s.get("status")) for s in subs]) for o, subs in owners.items()]
    done = [o for o, st in states if st and set(st) <= {"done", "baseline"}]
    return sorted(done, key=_key)


def _template_set(project: Path) -> str:
    """The project's template set: its install record, else what its files say."""
    import _templates  # lazy: only the activation needs it

    try:
        found = json.loads((project / ".harness" / "templates.json").read_text("utf-8"))
        tset = str(found.get("set", ""))
    except (OSError, ValueError, AttributeError):
        tset = ""
    return tset if tset in ("templates", "templates-c") else _templates.detect_set(project)


def prepare(project: Path) -> Activation:
    project = project.resolve()
    if not ((project / "harness").exists() or (project / ".harness" / "templates.json").exists()):
        raise ChangelogError(f"{project} has no harness installed (install.sh <project> first)")
    top = Path(_must(project, "rev-parse", "--show-toplevel")).resolve()
    prefix = project.relative_to(top).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    base, remote = _base(top)
    act = Activation(project, top, prefix, base, _must(top, "rev-parse", base), remote)
    act.tset = _template_set(project)
    act.finished = _finished(act)
    files = _project_files(act.tset, _activation_note(act))
    act.kept_changelog = _show(act, CHANGELOG.as_posix()) is not None
    files = {rel: text for rel, text in files.items() if _show(act, rel) is None}
    files[RECORD.as_posix()] = _record_text(act.sha, act.finished)
    act.files = files
    return act


def print_plan(act: Activation) -> None:
    print(f"Activate the changelog of {act.project} (base {act.base} {act.sha[:12]}):")
    print(f"  branch  {BRANCH} (from {act.base})")
    for rel in act.files:
        print(f"  add     {act.prefix}{rel}")
    if act.kept_changelog:
        note = _activation_note(act)
        print(f"  keep    {act.prefix}{CHANGELOG} (it exists). Add to it yourself, if missing:")
        print("            ## [Unreleased]   (the consolidation adds it otherwise)")
        print(f"            {note}")
    print(
        f"  record  since {act.sha[:12]}; finished (no fragment): "
        f"{', '.join(act.finished) or 'none'}"
    )
    print(f"  commit  {SUBJECT}")
    if act.remote:
        print(
            f"  PR      {BRANCH} -> {act.base.removeprefix('origin/')}, merged once its checks"
            " are green"
        )
    else:
        print(f"  merge   no origin remote: merge {BRANCH} into {act.base} yourself")


def _activation_note(act: Activation) -> str:
    return (
        f"Changes are recorded from commit {act.sha[:12]} (harness changelog activation, "
        f"{datetime.date.today().isoformat()}); earlier history is not listed here."
    )


def _confirm(yes: bool, what: str) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise ChangelogError("no terminal to confirm: re-run with --apply --yes (human only)")
    if input(f"{what} [y/N] ").strip().lower() != "y":
        raise ChangelogError("cancelled: nothing changed")


def _commit(act: Activation, wt: Path) -> bool:
    """Write the files and commit them on the branch; False when it was committed before."""
    base = wt / act.prefix
    if (base / RECORD).exists():
        return False
    _put(base, act.files)
    _must(wt, "add", "--", *(f"{act.prefix}{rel}" for rel in act.files))
    body = (
        f"{SUBJECT}\n\nChanges are recorded from {act.sha[:12]} on; tasks and issues "
        "finished before it need no fragment.\n"
    )
    _must(wt, "commit", "-q", "-m", body)
    return True


def _gh(wt: Path, *args: str) -> subprocess.CompletedProcess[str]:
    gh = shutil.which("gh")
    if gh is None:
        raise ChangelogError(f"gh not found: push {BRANCH} and open its PR yourself")
    return subprocess.run([gh, *args], cwd=wt, capture_output=True, text=True, check=False)


def _pr(act: Activation, wt: Path) -> str:
    """The PR state (OPEN / MERGED), opening it when there is none."""
    view = _gh(wt, "pr", "view", BRANCH, "--json", "state", "--jq", ".state")
    state = view.stdout.strip() if view.returncode == 0 else ""
    if state == "CLOSED":
        raise ChangelogError(f"the PR of {BRANCH} was closed: reopen it or delete the branch")
    if state:
        say("OK", f"PR of {BRANCH} already {state.lower()}")
        return state
    text = (
        "Activates the harness changelog: CHANGELOG.md, changelog.d/ and the activation "
        f"record (changes are recorded from {act.sha[:12]}).\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8") as body:
        body.write(text)
        body.flush()
        res = _gh(
            wt,
            "pr",
            "create",
            "--head",
            BRANCH,
            "--base",
            act.base.removeprefix("origin/"),
            "--title",
            SUBJECT,
            "--body-file",
            body.name,
        )
    if res.returncode != 0:
        raise ChangelogError(f"gh pr create failed: {res.stderr.strip()}")
    say("OK", f"PR opened: {res.stdout.strip()}")
    return "OPEN"


def _publish(act: Activation, wt: Path, yes: bool) -> int:
    _must(wt, "push", "-q", "-u", "origin", BRANCH)
    # The merge is pinned to what was pushed and checked (review rel1rev #6): a later
    # push to the branch makes `gh pr merge` refuse instead of merging unverified work.
    pushed = _must(wt, "rev-parse", "HEAD").strip()
    if _pr(act, wt) == "MERGED":
        return 0
    checks = _gh(wt, "pr", "checks", BRANCH, "--watch")
    merge = ["pr", "merge", BRANCH, "--merge", "--match-head-commit", pushed]
    if checks.returncode != 0 and RE_NO_CHECKS.search(checks.stdout + checks.stderr):
        say(
            "WARN",
            f"the PR of {BRANCH} has no checks (no CI on the repo): not merged. The human "
            f"reviews it and merges it: gh {' '.join(merge)}",
        )
        return 1
    if checks.returncode != 0:
        say(
            "WARN",
            f"the checks of the PR are not all green: not merged. Re-run "
            f"{activation_command(act.project)} --apply once they are\n{checks.stdout.strip()}",
        )
        return 1
    _confirm(yes, f"checks green: merge the PR of {BRANCH} at {pushed[:12]}?")
    res = _gh(wt, *merge)
    if res.returncode != 0:
        raise ChangelogError(f"gh pr merge failed: {res.stderr.strip()}")
    say("OK", f"merged: pull {act.base.removeprefix('origin/')} to get it")
    return 0


def _worktree(act: Activation, path: Path) -> None:
    local = _git(act.top, "rev-parse", "-q", "--verify", f"refs/heads/{BRANCH}").returncode == 0
    remote = f"refs/remotes/origin/{BRANCH}"
    if local:
        _must(act.top, "worktree", "add", "-q", str(path), BRANCH)
    elif act.remote and _git(act.top, "rev-parse", "-q", "--verify", remote).returncode == 0:
        _must(act.top, "worktree", "add", "-q", "-b", BRANCH, str(path), f"origin/{BRANCH}")
    else:
        _must(act.top, "worktree", "add", "-q", "-b", BRANCH, str(path), act.base)


def _owned_paths(act: Activation) -> set[str]:
    rels = [*_project_files(act.tset, None), RECORD.as_posix()]
    return {f"{act.prefix}{rel}" for rel in rels}


def verify_branch(act: Activation, ref: str) -> None:
    """An existing branch of that name must hold nothing but this activation: at most
    one commit over the base, with its subject, touching only the changelog files."""
    commits = _must(act.top, "rev-list", f"{act.base}..{ref}").split()
    why = ""
    if len(commits) > 1:
        why = f"{len(commits)} commits over {act.base}"
    elif commits:
        subject = _must(act.top, "log", "-1", "--format=%s", commits[0])
        changed = set(_must(act.top, "diff", "--name-only", f"{commits[0]}^", commits[0]).split())
        if subject != SUBJECT or not changed <= _owned_paths(act):
            why = "a commit that is not this activation"
    if why:
        raise ChangelogError(
            f"branch {BRANCH} already exists with {why}: nothing pushed or merged. Rename or "
            "delete it (it is not this command's), then re-run"
        )


def _existing_branch(act: Activation) -> str | None:
    if _git(act.top, "rev-parse", "-q", "--verify", f"refs/heads/{BRANCH}").returncode == 0:
        return BRANCH
    remote = f"refs/remotes/origin/{BRANCH}"
    if act.remote and _git(act.top, "rev-parse", "-q", "--verify", remote).returncode == 0:
        return f"origin/{BRANCH}"
    return None


def _run(act: Activation, yes: bool) -> int:
    existing = _existing_branch(act)
    if existing:
        verify_branch(act, existing)
    tmp = Path(tempfile.mkdtemp(prefix="harness-changelog-"))
    path = tmp / "wt"
    try:
        _worktree(act, path)
        made = _commit(act, path)
        say("OK", f"{BRANCH}: {'committed' if made else 'already committed'} ({SUBJECT})")
        if not act.remote:
            say("WARN", f"no origin remote: merge {BRANCH} into {act.base} yourself")
            return 0
        return _publish(act, path, yes)
    finally:
        _git(act.top, "worktree", "remove", "--force", str(path))
        shutil.rmtree(tmp, ignore_errors=True)


def activate(project: Path, apply: bool, yes: bool) -> int:
    try:
        act = prepare(project)
        if _show(act, RECORD.as_posix()) is not None:
            say("OK", f"the changelog is already activated on {act.base}: nothing to do")
            return 0
        print_plan(act)
        if not apply:
            say(
                "SKIP",
                f"dry-run: nothing changed. To apply: {activation_command(act.project)}"
                " --apply [--yes] (human only)",
            )
            return 0
        _confirm(yes, "apply this plan?")
        return _run(act, yes)
    except ChangelogError as exc:
        say("FAIL", str(exc), sys.stderr)
        return 1


# ── commands ─────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    # The subcommand comes first and its flags after it (review rel1rev #1).
    flags = {a for a in argv[1:] if a in ("--apply", "--yes")}
    args = argv[:1] + [a for a in argv[1:] if a not in flags]
    try:
        read_only = {"due": due, "fragment": _owed_command}  # one id, no flags used
        if args[:1] and args[0] in read_only and len(args) == 2:
            return read_only[args[0]](root, args[1])
        if args == ["consolidate"] and "--yes" not in flags:
            return consolidate(root, "--apply" in flags)
        if args[:1] == ["install"] and len(args) == 3 and not flags:
            return install(Path(args[1]), args[2])
        if args[:1] == ["activate"] and len(args) == 2:
            if "--yes" in flags and "--apply" not in flags:
                raise ChangelogError("--yes goes with --apply")
            return activate(Path(args[1]), "--apply" in flags, "--yes" in flags)
    except ChangelogError as exc:
        say("FAIL", str(exc), sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
