"""Releases + CHANGELOG, delivery 1: the changelog of a new install and the activation
of a started project (install.sh --changelog), with a fake gh and a local bare origin."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT

pytestmark = pytest.mark.e2e  # real install.sh (#68)

IDENTITY = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}
OWNED = ("CHANGELOG.md", "changelog.d/README.md", "docs/releases.md", ".harness/changelog.json")
BRANCH = "chore/harness-changelog"

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text())
argv = sys.argv[1:]
state["calls"].append(argv)
prs = state["prs"]
rc = 0
if argv[:2] == ["pr", "view"]:
    pr = prs.get(argv[2])
    if pr is None:
        rc = 1
    else:
        print(pr["state"])
elif argv[:2] == ["pr", "create"]:
    head = argv[argv.index("--head") + 1]
    prs[head] = {"state": "OPEN", "base": argv[argv.index("--base") + 1]}
    print(f"https://github.com/acme/app/pull/{len(prs)}")
elif argv[:2] == ["pr", "checks"]:
    origin, ref = os.environ["FAKE_ORIGIN"], f"refs/heads/{argv[2]}"
    if os.environ.get("FAKE_GH_PUSH_DURING_CHECKS"):  # someone pushes to the branch
        git = ["git", "--git-dir", origin]
        tree = subprocess.run([*git, "rev-parse", f"{ref}^{{tree}}"], capture_output=True,
                              text=True, check=True).stdout.strip()
        new = subprocess.run([*git, "commit-tree", tree, "-p", ref, "-m", "late push"],
                             capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run([*git, "update-ref", ref, new], check=True)
    if os.environ.get("FAKE_GH_CHECKS_RC") == "none":  # a repo without CI (real gh text)
        print(f"no checks reported on the '{argv[2]}' branch", file=sys.stderr)
        rc = 1
    else:
        rc = int(os.environ.get("FAKE_GH_CHECKS_RC", "0"))
        print("ci  pass" if rc == 0 else "ci  fail")
elif argv[:2] == ["pr", "merge"]:
    pr = prs[argv[2]]
    origin = os.environ["FAKE_ORIGIN"]
    tip = subprocess.run(["git", "--git-dir", origin, "rev-parse", f"refs/heads/{argv[2]}"],
                         capture_output=True, text=True, check=True).stdout.strip()
    if "--match-head-commit" in argv and argv[argv.index("--match-head-commit") + 1] != tip:
        print("GraphQL: Head branch was modified. Review and try the merge again.",
              file=sys.stderr)
        rc = 1
    else:
        subprocess.run(["git", "--git-dir", origin, "update-ref", f"refs/heads/{pr['base']}",
                        tip], check=True)
        pr["state"] = "MERGED"
else:
    print(f"fake gh: unsupported {argv}", file=sys.stderr)
    rc = 1
state_path.write_text(json.dumps(state))
sys.exit(rc)
"""


def _env(tmp_path: Path) -> dict[str, str]:
    cfg = tmp_path / "empty.gitconfig"
    cfg.touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    state = tmp_path / "gh.json"
    state.write_text(json.dumps({"calls": [], "prs": {}}))
    base = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "HARNESS_"))}
    return {
        **base,
        **IDENTITY,
        "CLAUDE_HOME": str(tmp_path / "claude-home"),
        "GIT_CONFIG_GLOBAL": str(cfg),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": f"{bin_dir}:{base['PATH']}",
        "FAKE_GH_STATE": str(state),
        "FAKE_ORIGIN": str(tmp_path / "origin.git"),
    }


def _run(
    cmd: list[str], env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _origin_show(env: dict[str, str], spec: str) -> str:
    return _run(["git", "--git-dir", env["FAKE_ORIGIN"], "show", spec], env).stdout


def _install(dest: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(TOOLKIT / "install.sh"), str(dest), *args], env)


def _git(root: Path, env: dict[str, str], *args: str) -> str:
    res = _run(["git", *args], env, cwd=root)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def _gh_calls(env: dict[str, str], verb: str) -> list[list[str]]:
    calls = json.loads(Path(env["FAKE_GH_STATE"]).read_text())["calls"]
    return [c for c in calls if c[:2] == ["pr", verb]]


@pytest.mark.parametrize("lang", ["python", "c"])
def test_new_install_activates_the_changelog(tmp_path: Path, lang: str) -> None:
    if lang == "c" and not shutil.which("gcc"):
        pytest.skip("needs gcc")
    dest, env = tmp_path / "app", _env(tmp_path)
    args = ["--lang", "python"] if lang == "python" else ["--c", "demo"]
    res = _install(dest, env, *args)
    assert res.returncode == 0, res.stdout + res.stderr
    for rel in OWNED:
        assert (dest / rel).is_file(), rel
    record = json.loads((dest / ".harness/changelog.json").read_text())
    assert record["since"] is None and record["finished"] == []
    source = "pyproject.toml" if lang == "python" else "VERSION"
    assert f"- version source: {source}" in (dest / "docs/releases.md").read_text()
    assert "## [Unreleased]" in (dest / "CHANGELOG.md").read_text()
    manifest = TOOLKIT / ("templates" if lang == "python" else "templates-c") / "MANIFEST.toml"
    assert "CHANGELOG" not in manifest.read_text()
    assert "changelog not activated" not in res.stdout


