"""C0.5 (no Docker needed): delta report, Stop-cache clearing, run-scoped cleanup.

report BASE_VS_CAND.json   verdict per project; exit 1 on any regression
cleanup --run-id ID        remove ONLY containers/volumes labelled with that run
purge-images --state DIR   remove ONLY recorded image ids still labelled harness-compat=1
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

RUNNER = TOOLKIT / "compat" / "_compat.py"
FAKE_DOCKER = Path(__file__).resolve().parent / "fake_docker.py"
SOCKET = "unix:///var/run/docker.sock"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


# ── report ───────────────────────────────────────────────────────────────────


def _side(exit_code: int, output: str = "") -> dict[str, Any]:
    return {"exit": exit_code, "output": output}


def _report(tmp_path: Path, results: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    path = tmp_path / "results.json"
    path.write_text(json.dumps(results))
    res = _run("report", str(path))
    assert res.stdout, res.stderr
    data: dict[str, Any] = json.loads(res.stdout)
    return res.returncode, data


@pytest.mark.parametrize(
    ("base", "cand", "verdict", "code"),
    [
        (_side(0, "ok"), _side(0, "ok"), "same", 0),
        (_side(0, "ok"), _side(2, "boom"), "regression", 1),
        (_side(2, "boom"), _side(0, "ok"), "fixed", 0),
        (_side(2, "boom"), _side(2, "boom"), "inconclusive", 2),
    ],
)
def test_report_verdict_by_exit_codes(
    tmp_path: Path, base: dict[str, Any], cand: dict[str, Any], verdict: str, code: int
) -> None:
    rc, data = _report(tmp_path, {"proj": {"baseline": base, "candidate": cand}})
    assert data["projects"]["proj"]["verdict"] == verdict
    assert rc == code


@pytest.mark.parametrize("which", ["baseline", "candidate", "both"])
def test_a_compat_error_on_any_side_is_an_error_not_a_pass(tmp_path: Path, which: str) -> None:
    """C0 itself failed (snapshot/relink/deps): no make check ran, so it can never pass."""
    error = {"exit": 99, "output": "[FAIL] compat snapshot: boom", "error": True}
    sides = {"baseline": _side(0, "ok"), "candidate": _side(0, "ok")}
    for side in ("baseline", "candidate") if which == "both" else (which,):
        sides[side] = error
    rc, data = _report(tmp_path, {"p": sides})
    assert data["projects"]["p"]["verdict"] == "error"
    assert data["errors"] == ["p"]
    assert rc == 1


def test_inconclusive_projects_are_listed(tmp_path: Path) -> None:
    rc, data = _report(tmp_path, {"p": {"baseline": _side(1), "candidate": _side(1)}})
    assert data["inconclusive"] == ["p"]
    assert rc == 2


def test_report_lists_new_warnings_without_failing(tmp_path: Path) -> None:
    base = _side(0, "[OK] a\nWARN: old warning\n")
    cand = _side(0, "[OK] a\nWARN: old warning\nWARN: seal missing for 1.1.1\n")
    rc, data = _report(tmp_path, {"p": {"baseline": base, "candidate": cand}})
    assert data["projects"]["p"]["new_warnings"] == ["WARN: seal missing for 1.1.1"]
    assert data["projects"]["p"]["verdict"] == "same"
    assert rc == 0


def test_report_new_fail_line_is_a_regression_even_with_exit_zero(tmp_path: Path) -> None:
    base = _side(0, "[OK] gate\n")
    cand = _side(0, "[OK] gate\n[FAIL] seal manifest mismatch\n")
    rc, data = _report(tmp_path, {"p": {"baseline": base, "candidate": cand}})
    assert data["projects"]["p"]["new_failures"] == ["[FAIL] seal manifest mismatch"]
    assert data["projects"]["p"]["verdict"] == "regression"
    assert rc == 1


def test_report_covers_every_project_and_sums_up(tmp_path: Path) -> None:
    results = {
        "a": {"baseline": _side(0), "candidate": _side(0)},
        "b": {"baseline": _side(0), "candidate": _side(1)},
    }
    rc, data = _report(tmp_path, results)
    assert set(data["projects"]) == {"a", "b"}
    assert data["regressions"] == ["b"]
    assert rc == 1


# ── Stop cache in the copy, worktrees refused ────────────────────────────────


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_copy_clears_the_stop_cache_of_the_copy_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".git" / "harness-check.ok").write_text("fingerprint")
    res = _run("copy", str(repo), str(tmp_path / "scratch"))
    assert res.returncode == 0, res.stderr
    copy = Path(res.stdout.strip())
    assert not (copy / ".git" / "harness-check.ok").exists()
    assert (repo / ".git" / "harness-check.ok").read_text() == "fingerprint"


def test_copy_refuses_a_git_worktree_and_leaves_the_original_marker(tmp_path: Path) -> None:
    """A worktree's .git is a FILE pointing at the original repo: a copy would still write
    into the original's git dir (e.g. the Stop marker). Refused outright."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(
        repo,
        "-c",
        "user.email=d@e.x",
        "-c",
        "user.name=d",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "i",
    )
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(wt))
    marker = repo / ".git" / "worktrees" / "wt" / "harness-check.ok"
    marker.write_text("fingerprint")
    res = _run("copy", str(wt), str(tmp_path / "scratch"))
    assert res.returncode != 0
    assert "worktree" in res.stderr.lower()
    assert marker.read_text() == "fingerprint"
    assert not (tmp_path / "scratch").exists()


