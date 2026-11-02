"""C0.3 with the real Docker Engine: the check container is isolated, and the in-container
self-checks fail when any guarantee is broken.

Marked `docker`. Skipped (with the reason shown) when the Engine or the image is missing;
with HARNESS_COMPAT_REQUIRE_DOCKER=1 a missing prerequisite FAILS instead (mandatory
before the C0 PR and in real compatibility runs). Build the image once with:
  python3 compat/_compat.py build-image --tag local --state <dir> --docker-config <empty dir>
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

pytestmark = pytest.mark.docker

RUNNER = TOOLKIT / "compat" / "_compat.py"
IMAGE = "harness-compat:local"


def _require(reason: str) -> None:
    if os.environ.get("HARNESS_COMPAT_REQUIRE_DOCKER") == "1":
        pytest.fail(f"docker test prerequisite missing: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    config = tmp_path_factory.mktemp("docker-config")
    argv = [sys.executable, str(RUNNER), "preflight", "--image", IMAGE]
    argv += ["--docker-config", str(config)]
    res = subprocess.run(argv, text=True, capture_output=True, check=False)
    if res.returncode != 0:
        _require(f"Docker Engine or image {IMAGE} not ready: {res.stderr.strip()}")
    return config


@pytest.fixture
def run(tmp_path: Path, engine: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()
    run_id = "t" + uuid.uuid4().hex[:10]
    res = subprocess.run(
        [
            sys.executable, str(RUNNER), "docker-cmd",
            "--repo", str(repo), "--workdir", ".", "--toolkit", str(toolkit),
            "--image", IMAGE, "--run-id", run_id,
            "--uid", str(os.getuid()), "--gid", str(os.getgid()),
            "--docker-config", str(engine), "--", "true",
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert res.returncode == 0, res.stderr
    data: dict[str, Any] = json.loads(res.stdout)
    return {**data, "repo": repo, "run_id": run_id}


def _exec(run: dict[str, Any], argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, text=True, capture_output=True, env=run["client_env"], check=False, timeout=120
    )


def _swap(argv: list[str], flag: str, value: str) -> list[str]:
    out = list(argv)
    out[out.index(flag) + 1] = value
    return out


def _before_image(argv: list[str], *extra: str) -> list[str]:
    i = argv.index(IMAGE)
    return [*argv[:i], *extra, *argv[i:]]


def test_locked_down_container_passes_its_self_checks(run: dict[str, Any]) -> None:
    res = _exec(run, run["argv"])
    assert res.returncode == 0, res.stdout + res.stderr


def test_self_check_fails_with_network(run: dict[str, Any]) -> None:
    res = _exec(run, _swap(run["argv"], "--network", "bridge"))
    assert res.returncode != 0
    assert "network" in (res.stdout + res.stderr).lower()


def test_env_guard_fails_with_an_extra_variable(run: dict[str, Any]) -> None:
    res = _exec(run, _before_image(run["argv"], "-e", "DATABASE_URL=postgres://x"))
    assert res.returncode != 0
    assert "DATABASE_URL" in res.stdout + res.stderr


def test_self_check_fails_with_a_writable_root(run: dict[str, Any]) -> None:
    argv = [a for a in run["argv"] if a != "--read-only"]
    res = _exec(run, argv)
    assert res.returncode != 0
    assert "read-only" in (res.stdout + res.stderr).lower()


def test_self_check_fails_with_a_writable_toolkit(run: dict[str, Any]) -> None:
    argv = [a.replace(",dst=/toolkit,readonly", ",dst=/toolkit") for a in run["argv"]]
    res = _exec(run, argv)
    assert res.returncode != 0
    assert "toolkit" in (res.stdout + res.stderr).lower()


def test_self_check_fails_with_a_socket_in_a_mount(run: dict[str, Any]) -> None:
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(run["repo"] / "db.sock"))
        res = _exec(run, run["argv"])
    finally:
        sock.close()
    assert res.returncode != 0
    assert "socket" in (res.stdout + res.stderr).lower()


def test_container_is_removed_after_the_run(run: dict[str, Any]) -> None:
    _exec(run, run["argv"])
    ps = _exec(
        run,
        ["docker", "--host", "unix:///var/run/docker.sock", "ps", "-aq",
         "--filter", f"label=harness-compat.run={run['run_id']}"],
    )  # fmt: skip
    assert ps.returncode == 0, ps.stderr
    assert ps.stdout.strip() == ""


def _with_command(run: dict[str, Any], *command: str) -> list[str]:
    argv: list[str] = run["argv"]
    return [*argv[: argv.index(IMAGE) + 1], *command]


def test_tmp_allows_running_a_compiled_probe(run: dict[str, Any]) -> None:
    script = "cp /usr/bin/true /tmp/probe && /tmp/probe"
    res = _exec(run, _with_command(run, "bash", "-c", script))
    assert res.returncode == 0, res.stdout + res.stderr


def test_home_exists_and_the_passwd_home_is_writable(run: dict[str, Any]) -> None:
    script = 'test -d "$HOME" && mkdir -p /home/ubuntu/.cache/uv && touch "$HOME/x"'
    res = _exec(run, _with_command(run, "bash", "-c", script))
    assert res.returncode == 0, res.stdout + res.stderr


def test_norminette_is_available(run: dict[str, Any]) -> None:
    res = _exec(run, _with_command(run, "norminette", "--version"))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "3.3.60" in res.stdout


def test_valgrind_is_available(run: dict[str, Any]) -> None:
    res = _exec(run, _with_command(run, "valgrind", "--version"))
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.startswith("valgrind-")


def test_self_check_fails_with_an_unexpected_mount(run: dict[str, Any], tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    argv = _before_image(run["argv"], "--mount", f"type=bind,src={extra},dst=/extra,readonly")
    res = _exec(run, argv)
    assert res.returncode != 0
    assert "unexpected mount" in (res.stdout + res.stderr).lower()


def test_self_check_fails_with_an_extra_mount_under_dev(
    run: dict[str, Any], tmp_path: Path
) -> None:
    """Wildcards under /dev, /proc or /sys would hide e.g. a host socket at /dev/host."""
    extra = tmp_path / "hostdir"
    extra.mkdir()
    argv = _before_image(run["argv"], "--mount", f"type=bind,src={extra},dst=/dev/host,readonly")
    res = _exec(run, argv)
    assert res.returncode != 0
    assert "unexpected mount" in (res.stdout + res.stderr).lower()
