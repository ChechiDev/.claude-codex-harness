"""C0.4 (no Docker needed): the three dependency-prep containers of a Python project.

  init     only `chown` on the run's deps volume (root with CAP_CHOWN only, no network)
  fetch    WITH network but WITHOUT project code: only pyproject.toml + uv.lock copies
  install  WITHOUT network, project copy read-only: installs from the fetch cache

`deps-cmd` prints JSON {"argv": [...], "client_env": {...}}; it never runs docker.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT

COMPAT = TOOLKIT / "compat"
RUNNER = COMPAT / "_compat.py"
RUN_ID = "r1a2b3"
VOLUME = f"harness-compat-deps-{RUN_ID}"
IMAGE = "harness-compat:test"
PREP = "/usr/local/bin/compat-entrypoint-prep"
FETCH_SYNC = [
    "uv", "sync", "--frozen", "--no-build",
    "--no-install-project", "--no-install-workspace", "--no-install-local",
]  # fmt: skip


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "scratch" / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    (repo / "backend" / "uv.lock").write_text("version = 1\n")
    (repo / "backend" / "app.py").write_text("print('project code')\n")
    lock = tmp_path / "scratch" / "lock"
    lock.mkdir()
    (lock / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    (lock / "uv.lock").write_text("version = 1\n")
    config = tmp_path / "docker-config"
    config.mkdir()
    return {"repo": repo, "lock": lock, "config": config, "tmp": tmp_path}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args], text=True, capture_output=True, check=False
    )


def _deps(paths: dict[str, Path], phase: str, *extra: str) -> subprocess.CompletedProcess[str]:
    args = [
        "deps-cmd", "--phase", phase, "--run-id", RUN_ID, "--uid", "1000", "--gid", "1000",
        "--image", IMAGE, "--docker-config", str(paths["config"]), "--volume", VOLUME,
    ]  # fmt: skip
    if phase == "fetch":
        args += ["--lock-dir", str(paths["lock"])]
    if phase == "install":
        args += ["--repo", str(paths["repo"]), "--workdir", "backend"]
    return _run(*args, *extra)


def _ok(paths: dict[str, Path], phase: str, *extra: str) -> list[str]:
    res = _deps(paths, phase, *extra)
    assert res.returncode == 0, res.stderr
    argv: list[str] = json.loads(res.stdout)["argv"]
    return argv


def _values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


def _mounts(argv: list[str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for spec in _values(argv, "--mount"):
        fields: dict[str, str] = {}
        for field in spec.split(","):
            key, sep, value = field.partition("=")
            fields[key] = value if sep else "true"
        out[fields["dst"]] = fields
    return out


def _env(argv: list[str]) -> dict[str, str]:
    pairs = _values(argv, "-e")
    assert all("=" in p for p in pairs)
    return dict(p.split("=", 1) for p in pairs)


def _after_image(argv: list[str]) -> list[str]:
    return argv[argv.index(IMAGE) + 1 :]


# ── common to the three phases ───────────────────────────────────────────────


@pytest.mark.parametrize("phase", ["init", "fetch", "install"])
def test_every_phase_is_locked_down_and_labelled(paths: dict[str, Path], phase: str) -> None:
    argv = _ok(paths, phase)
    assert argv[:4] == ["docker", "--host", "unix:///var/run/docker.sock", "run"]
    for flag in ("--rm", "--read-only"):
        assert flag in argv
    assert _values(argv, "--cap-drop") == ["ALL"]
    assert "no-new-privileges" in _values(argv, "--security-opt")
    assert "--privileged" not in argv
    assert f"harness-compat.run={RUN_ID}" in _values(argv, "--label")
    assert "harness-compat=1" in _values(argv, "--label")
    assert _values(argv, "--name") == [f"harness-compat-{RUN_ID}-deps-{phase}"]
    assert _values(argv, "--entrypoint") == [PREP]
    assert "--env-file" not in argv


@pytest.mark.parametrize("phase", ["init", "fetch", "install"])
def test_the_deps_volume_must_belong_to_the_run(paths: dict[str, Path], phase: str) -> None:
    res = _deps(paths, phase, "--volume", "my-precious-volume")
    assert res.returncode != 0
    assert "volume" in res.stderr.lower()


# ── init ─────────────────────────────────────────────────────────────────────


def test_init_only_chowns_the_volume_as_root_with_chown_only(paths: dict[str, Path]) -> None:
    argv = _ok(paths, "init")
    assert _values(argv, "--user") == ["0:0"]
    assert _values(argv, "--cap-add") == ["CHOWN"]
    assert _values(argv, "--network") == ["none"]
    mounts = _mounts(argv)
    assert set(mounts) == {"/deps"}
    assert mounts["/deps"] == {"type": "volume", "src": VOLUME, "dst": "/deps"}
    assert _after_image(argv) == ["--rw", "/deps", "--", "chown", "1000:1000", "/deps"]


# ── fetch ────────────────────────────────────────────────────────────────────


def test_fetch_has_network_but_never_sees_the_project(paths: dict[str, Path]) -> None:
    argv = _ok(paths, "fetch")
    assert _values(argv, "--network") == ["bridge"]
    assert _values(argv, "--user") == ["1000:1000"]
    assert "--cap-add" not in argv
    mounts = _mounts(argv)
    assert set(mounts) == {"/lock", "/deps"}
    assert mounts["/lock"]["src"] == str(paths["lock"])
    assert mounts["/lock"]["readonly"] == "true"
    assert mounts["/deps"]["src"] == VOLUME
    assert "readonly" not in mounts["/deps"]
    assert _values(argv, "--workdir") == ["/lock"]
    assert _after_image(argv) == ["--allow-network", "--rw", "/deps", "--", *FETCH_SYNC]


def test_fetch_env_points_uv_into_the_volume_and_is_not_offline(paths: dict[str, Path]) -> None:
    env = _env(_ok(paths, "fetch"))
    assert env["UV_CACHE_DIR"] == "/deps/cache"
    assert env["UV_PROJECT_ENVIRONMENT"] == "/deps/venv"
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert "UV_OFFLINE" not in env


@pytest.mark.parametrize("extra", ["app.py", "src", "conftest.py"])
def test_fetch_refuses_a_lock_dir_with_anything_but_the_two_files(
    paths: dict[str, Path], extra: str
) -> None:
    target = paths["lock"] / extra
    if "." in extra:
        target.write_text("x")
    else:
        target.mkdir()
    res = _deps(paths, "fetch")
    assert res.returncode != 0
    assert "lock" in res.stderr.lower()


# ── install ──────────────────────────────────────────────────────────────────


def test_install_has_no_network_and_a_read_only_copy(paths: dict[str, Path]) -> None:
    argv = _ok(paths, "install")
    assert _values(argv, "--network") == ["none"]
    assert _values(argv, "--user") == ["1000:1000"]
    mounts = _mounts(argv)
    assert set(mounts) == {"/work", "/deps"}
    assert mounts["/work"]["src"] == str(paths["repo"])
    assert mounts["/work"]["readonly"] == "true"
    assert "readonly" not in mounts["/deps"]
    assert _values(argv, "--workdir") == ["/work/backend"]
    assert _after_image(argv) == ["--rw", "/deps", "--", "uv", "sync", "--frozen", "--offline"]


def test_install_env_is_offline(paths: dict[str, Path]) -> None:
    env = _env(_ok(paths, "install"))
    assert env["UV_OFFLINE"] == "1"
    assert env["UV_PROJECT_ENVIRONMENT"] == "/deps/venv"


# ── lock dir preparation ─────────────────────────────────────────────────────


def test_prepare_lock_copies_only_pyproject_and_lockfile(paths: dict[str, Path]) -> None:
    out = paths["tmp"] / "lock2"
    res = _run("prepare-lock", str(paths["repo"] / "backend"), str(out))
    assert res.returncode == 0, res.stderr
    assert sorted(p.name for p in out.iterdir()) == ["pyproject.toml", "uv.lock"]


def test_prepare_lock_fails_without_a_lockfile(paths: dict[str, Path]) -> None:
    (paths["repo"] / "backend" / "uv.lock").unlink()
    res = _run("prepare-lock", str(paths["repo"] / "backend"), str(paths["tmp"] / "lock3"))
    assert res.returncode != 0
    assert "uv.lock" in res.stderr


def test_a_project_without_pyproject_needs_no_deps(paths: dict[str, Path]) -> None:
    c_project = paths["tmp"] / "cproj"
    c_project.mkdir()
    res = _run("prepare-lock", str(c_project), str(paths["tmp"] / "lock4"))
    assert res.returncode == 0, res.stderr
    assert "no python deps" in res.stdout.lower()
    assert not (paths["tmp"] / "lock4").exists()


# ── the prep entrypoint, run with bash on the host ───────────────────────────


def _bash(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = ["PATH=/usr/bin:/bin", "HOME=/home/compat", "LANG=C.UTF-8"]
    argv = ["/usr/bin/env", "-i", *env, "bash", str(COMPAT / script), *args]
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def test_selfcheck_allow_network_skips_only_the_network_check() -> None:
    res = _bash("selfcheck.sh", "--allow-network")
    assert res.returncode != 0
    out = res.stderr.lower()
    assert "network" not in out
    assert "read-only" in out


def test_selfcheck_rejects_unknown_options() -> None:
    res = _bash("selfcheck.sh", "--skip-everything")
    assert res.returncode != 0
    assert "option" in res.stderr.lower()


def test_prep_entrypoint_requires_the_separator() -> None:
    res = _bash("entrypoint_prep.sh", "--rw", "/deps", "true")
    assert res.returncode != 0
    assert "--" in res.stderr


def test_prep_entrypoint_runs_guards_before_the_command() -> None:
    text = (COMPAT / "entrypoint_prep.sh").read_text()
    guard, check, run = (text.find(s) for s in ("guard_env", "selfcheck", 'exec "$@"'))
    assert -1 < guard < check < run


def test_image_ships_the_prep_entrypoint() -> None:
    text = (COMPAT / "Dockerfile").read_text()
    assert "entrypoint_prep.sh" in text
    assert PREP in text
