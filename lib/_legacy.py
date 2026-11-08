"""Sealed legacy inventory (plan 0.6, M0b): what was approved or done before a rule existed.

Human-run, never through a make target:

  harness/legacy_inventory.sh create --at <commit>   build progress/legacy_inventory.json
                                                     from the tree at <commit> (never the
                                                     working tree)
  harness/legacy_inventory.sh review                 Codex (read-only) checks it; this
                                                     command writes legacy_inventory_review.md
  harness/legacy_inventory.sh seal                   needs INVENTORY_APPROVED on this exact JSON
  harness/legacy_inventory.sh verify                 OK | WARN not created | FAIL <reason>

The inventory is closed and append-never: `create` refuses while any of its three
files exists. An item leaves it by changing (its hashes no longer match), so it is
never rewritten. Later rules (I1, I4, I16) read it through `legacy_entry` and
`is_legacy_unchanged`; nothing calls them yet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _harness
import _plan_seal

INVENTORY = Path("progress/legacy_inventory.json")
REVIEW = Path("progress/legacy_inventory_review.md")
SEAL = Path("progress/legacy_inventory.sha256")
ARTIFACTS = (INVENTORY, REVIEW, SEAL)
SCHEMA = 1
TOOLKIT = Path(__file__).resolve().parents[1]
ROLE = TOOLKIT / "codex" / "reviewer.md"
VERDICTS = ("INVENTORY_APPROVED", "CHANGES_REQUESTED")
RE_LAST = re.compile(r"^VERDICT: (\S+)$")
RE_REVIEW_VERDICT = re.compile(r"^- verdict: (\S+)$", re.MULTILINE)
RE_REVIEW_SHA = re.compile(r"^- inventory_sha256: ([0-9a-f]{64})$", re.MULTILINE)
ROADMAP_ID = "roadmap"
DRAFT_ID = "issue_draft"

Item = dict[str, Any]
FileRef = dict[str, str] | None


class Refused(Exception):
    """The command refuses; nothing was written."""


# ── small helpers ────────────────────────────────────────────────────────────


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str | None:
    return _sha_bytes(path.read_bytes()) if path.is_file() else None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)


def _is_ancestor(root: Path, commit: str) -> bool:
    return _git(root, "merge-base", "--is-ancestor", commit, "HEAD").returncode == 0


def _toolkit_version() -> str:
    res = _git(TOOLKIT, "describe", "--always", "--dirty", "--tags")
    return res.stdout.strip() or "unknown"


def _ref(tree: Path, rel: str) -> FileRef:
    digest = _sha_file(tree / rel)
    return None if digest is None else {"path": rel, "sha256": digest}


def _verdict_ref(tree: Path, rel: str) -> FileRef:
    ref = _ref(tree, rel)
    if ref is not None:
        ref["verdict"] = _harness.read_verdict(tree / rel) or "NONE"
    return ref


def _verdict(tree: Path, rel: str) -> str | None:
    return _harness.read_verdict(tree / rel) or None


# ── create ───────────────────────────────────────────────────────────────────


def _acceptance(tree: Path, sid: str) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(tree).as_posix(), "sha256": _sha_bytes(path.read_bytes())}
        for path in _harness.acceptance_files(tree, sid)
    ]


def _spec_ref(tree: Path, spec: str) -> Item | None:
    """A declared spec keeps its path even when absent (sha256 null): its later
    appearance must count as a change. null only when no spec is declared."""
    return {"path": spec, "sha256": _sha_file(tree / spec)} if spec else None


def _leaf_entry(tree: Path, leaf: Item, approved_phases: set[str]) -> Item:
    sid = str(leaf["id"])
    spec = str(leaf.get("spec") or "")
    return {
        "id": sid,
        "status": leaf["status"],
        "phase": leaf["phase"] or None,
        "issue": leaf["issue"] or None,
        "baseline": leaf["status"] == "baseline"
        and not leaf["issue"]
        and leaf["phase"] in approved_phases,
        "declared_files": list(leaf.get("files") or []),
        "spec": _spec_ref(tree, spec),
        "acceptance": _acceptance(tree, sid),
        "seal": _ref(tree, f"progress/acceptance_{sid}.sha256"),
        "plan_review": _verdict_ref(tree, f"progress/plan_review_{sid}.md"),
        "review": _verdict_ref(tree, f"progress/review_{sid}.md"),
    }


def _approval(tree: Path, review: str, seal: str, approved: dict[str, str]) -> Item:
    return {
        "verdict": _verdict(tree, review),
        "review": _ref(tree, review),
        "seal": _ref(tree, seal),
        "approved": approved,
    }


def _approval_files(kind: str, item_id: str) -> tuple[str, str]:
    """(review, seal) paths of a plan item: kind is phase, issue, roadmap or draft."""
    return {
        "phase": (f"progress/phase_review_{item_id}.md", f"progress/plan_phase_{item_id}.sha256"),
        "issue": (f"progress/issue_review_{item_id}.md", f"progress/plan_issue_{item_id}.sha256"),
        "roadmap": ("progress/roadmap_review.md", "progress/plan_roadmap.sha256"),
        "draft": ("progress/issue_draft_review.md", "progress/issue_draft.sha256"),
    }[kind]


def _draft(tree: Path) -> Item | None:
    draft = "progress/issue_draft.md"
    review, seal = _approval_files("draft", DRAFT_ID)
    digest = _sha_file(tree / draft)
    if digest is None:  # no draft: a review or seal left alone approves nothing
        return None
    return _approval(tree, review, seal, {draft: digest})


RE_LEAF_ID = re.compile(r"^(ISS-\d+|\d+(\.\d+)*)\.\d+$")
RE_PHASE_ID = re.compile(r"^\d{2}$")


def _validate(data: Item) -> None:
    """feature_list.json is untrusted: ids and spec paths must stay inside the tree."""
    if not isinstance(data, dict):
        raise Refused("feature_list.json is not an object")
    for phase in data.get("phases", []):
        if not RE_PHASE_ID.match(str(phase.get("id", ""))):
            raise Refused(f"unsafe phase id: {phase.get('id')!r}")
    for issue in data.get("issues", []):
        if not _harness.RE_ISSUE_ID.match(str(issue.get("id", ""))):
            raise Refused(f"unsafe issue id: {issue.get('id')!r}")
    for leaf in _harness.leaves(data):
        if not RE_LEAF_ID.match(str(leaf["id"])):
            raise Refused(f"unsafe leaf id: {leaf['id']!r}")
        spec = str(leaf.get("spec") or "")
        parts = Path(spec).parts
        if spec and (Path(spec).is_absolute() or ".." in parts or "\\" in spec):
            raise Refused(f"unsafe spec path for {leaf['id']}: {spec!r}")


def build(tree: Path) -> Item:
    """The inventory body of an extracted install root (header excluded)."""
    try:
        data: Item = json.loads((tree / "feature_list.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"invalid feature_list.json: {exc}") from exc
    _validate(data)
    phases = {
        str(p["id"]): _approval(
            tree,
            *_approval_files("phase", str(p["id"])),
            _plan_seal.phase_entries(tree, str(p["id"])),
        )
        for p in data.get("phases", [])
    }
    approved = {pid for pid, p in phases.items() if p["verdict"] == "PHASE_APPROVED"}
    leaves = {str(leaf["id"]): _leaf_entry(tree, leaf, approved) for leaf in _harness.leaves(data)}
    issues = {
        str(i["id"]): _approval(
            tree,
            *_approval_files("issue", str(i["id"])),
            _plan_seal.issue_entries(tree, str(i["id"])),
        )
        for i in data.get("issues", [])
    }
    roadmap = _approval(
        tree,
        *_approval_files("roadmap", ROADMAP_ID),
        _plan_seal.roadmap_entries(tree),
    )
    return {
        "leaves": leaves,
        "phases": phases,
        "roadmap": roadmap,
        "issues": issues,
        "issue_draft": _draft(tree),
    }


def _extract(root: Path, commit: str, prefix: str, dest: Path) -> None:
    treeish = f"{commit}:{prefix.rstrip('/')}" if prefix else commit
    archive = subprocess.run(
        # From the git top: run from a subdirectory, git archive would restrict to it again.
        ["git", "archive", "--format=tar", treeish],
        cwd=_git(root, "rev-parse", "--show-toplevel").stdout.strip() or root,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        raise Refused(f"git archive failed: {archive.stderr.decode(errors='replace').strip()}")
    tar_path = dest.parent / "tree.tar"
    tar_path.write_bytes(archive.stdout)
    with tarfile.open(tar_path) as tar:
        tar.extractall(dest, filter=_regular_only)


def _regular_only(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
    """Only directories and regular files: links are never hashed and could point outside."""
    if not (member.isdir() or member.isreg()):
        return None
    return tarfile.data_filter(member, dest)


def create(root: Path, at: str) -> Item:
    left = [str(rel) for rel in ARTIFACTS if _present(root / rel)]
    if left:
        raise Refused(f"an inventory already exists ({', '.join(left)}); it is append-never")
    res = _git(root, "rev-parse", "--verify", "--quiet", f"{at}^{{commit}}")
    if res.returncode != 0:
        raise Refused(f"unknown commit: {at}")
    commit = res.stdout.strip()
    if not _is_ancestor(root, commit):
        raise Refused(f"{commit} is not an ancestor of HEAD")
    prefix = _git(root, "rev-parse", "--show-prefix").stdout.strip()
    if _git(root, "cat-file", "-e", f"{commit}:{prefix}feature_list.json").returncode != 0:
        raise Refused(f"feature_list.json is missing at {commit}")
    with tempfile.TemporaryDirectory(prefix="legacy-inv-") as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        _extract(root, commit, prefix, tree)
        body = build(tree)
    inventory: Item = {
        "version": SCHEMA,
        "commit": commit,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "toolkit": _toolkit_version(),
        "install_root": prefix.rstrip("/") or ".",
        **body,
    }
    _write(root / INVENTORY, json.dumps(inventory, indent=2, sort_keys=True) + "\n", exclusive=True)
    return inventory


def _present(path: Path) -> bool:
    """Exists as anything, a dangling link included."""
    return os.path.lexists(path)


def _write(path: Path, text: str, *, exclusive: bool) -> None:
    """All or nothing: an exclusively created temp file next to the target, then publish.

    exclusive: the target must not exist (create, seal: append-never), so a file that
    appeared during the operation is never overwritten; otherwise an atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if exclusive:
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise Refused(f"{path.name} appeared during the operation") from exc
        else:
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ── review ───────────────────────────────────────────────────────────────────

