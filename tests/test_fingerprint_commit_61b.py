"""Issue #61 items 4 and 5, plus the resume ledger sealer (iseal, follow-up of #46).

- Item 4: the working-tree fingerprint (`_guard.py fingerprint`) hashes a fixed list
  of files that drive the check by content, git-ignored or not.
- Item 5: with HARNESS_ALLOW_COMMIT=1 a commit needs a green record that matches the
  tree being committed (WARN for now); HARNESS_ALLOW_COMMIT=force skips it and logs.
- iseal: `_resume.py begin|end` is a sealer: Claude cannot reset the attempt budget.
- The close message the harness sets aside (.git/harness-close-<id>.msg, #107) is
  read-only for Claude and stays out of the fingerprint.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import LEAF_ID, TOOLKIT, Project, bash_payload, write_payload

BLOCKED = 2
FORCE_LOG = "harness-commit-force.log"

# Item 4: the user's fixed list (2026-11-22). docs/plan/** is a whole tree.
CHECK_INPUTS = [
    "Makefile",
    "GNUmakefile",
    "makefile",
    "harness.toml",
    "feature_list.json",
    "docs/plan/phase-01-users/1.1.1-user-entity.md",
    "docs/plan/deep/nested/notes.md",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "conftest.py",
]


@pytest.fixture(scope="module", autouse=True)
def _toolkit_bytecode() -> None:
    """The fingerprint hashes the toolkit's bytecode too: compile it up front, so a
    parallel test importing a just-changed module cannot move it between two reads."""
    for python in {"python3", sys.executable}:
        subprocess.run(
            [python, "-m", "compileall", "-q", str(TOOLKIT / "lib")], check=False, timeout=120
        )


def _fingerprint(project: Project) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    return subprocess.run(
        ["python3", str(project.root / "harness" / "_guard.py"), "fingerprint"],
        cwd=project.root,
        env={**env, "CLAUDE_PROJECT_DIR": str(project.root)},
        capture_output=True,
        text=True,
        check=False,
    )


def _fp(project: Project) -> str:
    res = _fingerprint(project)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip()
    return res.stdout.strip()


def _ignore(project: Project, *patterns: str) -> None:
    exclude = project.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    old = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    exclude.write_text(old + "".join(f"/{p}\n" for p in patterns), encoding="utf-8")


def _git_path(project: Project, name: str) -> Path:
    res = subprocess.run(
        ["git", "rev-parse", "--git-path", name],
        cwd=project.root,
        capture_output=True,
        text=True,
        check=True,
    )
    return project.root / res.stdout.strip()


def _record_green(project: Project) -> None:
    """The green record run_check.sh --record writes: the bare fingerprint."""
    _git_path(project, "harness-check.ok").write_text(_fp(project), encoding="utf-8")


def _guard(project: Project, cmd: str, allow: str | None) -> subprocess.CompletedProcess[str]:
    env = {} if allow is None else {"HARNESS_ALLOW_COMMIT": allow}
    return project.run("guard_bash.sh", bash_payload(cmd), env=env)


def _warning(res: subprocess.CompletedProcess[str]) -> str:
    """The WARN the hook hands to Claude and the user (exit 0, JSON on stdout)."""
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["systemMessage"] == ctx
    return str(ctx)


# ── item 4: check inputs in the fingerprint ─────────────────────────────────


@pytest.mark.parametrize("rel", CHECK_INPUTS)
def test_an_ignored_check_input_moves_the_fingerprint(project: Project, rel: str) -> None:
    """Git-ignored, so neither status nor diff sees it: the content still counts."""
    top = rel.split("/", 1)[0]
    _ignore(project, top)
    if (project.root / rel).exists():  # feature_list.json is tracked by the fixture
        subprocess.run(["git", "rm", "--cached", "-q", rel], cwd=project.root, check=True)
        project.commit_all("untrack")
        (project.root / rel).unlink()
    absent = _fp(project)
    project.write(rel, "one\n")
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=project.root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert rel not in status.stdout  # really invisible to git
    first = _fp(project)
    project.write(rel, "two\n")
    second = _fp(project)
    assert len({absent, first, second}) == 3, rel
    project.write(rel, "one\n")
    assert _fp(project) == first  # content, not timestamps


def test_an_ignored_file_outside_the_list_does_not_count(project: Project) -> None:
    _ignore(project, "notes.txt", "build")
    before = _fp(project)
    project.write("notes.txt", "scratch\n")
    project.write("build/out.o", "x")
    project.write("src/conftest.py", "x")  # only the root conftest.py is listed
    _ignore(project, "src/conftest.py")
    assert _fp(project) == before


def test_the_fingerprint_is_deterministic(project: Project) -> None:
    _ignore(project, "Makefile", "docs")
    project.write("Makefile", "check:\n\t@true\n")
    project.write("docs/plan/b.md", "b")
    project.write("docs/plan/a.md", "a")
    assert _fp(project) == _fp(project)


def test_a_renamed_plan_file_moves_the_fingerprint(project: Project) -> None:
    """The path is hashed with the content: moving a spec is a different plan."""
    _ignore(project, "docs")
    project.write("docs/plan/a.md", "same")
    before = _fp(project)
    (project.root / "docs/plan/a.md").rename(project.root / "docs/plan/b.md")
    assert _fp(project) != before


def test_an_unreadable_check_input_gives_no_fingerprint(project: Project) -> None:
    """Fail closed: a dangling link among the inputs is never fingerprinted as green."""
    _ignore(project, "Makefile")
    (project.root / "Makefile").symlink_to(project.root / "missing.mk")
    res = _fingerprint(project)
    assert res.returncode == 1 and not res.stdout.strip()


def test_the_set_aside_close_message_stays_out_of_the_fingerprint(project: Project) -> None:
    before = _fp(project)
    _git_path(project, f"harness-close-{LEAF_ID}.msg").write_text("feat: x [1.1.1]\n")
    assert _fp(project) == before


# ── item 5: HARNESS_ALLOW_COMMIT needs a matching green record ──────────────

RECORD_HINT = "green record"


def test_allow_commit_without_a_record_warns(project: Project) -> None:
    res = _guard(project, "git commit -m 'feat: x'", "1")
    msg = _warning(res)
    assert RECORD_HINT in msg and "harness-check.ok" in msg
    assert "HARNESS_ALLOW_COMMIT=force" in msg  # the human valve is named
    assert "run_check.sh --record" in msg  # and how to get a record


def test_allow_commit_with_a_matching_record_is_silent(project: Project) -> None:
    project.write("src/app/domain/user.py", "x = 1\n")
    _record_green(project)
    res = _guard(project, "git add -A && git commit -m 'feat: x'", "1")
    assert res.returncode == 0, res.stderr
    assert RECORD_HINT not in res.stdout


def test_allow_commit_with_a_stale_record_warns(project: Project) -> None:
    _record_green(project)
    project.write("src/app/domain/user.py", "x = 2\n")  # edited after the green run
    msg = _warning(_guard(project, "git commit -am 'feat: x'", "1"))
    assert RECORD_HINT in msg and "does not match" in msg


def test_a_change_to_an_ignored_check_input_makes_the_record_stale(project: Project) -> None:
    _ignore(project, "harness.toml")
    project.write("harness.toml", '[codex]\nmode = "auto"\n')
    _record_green(project)
    project.write("harness.toml", '[codex]\nmode = "manual"\n')
    msg = _warning(_guard(project, "git commit -m 'feat: x'", "1"))
    assert "does not match" in msg


def test_allow_commit_when_the_fingerprint_cannot_be_computed_warns(project: Project) -> None:
    _record_green(project)
    (project.root / ".claude" / "settings.json").symlink_to(project.root / "gone.json")
    msg = _warning(_guard(project, "git commit -m 'feat: x'", "1"))
    assert RECORD_HINT in msg and "cannot" in msg


def test_allow_commit_with_index_flags_warns(project: Project) -> None:
    """assume-unchanged hides an edit from status and the fingerprint (#55)."""
    _record_green(project)
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "feature_list.json"],
        cwd=project.root,
        check=True,
    )
    msg = _warning(_guard(project, "git commit -m 'feat: x'", "1"))
    assert "assume-unchanged" in msg


