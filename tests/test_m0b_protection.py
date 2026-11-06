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


# ── make check is unchanged without an inventory ─────────────────────────────


def test_make_check_does_not_mention_a_missing_inventory(project: Project) -> None:
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
    out = (res.stdout + res.stderr).replace(str(TOOLKIT), "<toolkit>")
    assert "legacy" not in out.lower() and "inventory" not in out.lower(), out


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