# ── cleanup and purge, with a fake docker ────────────────────────────────────


@pytest.fixture
def fake(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "docker_state.json"
    run_a = {"harness-compat": "1", "harness-compat.run": "runa"}
    run_b = {"harness-compat": "1", "harness-compat.run": "runb"}
    state.write_text(
        json.dumps(
            {
                "engine": "linux",
                "containers": {"ca1": run_a, "cb1": run_b, "mine": {"app": "postgres"}},
                "volumes": {
                    "harness-compat-deps-runa": run_a,
                    "harness-compat-deps-runb": run_b,
                    "pgdata": {},
                },
                "images": {
                    "sha256:ours": {"Config": {"Labels": {"harness-compat": "1"}}},
                    "sha256:yours": {"Config": {"Labels": {"maintainer": "you"}}},
                },
            }
        )
    )
    wrapper = bin_dir / "docker"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_DOCKER}" "{state}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "docker-config"
    config.mkdir()
    return {"bin": bin_dir, "state": state, "config": config, "tmp": tmp_path}


def _fake_env(fake: dict[str, Path]) -> dict[str, str]:
    return {"PATH": f"{fake['bin']}:{os.environ['PATH']}"}


def _state(fake: dict[str, Path]) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(fake["state"].read_text())
    return data


def test_cleanup_removes_only_the_runs_own_containers_and_volumes(
    fake: dict[str, Path],
) -> None:
    args = ["cleanup", "--run-id", "runa", "--docker-config", str(fake["config"])]
    res = _run(*args, env=_fake_env(fake))
    assert res.returncode == 0, res.stderr
    state = _state(fake)
    assert set(state["containers"]) == {"cb1", "mine"}
    assert set(state["volumes"]) == {"harness-compat-deps-runb", "pgdata"}


def test_cleanup_always_filters_by_the_run_label_and_never_prunes(
    fake: dict[str, Path],
) -> None:
    args = ["cleanup", "--run-id", "runa", "--docker-config", str(fake["config"])]
    _run(*args, env=_fake_env(fake))
    calls = _state(fake)["calls"]
    assert calls, "cleanup made no docker call"
    for call in calls:
        assert call[:2] == ["--host", SOCKET]
        assert "prune" not in call
        if call[2] in ("ps",) or call[2:4] == ["volume", "ls"]:
            assert "label=harness-compat.run=runa" in call


def test_cleanup_validates_the_run_id(fake: dict[str, Path]) -> None:
    args = ["cleanup", "--run-id", "", "--docker-config", str(fake["config"])]
    res = _run(*args, env=_fake_env(fake))
    assert res.returncode != 0
    assert "run-id" in res.stderr.lower()
    assert "calls" not in _state(fake)