@pytest.mark.parametrize(
    "cmd", ["bash harness/run_check.sh --record", "./harness/run_check.sh --record"]
)
def test_the_suggested_record_command_is_allowed(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


def test_the_record_warning_joins_the_i8_warnings(project: Project) -> None:
    msg = _warning(_guard(project, "git commit -m 'feat: x' && git push", "1"))
    assert RECORD_HINT in msg and "git push" in msg


def test_no_record_check_for_commands_that_do_not_commit(project: Project) -> None:
    res = _guard(project, "git status && make lint", "1")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == ""


def test_the_record_check_never_writes_the_record(project: Project) -> None:
    _guard(project, "git commit -m 'feat: x'", "1")
    assert not _git_path(project, "harness-check.ok").exists()


def test_force_skips_the_record_and_logs_its_use(project: Project) -> None:
    res = _guard(project, "git commit -m 'feat: x'", "force")
    msg = _warning(res)
    assert "HARNESS_ALLOW_COMMIT=force" in msg and RECORD_HINT in msg
    log = _git_path(project, FORCE_LOG)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project.root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head in lines[0]
    assert "feat: x" not in lines[0]  # the log never copies the command line
    _guard(project, "git commit -m 'feat: y'", "force")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2  # appended


def test_force_with_a_matching_record_still_logs(project: Project) -> None:
    _record_green(project)
    res = _guard(project, "git commit -m 'feat: x'", "force")
    assert res.returncode == 0, res.stderr
    assert _git_path(project, FORCE_LOG).is_file()


def test_force_does_not_lift_the_attribution_block(project: Project) -> None:
    res = _guard(project, "git commit -m x -m 'Co-Authored-By: Bot <b@x>'", "force")
    assert res.returncode == BLOCKED and "attribution" in res.stderr
    assert not _git_path(project, FORCE_LOG).exists()


def test_force_without_a_commit_logs_nothing(project: Project) -> None:
    res = _guard(project, "git status", "force")
    assert res.returncode == 0 and res.stdout.strip() == ""
    assert not _git_path(project, FORCE_LOG).exists()


@pytest.mark.parametrize("value", [None, "", "0", "yes", "true", "FORCE", " 1"])
def test_other_values_keep_the_commit_blocked(project: Project, value: str | None) -> None:
    res = _guard(project, "git commit -m 'feat: x'", value)
    assert res.returncode == BLOCKED and "Claude does not commit" in res.stderr


def test_an_inline_force_does_not_count(project: Project) -> None:
    res = _guard(project, "HARNESS_ALLOW_COMMIT=force git commit -m 'feat: x'", None)
    assert res.returncode == BLOCKED


@pytest.mark.parametrize(
    "cmd",
    [
        f"echo x > .git/{FORCE_LOG}",
        f"rm -f .git/{FORCE_LOG}",
        "rm -f .git/harness-check.ok",
        "echo deadbeef > .git/harness-check.ok",
    ],
)
def test_claude_cannot_touch_the_force_log_or_the_record(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize("rel", [f".git/{FORCE_LOG}", ".git/harness-check.ok"])
def test_the_write_tools_cannot_touch_the_force_log_or_the_record(
    project: Project, rel: str
) -> None:
    res = project.run("guard_write.sh", write_payload(project.root / rel))
    assert res.returncode == BLOCKED, (rel, res.stderr)


# ── iseal: the resume ledger is written only by the harness ─────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        f"python3 harness/_resume.py begin code {LEAF_ID} 1",
        f"python3 harness/_resume.py end code {LEAF_ID} 1 ok",
        f"python3 harness/_resume.py end code {LEAF_ID} 1 fail reason",
        f"harness/_resume.py begin code {LEAF_ID} 1",
        f"./harness/_resume.py end code {LEAF_ID} 1 ok",
        f"bash -c 'python3 harness/_resume.py begin code {LEAF_ID} 9'",
        f"cd harness && python3 -m _resume begin code {LEAF_ID} 1",
        f"python3 -IBm_resume end code {LEAF_ID} 1 ok",
        f"env python3 harness/_resume.py begin code {LEAF_ID} 1",
    ],
)
def test_claude_cannot_write_the_attempt_ledger(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)
    assert "seal" in res.stderr.lower()


