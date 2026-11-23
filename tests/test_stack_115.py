"""Issue #115: the stack step (docs/stack.md) and its gate, before the discovery."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan, Project, bash_payload, write_control, write_payload

sys.path.insert(0, str(TOOLKIT / "lib"))
import _resume  # noqa: E402
import _verdict  # noqa: E402

BLOCKED = 2
GATE = "./harness/review_with_codex.sh"
TEMPLATES = (TOOLKIT / "templates/docs/stack.md", TOOLKIT / "templates-c/docs/stack.md")
SECTIONS = (
    "## Project type",
    "## Architecture",
    "## Persistence",
    "## Framework",
    "## Toolchain",
    "## Deploy",
    "## Key dependencies",
)

FILLED = """# Stack

## Project type
- type: cli
- language: Python 3.12
- why: a command-line tool for the ops team.

## Architecture
- decision: none
- why: a single small module; layers would add nothing.

## Persistence
- decision: not applicable
- why: the tool keeps no state between runs.

## Framework
- decision: typer
- why: typed CLI arguments with little code.

## Toolchain
- tests: pytest
- lint: ruff
- types: mypy
- build: uv
- why: the team's standard Python toolchain.

## Deploy
- decision: not applicable
- why: installed by each user with uv tool install.

## Key dependencies
- typer: the CLI framework.
- httpx: HTTP client for the status API.
"""


# ── helpers ──────────────────────────────────────────────────────────────────


def _stack(plan: Plan, text: str = FILLED) -> Plan:
    (plan.root / "docs" / "stack.md").write_text(text)
    return plan


def _approve_stack(plan: Plan, seal: bool = True) -> Plan:
    plan.verdict("stack_review.md", "STACK_APPROVED")
    if seal:
        res = plan.run_py("_guard.py", "seal", "stack")
        assert res.returncode == 0, res.stdout + res.stderr
    return plan


def _run(plan: Plan, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    return subprocess.run(
        ["python3", str(TOOLKIT / "lib" / "_stack.py"), *args],
        cwd=plan.root,
        text=True,
        capture_output=True,
        check=False,
        env={**base, "CLAUDE_PROJECT_DIR": str(plan.root), **env},
    )


def _status(plan: Plan) -> str:
    res = plan.run_py("plan_status.py")
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


# ── the template ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", TEMPLATES, ids=["python", "c"])
def test_template_has_every_category_and_no_default_choice(path: Path) -> None:
    text = path.read_text()
    for heading in SECTIONS:
        assert heading in text, heading
    # Generic: it decides nothing for the project (no default architecture, DB or ORM).
    for line in text.splitlines():
        if line.startswith("- decision:"):
            assert "‹" in line, line
    assert "not applicable" in text
    assert "lock file" in text  # exact versions stay in the manifest / lock file


def test_both_template_sets_ship_the_same_stack_template() -> None:
    assert TEMPLATES[0].read_text() == TEMPLATES[1].read_text()


def test_the_template_is_not_ready_for_the_gate(plan: Plan) -> None:
    _stack(plan, TEMPLATES[0].read_text())
    res = _run(plan, "ready")
    assert res.returncode == 1
    assert "placeholder" in res.stdout + res.stderr


def test_a_filled_stack_is_ready(plan: Plan) -> None:
    assert _run(_stack(plan), "ready").returncode == 0


def test_missing_stack_is_not_ready(plan: Plan) -> None:
    res = _run(plan, "ready")
    assert res.returncode == 1
    assert "docs/stack.md" in res.stdout + res.stderr


def test_a_stack_without_a_category_is_not_ready(plan: Plan) -> None:
    text = FILLED.replace("## Deploy\n", "## Shipping\n")
    res = _run(_stack(plan, text), "ready")
    assert res.returncode == 1
    assert "## Deploy" in res.stdout + res.stderr


# ── make check (_stack.py check) ─────────────────────────────────────────────


def test_fresh_project_without_phases_passes(plan: Plan) -> None:
    _stack(plan, TEMPLATES[0].read_text()).sync()
    res = _run(plan, "check")
    assert res.returncode == 0, res.stdout + res.stderr


def test_discovery_before_the_stack_is_approved_fails(plan: Plan) -> None:
    _stack(plan).phase("01").sync()
    res = _run(plan, "check")
    assert res.returncode == 1
    assert "[FAIL]" in res.stdout and "STACK_APPROVED" in res.stdout


def test_discovery_with_a_rejected_stack_fails(plan: Plan) -> None:
    _stack(plan).phase("01").verdict("stack_review.md", "CHANGES_REQUESTED").sync()
    assert _run(plan, "check").returncode == 1


def test_approved_and_sealed_stack_passes(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").sync()
    res = _run(plan, "check")
    assert res.returncode == 0, res.stdout + res.stderr


def test_approved_but_unsealed_stack_fails_once_discovery_started(plan: Plan) -> None:
    _approve_stack(_stack(plan), seal=False).phase("01").sync()
    res = _run(plan, "check")
    assert res.returncode == 1
    assert "seal stack" in res.stdout


def test_a_changed_stack_voids_its_approval(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").approve_roadmap().sync()
    _stack(plan, FILLED.replace("typer", "click"))
    res = _run(plan, "check")
    assert res.returncode == 1
    assert "changed" in res.stdout and "stack gate" in res.stdout


def test_a_changed_stack_before_the_discovery_only_warns(plan: Plan) -> None:
    _approve_stack(_stack(plan)).sync()
    _stack(plan, FILLED.replace("typer", "click"))
    res = _run(plan, "check")
    assert res.returncode == 0, res.stdout
    assert "[WARN]" in res.stdout


def test_counterfactual_run_assumes_the_stack_approved(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").sync()
    _stack(plan, FILLED.replace("typer", "click"))
    res = _run(plan, "check", HARNESS_ASSUME_APPROVED="stack")
    assert res.returncode == 0, res.stdout


@pytest.mark.parametrize("with_template", [False, True])
def test_existing_project_with_an_approved_roadmap_only_warns(
    plan: Plan, with_template: bool
) -> None:
    if with_template:
        _stack(plan, TEMPLATES[0].read_text())
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().approve_phase("01").sync()
    res = _run(plan, "check")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[WARN]" in res.stdout and "docs/stack.md" in res.stdout


def test_project_from_before_the_stack_step_only_warns(plan: Plan) -> None:
    plan.phase("01").approve_roadmap().sync()  # no docs/stack.md: planned before #115
    res = _run(plan, "check")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[WARN]" in res.stdout


def test_check_sh_runs_the_stack_rules() -> None:
    assert '_stack.py" check' in (TOOLKIT / "lib/check.sh").read_text()


# ── dependency drift (pyproject.toml vs docs/stack.md): WARN only ─────────────

PYPROJECT = """[project]
name = "tool"
version = "0.1.0"
dependencies = [{deps}]

