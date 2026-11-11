"""M0b (#16), block 2: Claude cannot write or seal the legacy inventory, can still
verify and read it, `make check` is unchanged without one, and the docs say how."""

from __future__ import annotations

import os
import subprocess

import pytest
from conftest import TOOLKIT, Project, bash_payload, write_payload

BLOCKED = 2
INVENTORY = "progress/legacy_inventory.json"
REVIEW = "progress/legacy_inventory_review.md"
SEAL = "progress/legacy_inventory.sha256"


# ── Write/Edit tools ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", [INVENTORY, REVIEW, SEAL])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_claude_cannot_write_inventory_artifacts(project: Project, rel: str, tool: str) -> None:
    res = project.run("guard_write.sh", write_payload(rel, tool))
    assert res.returncode == BLOCKED, res.stderr


# ── Bash: writes and deletes ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        f"echo x > {INVENTORY}",
        f"printf '{{}}' >> {INVENTORY}",
        f"echo ok > {REVIEW}",
        f"echo abc > {SEAL}",
        f"rm {INVENTORY}",
        f"rm -f {REVIEW} {SEAL}",
        f"mv {INVENTORY} /tmp/x.json",
        f"cp /tmp/x.json {INVENTORY}",
        f"sed -i 's/a/b/' {INVENTORY}",
        f"python3 -c \"open('{INVENTORY}', 'w').write('{{}}')\"",
        f"python3 -c \"import os; os.remove('{REVIEW}')\"",
    ],
)
def test_claude_cannot_change_the_inventory_through_bash(project: Project, cmd: str) -> None:
    for rel in (INVENTORY, REVIEW, SEAL):
        project.write(rel, "x\n")
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


# ── Bash: the human-only subcommands ─────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "harness/legacy_inventory.sh create --at HEAD",
        "./harness/legacy_inventory.sh create --at abc123",
        "bash harness/legacy_inventory.sh review",
        "harness/legacy_inventory.sh seal",
        "sh ./harness/legacy_inventory.sh seal",
        "python3 harness/_legacy.py create --at HEAD",
        "python3 harness/_legacy.py review",
        "python3 harness/_legacy.py seal",
        "cd harness && ./legacy_inventory.sh seal",
        "true && harness/legacy_inventory.sh create --at HEAD",
    ],
)
def test_claude_cannot_run_create_review_or_seal(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "harness/legacy_inventory.sh verify",
        "./harness/legacy_inventory.sh verify",
        "python3 harness/_legacy.py verify",
        f"cat {INVENTORY}",
        f"cat {REVIEW}",
        f"cat {SEAL}",
        f"jq . {INVENTORY}",
        f"grep -n sha256 {INVENTORY}",
        f"sha256sum {INVENTORY}",
    ],
)
def test_verify_and_reads_stay_allowed(project: Project, cmd: str) -> None:
    for rel in (INVENTORY, REVIEW, SEAL):
        project.write(rel, "x\n")
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


