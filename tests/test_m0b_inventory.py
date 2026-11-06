"""M0b (#16), block 1: the sealed legacy inventory.

`bash harness/legacy_inventory.sh create|review|seal|verify` (human-run, lib/_legacy.py)
and the library `legacy_entry` / `is_legacy_unchanged` the later rules (I1, I4, I16)
will read. Plan 0.6. Block 2 covers the guard protection and docs/usage.md.

Two fixtures: a fast `Plan` repo (conftest builder + git) for most behaviour, and a
copy of the I0 legacy project (real cycle, tests/i0_legacy.py) for the real shapes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import ISSUE, SPEC, TOOLKIT, Plan
from i0_cycle import SID
from i0_legacy import BASELINE_SPEC, ORACLE, build_legacy, copy_legacy
from i0_projects import Installed

INVENTORY = "progress/legacy_inventory.json"
REVIEW = "progress/legacy_inventory_review.md"
SEAL = "progress/legacy_inventory.sha256"
ARTIFACTS = (INVENTORY, REVIEW, SEAL)

FAKE_CODEX = r"""#!/usr/bin/env bash
# Fake Codex for M0b: logs its argv, writes FAKE_CODEX_MSG to the -o file.
printf '%s\n' "$*" >> "$FAKE_CODEX_LOG"
cat > "$FAKE_CODEX_LOG.prompt"
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
if [ -n "$out" ] && [ -n "${FAKE_CODEX_MSG+x}" ]; then printf '%s' "$FAKE_CODEX_MSG" > "$out"; fi
exit "${FAKE_CODEX_RC:-0}"
"""
APPROVED_MSG = "All entries match the tree at the commit.\n\nVERDICT: INVENTORY_APPROVED\n"
CHANGES_MSG = "Leaf 1.1.1 is missing its oracle.\n\nVERDICT: CHANGES_REQUESTED\n"


# ── helpers ──────────────────────────────────────────────────────────────────


@dataclass
class Repo:
    """A project whose install root may sit below the git root (guakamole shape)."""

    root: Path  # install root
    repo: Path  # git top
    env: dict[str, str]

    def git(self, *args: str) -> str:
        res = subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, text=True, capture_output=True, check=True
        )
        return res.stdout.strip()

    def commit(self, msg: str = "wip") -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")

    def inv(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "harness/legacy_inventory.sh", *args],
            cwd=self.root,
            env={**self.env, **(env or {})},
            text=True,
            capture_output=True,
            check=False,
        )

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def data(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads((self.root / INVENTORY).read_text())
        return loaded


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _env(tmp: Path) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "GIT_"))}
    cfg = tmp / "empty.gitconfig"
    cfg.touch()
    return {
        **base,
        "GIT_CONFIG_GLOBAL": str(cfg),
        "GIT_AUTHOR_NAME": "dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }


def _fake_codex(tmp: Path, env: dict[str, str]) -> dict[str, str]:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "codex").write_text(FAKE_CODEX)
    (bin_dir / "codex").chmod(0o755)
    return {**env, "PATH": f"{bin_dir}:{env['PATH']}", "FAKE_CODEX_LOG": str(tmp / "codex.log")}


def _plan_repo(tmp: Path, subdir: str = "") -> tuple[Repo, Plan]:
    """git repo (first commit without feature_list.json) + harness link + Plan builder."""
    top = tmp / "repo"
    top.mkdir()
    env = _fake_codex(tmp, _env(tmp))
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=top, env=env, check=True)
    (top / "README.md").write_text("readme\n")
    root = top / subdir if subdir else top
    root.mkdir(exist_ok=True)
    (root / "harness").symlink_to(TOOLKIT / "lib")
    (root / ".gitignore").write_text("harness\n")
    repo = Repo(root=root, repo=top, env=env)
    repo.commit("pre-plan")
    return repo, Plan(root)


def _basic(tmp: Path, subdir: str = "") -> Repo:
    """Phase 01 approved: 1.1.1 pending, 1.1.2 baseline. No seals, no reviews."""
    repo, plan = _plan_repo(tmp, subdir)
    plan.phase("01", "1.1").sub("1.1.1").sub("1.1.2", status="baseline")
    plan.approve_roadmap().approve_phase("01").sync()
    repo.commit("plan")
    return repo


def _review(repo: Repo, msg: str = APPROVED_MSG, rc: int = 0) -> subprocess.CompletedProcess[str]:
    return repo.inv("review", env={"FAKE_CODEX_MSG": msg, "FAKE_CODEX_RC": str(rc)})


def _sealed(repo: Repo) -> None:
    head = repo.git("rev-parse", "HEAD")
    for res in (repo.inv("create", "--at", head), _review(repo), repo.inv("seal")):
        assert res.returncode == 0, res.stdout + res.stderr


def _load(name: str) -> ModuleType:
    """Import lib/<name>.py the way the scripts do (lib/ on sys.path for its siblings)."""
    lib_dir = str(TOOLKIT / "lib")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    spec = importlib.util.spec_from_file_location(name, TOOLKIT / "lib" / f"{name}.py")
    assert spec is not None and spec.loader is not None, f"lib/{name}.py is missing"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refused(res: subprocess.CompletedProcess[str]) -> bool:
    """A refusal by the command itself (not a missing script: bash gives 127)."""
    return res.returncode in (1, 2)


def _no_artifacts(root: Path) -> None:
    for rel in ARTIFACTS:
        assert not (root / rel).exists(), rel


@pytest.fixture
def lib() -> ModuleType:
    return _load("_legacy")


# ── create on the real I0 legacy project ─────────────────────────────────────


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    return build_legacy(tmp_path_factory.mktemp("legacy"))


@pytest.fixture
def lrepo(legacy: Installed, tmp_path: Path) -> Repo:
    copy = copy_legacy(legacy, tmp_path / "copy")
    return Repo(root=copy.root, repo=copy.repo, env=_fake_codex(tmp_path, copy.env))


def test_create_records_the_done_leaf_with_its_evidence(lrepo: Repo) -> None:
    head = lrepo.git("rev-parse", "HEAD")
    res = lrepo.inv("create", "--at", head)
    assert res.returncode == 0, res.stdout + res.stderr
    data = lrepo.data()
    assert data["version"] == 1 and data["commit"] == head
    assert data["install_root"] == "." and data["created_at"] and data["toolkit"]
    leaf = data["leaves"][SID]
    assert leaf["status"] == "done" and leaf["baseline"] is False
    assert leaf["declared_files"] == ["src/demo/greet.py"]
    spec_path = leaf["spec"]["path"]
    assert leaf["spec"]["sha256"] == _sha(lrepo.root / spec_path)
    assert leaf["acceptance"] == [{"path": ORACLE, "sha256": _sha(lrepo.root / ORACLE)}]
    seal = f"progress/acceptance_{SID}.sha256"
    assert leaf["seal"] == {"path": seal, "sha256": _sha(lrepo.root / seal)}
    for key, name, verdict in (
        ("plan_review", "plan_review", "PLAN_APPROVED"),
        ("review", "review", "APPROVED"),
    ):
        rel = f"progress/{name}_{SID}.md"
        assert leaf[key] == {"path": rel, "sha256": _sha(lrepo.root / rel), "verdict": verdict}


def test_create_marks_the_baseline_leaf_without_changing_its_status(lrepo: Repo) -> None:
    assert lrepo.inv("create", "--at", "HEAD").returncode == 0
    leaf = lrepo.data()["leaves"]["1.2.1"]
    assert leaf["status"] == "baseline" and leaf["baseline"] is True
    assert leaf["spec"]["path"] == BASELINE_SPEC
    assert leaf["acceptance"] == [] and leaf["seal"] is None and leaf["review"] is None
    assert '"status": "baseline"' in (lrepo.root / "feature_list.json").read_text()


def test_create_records_approved_phase_and_roadmap_content(lrepo: Repo) -> None:
    from_seal = _seal_module()
    assert lrepo.inv("create", "--at", "HEAD").returncode == 0
    data = lrepo.data()
    phase = data["phases"]["01"]
    assert phase["verdict"] == "PHASE_APPROVED"
    assert phase["seal"]["path"] == "progress/plan_phase_01.sha256"
    assert phase["approved"] == from_seal.phase_entries(lrepo.root, "01")
    roadmap = data["roadmap"]
    assert roadmap["verdict"] == "ROADMAP_APPROVED"
    assert roadmap["seal"]["sha256"] == _sha(lrepo.root / "progress/plan_roadmap.sha256")
    assert roadmap["approved"] == from_seal.roadmap_entries(lrepo.root)


def test_create_leaves_out_a_leaf_added_after_the_commit(lrepo: Repo) -> None:
    old = lrepo.git("rev-parse", "HEAD")
    folder = lrepo.root / BASELINE_SPEC
    lrepo.write(
        str(folder.parent.relative_to(lrepo.root) / "1.2.2-later.md"),
        "# 1.2.2 Later\n- status: pending\n- layer: domain\n- depends: none\n"
        "- files:\n  - src/demo/later.py\n",
    )
    subprocess.run(
        ["python3", "harness/sync_plan.py"],
        cwd=lrepo.root,
        env=lrepo.env,
        check=True,
        capture_output=True,
    )
    lrepo.commit("later leaf")
    assert lrepo.inv("create", "--at", old).returncode == 0
    assert "1.2.2" not in lrepo.data()["leaves"]
    assert lrepo.data()["commit"] == old


def test_create_reads_the_commit_not_the_working_tree(lrepo: Repo) -> None:
    spec = lrepo.root / BASELINE_SPEC
    committed = _sha(spec)
    spec.write_text(spec.read_text() + "\nuncommitted note\n")
    assert lrepo.inv("create", "--at", "HEAD").returncode == 0
    assert lrepo.data()["leaves"]["1.2.1"]["spec"]["sha256"] == committed


def _seal_module() -> ModuleType:
    return _load("_plan_seal")


# ── more states: issue subtask, approved draft, pending leaf ─────────────────


def _extend(lrepo: Repo) -> None:
    """Adds ISS-1 (ISSUE_APPROVED, subtask ISS-1.1 done), an approved draft, a pending leaf."""
    plan = lrepo.root / "docs" / "plan"
    issues = plan / "issues"
    (issues / "ISS-1-i1").mkdir(parents=True)
    (issues / "ISS-1-i1.md").write_text(
        ISSUE.format(iid="ISS-1", n=1, type="fix", priority="next", extra="")
    )
    (issues / "ISS-1-i1" / "ISS-1.1-s.md").write_text(
        SPEC.format(sid="ISS-1.1", status="done", depends="none", slug="ISS11")
    )
    lrepo.write("progress/issue_review_ISS-1.md", "# Review\n\n**Verdict:** ISSUE_APPROVED\n")
    lrepo.write("progress/issue_draft.md", "Title: draft\n\nBody.\n")
    lrepo.write("progress/issue_draft_review.md", "# Review\n\n**Verdict:** DRAFT_APPROVED\n")
    lrepo.write(
        str((lrepo.root / BASELINE_SPEC).parent.relative_to(lrepo.root) / "1.2.2-todo.md"),
        "# 1.2.2 Todo\n- status: pending\n- layer: domain\n- depends: none\n"
        "- files:\n  - src/demo/todo.py\n",
    )
    subprocess.run(
        ["python3", "harness/sync_plan.py"],
        cwd=lrepo.root,
        env=lrepo.env,
        check=True,
        capture_output=True,
    )
    lrepo.commit("more legacy states")


def test_create_records_issue_draft_and_pending_states(lrepo: Repo) -> None:
    _extend(lrepo)
    assert lrepo.inv("create", "--at", "HEAD").returncode == 0
    data = lrepo.data()
    sub = data["leaves"]["ISS-1.1"]
    assert sub["status"] == "done" and sub["baseline"] is False
    issue = data["issues"]["ISS-1"]
    assert issue["verdict"] == "ISSUE_APPROVED" and issue["seal"] is None
    assert issue["approved"] == _seal_module().issue_entries(lrepo.root, "ISS-1")
    draft = data["issue_draft"]
    assert draft["verdict"] == "DRAFT_APPROVED" and draft["seal"] is None
    assert draft["approved"] == {
        "progress/issue_draft.md": _sha(lrepo.root / "progress/issue_draft.md")
    }
    pending = data["leaves"]["1.2.2"]
    assert pending["status"] == "pending" and pending["baseline"] is False
    assert pending["acceptance"] == [] and pending["seal"] is None
    assert pending["plan_review"] is None and pending["review"] is None


def test_pending_leaf_is_never_reported_as_done(lrepo: Repo, lib: ModuleType) -> None:
    _extend(lrepo)
    _sealed(lrepo)
    entry = lib.legacy_entry(lrepo.root, "1.2.2")
    assert entry is not None and entry["status"] == "pending" and entry["baseline"] is False


# ── create on the fast Plan repo ─────────────────────────────────────────────


def test_absent_seal_and_verdict_are_recorded_as_null(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    data = repo.data()
    assert data["phases"]["01"]["seal"] is None and data["roadmap"]["seal"] is None
    leaf = data["leaves"]["1.1.1"]
    assert leaf["seal"] is None and leaf["plan_review"] is None and leaf["review"] is None
    assert leaf["acceptance"] == []


def test_monorepo_paths_are_relative_to_the_install_root(tmp_path: Path) -> None:
    repo = _basic(tmp_path, subdir="backend")
    res = repo.inv("create", "--at", "HEAD")
    assert res.returncode == 0, res.stdout + res.stderr
    data = repo.data()
    assert data["install_root"] == "backend"
    assert data["leaves"]["1.1.1"]["spec"]["path"] == "docs/plan/phase-01-p01/1.1.1-s.md"
    assert all(not p.startswith("backend/") for p in data["phases"]["01"]["approved"])
    assert not (repo.repo / INVENTORY).exists()


def test_create_requires_at(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    assert _refused(repo.inv("create"))
    _no_artifacts(repo.root)


def test_create_refuses_a_commit_without_feature_list(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    first = repo.git("rev-list", "--max-parents=0", "HEAD")
    assert _refused(repo.inv("create", "--at", first))
    _no_artifacts(repo.root)


def test_create_refuses_a_commit_that_is_not_an_ancestor(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    repo.git("switch", "-qc", "side")
    repo.write("side.txt", "x\n")
    side = repo.commit("side")
    repo.git("switch", "-q", "main")
    assert _refused(repo.inv("create", "--at", side))
    _no_artifacts(repo.root)


def test_create_refuses_a_second_time(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    before = (repo.root / INVENTORY).read_bytes()
    assert _refused(repo.inv("create", "--at", "HEAD"))
    assert (repo.root / INVENTORY).read_bytes() == before


@pytest.mark.parametrize("left", [REVIEW, SEAL])
def test_create_refuses_when_any_artifact_is_left(tmp_path: Path, left: str) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    for rel in ARTIFACTS:
        if rel != left:
            (repo.root / rel).unlink()
    assert _refused(repo.inv("create", "--at", "HEAD"))
    assert not (repo.root / INVENTORY).exists()


# ── review (fake Codex) ──────────────────────────────────────────────────────


def test_review_approved_writes_the_review_tied_to_the_json(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    res = _review(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    text = (repo.root / REVIEW).read_text()
    assert "INVENTORY_APPROVED" in text
    assert _sha(repo.root / INVENTORY) in text
    assert repo.data()["commit"] in text
    log = (tmp_path / "codex.log").read_text()
    assert "-s read-only" in log and "-o " in log


def test_review_changes_requested_is_written_and_blocks_seal(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    _review(repo, CHANGES_MSG)
    assert "CHANGES_REQUESTED" in (repo.root / REVIEW).read_text()
    assert _refused(repo.inv("seal"))
    assert not (repo.root / SEAL).exists()


@pytest.mark.parametrize(
    ("msg", "rc"),
    [
        (APPROVED_MSG, 1),  # Codex failed
        ("", 0),  # empty output
        ("Looks fine.\n", 0),  # no verdict line
        ("Hm.\n\nVERDICT: MAYBE\n", 0),  # unknown verdict
        ("VERDICT: INVENTORY_APPROVED\n\nTrailing words.\n", 0),  # not the last line
    ],
)
def test_review_failures_write_no_review_file(tmp_path: Path, msg: str, rc: int) -> None:
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert _review(repo, msg, rc).returncode == 1
    assert not (repo.root / REVIEW).exists()


# ── seal ─────────────────────────────────────────────────────────────────────


def test_seal_refuses_without_a_review(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert _refused(repo.inv("seal"))
    assert not (repo.root / SEAL).exists()


def test_seal_refuses_a_json_edited_after_the_review(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert _review(repo).returncode == 0
    data = repo.data()
    data["leaves"].pop("1.1.2")
    (repo.root / INVENTORY).write_text(json.dumps(data))
    assert _refused(repo.inv("seal"))
    assert not (repo.root / SEAL).exists()


def test_seal_after_approved_review(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    assert (repo.root / SEAL).is_file()


# ── verify ───────────────────────────────────────────────────────────────────


def test_verify_warns_when_not_created(tmp_path: Path) -> None:
    res = _basic(tmp_path).inv("verify")
    assert res.returncode == 0 and "WARN" in res.stdout and "not created" in res.stdout


def test_verify_ok_when_sealed(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    res = repo.inv("verify")
    assert res.returncode == 0 and "OK" in res.stdout, res.stdout + res.stderr


def _tamper(repo: Repo) -> None:
    path = repo.root / INVENTORY
    path.write_text(path.read_text().replace('"pending"', '"done"'))


def _drop_seal(repo: Repo) -> None:
    (repo.root / SEAL).unlink()


def _rewrite_history(repo: Repo) -> None:
    repo.git("reset", "-q", "--hard", "HEAD~1")


def _invalid_json(repo: Repo) -> None:
    (repo.root / INVENTORY).write_text("{not json")


@pytest.mark.parametrize("breaker", [_tamper, _drop_seal, _rewrite_history, _invalid_json])
def test_verify_fails(tmp_path: Path, breaker: Any) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    breaker(repo)
    res = repo.inv("verify")
    assert res.returncode == 1 and "FAIL" in res.stdout, res.stdout + res.stderr


def test_verify_fails_when_the_review_is_not_approved(tmp_path: Path) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    review = repo.root / REVIEW
    review.write_text(review.read_text().replace("INVENTORY_APPROVED", "CHANGES_REQUESTED"))
    assert repo.inv("verify").returncode == 1


# ── library: legacy_entry / is_legacy_unchanged ──────────────────────────────


def test_legacy_entry_needs_a_verified_inventory(tmp_path: Path, lib: ModuleType) -> None:
    repo = _basic(tmp_path)
    assert lib.legacy_entry(repo.root, "1.1.1") is None
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert lib.legacy_entry(repo.root, "1.1.1") is None  # created, not sealed
    assert _review(repo).returncode == 0 and repo.inv("seal").returncode == 0
    entry = lib.legacy_entry(repo.root, "1.1.2")
    assert entry is not None and entry["baseline"] is True
    assert lib.legacy_entry(repo.root, "9.9.9") is None
    _tamper(repo)
    assert lib.legacy_entry(repo.root, "1.1.2") is None


def _append(path: Path, text: str = "\nchanged\n") -> None:
    path.write_text(path.read_text() + text)


def test_unchanged_leaf_spec_and_oracle(lrepo: Repo, lib: ModuleType) -> None:
    _sealed(lrepo)
    assert lib.is_legacy_unchanged(lrepo.root, SID) is True
    _append(lrepo.root / ORACLE, "\n# edited\n")
    assert lib.is_legacy_unchanged(lrepo.root, SID) is False


def test_leaf_spec_change(lrepo: Repo, lib: ModuleType) -> None:
    _sealed(lrepo)
    _append(lrepo.root / lrepo.data()["leaves"][SID]["spec"]["path"])
    assert lib.is_legacy_unchanged(lrepo.root, SID) is False


@pytest.mark.parametrize(
    "rel",
    [
        "tests/acceptance/test_1.2.1.py",  # an oracle that did not exist
        "progress/acceptance_1.2.1.sha256",  # a seal that did not exist
        "progress/review_1.2.1.md",  # a verdict that did not exist
    ],
)
def test_leaf_evidence_that_appears_is_a_change(lrepo: Repo, lib: ModuleType, rel: str) -> None:
    _sealed(lrepo)
    assert lib.is_legacy_unchanged(lrepo.root, "1.2.1") is True
    lrepo.write(rel, "new\n")
    assert lib.is_legacy_unchanged(lrepo.root, "1.2.1") is False


def test_leaf_evidence_that_disappears_is_a_change(lrepo: Repo, lib: ModuleType) -> None:
    _sealed(lrepo)
    (lrepo.root / f"progress/review_{SID}.md").unlink()
    assert lib.is_legacy_unchanged(lrepo.root, SID) is False


PHASE_SPEC = "docs/plan/phase-01-p01/1.1.1-s.md"


def test_phase_status_change_alone_keeps_it_unchanged(tmp_path: Path, lib: ModuleType) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    spec = repo.root / PHASE_SPEC
    assert lib.is_legacy_unchanged(repo.root, "01") is True
    spec.write_text(spec.read_text().replace("- status: pending", "- status: in_progress"))
    assert lib.is_legacy_unchanged(repo.root, "01") is True
    spec.write_text(spec.read_text().replace("- status: in_progress", "- status: baseline"))
    assert lib.is_legacy_unchanged(repo.root, "01") is False


def test_phase_spec_body_change(tmp_path: Path, lib: ModuleType) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    _append(repo.root / PHASE_SPEC)
    assert lib.is_legacy_unchanged(repo.root, "01") is False


def test_phase_with_a_new_spec_is_a_change(tmp_path: Path, lib: ModuleType) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    spec = repo.root / PHASE_SPEC
    (spec.parent / "1.1.3-s.md").write_text(spec.read_text().replace("1.1.1", "1.1.3"))
    assert lib.is_legacy_unchanged(repo.root, "01") is False


@pytest.mark.parametrize("rel", ["docs/plan/PLAN.md", "docs/plan/phase-01-p01.md"])
def test_roadmap_plan_md_and_phase_header(tmp_path: Path, lib: ModuleType, rel: str) -> None:
    repo = _basic(tmp_path)
    _sealed(repo)
    assert lib.is_legacy_unchanged(repo.root, "roadmap") is True
    path = repo.root / rel
    path.write_text(path.read_text().replace("\n", "\nedited line\n", 1))
    assert lib.is_legacy_unchanged(repo.root, "roadmap") is False


def test_issue_spec_and_draft(lrepo: Repo, lib: ModuleType) -> None:
    _extend(lrepo)
    _sealed(lrepo)
    assert lib.is_legacy_unchanged(lrepo.root, "ISS-1") is True
    assert lib.is_legacy_unchanged(lrepo.root, "issue_draft") is True
    _append(lrepo.root / "docs/plan/issues/ISS-1-i1/ISS-1.1-s.md")
    _append(lrepo.root / "progress/issue_draft.md")
    assert lib.is_legacy_unchanged(lrepo.root, "ISS-1") is False
    assert lib.is_legacy_unchanged(lrepo.root, "issue_draft") is False


def test_unknown_item_or_unverified_inventory_is_not_unchanged(
    tmp_path: Path, lib: ModuleType
) -> None:
    repo = _basic(tmp_path)
    assert lib.is_legacy_unchanged(repo.root, "01") is False  # no inventory
    _sealed(repo)
    assert lib.is_legacy_unchanged(repo.root, "9.9.9") is False


# ── Codex review of block 1 (codex_verdict_m0b_b1_decisions.md) ──────────────


def test_a_partial_draft_at_the_commit_is_recorded_as_null(tmp_path: Path) -> None:
    """D1: a draft review or seal left without the draft itself."""
    repo = _basic(tmp_path)
    repo.write("progress/issue_draft_review.md", "# Review\n\n**Verdict:** DRAFT_APPROVED\n")
    repo.write("progress/issue_draft.sha256", "0" * 64 + "  progress/issue_draft.md\n")
    repo.commit("partial draft")
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert repo.data()["issue_draft"] is None


def test_the_review_prompt_says_links_are_skipped(tmp_path: Path) -> None:
    """D3: Codex must know the links of the tree were not inventoried."""
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert _review(repo).returncode == 0
    prompt = (tmp_path / "codex.log.prompt").read_text()
    assert "symlink" in prompt.lower()


def test_a_failed_rerun_removes_the_previous_review(tmp_path: Path) -> None:
    """D5: an old approval is never left looking current."""
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert _review(repo).returncode == 0
    assert _review(repo, "no verdict\n").returncode == 1
    assert not (repo.root / REVIEW).exists()
    assert _refused(repo.inv("seal"))


def test_verify_fails_on_a_seal_with_the_wrong_path(tmp_path: Path) -> None:
    """D6: the seal holds exactly the hash and the inventory path."""
    repo = _basic(tmp_path)
    _sealed(repo)
    seal = repo.root / SEAL
    seal.write_text(seal.read_text().replace("legacy_inventory.json", "other.json"))
    res = repo.inv("verify")
    assert res.returncode == 1 and "FAIL" in res.stdout


def test_verify_fails_on_a_broken_link_artifact(tmp_path: Path) -> None:
    """D6: a dangling link is not "not created"."""
    repo = _basic(tmp_path)
    (repo.root / "progress").mkdir(exist_ok=True)
    (repo.root / SEAL).symlink_to(tmp_path / "nowhere")
    res = repo.inv("verify")
    assert res.returncode == 1 and "FAIL" in res.stdout


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("spec", "../../../outside.md"),
        ("spec", "/etc/hostname"),
        ("id", "../1.1.1"),
        ("id", "1.1.1/x"),
    ],
)
def test_create_refuses_unsafe_ids_and_paths(tmp_path: Path, key: str, value: str) -> None:
    """D9: feature_list.json is untrusted: nothing is read outside the extracted tree."""
    repo = _basic(tmp_path)
    path = repo.root / "feature_list.json"
    data = json.loads(path.read_text())
    data["features"][0]["subtasks"][0][key] = value
    path.write_text(json.dumps(data))
    repo.commit("unsafe")
    assert _refused(repo.inv("create", "--at", "HEAD"))
    _no_artifacts(repo.root)


def test_writes_never_follow_a_planted_temp_file(tmp_path: Path) -> None:
    """D9: the temp file is created exclusively, never reused through a planted link."""
    repo = _basic(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched\n")
    (repo.root / "progress").mkdir(exist_ok=True)
    for name in ("legacy_inventory.json", "legacy_inventory.sha256"):
        (repo.root / "progress" / f".{name}.tmp").symlink_to(victim)
    _sealed(repo)
    assert victim.read_text() == "untouched\n"


# ── Codex review round 2 (codex_verdict_m0b_b1_decisions2.md) ────────────────


def test_a_rerun_on_an_invalid_json_removes_the_previous_review(tmp_path: Path) -> None:
    """D5: the old review goes before anything else can fail."""
    repo = _basic(tmp_path)
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert _review(repo).returncode == 0
    (repo.root / INVENTORY).write_text("{not json")
    assert _review(repo).returncode == 1
    assert not (repo.root / REVIEW).exists()


MISSING_SPEC = "docs/plan/phase-01-p01/1.1.1-missing.md"


def test_a_declared_spec_absent_at_the_commit_keeps_its_path(
    tmp_path: Path, lib: ModuleType
) -> None:
    """D7: an absent spec is recorded with its path, so its later appearance is a change."""
    repo = _basic(tmp_path)
    path = repo.root / "feature_list.json"
    data = json.loads(path.read_text())
    data["features"][0]["subtasks"][0]["spec"] = MISSING_SPEC
    path.write_text(json.dumps(data))
    repo.commit("spec declared, file absent")
    _sealed(repo)
    assert repo.data()["leaves"]["1.1.1"]["spec"] == {"path": MISSING_SPEC, "sha256": None}
    assert lib.is_legacy_unchanged(repo.root, "1.1.1") is True
    repo.write(MISSING_SPEC, "# appeared later\n")
    assert lib.is_legacy_unchanged(repo.root, "1.1.1") is False
