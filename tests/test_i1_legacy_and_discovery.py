"""I1 (issue #18) block 2: exact-stem discovery, sealing baseline leaves, legacy exemption."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from i0_cycle import SID
from i0_legacy import BASELINE_SPEC, ORACLE, SPEC, build_legacy, copy_legacy
from i0_projects import Installed
from test_m0b_inventory import INVENTORY, Repo, _fake_codex, _sealed

ACC_SEAL = f"progress/acceptance_{SID}.sha256"
PHASE_SEAL = "progress/plan_phase_01.sha256"
STRAY = f"tests/acceptance/test_{SID}.2.py"
BASELINE_TEST = "tests/acceptance/test_1.2.1.py"
BASELINE_SEAL = "progress/acceptance_1.2.1.sha256"
EXEMPT = "legacy item(s) exempt"
NO_INVENTORY = "legacy inventory not created"


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    return build_legacy(tmp_path_factory.mktemp("legacy"))


@pytest.fixture
def repo(legacy: Installed, tmp_path: Path) -> Repo:
    copy = copy_legacy(legacy, tmp_path / "copy")
    return Repo(root=copy.root, repo=copy.repo, env=_fake_codex(tmp_path, copy.env))


def _run(repo: Repo, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=repo.root, env=repo.env, text=True, capture_output=True, check=False
    )


def _integrity(repo: Repo) -> subprocess.CompletedProcess[str]:
    return _run(repo, "python3", "harness/_guard.py", "integrity")


def _seal(repo: Repo, leaf: str) -> subprocess.CompletedProcess[str]:
    return _run(repo, "bash", "harness/review_with_codex.sh", "seal", leaf)


def _has(res: subprocess.CompletedProcess[str], kind: str, *needles: str) -> bool:
    lines = (res.stdout + res.stderr).splitlines()
    return any(kind in ln and all(n in ln for n in needles) for ln in lines)


def _discovered(repo: Repo, leaf: str) -> list[str]:
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, 'harness'); import _harness; "
        f"print(*[p.name for p in _harness.acceptance_files(Path('.'), {leaf!r})])"
    )
    res = _run(repo, "python3", "-c", code)
    assert res.returncode == 0, res.stderr
    return res.stdout.split()


# ── exact-stem discovery ─────────────────────────────────────────────────────


def test_discovery_does_not_cross_leaf_ids_in_either_direction(repo: Repo) -> None:
    repo.write("tests/acceptance/test_1.1.py", "x = 1\n")
    assert _discovered(repo, "1.1") == ["test_1.1.py"]
    assert _discovered(repo, SID) == [f"test_{SID}.py"]


def test_seal_leaves_out_a_longer_id_matching_the_glob(repo: Repo) -> None:
    repo.write(STRAY, "x = 1\n")
    (repo.root / ACC_SEAL).unlink()
    assert _seal(repo, SID).returncode == 0
    sealed = (repo.root / ACC_SEAL).read_text()
    assert ORACLE in sealed and STRAY not in sealed


def test_completeness_ignores_a_longer_id_matching_the_glob(repo: Repo) -> None:
    repo.write(STRAY, "x = 1\n")
    (repo.root / "tests/acceptance" / f"test_{SID}.extra").write_text("x\n")
    res = _integrity(repo)
    assert _has(res, "WARN", f"test_{SID}.extra", "not in its seal"), res.stdout
    assert not _has(res, "WARN", STRAY), res.stdout


def test_inventory_records_only_exact_stem_tests(repo: Repo) -> None:
    repo.write(STRAY, "x = 1\n")
    repo.write(f"tests/acceptance/test_{SID}_more.py", "x = 1\n")
    repo.commit("strays")
    assert repo.inv("create", "--at", "HEAD").returncode == 0
    assert [a["path"] for a in repo.data()["leaves"][SID]["acceptance"]] == [ORACLE]


# ── sealing a baseline leaf ──────────────────────────────────────────────────


def test_seal_accepts_baseline_leaf_of_approved_phase_and_clears_warn(repo: Repo) -> None:
    repo.write(BASELINE_TEST, "def test_ac1() -> None: ...\n")
    res = _seal(repo, "1.2.1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert BASELINE_TEST in (repo.root / BASELINE_SEAL).read_text()
    assert not _has(_integrity(repo), "WARN", "1.2.1"), "the WARN must be gone"


def test_sealing_a_baseline_leaf_does_not_absorb_a_changed_spec(repo: Repo) -> None:
    repo.write(BASELINE_TEST, "def test_ac1() -> None: ...\n")
    with (repo.root / BASELINE_SPEC).open("a") as spec:
        spec.write("\nChanged after the phase approval.\n")
    assert _seal(repo, "1.2.1").returncode == 0
    res = _integrity(repo)
    assert res.returncode == 1 and _has(res, "FAIL", BASELINE_SPEC), res.stdout


def test_seal_refuses_baseline_leaf_of_unapproved_phase(repo: Repo) -> None:
    repo.write(BASELINE_TEST, "def test_ac1() -> None: ...\n")
    (repo.root / "progress/phase_review_01.md").unlink()
    assert _seal(repo, "1.2.1").returncode != 0
    assert not (repo.root / BASELINE_SEAL).exists()


def test_seal_refuses_pending_leaf_without_plan_approved(repo: Repo) -> None:
    spec = (repo.root / BASELINE_SPEC).parent / "1.2.2-todo.md"
    spec.write_text(
        "# 1.2.2 Todo\n- status: pending\n- layer: domain\n- depends: none\n"
        "- files:\n  - src/demo/todo.py\n"
    )
    assert _run(repo, "python3", "harness/sync_plan.py").returncode == 0
    repo.write("tests/acceptance/test_1.2.2.py", "x = 1\n")
    assert _seal(repo, "1.2.2").returncode != 0
    assert not (repo.root / "progress/acceptance_1.2.2.sha256").exists()


# ── legacy exemption (plan 0.6) ──────────────────────────────────────────────


def test_unchanged_legacy_items_are_exempt_and_counted(repo: Repo) -> None:
    (repo.root / ACC_SEAL).unlink()
    (repo.root / PHASE_SEAL).unlink()
    repo.commit("gaps that predate the rule")
    _sealed(repo)
    res = _integrity(repo)
    assert res.returncode == 0, res.stdout
    assert not _has(res, "WARN", SID), res.stdout
    assert not _has(res, "WARN", "1.2.1"), res.stdout
    assert not _has(res, "WARN", "phase 01"), res.stdout
    assert _has(res, "OK", EXEMPT), res.stdout
    assert not _has(res, "WARN", NO_INVENTORY), res.stdout


def test_done_leaf_loses_the_exemption_when_its_spec_changes(repo: Repo) -> None:
    (repo.root / ACC_SEAL).unlink()
    repo.commit("gap")
    _sealed(repo)
    with (repo.root / SPEC).open("a") as spec:
        spec.write("\nChanged after the inventory.\n")
    assert _has(_integrity(repo), "WARN", SID, "review_with_codex.sh seal")


def test_done_leaf_loses_the_exemption_when_its_oracle_changes(repo: Repo) -> None:
    (repo.root / ACC_SEAL).unlink()
    repo.commit("gap")
    _sealed(repo)
    (repo.root / ORACLE).write_text("changed\n")
    assert _has(_integrity(repo), "WARN", SID, "review_with_codex.sh seal")


def test_baseline_leaf_loses_the_exemption_when_its_spec_changes(repo: Repo) -> None:
    _sealed(repo)
    with (repo.root / BASELINE_SPEC).open("a") as spec:
        spec.write("\nChanged after the inventory.\n")
    assert _has(_integrity(repo), "WARN", "1.2.1", "no acceptance test")


def test_tampered_inventory_fails_and_exempts_nothing(repo: Repo) -> None:
    _sealed(repo)
    path = repo.root / INVENTORY
    data = json.loads(path.read_text())
    data["created_at"] = "tampered"
    path.write_text(json.dumps(data))
    res = _integrity(repo)
    assert res.returncode == 1, res.stdout
    assert _has(res, "FAIL", "legacy"), res.stdout
    assert _has(res, "WARN", "1.2.1", "no acceptance test"), res.stdout
    assert not _has(res, "OK", EXEMPT), res.stdout


def test_no_inventory_warns_once_and_reports_the_gaps(repo: Repo) -> None:
    res = _integrity(repo)
    assert res.returncode == 0, res.stdout
    lines = [ln for ln in res.stdout.splitlines() if NO_INVENTORY in ln]
    assert len(lines) == 1 and "legacy_inventory.sh create --at" in lines[0], res.stdout
    assert _has(res, "WARN", "1.2.1", "no acceptance test"), res.stdout


def _seal_baseline_leaf(repo: Repo) -> None:
    repo.write(BASELINE_TEST, "def test_ac1() -> None: ...\n")
    assert _seal(repo, "1.2.1").returncode == 0


def test_no_inventory_and_no_gap_says_nothing_about_the_inventory(repo: Repo) -> None:
    _seal_baseline_leaf(repo)
    res = _integrity(repo)
    assert "legacy" not in (res.stdout + res.stderr).lower(), res.stdout


@pytest.mark.parametrize("seal", [PHASE_SEAL, ACC_SEAL])
def test_no_inventory_with_a_single_gap_warns_once(repo: Repo, seal: str) -> None:
    _seal_baseline_leaf(repo)
    (repo.root / seal).unlink()
    res = _integrity(repo)
    assert res.stdout.count(NO_INVENTORY) == 1, res.stdout


def test_make_check_stays_zero_with_a_verified_inventory(repo: Repo) -> None:
    _sealed(repo)
    res = _run(repo, "make", "-s", "--no-print-directory", "check")
    assert res.returncode == 0, res.stdout + res.stderr
