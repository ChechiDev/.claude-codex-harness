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
        (_side(2, "boom"), _side(2, "boom"), "still-failing", 0),
    ],
)
def test_report_verdict_by_exit_codes(
    tmp_path: Path, base: dict[str, Any], cand: dict[str, Any], verdict: str, code: int
) -> None:
    rc, data = _report(tmp_path, {"proj": {"baseline": base, "candidate": cand}})
    assert data["projects"]["proj"]["verdict"] == verdict
    assert rc == code


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


# ── Stop cache in the copy ───────────────────────────────────────────────────


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
