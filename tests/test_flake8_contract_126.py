"""flake8 configuration in the sealed verification contract (issue #126).

.flake8 and the [flake8] sections of setup.cfg / tox.ini (and [tool.flake8] of
pyproject.toml, read by the flake8-pyproject plugin) are inventoried like ruff/mypy
config: drift is a [WARN] through the launcher. Baselines sealed before this change
(version 1) did not inventory flake8: their flake8 side is derived from the baseline's
`from` commit, so a project without flake8 sees no new warning and a project whose
flake8 config is unchanged since that commit sees none either.
"""

from __future__ import annotations

import json

from conftest import Project, write_payload
from test_verification_contract_66 import (
    BASELINE,
    PROPOSAL,
    PYPROJECT,
    _baseline,
    _contract_warns,
    _launch,
    _py,
    _python_project,
    _review,
)

FLAKE8 = "[flake8]\nmax-line-length = 100\nselect = E,F,W\n"
SETUP_CFG = "[metadata]\nname = p\n\n[flake8]\nmax-line-length = 100\n"
TOX_INI = "[tox]\nenvlist = py312\n\n[flake8]\nmax-line-length = 100\n"
TOX_NO_FLAKE8 = "[tox]\nenvlist = py312\n\n[testenv]\ncommands = pytest\n"
PYPROJECT_FLAKE8 = PYPROJECT + "\n[tool.flake8]\nmax-line-length = 100\n"


def _flake8_project(project: Project) -> None:
    _python_project(project)
    project.write(".flake8", FLAKE8)
    project.write("setup.cfg", SETUP_CFG)
    project.write("tox.ini", TOX_INI)
    project.write("pyproject.toml", PYPROJECT_FLAKE8)


def _baseline_symlinked(project: Project) -> None:
    """baseline-create compares a symlink's target content with its blob (pre-existing,
    out of #126's scope): it warns, so this one does not demand zero warnings."""
    project.commit_all("pre-rule")
    assert _py(project, "baseline-create", "--from", "HEAD").returncode == 0
    assert _py(project, "baseline-seal").returncode == 0


def _as_v1(project: Project) -> None:
    """Turn the sealed baseline into one written before #126, and reseal it."""
    path = project.root / BASELINE
    data = json.loads(path.read_text())
    data["version"] = 1
    data["inputs"] = {k: v for k, v in data["inputs"].items() if "flake8" not in k}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    res = _py(project, "baseline-seal")
    assert res.returncode == 0, res.stdout + res.stderr


# ── inventory and drift ──────────────────────────────────────────────────────


def test_flake8_config_is_inventoried_in_every_location(project: Project) -> None:
    _flake8_project(project)
    project.write("src/pkg/.flake8", FLAKE8)
    inv = json.loads(_py(project, "inventory").stdout)
    for key in (
        ".flake8",
        "src/pkg/.flake8",
        "setup.cfg[flake8]",
        "tox.ini[flake8]",
        "pyproject.toml[tool.flake8]",
    ):
        assert key in inv, (key, sorted(inv))


