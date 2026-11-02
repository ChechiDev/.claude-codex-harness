"""Task pull request: review history, Codex-written narrative, PR body and opening the PR."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import LEAF_ID, Plan, Project, write_payload

# ── review history (append-only log of every gate round) ─────────────────────


def _record(plan: Plan, gate: str, fid: str, verdict: str) -> subprocess.CompletedProcess[str]:
    plan.verdict("current.md", verdict)
    return plan.run_py("_pr.py", "record", gate, fid, "progress/current.md")


def test_history_keeps_every_round_in_order(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sync()
    assert _record(plan, "plan", "1.1.1", "CHANGES_REQUESTED").returncode == 0
    assert _record(plan, "plan", "1.1.1", "PLAN_APPROVED").returncode == 0
    assert _record(plan, "code", "1.1.1", "APPROVED").returncode == 0
    text = (plan.root / "progress/review_history/1.1.1.md").read_text()
    assert text.startswith("# Review history: 1.1.1\n")
    first = text.index("## Round 1: plan gate, CHANGES_REQUESTED")
    second = text.index("## Round 2: plan gate, PLAN_APPROVED")
    code = text.index("## Round 1: code gate, APPROVED")
    assert first < second < code


def test_history_demotes_the_verdict_headings(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sync()
    _record(plan, "plan", "1.1.1", "PLAN_APPROVED")
    text = (plan.root / "progress/review_history/1.1.1.md").read_text()
    assert "\n### Review\n" in text
    assert "\n# Review\n" not in text


@pytest.mark.parametrize(
    ("gate", "fid", "name"),
    [("roadmap", "roadmap", "roadmap"), ("phase", "02", "phase-02"), ("code", "1.1.1", "1.1.1")],
)
def test_history_file_per_gate_target(plan: Plan, gate: str, fid: str, name: str) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sync()
    assert _record(plan, gate, fid, "APPROVED").returncode == 0
    assert (plan.root / f"progress/review_history/{name}.md").is_file()


def test_history_needs_the_verdict_file(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sync()
    res = plan.run_py("_pr.py", "record", "plan", "1.1.1", "progress/missing.md")
    assert res.returncode == 1


# ── when a task is complete ──────────────────────────────────────────────────


def test_last_of_task_only_when_every_sibling_is_complete(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2", "in_progress").sub("1.1.3")
    plan.approve_roadmap().approve_phase("01").sync()
    assert plan.run_py("_pr.py", "last-of-task", "1.1.2").returncode == 1
    plan.sub("1.1.2", "done").sub("1.1.3", "in_progress").sync()
    res = plan.run_py("_pr.py", "last-of-task", "1.1.3")
    assert res.returncode == 0
    assert res.stdout.strip() == "1.1"


def test_pr_due_once_the_whole_task_is_done(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2", "in_progress")
    plan.approve_roadmap().approve_phase("01").sync()
    assert plan.run_py("_pr.py", "pr-due", "1.1.2").returncode == 1
    plan.sub("1.1.2", "done").sync()
    res = plan.run_py("_pr.py", "pr-due", "1.1.2")
    assert res.returncode == 0
    assert res.stdout.strip() == "1.1"


def test_pr_due_respects_auto_pr_false(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01")
    plan.config("[git]\nauto_pr = false\n").sync()
    assert plan.run_py("_pr.py", "pr-due", "1.1.1").returncode == 1


def test_pr_due_respects_the_env_switch(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01").sync()
    res = subprocess.run(
        ["python3", str(Path(__file__).resolve().parents[1] / "lib/_pr.py"), "pr-due", "1.1.1"],
        cwd=plan.root,
        text=True,
        capture_output=True,
        env={**os.environ, "HARNESS_AUTO_PR": "0"},
        check=False,
    )
    assert res.returncode == 1


@pytest.mark.parametrize(
    "text", ['[git]\nauto_pr = "yes"\n', "[git]\npr_template = 3\n"], ids=["auto_pr", "template"]
)
def test_bad_pr_config_fails_closed(plan: Plan, text: str) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01")
    plan.config(text).sync()
    res = plan.run_py("_pr.py", "pr-due", "1.1.1")
    assert res.returncode == 1
    assert "harness.toml" in res.stdout + res.stderr


# ── title and body ───────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    ).stdout


def _task_branch(plan: Plan) -> Plan:
    """A repo with main, a task branch and one close commit per subtask."""
    root = plan.root
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "dev")
    plan.phase("01", "1.1", "1.2").sub("1.1.1", "done").sub("1.1.2", "done")
    plan.sub("1.2.1", depends="1.1.2").approve_roadmap().approve_phase("01").sync()
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore: plan")
    _git(root, "switch", "-qc", "feat/1.1-task-1-1")
    for sid in ("1.1.1", "1.1.2"):
        src = root / f"src/app/m{sid.replace('.', '')}.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("x = 1\n")
        (root / f"progress/review_{sid}.md").write_text("**Verdict:** APPROVED\n")
        (root / "docs/reference").mkdir(parents=True, exist_ok=True)
        (root / f"docs/reference/{sid}-s.md").write_text("ref\n")
        (root / f"progress/acceptance_{sid}.sha256").write_text("abc\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", f"feat(app): sub {sid} [{sid}]")
    return plan


NARRATIVE = """# PR 1.1