PROMPT = """You are reviewing a sealed legacy inventory (plan 0.6) of the project in the
current directory. You are read-only: do not modify anything.

The inventory below was generated from git commit {commit} (install root: {install_root}).
Check, using `git show {commit}:<path>` / `git ls-tree` (never the working tree):
1. every entry (ids, statuses, paths, sha256 values, verdicts) matches the tree at that commit;
2. nothing that only exists after {commit} was included;
3. no done or baseline item at that commit is missing.
Approved content hashes of phases, roadmap and issues use the plan seal normalization
(lib/_plan_seal.py); `null` means the file was absent at the commit, which is expected for
legacy items. Symlinks of the tree are skipped on purpose (never inventoried, never followed):
only directories and regular files were extracted.

The last non-empty line of your answer must be exactly one of:
VERDICT: INVENTORY_APPROVED
VERDICT: CHANGES_REQUESTED

--- progress/legacy_inventory.json (sha256 {sha}) ---
{body}
"""


def _role(key: str) -> str:
    try:
        text = ROLE.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"^{key}:[ \t]*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def _codex_args(out: Path) -> list[str]:
    args = ["codex", "exec"]
    model, effort = _role("codex_model"), _role("codex_effort")
    if model:
        args += ["--model", model]
    args += ["-s", "read-only"]
    if effort in ("low", "medium", "high", "xhigh"):
        args += ["-c", f"model_reasoning_effort={effort}"]
    return [*args, "-o", str(out)]