def test_relaxing_flake8_through_any_file_warns(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    data = json.loads((project.root / BASELINE).read_text())
    assert data["version"] == 2
    assert _contract_warns(_launch(project)) == []
    cases = {
        ".flake8": "[flake8]\nmax-line-length = 300\nextend-ignore = E,W\n",
        "setup.cfg": SETUP_CFG.replace("100", "300"),
        "tox.ini": TOX_INI + "extend-ignore = F401\n",
        "pyproject.toml": PYPROJECT_FLAKE8.replace("max-line-length = 100", "max-line-length = 1"),
        "src/pkg/.flake8": "[flake8]\nignore = E\n",
    }
    for rel, text in cases.items():
        original = (project.root / rel).read_text() if (project.root / rel).is_file() else None
        project.write(rel, text)
        res = _launch(project)
        assert res.returncode == 0
        warns = _contract_warns(res)
        assert any(rel in w for w in warns), (rel, res.stdout + res.stderr)
        if original is None:
            (project.root / rel).unlink()
        else:
            project.write(rel, original)
    assert _contract_warns(_launch(project)) == []


def test_removing_flake8_config_warns(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    (project.root / ".flake8").unlink()
    project.write("tox.ini", TOX_NO_FLAKE8)
    warns = _contract_warns(_launch(project))
    assert any(".flake8 removed" in w for w in warns), warns
    assert any("tox.ini[flake8] removed" in w for w in warns), warns


def test_unparsable_tox_ini_with_flake8_is_sealed_whole(project: Project) -> None:
    _python_project(project)
    broken = "[flake8]\nmax-line-length = 100\nmax-line-length = 120\n"  # duplicate option
    project.write("tox.ini", broken)
    _baseline(project)
    assert "tox.ini" in json.loads((project.root / BASELINE).read_text())["inputs"]
    project.write("tox.ini", broken + "extend-ignore = E\n")
    warns = _contract_warns(_launch(project))
    assert any("tox.ini" in w for w in warns), warns


def test_tox_ini_and_setup_cfg_without_flake8_are_not_inventoried(project: Project) -> None:
    _python_project(project)
    project.write("tox.ini", TOX_NO_FLAKE8)
    project.write("setup.cfg", "[metadata]\nname = p\n")
    project.write("sub/tox.ini", "[tox]\nenvlist = a\nenvlist = b\n")  # unparsable, no flake8
    inv = json.loads(_py(project, "inventory").stdout)
    assert not any("tox.ini" in k or "flake8" in k for k in inv), sorted(inv)


# ── baselines sealed before #126 (version 1) ─────────────────────────────────


def test_v1_baseline_without_flake8_gets_no_new_warning(project: Project) -> None:
    _python_project(project)
    project.write("tox.ini", TOX_NO_FLAKE8)
    project.write("setup.cfg", "[metadata]\nname = p\n")
    _baseline(project)
    _as_v1(project)
    assert _contract_warns(_launch(project)) == []


def test_v1_baseline_with_unchanged_flake8_gets_no_new_warning(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    _as_v1(project)
    assert _contract_warns(_launch(project)) == []


def test_v1_baseline_detects_flake8_drift_since_its_commit(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    _as_v1(project)
    project.write(".flake8", "[flake8]\nextend-ignore = E,F,W\n")
    project.write("tox.ini", TOX_NO_FLAKE8)
    project.write("sub/.flake8", "[flake8]\nignore = E\n")
    warns = _contract_warns(_launch(project))
    assert any(".flake8 changed" in w for w in warns), warns
    assert any("tox.ini[flake8] removed" in w for w in warns), warns
    assert any("sub/.flake8 added" in w for w in warns), warns


def test_v1_baseline_with_unreadable_commit_fails_closed_for_flake8(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    _as_v1(project)
    path = project.root / BASELINE
    data = json.loads(path.read_text())
    data["from"] = "0" * 40
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    _py(project, "baseline-seal")
    warns = _contract_warns(_launch(project))
    assert any(".flake8 added" in w for w in warns), warns


def test_v1_baseline_upgrades_through_the_contract_flow(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    _as_v1(project)
    project.write(".flake8", FLAKE8.replace("100", "88"))
    res = _py(project, "contract-propose")
    assert res.returncode == 0, res.stdout + res.stderr
    prop = json.loads((project.root / PROPOSAL).read_text())
    assert ".flake8" in prop["changes"], prop["changes"]
    digest = _review(project)
    res = _py(project, "contract-approve", digest)
    assert "approved" in res.stdout + res.stderr, res.stdout + res.stderr
    data = json.loads((project.root / BASELINE).read_text())
    assert data["version"] == 2
    assert ".flake8" in data["inputs"]
    assert "tox.ini[flake8]" in data["inputs"]
    assert _contract_warns(_launch(project)) == []


# ── guard ────────────────────────────────────────────────────────────────────


def test_guard_warns_on_claude_editing_flake8_config(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    for rel in (".flake8", "sub/.flake8", "tox.ini"):
        res = project.run("guard_write.sh", write_payload(project.root / rel))
        assert res.returncode == 0, (rel, res.stdout, res.stderr)
        assert "lint/type/build" in res.stdout, (rel, res.stdout)
    project.write("tox.ini", TOX_NO_FLAKE8)
    project.write("sub/tox.ini", TOX_NO_FLAKE8)
    res = project.run("guard_write.sh", write_payload(project.root / "sub/tox.ini"))
    assert "lint/type/build" not in res.stdout, res.stdout


def test_v1_baseline_follows_symlinked_flake8_config(project: Project) -> None:
    _python_project(project)
    project.write("cfg/lint.ini", TOX_INI)
    (project.root / "tox.ini").symlink_to("cfg/lint.ini")
    _baseline_symlinked(project)
    _as_v1(project)
    assert _contract_warns(_launch(project)) == []
    project.write("cfg/lint.ini", TOX_NO_FLAKE8)
    warns = _contract_warns(_launch(project))
    assert any("tox.ini[flake8] removed" in w for w in warns), warns


def test_v1_baseline_symlink_out_of_the_project_fails_closed(project: Project) -> None:
    _python_project(project)
    outside = project.root.parent / f"{project.root.name}-flake8"
    outside.write_text(FLAKE8)
    (project.root / ".flake8").symlink_to(outside)
    _baseline_symlinked(project)
    _as_v1(project)
    warns = _contract_warns(_launch(project))
    assert any(".flake8" in w for w in warns), warns


def test_guard_warns_on_tox_ini_sealed_by_a_v1_baseline(project: Project) -> None:
    _flake8_project(project)
    _baseline(project)
    _as_v1(project)
    project.write("tox.ini", TOX_NO_FLAKE8)
    res = project.run("guard_write.sh", write_payload(project.root / "tox.ini"))
    assert "lint/type/build" in res.stdout, res.stdout


def test_v1_baseline_shared_file_linked_out_without_flake8_is_silent(project: Project) -> None:
    _python_project(project)
    outside = project.root.parent / f"{project.root.name}-tox.ini"
    outside.write_text(TOX_NO_FLAKE8)
    (project.root / "tox.ini").symlink_to(outside)
    _baseline_symlinked(project)
    _as_v1(project)
    assert _contract_warns(_launch(project)) == []


def test_v1_baseline_with_an_empty_flake8_file_is_silent(project: Project) -> None:
    _python_project(project)
    project.write(".flake8", "")
    _baseline(project)
    _as_v1(project)
    assert _contract_warns(_launch(project)) == []


def test_v1_baseline_follows_a_symlinked_directory_in_the_path(project: Project) -> None:
    _python_project(project)
    project.write("cfg/versions/v1/lint.ini", FLAKE8)
    (project.root / "cfg/current").symlink_to("versions/v1")
    (project.root / ".flake8").symlink_to("cfg/current/lint.ini")
    _baseline_symlinked(project)
    _as_v1(project)
    assert _contract_warns(_launch(project)) == []
    project.write("cfg/versions/v1/lint.ini", "[flake8]\nignore = E\n")
    warns = _contract_warns(_launch(project))
    assert any(".flake8 changed" in w for w in warns), warns


def test_v1_baseline_keeps_a_symlink_target_byte_exact(project: Project) -> None:
    _python_project(project)
    project.write("lint\n", FLAKE8)  # a target name ending in a newline
    (project.root / ".flake8").symlink_to("lint\n")
    _baseline_symlinked(project)
    _as_v1(project)
    assert _contract_warns(_launch(project)) == []


def test_baseline_create_ignores_a_removed_tox_ini_without_flake8(project: Project) -> None:
    _python_project(project)
    project.write("tox.ini", TOX_NO_FLAKE8)
    project.commit_all("pre-rule")
    (project.root / "tox.ini").unlink()
    res = _py(project, "baseline-create", "--from", "HEAD")
    assert _contract_warns(res) == [], res.stdout + res.stderr
