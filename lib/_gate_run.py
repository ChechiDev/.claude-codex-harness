"""Code-gate evidence bound to the reviewed tree.

One record per gate attempt: the `make check` result, the tail of its log, HEAD,
a fingerprint of the implementation under review (production code + the subtask's
unit tests only, never docs/verdicts/progress) and a fingerprint of the toolkit.

- `should_skip_review`: a red check in code mode means Codex is not launched. Plan
  mode (red is expected) and the counterfactual re-gate are never skipped.
- `is_stale`: the closure is refused when the reviewed inputs changed since approval.

The functions above the CLI are pure: the caller supplies the files. The CLI
(used by review_with_codex.sh, run from the project root) reads the subtask's
`files:` from the plan, adds every file under tests/unit/ and keeps the records
under .git/harness-gate-runs/ (outside the tree, never committed):

  record <id> <rc> <log> <tag>  record one code-gate attempt (tag: unique per
                           gate run); exit 10 = skip the review
  approve <id> <tag>       bind that attempt to the APPROVED code review
  drop <id>                forget the approval (any other verdict)
  fresh <id>               exit 0 only if the approval still covers the tree
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

Mode = Literal["code", "plan", "regate"]
MODES: tuple[str, ...] = get_args(Mode)

# Paths never part of the implementation fingerprint: writing docs, verdicts or
# progress during the gate must not invalidate an approval.
EXCLUDED_PREFIXES: tuple[str, ...] = ("docs/", "progress/")
# Toolkit dirs whose content shapes a review (scripts + reviewer instructions).
TOOLKIT_DIRS: tuple[str, ...] = ("lib", "codex")
_MISSING = b"\x00<missing>\x00"
SCHEMA = 1


class GateRunError(ValueError):
    """A gate-run record is unreadable or malformed."""


@dataclass(frozen=True, slots=True)
class GateAttempt:
    mode: Mode
    subtask: str
    check_rc: int
    log_tail: str
    head: str
    impl_fp: str
    toolkit_fp: str
    created_at: str

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"invalid gate mode: {self.mode!r}")


# ── fingerprints ────────────────────────────────────────────────────────────


def _is_excluded(rel: str) -> bool:
    return any(rel.startswith(p) for p in EXCLUDED_PREFIXES)


def _safe_rel(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes the project root: {rel!r}")
    return path


def implementation_fingerprint(root: Path, files: list[str]) -> str:
    """sha256 over (path, content) of the subtask's code + tests; deletions count."""
    h = hashlib.sha256()
    for rel in sorted({f.strip() for f in files if f.strip()}):
        path = _safe_rel(root, rel)
        if _is_excluded(rel):
            continue
        if path.is_file():
            mode = b"x" if os.access(path, os.X_OK) else b"-"
            data = mode + hashlib.sha256(path.read_bytes()).digest()
        else:
            data = _MISSING
        h.update(rel.encode() + b"\x00" + data)
    return h.hexdigest()


