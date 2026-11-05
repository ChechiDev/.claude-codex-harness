"""C0 migration rehearsal (M0a #14, plan 0.3 / 0.7 / item 0).

compat/_rehearse.py ROOT [--check-cmd CMD]
    runs INSIDE the C0 container on a copy: state snapshot, dry-run (nothing changes),
    apply (links identical, record at current versions, exclude entry), check (no
    regression), rollback dry-run + apply (files and links back). Prints a JSON
    report; exit 0 pass, 1 fail, 2 inconclusive (the check was already red).

compat/_compat.py rehearse-migration --project P ... --candidate C --origin O ...
    host side: copy, relink to /toolkit, deps, locked-down container running the
    driver, run-scoped cleanup, copies kept on failure.

The driver is tested here directly on throwaway projects (no Docker); the host
command with the fake docker CLI."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import TOOLKIT

DRIVER = TOOLKIT / "compat" / "_rehearse.py"
RUNNER = TOOLKIT / "compat" / "_compat.py"
FAKE_DOCKER = Path(__file__).resolve().parent / "fake_docker.py"
LINKS = {
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
    "harness": "lib",
}
STEPS = [
    "snapshot",
    "check-before",
    "dry-run",
    "apply",
    "check-after",
    "rollback-dry-run",
    "rollback",
]


def _env(tmp_path: Path) -> dict[str, str]:
    empty = tmp_path / "empty.gitconfig"
    empty.touch()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "HARNESS_"))}
    return {
        **env,
        "CLAUDE_HOME": str(tmp_path / "claude-home"),
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }


def _project(tmp_path: Path, readme: str | None = None) -> Path:
    """A pre-M0a project (old Makefile, no record) in its own git repo."""
    root = tmp_path / "proj"
    root.mkdir()
    old = subprocess.run(
        ["git", "show", "54ca858:templates/Makefile"],
        cwd=TOOLKIT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    (root / "Makefile").write_text(old)
    (root / "CLAUDE.md").write_text("# Project instructions for Claude\n\nx\n")
    (root / "AGENTS.md").write_text("# AGENTS.md — Navigation map for agents\n")
    (root / "src").mkdir()
    (root / "src/app.py").write_text("x = 1\n")
    if readme is not None:
        (root / "docs/plan/issues").mkdir(parents=True)
        (root / "docs/plan/issues/README.md").write_text(readme)
    for rel, target in LINKS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).symlink_to(TOOLKIT / target)
    (tmp_path / "claude-home").mkdir(exist_ok=True)
    env = _env(tmp_path)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return root


def _drive(root: Path, tmp_path: Path, check: str = "true") -> tuple[int, dict[str, Any]]:
    res = subprocess.run(
        [sys.executable, str(DRIVER), str(root), "--check-cmd", check],
        text=True,
        capture_output=True,
        check=False,
        env=_env(tmp_path),
        stdin=subprocess.DEVNULL,
    )
    assert res.stdout.strip(), res.stderr
    report: dict[str, Any] = json.loads(res.stdout)
    return res.returncode, report


def _driver_module() -> ModuleType:
    sys.path.insert(0, str(TOOLKIT / "lib"))
    spec = importlib.util.spec_from_file_location("_rehearse_t", DRIVER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── driver: the sequence and its evidence ────────────────────────────────────


def test_a_clean_project_passes_every_step(tmp_path: Path) -> None:
    root = _project(tmp_path)
    code, report = _drive(root, tmp_path)
    assert code == 0, report
    assert report["verdict"] == "pass"
    assert [s["name"] for s in report["steps"]] == STEPS
    assert all(s["ok"] for s in report["steps"]), report["steps"]
    for key in ("toolkit", "root", "claude_home", "migration", "record", "exclude_entry"):
        assert report[key], key
    assert report["claude_home"] == str(tmp_path / "claude-home")
    assert report["exclude_entry"] == "/.harness/migrations/"
    rec = report["record"]
    assert rec["migration_level"] == 0
    assert all(e["state"] in ("installed", "nofile") for e in rec["templates"].values())


def test_the_report_lists_what_apply_changed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _, report = _drive(root, tmp_path)
    changed = set(report["changed_by_apply"])
    assert {"Makefile", "CLAUDE.md", "AGENTS.md", "docs/plan/issues/README.md"} <= changed
    assert ".harness/templates.json" in changed
    assert not [p for p in changed if p.startswith("src/")]


def test_the_copy_is_back_after_the_rehearsal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = {p: (root / p).read_text() for p in ("Makefile", "CLAUDE.md", "AGENTS.md")}
    code, _ = _drive(root, tmp_path)
    assert code == 0
    assert {p: (root / p).read_text() for p in before} == before
    assert not (root / "docs/plan/issues/README.md").exists()
    assert not (root / ".harness/templates.json").exists()
    # declared exceptions: the backup and the exclude entry stay
    assert list((root / ".harness/migrations").iterdir())
    assert "/.harness/migrations/" in (root / ".git/info/exclude").read_text().splitlines()


def test_a_migration_refusal_fails_the_rehearsal(tmp_path: Path) -> None:
    root = _project(tmp_path, readme="# my own notes\n")  # unknown provenance
    code, report = _drive(root, tmp_path)
    assert code == 1
    assert report["verdict"] == "fail"
    failed = [s for s in report["steps"] if not s["ok"]]
    assert failed and failed[0]["name"] == "dry-run"
    assert "unknown provenance" in failed[0]["output"]


def test_a_check_that_turns_red_after_apply_fails(tmp_path: Path) -> None:
    root = _project(tmp_path)
    code, report = _drive(root, tmp_path, check="test ! -f .harness/templates.json")
    assert code == 1
    assert report["verdict"] == "fail"
    assert next(s for s in report["steps"] if s["name"] == "check-after")["ok"] is False


def test_a_check_already_red_is_inconclusive(tmp_path: Path) -> None:
    root = _project(tmp_path)
    code, report = _drive(root, tmp_path, check="false")
    assert code == 2
    assert report["verdict"] == "inconclusive"


def test_a_wrong_link_fails_and_is_not_repaired(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".claude/agents").unlink()
    (root / ".claude/agents").symlink_to(TOOLKIT / "codex")
    code, report = _drive(root, tmp_path)
    assert code == 1 and report["verdict"] == "fail"
    assert os.readlink(root / ".claude/agents") == str(TOOLKIT / "codex")


def test_compare_reports_any_file_or_link_difference() -> None:
    mod = _driver_module()
    s0 = {"files": {"a": "1", "b": "2"}, "links": {"project:harness": ["link", "/t/lib"]}}
    same = {"files": {"a": "1", "b": "2"}, "links": {"project:harness": ["link", "/t/lib"]}}
    assert mod.compare(s0, same) == []
    other = {"files": {"a": "1", "b": "3", "c": "4"}, "links": {}}
    diffs = mod.compare(s0, other)
    assert any("b" in d for d in diffs) and any("c" in d for d in diffs)
    assert any("harness" in d for d in diffs)


def test_compare_honours_only_the_declared_exceptions() -> None:
    mod = _driver_module()
    s0 = {"files": {"a": "1"}, "links": {}}
    s4 = {"files": {"a": "1", ".harness/migrations/x/manifest.json": "9"}, "links": {}}
    assert mod.compare(s0, s4, allow=mod.ROLLBACK_EXCEPTIONS) == []
    assert mod.compare(s0, s4) != []
    s5 = {"files": {"a": "1", ".harness/templates.json": "9"}, "links": {}}
    assert mod.compare(s0, s5, allow=mod.ROLLBACK_EXCEPTIONS) != []


# ── host command with the fake docker CLI ────────────────────────────────────


@pytest.fixture
def fake(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "docker_state.json"
    state.write_text(json.dumps({"engine": "linux", "containers": {}, "volumes": {}}))
    wrapper = bin_dir / "docker"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_DOCKER}" "{state}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    systemctl = bin_dir / "systemctl"
    fake_systemctl = Path(__file__).resolve().parent / "fake_systemctl.py"
    systemd = tmp_path / "systemd.json"
    systemctl.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{fake_systemctl}" "{systemd}" "$@"\n'
    )
    systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "docker-config"
    config.mkdir()
    return {"bin": bin_dir, "state": state, "config": config, "tmp": tmp_path}


def _set(fake: dict[str, Path], **changes: Any) -> None:
    data = json.loads(fake["state"].read_text())
    data.update(changes)
    fake["state"].write_text(json.dumps(data))


def _host(fake: dict[str, Path], repo: Path, run_id: str) -> subprocess.CompletedProcess[str]:
    image = {
        "Config": {
            "Env": ["PATH=/usr/bin"],
            "Labels": {"harness-compat": "1"},
            "Entrypoint": ["/usr/local/bin/compat-entrypoint"],
        }
    }
    # the canned output is the driver's only: preflight's tool probe must pass
    _set(fake, images={"harness-compat:t": image}, run_match="/toolkit/compat/_rehearse.py")
    args = [
        "rehearse-migration", "--project", str(repo), "--run-id", run_id,
        "--candidate", str(TOOLKIT), "--origin", str(TOOLKIT),
        "--image", "harness-compat:t", "--docker-config", str(fake["config"]),
        "--scratch", str(fake["tmp"] / "scratch"),
    ]  # fmt: skip
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{fake['bin']}:{os.environ['PATH']}"},
    )


def _driver_out(verdict: str) -> str:
    return json.dumps({"verdict": verdict, "steps": []})


def test_host_runs_the_driver_in_the_locked_down_container(fake: dict[str, Path]) -> None:
    repo = _project(fake["tmp"])
    _set(fake, run_stdout=_driver_out("pass"))
    res = _host(fake, repo, "rh1")
    assert res.returncode == 0, res.stdout + res.stderr
    calls = json.loads(fake["state"].read_text())["calls"]
    run = next(c for c in calls if c[2:3] == ["run"] and "/toolkit/compat/_rehearse.py" in c)
    assert "--network" in run and "none" in run and "--read-only" in run
    assert f"type=bind,src={TOOLKIT},dst=/toolkit,readonly" in run
    report = json.loads(res.stdout)
    assert report["projects"][str(repo)]["verdict"] == "pass"
    assert report["kept"] == [] and report["run_id"] == "rh1"
    assert not (fake["tmp"] / "scratch" / "rh1").exists()


def test_host_fails_and_keeps_the_copy_when_the_driver_fails(fake: dict[str, Path]) -> None:
    repo = _project(fake["tmp"])
    _set(fake, run_stdout=_driver_out("fail"), run_exit=1)
    res = _host(fake, repo, "rh2")
    assert res.returncode == 1
    report = json.loads(res.stdout)
    assert report["failures"] == [str(repo)]
    assert report["kept"]


def test_host_treats_unreadable_driver_output_as_an_error(fake: dict[str, Path]) -> None:
    repo = _project(fake["tmp"])
    _set(fake, run_stdout="not json")
    res = _host(fake, repo, "rh3")
    assert res.returncode == 1
    report = json.loads(res.stdout)
    assert report["projects"][str(repo)]["verdict"] == "error"


def test_host_inconclusive_exits_2(fake: dict[str, Path]) -> None:
    repo = _project(fake["tmp"])
    _set(fake, run_stdout=_driver_out("inconclusive"), run_exit=2)
    res = _host(fake, repo, "rh4")
    assert res.returncode == 2
    assert json.loads(res.stdout)["inconclusive"] == [str(repo)]


def test_host_refuses_a_reused_run_id(fake: dict[str, Path]) -> None:
    repo = _project(fake["tmp"])
    (fake["tmp"] / "scratch" / "rh5").mkdir(parents=True)
    res = _host(fake, repo, "rh5")
    assert res.returncode == 1
    assert "in use" in res.stdout + res.stderr


# ── evidence a reviewer can check from the saved report (Codex rh1 review) ───


def test_the_report_carries_the_initial_and_final_snapshots(tmp_path: Path) -> None:
    import hashlib

    root = _project(tmp_path)
    makefile_sha = hashlib.sha256((root / "Makefile").read_bytes()).hexdigest()
    code, report = _drive(root, tmp_path)
    assert code == 0, report
    for key in ("initial", "final"):
        snap = report[key]
        assert snap["files"]["Makefile"] == makefile_sha, key
        assert snap["links"]["project:harness"] == ["link", str(TOOLKIT / "lib")], key
        assert snap["links"]["project:.claude/agents"] == ["link", str(TOOLKIT / "agents")]
        assert "exclude" in snap


def test_a_report_stopped_early_still_carries_the_initial_snapshot(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _, report = _drive(root, tmp_path, check="false")
    assert report["initial"]["files"]
    assert report["final"] is None


def _applied_record(tmp_path: Path) -> dict[str, Any]:
    root = _project(tmp_path)
    upgrade = TOOLKIT / "lib" / "_upgrade.py"
    subprocess.run(
        [sys.executable, str(upgrade), "migrate", str(root), "--apply", "--yes"],
        check=True, capture_output=True, env=_env(tmp_path), stdin=subprocess.DEVNULL,
    )  # fmt: skip
    record: dict[str, Any] = json.loads((root / ".harness/templates.json").read_text())
    return record


def test_record_problems_accepts_the_record_a_migration_writes(tmp_path: Path) -> None:
    mod = _driver_module()
    assert mod.record_problems(_applied_record(tmp_path)) == []


@pytest.mark.parametrize(
    ("field", "value", "word"),
    [("version", "0.0.0-old", "version"), ("sha256", "0" * 64, "sha256")],
)
def test_record_problems_reports_a_stale_version_or_hash(
    tmp_path: Path, field: str, value: str, word: str
) -> None:
    mod = _driver_module()
    record = _applied_record(tmp_path)
    entry = next(e for e in record["templates"].values() if e["state"] == "installed")
    entry[field] = value
    problems = mod.record_problems(record)
    assert problems and any(word in p for p in problems), problems


def test_record_problems_reports_a_missing_template(tmp_path: Path) -> None:
    mod = _driver_module()
    record = _applied_record(tmp_path)
    record["templates"].pop("Makefile")
    problems = mod.record_problems(record)
    assert any("Makefile" in p for p in problems), problems


def test_record_problems_reports_a_wrong_level(tmp_path: Path) -> None:
    mod = _driver_module()
    record = _applied_record(tmp_path)
    record["migration_level"] = 99
    assert any("migration_level" in p for p in mod.record_problems(record))


# ── the check runs as C0 runs it (rh2: `make -s` hid nested make messages) ───


def test_the_default_check_is_the_one_compat_run_uses() -> None:
    mod = _driver_module()
    assert mod.DEFAULT_CHECK.split() == ["make", "check"]


def test_the_default_check_lets_nested_make_speak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project test asserting on nested make output (codexion's no-relink check
    looks for "Nothing to be done") must see it: no -s in MAKEFLAGS."""
    monkeypatch.delenv("MAKEFLAGS", raising=False)
    monkeypatch.delenv("MFLAGS", raising=False)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/Makefile").write_text("all:\n.PHONY: all\n")
    (tmp_path / "Makefile").write_text("check:\n\t$(MAKE) -C sub\n")
    mod = _driver_module()
    rehearsal = mod.Rehearsal(tmp_path, mod.DEFAULT_CHECK)
    assert rehearsal.check("check-before"), rehearsal.steps
    assert "Nothing to be done" in rehearsal.steps[-1]["output"]