def parse_verdict(message: str) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        raise Refused("Codex returned an empty message")
    m = RE_LAST.match(lines[-1])
    if not m or m.group(1) not in VERDICTS:
        raise Refused(f"the last line is not a valid verdict: {lines[-1]!r}")
    return m.group(1)


def review(root: Path) -> str:
    if _present(root / SEAL):
        raise Refused("the inventory is already sealed")
    # An earlier review must never outlive a new attempt (failed or not), so it goes
    # before anything else can fail.
    (root / REVIEW).unlink(missing_ok=True)
    inventory = _load(root)
    raw = (root / INVENTORY).read_bytes()
    sha = _sha_bytes(raw)
    prompt = PROMPT.format(
        commit=inventory["commit"],
        install_root=inventory["install_root"],
        sha=sha,
        body=raw.decode("utf-8"),
    )
    with tempfile.TemporaryDirectory(prefix="legacy-review-") as tmp:
        out = Path(tmp) / "last_message.md"
        try:
            run = subprocess.run(
                _codex_args(out),
                input=prompt,
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise Refused(f"cannot run codex: {exc}") from exc
        if run.returncode != 0:
            raise Refused(f"codex exited {run.returncode}: {run.stderr.strip()[-500:]}")
        message = out.read_text(encoding="utf-8") if out.is_file() else ""
    verdict = parse_verdict(message)
    text = (
        "# Legacy inventory review (Codex, read-only)\n\n"
        f"- verdict: {verdict}\n"
        f"- inventory_sha256: {sha}\n"
        f"- commit: {inventory['commit']}\n"
        f"- toolkit: {_toolkit_version()}\n\n"
        "## Codex message\n\n"
        f"{message.rstrip()}\n"
    )
    _write(root / REVIEW, text, exclusive=False)
    return verdict


def _review_fields(root: Path) -> tuple[str, str]:
    try:
        text = (root / REVIEW).read_text(encoding="utf-8")
    except OSError:
        return "", ""
    verdict, sha = RE_REVIEW_VERDICT.search(text), RE_REVIEW_SHA.search(text)
    return (verdict.group(1) if verdict else ""), (sha.group(1) if sha else "")


# ── seal / verify ────────────────────────────────────────────────────────────


def _load(root: Path) -> Item:
    try:
        data: Item = json.loads((root / INVENTORY).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Refused("no inventory: run create first") from exc
    except (OSError, ValueError) as exc:
        raise Refused(f"invalid inventory JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != SCHEMA or "commit" not in data:
        raise Refused("invalid inventory JSON: unknown schema")
    return data


def seal(root: Path) -> str:
    if _present(root / SEAL):
        raise Refused("already sealed")
    _load(root)
    sha = _sha_bytes((root / INVENTORY).read_bytes())
    verdict, reviewed = _review_fields(root)
    if verdict != "INVENTORY_APPROVED":
        raise Refused(f"the review is not INVENTORY_APPROVED (got {verdict or 'none'})")
    if reviewed != sha:
        raise Refused("the inventory changed after the review")
    _write(root / SEAL, _seal_line(sha), exclusive=True)
    return sha


def _seal_line(sha: str) -> str:
    return f"{sha}  {INVENTORY.as_posix()}\n"


def _seal_problem(root: Path) -> str:
    """Why the sealed state does not hold, or "" when it does."""
    sha = _sha_bytes((root / INVENTORY).read_bytes())
    seal = root / SEAL
    if seal.is_symlink() or not seal.is_file():
        return "the seal is missing or not a regular file"
    try:
        sealed = seal.read_text(encoding="utf-8")
    except OSError:
        return "the seal is unreadable"
    if sealed != _seal_line(sha):
        return "the inventory does not match its seal"
    verdict, reviewed = _review_fields(root)
    if verdict != "INVENTORY_APPROVED" or reviewed != sha:
        return "the review is not INVENTORY_APPROVED on this inventory"
    return ""


def verify(root: Path) -> tuple[str, str]:
    """("OK" | "WARN" | "FAIL", reason)."""
    if not any(_present(root / rel) for rel in ARTIFACTS):
        return "WARN", "not created"
    try:
        data = _load(root)
    except Refused as exc:
        return "FAIL", str(exc)
    problem = _seal_problem(root)
    if not problem and not _is_ancestor(root, str(data["commit"])):
        problem = f"commit {data['commit']} is no longer an ancestor of HEAD"
    return ("FAIL", problem) if problem else ("OK", f"sealed at {data['commit']}")


# ── library for the later rules (I1, I4, I16) ────────────────────────────────


def _find(data: Item, item_id: str) -> Item | None:
    if item_id == ROADMAP_ID:
        found: Item | None = data.get("roadmap")
    elif item_id == DRAFT_ID:
        found = data.get("issue_draft")
    else:
        found = None
        for kind in ("leaves", "phases", "issues"):
            if item_id in data.get(kind, {}):
                found = data[kind][item_id]
                break
    return found


def legacy_entry(root: Path, item_id: str) -> Item | None:
    """The inventory entry of item_id, only while the inventory verifies OK."""
    if verify(root)[0] != "OK":
        return None
    return _find(_load(root), item_id)


def _sha_of(ref: FileRef) -> str | None:
    return ref["sha256"] if ref else None


def _leaf_evidence(entry: Item) -> Item:
    return {
        "status": entry.get("status"),
        "phase": entry.get("phase") or None,
        "issue": entry.get("issue") or None,
        "declared_files": list(entry.get("declared_files") or []),
        "spec": entry.get("spec"),
        "acceptance": list(entry.get("acceptance") or []),
        "seal": _sha_of(entry.get("seal")),
        "plan_review": _sha_of(entry.get("plan_review")),
        "review": _sha_of(entry.get("review")),
    }


def _current_leaf(root: Path, sid: str) -> Item | None:
    """The leaf as feature_list.json (what the harness reads) declares it now."""
    try:
        data: Item = json.loads((root / "feature_list.json").read_text(encoding="utf-8"))
        found = [leaf for leaf in _harness.leaves(data) if str(leaf["id"]) == sid]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return found[0] if len(found) == 1 else None


def _leaf_now(root: Path, entry: Item) -> Item | None:
    sid = str(entry["id"])
    leaf = _current_leaf(root, sid)
    if leaf is None:
        return None
    return {
        "status": leaf.get("status"),
        "phase": leaf.get("phase") or None,
        "issue": leaf.get("issue") or None,
        "declared_files": list(leaf.get("files") or []),
        "spec": _spec_ref(root, str(leaf.get("spec") or "")),
        "acceptance": _acceptance(root, sid),
        "seal": _sha_file(root / f"progress/acceptance_{sid}.sha256"),
        "plan_review": _sha_file(root / f"progress/plan_review_{sid}.md"),
        "review": _sha_file(root / f"progress/review_{sid}.md"),
    }


def _draft_now(root: Path) -> dict[str, str]:
    rel = "progress/issue_draft.md"
    digest = _sha_file(root / rel)
    return {} if digest is None else {rel: digest}


def is_legacy_unchanged(root: Path, item_id: str) -> bool:
    """True only while ALL the item's recorded evidence is as recorded: same hashes, no
    file gone, none that was absent (null) has appeared. A leaf also keeps the status,
    owner (phase/issue), declared files and spec path that feature_list.json gives it
    now. Plan items compare their content with the seal normalization (_plan_seal), so
    a `- status:` change alone is not a change, plus their review and seal files (a
    new approval is a change)."""
    entry = legacy_entry(root, item_id)
    if entry is None:
        return False
    data = _load(root)
    if item_id in data.get("leaves", {}):
        return bool(_leaf_evidence(entry) == _leaf_now(root, entry))
    if item_id == ROADMAP_ID:
        kind, now = "roadmap", _plan_seal.roadmap_entries(root)
    elif item_id == DRAFT_ID:
        kind, now = "draft", _draft_now(root)
    elif item_id in data.get("phases", {}):
        kind, now = "phase", _plan_seal.phase_entries(root, item_id)
    else:
        kind, now = "issue", _plan_seal.issue_entries(root, item_id)
    review, seal = _approval_files(kind, item_id)
    return bool(
        now == entry["approved"]
        and _sha_file(root / review) == _sha_of(entry.get("review"))
        and _sha_file(root / seal) == _sha_of(entry.get("seal"))
    )


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="legacy_inventory.sh",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create").add_argument("--at", required=True, help="the commit to inventory")
    for name in ("review", "seal", "verify"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.cmd == "create":
            inv = create(root, args.at)
            print(f"created {INVENTORY} at {inv['commit']} ({len(inv['leaves'])} leaves)")
        elif args.cmd == "review":
            print(f"review: {review(root)} -> {REVIEW}")
        elif args.cmd == "seal":
            print(f"sealed {INVENTORY}: {seal(root)}")
        else:
            status, reason = verify(root)
            print(f"{status} legacy inventory: {reason}")
            return 1 if status == "FAIL" else 0
    except Refused as exc:
        print(f"legacy_inventory {args.cmd}: refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