[dependency-groups]
dev = ["pytest>=8", "ruff"]
"""


def _pyproject(plan: Plan, *deps: str) -> None:
    quoted = ", ".join(f'"{d}"' for d in deps)
    (plan.root / "pyproject.toml").write_text(PYPROJECT.format(deps=quoted))


def test_matching_dependencies_do_not_warn(plan: Plan) -> None:
    _approve_stack(_stack(plan)).sync()
    _pyproject(plan, "Typer>=0.12", "httpx[http2]>=0.27; python_version>='3.11'")
    res = _run(plan, "check")
    assert res.returncode == 0
    assert "dependency" not in res.stdout.lower(), res.stdout


def test_undeclared_dependency_warns(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").sync()
    _pyproject(plan, "typer", "httpx", "SQLAlchemy>=2")
    res = _run(plan, "check")
    assert res.returncode == 0
    assert "[WARN]" in res.stdout and "sqlalchemy" in res.stdout.lower()


def test_stack_dependency_missing_from_pyproject_warns(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").sync()
    _pyproject(plan, "typer")
    res = _run(plan, "check")
    assert res.returncode == 0
    assert "[WARN]" in res.stdout and "httpx" in res.stdout


def test_names_are_normalized(plan: Plan) -> None:
    _approve_stack(_stack(plan, FILLED.replace("- httpx:", "- Foo_Bar.baz:"))).sync()
    _pyproject(plan, "typer", "foo-bar-baz")
    res = _run(plan, "check")
    assert "dependency" not in res.stdout.lower(), res.stdout


def test_no_pyproject_means_no_dependency_check(plan: Plan) -> None:
    _approve_stack(_stack(plan)).sync()
    res = _run(plan, "check")
    assert res.returncode == 0
    assert "dependency" not in res.stdout.lower(), res.stdout


def test_unreadable_pyproject_only_warns(plan: Plan) -> None:
    _approve_stack(_stack(plan)).sync()
    (plan.root / "pyproject.toml").write_text("[project\n")
    res = _run(plan, "check")
    assert res.returncode == 0
    assert "[WARN]" in res.stdout and "pyproject.toml" in res.stdout


# ── the roadmap waits for the stack ──────────────────────────────────────────


def test_roadmap_gate_waits_for_the_stack(plan: Plan) -> None:
    _stack(plan).phase("01").sync()
    res = _run(plan, "roadmap-ready")
    assert res.returncode == 1
    assert "stack" in res.stdout + res.stderr


def test_roadmap_gate_opens_once_the_stack_is_approved(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").sync()
    assert _run(plan, "roadmap-ready").returncode == 0


@pytest.mark.parametrize("legacy", ["roadmap", "no-stack-file"])
def test_roadmap_gate_stays_open_for_existing_projects(plan: Plan, legacy: str) -> None:
    if legacy == "roadmap":
        _stack(plan, TEMPLATES[0].read_text())
    plan.approve_roadmap().phase("01").sync()
    assert _run(plan, "roadmap-ready").returncode == 0


# ── verdict, seal and guard ──────────────────────────────────────────────────


def test_stack_verdict_is_read(plan: Plan) -> None:
    plan.verdict("stack_review.md", "STACK_APPROVED")
    res = plan.run_py("_harness.py", "verdict", "progress/stack_review.md")
    assert res.stdout.strip() == "STACK_APPROVED"


def test_verdict_protocol_knows_the_stack_gate() -> None:
    assert _verdict.GATE_TOKENS["stack"] == "STACK_APPROVED"
    assert _verdict.verdict_path("stack", "stack") == "progress/stack_review.md"
    assert any(p.match("stack_review.md") for p, gate, _ in _verdict.VERDICT_NAMES)


def test_seal_refuses_without_approval(plan: Plan) -> None:
    _stack(plan).verdict("stack_review.md", "CHANGES_REQUESTED")
    res = plan.run_py("_guard.py", "seal", "stack")
    assert res.returncode != 0
    assert not (plan.root / "progress/stack.sha256").exists()


def test_seal_records_the_approved_stack(plan: Plan) -> None:
    _approve_stack(_stack(plan))
    digest = hashlib.sha256((plan.root / "docs/stack.md").read_bytes()).hexdigest()
    assert (plan.root / "progress/stack.sha256").read_text() == f"{digest}  docs/stack.md\n"


def test_claude_cannot_write_the_stack_verdict(project: Project) -> None:
    res = project.run("guard_write.sh", write_payload(project.root / "progress/stack_review.md"))
    assert res.returncode == BLOCKED


def test_claude_cannot_seal_the_stack(project: Project) -> None:
    res = project.run("guard_bash.sh", bash_payload("python3 harness/_guard.py seal stack"))
    assert res.returncode == BLOCKED


def test_claude_cannot_write_the_stack_verdict_from_bash(project: Project) -> None:
    payload = bash_payload("echo '**Verdict:** STACK_APPROVED' > progress/stack_review.md")
    assert project.run("guard_bash.sh", payload).returncode == BLOCKED


def test_planner_writes_docs_stack(project: Project) -> None:
    payload = {
        **write_payload(project.root / "docs/stack.md"),
        "agent_id": "a1",
        "agent_type": "claude-planner",
    }
    res = project.run("guard_write.sh", payload)
    assert res.returncode == 0, res.stderr


# ── make status: the stack step comes first ──────────────────────────────────


def test_status_starts_with_the_stack_step(plan: Plan) -> None:
    _stack(plan, TEMPLATES[0].read_text()).sync()
    assert "Next: ask the leader to define the stack" in _status(plan)


def test_status_points_at_the_stack_gate(plan: Plan) -> None:
    out = _status(_stack(plan).sync())
    assert f"Next: stack gate: {GATE} stack" in out


def test_status_asks_to_fix_a_rejected_stack(plan: Plan) -> None:
    _stack(plan).verdict("stack_review.md", "CHANGES_REQUESTED").sync()
    out = _status(plan)
    assert f"Next: fix the stack (progress/stack_review.md), then stack gate: {GATE} stack" in out


def test_status_moves_to_discovery_once_approved(plan: Plan) -> None:
    out = _status(_approve_stack(_stack(plan)).sync())
    assert "Next: ask the leader to plan the project" in out


def test_status_regates_a_changed_stack(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").approve_roadmap().sync()
    _stack(plan, FILLED.replace("typer", "click"))
    out = _status(plan)
    assert (
        f"Next: docs/stack.md changed since STACK_APPROVED: stack gate again: {GATE} stack" in out
    )
    assert "Stack      changed since STACK_APPROVED" in out


def test_status_shows_the_stack_line(plan: Plan) -> None:
    out = _status(_approve_stack(_stack(plan)).phase("01").sync())
    assert "Stack      STACK_APPROVED" in out


def test_status_of_an_existing_project_is_unchanged(plan: Plan) -> None:
    out = _status(plan.phase("01").phase("02").approve_roadmap().sync())
    assert "Next: break down phase 01 into tasks" in out
    assert "Stack" not in out


def test_status_asks_the_human_to_seal_an_unsealed_stack(plan: Plan) -> None:
    out = _status(_approve_stack(_stack(plan), seal=False).sync())
    assert f"Next: needs human: seal the stack (human only): {GATE} seal stack" in out


# ── resume: the new Next phrases are classified ──────────────────────────────


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"stack gate: {GATE} stack", "review"),
        (f"fix the stack (progress/stack_review.md), then stack gate: {GATE} stack", "fix"),
        (f"docs/stack.md changed since STACK_APPROVED: stack gate again: {GATE} stack", "review"),
        (
            f"finish the seal of the stack (last attempt: seal failed): stack gate again: "
            f"{GATE} stack",
            "seal",
        ),
    ],
)
def test_resume_authorizes_the_stack_steps(text: str, kind: str) -> None:
    action = _resume.classify(text)
    assert action.authorized and action.kind == kind
    assert action.command == [GATE, "stack"]


def test_resume_waits_for_the_human_on_the_stack_step() -> None:
    text = "ask the leader to define the stack (stack step), then stack gate"
    assert not _resume.classify(text).authorized
    assert _resume.known_prefix("ask the leader to define the stack")


def test_attempt_ledger_knows_the_stack_step() -> None:
    assert "stack" in _resume.STEPS and "stack" in _resume.GATES


# ── the gate itself ──────────────────────────────────────────────────────────


def _stack_project(project: Project) -> Project:
    # A new project: no roadmap nor phase approved yet.
    (project.root / "progress/roadmap_review.md").unlink()
    (project.root / "progress/phase_review_01.md").unlink()
    project.write("docs/stack.md", FILLED)
    project.commit_all("stack")
    return project


def _manual_gate(project: Project, *args: str) -> subprocess.CompletedProcess[str]:
    write_control(project.root)
    return project.run(
        "review_with_codex.sh", None, *args, env={"UV_CACHE_DIR": "/tmp/fake-uv-cache"}
    )


@pytest.mark.e2e
def test_stack_gate_prompt_names_the_stack_and_its_verdict(project: Project) -> None:
    res = _manual_gate(_stack_project(project), "stack")
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert "Phase: stack" in out
    assert "codex-planner.md" in out
    assert "docs/stack.md" in out
    assert "progress/stack_review.md" in out
    assert "seal stack" in out


def test_stack_gate_refuses_an_unfilled_stack(project: Project) -> None:
    _stack_project(project).write("docs/stack.md", TEMPLATES[0].read_text())
    res = _manual_gate(project, "stack")
    assert res.returncode != 0
    assert "placeholder" in res.stdout + res.stderr


def test_roadmap_gate_refuses_before_the_stack_is_approved(project: Project) -> None:
    res = _manual_gate(_stack_project(project), "roadmap")
    assert res.returncode != 0
    assert "STACK_APPROVED" in res.stdout + res.stderr


@pytest.mark.e2e
def test_approved_stack_gate_seals_the_stack(project: Project, tmp_path: Path) -> None:
    _stack_project(project)
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        "printf '# Stack review\\n\\n**Verdict:** STACK_APPROVED\\n' > progress/stack_review.md\n"
    )
    codex.chmod(0o755)
    res = project.run(
        "review_with_codex.sh", None, "stack", env={"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert (project.root / "progress/stack.sha256").is_file()
    assert (project.root / "progress/review_history/stack.md").is_file()


def test_usage_lists_the_stack_gate(project: Project) -> None:
    res = project.run("review_with_codex.sh", None)
    assert " stack " in res.stderr or "| stack" in res.stderr, res.stderr
    assert "seal <N.M.K|roadmap|stack" in res.stderr


# ── the agents and the docs ──────────────────────────────────────────────────


def test_leader_runs_the_stack_step_before_the_discovery() -> None:
    text = (TOOLKIT / "agents/leader.md").read_text()
    assert "## Stack step" in text
    assert text.index("## Stack step") < text.index("## Discovery")
    step = text.split("## Stack step", 1)[1].split("\n## ", 1)[0]
    for needle in (
        "one question at a time",
        "project type",
        "language",
        "not applicable",
        "docs/stack.md",
        "review_with_codex.sh stack",
        "STACK_APPROVED",
        "user's approval",
    ):
        assert needle in step, needle
    discovery = text.split("## Discovery", 1)[1].split("\n## ", 1)[0]
    assert "docs/stack.md" in discovery
    assert "stack/constraints" not in discovery  # the stack is not asked again


def test_planner_reads_and_writes_the_stack() -> None:
    text = (TOOLKIT / "agents/claude-planner.md").read_text()
    assert "docs/stack.md" in text
    assert "never ask" in text.lower() or "does not ask" in text.lower()


def test_codex_planner_defines_the_stack_mode() -> None:
    text = (TOOLKIT / "codex/codex-planner.md").read_text()
    assert "`stack` mode" in text
    for needle in ("STACK_APPROVED", "CHANGES_REQUESTED", "progress/stack_review.md"):
        assert needle in text, needle


def test_workflow_mentions_the_stack_gate() -> None:
    text = (TOOLKIT / "lib/WORKFLOW.md").read_text()
    assert "review_with_codex.sh stack" in text or "stack |" in text
    assert "docs/stack.md" in text


@pytest.mark.parametrize("doc", ["docs/usage.md", "docs/architecture.md"])
def test_docs_describe_the_stack_step(doc: str) -> None:
    text = (TOOLKIT / doc).read_text()
    assert "docs/stack.md" in text and "STACK_APPROVED" in text


def test_claude_md_template_points_at_the_stack() -> None:
    text = (TOOLKIT / "templates/CLAUDE.md").read_text()
    assert "docs/stack.md" in text
    assert "FastAPI | Django" not in text  # no stack assumed by default


# ── the first close carries the approved stack (like the roadmap) ────────────


def test_close_scope_carries_the_approved_stack(tmp_path: Path) -> None:
    import _close_scope as cs

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    for rel in (
        "docs/stack.md",
        "progress/stack_review.md",
        "progress/stack.sha256",
        "progress/verdict_stack_review.md.sha256",
        "progress/review_history/stack.md",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    leaf = {"id": "1.1.1", "phase": "01", "issue": "", "files": [], "spec": "", "migrates": []}
    assert cs.check(tmp_path, "1.1.1", leaf).ok


def test_deleting_an_approved_stack_voids_it(plan: Plan) -> None:
    """Deleting docs/stack.md is a change: never a way back to the WARN-only state."""
    _approve_stack(_stack(plan)).phase("01").sync()
    (plan.root / "docs/stack.md").unlink()
    res = _run(plan, "check")
    assert res.returncode == 1
    assert "changed" in res.stdout


# ── Codex round 2 ────────────────────────────────────────────────────────────


def _git_commit_all(root: Path, msg: str) -> None:
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "t"),
    ):
        if args[0] != "init" or not (root / ".git").exists():
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=root, check=True, capture_output=True)


def test_deleting_the_installed_template_does_not_skip_the_stack(plan: Plan) -> None:
    """A docs/stack.md that git ever tracked keeps the project held to the stack step."""
    _stack(plan, TEMPLATES[0].read_text())
    _git_commit_all(plan.root, "chore: install harness")
    (plan.root / "docs/stack.md").unlink()
    plan.phase("01").sync()
    res = _run(plan, "check")
    assert res.returncode == 1, res.stdout
    assert _run(plan, "roadmap-ready").returncode == 1


def test_a_changed_stack_comes_before_the_issue_queue(plan: Plan) -> None:
    _approve_stack(_stack(plan)).phase("01").approve_roadmap()
    plan.issue(5, priority="now").isub("ISS-5.1").sync()
    _stack(plan, FILLED.replace("typer", "click"))
    env = {**os.environ, "HARNESS_GH": str(plan.root / "no-such-gh")}  # offline queue
    res = subprocess.run(
        ["python3", str(TOOLKIT / "lib/plan_status.py")],
        cwd=plan.root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert (
        f"Next: docs/stack.md changed since STACK_APPROVED: stack gate again: {GATE} stack"
        in res.stdout
    ), res.stdout


def test_existing_project_with_an_approved_unsealed_stack_is_held_to_it(plan: Plan) -> None:
    _approve_stack(_stack(plan), seal=False).phase("01").approve_roadmap().sync()
    res = _run(plan, "check")
    assert res.returncode == 1
    assert "seal stack" in res.stdout
    assert f"Next: needs human: seal the stack (human only): {GATE} seal stack" in _status(plan)


@pytest.mark.parametrize("evidence", ["phase_review", "phase_seal"])
def test_existing_project_with_an_approved_plan_only_warns(plan: Plan, evidence: str) -> None:
    """Round 3: an approved plan (a phase approval or seal) without a roadmap verdict."""
    _stack(plan, TEMPLATES[0].read_text()).phase("01", "1.1").sub("1.1.1")
    if evidence == "phase_review":
        plan.approve_phase("01")
    else:
        (plan.root / "progress/plan_phase_01.sha256").write_text("x  docs/plan/phase-01-p01.md\n")
    plan.sync()
    res = _run(plan, "check")
    assert res.returncode == 0, res.stdout
    assert "[WARN]" in res.stdout
    assert _run(plan, "roadmap-ready").returncode == 0


def test_codex_planner_extends_the_regate_exception_to_stack() -> None:
    text = (TOOLKIT / "codex/codex-planner.md").read_text()
    stack = text.split("## `stack` mode", 1)[1]
    assert "overrides" in stack and "HARNESS_ASSUME_APPROVED=stack" in stack


# ── Codex final pass: only an approved roadmap or plan is exempt ─────────────


def test_a_project_without_stack_file_nor_approved_plan_is_held_to_the_stack(
    plan: Plan,
) -> None:
    plan.phase("01").sync()  # no docs/stack.md, nothing approved
    res = _run(plan, "check")
    assert res.returncode == 1, res.stdout
    assert _run(plan, "roadmap-ready").returncode == 1


def test_a_fresh_project_without_stack_file_starts_with_the_stack_step(plan: Plan) -> None:
    plan.sync()
    assert _run(plan, "check").returncode == 0
    assert "Next: ask the leader to define the stack" in _status(plan)