## Overview
Adds the two app modules.

## Approach
Small functions, no shared state.

## Decisions
Kept the old name on purpose.

## Issues resolved
The plan gate asked for a clearer AC1.

## Issues open
Nothing open.

## Follow-ups
Task 1.2 must reuse m111.

## Branch notes
The first commit also carries the phase breakdown.

## Where to start reviewing
Start with `src/app/m111.py`.
"""


def test_title_follows_the_task(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sync()
    assert plan.run_py("_pr.py", "title", "1.1").stdout.strip() == "feat: task 1.1 [1.1]"


def test_body_renders_every_section_of_the_default_template(plan: Plan) -> None:
    _task_branch(plan)
    (plan.root / "progress/pr_1.1.md").write_text(NARRATIVE)
    res = plan.run_py("_pr.py", "body", "1.1", "main")
    assert res.returncode == 0, res.stdout + res.stderr
    body = res.stdout
    headings = [
        "## Overview",
        "## Approach",
        "## Decisions",
        "## Issues",
        "### Resolved",
        "### Open",
        "## Follow-ups",
        "## Subtasks",
        "## Files",
        "## Documentation",
        "## Branch and merge",
        "## Where to start reviewing",
    ]
    positions = [body.index(h + "\n") for h in headings]
    assert positions == sorted(positions)
    for line in NARRATIVE.splitlines():
        if line and not line.startswith("#"):
            assert line in body
    assert "{" not in body.replace("`{", "")


def test_body_lists_subtasks_with_their_commits(plan: Plan) -> None:
    _task_branch(plan)
    body = plan.run_py("_pr.py", "body", "1.1", "main").stdout
    for sid in ("1.1.1", "1.1.2"):
        sha = _git(plan.root, "log", "-1", "--format=%h", "-F", f"--grep=[{sid}]").strip()
        row = next(line for line in body.splitlines() if line.startswith(f"| {sid} "))
        assert sha in row
        assert f"docs/reference/{sid}-s.md" in row
        assert f"progress/review_{sid}.md" in row


def _section(body: str, heading: str, following: str) -> str:
    return body[body.index(heading) : body.index(following)]


def _files(plan: Plan) -> str:
    body = plan.run_py("_pr.py", "body", "1.1", "main").stdout
    return _section(body, "## Files", "## Documentation")


def _commit_extra_files(plan: Plan, paths: list[str]) -> None:
    for rel in paths:
        path = plan.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("" if path.suffix == ".toml" else "x\n")  # the harness parses toml
    _git(plan.root, "add", "-A")
    _git(plan.root, "commit", "-qm", "chore: extra files [1.1.2]")


def test_body_files_skip_harness_bookkeeping(plan: Plan) -> None:
    _task_branch(plan)
    files = _files(plan)
    assert "| Added | `src/app/m111.py` |" in files
    assert "progress/" not in files
    assert "feature_list.json" not in files
    assert ".sha256" not in files


def test_body_files_skip_project_docs_and_harness_files(plan: Plan) -> None:
    _task_branch(plan)
    excluded = [
        "docs/conventions.md",
        "docs/plan/notes.md",
        "harness/tool.sh",
        ".claude/agents/x.md",
        "harness.toml",
        "AGENTS.md",
        "CLAUDE.md",
        "CHECKPOINTS.md",
    ]
    _commit_extra_files(plan, excluded)
    files = _files(plan)
    for rel in excluded:
        assert f"`{rel}`" not in files, rel
    assert "docs/reference/" not in files


def test_body_files_keep_code_tests_ci_build_and_readme(plan: Plan) -> None:
    _task_branch(plan)
    kept = ["tests/unit/test_x.py", ".github/workflows/ci.yml", "Makefile", "README.md"]
    _commit_extra_files(plan, kept)
    files = _files(plan)
    for rel in [*kept, "src/app/m111.py"]:
        assert f"`{rel}`" in files, rel


def test_body_documentation_points_to_the_task_spec_folder(plan: Plan) -> None:
    _task_branch(plan)
    body = plan.run_py("_pr.py", "body", "1.1", "main").stdout
    docs = _section(body, "## Documentation", "## Branch and merge")
    specs = (plan.root / "docs/plan").rglob("1.1.*.md")
    folders = {p.parent.relative_to(plan.root).as_posix() for p in specs}
    assert folders
    for folder in folders:
        assert f"`{folder}/`" in docs
    assert "docs/reference" not in docs
    assert "progress/" not in docs


def test_body_branch_names_head_base_and_unblocked_tasks(plan: Plan) -> None:
    _task_branch(plan)
    (plan.root / "progress/pr_1.1.md").write_text(NARRATIVE)
    body = plan.run_py("_pr.py", "body", "1.1", "main").stdout
    branch = body[body.index("## Branch and merge") : body.index("## Where to start")]
    assert "`feat/1.1-task-1-1` into `main`" in branch
    assert "1.2" in branch
    assert "The first commit also carries the phase breakdown." in branch


def test_body_without_narrative_falls_back_to_the_task_goal(plan: Plan) -> None:
    _task_branch(plan)
    res = plan.run_py("_pr.py", "body", "1.1", "main")
    assert res.returncode == 0, res.stdout + res.stderr
    overview = res.stdout[res.stdout.index("## Overview") : res.stdout.index("## Approach")]
    assert "g." in overview  # the task goal from the phase file


def test_project_template_overrides_the_default(plan: Plan) -> None:
    _task_branch(plan)
    plan.config('[git]\npr_template = ".github/pr.md"\n')
    (plan.root / ".github").mkdir()
    (plan.root / ".github/pr.md").write_text("Custom for {task}\n\n{overview}\n")
    (plan.root / "progress/pr_1.1.md").write_text(NARRATIVE)
    body = plan.run_py("_pr.py", "body", "1.1", "main").stdout
    assert body.startswith("Custom for 1.1\n")
    assert "Adds the two app modules." in body
    assert "## Subtasks" not in body


def test_missing_project_template_fails(plan: Plan) -> None:
    _task_branch(plan)
    plan.config('[git]\npr_template = ".github/nope.md"\n')
    res = plan.run_py("_pr.py", "body", "1.1", "main")
    assert res.returncode == 1
    assert "nope.md" in res.stdout + res.stderr


# ── the close opens the PR (review_with_codex.sh) ────────────────────────────


def _fake_tools(project: Project, tmp: Path, *, with_gh: bool = True) -> dict[str, str]:
    """Fake codex (closes the leaf) and gh (logs its calls); a bare repo as origin."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(
        '#!/usr/bin/env bash\ncat > "$FAKE_LOG.prompt"\n'
        'sed -i \'s/"in_progress"/"done"/g\' feature_list.json\n'
        f"printf 'feat(domain): add user entity [{LEAF_ID}]\\n' > progress/close_{LEAF_ID}.msg\n"
        "printf '## Overview\\nUser entity.\\n' > progress/pr_1.1.md\n"
    )
    codex.chmod(0o755)
    if with_gh:
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'echo "$*" >> "$FAKE_LOG"\n'
            'if [ "$1 $2" = "pr view" ]; then [ -f "$FAKE_LOG.exists" ]; exit $?; fi\n'
            'if [ "$1 $2" = "pr create" ]; then\n'
            '  while [ $# -gt 0 ]; do [ "$1" = --body-file ] && cp "$2" "$FAKE_LOG.body"; shift;'
            " done\nfi\nexit 0\n"
        )
        gh.chmod(0o755)
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(project.root, "remote", "add", "origin", str(remote))
    _git(project.root, "push", "-q", "origin", "main")
    _git(project.root, "switch", "-qc", "feat/1.1-users")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HARNESS_CHECK_CMD": "true",
        "FAKE_LOG": str(tmp / "gh.log"),
    }
    if not with_gh:
        env["HARNESS_GH"] = str(tmp / "no-such-gh")  # never reach a real gh
    return env


