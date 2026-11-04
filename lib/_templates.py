"""Versioned harness-managed copied files (M0a, #14): manifest, bases, classification.

templates/MANIFEST.toml (and templates-c/) lists every managed file with its mode:
  whole      the file is the harness's: hashed and managed as a whole
  fragments  the file is the project's; only the harness-owned fragment is managed
and every released version, whose content is kept in <set>/_bases/<id>/<version>.

classify() tells, for one file, whether its harness part is installed at a known
version, absent, or neither (customised / modified / unknown provenance): the
last one stops a migration. The project record (.harness/templates.json) is the
authority once it exists; before that, a fragment's signature lines decide.
"""

from __future__ import annotations

import difflib
import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOOLKIT = Path(__file__).resolve().parents[1]
SETS = ("templates", "templates-c")


@dataclass(frozen=True)
class Template:
    id: str
    path: str
    mode: str
    current: str
    signature: tuple[str, ...]
    versions: dict[str, str]  # version -> base text

    @property
    def current_text(self) -> str:
        return self.versions[self.current]


@dataclass(frozen=True)
class State:
    """installed (version set), absent, nofile, or a stop reason in `problem`."""

    kind: str
    version: str | None = None
    problem: str = ""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load(tset: str) -> list[Template]:
    base = TOOLKIT / tset
    data = tomllib.loads((base / "MANIFEST.toml").read_text("utf-8"))
    out = []
    for t in data["template"]:
        versions = {
            v["version"]: (base / "_bases" / t["id"] / v["version"]).read_text("utf-8")
            for v in t["version"]
        }
        out.append(
            Template(t["id"], t["path"], t["mode"], t["current"], tuple(t["signature"]), versions)
        )
    return out


def _contains(text: str, fragment: str) -> bool:
    """`fragment`'s lines appear as a contiguous run of whole lines of `text`."""
    lines, frag = text.splitlines(), fragment.splitlines()
    n = len(frag)
    return any(lines[i : i + n] == frag for i in range(len(lines) - n + 1))


def fragment_version(tpl: Template, text: str) -> str | None:
    for version, base in sorted(tpl.versions.items(), reverse=True):
        if _contains(text, base):
            return version
    return None


def three_way(tpl: Template, base_version: str, ours: str) -> str:
    """Base, project and current side by side, as unified diffs from the base."""
    base, theirs = tpl.versions[base_version], tpl.current_text
    ours_diff = difflib.unified_diff(
        base.splitlines(True), ours.splitlines(True), f"base v{base_version}", "project"
    )
    theirs_diff = difflib.unified_diff(
        base.splitlines(True), theirs.splitlines(True), f"base v{base_version}", "current"
    )
    return (
        f"<<<<<<< project {tpl.path} vs base v{base_version}\n{''.join(ours_diff)}"
        f"||||||| base v{base_version}\n{base}"
        f"=======\n{''.join(theirs_diff)}>>>>>>> current v{tpl.current}\n"
    )


def _classify_whole(tpl: Template, text: str, recorded: dict[str, Any] | None) -> State:
    found = next((v for v, b in tpl.versions.items() if sha256(b) == sha256(text)), None)
    if found:
        return State("installed", found)
    base = recorded.get("version") if recorded and recorded.get("state") == "installed" else None
    if base in tpl.versions:
        return State("stop", problem=f"{tpl.path}: customised\n{three_way(tpl, str(base), text)}")
    return State("stop", problem=f"{tpl.path}: unknown provenance (matches no known version)")


def _classify_fragment(tpl: Template, text: str, recorded: dict[str, Any] | None) -> State:
    found = fragment_version(tpl, text)
    if found:
        return State("installed", found)
    if recorded and recorded.get("state") == "installed":
        return State("stop", problem=f"{tpl.path}: harness fragment {tpl.id} modified or removed")
    if {line.rstrip() for line in text.splitlines()} & set(tpl.signature):
        return State(
            "stop", problem=f"{tpl.path}: harness fragment {tpl.id} modified (unknown provenance)"
        )
    return State("absent")


def classify(tpl: Template, root: Path, recorded: dict[str, Any] | None) -> State:
    path = root / tpl.path
    if not path.is_file():
        return State("absent" if tpl.mode == "whole" else "nofile")
    text = path.read_text("utf-8")
    if tpl.mode == "whole":
        return _classify_whole(tpl, text, recorded)
    return _classify_fragment(tpl, text, recorded)


def detect_set(root: Path) -> str:
    """The template set closest to the project's Makefile (bases are shared)."""
    mk = root / "Makefile"
    text = mk.read_text("utf-8").splitlines() if mk.is_file() else []

    def ratio(tset: str) -> float:
        tpl = (TOOLKIT / tset / "Makefile").read_text("utf-8").splitlines()
        return difflib.SequenceMatcher(None, text, tpl).ratio()

    return max(SETS, key=ratio)


def record_entry(tpl: Template, state: State) -> dict[str, Any]:
    if state.kind != "installed" or state.version is None:
        return {"id": tpl.id, "mode": tpl.mode, "state": state.kind}
    return {
        "id": tpl.id,
        "mode": tpl.mode,
        "state": "installed",
        "version": state.version,
        "sha256": sha256(tpl.versions[state.version]),
    }
