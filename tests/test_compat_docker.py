"""C0.2: the docker command of a compatibility run, built but never executed.

`compat/_compat.py docker-cmd` prints JSON: {"argv": [...], "client_env": {...}}.
argv is the full docker invocation; client_env is the environment to run it with.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

RUNNER = TOOLKIT / "compat" / "_compat.py"
SOCKET = "unix:///var/run/docker.sock"
ALLOWED_ENV = {
    "LANG",
    "HOME",
    "UV_CACHE_DIR",
    "UV_PROJECT_ENVIRONMENT",
    "UV_PYTHON_DOWNLOADS",
    "UV_OFFLINE",
    "UV_NO_SYNC",
}


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "scratch" / "repo"
    (repo / "backend").mkdir(parents=True)
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()
    config = tmp_path / "docker-config"
    config.mkdir()
    return {"repo": repo, "toolkit": toolkit, "config": config}


def _run(
    paths: dict[str, Path], *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    args = [
        "docker-cmd",
        "--repo", str(paths["repo"]),
        "--workdir", "backend",
        "--toolkit", str(paths["toolkit"]),
        "--image", "harness-compat:test",
        "--run-id", "r1a2b3",
        "--uid", "1000",
        "--gid", "1000",
        "--docker-config", str(paths["config"]),
        *extra,
    ]  # fmt: skip
    if "--" not in extra:
        args += ["--", "make", "check"]
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        text=True,
        capture_output=True,
        env=full_env,
        check=False,
    )


def _ok(paths: dict[str, Path], *extra: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    res = _run(paths, *extra, env=env)
    assert res.returncode == 0, res.stderr
    data: dict[str, Any] = json.loads(res.stdout)
    return data


def _values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


# ── container restrictions ───────────────────────────────────────────────────


def test_argv_targets_the_local_engine_explicitly(paths: dict[str, Path]) -> None:
    argv = _ok(paths)["argv"]
    assert argv[:4] == ["docker", "--host", SOCKET, "run"]


def test_container_is_locked_down(paths: dict[str, Path]) -> None:
    argv = _ok(paths)["argv"]
    for flag in ("--rm", "--read-only"):
        assert flag in argv
    assert _values(argv, "--network") == ["none"]
    assert _values(argv, "--cap-drop") == ["ALL"]
    assert "no-new-privileges" in _values(argv, "--security-opt")
    assert _values(argv, "--user") == ["1000:1000"]
    assert _values(argv, "--pids-limit")
    assert "--privileged" not in argv
    assert "--cap-add" not in argv


def test_tmp_and_home_are_tmpfs(paths: dict[str, Path]) -> None:
    tmpfs = [v.split(":")[0] for v in _values(_ok(paths)["argv"], "--tmpfs")]
    assert sorted(tmpfs) == ["/home/compat", "/tmp"]


# ── mounts ───────────────────────────────────────────────────────────────────


def _mounts(argv: list[str]) -> list[dict[str, str]]:
    out = []
    for spec in _values(argv, "--mount"):
        fields: dict[str, str] = {}
        for field in spec.split(","):
            key, sep, value = field.partition("=")
            fields[key] = value if sep else "true"
        out.append(fields)
    return out


def test_only_the_copy_and_the_toolkit_are_mounted(paths: dict[str, Path]) -> None:
    argv = _ok(paths)["argv"]
    assert "-v" not in argv and "--volume" not in argv
    mounts = {m["dst"]: m for m in _mounts(argv)}
    assert set(mounts) == {"/work", "/toolkit"}
    assert mounts["/work"]["src"] == str(paths["repo"])
    assert "readonly" not in mounts["/work"]
    assert mounts["/toolkit"]["src"] == str(paths["toolkit"])
    assert mounts["/toolkit"]["readonly"] == "true"
    assert all(m["type"] == "bind" for m in mounts.values())


def test_workdir_is_the_install_root_inside_the_copy(paths: dict[str, Path]) -> None:
    assert _values(_ok(paths)["argv"], "--workdir") == ["/work/backend"]


def test_deps_volume_is_a_third_read_only_mount(paths: dict[str, Path]) -> None:
    argv = _ok(paths, "--deps-volume", "harness-compat-deps-r1a2b3")["argv"]
    mounts = {m["dst"]: m for m in _mounts(argv)}
    assert set(mounts) == {"/work", "/toolkit", "/deps"}
    assert mounts["/deps"]["type"] == "volume"
    assert mounts["/deps"]["src"] == "harness-compat-deps-r1a2b3"
    assert mounts["/deps"]["readonly"] == "true"


@pytest.mark.parametrize("bad", ["/", str(Path.home()), str(Path.home() / ".claude")])
def test_host_root_home_and_claude_dir_are_never_mounted(paths: dict[str, Path], bad: str) -> None:
    res = _run({**paths, "repo": Path(bad)})
    assert res.returncode != 0
    assert "mount" in res.stderr.lower()


def test_docker_socket_is_never_mounted(paths: dict[str, Path]) -> None:
    res = _run({**paths, "toolkit": Path("/var/run/docker.sock")})
    assert res.returncode != 0
    assert "mount" in res.stderr.lower()


@pytest.mark.parametrize("bad", ["/abs", "../up", "a/../../up"])
def test_workdir_must_stay_inside_the_copy(paths: dict[str, Path], bad: str) -> None:
    argv = [a if a != "backend" else bad for a in ["--workdir", "backend"]]
    res = _run(paths, *argv)
    assert res.returncode != 0
    assert "workdir" in res.stderr.lower()


# ── environment ──────────────────────────────────────────────────────────────


def _env(argv: list[str]) -> dict[str, str]:
    pairs = _values(argv, "-e") + _values(argv, "--env")
    assert all("=" in p for p in pairs), "every -e must carry KEY=VALUE (no host passthrough)"
    return dict(p.split("=", 1) for p in pairs)


def test_container_env_is_the_allowlist_only(paths: dict[str, Path]) -> None:
    host = {
        "DATABASE_URL": "postgres://secret-db-host/app",
        "TEST_DATABASE_URL": "postgres://secret-test-db/app",
        "GH_TOKEN": "ghp_secret_token_value",
    }
    argv = _ok(paths, env=host)["argv"]
    env = _env(argv)
    assert set(env) <= ALLOWED_ENV
    assert env["HOME"] == "/home/compat"
    assert env["UV_OFFLINE"] == "1"
    assert env["UV_NO_SYNC"] == "1"
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert "--env-file" not in argv
    joined = " ".join(argv)
    for secret in host.values():
        assert secret not in joined


def test_uv_paths_point_into_the_deps_volume(paths: dict[str, Path]) -> None:
    env = _env(_ok(paths, "--deps-volume", "harness-compat-deps-r1a2b3")["argv"])
    assert env["UV_CACHE_DIR"].startswith("/deps/")
    assert env["UV_PROJECT_ENVIRONMENT"].startswith("/deps/")


# ── docker client hardening ──────────────────────────────────────────────────


def test_client_env_drops_remote_daemon_selectors(paths: dict[str, Path]) -> None:
    host = {
        "DOCKER_HOST": "tcp://evil:2375",
        "DOCKER_CONTEXT": "remote",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/certs",
    }
    client = _ok(paths, env=host)["client_env"]
    for key in host:
        assert key not in client
    assert client["DOCKER_CONFIG"] == str(paths["config"])


@pytest.mark.parametrize("user_config", ["config.json", "contexts", "cli-plugins"])
def test_docker_config_must_hold_no_user_configuration(
    paths: dict[str, Path], user_config: str
) -> None:
    """Credentials/proxies (config.json), remote contexts and plugins must never apply."""
    target = paths["config"] / user_config
    if user_config == "config.json":
        target.write_text('{"credsStore": "desktop.exe"}')
    else:
        target.mkdir()
    res = _run(paths)
    assert res.returncode != 0
    assert "docker-config" in res.stderr.lower()
    assert user_config in res.stderr


def test_docker_config_may_hold_docker_runtime_state(paths: dict[str, Path]) -> None:
    """The CLI writes its own state there (buildx/, .token_seed): harmless, accepted."""
    (paths["config"] / "buildx" / "refs").mkdir(parents=True)
    (paths["config"] / ".token_seed").write_text("seed")
    _ok(paths)


def test_docker_config_must_exist(paths: dict[str, Path]) -> None:
    res = _run({**paths, "config": paths["config"] / "missing"})
    assert res.returncode != 0
    assert "docker-config" in res.stderr.lower()


# ── labels, names, command ───────────────────────────────────────────────────


def test_container_carries_the_run_labels_and_name(paths: dict[str, Path]) -> None:
    argv = _ok(paths)["argv"]
    labels = _values(argv, "--label")
    assert "harness-compat=1" in labels
    assert "harness-compat.run=r1a2b3" in labels
    assert _values(argv, "--name") == ["harness-compat-r1a2b3-check"]


@pytest.mark.parametrize("bad", ["", "UPPER", "a b", "x;rm", "a/b", "-dash"])
def test_run_id_is_strictly_validated(paths: dict[str, Path], bad: str) -> None:
    res = _run(paths, "--run-id", bad)
    assert res.returncode != 0
    assert "run-id" in res.stderr.lower()


def test_image_then_command_close_the_argv(paths: dict[str, Path]) -> None:
    argv = _ok(paths)["argv"]
    assert argv[-3:] == ["harness-compat:test", "make", "check"]


def test_a_command_is_required(paths: dict[str, Path]) -> None:
    res = _run(paths, "--")
    assert res.returncode != 0
    assert "command" in res.stderr.lower()
