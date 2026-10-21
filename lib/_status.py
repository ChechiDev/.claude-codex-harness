"""Status tags shared by the harness Python scripts (shell counterpart: _colors.sh).

[OK] green · [SKIP] orange · [WARN] yellow · [FAIL] red. Colors only when the
stream is a terminal (or FORCE_COLOR is set, for output captured to be replayed
on one) and never with NO_COLOR (https://no-color.org), so output captured by
hooks, Claude or CI stays plain.
"""

from __future__ import annotations

import os
import sys
from typing import Literal, TextIO

Kind = Literal["OK", "SKIP", "WARN", "FAIL"]

_COLORS: dict[str, str] = {
    "OK": "\033[0;32m",
    "SKIP": "\033[38;5;208m",
    "WARN": "\033[0;33m",
    "FAIL": "\033[0;31m",
}
_RESET = "\033[0m"
_WIDTH = 8  # "[FAIL]  " — messages line up whatever the tag


def paint(text: str, kind: Kind | None, stream: TextIO) -> str:
    """`text` in the color of `kind` (None: plain), honoring tty / FORCE_COLOR / NO_COLOR."""
    wants = stream.isatty() or bool(os.environ.get("FORCE_COLOR"))
    if kind is None or not wants or os.environ.get("NO_COLOR"):
        return text
    return f"{_COLORS[kind]}{text}{_RESET}"


def tag(kind: Kind, stream: TextIO) -> str:
    label = f"[{kind}]"
    return paint(label, kind, stream) + " " * (_WIDTH - len(label))


def say(kind: Kind, message: str, stream: TextIO | None = None) -> None:
    out = stream if stream is not None else sys.stdout
    print(f"{tag(kind, out)}{message}", file=out)
