"""Review round cap per gate and id (issue #42): [review] max_rounds in harness.toml.

After N consecutive CHANGES_REQUESTED rounds of one gate on one id (counted from
progress/review_history/), review_with_codex.sh refuses to launch the reviewer and
`make status` says the step needs the human. Only harness.toml sets N (default 3,
0 = off); no environment variable changes it. The human unblocks by raising
max_rounds (or 0) in harness.toml.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import LEAF_ID, Project


def _fake_codex(tmp: Path, verdict: str = "APPROVED") -> dict[str, str]:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    codex = bin_dir / "codex"
    codex.write_text(
        '#!/usr/bin/env bash\ncat >/dev/null\ntouch "$CODEX_MARK"\n'
        f"printf '# r\\n\\n**Verdict:** {verdict}\\n' > progress/review_{LEAF_ID}.md\n"
    )
    codex.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CODEX_MARK": str(tmp / "codex-ran"),
        "HARNESS_CHECK_CMD": "true",
    }


def _history(project: Project, name: str, *rounds: tuple[str, str]) -> None:
    text = f"# Review history: {name}\n"
    for n, (gate, verdict) in enumerate(rounds, 1):
        text += f"\n## Round {n}: {gate} gate, {verdict} (2026-11-18 10:00)\n\nbody\n"
    project.write(f"progress/review_history/{name}.md", text)


def _rejected(project: Project, n: int, gate: str = "code", name: str = LEAF_ID) -> None:
    _history(project, name, *[(gate, "CHANGES_REQUESTED")] * n)


@pytest.fixture
def code_ready(project: Project) -> Project:
    project.write("tests/unit/test_user.py", "def test_u(): pass\n")
    project.set_verdict("code", "CHANGES_REQUESTED")
    project.commit_all("base")
    return project


def _code_gate(project: Project, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return project.run("review_with_codex.sh", None, "code", LEAF_ID, env=env)


def _status(project: Project) -> str:
    res = subprocess.run(
        ["python3", str(project.root / "harness" / "plan_status.py")],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout


def _review_file(project: Project) -> str:
    return (project.root / f"progress/review_{LEAF_ID}.md").read_text()


# ── the gate ────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_below_the_cap_the_review_still_runs(code_ready: Project, tmp_path: Path) -> None:
    _rejected(code_ready, 2)
    res = _code_gate(code_ready, _fake_codex(tmp_path))
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "codex-ran").exists()


def test_at_the_cap_the_review_is_not_launched(code_ready: Project, tmp_path: Path) -> None:
    _rejected(code_ready, 3)
    before = _review_file(code_ready)
    res = _code_gate(code_ready, _fake_codex(tmp_path))
    assert res.returncode != 0
    assert "needs human" in res.stderr and "3" in res.stderr
    assert "max_rounds" in res.stderr
    assert not (tmp_path / "codex-ran").exists()
    assert _review_file(code_ready) == before


@pytest.mark.e2e
def test_the_cap_comes_from_harness_toml(code_ready: Project, tmp_path: Path) -> None:
    code_ready.write("harness.toml", "[review]\nmax_rounds = 5\n")
    _rejected(code_ready, 4)
    assert _code_gate(code_ready, _fake_codex(tmp_path)).returncode == 0
    _rejected(code_ready, 5)
    (tmp_path / "codex-ran").unlink()
    assert _code_gate(code_ready, _fake_codex(tmp_path)).returncode != 0
    assert not (tmp_path / "codex-ran").exists()


@pytest.mark.e2e
def test_zero_disables_the_cap(code_ready: Project, tmp_path: Path) -> None:
    code_ready.write("harness.toml", "[review]\nmax_rounds = 0\n")
    _rejected(code_ready, 12)
    assert _code_gate(code_ready, _fake_codex(tmp_path)).returncode == 0
    assert (tmp_path / "codex-ran").exists()


@pytest.mark.parametrize("name", ["HARNESS_MAX_ROUNDS", "HARNESS_REVIEW_MAX_ROUNDS", "MAX_ROUNDS"])
def test_no_environment_variable_lifts_the_cap(
    code_ready: Project, tmp_path: Path, name: str
) -> None:
    _rejected(code_ready, 3)
    res = _code_gate(code_ready, {**_fake_codex(tmp_path), name: "0"})
    assert res.returncode != 0
    assert not (tmp_path / "codex-ran").exists()


@pytest.mark.e2e
def test_counters_are_per_gate(code_ready: Project, tmp_path: Path) -> None:
    _history(code_ready, LEAF_ID, *[("plan", "CHANGES_REQUESTED")] * 3, ("plan", "PLAN_APPROVED"))
    assert _code_gate(code_ready, _fake_codex(tmp_path)).returncode == 0


@pytest.mark.e2e
def test_counters_are_per_id(code_ready: Project, tmp_path: Path) -> None:
    _rejected(code_ready, 3, name="1.1.2")
    assert _code_gate(code_ready, _fake_codex(tmp_path)).returncode == 0


@pytest.mark.e2e
def test_an_approval_resets_the_count(code_ready: Project, tmp_path: Path) -> None:
    _history(
        code_ready,
        LEAF_ID,
        *[("code", "CHANGES_REQUESTED")] * 2,
        ("code", "APPROVED"),
        *[("code", "CHANGES_REQUESTED")] * 2,
    )
    assert _code_gate(code_ready, _fake_codex(tmp_path)).returncode == 0


def test_phase_gate_is_capped_too(code_ready: Project, tmp_path: Path) -> None:
    _rejected(code_ready, 3, gate="phase", name="phase-01")
    res = code_ready.run("review_with_codex.sh", None, "phase", "01", env=_fake_codex(tmp_path))
    assert res.returncode != 0
    assert "needs human" in res.stderr
    assert not (tmp_path / "codex-ran").exists()


@pytest.mark.e2e
@pytest.mark.parametrize(
    "toml",
    [
        "[review]\nmax_rounds = -1\n",
        '[review]\nmax_rounds = "3"\n',
        "[review]\nmax_rounds = 2.5\n",
        "[review]\nmax_rounds = true\n",
        "review = 3\n",
    ],
)
def test_invalid_max_rounds_fails_clearly(code_ready: Project, tmp_path: Path, toml: str) -> None:
    code_ready.write("harness.toml", toml)
    res = _code_gate(code_ready, _fake_codex(tmp_path))
    assert res.returncode != 0
    assert "harness.toml" in res.stderr and "review" in res.stderr
    assert not (tmp_path / "codex-ran").exists()


# ── make status ─────────────────────────────────────────────────────────────


def test_status_shows_needs_human(code_ready: Project) -> None:
    _rejected(code_ready, 3)
    out = _status(code_ready)
    assert "Next: needs human (3 rounds)" in out
    assert "code gate" in out and LEAF_ID in out


def test_status_below_the_cap_is_unchanged(code_ready: Project) -> None:
    _rejected(code_ready, 2)
    out = _status(code_ready)
    assert f"Next: fix {LEAF_ID}" in out


def test_status_respects_zero(code_ready: Project) -> None:
    code_ready.write("harness.toml", "[review]\nmax_rounds = 0\n")
    _rejected(code_ready, 9)
    assert "needs human" not in _status(code_ready)


def test_status_caps_even_without_a_verdict_file(code_ready: Project) -> None:
    (code_ready.root / f"progress/review_{LEAF_ID}.md").unlink()
    _rejected(code_ready, 3)
    assert "Next: needs human (3 rounds)" in _status(code_ready)


def test_status_shows_the_real_count(code_ready: Project) -> None:
    _rejected(code_ready, 5)
    assert "Next: needs human (5 rounds)" in _status(code_ready)
