#!/usr/bin/env python3
"""Verdict handling of a review gate: only a verdict written by that run counts.

review_with_codex.sh calls this module around the reviewer CLI (auto mode, every
gate but `doc`):

  _verdict.py begin  --owner <pid> <gate> <verdict>   set the previous verdict aside;
                                                      prints a read-only copy of it
  _verdict.py commit --owner <pid> <gate> <verdict>   validate the new verdict, write its
                                                      receipt; prints the token
  _verdict.py abort  --owner <pid> <gate> <verdict>   restore the previous verdict (only
                                                      for its owner, while active)

Slot: $(git rev-parse --git-path harness-verdict-slot)/<verdict-basename>/ holds
`owner` (pid), `phase` and, when there was a previous verdict, `prev` (a copy, so
.git may live on another filesystem). The slot is built under a temporary name and
renamed into place: that rename is the per-verdict lock. Recovery decides only from
`phase`:

  preparing          the verdict was not touched: drop the slot
  active             abort
  aborting           restore `prev` again (idempotent), drop the slot
  committed <sha>    if the verdict still has that hash, write its receipt; drop the slot

Durability: every step ends with an fsync of the directory it changed, so a later
destructive step (deleting or overwriting a verdict, dropping the slot) is never
persisted without the state that protects it. A slot is dropped by renaming it to a
trash name first, so a slot never exists half removed.

Receipt: progress/verdict_<verdict-basename>.sha256, one line `<sha256>  <path>  <gate>`.
It detects later byte changes; it does not prove who wrote the bytes.

  _verdict.py receipt <gate> [<id>]   human only (manual mode): validate the verdict
                                      written by hand-run Codex and write its receipt

`make check` (integrity) reports receipts as WARN only (receipt_warnings): a verdict
that no longer matches its receipt, verdicts without receipt (one summary line; legacy
items exempt while the inventory verifies, plan 0.6) and receipts without verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from _harness import RE_VERDICT
from _status import say

GATE_TOKENS: dict[str, str] = {
    "roadmap": "ROADMAP_APPROVED",
    "phase": "PHASE_APPROVED",
    "issue": "ISSUE_APPROVED",
    "issue-draft": "DRAFT_APPROVED",
    "plan": "PLAN_APPROVED",
    "code": "APPROVED",
}
REJECTED = "CHANGES_REQUESTED"
TMP_PREFIX = ".tmp-"
TRASH_PREFIX = ".trash-"
SLOT_DIR = "harness-verdict-slot"
PARTIAL_DIR = "harness-verdict-partial"
PREV = "prev"
PHASE = "phase"
OWNER = "owner"


class VerdictError(Exception):
    """The verdict protocol cannot go on; the message says why."""


class SlotBusy(VerdictError):
    """Another live gate owns the slot of this verdict."""


class InvalidVerdict(VerdictError):
    """The reviewer left no valid verdict for this gate."""


class FileSystem(Protocol):
    """The primitive operations of the protocol (a port: tests record them)."""

    def mkdir(self, path: Path) -> None: ...
    def write(self, path: Path, data: bytes) -> None:
        """Create or truncate, without fsync: the protocol calls fsync_file itself."""
        ...

    def rename(self, src: Path, dst: Path) -> None: ...
    def unlink(self, path: Path) -> None: ...
    def rmdir(self, path: Path) -> None: ...
    def fsync_dir(self, path: Path) -> None: ...
    def fsync_file(self, path: Path) -> None: ...


class OsFileSystem:
    """FileSystem on the real OS."""

    def mkdir(self, path: Path) -> None:
        os.mkdir(path)

    def write(self, path: Path, data: bytes) -> None:
        """Create or truncate `path` (never through a symlink); the caller fsyncs it."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
        finally:
            os.close(fd)

    def rename(self, src: Path, dst: Path) -> None:
        os.rename(src, dst)

    def unlink(self, path: Path) -> None:
        os.unlink(path)

    def rmdir(self, path: Path) -> None:
        os.rmdir(path)

    def fsync_dir(self, path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def fsync_file(self, path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def pid_alive(pid: int) -> bool:
    """True if `pid` exists (a reused pid also counts: that fails safe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def _read_regular(path: Path) -> bytes | None:
    """The bytes of a regular, non-symlink file; None for anything else or nothing."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _token(data: bytes) -> str:
    match = RE_VERDICT.search(data.decode("utf-8", errors="replace"))
    return match.group(1) if match else ""


def checked_token(gate: str, verdict: str, data: bytes) -> str:
    """The verdict token, if this gate accepts it; InvalidVerdict otherwise."""
    token = _token(data)
    accepted = (GATE_TOKENS[gate], REJECTED)
    if token not in accepted:
        found = token or "no verdict token"
        raise InvalidVerdict(
            f"{verdict} has {found}; the {gate} gate accepts {' or '.join(accepted)}"
        )
    return token


def _unique(path: Path) -> Path:
    """`path`, or `path.N` with the first free N: a kept output is never overwritten.

    Only the slot owner writes these names, so no other gate races for them."""
    candidate, n = path, 0
    while os.path.lexists(candidate):
        n += 1
        candidate = path.with_name(f"{path.name}.{n}")
    return candidate


def receipt_rel(verdict: str) -> str:
    return f"progress/verdict_{Path(verdict).name}.sha256"


def put_atomic(fs: FileSystem, path: Path, data: bytes, tag: int) -> None:
    """Replace `path` atomically: temporary file, fsync, rename, directory fsync."""
    tmp = path.parent / f"{TMP_PREFIX}{path.name}.{tag}"
    fs.write(tmp, data)
    fs.fsync_file(tmp)
    fs.rename(tmp, path)
    fs.fsync_dir(path.parent)


def receipt_line(digest: str, verdict: str, gate: str) -> bytes:
    return f"{digest}  {verdict}  {gate}\n".encode()


# ── verdict files and their receipts (section C, reporting) ──────────────────

# (verdict basename pattern, gate, item id builder): the item ids are those of the
# legacy inventory; the gate is the one whose receipt the verdict must carry.
VERDICT_NAMES: tuple[tuple[re.Pattern[str], str, Callable[[re.Match[str]], str]], ...] = (
    (re.compile(r"^roadmap_review\.md$"), "roadmap", lambda m: "roadmap"),
    (re.compile(r"^phase_review_(\d+)\.md$"), "phase", lambda m: m.group(1)),
    (re.compile(r"^issue_review_(ISS-\d+)\.md$"), "issue", lambda m: m.group(1)),
    (re.compile(r"^issue_draft_review\.md$"), "issue-draft", lambda m: "issue_draft"),
    (re.compile(r"^plan_review_(.+)\.md$"), "plan", lambda m: m.group(1)),
    (re.compile(r"^review_(.+)\.md$"), "code", lambda m: m.group(1)),
)
RECEIPT_NAME = re.compile(r"^verdict_(.+)\.sha256$")
SUMMARY_IDS = 3


def verdict_path(gate: str, item_id: str) -> str:
    """The verdict file a gate writes for an item (same names as review_with_codex.sh)."""
    return {
        "roadmap": "progress/roadmap_review.md",
        "phase": f"progress/phase_review_{item_id}.md",
        "issue": f"progress/issue_review_{item_id}.md",
        "issue-draft": "progress/issue_draft_review.md",
        "plan": f"progress/plan_review_{item_id}.md",
        "code": f"progress/review_{item_id}.md",
    }[gate]


def verdict_files(root: Path) -> dict[str, tuple[str, str]]:
    """{verdict path: (gate, item id)} of every verdict file in progress/."""
    found: dict[str, tuple[str, str]] = {}
    for path in sorted((root / "progress").glob("*.md")):
        for pattern, gate, item in VERDICT_NAMES:
            match = pattern.match(path.name)
            if match and path.is_file():
                found[f"progress/{path.name}"] = (gate, item(match))
                break
    return found


def _receipt_matches(root: Path, receipt: Path, verdict: str, gate: str) -> bool:
    """Exactly the line the gate writes for the verdict's current bytes; a receipt that
    is not a regular file, or cannot be read, never matches."""
    found = _read_regular(receipt)
    data = _read_regular(root / verdict)
    if found is None or data is None:
        return False
    return found == receipt_line(hashlib.sha256(data).hexdigest(), verdict, gate)


def receipt_warnings(root: Path, exempt: Callable[[str], bool]) -> list[str]:
    """`exempt(item_id)` is asked once per verdict without receipt (legacy, plan 0.6)."""
    """Table C of I2, WARN only: mismatches, verdicts without receipt, stray receipts."""
    verdicts = verdict_files(root)
    out: list[str] = []
    covered: set[str] = set()
    for receipt in sorted((root / "progress").glob("verdict_*.sha256")):
        match = RECEIPT_NAME.match(receipt.name)
        if not match:
            continue
        verdict = f"progress/{match.group(1)}"
        rel = f"progress/{receipt.name}"
        if verdict not in verdicts:
            out.append(f"receipt {rel} has no verdict {verdict}: remove it, or re-run the gate")
            continue
        covered.add(verdict)
        if not _receipt_matches(root, receipt, verdict, verdicts[verdict][0]):
            out.append(
                f"{verdict} does not match its receipt {rel}: changed after its gate; "
                "re-run the gate"
            )
    missing = [v for v in verdicts if v not in covered and not exempt(verdicts[v][1])]
    if missing:
        ids = sorted({verdicts[v][1] for v in missing})
        shown = ", ".join(ids[:SUMMARY_IDS]) + (", ..." if len(ids) > SUMMARY_IDS else "")
        out.append(
            f"{len(missing)} verdict(s) without a receipt (not written by a gate run): "
            f"{shown}; re-run their gates"
        )
    return out


@dataclass
class Slot:
    """The set-aside state of one verdict file during one gate run."""

    root: Path
    verdict: str
    gate: str
    slot_root: Path
    partial_root: Path
    owner: int
    fs: FileSystem = field(default_factory=OsFileSystem)
    is_alive: Callable[[int], bool] = pid_alive
    clock: Callable[[], str] = _now

    # ── paths ────────────────────────────────────────────────────────────────

    @property
    def _path(self) -> Path:
        return self.root / self.verdict

    @property
    def _name(self) -> str:
        return self._path.name

    @property
    def _slot(self) -> Path:
        return self.slot_root / self._name

    @property
    def _receipt(self) -> Path:
        return self.root / receipt_rel(self.verdict)

    # ── primitives ───────────────────────────────────────────────────────────

    def _ensure_dir(self, path: Path) -> None:
        if path.is_dir():
            return
        self._ensure_dir(path.parent)
        self.fs.mkdir(path)
        self.fs.fsync_dir(path.parent)

    def _put(self, path: Path, data: bytes) -> None:
        put_atomic(self.fs, path, data, self.owner)

    def _discard(self, folder: Path) -> None:
        """Remove a slot-like folder: rename it to a trash name first, then empty it."""
        trash = folder
        if not folder.name.startswith(TRASH_PREFIX):
            trash = folder.parent / f"{TRASH_PREFIX}{folder.name}.{self.owner}"
            if trash.exists():
                self._discard(trash)
            self.fs.rename(folder, trash)
            self.fs.fsync_dir(folder.parent)
        for child in sorted(trash.iterdir()):
            self.fs.unlink(child)
        self.fs.rmdir(trash)
        self.fs.fsync_dir(trash.parent)

    def _phase(self) -> list[str]:
        try:
            return (self._slot / PHASE).read_text(encoding="utf-8").split()
        except OSError as exc:
            raise VerdictError(f"verdict slot {self._slot} has no readable phase") from exc

    def _set_phase(self, *words: str) -> None:
        self._put(self._slot / PHASE, (" ".join(words) + "\n").encode())

    def _slot_owner(self) -> int:
        try:
            return int((self._slot / OWNER).read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            raise VerdictError(f"verdict slot {self._slot} has no readable owner") from exc

    def previous(self) -> bytes | None:
        """The verdict set aside by begin, or None when there was none."""
        if not self._slot.is_dir() or self._phase()[-1:] != [PREV]:
            return None
        return (self._slot / PREV).read_bytes()

    def _restore(self, prev: bytes | None) -> None:
        if prev is not None:
            self._put(self._path, prev)
        elif os.path.lexists(self._path):
            self.fs.unlink(self._path)
            self.fs.fsync_dir(self._path.parent)

    def _write_receipt(self, digest: str) -> None:
        self._put(self._receipt, receipt_line(digest, self.verdict, self.gate))

    # ── recovery ─────────────────────────────────────────────────────────────

    def _leftovers(self) -> list[Path]:
        """Temporary and trash folders of this verdict whose owner is gone."""
        found = []
        for prefix in (TMP_PREFIX, TRASH_PREFIX):
            for path in self.slot_root.glob(f"{prefix}{self._name}.*"):
                pid = path.name.rsplit(".", 1)[1]
                if path.is_dir() and pid.isdigit() and not self.is_alive(int(pid)):
                    found.append(path)
        return sorted(found)

    def recover(self) -> None:
        """Finish or undo a run left behind by a dead owner (see the module doc)."""
        if not self.slot_root.is_dir():
            return
        for path in self._leftovers():
            self._discard(path)
        if not self._slot.is_dir():
            return
        owner = self._slot_owner()
        if self.is_alive(owner):
            raise SlotBusy(
                f"verdict slot {self._slot} belongs to running pid {owner}: wait for that "
                "gate, or remove the slot by hand if that pid is not a gate"
            )
        phase = self._phase()
        state = phase[0] if phase else ""
        if state == "active":
            self._abort()
        elif state == "aborting":
            self._restore(self.previous())
            self._discard(self._slot)
        elif state == "committed" and len(phase) == 2:
            data = _read_regular(self._path)
            if data is not None and hashlib.sha256(data).hexdigest() == phase[1]:
                self._write_receipt(phase[1])
            self._discard(self._slot)
        elif state == "preparing":
            self._discard(self._slot)
        else:
            raise VerdictError(f"verdict slot {self._slot} has an unknown phase: {phase}")

    # ── the protocol ─────────────────────────────────────────────────────────

    def begin(self) -> None:
        """Set the previous verdict aside. A failure leaves the verdict untouched."""
        self._ensure_dir(self.slot_root)
        self.recover()
        build = self.slot_root / f"{TMP_PREFIX}{self._name}.{self.owner}"
        self.fs.mkdir(build)
        for name, data in ((OWNER, f"{self.owner}\n".encode()), (PHASE, b"preparing\n")):
            self.fs.write(build / name, data)
            self.fs.fsync_file(build / name)
        self.fs.fsync_dir(build)
        try:
            self.fs.rename(build, self._slot)
        except OSError as exc:
            self._discard(build)
            raise SlotBusy(f"verdict slot {self._slot} is taken by another gate") from exc
        self.fs.fsync_dir(self.slot_root)
        try:
            prev = self._set_aside()
        except OSError as exc:
            self._discard(self._slot)
            raise VerdictError(f"could not set {self.verdict} aside: {exc}") from exc
        self._set_phase("active", PREV if prev else "none")
        if prev:
            self.fs.unlink(self._path)
            self.fs.fsync_dir(self._path.parent)

    def _set_aside(self) -> bool:
        if not os.path.lexists(self._path):
            return False
        data = _read_regular(self._path)
        if data is None:
            raise OSError(f"{self.verdict} is not a regular file")
        self.fs.write(self._slot / PREV, data)
        self.fs.fsync_file(self._slot / PREV)
        self.fs.fsync_dir(self._slot)
        return True

    def commit(self) -> str:
        """Validate the new verdict and make it final; returns its token."""
        self._require_active()
        data = _read_regular(self._path)
        if data is None:
            what = "is not a regular file" if os.path.lexists(self._path) else "was not written"
            raise InvalidVerdict(f"{self.verdict} {what} by this run")
        token = checked_token(self.gate, self.verdict, data)
        digest = hashlib.sha256(data).hexdigest()
        self.fs.fsync_file(self._path)
        self.fs.fsync_dir(self._path.parent)
        self._set_phase("committed", digest)  # point of no return
        self._write_receipt(digest)
        self._discard(self._slot)
        return token

    def _require_active(self) -> None:
        if not self._slot.is_dir() or self._slot_owner() != self.owner:
            raise VerdictError(f"verdict slot {self._slot} is not owned by pid {self.owner}")
        if self._phase()[:1] != ["active"]:
            raise VerdictError(f"verdict slot {self._slot} is not active")

    def abort(self) -> Path | None:
        """Restore the previous verdict; returns where a partial output was kept."""
        self._require_active()
        return self._abort()

    def _abort(self) -> Path | None:
        prev = self.previous()
        partial = None
        if self._path.is_dir() and not self._path.is_symlink():
            # Moved aside next to the verdict (same filesystem, atomic), never deleted.
            partial = _unique(self._path.parent / f".{self._name}.rejected-{self.clock()}")
            self.fs.rename(self._path, partial)
            self.fs.fsync_dir(self._path.parent)
        data = _read_regular(self._path)
        if data is not None and data != prev:
            self._ensure_dir(self.partial_root)
            partial = _unique(self.partial_root / f"{self._name}.{self.clock()}")
            self._put(partial, data)
        self._set_phase("aborting", PREV if prev is not None else "none")
        self._restore(prev)
        self._discard(self._slot)
        return partial

    def owns_active(self) -> bool:
        """True while this owner holds the slot in phase `active`."""
        try:
            self._require_active()
        except VerdictError:
            return False
        return True

    def abort_if_owner(self) -> bool:
        """Abort only when this owner holds an active slot (the gate's exit trap)."""
        if not self.owns_active():
            return False
        self._abort()
        return True


def context_copy(data: bytes, directory: Path | None = None) -> Path | None:
    """A read-only temporary copy of the previous verdict for the reviewer's prompt."""
    try:
        fd, name = tempfile.mkstemp(prefix="harness-prev-verdict.", suffix=".md", dir=directory)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(name, 0o444)
    except OSError as exc:
        say("WARN", f"could not copy the previous verdict for the reviewer: {exc}", sys.stderr)
        return None
    return Path(name)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _git_path(name: str) -> Path:
    res = subprocess.run(
        ["git", "rev-parse", "--git-path", name], text=True, capture_output=True, check=False
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise VerdictError(f"git rev-parse --git-path {name} failed: {res.stderr.strip()}")
    return Path(res.stdout.strip()).resolve()


def _slot_for(args: argparse.Namespace) -> Slot:
    return Slot(
        root=Path.cwd(),
        verdict=args.verdict,
        gate=args.gate,
        slot_root=_git_path(SLOT_DIR),
        partial_root=_git_path(PARTIAL_DIR),
        owner=args.owner,
    )


def _say_partial(partial: Path | None) -> None:
    if partial is not None:
        say("WARN", f"partial reviewer output kept in {partial}", sys.stderr)


def _begin(slot: Slot) -> int:
    slot.begin()
    prev = slot.previous()
    if prev is not None:
        copy = context_copy(prev)
        if copy is not None:
            print(copy)
    return 0


def _commit(slot: Slot) -> int:
    try:
        print(slot.commit())
    except InvalidVerdict as exc:
        say("FAIL", f"{exc}: nothing recorded or sealed, previous verdict restored", sys.stderr)
        _say_partial(slot.abort())
        return 1
    return 0


def _abort(slot: Slot) -> int:
    if slot.owns_active():
        _say_partial(slot.abort())
        say("WARN", f"{slot.verdict}: previous verdict restored", sys.stderr)
    return 0


def _receipt(gate: str, item_id: str) -> int:
    """Manual mode, human only: validate a hand-run verdict and write its receipt."""
    root = Path.cwd()
    verdict = verdict_path(gate, item_id)
    data = _read_regular(root / verdict)
    if data is None:
        raise InvalidVerdict(f"{verdict} is missing or not a regular file")
    token = checked_token(gate, verdict, data)
    digest = hashlib.sha256(data).hexdigest()
    put_atomic(
        OsFileSystem(),
        root / receipt_rel(verdict),
        receipt_line(digest, verdict, gate),
        os.getpid(),
    )
    say("OK", f"{verdict} ({token}) -> receipt {receipt_rel(verdict)}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="_verdict.py", description="Verdict handling of a review gate."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("begin", "commit", "abort"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--owner", type=int, required=True)
        cmd.add_argument("gate", choices=sorted(GATE_TOKENS))
        cmd.add_argument("verdict")
    receipt = sub.add_parser("receipt", help="human only: write the receipt of a verdict")
    receipt.add_argument("gate", choices=sorted(GATE_TOKENS))
    receipt.add_argument("id", nargs="?", default="")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "receipt":
            return _receipt(args.gate, args.id)
        slot = _slot_for(args)
        if args.command == "begin":
            return _begin(slot)
        if args.command == "commit":
            return _commit(slot)
        return _abort(slot)
    except VerdictError as exc:
        say("FAIL", str(exc), sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