@pytest.mark.parametrize(
    "cmd",
    [
        "python3 harness/_resume.py",
        "python3 harness/_resume.py --json",
        f"python3 harness/_resume.py budget code {LEAF_ID}",
        "make resume",
    ],
)
def test_reading_the_resume_state_stays_allowed(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


def test_the_ledger_file_is_read_only_for_the_write_tools(project: Project) -> None:
    rel = f".git/harness-gate-runs/attempts_code_{LEAF_ID}.json"
    res = project.run("guard_write.sh", write_payload(project.root / rel))
    assert res.returncode == BLOCKED, res.stderr


# ── the set-aside close message (#107) is read-only for Claude ──────────────


CLOSE_MSG = f".git/harness-close-{LEAF_ID}.msg"


def test_the_write_tools_cannot_touch_the_set_aside_close_message(project: Project) -> None:
    for tool in ("Write", "Edit"):
        res = project.run("guard_write.sh", write_payload(project.root / CLOSE_MSG, tool))
        assert res.returncode == BLOCKED, (tool, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        f"echo 'feat: x [{LEAF_ID}]' > {CLOSE_MSG}",
        f"printf x >> {CLOSE_MSG}",
        f"rm {CLOSE_MSG}",
        f"cp /dev/null {CLOSE_MSG}",
        f"mv progress/close_{LEAF_ID}.msg {CLOSE_MSG}",
        f"sed -i s/x/y/ {CLOSE_MSG}",
        f"python3 -c \"open('{CLOSE_MSG}', 'w').write('x')\"",
    ],
)
def test_bash_cannot_touch_the_set_aside_close_message(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_a_non_utf8_plan_file_name_is_fingerprinted(project: Project) -> None:
    """Codex major (round 1): git allows any bytes in a name; the hash must too."""
    _ignore(project, "docs")
    (project.root / "docs/plan").mkdir(parents=True)
    before = _fp(project)
    name = os.fsdecode(b"spec-\xff.md")
    (project.root / "docs/plan" / name).write_text("x", encoding="utf-8")
    assert _fp(project) != before
    msg = _warning(_guard(project, "git commit -m 'feat: x'", "1"))
    assert "no green record" in msg


def test_a_non_utf8_untracked_file_name_is_fingerprinted(project: Project) -> None:
    before = _fp(project)
    (project.root / os.fsdecode(b"note-\xfe.txt")).write_text("x", encoding="utf-8")
    assert _fp(project) != before


# ── Codex final pass: fail closed, never absent; an unverifiable tree warns ──

needs_perms = pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")


@needs_perms
def test_an_untraversable_parent_is_not_hashed_as_absent(project: Project) -> None:
    _ignore(project, "docs")
    (project.root / "docs/plan").mkdir(parents=True)
    (project.root / "docs/plan/a.md").write_text("a")
    docs = project.root / "docs"
    docs.chmod(0)
    try:
        res = _fingerprint(project)
    finally:
        docs.chmod(0o755)
    assert res.returncode == 1 and not res.stdout.strip(), res.stdout


def test_an_input_below_a_file_is_absent(project: Project) -> None:
    """`docs` is a plain file: docs/plan does not exist (not an error)."""
    _ignore(project, "docs")
    project.write("docs", "not a dir")
    assert _fp(project)


@needs_perms
def test_an_unreadable_untracked_file_gives_no_fingerprint(project: Project) -> None:
    secret = project.write("notes.txt", "x")
    secret.chmod(0)
    try:
        res = _fingerprint(project)
        commit = _guard(project, "git commit -m 'feat: x'", "1")
        forced = _guard(project, "git commit -m 'feat: x'", "force")
    finally:
        secret.chmod(0o644)
    assert res.returncode == 1 and "Traceback" not in res.stderr, res.stderr
    assert "cannot" in _warning(commit)  # WARN, not a guard error
    assert "HARNESS_ALLOW_COMMIT=force" in _warning(forced)
    assert "fingerprint=unknown" in _git_path(project, FORCE_LOG).read_text()


def test_a_corrupt_green_record_warns(project: Project) -> None:
    _git_path(project, "harness-check.ok").write_bytes(b"\xff\xfe")
    msg = _warning(_guard(project, "git commit -m 'feat: x'", "1"))
    assert RECORD_HINT in msg and "does not match" in msg
