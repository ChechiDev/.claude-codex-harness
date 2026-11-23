"""Run pytest with the suite_timing plugin loaded (#117).

    uv run python tests/tools/pytest_timed.py --timing-dir=DIR [pytest args...]

Running this file puts tests/tools first on sys.path, so `-p suite_timing` imports
without exporting PYTHONPATH: an exported one would leak into every subprocess the
tests start (the harness's run_check refuses it). xdist workers started with popen
inherit the controller's sys.path.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

TOOLS = str(Path(__file__).resolve().parent)


def main(argv: Sequence[str]) -> int:
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    return int(pytest.main(["-p", "suite_timing", *argv]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
