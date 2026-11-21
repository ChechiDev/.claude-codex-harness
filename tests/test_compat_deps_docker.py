"""C0.4 with the real Docker Engine: dependency prep for a Python project, end to end.

Uses a virtual project with no third-party dependencies, so the fetch phase needs no
real download; the real PyPI path is exercised in C0.6 against guakamole's copy.
Marked `docker`; skip/fail rules as in test_compat_isolation.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

pytestmark = [pytest.mark.docker, pytest.mark.e2e]  # e2e: real containers (#68)

RUNNER = TOOLKIT / "compat" / "_compat.py"
IMAGE = "harness-compat:local"
SOCKET = "unix:///var/run/docker.sock"
PYPROJECT = """[project]
name = "toy"
version = "0"
requires-python = ">=3.12"
dependencies = []

[tool.uv]
package = false
"""


def _require(reason: str) -> None:
    if os.environ.get("HARNESS_COMPAT_REQUIRE_DOCKER") == "1":
        pytest.fail(f"docker test prerequisite missing: {reason}")
    pytest.skip(reason)


def _compat(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args], text=True, capture_output=True, check=False
    )


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cfg = tmp_path_factory.mktemp("docker-config")
    res = _compat("preflight", "--image", IMAGE, "--docker-config", str(cfg))
    if res.returncode != 0:
        _require(f"Docker Engine or image {IMAGE} not ready: {res.stderr.strip()}")
    return cfg


@pytest.fixture
def toy(tmp_path: Path, config: Path) -> Iterator[dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(PYPROJECT)
    lock = subprocess.run(
        ["uv", "lock", "--offline"], cwd=repo, text=True, capture_output=True, check=False
    )
    assert lock.returncode == 0, lock.stderr
    run_id = "t" + uuid.uuid4().hex[:10]
    ids = ["--uid", str(os.getuid()), "--gid", str(os.getgid())]
    common = ["--run-id", run_id, *ids, "--image", IMAGE, "--docker-config", str(config)]
    yield {"repo": repo, "run_id": run_id, "config": config, "common": common, "tmp": tmp_path}
    subprocess.run(
        ["docker", "--host", SOCKET, "volume", "rm", "-f", f"harness-compat-deps-{run_id}"],
        capture_output=True,
        check=False,
    )


def _prepare(toy: dict[str, Any]) -> str:
    res = _compat(
        "prepare-deps", "--repo", str(toy["repo"]), "--workdir", ".",
        "--scratch", str(toy["tmp"] / "scratch"), *toy["common"],
    )  # fmt: skip
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.strip().splitlines()[-1]


def _exec(spec: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        spec["argv"],
        env=spec["client_env"],
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )


def _check_cmd(toy: dict[str, Any], volume: str, *command: str) -> dict[str, Any]:
    res = _compat(
        "docker-cmd", "--repo", str(toy["repo"]), "--workdir", ".",
        "--toolkit", str(toy["tmp"]), "--deps-volume", volume, *toy["common"], "--", *command,
    )  # fmt: skip
    assert res.returncode == 0, res.stderr
    spec: dict[str, Any] = json.loads(res.stdout)
    return spec


def _phase(toy: dict[str, Any], phase: str, *extra: str) -> dict[str, Any]:
    volume = f"harness-compat-deps-{toy['run_id']}"
    args = ["deps-cmd", "--phase", phase, "--volume", volume, *toy["common"], *extra]
    res = _compat(*args)
    assert res.returncode == 0, res.stderr
    spec: dict[str, Any] = json.loads(res.stdout)
    return spec


def test_prepared_venv_is_used_by_the_offline_check_container(toy: dict[str, Any]) -> None:
    volume = _prepare(toy)
    assert volume == f"harness-compat-deps-{toy['run_id']}"
    probe = "import sys; print(sys.prefix)"
    res = _exec(_check_cmd(toy, volume, "uv", "run", "python", "-c", probe))
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip() == "/deps/venv"


def test_volume_is_owned_by_the_runner_user(toy: dict[str, Any]) -> None:
    volume = _prepare(toy)
    res = _exec(_check_cmd(toy, volume, "stat", "-c", "%u:%g", "/deps"))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == f"{os.getuid()}:{os.getgid()}"


def test_fetch_container_cannot_see_the_project(toy: dict[str, Any]) -> None:
    _prepare(toy)
    lock_dir = toy["tmp"] / "scratch" / f"lock-{toy['run_id']}"
    spec = _phase(toy, "fetch", "--lock-dir", str(lock_dir))
    argv = spec["argv"]
    sep = argv.index("--", argv.index(IMAGE))
    spec["argv"] = [*argv[: sep + 1], "bash", "-c", "test ! -e /work && ls -A /lock"]
    res = _exec(spec)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.split() == ["pyproject.toml", "uv.lock"]


def test_install_phase_refuses_network(toy: dict[str, Any]) -> None:
    _prepare(toy)
    spec = _phase(toy, "install", "--repo", str(toy["repo"]), "--workdir", ".")
    argv = list(spec["argv"])
    argv[argv.index("--network") + 1] = "bridge"
    spec["argv"] = argv
    res = _exec(spec)
    assert res.returncode != 0
    assert "network" in (res.stdout + res.stderr).lower()
