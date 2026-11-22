"""Parallel-session launch (issue #101): harness.toml [sessions] + harness/session_launch.sh.

The launcher resolves the model and effort of a parallel Claude session from the
`files:` of the task/subtask it will execute: sonnet/medium by default, opus/high when
a file matches [sessions] high_risk_paths. It prints the exact `claude` command, with
every word shell-quoted, so nothing in a task title reaches the shell.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

import _config
import pytest
from conftest import TOOLKIT

LAUNCHER = TOOLKIT / "lib" / "session_launch.sh"


def _features(root: Path, title: str = "Add user entity") -> None:
    data: dict[str, Any] = {
        "phases": [{"id": "01", "title": "Phase", "status": "pending"}],
        "features": [
            {
                "id": "1.1",
                "phase": "01",
                "title": "Users",
                "subtasks": [
                    {
                        "id": "1.1.1",
                        "title": title,
                        "status": "pending",
                        "files": ["src/pkg/domain/user.py", "tests/unit/test_user.py"],
                        "spec": "docs/plan/phase-01-p/1.1.1-s.md",
                    },
                    {
                        "id": "1.1.2",
                        "title": "User migration",
                        "status": "pending",
                        "files": ["./migrations/versions/0001_users.py"],
                        "spec": "docs/plan/phase-01-p/1.1.2-s.md",
                    },
                ],
            }
        ],
        "issues": [
            {
                "id": "ISS-7",
                "title": "Fix login",
                "type": "fix",
                "subtasks": [
                    {
                        "id": "ISS-7.1",
                        "title": "Fix the login guard",
                        "status": "pending",
                        "files": ["src/pkg/auth/guard.py"],
                        "spec": "docs/plan/issues/ISS-7-fix/ISS-7.1-s.md",
                    }
                ],
            }
        ],
    }
    (root / "feature_list.json").write_text(json.dumps(data), encoding="utf-8")


def _toml(root: Path, text: str) -> None:
    (root / "harness.toml").write_text(text, encoding="utf-8")


def _launch(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _argv(res: subprocess.CompletedProcess[str]) -> list[str]:
    assert res.returncode == 0, res.stderr
    return shlex.split(res.stdout.strip())


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


RISKY = '[sessions]\nhigh_risk_paths = ["migrations/**", "src/*/auth/*.py"]\n'


# ── [sessions] in harness.toml ───────────────────────────────────────────────


def test_defaults_without_a_sessions_section(tmp_path: Path) -> None:
    cfg = _config.load(tmp_path)
    assert (cfg.session_model, cfg.session_effort) == ("sonnet", "medium")
    assert (cfg.session_high_risk_model, cfg.session_high_risk_effort) == ("opus", "high")
    assert cfg.session_high_risk_paths == ()


def test_sessions_section_is_read(tmp_path: Path) -> None:
    _toml(
        tmp_path,
        '[sessions]\nmodel = "haiku"\neffort = "low"\nhigh_risk_model = "claude-opus-5-5"\n'
        'high_risk_effort = "max"\nhigh_risk_paths = ["lib/_guard.py", "migrations/**"]\n',
    )
    cfg = _config.load(tmp_path)
    assert (cfg.session_model, cfg.session_effort) == ("haiku", "low")
    assert (cfg.session_high_risk_model, cfg.session_high_risk_effort) == ("claude-opus-5-5", "max")
    assert cfg.session_high_risk_paths == ("lib/_guard.py", "migrations/**")


@pytest.mark.parametrize(
    "body",
    [
        "sessions = 1",
        '[sessions]\neffort = "huge"',
        '[sessions]\nhigh_risk_effort = "turbo"',
        "[sessions]\neffort = 3",
        '[sessions]\nmodel = ""',
        '[sessions]\nmodel = "opus --dangerously-skip-permissions"',
        "[sessions]\nmodel = \"opus'; rm -rf ~; '\"",
        '[sessions]\nhigh_risk_model = "-x"',
        "[sessions]\nhigh_risk_model = 5",
        '[sessions]\nhigh_risk_paths = "migrations/**"',
        '[sessions]\nhigh_risk_paths = [""]',
        "[sessions]\nhigh_risk_paths = [1]",
        '[sessions]\nhigh_risk_paths = ["/etc/passwd"]',
        '[sessions]\nhigh_risk_paths = ["../outside/**"]',
        '[sessions]\nhigh_risk_paths = ["a\\nb"]',
        # Glob syntax the matcher does not support must not be silently literal.
        '[sessions]\nhigh_risk_paths = ["src/[ab]/**"]',
        '[sessions]\nhigh_risk_paths = ["src/{a,b}/**"]',
        '[sessions]\nmodel = "sonnet\\n"',
        '[sessions]\nhigh_risk_paths = ["src/\\u001bx\\u0007/**"]',
        # A typo must not silently drop the escalation.
        '[sessions]\nhigh_risk_path = ["migrations/**"]',
    ],
)
def test_invalid_sessions_values_fail_closed(tmp_path: Path, body: str) -> None:
    _toml(tmp_path, body + "\n")
    with pytest.raises(_config.ConfigError, match="sessions"):
        _config.load(tmp_path)


def test_invalid_sessions_makes_the_launcher_fail_closed(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(tmp_path, '[sessions]\neffort = "huge"\n')
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode != 0
    assert res.stdout == ""
    assert "[sessions] effort" in res.stderr


# ── resolution ───────────────────────────────────────────────────────────────


def test_default_resolution_is_sonnet_medium(tmp_path: Path) -> None:
    _features(tmp_path)
    argv = _argv(_launch(tmp_path, "1.1.1"))
    assert argv[0] == "claude"
    assert _flag(argv, "--model") == "sonnet"
    assert _flag(argv, "--effort") == "medium"
    assert _flag(argv, "--permission-mode") == "auto"


def test_no_high_risk_match_stays_default(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(tmp_path, RISKY)
    argv = _argv(_launch(tmp_path, "1.1.1"))
    assert (_flag(argv, "--model"), _flag(argv, "--effort")) == ("sonnet", "medium")


def test_a_subtask_matching_high_risk_paths_escalates(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(tmp_path, RISKY)
    res = _launch(tmp_path, "1.1.2")  # ./migrations/... normalizes to migrations/...
    argv = _argv(res)
    assert (_flag(argv, "--model"), _flag(argv, "--effort")) == ("opus", "high")
    assert "migrations/versions/0001_users.py" in res.stderr


def test_single_star_does_not_cross_directories(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(tmp_path, '[sessions]\nhigh_risk_paths = ["src/*.py"]\n')
    argv = _argv(_launch(tmp_path, "1.1.1"))
    assert _flag(argv, "--model") == "sonnet"


def test_issue_subtask_escalates(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(tmp_path, RISKY)
    argv = _argv(_launch(tmp_path, "ISS-7.1"))
    assert _flag(argv, "--model") == "opus"


def test_a_task_escalates_when_any_of_its_subtasks_matches(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(tmp_path, RISKY)
    argv = _argv(_launch(tmp_path, "1.1"))
    assert (_flag(argv, "--model"), _flag(argv, "--effort")) == ("opus", "high")


def test_an_issue_resolves_from_its_subtasks(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(tmp_path, RISKY)
    argv = _argv(_launch(tmp_path, "ISS-7"))
    assert _flag(argv, "--model") == "opus"


def test_custom_models_and_efforts_are_used(tmp_path: Path) -> None:
    _features(tmp_path)
    _toml(
        tmp_path,
        '[sessions]\nmodel = "haiku"\neffort = "low"\nhigh_risk_model = "fable"\n'
        'high_risk_effort = "xhigh"\nhigh_risk_paths = ["migrations/**"]\n',
    )
    low = _argv(_launch(tmp_path, "1.1.1"))
    high = _argv(_launch(tmp_path, "1.1.2"))
    assert (_flag(low, "--model"), _flag(low, "--effort")) == ("haiku", "low")
    assert (_flag(high, "--model"), _flag(high, "--effort")) == ("fable", "xhigh")


def test_an_id_from_the_plan_must_be_well_formed(tmp_path: Path) -> None:
    _features(tmp_path)
    data = json.loads((tmp_path / "feature_list.json").read_text())
    data["features"][0]["subtasks"][0]["id"] = "1.1.1\x1b[2J"
    (tmp_path / "feature_list.json").write_text(json.dumps(data))
    res = _launch(tmp_path, "1.1.1\x1b[2J")
    assert res.returncode != 0
    assert res.stdout == ""
    assert "\x1b" not in res.stderr


def test_a_malformed_sibling_id_is_not_printed(tmp_path: Path) -> None:
    _features(tmp_path)
    data = json.loads((tmp_path / "feature_list.json").read_text())
    data["features"][0]["subtasks"][0]["id"] = "1.1.\x1b[2J"
    del data["features"][0]["subtasks"][0]["files"]
    (tmp_path / "feature_list.json").write_text(json.dumps(data))
    res = _launch(tmp_path, "1.1")
    assert res.returncode != 0
    assert "\x1b" not in res.stderr


def test_unknown_id_fails(tmp_path: Path) -> None:
    _features(tmp_path)
    res = _launch(tmp_path, "9.9.9")
    assert res.returncode != 0
    assert res.stdout == ""
    assert "9.9.9" in res.stderr


def test_missing_feature_list_fails(tmp_path: Path) -> None:
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode != 0
    assert "feature_list.json" in res.stderr


def test_usage_without_an_id(tmp_path: Path) -> None:
    res = _launch(tmp_path)
    assert res.returncode == 2
    assert "usage" in res.stderr.lower()


# ── the printed command ──────────────────────────────────────────────────────


def test_default_prompt_names_the_id_and_title(tmp_path: Path) -> None:
    _features(tmp_path)
    prompt = _argv(_launch(tmp_path, "1.1.1"))[-1]
    assert "1.1.1" in prompt
    assert "Add user entity" in prompt


HOSTILE = 'x\'; touch "$PWNED"; echo \'$(touch "$PWNED")`touch "$PWNED"`'


def _run_printed(root: Path, line: str, pwned: Path) -> None:
    """Run the printed line in a real shell with a fake `claude` that records its argv."""
    fakebin = root / "fakebin"
    fakebin.mkdir(exist_ok=True)
    claude = fakebin / "claude"
    claude.write_text('#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "$ARGV_OUT"\n')
    claude.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fakebin}:{os.environ['PATH']}",
        "PWNED": str(pwned),
        "ARGV_OUT": str(root / "argv.bin"),
    }
    subprocess.run(["bash", "-c", line], cwd=root, env=env, check=True, stdin=subprocess.DEVNULL)


def test_a_hostile_title_never_reaches_the_shell(tmp_path: Path) -> None:
    _features(tmp_path, title=HOSTILE)
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode == 0, res.stderr
    pwned = tmp_path / "pwned"
    _run_printed(tmp_path, res.stdout.strip(), pwned)
    assert not pwned.exists()
    argv = (tmp_path / "argv.bin").read_text().split("\0")[:-1]
    assert HOSTILE in argv[-1]
    assert argv[:2] == ["--model", "sonnet"]


def test_control_characters_in_a_title_are_dropped(tmp_path: Path) -> None:
    _features(tmp_path, title="evil\x1b]0;owned\x07\nsecond line")
    prompt = _argv(_launch(tmp_path, "1.1.1"))[-1]
    assert "\x1b" not in prompt and "\x07" not in prompt and "\n" not in prompt


def test_a_custom_prompt_is_quoted(tmp_path: Path) -> None:
    _features(tmp_path)
    res = _launch(tmp_path, "1.1.1", "--prompt", HOSTILE)
    assert _argv(res)[-1] == HOSTILE
    pwned = tmp_path / "pwned"
    _run_printed(tmp_path, res.stdout.strip(), pwned)
    assert not pwned.exists()


def test_a_prompt_that_looks_like_an_option_is_refused(tmp_path: Path) -> None:
    _features(tmp_path)
    res = _launch(tmp_path, "1.1.1", "--prompt", "--dangerously-skip-permissions")
    assert res.returncode != 0
    assert res.stdout == ""


def test_the_worktree_becomes_a_quoted_cd(tmp_path: Path) -> None:
    _features(tmp_path)
    wt = tmp_path / "wt $(touch pwned) 'x'"
    wt.mkdir()
    res = _launch(tmp_path, "1.1.1", "--worktree", str(wt))
    assert res.returncode == 0, res.stderr
    words = shlex.split(res.stdout.strip())
    assert words[:3] == ["cd", str(wt.resolve()), "&&"]
    assert words[3] == "claude"
    _run_printed(tmp_path, res.stdout.strip(), tmp_path / "pwned")
    assert not (tmp_path / "pwned").exists()
    assert not (wt / "pwned").exists()


def test_a_missing_worktree_fails(tmp_path: Path) -> None:
    _features(tmp_path)
    res = _launch(tmp_path, "1.1.1", "--worktree", str(tmp_path / "nope"))
    assert res.returncode != 0
    assert "worktree" in res.stderr


def test_the_launcher_creates_no_worktree(tmp_path: Path) -> None:
    _features(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    _launch(tmp_path, "1.1.1", "--worktree", str(tmp_path / "new-wt"))
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# ── running it ───────────────────────────────────────────────────────────────


def test_exec_runs_claude_in_the_worktree_without_a_shell(tmp_path: Path) -> None:
    _features(tmp_path, title=HOSTILE)
    wt = tmp_path / "wt"
    wt.mkdir()
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    claude = fakebin / "claude"
    claude.write_text('#!/usr/bin/env bash\npwd > "$OUT.cwd"\nprintf "%s\\0" "$@" > "$OUT"\n')
    claude.chmod(0o755)
    out = tmp_path / "argv.bin"
    env = {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}", "OUT": str(out)}
    res = subprocess.run(
        ["bash", str(LAUNCHER), "1.1.1", "--worktree", str(wt), "--exec"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert res.returncode == 0, res.stderr
    argv = out.read_text().split("\0")[:-1]
    assert argv[:4] == ["--model", "sonnet", "--effort", "medium"]
    assert HOSTILE in argv[-1]
    assert Path(out.with_suffix(".bin.cwd").read_text().strip()) == wt.resolve()


def test_hook_receives_the_command_as_one_argument(tmp_path: Path) -> None:
    _features(tmp_path, title=HOSTILE)
    hook = tmp_path / "pane-hook"
    out = tmp_path / "hook.out"
    hook.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > {shlex.quote(str(out))}\n')
    hook.chmod(0o755)
    printed = _launch(tmp_path, "1.1.1").stdout.strip()
    res = _launch(tmp_path, "1.1.1", "--hook", str(hook))
    assert res.returncode == 0, res.stderr
    assert out.read_text().split("\0")[:-1] == [printed]


def test_exec_and_hook_are_exclusive(tmp_path: Path) -> None:
    _features(tmp_path)
    res = _launch(tmp_path, "1.1.1", "--exec", "--hook", "/bin/true")
    assert res.returncode == 2


# ── docs and the leader rule ─────────────────────────────────────────────────


def test_leader_and_workflow_require_the_launcher() -> None:
    for doc in ("agents/leader.md", "lib/WORKFLOW.md"):
        text = (TOOLKIT / doc).read_text(encoding="utf-8")
        assert "harness/session_launch.sh" in text, doc


def test_configuration_documents_sessions() -> None:
    text = (TOOLKIT / "docs/configuration.md").read_text(encoding="utf-8")
    assert "[sessions]" in text
    assert "high_risk_paths" in text


def test_launcher_is_executable() -> None:
    assert os.access(LAUNCHER, os.X_OK)


def test_dot_segments_in_a_glob_are_normalized(tmp_path: Path) -> None:
    _toml(tmp_path, '[sessions]\nhigh_risk_paths = ["./migrations/**"]\n')
    assert _config.load(tmp_path).session_high_risk_paths == ("migrations/**",)
    _features(tmp_path)
    argv = _argv(_launch(tmp_path, "1.1.2"))
    assert _flag(argv, "--model") == "opus"


def _with_files(root: Path, files: list[str]) -> None:
    _features(root)
    data = json.loads((root / "feature_list.json").read_text())
    data["features"][0]["subtasks"][0]["files"] = files
    (root / "feature_list.json").write_text(json.dumps(data))


def test_dot_dot_in_declared_files_is_canonicalized(tmp_path: Path) -> None:
    _with_files(tmp_path, ["src/../migrations/0001.py"])
    _toml(tmp_path, RISKY)
    argv = _argv(_launch(tmp_path, "1.1.1"))
    assert _flag(argv, "--model") == "opus"


@pytest.mark.parametrize("files", ["migrations/0001.py", [1], None, "missing"])
def test_malformed_files_fail_closed(tmp_path: Path, files: object) -> None:
    _features(tmp_path)
    data = json.loads((tmp_path / "feature_list.json").read_text())
    data["features"][0]["subtasks"][0]["files"] = files
    if files == "missing":
        del data["features"][0]["subtasks"][0]["files"]
    (tmp_path / "feature_list.json").write_text(json.dumps(data))
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode != 0
    assert res.stdout == ""


def test_diagnostics_drop_control_characters(tmp_path: Path) -> None:
    _features(tmp_path)
    assert "\x1b" not in _launch(tmp_path, "9.9\x1b[2J").stderr
    res = _launch(tmp_path, "1.1.1", "--worktree", str(tmp_path / "no\x1b[2Jpe"))
    assert res.returncode != 0
    assert "\x1b" not in res.stderr


def test_control_characters_in_declared_files_fail_closed(tmp_path: Path) -> None:
    _with_files(tmp_path, ["src/auth\nextra/guard\x1b[2J.py"])
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode != 0
    assert res.stdout == ""
    assert "\x1b" not in res.stderr


@pytest.mark.parametrize("bad", ["../outside.py", "/etc/passwd", "src/../../x.py"])
def test_declared_files_outside_the_project_fail_closed(tmp_path: Path, bad: str) -> None:
    _with_files(tmp_path, [bad])
    res = _launch(tmp_path, "1.1.1")
    assert res.returncode != 0
    assert res.stdout == ""
    assert "outside the project" in res.stderr
