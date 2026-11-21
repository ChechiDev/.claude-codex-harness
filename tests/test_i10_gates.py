"""I10: gate.sh and doc_gate.sh fail when they cannot trust what their helper says.

The I0 legacy test covers a broken feature_list.json. Here, from a throwaway copy of
the toolkit (the real one is never changed): the helper crashing on start, the per-leaf
`verdict` helper failing after printing APPROVED, a script unable to enter the project;
and the control that a project with no done leaf stays OK.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT
from i0_legacy import build_legacy
from i0_projects import Installed, install_project

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

SCRIPTS = {"gate.sh": "gates consistent", "doc_gate.sh": "reference docs complete"}
SKIP = shutil.ignore_patterns(".git", ".venv", "__pycache__", "*_cache", "scratchpad")
FUTURE = "from __future__ import annotations\n"


def _gate(project: Installed, script: str, **env: str) -> tuple[int, str, str]:
    res = subprocess.run(
        ["bash", str(project.root / "harness" / script)],
        cwd=project.root,
        env={**project.env, **env},
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    return res.returncode, res.stdout, res.stderr


def _toolkit_copy(tmp: Path) -> Path:
    copy = tmp / "toolkit"
    shutil.copytree(TOOLKIT, copy, symlinks=True, ignore=SKIP)
    return copy


def _break_helper(toolkit: Path, code: str) -> None:
    """Insert `code` into the copy's _harness.py right after its __future__ import."""
    helper = toolkit / "lib/_harness.py"
    text = helper.read_text()
    assert text.count(FUTURE) == 1
    helper.write_text(text.replace(FUTURE, FUTURE + code))


@pytest.fixture(scope="module")
def fresh(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    return install_project(tmp_path_factory.mktemp("fresh"), "python")


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_no_done_leaf_is_still_ok(fresh: Installed, script: str) -> None:
    code, out, err = _gate(fresh, script)
    assert code == 0, out + err
    assert SCRIPTS[script] in out


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_a_crashing_helper_fails_the_gate(tmp_path: Path, script: str) -> None:
    toolkit = _toolkit_copy(tmp_path)
    project = install_project(tmp_path, "python", toolkit=toolkit)
    _break_helper(toolkit, 'raise ImportError("helper broken")\n')
    code, out, err = _gate(project, script)
    assert code == 1, out + err
    assert "[FAIL]" in out + err
    assert "helper broken" in out + err, "the cause is shown"
    assert SCRIPTS[script] not in out


def test_a_failing_verdict_helper_fails_the_gate(tmp_path: Path) -> None:
    """`verdict` prints APPROVED and then fails: the review is not trusted."""
    toolkit = _toolkit_copy(tmp_path)
    project = build_legacy(tmp_path, toolkit=toolkit)  # one done leaf, review APPROVED
    assert _gate(project, "gate.sh")[0] == 0, "control: green before the helper breaks"
    _break_helper(
        toolkit,
        'import sys as _sys\nif _sys.argv[1:2] == ["verdict"]:\n'
        '    print("APPROVED")\n    raise SystemExit(3)\n',
    )
    code, out, err = _gate(project, "gate.sh")
    assert code == 1, out + err
    assert "[FAIL]" in out + err
    assert SCRIPTS["gate.sh"] not in out


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_a_gate_fails_when_it_cannot_enter_the_project(
    fresh: Installed, tmp_path: Path, script: str
) -> None:
    code, out, err = _gate(fresh, script, CLAUDE_PROJECT_DIR=str(tmp_path / "missing"))
    assert code == 1, out + err
    assert "[FAIL]" in out + err
    assert SCRIPTS[script] not in out


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_a_healthy_project_needs_no_temp_dir(fresh: Installed, tmp_path: Path, script: str) -> None:
    """No temp file on the way: an unwritable TMPDIR must not turn a healthy gate red."""
    code, out, err = _gate(fresh, script, TMPDIR=str(tmp_path / "no-such-tmp"))
    assert code == 0, out + err
    assert SCRIPTS[script] in out


def test_an_unreadable_reference_doc_fails_the_doc_gate(tmp_path: Path) -> None:
    project = build_legacy(tmp_path)
    assert _gate(project, "doc_gate.sh")[0] == 0, "control: green while readable"
    doc = next((project.root / "docs/reference").glob("1.1.1-*.md"))
    doc.chmod(0)
    try:
        code, out, err = _gate(project, "doc_gate.sh")
    finally:
        doc.chmod(0o644)
    assert code == 1, out + err
    assert f"cannot read {doc.relative_to(project.root)}" in out + err
    assert SCRIPTS["doc_gate.sh"] not in out
