"""Contract for #94: shared-standard skills carry standards, not workflow."""

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
SHARED = ["docs", "py-style", "py-pytest", "py-pydantic", "py-typing"]

# Workflow wording: git/PR lifecycle, harness gates and role assignments.
# "Decision Gates" (a standards section heading) is not workflow.
WORKFLOW = re.compile(
    r"(?<!pre-)\bcommit\w*|\bgit branch|feature branch|\bpush\b|\bpull request|\bPRs?\b|pre-PR"
    r"|make verify|\bgate\b(?<!Decision Gate)|\bCodex\b|\bClaude\b|\bleader\b"
    r"|\bimplementer\b|\breviewer\b|github/commit|workflow_dispatch|GitHub Actions",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    ("line", "workflow"),
    [
        ("commit the change", True),
        ("Transaction committed to git", True),
        ("then git commit -m x", True),
        ("recommit it", False),  # not a word boundary
        ('    "pre-commit",', False),  # the pre-commit tool's name (Python profile v4)
        ("install pre-commit, then commit", True),
    ],
)
def test_the_workflow_wording_allows_only_the_pre_commit_tool_name(
    line: str, workflow: bool
) -> None:
    assert bool(WORKFLOW.search(line)) is workflow


def _shared_files() -> list[Path]:
    files = [p for name in SHARED for p in (SKILLS / name).rglob("*") if p.is_file()]
    assert files
    return files


@pytest.mark.parametrize("path", _shared_files(), ids=lambda p: str(p.relative_to(SKILLS)))
def test_shared_standard_has_no_workflow(path: Path) -> None:
    """Shared-standard skill files hold no commit/branch/PR/gate/role wording."""
    hits = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if WORKFLOW.search(line.replace("Decision Gate", ""))
        and "Transaction committed" not in line  # DB transaction, not git
    ]
    assert not hits, "\n".join(hits)


def test_implement_defers_to_harness() -> None:
    """`implement` names the harness rule: implementer agent + harness/WORKFLOW.md."""
    text = (SKILLS / "implement" / "SKILL.md").read_text()
    assert "harness/WORKFLOW.md" in text
    assert "implementer" in text


@pytest.mark.parametrize("path", sorted(SKILLS.rglob("SKILL.md")), ids=str)
def test_no_skill_inverts_roles(path: Path) -> None:
    """No skill tells Claude to commit/close or Codex to implement."""
    text = path.read_text()
    inverted = re.compile(
        r"Claude (?:commits|makes the (?:single )?commit|sets `?done)"
        r"|Codex (?:implements|writes (?:the )?(?:code|src))",
        re.IGNORECASE,
    )
    assert not inverted.search(text)