# ── Codex code review r2: the `python -m` module form ────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "PYTHONPATH=harness python3 -m _legacy create --at HEAD",
        "PYTHONPATH=harness python3 -m _legacy review",
        "PYTHONPATH=harness python3 -m _legacy seal",
        "cd harness && python3 -m _legacy seal",
        "env PYTHONPATH=harness python -m _legacy seal",
        "python3 -m _legacy seal",
        "PYTHONPATH=harness python3 -m _guard seal 1.1.1",
        "cd harness && python -m _guard seal roadmap",
    ],
)
def test_claude_cannot_run_the_module_form(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_the_module_form_of_verify_stays_allowed(project: Project) -> None:
    res = project.run("guard_bash.sh", bash_payload("PYTHONPATH=harness python3 -m _legacy verify"))
    assert res.returncode == 0, res.stderr


# ── Codex code review r3: wrapper flags that take a value ────────────────────

WRAPPER_PREFIXES = [
    "env -u DUMMY PYTHONPATH=harness",
    "env --unset DUMMY PYTHONPATH=harness",
    "env --unset=DUMMY PYTHONPATH=harness",
    "env -C . PYTHONPATH=harness",
    "env -S 'x' PYTHONPATH=harness",
    "PYTHONPATH=harness uv run --python 3.12",
    "PYTHONPATH=harness uv run --with pytest",
    "PYTHONPATH=harness uv run --project . --python 3.12",
    "PYTHONPATH=harness uv run -p 3.12",
    "PYTHONPATH=harness uv run --unknown-flag value",  # fail-closed
    "env --some-new-flag value PYTHONPATH=harness",  # fail-closed
]


@pytest.mark.parametrize(
    "cmd",
    [
        "PYTHONPATH=harness uv run -m _legacy seal",
        "PYTHONPATH=harness uv run --module _legacy create --at HEAD",
        "PYTHONPATH=harness uv run --python 3.12 -m _guard seal 1.1.1",
        "env -S 'python -m _legacy seal'",
    ],
)
def test_uv_run_module_and_env_split_string_are_blocked(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize("verb", ["create --at HEAD", "review", "seal"])
@pytest.mark.parametrize("prefix", WRAPPER_PREFIXES)
def test_wrapper_flag_values_do_not_hide_the_module_form(
    project: Project, prefix: str, verb: str
) -> None:
    cmd = f"{prefix} python -m _legacy {verb}"
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize("prefix", WRAPPER_PREFIXES[:9])
def test_wrapper_flag_values_do_not_hide_guard_seal(project: Project, prefix: str) -> None:
    cmd = f"{prefix} python -m _guard seal 1.1.1"
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "env -u DUMMY PYTHONPATH=harness python -m _legacy verify",
        "PYTHONPATH=harness uv run --python 3.12 python -m _legacy verify",
        "uv run --with pytest pytest -q",
        "env -u DUMMY ls",
    ],
)
def test_wrapper_flags_with_harmless_commands_stay_allowed(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


# ── Codex code review r4: python short flags attached to -m ──────────────────


@pytest.mark.parametrize("verb", ["create --at HEAD", "review", "seal"])
@pytest.mark.parametrize(
    "flags",
    [
        "-m_legacy",
        "-Im_legacy",
        "-IBm_legacy",
        "-I -m_legacy",
        "-Xdev -m_legacy",
        "-X dev -Im_legacy",
        "-W ignore -m_legacy",
        "-Wignore -Em_legacy",
        "-qm _legacy",
    ],
)
def test_attached_module_flag_is_blocked(project: Project, flags: str, verb: str) -> None:
    cmd = f"PYTHONPATH=harness python3 {flags} {verb}"
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "PYTHONPATH=harness python3 -m_guard seal 1.1.1",
        "PYTHONPATH=harness python -Im_guard seal roadmap",
        "PYTHONPATH=harness uv run python -Im_legacy seal",
        "PYTHONPATH=harness python3 -Z -m _legacy seal",  # unknown short flag: fail-closed
        # Codex r5: fail-closed must also see a module name attached to -m
        "PYTHONPATH=harness python3 -Z -m_legacy seal",
        "PYTHONPATH=harness python3 --unknown -Im_legacy seal",
        "PYTHONPATH=harness python3 -Z -m_guard seal 1.1.1",
    ],
)
def test_attached_module_flag_other_forms_are_blocked(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "PYTHONPATH=harness python3 -m_legacy verify",
        "PYTHONPATH=harness python3 -Im_legacy verify",
        "python3 -Im pytest -q",
        "python3 -X dev -c 'print(1)'",
    ],
)
def test_attached_module_flag_harmless_forms_stay_allowed(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


# ── make check and a missing inventory (I1 is the first rule that reads it) ───────


def _check_output(project: Project) -> str:
    env = {**os.environ, **project.env, "CLAUDE_PROJECT_DIR": str(project.root)}
    res = subprocess.run(
        ["bash", str(TOOLKIT / "lib/check.sh")],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    return (res.stdout + res.stderr).replace(str(TOOLKIT), "<toolkit>").lower()


def test_make_check_mentions_a_missing_inventory_only_with_a_seal_gap(project: Project) -> None:
    assert _check_output(project).count("legacy inventory not created") == 1
    assert project.run("review_with_codex.sh", None, "seal", "roadmap").returncode == 0
    # The fixture approves phase 01 without a plan file, which is a gap too.
    (project.root / "progress/phase_review_01.md").unlink()
    # Its hand-written verdicts lack receipts (I2), also a gap: the human writes them.
    for args in (("roadmap",), ("plan", "1.1.1")):
        res = subprocess.run(
            ["python3", "harness/_verdict.py", "receipt", *args],
            cwd=project.root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert res.returncode == 0, res.stdout + res.stderr
    out = _check_output(project)
    assert "legacy" not in out and "inventory" not in out, out


# ── docs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "needle",
    [
        "## Legacy inventory",
        "legacy_inventory.sh create --at",
        "legacy_inventory.sh review",
        "legacy_inventory.sh seal",
        "legacy_inventory.sh verify",
        INVENTORY,
        REVIEW,
        SEAL,
        "INVENTORY_APPROVED",
        "human-only",
    ],
)
def test_usage_documents_the_legacy_inventory(needle: str) -> None:
    assert needle in (TOOLKIT / "docs/usage.md").read_text()
