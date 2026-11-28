"""I3c part 1: Claude Code's native Bash sandbox as a write barrier (WARN phase).

The toolkit settings.json (reached by every project through the
.claude/settings.json link) ships the sandbox policy switched off: a project
opts in with `{"sandbox": {"enabled": true}}` in its .claude/settings.local.json,
and the rest of the policy (harness-owned paths write-denied, excluded writers,
network) then applies. `make env-check` runs a doctor check that is silent while
the sandbox is off and only ever warns once it is on.
Design: docs/design/I3c-sandbox.md.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import _guard
import _sandbox_check as sc
import _upgrade
import pytest
from conftest import TOOLKIT

SETTINGS: dict[str, Any] = json.loads((TOOLKIT / "settings.json").read_text())
SANDBOX: dict[str, Any] = SETTINGS.get("sandbox", {})
DENY: list[str] = SETTINGS["permissions"]["deny"]

# Files Codex or the harness own under progress/ (guard: _guard.RE_CODEX_OWNED).
CODEX_OWNED = (
    "progress/plan_review_1.1.1.md",
    "progress/review_1.1.1.md",
    "progress/phase_review_01.md",
    "progress/issue_review_ISS-3.1.md",
    "progress/task_review_1.1.md",
    "progress/task_close_1.1.md",
    "progress/phase_close_01.md",
    "progress/roadmap_review.md",
    "progress/issue_draft_review.md",
    "progress/legacy_inventory_review.md",
    "progress/oracle_contract_review.md",
    "progress/config_contract_review.md",
    "progress/plan_task_1.1.sha256",
    "progress/close_1.1.1.msg",
    "progress/pr_1.1.md",
    "progress/review_history/plan_1.1.1.md",
    "progress/oracle_baseline.json",
    "progress/contract_baseline.json",
    "progress/legacy_inventory.json",
    "progress/task_flow_legacy_01.json",
    "progress/issue_migrations_3.json",
    "progress/oracle_migrations/m1.json",
    "progress/stack_review.md",  # the stack gate (issue #115)
    "progress/stack.sha256",
    "progress/plan_roadmap.sha256",
    "progress/review_history/stack.md",
)
OTHER_PROTECTED = (
    "tests/acceptance/test_1_1_1.py",
    "harness/_guard.py",
    ".claude/settings.json",
    ".claude/settings.local.json",
    "harness.toml",
    ".harness/templates.json",
    # RE_CODEX_OWNED's .git/harness-* (verdict slots, attempt ledger, supersede tickets).
    ".git/harness-check.ok",
    ".git/harness-verdict/review_1.1.1.md",
)
# What Claude writes itself with Edit/Write: a deny rule here breaks the workflow.
CLAUDE_WRITES = (
    "progress/current.md",
    "progress/history.md",
    "progress/impl_1.1.1.md",
    "progress/issue_draft.md",
    "progress/issue_question-ISS-3.md",
    "src/demo/domain.py",
    "docs/reference/demo.md",
    "tests/unit/test_demo.py",
    "docs/plan/phase-01.md",
    "docs/stack.md",  # the stack step: claude-planner writes it (issue #115)
    "PLAN.md",
    "CLAUDE.md",
)
# What a project writes in .claude/settings.local.json to switch the sandbox on.
OPT_IN: dict[str, Any] = {"sandbox": {"enabled": True}}


def _edit_denied(rel: str) -> bool:
    """`rel` (project-relative) is matched by an Edit(...) deny rule. `/x` in a
    permission rule is relative to the project root; `**` spans directories."""
    for rule in DENY:
        if not (rule.startswith("Edit(/") and rule.endswith(")")):
            continue
        pattern = rule[len("Edit(/") : -1]
        if pattern.endswith("/**") and (rel == pattern[:-3] or rel.startswith(pattern[:-2])):
            return True
        if fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


# ── settings.json: the sandbox block ─────────────────────────────────────────


def test_the_sandbox_ships_switched_off() -> None:
    """Opt-in per project (the user's decision, 2026-11-26): the toolkit reaches every
    project through the link, so the policy is there but off; the rest of the block is
    what an opted-in project gets."""
    assert SANDBOX["enabled"] is False
    assert SANDBOX["autoAllowBashIfSandboxed"] is True
    assert SANDBOX["allowUnsandboxedCommands"] is False  # no dangerouslyDisableSandbox retry
    assert SANDBOX["failIfUnavailable"] is False  # WARN phase: no bwrap still starts


def test_deny_write_covers_the_harness_owned_paths() -> None:
    deny = set(SANDBOX["filesystem"]["denyWrite"])
    for path in ("./progress", "./harness", "./.claude", "./tests/acceptance"):
        assert path in deny, path
    assert {"./harness.toml", "./.harness"} <= deny


def test_deny_write_entries_work_on_linux() -> None:
    """bubblewrap honors only plain paths or a trailing /**; anything else is a no-op."""
    for path in SANDBOX["filesystem"]["denyWrite"]:
        body = path[:-3] if path.endswith("/**") else path
        assert not any(c in body for c in "*?[{"), path
        assert path.startswith("./"), path  # project-relative in a project settings file


def test_allow_write_lets_uv_and_codex_keep_their_state() -> None:
    allow = SANDBOX["filesystem"]["allowWrite"]
    assert "~/.cache/uv" in allow
    assert "~/.codex" in allow


def test_network_allows_only_the_toolchain_hosts() -> None:
    domains = set(SANDBOX["network"]["allowedDomains"])
    for host in ("github.com", "api.github.com", "pypi.org", "files.pythonhosted.org"):
        assert host in domains, host
    for host in ("chatgpt.com", "api.openai.com"):  # codex
        assert host in domains, host
    assert not any(d == "*" or d.startswith("*.") and d.count(".") < 2 for d in domains)


@pytest.mark.parametrize(
    "command",
    [
        "./harness/review_with_codex.sh *",
        "harness/review_with_codex.sh *",
        "./harness/issue.sh *",
        "make issue *",
        "make issue-start *",
        "make issue-new",
        "make plan",  # writes progress/task_flow_legacy_<NN>.json once (issue #62)
    ],
)
def test_the_harness_writers_run_outside_the_sandbox(command: str) -> None:
    """The gates, the close (Codex writes verdicts, the script commits) and the issues
    lane write progress/: they must run unsandboxed or they fail Read-only."""
    assert command in SANDBOX["excludedCommands"]


def test_no_general_purpose_command_is_excluded() -> None:
    """An excluded interpreter, make target or shell would carry any write past the
    sandbox. Only the harness writers and output filters that cannot write."""
    allowed_heads = {
        "./harness/",
        "harness/",
        "make issue",
        "make plan",
        "python3 harness/_contract.py contract-propose",
        "python3 harness/_oracle.py contract-propose",
        "python3 harness/_oracle.py replan-history ",
        "tail ",
        "head ",
    }
    for command in SANDBOX["excludedCommands"]:
        assert any(command.startswith(h) for h in allowed_heads), command
    assert "make *" not in SANDBOX["excludedCommands"]
    assert "make check" not in SANDBOX["excludedCommands"]  # runs code Claude wrote


# ── settings.json: Edit deny rules (Edit/Write never go through the sandbox) ─


@pytest.mark.parametrize("rel", CODEX_OWNED)
def test_edit_deny_rules_cover_every_codex_owned_file(rel: str) -> None:
    assert _guard.RE_CODEX_OWNED.match(rel), f"fixture drifted from the guard: {rel}"
    assert _edit_denied(rel), rel


@pytest.mark.parametrize("rel", OTHER_PROTECTED)
def test_edit_deny_rules_cover_the_oracle_and_harness_files(rel: str) -> None:
    assert _edit_denied(rel), rel


@pytest.mark.parametrize("rel", CLAUDE_WRITES)
def test_edit_deny_rules_leave_claudes_own_files_writable(rel: str) -> None:
    assert not _edit_denied(rel), rel


def test_there_are_no_write_rules() -> None:
    """Claude Code ignores Write(path) rules (verified: 'only Edit(path) rules are'
    matched); Edit(...) covers every file-editing tool."""
    assert not [r for r in DENY if r.startswith("Write(")]


def test_the_i8_rules_are_kept() -> None:
    for rule in ("Bash(git am:*)", "Bash(git apply:*)", "Bash(patch:*)"):
        assert rule in DENY
    assert "Bash(./harness/review_with_codex.sh seal:*)" in DENY
    assert "Bash(./harness/review_with_codex.sh:*)" in SETTINGS["permissions"]["allow"]


# ── the doctor check (lib/_sandbox_check.py): WARN only, never fails ──────────


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _opted_in(local: dict[str, Any] | None) -> dict[str, Any]:
    """`local` with the opt-in added (a value it sets itself is kept)."""
    local = dict(local or {})
    local["sandbox"] = {**OPT_IN["sandbox"], **local.get("sandbox", {})}
    return local


def _project(tmp_path: Path, local: dict[str, Any] | None = None, opt_in: bool = True) -> Path:
    """A project linked to the toolkit settings; opted in through its
    settings.local.json unless `opt_in` is False."""
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude/settings.json").symlink_to(TOOLKIT / "settings.json")
    local = _opted_in(local) if opt_in else local
    if local is not None:
        _write(root / ".claude/settings.local.json", local)
    return root


def _host(
    platform: str = "linux",
    tools: tuple[str, ...] = ("bwrap", "socat"),
    bwrap_works: bool = True,
    inside: bool = False,
) -> sc.Host:
    return sc.Host(
        platform=platform,
        tools={t: f"/usr/bin/{t}" for t in tools},
        bwrap_works=bwrap_works,
        inside_sandbox=inside,
    )


def _report(
    tmp_path: Path, host: sc.Host | None = None, opt_in: bool = True, **files: dict[str, Any]
) -> list[sc.Finding]:
    """Findings for a project linked to the toolkit settings (opted in unless
    `opt_in` is False); `files` holds the local / user / managed settings to add."""
    root = _project(tmp_path, files.get("local"), opt_in)
    user_file = _write(tmp_path / "home/.claude/settings.json", files.get("user", {}))
    managed_file = _write(tmp_path / "etc/managed-settings.json", files.get("managed", {}))
    sources = sc.load_sources(root, user_file, managed_file)
    return sc.findings(sc.effective(sources), host or _host())


def _kinds(report: Sequence[tuple[str, str]]) -> set[str]:
    return {kind for kind, _ in report}


def _text(report: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"[{k}] {m}" for k, m in report)


def test_check_is_ok_with_the_toolkit_settings_and_bwrap(tmp_path: Path) -> None:
    report = _report(tmp_path)
    assert _kinds(report) == {"OK"}, _text(report)
    assert "Bash sandbox" in _text(report)


def test_check_warns_and_says_how_to_install_bwrap(tmp_path: Path) -> None:
    report = _report(tmp_path, _host(tools=("socat",)))
    assert "WARN" in _kinds(report)
    text = _text(report)
    assert "bubblewrap" in text
    assert "apt-get install bubblewrap socat" in text
    assert "unsandboxed" in text


def test_check_warns_without_socat(tmp_path: Path) -> None:
    text = _text(_report(tmp_path, _host(tools=("bwrap",))))
    assert "[WARN]" in text and "socat" in text


def test_check_warns_when_bwrap_cannot_start(tmp_path: Path) -> None:
    text = _text(_report(tmp_path, _host(bwrap_works=False)))
    assert "[WARN]" in text and "user namespaces" in text


def test_inside_the_sandbox_the_probe_is_skipped(tmp_path: Path) -> None:
    """Claude runs make check inside its own sandbox: no nested bwrap probe."""
    report = _report(tmp_path, _host(bwrap_works=False, inside=True))
    assert _kinds(report) == {"OK"}, _text(report)
    assert "inside Claude Code's Bash sandbox" in _text(report)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_check_is_a_permanent_warn_off_linux(tmp_path: Path, platform: str) -> None:
    report = _report(tmp_path, _host(platform=platform, tools=()))
    assert "WARN" in _kinds(report)
    assert "Linux" in _text(report)


@pytest.mark.parametrize(
    "host",
    [
        _host(),
        _host(tools=()),
        _host(bwrap_works=False),
        _host(platform="darwin", tools=()),
        _host(platform="win32", tools=()),
    ],
    ids=["linux", "no-bwrap", "bwrap-broken", "darwin", "win32"],
)
def test_the_doctor_is_silent_with_the_toolkit_default(tmp_path: Path, host: sc.Host) -> None:
    """Off by default: every project runs env-check (make check runs it), and one that
    never opted in hears nothing about the sandbox, whatever the machine."""
    assert _report(tmp_path, host, opt_in=False) == []


def test_a_local_false_switches_it_off_and_the_doctor_says_nothing(tmp_path: Path) -> None:
    report = _report(tmp_path, _host(tools=()), local={"sandbox": {"enabled": False}})
    assert report == []


def test_the_local_opt_in_turns_the_doctor_on(tmp_path: Path) -> None:
    text = _text(_report(tmp_path, _host(tools=())))
    assert "[WARN]" in text and "bubblewrap" in text


def test_a_user_true_does_not_opt_in(tmp_path: Path) -> None:
    """Project settings outrank user settings: the toolkit's explicit false holds, so
    the opt-in goes in the project's settings.local.json (or managed settings)."""
    report = _report(tmp_path, _host(tools=()), opt_in=False, user=OPT_IN)
    assert report == []


def test_a_user_true_does_not_hide_the_projects_value(tmp_path: Path) -> None:
    """Project settings outrank user settings: the toolkit's false holds."""
    report = _report(tmp_path, user={"sandbox": {"allowUnsandboxedCommands": True}})
    assert _kinds(report) == {"OK"}, _text(report)


def test_managed_settings_win(tmp_path: Path) -> None:
    text = _text(_report(tmp_path, managed={"sandbox": {"allowUnsandboxedCommands": True}}))
    assert "[WARN]" in text and "allowUnsandboxedCommands" in text
    assert "managed" in text


def test_managed_settings_can_switch_it_on(tmp_path: Path) -> None:
    report = _report(tmp_path, _host(tools=()), opt_in=False, managed=OPT_IN)
    assert "WARN" in _kinds(report), _text(report)


def test_managed_settings_can_switch_it_off(tmp_path: Path) -> None:
    report = _report(tmp_path, _host(tools=()), managed={"sandbox": {"enabled": False}})
    assert report == []


def test_a_local_true_reopens_the_escape_hatch(tmp_path: Path) -> None:
    text = _text(_report(tmp_path, local={"sandbox": {"allowUnsandboxedCommands": True}}))
    assert "[WARN]" in text and "allowUnsandboxedCommands" in text


def test_a_user_false_holds_against_a_local_true(tmp_path: Path) -> None:
    """Documented: a false in user (or managed) settings holds against a project true."""
    report = _report(
        tmp_path,
        user={"sandbox": {"allowUnsandboxedCommands": False}},
        local={"sandbox": {"allowUnsandboxedCommands": True}},
    )
    assert _kinds(report) == {"OK"}, _text(report)


def test_deny_write_lists_merge_across_files(tmp_path: Path) -> None:
    extra = {"sandbox": {"filesystem": {"denyWrite": ["./secrets"]}}}
    eff = sc.effective(
        sc.load_sources(
            _project(tmp_path, extra),
            _write(tmp_path / "u.json", {}),
            _write(tmp_path / "m.json", {}),
        )
    )
    assert "./secrets" in eff.deny_write and "./progress" in eff.deny_write


def test_a_missing_deny_write_path_warns(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    bare = {"sandbox": {**SANDBOX, "enabled": True, "filesystem": {"denyWrite": ["./harness"]}}}
    _write(root / ".claude/settings.json", bare)
    sources = sc.load_sources(root, tmp_path / "none.json", tmp_path / "none2.json")
    host = _host()
    text = _text(sc.findings(sc.effective(sources), host))
    assert "[WARN]" in text and "./progress" in text and "./.claude" in text


def test_unreadable_settings_warn_instead_of_crashing(tmp_path: Path) -> None:
    root = _project(tmp_path)  # opted in: a broken file is reported (off, it is not)
    user = tmp_path / "home/.claude/settings.json"
    user.parent.mkdir(parents=True)
    user.write_text("{not json")
    sources = sc.load_sources(root, user, tmp_path / "none2.json")
    assert any("user settings" in w for w in sources.errors)
    host = _host()
    assert "WARN" in _kinds(sc.findings(sc.effective(sources), host))


@pytest.mark.parametrize("broken", ["user", "local", "managed"])
def test_an_unreadable_file_is_no_noise_while_the_sandbox_is_off(
    tmp_path: Path, broken: str
) -> None:
    """Claude Code skips a settings file it cannot parse, so the toolkit's false holds
    and make check stays quiet (the guard reports a broken settings.local.json)."""
    root = _project(tmp_path, opt_in=False)
    files = {
        "user": tmp_path / "home/.claude/settings.json",
        "local": root / ".claude/settings.local.json",
        "managed": tmp_path / "etc/managed-settings.json",
    }
    for name, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json" if name == broken else "{}")
    sources = sc.load_sources(root, files["user"], files["managed"])
    assert sc.findings(sc.effective(sources), _host()) == []


def _env_check(root: Path, path: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "SANDBOX_"))}
    env |= {"PATH": path, "HOME": str(root.parent / "home"), "NO_COLOR": "1"}
    return subprocess.run(
        ["make", "-s", "--no-print-directory", "env-check"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _make_project(tmp_path: Path, opt_in: bool = True) -> Path:
    root = _project(tmp_path, opt_in=opt_in)
    (root / "harness").symlink_to(TOOLKIT / "lib")
    (root / "Makefile").write_text((TOOLKIT / "templates/Makefile").read_text())
    return root


def _path_without_bwrap(tmp_path: Path) -> str:
    """PATH with the system tools but bwrap and socat, then uv's dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for d in ("/usr/bin", "/bin"):
        for tool in os.listdir(d):
            link = bin_dir / tool
            if tool not in ("bwrap", "socat") and not link.is_symlink():
                link.symlink_to(os.path.join(d, tool))
    uv = shutil.which("uv")
    assert uv
    return f"{bin_dir}{os.pathsep}{os.path.dirname(uv)}"


def test_env_check_runs_the_doctor_and_still_passes_without_bwrap(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    res = _env_check(root, _path_without_bwrap(tmp_path))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[WARN]" in res.stdout and "bubblewrap" in res.stdout
    assert "uv ->" in res.stdout  # the project's own env-check recipe still runs


def test_env_check_is_silent_about_the_sandbox_when_it_is_off(tmp_path: Path) -> None:
    root = _make_project(tmp_path, opt_in=False)
    res = _env_check(root, _path_without_bwrap(tmp_path))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "sandbox" not in (res.stdout + res.stderr).lower()
    assert "uv ->" in res.stdout


def test_the_doctor_never_fails_even_when_it_crashes(tmp_path: Path) -> None:
    root = _make_project(tmp_path)  # opted in, so the broken file below is reported
    _write(tmp_path / "home/.claude/settings.json", {}).write_text("[]")  # wrong shape
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR",)}
    res = subprocess.run(
        [sys.executable, "-B", str(TOOLKIT / "lib/_sandbox_check.py")],
        cwd=root,
        env={**env, "HOME": str(tmp_path / "home")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[WARN]" in res.stdout


def test_an_extra_excluded_command_warns_and_names_the_file(tmp_path: Path) -> None:
    """An exclusion added anywhere but the toolkit runs that command unsandboxed."""
    text = _text(_report(tmp_path, local={"sandbox": {"excludedCommands": ["python3 *"]}}))
    assert "[WARN]" in text and "python3 *" in text and "settings.local.json" in text


def test_an_extra_writable_path_warns(tmp_path: Path) -> None:
    text = _text(_report(tmp_path, user={"sandbox": {"filesystem": {"allowWrite": ["~/"]}}}))
    assert "[WARN]" in text and "allowWrite" in text and "~/" in text


def test_filesystem_isolation_switched_off_warns(tmp_path: Path) -> None:
    text = _text(_report(tmp_path, user={"sandbox": {"filesystem": {"disabled": True}}}))
    assert "[WARN]" in text and "filesystem.disabled" in text


# ── rule: a harness command that writes progress/ is an excludedCommands entry ─
# (docs/design/I3c-sandbox.md, "Adding a harness command"). Every lib/ script that
# can write progress/ is classified here: the commands Claude runs, each covered
# by an exclusion, or why Claude never runs it inside its Bash sandbox. A new
# writer fails this test until it is classified.

RUN_BY_CLAUDE: dict[str, tuple[str, ...]] = {
    "review_with_codex.sh": ("./harness/review_with_codex.sh plan 1.1.1",),
    "_issue.py": (
        "harness/issue.sh new",
        "./harness/issue.sh ask 14",
        "make issue N=14",
        "make issue-start N=14",
        "make issue-new",
    ),
    "sync_plan.py": ("make plan",),
    "_plan_seal.py": ("make plan",),  # record_legacy, called by sync_plan.py
    "_contract.py": (
        "python3 harness/_contract.py contract-propose",
        "python3 harness/_contract.py contract-propose --reason why",
    ),
    "_oracle.py": (
        "python3 harness/_oracle.py contract-propose",
        # The plan gate's retry hint after a re-plan (issue #125): it appends the
        # superseded oracle to progress/review_history/<leaf>.md.
        "python3 harness/_oracle.py replan-history 1.1.1 .git/harness-replan/1.1.1/abc",
    ),
}
NOT_IN_CLAUDES_SANDBOX: dict[str, str] = {
    "_gate_run.py": "run by review_with_codex.sh (excluded); its writers are sealers",
    "_guard.py": "a hook (hooks run outside the sandbox); make check only reads",
    "_legacy.py": "human-run (legacy_inventory.sh create/review/seal are denied)",
    "_migrate.py": "human-run install.sh --migrate",
    "_pr.py": "run by review_with_codex.sh pr / close (excluded)",
    # Claude also runs `python3 harness/_resume.py` (WORKFLOW.md): that only reads; the
    # attempt ledger it keeps is written under .git/harness-*, by the excluded gates.
    "_resume.py": "reads when Claude runs it; the gates (excluded) write .git/harness-*",
    # Claude reaches its CLI through make check (`_stack.py check`, reads only); the seal
    # (progress/stack.sha256) is `_guard.py seal stack`, run by the excluded stack gate.
    "_stack.py": "check/ready only read; the seal runs inside the excluded stack gate",
    # The same for the release plan (docs/design/releases.md): `_releases.py check|ready`
    # only read; the seal (progress/releases.sha256) runs inside the excluded releases
    # gate (`_guard.py seal releases`), and the guard denies `_releases.py seal` to Claude.
    "_releases.py": "check/ready only read; the seal runs inside the excluded releases gate",
    "_colors.sh": "sourced status helpers; prints only",
    "_status_export.py": "writes only stdout or --out",
    "_supersede.py": "run by the gates (excluded); writes .git/harness-*",
    "_verdict.py": "run by review_with_codex.sh (excluded); a sealer for Claude",
    "check.sh": "reads only",
    "run_check.sh": "writes $TMPDIR and .git/harness-check.ok, never progress/",
}
# A coarse net, not a proof: it over-includes (then classify the script as not a
# writer) rather than miss one. Python: any open/write/rename/dump; shell: any
# redirection to a word, tee, cp, mv, mktemp.
RE_PY_WRITES = re.compile(
    r"write_text\(|write_bytes\(|\bopen\(|os\.(replace|rename)\(|shutil\.(copy|move)"
    r"|json\.dump\(|tempfile\."
)
RE_SH_WRITES = re.compile(r">>?\s*[\"'$\w./{]|\btee\b|\bcp\b|\bmv\b|\bmktemp\b|\binstall\b")


def _progress_writers() -> set[str]:
    """lib/ scripts that mention progress and can write a file."""
    found = set()
    for path in sorted((TOOLKIT / "lib").iterdir()):
        regex = {".py": RE_PY_WRITES, ".sh": RE_SH_WRITES}.get(path.suffix)
        if regex is None or not path.is_file():
            continue
        text = path.read_text("utf-8")
        if "progress" in text and regex.search(text):
            found.add(path.name)
    return found


def _excluded(command: str) -> bool:
    """Bash-rule shape: an exact entry, or `prefix *` (also the bare prefix)."""
    for entry in SANDBOX["excludedCommands"]:
        if entry == command:
            return True
        if entry.endswith(" *") and (command == entry[:-2] or command.startswith(entry[:-1])):
            return True
    return False


def test_every_progress_writer_is_classified() -> None:
    unclassified = _progress_writers() - RUN_BY_CLAUDE.keys() - NOT_IN_CLAUDES_SANDBOX.keys()
    assert not unclassified, (
        f"{sorted(unclassified)} can write progress/: if Claude runs it, add its command to "
        "sandbox.excludedCommands in settings.json and RUN_BY_CLAUDE here; otherwise say why "
        "in NOT_IN_CLAUDES_SANDBOX (docs/design/I3c-sandbox.md)"
    )


@pytest.mark.parametrize(
    "command", sorted({c for cs in RUN_BY_CLAUDE.values() for c in cs}), ids=str
)
def test_every_progress_writer_claude_runs_is_excluded(command: str) -> None:
    assert _excluded(command), command


@pytest.mark.parametrize(
    "command",
    [
        "make check",
        "uv run pytest",
        "python3 -c 'x'",
        "python3 harness/_contract.py contract-approve abc",  # human only
        "python3 harness/_oracle.py baseline-create --from HEAD",
    ],
)
def test_ordinary_commands_stay_sandboxed(command: str) -> None:
    assert not _excluded(command), command


def test_migration_never_takes_env_check_for_a_harness_recipe() -> None:
    """install.sh --migrate compares the project Makefile with harness.mk's recipes:
    the env-check prerequisite rule must not make the project's env-check 'custom'."""
    recipes = _upgrade._harness_recipes()
    assert "env-check" not in recipes
    assert "sandbox-check" in recipes
