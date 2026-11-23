"""Suite tiers (#68): the default run is the fast tier; e2e tests are marked and guarded.

The guard (conftest.py) refuses a real install.sh or a real `make check` from a test
that is not marked e2e, so a heavy test cannot drift into the fast tier unnoticed.
"""

from __future__ import annotations

import subprocess
import tomllib

import pytest
from conftest import TOOLKIT, UnmarkedE2EError, e2e_reason


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", "/x/install.sh", "/tmp/p", "--python", "demo"],
        ["bash", "install.sh"],
        ["make", "check"],
        ["make", "-s", "--no-print-directory", "check"],
        ["/usr/bin/make", "-C", "proj", "check"],
        ["make", "-C/tmp/my-project-name", "check"],
        ["make", "-fother-name.mk", "check"],
        "make check",
        "cd proj && make -s check",
        [b"make", b"check"],
        [b"bash", b"install.sh"],
    ],
)
def test_heavy_commands_are_e2e(argv: list[str] | list[bytes] | str) -> None:
    assert e2e_reason(argv) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["make", "-n", "check"],
        ["make", "--dry-run", "check"],
        ["make", "-pn"],
        ["make", "-s", "plan"],
        ["git", "commit", "-m", "make check"],
        ["make", "-sn", "check"],
        ["python3", "lib/_harness.py", "validate"],
        "make -n check",
        "echo 'unbalanced",
    ],
)
def test_light_commands_are_not_e2e(argv: list[str] | str) -> None:
    assert e2e_reason(argv) is None


def test_unmarked_test_cannot_run_install() -> None:
    with pytest.raises(UnmarkedE2EError, match="mark it @pytest.mark.e2e"):
        subprocess.run(["true", "install.sh"], check=False)


def test_unmarked_test_cannot_run_make_check() -> None:
    with pytest.raises(UnmarkedE2EError):
        subprocess.Popen(["make", "check"], cwd="/nonexistent")


@pytest.mark.e2e
def test_marked_test_may_run_heavy_commands() -> None:
    assert subprocess.run(["true", "install.sh"], check=False).returncode == 0


def _pytest_ini() -> dict[str, object]:
    data = tomllib.loads((TOOLKIT / "pyproject.toml").read_text())
    ini: dict[str, object] = data["tool"]["pytest"]["ini_options"]
    return ini


def test_default_run_is_the_fast_tier() -> None:
    ini = _pytest_ini()
    assert "-m 'not e2e'" in str(ini["addopts"])
    assert any(str(m).startswith("e2e:") for m in ini["markers"])  # type: ignore[attr-defined]


def test_xdist_is_a_dev_dependency() -> None:
    data = tomllib.loads((TOOLKIT / "pyproject.toml").read_text())
    assert any(d.startswith("pytest-xdist") for d in data["dependency-groups"]["dev"])


def _make_n(target: str) -> str:
    res = subprocess.run(
        ["make", "-n", target], cwd=TOOLKIT, text=True, capture_output=True, check=True
    )
    return res.stdout


def test_make_test_e2e_runs_only_e2e() -> None:
    out = _make_n("test-e2e")
    assert "pytest" in out
    assert "-m e2e" in out
    assert "-n auto" in out


def test_make_test_runs_the_fast_tier() -> None:
    out = _make_n("test")
    assert "pytest" in out
    assert "e2e" not in out  # the default addopts already exclude e2e


def test_tiers_are_documented() -> None:
    doc = (TOOLKIT / "docs" / "testing.md").read_text()
    for needle in ("e2e", "make test-e2e", "-n auto", "e2e tier", "measure_suite.py"):
        assert needle in doc
    assert "docs/testing.md" in (TOOLKIT / "README.md").read_text()
