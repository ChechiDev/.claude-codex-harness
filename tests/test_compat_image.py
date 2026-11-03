"""C0.3 (no Docker needed): image definition, in-container guards, host preflight, build.

Host-side docker calls go through tests/fake_docker.py; the in-container scripts are
exercised directly with bash.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

COMPAT = TOOLKIT / "compat"
RUNNER = COMPAT / "_compat.py"
FAKE_DOCKER = Path(__file__).resolve().parent / "fake_docker.py"
SOCKET = "unix:///var/run/docker.sock"
ENTRYPOINT = ["/usr/local/bin/compat-entrypoint"]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
GOOD_IMAGE: dict[str, Any] = {
    "Id": "sha256:feed",
    "Config": {
        "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"],
        "Labels": {"harness-compat": "1"},
        "Entrypoint": ENTRYPOINT,
    },
}


# ── Dockerfile ───────────────────────────────────────────────────────────────


def _dockerfile() -> list[str]:
    return [ln.strip() for ln in (COMPAT / "Dockerfile").read_text().splitlines()]


def test_every_base_image_is_pinned_by_digest() -> None:
    refs = [ln.split()[1] for ln in _dockerfile() if ln.upper().startswith("FROM ")]
    refs += re.findall(r"--from=(\S+)", "\n".join(_dockerfile()))
    assert refs, "Dockerfile has no FROM"
    for ref in refs:
        assert DIGEST.search(ref), f"not pinned by digest: {ref}"


def test_image_sets_no_env_besides_path() -> None:
    envs = [ln for ln in _dockerfile() if ln.upper().startswith("ENV ")]
    for line in envs:
        assert line.split()[1].split("=")[0] == "PATH", line


def test_image_is_labelled_and_uses_the_guard_entrypoint() -> None:
    text = "\n".join(_dockerfile())
    assert re.search(r"^LABEL\s+harness-compat=\"?1\"?", text, re.M)
    assert f"ENTRYPOINT {json.dumps(ENTRYPOINT)}" in text


def test_image_installs_the_tools_the_checks_need() -> None:
    text = "\n".join(_dockerfile())
    for tool in ("make", "git", "gcc", "bash"):
        assert re.search(rf"\b{tool}\b", text), tool


def test_image_never_fetches_urls_at_build_time() -> None:
    text = "\n".join(_dockerfile()).lower()
    assert "curl " not in text and "wget " not in text
    assert not re.search(r"^add\s+https?://", text, re.M)


# ── in-container guards, run with bash on the host ───────────────────────────


def _bash(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    pairs = [f"{k}={v}" for k, v in env.items()]
    argv = ["/usr/bin/env", "-i", *pairs, "bash", str(COMPAT / script), *args]
    return subprocess.run(argv, text=True, capture_output=True, check=False)


ALLOWED = {
    "PATH": "/usr/bin:/bin",
    "HOSTNAME": "abc123",
    "HOME": "/home/compat",
    "LANG": "C.UTF-8",
    "UV_OFFLINE": "1",
    "UV_NO_SYNC": "1",
    "UV_PYTHON_DOWNLOADS": "never",
}


def test_env_guard_accepts_the_allowlist() -> None:
    res = _bash("guard_env.sh", ALLOWED)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("extra", ["FOO", "DATABASE_URL", "GH_TOKEN", "HTTP_PROXY"])
def test_env_guard_rejects_any_extra_variable(extra: str) -> None:
    res = _bash("guard_env.sh", {**ALLOWED, extra: "x"})
    assert res.returncode != 0
    assert extra in res.stderr


def test_selfcheck_fails_on_a_host_with_network() -> None:
    """The host has interfaces besides lo: the isolation self-check must refuse it."""
    res = _bash("selfcheck.sh", ALLOWED)
    assert res.returncode != 0
    assert "network" in res.stderr.lower()


@pytest.mark.parametrize("script", ["entrypoint.sh", "entrypoint_prep.sh"])
def test_entrypoint_runs_guards_then_creates_home_then_the_command(script: str) -> None:
    text = (COMPAT / script).read_text()
    marks = ("/guard_env.sh", "/selfcheck.sh", 'mkdir -p "$HOME"', 'exec "$@"')
    positions = [text.find(s) for s in marks]
    assert -1 < positions[0] < positions[1] < positions[2] < positions[3], positions


def test_image_ships_valgrind_for_c_projects() -> None:
    text = (COMPAT / "Dockerfile").read_text()
    assert re.search(r"\bvalgrind\b", text)


def test_image_pins_norminette_for_42_c_projects() -> None:
    text = (COMPAT / "Dockerfile").read_text()
    assert "norminette==3.3.60" in text
    assert "--exclude-newer" in text  # transitive deps frozen in time too


# ── host preflight and build, with a fake docker ─────────────────────────────


@pytest.fixture
def fake(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "docker_state.json"
    state.write_text(json.dumps({"engine": "linux", "images": {"harness-compat:t": GOOD_IMAGE}}))
    wrapper = bin_dir / "docker"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_DOCKER}" "{state}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    systemctl = bin_dir / "systemctl"
    fake_systemctl = Path(__file__).resolve().parent / "fake_systemctl.py"
    systemd = tmp_path / "systemd.json"
    systemctl.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{fake_systemctl}" "{systemd}" "$@"\n'
    )
    systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "docker-config"
    config.mkdir()
    return {"bin": bin_dir, "state": state, "config": config, "tmp": tmp_path}


def _set(fake: dict[str, Path], **changes: Any) -> None:
    state = json.loads(fake["state"].read_text())
    state.update(changes)
    fake["state"].write_text(json.dumps(state))


def _calls(fake: dict[str, Path]) -> list[list[str]]:
    calls: list[list[str]] = json.loads(fake["state"].read_text()).get("calls", [])
    return calls


def _compat(fake: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{fake['bin']}:{os.environ['PATH']}",
        "DOCKER_HOST": "tcp://evil:2375",
        "DOCKER_CONTEXT": "remote",
    }
    return subprocess.run(
        [sys.executable, str(RUNNER), *args, "--docker-config", str(fake["config"])],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_preflight_passes_with_a_local_engine_and_a_good_image(fake: dict[str, Path]) -> None:
    res = _compat(fake, "preflight", "--image", "harness-compat:t")
    assert res.returncode == 0, res.stderr
    assert all(call[:2] == ["--host", SOCKET] for call in _calls(fake))


def test_preflight_fails_when_the_engine_is_unreachable(fake: dict[str, Path]) -> None:
    _set(fake, engine=None)
    res = _compat(fake, "preflight", "--image", "harness-compat:t")
    assert res.returncode != 0
    assert "engine" in res.stderr.lower()


def test_preflight_fails_when_the_image_is_missing(fake: dict[str, Path]) -> None:
    res = _compat(fake, "preflight", "--image", "harness-compat:missing")
    assert res.returncode != 0
    assert "build-image" in res.stderr


@pytest.mark.parametrize(
    ("field", "value", "word"),
    [
        ("Env", ["PATH=/usr/bin", "FOO=1"], "env"),
        ("Labels", {}, "label"),
        ("Entrypoint", ["/bin/sh"], "entrypoint"),
    ],
)
def test_preflight_rejects_an_image_that_drifted(
    fake: dict[str, Path], field: str, value: Any, word: str
) -> None:
    bad = json.loads(json.dumps(GOOD_IMAGE))
    bad["Config"][field] = value
    _set(fake, images={"harness-compat:t": bad})
    res = _compat(fake, "preflight", "--image", "harness-compat:t")
    assert res.returncode != 0
    assert word in res.stderr.lower()


def test_build_image_labels_tags_and_records_the_image_id(fake: dict[str, Path]) -> None:
    _set(fake, build_id="sha256:0123abcd")
    state_dir = fake["tmp"] / "compat-state"
    res = _compat(fake, "build-image", "--tag", "fresh", "--state", str(state_dir))
    assert res.returncode == 0, res.stderr
    build = next(c for c in _calls(fake) if "build" in c)
    assert build[:3] == ["--host", SOCKET, "build"]
    assert "harness-compat=1" in build
    assert build[build.index("-t") + 1] == "harness-compat:fresh"
    assert build[-1] == str(COMPAT)
    assert "--network" not in build
    recorded = json.loads((state_dir / "images.json").read_text())
    assert {"id": "sha256:0123abcd", "tag": "harness-compat:fresh"} in recorded


def _build(fake: dict[str, Path], state_dir: Path) -> subprocess.CompletedProcess[str]:
    return _compat(fake, "build-image", "--tag", "t", "--state", str(state_dir))


def test_build_image_never_retags_a_foreign_image(fake: dict[str, Path]) -> None:
    """An existing harness-compat:<tag> that C0 did not record is someone else's."""
    _set(fake, images={"harness-compat:t": {"Id": "sha256:theirs", "Config": {"Labels": {}}}})
    res = _build(fake, fake["tmp"] / "compat-state")
    assert res.returncode != 0
    assert "not created by c0" in res.stderr.lower()
    assert not any("build" in c for c in _calls(fake))


def test_build_image_may_rebuild_its_own_recorded_image(fake: dict[str, Path]) -> None:
    state_dir = fake["tmp"] / "compat-state"
    state_dir.mkdir()
    (state_dir / "images.json").write_text(
        json.dumps([{"id": "sha256:ours", "tag": "harness-compat:t"}])
    )
    own = {"Id": "sha256:ours", "Config": {"Labels": {"harness-compat": "1"}}}
    _set(fake, images={"harness-compat:t": own})
    res = _build(fake, state_dir)
    assert res.returncode == 0, res.stderr


def test_build_image_never_moves_a_tag_c0_did_not_record(fake: dict[str, Path]) -> None:
    """The image is ours, but this TAG on it was added by someone else."""
    state_dir = fake["tmp"] / "compat-state"
    state_dir.mkdir()
    (state_dir / "images.json").write_text(
        json.dumps([{"id": "sha256:ours", "tag": "harness-compat:other"}])
    )
    own = {"Id": "sha256:ours", "Config": {"Labels": {"harness-compat": "1"}}}
    _set(fake, images={"harness-compat:t": own})
    res = _build(fake, state_dir)
    assert res.returncode != 0
    assert "not created by c0" in res.stderr.lower()