def test_force_reinstall_never_overwrites_the_changelog(tmp_path: Path) -> None:
    dest, env = tmp_path / "app", _env(tmp_path)
    assert _install(dest, env, "--lang", "python").returncode == 0
    (dest / "CHANGELOG.md").write_text("# Ours\n")
    (dest / "changelog.d" / "1.1.md").write_text("- id: 1.1\n")
    res = _install(dest, env, "--lang", "python", "--force")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (dest / "CHANGELOG.md").read_text() == "# Ours\n"
    assert (dest / "changelog.d" / "1.1.md").read_text() == "- id: 1.1\n"


def _started(tmp_path: Path, env: dict[str, str], changelog: str | None = None) -> Path:
    """A project installed before the changelog: a done task 1.1, task 1.2 in progress,
    pushed to a bare origin, the user on a task branch."""
    dest = tmp_path / "app"
    assert _install(dest, env, "--lang", "python").returncode == 0
    for rel in OWNED:
        (dest / rel).unlink()
    (dest / "changelog.d").rmdir()
    if changelog is not None:
        (dest / "CHANGELOG.md").write_text(changelog)
    plan = {
        "phases": [{"id": "01", "title": "p", "tasks": ["1.1", "1.2"]}],
        "features": [
            {"id": "1.1", "phase": "01", "subtasks": [{"id": "1.1.1", "status": "done"}]},
            {"id": "1.2", "phase": "01", "subtasks": [{"id": "1.2.1", "status": "in_progress"}]},
        ],
    }
    (dest / "feature_list.json").write_text(json.dumps(plan))
    _git(dest, env, "add", "-A")
    _git(dest, env, "commit", "-qm", "work before the changelog")
    _git(dest, env, "switch", "-q", "main")
    _git(dest, env, "merge", "-q", "chore/project-setup")
    origin = Path(env["FAKE_ORIGIN"])
    _git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(dest, env, "remote", "add", "origin", str(origin))
    _git(dest, env, "push", "-q", "origin", "main")
    _git(dest, env, "remote", "set-head", "origin", "main")
    _git(dest, env, "switch", "-qc", "feat/1.2-x")
    return dest


def _origin_has(env: dict[str, str], rel: str, ref: str = "main") -> bool:
    res = _run(["git", "--git-dir", env["FAKE_ORIGIN"], "cat-file", "-e", f"{ref}:{rel}"], env)
    return res.returncode == 0


