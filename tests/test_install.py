"""install.sh must not clobber the user's locale (LANG) for its child processes."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from conftest import TOOLKIT


def test_children_keep_the_callers_locale(tmp_path: Path) -> None:
    # A python3 shim first in PATH records the LANG each child process sees.
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    record = tmp_path / "lang.txt"
    real_python = shutil.which("python3")
    assert real_python
    shim = shim_dir / "python3"
    shim.write_text(f'#!/usr/bin/env bash\necho "$LANG" >> "{record}"\nexec "{real_python}" "$@"\n')
    shim.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "LANG": "C.UTF-8",
        "CLAUDE_HOME": str(tmp_path / "claude-home"),
    }
    res = subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(tmp_path / "proj"), "--lang", "python"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert res.returncode == 0, res.stderr
    seen = set(record.read_text().split())
    assert seen == {"C.UTF-8"}
    assert "python (templates/)" in res.stdout
