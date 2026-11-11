"""I2 (A/B): the verdict slot protocol of lib/_verdict.py, driven in-process.

The filesystem is a port: a recording adapter logs every primitive operation, can
simulate a crash before any of them, and checks the durability order (every
destructive step comes after the directory fsync that protects the state before it).
Real power loss is not simulated.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path

import _verdict
import pytest
from _verdict import GATE_TOKENS, InvalidVerdict, OsFileSystem, Slot, SlotBusy

VERDICT = "progress/review_1.1.1.md"
RECEIPT = "progress/verdict_review_1.1.1.md.sha256"
PREV = b"# Review\n\n**Verdict:** CHANGES_REQUESTED\n"
NEW = b"# Review\n\n**Verdict:** APPROVED\n"
PARTIAL = b"# Review\n\nhalf written"
ME = 1000
OTHER = 2000


class Crash(BaseException):
    """A simulated crash: no `except Exception` in the code under test catches it."""


@dataclass
class RecordingFs(OsFileSystem):
    """Logs each primitive, crashes before op number `crash_at`, checks fsync order."""

    crash_at: int = -1
    ops: list[tuple[str, Path]] = field(default_factory=list)
    writes: dict[Path, bytes] = field(default_factory=dict)
    renamed: dict[int, bytes] = field(default_factory=dict)  # op index -> bytes moved
    dirty: dict[Path, set[Path]] = field(default_factory=dict)
    unsynced: set[Path] = field(default_factory=set)  # written, contents not fsynced
    violations: list[str] = field(default_factory=list)

    def _step(self, name: str, path: Path) -> None:
        if len(self.ops) == self.crash_at:
            raise Crash(f"{name} {path}")
        self.ops.append((name, path))

    def _mark(self, path: Path) -> None:
        self.dirty.setdefault(path.parent, set()).add(path)

    def _destructive(self, what: str, allowed: set[Path]) -> None:
        # Entries inside a trash folder are moot: the folder is already unreachable.
        pending = {
            p
            for folder, paths in self.dirty.items()
            if not folder.name.startswith(_verdict.TRASH_PREFIX)
            for p in paths
        } - allowed
        if pending:
            self.violations.append(f"{what} before fsync of {sorted(map(str, pending))}")

    def mkdir(self, path: Path) -> None:
        self._step("mkdir", path)
        super().mkdir(path)
        self._mark(path)

    def write(self, path: Path, data: bytes) -> None:
        self._step("write", path)
        super().write(path, data)
        self.writes[path] = data
        self.unsynced.add(path)
        self._mark(path)

    def rename(self, src: Path, dst: Path) -> None:
        self._step("rename", dst)
        lost = sorted(str(p) for p in self.unsynced if p == src or src in p.parents)
        if lost:
            self.violations.append(f"rename {src} before fsync of contents {lost}")
        if os.path.lexists(dst):
            self._destructive(f"rename over {dst}", {src})
        elif src.is_dir() and not src.name.startswith(_verdict.TMP_PREFIX):
            # Moving a live folder away (the slot into trash) drops its state.
            self._destructive(f"move away {src}", set())
        super().rename(src, dst)
        self.writes[dst] = self.writes.pop(src, b"")
        self.renamed[len(self.ops) - 1] = self.writes[dst]
        self._mark(src)
        self._mark(dst)

    def unlink(self, path: Path) -> None:
        self._step("unlink", path)
        self._destructive(f"unlink {path}", set())
        super().unlink(path)
        self._mark(path)

    def rmdir(self, path: Path) -> None:
        self._step("rmdir", path)
        self._destructive(f"rmdir {path}", set())
        super().rmdir(path)
        self.dirty.pop(path, None)  # entries of a removed directory need no fsync
        self._mark(path)

    def fsync_dir(self, path: Path) -> None:
        self._step("fsync_dir", path)
        super().fsync_dir(path)
        self.dirty.pop(path, None)

    def fsync_file(self, path: Path) -> None:
        self._step("fsync_file", path)
        super().fsync_file(path)
        self.unsynced.discard(path)

    def verdict_durable_before_commit(self, verdict: Path) -> bool:
        """The verdict file, then its folder, are fsynced before `phase=committed`."""
        point = self.commit_point()
        synced = [i for i, op in enumerate(self.ops[:point]) if op == ("fsync_file", verdict)]
        if not synced:
            return False
        later = self.ops[synced[-1] : point]
        return ("fsync_dir", verdict.parent) in later

    def commit_point(self) -> int:
        """Index of the rename that makes `phase=committed` live."""
        for i, (name, path) in enumerate(self.ops):
            if (
                name == "rename"
                and path.name == "phase"
                and self.renamed.get(i, b"").startswith(b"committed")
            ):
                return i
        raise AssertionError("no committed phase in this run")


@dataclass
class Env:
    root: Path
    slot_root: Path
    partial_root: Path

    def slot(
        self,
        fs: OsFileSystem | None = None,
        owner: int = ME,
        alive: Callable[[int], bool] = lambda pid: pid == ME,
        gate: str = "code",
    ) -> Slot:
        return Slot(
            root=self.root,
            verdict=VERDICT,
            gate=gate,
            slot_root=self.slot_root,
            partial_root=self.partial_root,
            owner=owner,
            fs=fs or OsFileSystem(),
            is_alive=alive,
            clock=lambda: "20261110T000000",
        )

    def write_verdict(self, data: bytes) -> None:
        path = self.root / VERDICT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def verdict(self) -> bytes | None:
        path = self.root / VERDICT
        return path.read_bytes() if path.exists() else None

    def receipt(self) -> str | None:
        path = self.root / RECEIPT
        return path.read_text() if path.exists() else None

    def leftovers(self) -> list[str]:
        if not self.slot_root.exists():
            return []
        return sorted(p.name for p in self.slot_root.iterdir())


def receipt_of(data: bytes, gate: str = "code") -> str:
    return f"{hashlib.sha256(data).hexdigest()}  {VERDICT}  {gate}\n"


@pytest.fixture
def env(tmp_path: Path) -> Env:
    root = tmp_path / "proj"
    (root / "progress").mkdir(parents=True)
    git = tmp_path / "git"
    return Env(root, git / "harness-verdict-slot", git / "harness-verdict-partial")


# ── B: validation ────────────────────────────────────────────────────────────


def test_gate_tokens_cover_every_review_gate() -> None:
    assert GATE_TOKENS == {
        "roadmap": "ROADMAP_APPROVED",
        "phase": "PHASE_APPROVED",
        "issue": "ISSUE_APPROVED",
        "issue-draft": "DRAFT_APPROVED",
        "plan": "PLAN_APPROVED",
        "code": "APPROVED",
    }


@pytest.mark.parametrize("gate", sorted(GATE_TOKENS))
@pytest.mark.parametrize("token", ["approval", "CHANGES_REQUESTED"])
def test_commit_accepts_the_gate_tokens(env: Env, gate: str, token: str) -> None:
    accepted = GATE_TOKENS[gate] if token == "approval" else token
    slot = env.slot(gate=gate)
    slot.begin()
    data = f"# Review\n\n**Verdict:** {accepted}\n".encode()
    env.write_verdict(data)
    assert slot.commit() == accepted
    assert env.receipt() == receipt_of(data, gate)
    assert env.leftovers() == []


def _other_token(gate: str) -> str:
    return next(t for g, t in GATE_TOKENS.items() if g != gate and t != GATE_TOKENS[gate])


@pytest.mark.parametrize("gate", sorted(GATE_TOKENS))
@pytest.mark.parametrize("case", ["missing", "no-token", "other-token", "symlink", "directory"])
def test_commit_rejects_an_invalid_verdict(env: Env, gate: str, case: str, tmp_path: Path) -> None:
    env.write_verdict(PREV)
    slot = env.slot(gate=gate)
    slot.begin()
    path = env.root / VERDICT
    if case == "no-token":
        path.write_bytes(b"# Review\n\nlooks fine\n")
    elif case == "other-token":
        path.write_text(f"# Review\n\n**Verdict:** {_other_token(gate)}\n")
    elif case == "symlink":
        real = tmp_path / "real.md"
        real.write_text(f"# Review\n\n**Verdict:** {GATE_TOKENS[gate]}\n")
        path.symlink_to(real)
    elif case == "directory":
        path.mkdir()
    with pytest.raises(InvalidVerdict):
        slot.commit()
    kept = slot.abort()
    assert env.verdict() == PREV
    if case == "directory":
        assert kept is not None and kept.is_dir()
        assert kept.parent == path.parent
    assert env.receipt() is None
    assert env.leftovers() == []


# ── A: begin / commit / abort ────────────────────────────────────────────────


def test_begin_sets_the_previous_verdict_aside(env: Env) -> None:
    env.write_verdict(PREV)
    slot = env.slot()
    slot.begin()
    assert env.verdict() is None
    assert slot.previous() == PREV


def test_begin_without_previous_verdict(env: Env) -> None:
    slot = env.slot()
    slot.begin()
    assert slot.previous() is None
    env.write_verdict(NEW)
    assert slot.commit() == "APPROVED"
    assert env.receipt() == receipt_of(NEW)


@pytest.mark.parametrize("prev", [PREV, None])
def test_abort_restores_the_previous_verdict_and_keeps_the_partial(
    env: Env, prev: bytes | None
) -> None:
    if prev is not None:
        env.write_verdict(prev)
    slot = env.slot()
    slot.begin()
    env.write_verdict(PARTIAL)
    partial = slot.abort()
    assert env.verdict() == prev
    assert partial is not None
    assert partial.parent == env.partial_root
    assert partial.name == "review_1.1.1.md.20261110T000000"
    assert partial.read_bytes() == PARTIAL
    assert env.leftovers() == []


def test_two_partials_in_the_same_second_both_survive(env: Env) -> None:
    kept = []
    for data in (b"first\n", b"second\n"):
        slot = env.slot()
        slot.begin()
        env.write_verdict(data)
        kept.append(slot.abort())
    assert kept[0] is not None and kept[1] is not None
    assert kept[0] != kept[1]
    assert kept[0].read_bytes() == b"first\n"
    assert kept[1].read_bytes() == b"second\n"


@pytest.mark.parametrize("prev", [PREV, None])
def test_recovery_restores_past_a_directory_left_by_a_dead_reviewer(
    env: Env, prev: bytes | None
) -> None:
    if prev is not None:
        env.write_verdict(prev)
    env.slot().begin()
    (env.root / VERDICT).mkdir()
    env.slot(owner=OTHER, alive=lambda pid: False).recover()
    assert env.verdict() == prev
    assert env.leftovers() == []
    aside = [p for p in (env.root / "progress").iterdir() if p.is_dir()]
    assert len(aside) == 1


def test_abort_keeps_no_partial_when_nothing_new_was_written(env: Env) -> None:
    env.write_verdict(PREV)
    slot = env.slot()
    slot.begin()
    env.write_verdict(PREV)
    assert slot.abort() is None
    assert env.verdict() == PREV


def test_a_live_owner_blocks_a_second_gate(env: Env) -> None:
    env.write_verdict(PREV)
    env.slot().begin()
    env.write_verdict(PARTIAL)
    with pytest.raises(SlotBusy, match="harness-verdict-slot"):
        env.slot(owner=OTHER, alive=lambda pid: True).begin()
    assert env.verdict() == PARTIAL


def test_abort_if_owner_only_acts_for_its_owner_while_active(env: Env) -> None:
    env.write_verdict(PREV)
    env.slot().begin()
    env.write_verdict(PARTIAL)
    assert env.slot(owner=OTHER).abort_if_owner() is False
    assert env.verdict() == PARTIAL
    assert env.slot().abort_if_owner() is True
    assert env.verdict() == PREV
    assert env.slot().abort_if_owner() is False


def test_abort_if_owner_never_undoes_a_commit(env: Env) -> None:
    slot = env.slot()
    slot.begin()
    env.write_verdict(NEW)
    slot.commit()
    assert slot.abort_if_owner() is False
    assert env.verdict() == NEW
    assert env.receipt() == receipt_of(NEW)


def test_a_failure_before_the_delete_leaves_the_verdict_untouched(env: Env) -> None:
    env.write_verdict(PREV)
    fs = RecordingFs()
    with pytest.raises(Crash):
        env.slot(fs=RecordingFs(crash_at=1)).begin()
    env.slot(fs=fs, owner=OTHER, alive=lambda pid: False).recover()
    assert env.verdict() == PREV
    assert env.leftovers() == []


# ── A: crash at every transition, then recovery ──────────────────────────────

Run = Callable[[Env, Slot], None]


def _success(env: Env, slot: Slot) -> None:
    slot.begin()
    env.write_verdict(NEW)
    slot.commit()


def _identical(env: Env, slot: Slot) -> None:
    slot.begin()
    env.write_verdict(PREV)
    slot.commit()


def _failure(env: Env, slot: Slot) -> None:
    slot.begin()
    env.write_verdict(PARTIAL)
    slot.abort()


def _setup(env: Env, prev: bytes | None, stale_receipt: bool) -> None:
    if prev is not None:
        env.write_verdict(prev)
    if stale_receipt:
        (env.root / RECEIPT).write_text(receipt_of(PREV))


def _count_ops(env: Env, run: Run, prev: bytes | None, stale: bool, tmp: Path) -> RecordingFs:
    probe = Env(tmp / "probe", tmp / "probe-git" / "slot", tmp / "probe-git" / "partial")
    (probe.root / "progress").mkdir(parents=True)
    _setup(probe, prev, stale)
    fs = RecordingFs()
    run(probe, probe.slot(fs=fs))
    return fs


CASES = [
    pytest.param(_success, PREV, False, id="success-with-prev"),
    pytest.param(_success, None, False, id="success-without-prev"),
    pytest.param(_identical, PREV, True, id="identical-with-stale-receipt"),
    pytest.param(_failure, PREV, False, id="failure-with-prev"),
    pytest.param(_failure, None, False, id="failure-without-prev"),
]


@pytest.mark.parametrize(("run", "prev", "stale"), CASES)
def test_the_protocol_keeps_fsync_order(
    env: Env, tmp_path: Path, run: Run, prev: bytes | None, stale: bool
) -> None:
    fs = _count_ops(env, run, prev, stale, tmp_path)
    assert fs.violations == []
    assert not any(fs.dirty.values()), fs.dirty


@pytest.mark.parametrize(("run", "prev", "stale"), CASES[:3])
def test_commit_makes_the_verdict_durable_before_the_point_of_no_return(
    env: Env, tmp_path: Path, run: Run, prev: bytes | None, stale: bool
) -> None:
    fs = _count_ops(env, run, prev, stale, tmp_path)
    assert fs.verdict_durable_before_commit(tmp_path / "probe" / VERDICT)


@dataclass
class NoSlotRootSync(RecordingFs):
    """A broken protocol: never fsyncs the slot root."""

    slot_root: Path = Path("/nonexistent")

    def fsync_dir(self, path: Path) -> None:
        if path != self.slot_root:
            super().fsync_dir(path)


@dataclass
class NoFileSync(RecordingFs):
    """A broken protocol: never fsyncs a file."""

    def fsync_file(self, path: Path) -> None:
        return None


def test_the_checker_catches_a_missing_slot_root_fsync(env: Env) -> None:
    env.write_verdict(PREV)
    fs = NoSlotRootSync(slot_root=env.slot_root)
    _success(env, env.slot(fs=fs))
    assert any(str(env.slot_root) in v for v in fs.violations), fs.violations


def test_the_checker_catches_unsynced_file_contents(env: Env) -> None:
    env.write_verdict(PREV)
    fs = NoFileSync()
    _success(env, env.slot(fs=fs))
    assert any("before fsync of contents" in v for v in fs.violations), fs.violations


class LateDiscardSlot(Slot):
    """A broken protocol: empties the trash before fsyncing the slot root."""

    def _discard(self, folder: Path) -> None:
        trash = folder.parent / f"{_verdict.TRASH_PREFIX}{folder.name}.{self.owner}"
        self.fs.rename(folder, trash)
        for child in sorted(trash.iterdir()):
            self.fs.unlink(child)
        self.fs.rmdir(trash)
        self.fs.fsync_dir(folder.parent)


def test_the_checker_catches_emptying_the_trash_before_the_root_fsync(env: Env) -> None:
    env.write_verdict(PREV)
    fs = RecordingFs()
    base = env.slot(fs=fs)
    slot = LateDiscardSlot(**{f.name: getattr(base, f.name) for f in fields(base)})
    _success(env, slot)
    assert any(".trash-" in v for v in fs.violations), fs.violations


def test_the_checker_catches_a_missing_verdict_fsync(env: Env) -> None:
    env.write_verdict(PREV)
    fs = NoFileSync()
    _success(env, env.slot(fs=fs))
    assert not fs.verdict_durable_before_commit(env.root / VERDICT)


@pytest.mark.parametrize(("run", "prev", "stale"), CASES)
def test_recovery_after_a_crash_at_every_step(
    env: Env, tmp_path: Path, run: Run, prev: bytes | None, stale: bool
) -> None:
    probe = _count_ops(env, run, prev, stale, tmp_path)
    committed = probe.commit_point() if run is not _failure else len(probe.ops)
    new = PREV if run is _identical else NEW
    for k in range(len(probe.ops)):
        case = Env(tmp_path / f"k{k}", tmp_path / f"g{k}" / "slot", tmp_path / f"g{k}" / "partial")
        (case.root / "progress").mkdir(parents=True)
        _setup(case, prev, stale)
        with pytest.raises(Crash):
            run(case, case.slot(fs=RecordingFs(crash_at=k)))
        fs = RecordingFs()
        case.slot(fs=fs, owner=OTHER, alive=lambda pid: False).recover()
        where = f"crash before op {k}: {probe.ops[k]}"
        assert case.leftovers() == [], where
        assert fs.violations == [], where
        if k > committed:
            assert case.verdict() == new, where
            assert case.receipt() == receipt_of(new), where
        else:
            assert case.verdict() == prev, where
            assert case.receipt() == (receipt_of(PREV) if stale else None), where


# ── previous verdict as reviewer context ─────────────────────────────────────


def test_context_copy_is_a_readable_copy(tmp_path: Path) -> None:
    path = _verdict.context_copy(PREV, tmp_path)
    assert path is not None
    assert path.parent == tmp_path
    assert path.read_bytes() == PREV


def test_context_copy_failure_warns_and_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert _verdict.context_copy(PREV, locked) is None
    finally:
        locked.chmod(0o700)
    assert "WARN" in capsys.readouterr().err


# ── .git on another filesystem ───────────────────────────────────────────────

SHM = Path("/dev/shm")


@pytest.mark.skipif(not SHM.is_dir(), reason="no /dev/shm")
@pytest.mark.parametrize("run", [_success, _failure])
def test_slot_on_another_filesystem(env: Env, tmp_path: Path, run: Run) -> None:
    import tempfile

    with tempfile.TemporaryDirectory(dir=SHM) as shm:
        if os.stat(shm).st_dev == os.stat(tmp_path).st_dev:
            pytest.skip("/dev/shm is on the same filesystem")
        other = Env(env.root, Path(shm) / "slot", Path(shm) / "partial")
        other.write_verdict(PREV)
        run(other, other.slot())
        assert other.verdict() == (NEW if run is _success else PREV)
        assert other.leftovers() == []