def test_reinstall_of_a_started_project_does_not_activate_it(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    res = _install(dest, env, "--lang", "python")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (dest / ".harness/changelog.json").exists()
    assert not (dest / "CHANGELOG.md").exists()


def test_activation_dry_run_changes_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    head = _git(dest, env, "rev-parse", "HEAD")
    res = _install(dest, env, "--changelog")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "add     CHANGELOG.md" in res.stdout and "finished (no fragment): 1.1" in res.stdout
    assert "--apply" in res.stdout
    assert _git(dest, env, "rev-parse", "HEAD") == head
    assert _git(dest, env, "branch", "--list", BRANCH) == ""
    assert _gh_calls(env, "create") == []


def test_activation_opens_and_merges_its_pr(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    head, status = _git(dest, env, "rev-parse", "HEAD"), _git(dest, env, "status", "--short")
    base = _git(dest, env, "rev-parse", "origin/main")
    res = _install(dest, env, "--changelog", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    for rel in OWNED:
        assert _origin_has(env, rel), rel
    message = _run(
        ["git", "--git-dir", env["FAKE_ORIGIN"], "log", "-1", "--format=%B", "main"], env
    ).stdout
    assert message.startswith("chore: activate the harness changelog")
    assert "Co-Authored-By" not in message and "Claude" not in message
    record = json.loads(_origin_show(env, "main:.harness/changelog.json"))
    assert record["since"] == base and record["finished"] == ["1.1"]
    changelog = _origin_show(env, "main:CHANGELOG.md")
    assert f"recorded from commit {base[:12]}" in changelog
    assert len(_gh_calls(env, "create")) == 1 and len(_gh_calls(env, "merge")) == 1
    # The merge is pinned to the pushed commit (review rel1rev #6).
    merge = _gh_calls(env, "merge")[0]
    pushed = _run(["git", "--git-dir", env["FAKE_ORIGIN"], "rev-parse", BRANCH], env).stdout
    assert merge[merge.index("--match-head-commit") + 1] == pushed.strip()
    # The user's checkout is untouched: same branch, same commit, no worktree left.
    assert _git(dest, env, "rev-parse", "--abbrev-ref", "HEAD") == "feat/1.2-x"
    assert _git(dest, env, "rev-parse", "HEAD") == head
    assert _git(dest, env, "status", "--short") == status
    assert len(_git(dest, env, "worktree", "list").splitlines()) == 1
    # Relaunched: a no-op.
    again = _install(dest, env, "--changelog", "--apply", "--yes")
    assert again.returncode == 0 and "already activated" in again.stdout
    assert len(_gh_calls(env, "create")) == 1


def test_red_checks_never_merge_and_the_relaunch_reuses_everything(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    red = {**env, "FAKE_GH_CHECKS_RC": "1"}
    res = _install(dest, red, "--changelog", "--apply", "--yes")
    assert res.returncode == 1 and "not merged" in res.stdout
    assert not _origin_has(env, ".harness/changelog.json")
    assert _gh_calls(env, "merge") == []
    tip = _git(dest, env, "rev-parse", BRANCH)
    res = _install(dest, env, "--changelog", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "already committed" in res.stdout
    assert _git(dest, env, "rev-parse", f"origin/{BRANCH}") == tip
    assert len(_gh_calls(env, "create")) == 1 and len(_gh_calls(env, "merge")) == 1
    assert _origin_has(env, ".harness/changelog.json")


def test_a_repo_without_checks_is_left_to_the_human(tmp_path: Path) -> None:
    """`gh pr checks` fails with "no checks reported": its own message, never merged."""
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    res = _install(dest, {**env, "FAKE_GH_CHECKS_RC": "none"}, "--changelog", "--apply", "--yes")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "no checks" in res.stdout and "not all green" not in res.stdout
    assert "gh pr merge" in res.stdout and "--match-head-commit" in res.stdout
    assert _gh_calls(env, "merge") == []
    assert not _origin_has(env, ".harness/changelog.json")


def test_a_push_after_the_checks_is_never_merged(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    late = {**env, "FAKE_GH_PUSH_DURING_CHECKS": "1"}
    res = _install(dest, late, "--changelog", "--apply", "--yes")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "gh pr merge failed" in res.stderr
    assert not _origin_has(env, ".harness/changelog.json")


def test_activation_keeps_an_existing_changelog(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env, changelog="# History\n\n## 0.3.0\n- Old.\n")
    res = _install(dest, env, "--changelog")
    assert "keep    CHANGELOG.md" in res.stdout and "earlier history is not listed" in res.stdout
    assert "add     CHANGELOG.md" not in res.stdout
    assert _install(dest, env, "--changelog", "--apply", "--yes").returncode == 0
    shown = _origin_show(env, "main:CHANGELOG.md")
    assert shown == "# History\n\n## 0.3.0\n- Old.\n"


def test_apply_without_a_terminal_needs_yes(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    res = _install(dest, env, "--changelog", "--apply")
    assert res.returncode == 1 and "--apply --yes" in res.stderr
    assert _git(dest, env, "branch", "--list", BRANCH) == ""


def test_yes_alone_is_refused(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    res = _install(dest, env, "--changelog", "--yes")
    assert res.returncode == 1 and "--apply" in res.stderr


def test_changelog_is_exclusive_with_migrate(tmp_path: Path) -> None:
    res = _install(tmp_path / "app", _env(tmp_path), "--changelog", "--migrate")
    assert res.returncode == 2 and "exclusive" in res.stderr


def test_activation_refuses_a_foreign_branch_of_the_same_name(tmp_path: Path) -> None:
    env = _env(tmp_path)
    dest = _started(tmp_path, env)
    _git(dest, env, "switch", "-qc", BRANCH, "main")
    (dest / "unrelated.txt").write_text("x\n")
    _git(dest, env, "add", "unrelated.txt")
    _git(dest, env, "commit", "-qm", "unrelated work")
    _git(dest, env, "switch", "-q", "feat/1.2-x")
    res = _install(dest, env, "--changelog", "--apply", "--yes")
    assert res.returncode == 1 and BRANCH in res.stderr
    assert _gh_calls(env, "create") == [] and _gh_calls(env, "merge") == []
    assert (
        _run(
            ["git", "--git-dir", env["FAKE_ORIGIN"], "rev-parse", "-q", "--verify", BRANCH], env
        ).returncode
        != 0
    )


def test_first_install_into_a_repo_with_a_plan_folder_activates(tmp_path: Path) -> None:
    dest, env = tmp_path / "app", _env(tmp_path)
    (dest / "docs" / "plan").mkdir(parents=True)
    (dest / "docs" / "plan" / "PLAN.md").write_text("# Our own plan\n")
    res = _install(dest, env, "--lang", "python")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (dest / ".harness/changelog.json").is_file()
    assert (dest / "docs/plan/PLAN.md").read_text().startswith("# Our own plan\n")
