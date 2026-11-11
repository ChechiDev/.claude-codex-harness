"""I2 (#21), block 3 (section D): the guard blocks the state-changing subcommands of
_verdict.py in every form it analyses, and interpreter writes to the verdict files and
receipts that RE_MENTIONS_STATE did not cover yet. Read-only forms stay allowed."""

from __future__ import annotations

import pytest
from conftest import Project, bash_payload

BLOCKED = 2

STATE_CHANGING = [
    "begin --owner 1 code progress/review_1.1.1.md",
    "commit --owner 1 code progress/review_1.1.1.md",
    "abort --owner 1 code progress/review_1.1.1.md",
    "receipt code 1.1.1",
    "receipt issue-draft",
]

SCRIPT_FORMS = [
    "python3 harness/_verdict.py {sub}",
    "python harness/_verdict.py {sub}",
    "harness/_verdict.py {sub}",
    "./harness/_verdict.py {sub}",
    "cd harness && python3 _verdict.py {sub}",
    "bash -c 'python3 harness/_verdict.py {sub}'",
    "true && python3 harness/_verdict.py {sub}",
]

MODULE_FORMS = [
    "PYTHONPATH=harness python3 -m _verdict {sub}",
    "cd harness && python -m _verdict {sub}",
    "python3 -IBm_verdict {sub}",
    "env PYTHONPATH=harness python -m _verdict {sub}",
    "PYTHONPATH=harness uv run -m _verdict {sub}",
    "PYTHONPATH=harness uv run --module _verdict {sub}",
]

UNCERTAIN_WRAPPER_FORMS = [
    "env --some-new-flag value PYTHONPATH=harness python -m _verdict {sub}",
    "PYTHONPATH=harness uv run --unknown-flag value python -m _verdict {sub}",
    "python3 --unknown-flag -m _verdict {sub}",
    "env -S 'python -m _verdict {sub}'",
]


@pytest.mark.parametrize("sub", STATE_CHANGING)
@pytest.mark.parametrize("form", SCRIPT_FORMS + MODULE_FORMS + UNCERTAIN_WRAPPER_FORMS)
def test_state_changing_subcommands_are_blocked(project: Project, form: str, sub: str) -> None:
    cmd = form.format(sub=sub)
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "python3 harness/_verdict.py --help",
        "python3 harness/_verdict.py",
        "harness/_verdict.py -h",
        "PYTHONPATH=harness python3 -m _verdict --help",
        "cat harness/_verdict.py",
        "grep -n receipt harness/_verdict.py",
    ],
)
def test_read_only_forms_stay_allowed(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


VERDICT_TARGETS = [
    "progress/issue_review_ISS-1.md",
    "progress/issue_draft_review.md",
    "progress/verdict_review_1.1.1.md.sha256",
    "progress/verdict_issue_review_ISS-1.md.sha256",
]


@pytest.mark.parametrize("rel", VERDICT_TARGETS)
@pytest.mark.parametrize(
    "template",
    [
        "python3 -c \"open('{rel}', 'w').write('APPROVED')\"",
        "python3 -c \"import os; os.remove('{rel}')\"",
        "python3 -c \"import pathlib; pathlib.Path('{rel}').write_text('x')\"",
        'perl -e \'open(F, ">{rel}"); print F "x"\'',
    ],
)
def test_interpreter_writes_to_verdicts_and_receipts_are_blocked(
    project: Project, rel: str, template: str
) -> None:
    project.write(rel, "x\n")
    cmd = template.format(rel=rel)
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize("rel", VERDICT_TARGETS)
def test_interpreter_reads_of_verdicts_stay_allowed(project: Project, rel: str) -> None:
    project.write(rel, "x\n")
    cmd = f"python3 -c \"print(open('{rel}').read())\""
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


# ── Codex block 3 r1: dotted module names and perl write modes ──────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "PYTHONPATH=. python3 -m lib._verdict receipt issue-draft",
        "python3 -m lib._verdict begin --owner 1 code progress/review_1.1.1.md",
        "python3 -m harness._verdict commit --owner 1 code progress/review_1.1.1.md",
        "python3 -IBmlib._verdict abort --owner 1 code progress/review_1.1.1.md",
        "python3 -m lib._legacy seal",
        "python3 -m harness._legacy create --at HEAD",
        "python3 -m lib._guard seal 1.1.1",
    ],
)
def test_dotted_module_names_are_blocked(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_dotted_module_read_only_forms_stay_allowed(project: Project) -> None:
    for cmd in ("python3 -m lib._verdict --help", "python3 -m lib._legacy verify"):
        res = project.run("guard_bash.sh", bash_payload(cmd))
        assert res.returncode == 0, (cmd, res.stderr)


PERL_WRITES = [
    'perl -e \'open(F, ">>{rel}"); print F "x"\'',
    'perl -e \'open(F, "+<{rel}"); print F "x"\'',
    'perl -e \'open(my $f, ">", "{rel}"); print $f "x"\'',
    "perl -e \"open(F, '>', '{rel}'); print F 'x'\"",
]


@pytest.mark.parametrize("rel", [*VERDICT_TARGETS, "progress/legacy_inventory.json"])
@pytest.mark.parametrize("template", PERL_WRITES)
def test_perl_write_modes_are_blocked(project: Project, rel: str, template: str) -> None:
    project.write(rel, "x\n")
    cmd = template.format(rel=rel)
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize("rel", [*VERDICT_TARGETS, "progress/legacy_inventory.json"])
def test_perl_reads_stay_allowed(project: Project, rel: str) -> None:
    project.write(rel, "x\n")
    cmd = f"perl -e 'open(F, \"<{rel}\"); print <F>'"
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)