def toolkit_fingerprint(harness_dir: Path) -> str:
    """sha256 over every regular file under the toolkit's lib/ and codex/."""
    h = hashlib.sha256()
    for top in TOOLKIT_DIRS:
        base = harness_dir / top
        paths = sorted(p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        for path in paths:
            rel = path.relative_to(harness_dir).as_posix()
            h.update(rel.encode() + b"\x00" + hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def tail(log: str, lines: int = 80, max_chars: int = 8000) -> str:
    out = "\n".join(log.splitlines()[-lines:])
    return out[-max_chars:]


# ── decisions ───────────────────────────────────────────────────────────────


def should_skip_review(attempt: GateAttempt) -> bool:
    """Code mode with a red check: do not launch Codex."""
    return attempt.mode == "code" and attempt.check_rc != 0


def is_stale(approved: GateAttempt, current: GateAttempt) -> bool:
    """True when the approval no longer covers the current tree.

    Only a green code-mode approval can authorise a code closure. HEAD alone is not
    compared: the closure commit moves it without touching code.
    """
    return (
        approved.mode != "code"
        or current.mode != "code"
        or approved.check_rc != 0
        or approved.subtask != current.subtask
        or approved.impl_fp != current.impl_fp
        or approved.toolkit_fp != current.toolkit_fp
    )


# ── persistence ─────────────────────────────────────────────────────────────


def record_attempt(path: Path, attempt: GateAttempt) -> None:
    """Atomic write: temp file in the same dir, fsync, os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"schema": SCHEMA, **asdict(attempt)}, indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


_TYPES: dict[str, type] = {
    f.name: (int if f.name == "check_rc" else str) for f in fields(GateAttempt)
}


def load_attempt(path: Path) -> GateAttempt:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateRunError(f"unreadable gate-run record {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise GateRunError(f"bad gate-run record {path}: wrong shape or schema")
    values: dict[str, object] = {}
    for name, typ in _TYPES.items():
        val = raw.get(name)
        if type(val) is not typ:
            raise GateRunError(f"bad gate-run record {path}: field {name!r}")
        values[name] = val
    try:
        return GateAttempt(**values)  # type: ignore[arg-type]
    except ValueError as exc:
        raise GateRunError(f"bad gate-run record {path}: {exc}") from exc


# ── CLI ─────────────────────────────────────────────────────────────────────

UNIT_TESTS = "tests/unit"
SKIP_RC = 10
TOOLKIT_ROOT = Path(__file__).resolve().parent.parent


def unit_test_files(root: Path) -> list[str]:
    base = root / UNIT_TESTS
    found = (p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return sorted(p.relative_to(root).as_posix() for p in found)


def reviewed_files(root: Path, declared: list[str]) -> list[str]:
    """The implementation under review: the subtask's `files:` plus its unit tests."""
    return sorted(set(declared) | set(unit_test_files(root)))


def _records_dir() -> Path:
    res = subprocess.run(
        ["git", "rev-parse", "--git-path", "harness-gate-runs"],
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise GateRunError(f"git rev-parse --git-path failed: {res.stderr.strip()}")
    return Path(res.stdout.strip()).resolve()


def _path(kind: str, sid: str) -> Path:
    if "/" in sid or sid.startswith("."):
        raise GateRunError(f"bad subtask id: {sid!r}")
    return _records_dir() / f"{kind}_{sid}.json"


def _declared(sid: str) -> list[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _harness

    files = _harness.leaf_files(sid)
    if files is None:
        raise GateRunError(f"subtask {sid} does not exist in docs/plan/")
    return files


def _head() -> str:
    res = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def current_attempt(sid: str, rc: int, log_tail: str) -> GateAttempt:
    root = Path.cwd()
    return GateAttempt(
        mode="code",
        subtask=sid,
        check_rc=rc,
        log_tail=log_tail,
        head=_head(),
        impl_fp=implementation_fingerprint(root, reviewed_files(root, _declared(sid))),
        toolkit_fp=toolkit_fingerprint(TOOLKIT_ROOT),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def _attempt_path(sid: str, tag: str) -> Path:
    if not tag.isdigit():
        raise GateRunError(f"bad attempt tag: {tag!r}")
    return _path(f"code.{tag}", sid)


def _cmd_record(sid: str, rc: str, log: str, tag: str) -> int:
    text = Path(log).read_text(encoding="utf-8", errors="replace")
    attempt = current_attempt(sid, int(rc), tail(text))
    record_attempt(_attempt_path(sid, tag), attempt)
    if should_skip_review(attempt):
        print(attempt.log_tail, file=sys.stderr)
        return SKIP_RC
    return 0


def _cmd_approve(sid: str, tag: str) -> int:
    """Bind this run's own attempt (never another run's) to an APPROVED review."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _harness

    if _harness.read_verdict(Path(f"progress/review_{sid}.md")) != "APPROVED":
        raise GateRunError(f"progress/review_{sid}.md is not APPROVED: nothing to bind")
    src = _attempt_path(sid, tag)
    record_attempt(_path("code.approved", sid), load_attempt(src))
    src.unlink(missing_ok=True)
    return 0


def _cmd_drop(sid: str) -> int:
    _path("code.approved", sid).unlink(missing_ok=True)
    return 0


def _cmd_fresh(sid: str) -> int:
    path = _path("code.approved", sid)
    if not path.is_file():
        print(
            f"[FAIL] {sid}: no record of an approved code gate ({path}): run the code gate again",
            file=sys.stderr,
        )
        return 1
    approved = load_attempt(path)
    if is_stale(approved, current_attempt(sid, 0, "")):
        print(
            f"[FAIL] {sid}: the code approval is stale (the implementation, its unit tests "
            "or the toolkit changed since): run the code gate again",
            file=sys.stderr,
        )
        return 1
    return 0


_COMMANDS: dict[str, tuple[Callable[..., int], int]] = {
    "record": (_cmd_record, 4),
    "approve": (_cmd_approve, 2),
    "drop": (_cmd_drop, 1),
    "fresh": (_cmd_fresh, 1),
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in _COMMANDS or len(argv) - 1 != _COMMANDS[argv[0]][1]:
        print(
            "usage: _gate_run.py record <id> <rc> <log> <tag> | approve <id> <tag>"
            " | drop|fresh <id>",
            file=sys.stderr,
        )
        return 2
    func, _ = _COMMANDS[argv[0]]
    try:
        return func(*argv[1:])
    except (GateRunError, ValueError, OSError) as exc:
        print(f"[FAIL] gate run: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
