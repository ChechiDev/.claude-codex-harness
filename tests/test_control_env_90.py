"""Issue #90: control variables from the environment do not steer a gate run.

HARNESS_CODEX_SANDBOX / _MODE / _NETWORK, HARNESS_GH and HARNESS_AUTO_PR are
ignored by review_with_codex.sh (one warning names each); the same setting in
harness.toml ([codex], [pr]) takes effect. The Bash guard blocks an inline
assignment of these names in a command that runs the harness gates.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import LEAF_ID, Plan, Project, bash_payload, write_control

TOOLKIT = Path(__file__).resolve().parents[1]
CONTROL_VARS = (
    "HARNESS_CODEX_SANDBOX",
    "HARNESS_CODEX_MODE",
    "HARNESS_CODEX_NETWORK",
    "HARNESS_GH",
    "HARNESS_AUTO_PR",
)


def _with_config(project: Project, text: str) -> None:
    project.write("harness.toml", text)
    project.commit_all("config")


def _plan_gate(project: Project, **env: str) -> subprocess.CompletedProcess[str]:
    return project.run(
        "review_with_codex.sh", None, "plan", LEAF_ID, env={"UV_CACHE_DIR": "/tmp/u", **env}
    )


@pytest.mark.e2e
def test_env_sandbox_is_ignored_and_named(project: Project) -> None:
    _with_config(project, '[codex]\nmode = "manual"\n')
    res = _plan_gate(project, HARNESS_CODEX_SANDBOX="danger-full-access")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "--sandbox workspace-write" in res.stdout
    assert "danger-full-access" not in res.stdout
    assert "HARNESS_CODEX_SANDBOX" in res.stderr
    assert "harness.toml" in res.stderr


@pytest.mark.e2e
def test_env_network_is_ignored(project: Project) -> None:
    _with_config(project, '[codex]\nmode = "manual"\n')
    res = _plan_gate(project, HARNESS_CODEX_NETWORK="1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "--sandbox workspace-write" in res.stdout
    assert "network_access=true" not in res.stdout
    assert "HARNESS_CODEX_NETWORK" in res.stderr


@pytest.mark.e2e
def test_env_manual_mode_is_ignored(project: Project, tmp_path: Path) -> None:
    # No codex on PATH: the auto mode fails instead of printing the manual command.
    res = _plan_gate(project, HARNESS_CODEX_MODE="manual", PATH="/usr/bin:/bin")
    assert "MANUAL MODE" not in res.stdout
    assert res.returncode != 0
    assert "codex is not in PATH" in res.stdout + res.stderr
    assert "HARNESS_CODEX_MODE" in res.stderr


@pytest.mark.e2e
def test_each_variable_warns_once(project: Project) -> None:
    _with_config(project, '[codex]\nmode = "manual"\n')
    res = _plan_gate(project, **dict.fromkeys(CONTROL_VARS, "0"))
    for name in CONTROL_VARS:
        assert res.stderr.count(f"{name} from the environment") == 1, (name, res.stderr)


@pytest.mark.e2e
def test_toml_sandbox_and_network_take_effect(project: Project) -> None:
    _with_config(project, '[codex]\nmode = "manual"\nnetwork = true\n')
    res = _plan_gate(project)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "network_access=true" in res.stdout
    assert "HARNESS_" not in res.stderr
    _with_config(project, '[codex]\nmode = "manual"\nsandbox = "danger-full-access"\n')
    res = _plan_gate(project)
    assert "--sandbox danger-full-access" in res.stdout


@pytest.mark.e2e
def test_bad_codex_config_fails_closed(project: Project) -> None:
    _with_config(project, '[codex]\nmode = "manual"\nsandbox = "everything"\n')
    res = _plan_gate(project)
    assert res.returncode != 0
    assert "MANUAL MODE" not in res.stdout
    assert "harness.toml" in res.stdout + res.stderr


# ── _config.py ───────────────────────────────────────────────────────────────


def _control(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(TOOLKIT / "lib/_config.py"), "control"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")},
    )


def test_control_defaults(tmp_path: Path) -> None:
    res = _control(tmp_path)
    assert res.returncode == 0, res.stderr
    assert res.stdout.split("\t") == ["auto", "workspace-write", "0", "1", "gh\n"]


def test_control_reads_codex_and_pr(tmp_path: Path) -> None:
    (tmp_path / "harness.toml").write_text(
        '[codex]\nmode = "manual"\nsandbox = "read-only"\nnetwork = true\n'
        '[pr]\nauto = false\ngh = "/opt/gh"\n'
    )
    res = _control(tmp_path)
    assert res.stdout.split("\t") == ["manual", "read-only", "1", "0", "/opt/gh\n"]


@pytest.mark.parametrize(
    "text",
    [
        '[codex]\nmode = "x"\n',
        '[codex]\nsandbox = "x"\n',
        '[codex]\nnetwork = "1"\n',
        "[pr]\nauto = 1\n",
        "[pr]\ngh = 3\n",
        '[pr]\ngh = "a\tb"\n',
    ],
)
def test_control_bad_values_fail_closed(tmp_path: Path, text: str) -> None:
    (tmp_path / "harness.toml").write_text(text)
    res = _control(tmp_path)
    assert res.returncode != 0
    assert "harness.toml" in res.stderr


# ── _pr.py ───────────────────────────────────────────────────────────────────


def _pr_due(plan: Plan, **env: str) -> int:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    return subprocess.run(
        ["python3", str(TOOLKIT / "lib/_pr.py"), "pr-due", "1.1.1"],
        cwd=plan.root,
        text=True,
        capture_output=True,
        env={**clean, **env},
        check=False,
    ).returncode


def test_pr_due_ignores_env_auto_pr(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01").sync()
    assert _pr_due(plan, HARNESS_AUTO_PR="0") == 0


def test_pr_due_respects_pr_auto_false(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01")
    plan.config("[pr]\nauto = false\n").sync()
    assert _pr_due(plan) == 1


# ── Bash guard ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", CONTROL_VARS)
@pytest.mark.parametrize(
    "tail", ["./harness/review_with_codex.sh code 1.1.1", "make code ID=1.1.1"]
)
def test_guard_blocks_inline_control_assignment(project: Project, name: str, tail: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(f"{name}=x {tail}"))
    assert res.returncode == 2, res.stdout + res.stderr
    assert name in res.stderr
    assert "harness.toml" in res.stderr


def test_guard_blocks_append_assignment(project: Project) -> None:
    cmd = "HARNESS_CODEX_SANDBOX+=danger-full-access make code ID=1.1.1"
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 2, res.stdout + res.stderr


def test_guard_allows_unrelated_commands(project: Project) -> None:
    res = project.run("guard_bash.sh", bash_payload("HARNESS_GH=x uv run pytest -q"))
    assert res.returncode == 0, res.stdout + res.stderr


# ── the PR step at the last close ────────────────────────────────────────────


@pytest.mark.e2e
def test_env_gh_and_auto_pr_do_not_change_the_pr_step(project: Project, tmp_path: Path) -> None:
    from test_pr import _close, _fake_tools, _real_check

    other = tmp_path / "other-gh"
    other.write_text(
        f'#!/usr/bin/env bash\necho "$*" >> {tmp_path / "other.log"}\n'
        'if [ "$1 $2" = "pr view" ]; then exit 1; fi\n'
    )
    other.chmod(0o755)
    env = _real_check(project, _fake_tools(project, tmp_path))
    res = _close(project, {**env, "HARNESS_GH": str(other), "HARNESS_AUTO_PR": "0"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert "pr create" in (tmp_path / "gh.log").read_text()  # the PATH gh, PR opened
    assert not (tmp_path / "other.log").exists()
    assert "HARNESS_GH from the environment" in res.stderr
    assert "HARNESS_AUTO_PR from the environment" in res.stderr


@pytest.mark.e2e
def test_toml_gh_takes_effect_in_the_pr_step(project: Project, tmp_path: Path) -> None:
    from test_pr import _close, _fake_tools, _real_check

    other = tmp_path / "other-gh"
    other.write_text(
        f'#!/usr/bin/env bash\necho "$*" >> {tmp_path / "other.log"}\n'
        'if [ "$1 $2" = "pr view" ]; then exit 1; fi\n'
    )
    other.chmod(0o755)
    env = _real_check(project, _fake_tools(project, tmp_path))
    write_control(project.root, f'[pr]\ngh = "{other}"\n')
    res = _close(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "pr create" in (tmp_path / "other.log").read_text()
    assert not (tmp_path / "gh.log").exists()
