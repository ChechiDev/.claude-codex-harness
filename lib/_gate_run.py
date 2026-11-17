"""Code-gate evidence bound to the reviewed tree.

One record per gate attempt: the `make check` result, the tail of its log, HEAD,
a fingerprint of the implementation under review (production code + the subtask's
unit tests only, never docs/verdicts/progress) and a fingerprint of the toolkit.

- `should_skip_review`: a red check in code mode means Codex is not launched. Plan
  mode (red is expected) and the counterfactual re-gate are never skipped.
- `is_stale`: the closure is refused when the reviewed inputs changed since approval.

Pure module: the caller supplies the subtask's declared `files:`; nothing here
parses the plan. Not yet wired into review_with_codex.sh / _verdict.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
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
