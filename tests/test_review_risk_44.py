"""Codex review effort and checklists by risk and surface (issue #44).

A subtask spec may declare `- risk: low|medium|high` and `- surfaces: a, b`. The
harness.toml table [review.risk.<level>] / [review.surfaces.<name>] maps them to a
codex_effort and extra review checklists (codex/checklist-<name>.md). The plan and
code gates of that subtask apply them. Conservative policy (no risk washing):

- the effort is never below the reviewer role's own (`codex_effort` in reviewer.md);
- a file matching [sessions] high_risk_paths makes the risk high, and one matching a
  surface's `paths` adds that surface, whatever the spec declares;
- the rating sealed at the phase/task/issue gate is a floor: lowering it afterwards
  fails `make check` until that gate (or the human) re-seals it;
- unknown values fail closed. A spec without the fields reviews exactly as before.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import _config
import _plan_seal
import _review_risk
import pytest
from conftest import LEAF_ID, TOOLKIT, Plan, Project, write_control

SPEC_DIR = "docs/plan/phase-01-p01"
SPEC = f"{SPEC_DIR}/1.1.1-s.md"
CODEX = TOOLKIT / "codex"
CHECKLISTS = ("high-risk", "security", "data", "migrations")


def _cfg(root: Path, toml: str) -> _config.Config:
    (root / "harness.toml").write_text(toml, encoding="utf-8")
    return _config.load(root)


# ── harness.toml table ──────────────────────────────────────────────────────


def test_without_a_table_the_builtin_policy_applies(tmp_path: Path) -> None:
    cfg = _config.load(tmp_path)
    assert cfg.review_risk["low"] == _config.ReviewRule()
    assert cfg.review_risk["medium"] == _config.ReviewRule()
    assert cfg.review_risk["high"] == _config.ReviewRule("xhigh", ("high-risk",))
    assert cfg.review_surfaces["security"] == _config.ReviewRule("xhigh", ("security",))
    assert cfg.review_surfaces["data"] == _config.ReviewRule("", ("data",))
    assert cfg.review_surfaces["migrations"] == _config.ReviewRule("xhigh", ("migrations",))


def test_the_project_table_overrides_per_field_and_adds_surfaces(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        "[review]\nmax_rounds = 2\n"
        '[review.surfaces.security]\npaths = ["src/app/auth/**"]\n'
        '[review.surfaces.payments]\ncodex_effort = "xhigh"\nchecklists = ["security"]\n'
        'paths = ["src/app/pay/**"]\n'
        '[review.risk.medium]\nchecklists = ["data"]\n',
    )
    assert cfg.max_rounds == 2
    assert cfg.review_surfaces["security"] == _config.ReviewRule(
        "xhigh", ("security",), ("src/app/auth/**",)
    )
    assert cfg.review_surfaces["payments"] == _config.ReviewRule(
        "xhigh", ("security",), ("src/app/pay/**",)
    )
    assert cfg.review_risk["medium"] == _config.ReviewRule("", ("data",))


@pytest.mark.parametrize(
    "toml",
    [
        "[review.risk.critical]\ncodex_effort = 'xhigh'\n",
        "[review.risk.high]\ncodex_effort = 'max'\n",
        "[review.risk.high]\nchecklists = ['../x']\n",
        "[review.risk.high]\nchecklists = 'security'\n",
        "[review.risk.high]\nefort = 'xhigh'\n",
        "[review.risk.high]\npaths = ['src/**']\n",
        "[review.surfaces.Bad_Name]\nchecklists = ['security']\n",
        "[review.surfaces.payments]\npaths = ['/etc/**']\n",
        "[review.surfaces.payments]\npaths = ['src/[ab]/**']\n",
        "[review]\nrisk = 'high'\n",
        "[review.risk.low]\ncodex_effort = ''\n",
    ],
)
def test_an_invalid_table_fails_closed(tmp_path: Path, toml: str) -> None:
    with pytest.raises(_config.ConfigError):
        _cfg(tmp_path, toml)


# ── the spec fields ─────────────────────────────────────────────────────────


def test_a_spec_declares_risk_and_surfaces() -> None:
    text = "# 1.1.1 X\n- status: pending\n- risk: high\n- surfaces: security, data\n"
    assert _review_risk.parse_rating(text) == _review_risk.Rating("high", ("data", "security"))


def test_a_spec_without_the_fields_has_no_rating() -> None:
    text = "# 1.1.1 X\n- status: pending\n\n## Description\n- risk: high\n"
    assert _review_risk.parse_rating(text) == _review_risk.Rating(None, ())


@pytest.mark.parametrize(
    "lines",
    [
        "- risk: critical\n",
        "- risk: High\n",
        "- risk:\n",
        "- risk: low\n- risk: high\n",
        "- surfaces: security\n- surfaces: data\n",
        "- surfaces: sec urity\n",
        "- surfaces: ../x\n",
    ],
)
def test_an_invalid_rating_fails_closed(lines: str) -> None:
    with pytest.raises(_review_risk.RatingError):
        _review_risk.parse_rating("# 1.1.1 X\n- status: pending\n" + lines)


def test_the_rating_token_round_trips() -> None:
    rating = _review_risk.Rating("high", ("data", "security"))
    assert rating.token() == "high;data,security"
    assert _review_risk.Rating.from_token(rating.token()) == rating
    assert _review_risk.Rating.from_token("-;security") == _review_risk.Rating(None, ("security",))


# ── the policy of one subtask's gate ────────────────────────────────────────


def _leaf_project(
    root: Path, rating: str = "", files: tuple[str, ...] = ("src/app/m.py",), toml: str = ""
) -> None:
    spec = f"{SPEC_DIR}/{LEAF_ID}-s.md"
    sub = {"id": LEAF_ID, "title": "s", "status": "in_progress", "files": list(files)}
    task = {"id": "1.1", "phase": "01", "title": "t", "subtasks": [{**sub, "spec": spec}]}
    data = {"phases": [{"id": "01", "title": "p"}], "features": [task]}
    (root / "feature_list.json").write_text(json.dumps(data), encoding="utf-8")
    (root / SPEC_DIR).mkdir(parents=True, exist_ok=True)
    (root / spec).write_text(f"# {LEAF_ID} s\n- status: in_progress\n{rating}", encoding="utf-8")
    (root / "harness.toml").write_text(toml, encoding="utf-8")


def _policy(root: Path, base: str = "high", codex: Path = CODEX) -> _review_risk.Policy:
    return _review_risk.policy(root, LEAF_ID, base, codex)


def test_no_rating_keeps_the_role_effort_and_no_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path)
    policy = _policy(tmp_path)
    assert policy == _review_risk.Policy("high", (), ())


def test_a_low_risk_never_lowers_the_role_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path, "- risk: low\n", toml='[review.risk.low]\ncodex_effort = "low"\n')
    assert _policy(tmp_path).effort == "high"


def test_a_table_cannot_set_an_effort_below_the_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path, "- risk: high\n", toml='[review.risk.high]\ncodex_effort = "low"\n')
    policy = _policy(tmp_path)
    assert policy.effort == "high"
    assert policy.checklists == (CODEX / "checklist-high-risk.md",)


def test_high_risk_raises_the_effort_and_adds_its_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path, "- risk: high\n- surfaces: data\n")
    policy = _policy(tmp_path)
    assert policy.effort == "xhigh"
    assert policy.checklists == (CODEX / "checklist-high-risk.md", CODEX / "checklist-data.md")
    assert any("risk high (declared)" in r for r in policy.reasons)


def test_a_higher_risk_keeps_everything_a_lower_risk_gets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    toml = (
        '[review.risk.medium]\ncodex_effort = "xhigh"\nchecklists = ["data"]\n'
        '[review.risk.high]\ncodex_effort = "low"\nchecklists = []\n'
    )
    _leaf_project(tmp_path, "- risk: high\n", toml=toml)
    policy = _policy(tmp_path)
    assert policy.effort == "xhigh"
    assert policy.checklists == (CODEX / "checklist-data.md",)


def test_a_surface_alone_adds_its_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path, "- surfaces: data\n")
    policy = _policy(tmp_path)
    assert policy.effort == "high"
    assert policy.checklists == (CODEX / "checklist-data.md",)


def test_a_high_risk_path_makes_the_risk_high_whatever_the_spec_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    toml = '[sessions]\nhigh_risk_paths = ["migrations/**"]\n'
    _leaf_project(tmp_path, "- risk: low\n", files=("migrations/0001_users.py",), toml=toml)
    policy = _policy(tmp_path)
    assert policy.effort == "xhigh"
    assert policy.checklists == (CODEX / "checklist-high-risk.md",)
    assert any("migrations/0001_users.py" in r and "migrations/**" in r for r in policy.reasons)


def test_a_surface_path_adds_the_surface_even_undeclared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    toml = '[review.surfaces.payments]\nchecklists = ["security"]\npaths = ["src/app/pay/**"]\n'
    _leaf_project(tmp_path, files=("src/app/pay/charge.py",), toml=toml)
    policy = _policy(tmp_path)
    assert policy.checklists == (CODEX / "checklist-security.md",)
    assert any("surface payments" in r and "src/app/pay/charge.py" in r for r in policy.reasons)


def test_the_sealed_rating_is_a_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path)  # the spec no longer declares anything
    spec = f"{SPEC_DIR}/{LEAF_ID}-s.md"
    seal = tmp_path / "progress" / "plan_task_1.1.sha256"
    seal.parent.mkdir()
    seal.write_text(f"{'0' * 64}  {spec}\nhigh;security  rating:{spec}\n", encoding="utf-8")
    policy = _policy(tmp_path)
    assert policy.effort == "xhigh"
    assert policy.checklists == (
        CODEX / "checklist-high-risk.md",
        CODEX / "checklist-security.md",
    )
    assert any("sealed" in r for r in policy.reasons)


def test_an_unknown_surface_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path, "- surfaces: securty\n")
    with pytest.raises(_review_risk.RatingError, match="securty"):
        _policy(tmp_path)


def test_a_missing_checklist_file_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _leaf_project(tmp_path, "- surfaces: security\n")
    empty = tmp_path / "codex"
    empty.mkdir()
    with pytest.raises(_review_risk.RatingError, match="checklist-security.md"):
        _policy(tmp_path, codex=empty)


def test_the_policy_cli_prints_effort_reasons_and_checklists(tmp_path: Path) -> None:
    _leaf_project(tmp_path, "- risk: high\n")
    res = subprocess.run(
        ["python3", str(TOOLKIT / "lib" / "_review_risk.py"), "policy", LEAF_ID, "high",
         str(CODEX)],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == "xhigh"
    assert "risk high (declared)" in lines[1]
    assert lines[2:] == [str(CODEX / "checklist-high-risk.md")]


def test_the_policy_cli_fails_closed(tmp_path: Path) -> None:
    _leaf_project(tmp_path, "- risk: extreme\n")
    res = subprocess.run(
        ["python3", str(TOOLKIT / "lib" / "_review_risk.py"), "policy", LEAF_ID, "high",
         str(CODEX)],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )  # fmt: skip
    assert res.returncode == 1
    assert "[FAIL]" in res.stderr and "extreme" in res.stderr
    assert res.stdout == ""


# ── sealed with the phase/task/issue gate ───────────────────────────────────


def _rate(plan: Plan, rel: str, lines: str) -> None:
    path = plan.root / rel
    text = path.read_text().replace("- layer: domain\n", f"- layer: domain\n{lines}", 1)
    path.write_text(text)
    assert plan.run_py("sync_plan.py").returncode == 0


def _sealed_phase(plan: Plan, rating: str = "- risk: high\n- surfaces: security\n") -> Plan:
    plan.phase("01", "1.1").sub("1.1.1").sub("1.1.2").approve_roadmap().sync()
    if rating:
        _rate(plan, SPEC, rating)
    plan.approve_phase("01")
    res = plan.run_py("_guard.py", "seal", "phase", "01")
    assert res.returncode == 0, res.stdout + res.stderr
    return plan


def _seal_entries(plan: Plan) -> dict[str, str]:
    return _plan_seal.read(plan.root / "progress" / "plan_phase_01.sha256")


def test_the_seal_records_the_rating_of_a_rated_spec(plan: Plan) -> None:
    entries = _seal_entries(_sealed_phase(plan))
    assert entries[f"rating:{SPEC}"] == "high;security"
    assert f"rating:{SPEC_DIR}/1.1.2-s.md" not in entries  # unrated: no rating entry


def test_unrated_specs_keep_the_exact_previous_seal(plan: Plan) -> None:
    entries = _seal_entries(_sealed_phase(plan, rating=""))
    assert not any(rel.startswith("rating:") for rel in entries)


def test_a_sealed_rated_plan_passes_integrity(plan: Plan) -> None:
    res = _sealed_phase(plan).run_py("_guard.py", "integrity")
    assert res.returncode == 0, res.stdout


def test_seal_entries_stay_digests_only(plan: Plan) -> None:
    # _legacy, _supersede and _close_scope compare these with what they recorded.
    _sealed_phase(plan)
    for entries in (
        _plan_seal.phase_entries(plan.root, "01"),
        _plan_seal.task_entries(plan.root, "1.1"),
    ):
        assert entries and not any(rel.startswith("rating:") for rel in entries)


def test_a_seal_from_before_issue_44_stays_valid(plan: Plan) -> None:
    # A spec that already said `- risk: high` when it was sealed, by a harness that
    # wrote no rating lines: unchanged, it must not fail integrity.
    _sealed_phase(plan)
    manifest = plan.root / "progress" / "plan_phase_01.sha256"
    lines = [ln for ln in manifest.read_text().splitlines() if "rating:" not in ln]
    manifest.write_text("\n".join(lines) + "\n")
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 0, res.stdout


def test_a_sealed_old_free_text_risk_stays_valid_while_unchanged(plan: Plan) -> None:
    _sealed_phase(plan, rating="- risk: low, only docs\n")
    assert _seal_entries(plan)[f"rating:{SPEC}"] == "invalid"
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 0, res.stdout


def _open_plan_gate(plan: Plan, old: str, new: str) -> None:
    path = plan.root / SPEC
    path.write_text(path.read_text().replace("status: pending", "status: in_progress"))
    path.write_text(path.read_text().replace(old, new))
    assert plan.run_py("sync_plan.py").returncode == 0
    plan.verdict("plan_review_1.1.1.md", "CHANGES_REQUESTED")


def test_lowering_the_rating_in_the_plan_gate_fails(plan: Plan) -> None:
    _sealed_phase(plan)
    _open_plan_gate(plan, "- risk: high\n", "- risk: low\n")
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 1, res.stdout
    assert "lowered" in res.stdout and "1.1.1" in res.stdout


def test_dropping_a_surface_in_the_plan_gate_fails(plan: Plan) -> None:
    _sealed_phase(plan)
    _open_plan_gate(plan, "- surfaces: security\n", "")
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 1, res.stdout
    assert "lowered" in res.stdout


def test_raising_the_rating_in_the_plan_gate_only_warns(plan: Plan) -> None:
    _sealed_phase(plan)
    _open_plan_gate(plan, "- surfaces: security\n", "- surfaces: data, security\n")
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 0, res.stdout
    assert "WARN" in res.stdout


def test_the_plan_gate_reseal_keeps_a_raise(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    _sealed_phase(plan)
    _open_plan_gate(plan, "- surfaces: security\n", "- surfaces: data, security\n")
    monkeypatch.chdir(plan.root)
    assert _plan_seal.reseal_spec(plan.root, SPEC, "01")
    assert _seal_entries(plan)[f"rating:{SPEC}"] == "high;data,security"
    assert plan.run_py("_guard.py", "integrity").returncode == 0


def test_the_plan_gate_reseal_never_lowers_the_sealed_rating(
    plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sealed_phase(plan)
    _open_plan_gate(plan, "- risk: high\n", "- risk: low\n")
    monkeypatch.chdir(plan.root)
    assert _plan_seal.reseal_spec(plan.root, SPEC, "01")
    assert _seal_entries(plan)[f"rating:{SPEC}"] == "high;security"
    plan.verdict("plan_review_1.1.1.md", "PLAN_APPROVED")
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 1  # still lowered: the phase gate (or the human) must re-seal
    assert "lowered" in res.stdout


# ── validate (make check) ───────────────────────────────────────────────────


def test_validate_fails_on_an_invalid_rating(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()
    _rate(plan, SPEC, "- risk: critical\n")
    res = plan.validate()
    assert res.returncode == 1
    assert "critical" in res.stdout + res.stderr


def test_validate_fails_on_an_unknown_surface(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()
    _rate(plan, SPEC, "- surfaces: securty\n")
    res = plan.validate()
    assert res.returncode == 1
    assert "securty" in res.stdout + res.stderr


def test_validate_fails_on_a_missing_checklist_file(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap()
    plan.config('[review.surfaces.payments]\nchecklists = ["payments"]\n').sync()
    _rate(plan, SPEC, "- surfaces: payments\n")
    res = plan.validate()
    assert res.returncode == 1
    assert "checklist-payments.md" in res.stdout + res.stderr


def test_validate_exempts_done_specs_with_an_old_free_text_risk(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", status="done").approve_roadmap().sync()
    _rate(plan, SPEC, "- risk: low, only docs\n")
    res = plan.validate()
    assert "only docs" not in res.stdout + res.stderr


def test_validate_warns_when_the_spec_understates_its_paths(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap()
    plan.config('[sessions]\nhigh_risk_paths = ["src/app/**"]\n').sync()
    _rate(plan, SPEC, "- risk: low\n")
    res = plan.validate()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "WARN" in res.stdout and "1.1.1" in res.stdout and "high" in res.stdout


def test_validate_is_silent_without_ratings(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()
    res = plan.validate()
    assert res.returncode == 0
    assert "risk" not in res.stdout


# ── the gates (manual mode prints the codex command and prompt) ─────────────


def _gate(project: Project, phase: str, rating: str) -> subprocess.CompletedProcess[str]:
    project.write(f"docs/plan/phase-01-users/{LEAF_ID}-user-entity.md",
                  f"# {LEAF_ID} user entity\n- status: in_progress\n{rating}")  # fmt: skip
    write_control(project.root, '[codex]\nmode = "manual"\n')
    return project.run(
        "review_with_codex.sh", None, phase, LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )


@pytest.mark.e2e
@pytest.mark.parametrize("phase", ["plan", "code"])
def test_an_unrated_subtask_reviews_as_before(project: Project, phase: str) -> None:
    res = _gate(project, phase, "")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "model_reasoning_effort=high" in res.stdout
    assert "xhigh" not in res.stdout
    assert "checklist-" not in res.stdout
    assert "Review policy" not in res.stdout


@pytest.mark.e2e
@pytest.mark.parametrize("phase", ["plan", "code"])
def test_a_rated_subtask_gets_its_effort_and_checklists(project: Project, phase: str) -> None:
    res = _gate(project, phase, "- risk: high\n- surfaces: security\n")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "model_reasoning_effort=xhigh" in res.stdout
    assert "Review policy" in res.stdout
    for name in ("high-risk", "security"):
        assert f"{project.root}/.claude/codex/checklist-{name}.md" in res.stdout


@pytest.mark.e2e
def test_an_invalid_rating_stops_the_gate(project: Project) -> None:
    res = _gate(project, "code", "- surfaces: securty\n")
    assert res.returncode == 1
    assert "securty" in res.stderr
    assert "codex exec" not in res.stdout


# ── contract: the checklists and the role rules ─────────────────────────────


@pytest.mark.parametrize("name", CHECKLISTS)
def test_every_builtin_checklist_exists(name: str) -> None:
    text = (CODEX / f"checklist-{name}.md").read_text(encoding="utf-8")
    assert text.startswith("# Review checklist:")
    assert "codex_model" not in text  # a checklist is not a role: no model header
    assert text.count("\n- [ ] ") >= 3


def test_every_builtin_checklist_is_referenced_by_the_builtin_table() -> None:
    cfg = _config.Config()
    named = {c for rule in (*cfg.review_risk.values(), *cfg.review_surfaces.values())
             for c in rule.checklists}  # fmt: skip
    assert named == set(CHECKLISTS)


def test_the_reviewer_checks_the_rating_and_applies_the_checklists() -> None:
    text = (CODEX / "reviewer.md").read_text(encoding="utf-8")
    assert "## Risk and surfaces (issue #44)" in text
    section = text.split("## Risk and surfaces (issue #44)", 1)[1]
    assert "understated" in section
    assert "CHANGES_REQUESTED" in section
    assert "checklist" in section


def test_the_planner_checks_the_rating_at_phase_and_task_gates() -> None:
    text = (CODEX / "codex-planner.md").read_text(encoding="utf-8")
    assert "## Risk and surfaces (issue #44)" in text
    section = text.split("## Risk and surfaces (issue #44)", 1)[1]
    assert "understated" in section
    assert "CHANGES_REQUESTED" in section