def _close(project: Project, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    project.set_verdict("code", "APPROVED")  # the close runs after an approved code gate
    return project.run("review_with_codex.sh", None, "doc", LEAF_ID, env=env)


def test_closing_the_last_subtask_pushes_and_opens_the_pr(project: Project, tmp_path: Path) -> None:
    env = _fake_tools(project, tmp_path)
    res = _close(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    remote_head = _git(tmp_path / "remote.git", "rev-parse", "feat/1.1-users")
    assert remote_head == _git(project.root, "rev-parse", "HEAD")
    calls = (tmp_path / "gh.log").read_text()
    create = next(line for line in calls.splitlines() if line.startswith("pr create"))
    assert "--head feat/1.1-users" in create
    assert "--base main" in create
    assert "--title feat: users [1.1]" in create
    body = (tmp_path / "gh.log.body").read_text()
    assert "User entity." in body
    assert "co-authored-by" not in body.lower()
    assert "PR opened" in res.stdout + res.stderr


def test_the_last_close_asks_codex_for_the_pr_narrative(project: Project, tmp_path: Path) -> None:
    env = _fake_tools(project, tmp_path)
    _close(project, env)
    prompt = (tmp_path / "gh.log.prompt").read_text()
    assert "progress/pr_1.1.md" in prompt
    assert "progress/review_history/" in prompt


def test_a_middle_close_opens_no_pr(project: Project, tmp_path: Path) -> None:
    data = json.loads((project.root / "feature_list.json").read_text())
    data["features"][0]["subtasks"].append(
        {"id": "1.1.2", "title": "next", "status": "pending", "depends": [], "files": []}
    )
    project.write("feature_list.json", json.dumps(data))
    project.commit_all("second subtask")
    env = _fake_tools(project, tmp_path)
    res = _close(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (tmp_path / "gh.log").exists()
    assert "progress/pr_1.1.md" not in (tmp_path / "gh.log.prompt").read_text()


def test_an_existing_pr_is_not_duplicated(project: Project, tmp_path: Path) -> None:
    env = _fake_tools(project, tmp_path)
    (tmp_path / "gh.log.exists").write_text("")
    res = _close(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "pr create" not in (tmp_path / "gh.log").read_text()


def test_auto_pr_off_keeps_the_branch_local(project: Project, tmp_path: Path) -> None:
    env = {**_fake_tools(project, tmp_path), "HARNESS_AUTO_PR": "0"}
    res = _close(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (tmp_path / "gh.log").exists()


def test_without_gh_the_close_still_succeeds(project: Project, tmp_path: Path) -> None:
    env = _fake_tools(project, tmp_path, with_gh=False)
    res = _close(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "closed ->" in res.stdout
    assert "review_with_codex.sh pr 1.1" in res.stdout + res.stderr


def test_manual_close_prints_the_pr_step(project: Project) -> None:
    project.set_verdict("code", "APPROVED")
    res = project.run(
        "review_with_codex.sh", None, "doc", LEAF_ID, env={"HARNESS_CODEX_MODE": "manual"}
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "./harness/review_with_codex.sh pr 1.1" in res.stdout


def test_usage_lists_the_pr_command(project: Project) -> None:
    res = project.run("review_with_codex.sh", None, "bogus")
    assert "pr <N.M>" in res.stderr


# ── Claude cannot forge the evidence ─────────────────────────────────────────


@pytest.mark.parametrize("rel", ["progress/review_history/1.1.1.md", "progress/pr_1.1.md"])
def test_claude_cannot_write_history_or_pr_narrative(project: Project, rel: str) -> None:
    res = project.run("guard_write.sh", write_payload(project.root / rel))
    assert res.returncode == 2
