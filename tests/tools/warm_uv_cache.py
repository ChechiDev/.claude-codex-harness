"""Warm the uv cache for the e2e tier's throwaway projects (#117, #123).

    uv run python tests/tools/warm_uv_cache.py [scratch-dir]

The installed-project fixtures (tests/i0_projects.py) sync with UV_OFFLINE=1 from
`uv cache dir`, in isolated_env: every UV_* variable dropped, a throwaway HOME. Which
interpreter uv picks there (so which wheels it wants) depends on that environment, so
the warm-up reuses it: one online `install.sh --python` with isolated_env minus
UV_OFFLINE, then the same install offline, exactly as the fixtures run it. A cache
that misses a wheel fails here, once, instead of erroring every installed-project test.
Run it under `uv run` so PATH matches the pytest run; the scratch dir defaults to a
fresh system temp dir, where the fixtures build their templates too.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from conftest import TOOLKIT  # noqa: E402
from i0_projects import isolated_env  # noqa: E402


def warm_env(base: Path) -> dict[str, str]:
    """The fixtures' environment for `base`, with the network on."""
    env = isolated_env(base)
    env.pop("UV_OFFLINE")
    return env


def install(base: Path, offline: bool) -> subprocess.CompletedProcess[str]:
    """Run the real install.sh into `base`/py (`base` must exist and be empty)."""
    env = isolated_env(base) if offline else warm_env(base)
    return subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(base / "py"), "--python", "demo"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )


def main(argv: Sequence[str]) -> int:
    if len(argv) > 1:
        print("usage: warm_uv_cache.py [scratch-dir]", file=sys.stderr)
        return 2
    scratch = Path(argv[0]) if argv else Path(tempfile.mkdtemp(prefix="harness-warm-"))
    for name, offline in (("online", False), ("offline", True)):
        base = scratch / name
        base.mkdir(parents=True, exist_ok=False)
        res = install(base, offline)
        print(f"== install.sh --python ({name})\n{res.stdout}", end="")
        if res.returncode != 0:
            print(f"install.sh failed ({name}):\n{res.stderr}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