def test_purge_images_removes_only_recorded_and_still_labelled_ids(
    fake: dict[str, Path],
) -> None:
    state_dir = fake["tmp"] / "compat-state"
    state_dir.mkdir()
    records = [
        {"id": "sha256:ours", "tag": "harness-compat:local"},
        {"id": "sha256:yours", "tag": "harness-compat:local"},
        {"id": "sha256:gone", "tag": "harness-compat:local"},
    ]
    (state_dir / "images.json").write_text(json.dumps(records))
    args = ["purge-images", "--state", str(state_dir), "--docker-config", str(fake["config"])]
    res = _run(*args, env=_fake_env(fake))
    assert res.returncode == 0, res.stderr
    images = _state(fake)["images"]
    assert "sha256:ours" not in images
    assert "sha256:yours" in images
    out = res.stdout.lower()
    assert "sha256:gone" in out and "not found" in out
    assert "sha256:yours" in out and "not labelled" in out
    remaining = json.loads((state_dir / "images.json").read_text())
    assert {"id": "sha256:yours", "tag": "harness-compat:local"} in remaining
    assert all(r["id"] != "sha256:ours" for r in remaining)


# ── deps volume ownership and preflight hardening, with a fake docker ────────


def _set(fake: dict[str, Path], **changes: Any) -> None:
    state = _state(fake)
    state.update(changes)
    fake["state"].write_text(json.dumps(state))


def _python_project(tmp_path: Path) -> Path:
    proj = tmp_path / "pyproj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    (proj / "uv.lock").write_text("version = 1\n")
    return proj


def _prepare_deps(fake: dict[str, Path], run_id: str) -> subprocess.CompletedProcess[str]:
    args = [
        "prepare-deps", "--repo", str(_python_project(fake["tmp"])), "--workdir", ".",
        "--scratch", str(fake["tmp"] / "scratch"), "--run-id", run_id,
        "--uid", "1000", "--gid", "1000", "--image", "harness-compat:t",
        "--docker-config", str(fake["config"]),
    ]  # fmt: skip
    return _run(*args, env=_fake_env(fake))


def test_prepare_deps_refuses_a_volume_that_already_exists(fake: dict[str, Path]) -> None:
    """docker volume create silently REUSES an existing volume: never touch a foreign one."""
    state = _state(fake)
    state["volumes"]["harness-compat-deps-runz"] = {"owner": "someone-else"}
    fake["state"].write_text(json.dumps(state))
    res = _prepare_deps(fake, "runz")
    assert res.returncode != 0
    assert "exists" in res.stderr.lower()
    calls = _state(fake)["calls"]
    assert not any(c[2:4] == ["volume", "create"] or c[2:3] == ["run"] for c in calls)
    assert _state(fake)["volumes"]["harness-compat-deps-runz"] == {"owner": "someone-else"}


def test_prepare_deps_verifies_the_labels_of_the_volume_it_created(
    fake: dict[str, Path],
) -> None:
    _set(fake, create_drops_labels=True)
    res = _prepare_deps(fake, "runy")
    assert res.returncode != 0
    assert "label" in res.stderr.lower()
    assert not any(c[2:3] == ["run"] for c in _state(fake)["calls"])


def _preflight(fake: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    state = _state(fake)
    state["images"]["harness-compat:t"] = {
        "Config": {
            "Env": ["PATH=/usr/bin"],
            "Labels": {"harness-compat": "1"},
            "Entrypoint": ["/usr/local/bin/compat-entrypoint"],
        }
    }
    fake["state"].write_text(json.dumps(state))
    args = ["preflight", "--image", "harness-compat:t", "--docker-config", str(fake["config"])]
    return _run(*args, env=_fake_env(fake))


def test_preflight_runs_a_locked_down_tool_check_in_the_image(fake: dict[str, Path]) -> None:
    res = _preflight(fake)
    assert res.returncode == 0, res.stderr
    run = next(c for c in _state(fake)["calls"] if c[2:3] == ["run"])
    assert run[run.index("--network") + 1] == "none"
    assert "--read-only" in run and "--rm" in run
    assert run[run.index("--cap-drop") + 1] == "ALL"
    joined = " ".join(run)
    for tool in ("bash", "make", "git", "gcc", "python3", "uv", "norminette", "valgrind"):
        assert tool in joined, tool


def test_preflight_fails_when_the_image_lacks_a_tool(fake: dict[str, Path]) -> None:
    _set(fake, run_exit=1, run_stdout="missing: valgrind\n")
    res = _preflight(fake)
    assert res.returncode != 0
    assert "tool" in res.stderr.lower()


def test_preflight_refuses_a_non_native_engine(fake: dict[str, Path]) -> None:
    _set(fake, os="Docker Desktop")
    res = _preflight(fake)
    assert res.returncode != 0
    assert "native" in res.stderr.lower()
