"""I3b (#32): dynamic Bash targets, nested shells and finer interpreter analysis."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import Project, bash_payload
from i3a_helpers import BLOCKED, HARNESS_MSG, installed

P = "progress/review_1.md"  # protected state, present
H = ".claude/settings.local.json"  # harness-owned, absent
PROTECTED_MSGS = ("would modify", "cannot inspect")
SEAL_MSG = "sealing"


@pytest.fixture
def proj(project: Project, tmp_path: Path) -> Project:
    installed(project, tmp_path)
    project.write(P, "# Review\n")
    return project


def _bash(project: Project, cmd: str) -> subprocess.CompletedProcess[str]:
    return project.run("guard_bash.sh", bash_payload(cmd))


def _blocked_protected(project: Project, cmd: str) -> None:
    res = _bash(project, cmd)
    assert res.returncode == BLOCKED, (cmd, res.stderr)
    assert any(m in res.stderr for m in PROTECTED_MSGS), (cmd, res.stderr)


def _blocked_harness(project: Project, cmd: str) -> None:
    res = _bash(project, cmd)
    assert res.returncode == BLOCKED, (cmd, res.stderr)
    assert HARNESS_MSG in res.stderr, (cmd, res.stderr)


def _blocked_seal(project: Project, cmd: str) -> None:
    res = _bash(project, cmd)
    assert res.returncode == BLOCKED, (cmd, res.stderr)
    assert SEAL_MSG in res.stderr, (cmd, res.stderr)


def _allowed(project: Project, cmd: str) -> None:
    res = _bash(project, cmd)
    assert res.returncode == 0, (cmd, res.stderr)


# ── Acceptance 1: Context table ─────────────────────────────────────────────

CONTEXT_ROWS = [
    "F={t}; echo x > $F",
    "echo x > $(echo {t})",
    "python3 -c \"from pathlib import Path; Path('{t}').open('w')\"",
    "python3 -c \"open('{t}', mode='w')\"",
    "python3 -c \"m='w'; open('{t}', m)\"",
    'echo {t} | xargs -I{{}} sh -c "echo x > {{}}"',
    'bash -lc "echo x > {t}"',
    "echo x > >(tee {t})",
    "python3 - <<EOF\nfrom pathlib import Path\nPath('{t}').open('w')\nEOF",
]


@pytest.mark.e2e
@pytest.mark.parametrize("row", CONTEXT_ROWS)
def test_context_rows_protected(proj: Project, row: str) -> None:
    _blocked_protected(proj, row.format(t=P))


@pytest.mark.e2e
@pytest.mark.parametrize("row", CONTEXT_ROWS)
def test_context_rows_harness(proj: Project, row: str) -> None:
    _blocked_harness(proj, row.format(t=H))


def test_context_variable_toolkit_file(proj: Project) -> None:
    _blocked_harness(proj, "F=harness/_guard.py; sed -i s/a/b/ $F")


def test_context_unmatched_glob(proj: Project) -> None:
    _blocked_harness(proj, "cd .claude; echo x > settings.loc*.json")


def test_context_matched_glob_stays_blocked(proj: Project) -> None:
    _blocked_protected(proj, "cd progress; echo x > rev*_1.md")


def test_context_split_constant_path(proj: Project) -> None:
    _blocked_protected(proj, "python3 -c \"open('pro' + 'gress/review_1.md', 'w')\"")


def test_context_interpreter_read_only_allowed(proj: Project) -> None:
    _allowed(proj, "python3 -c \"print(open('harness/_guard.py').read()); open('out.txt','w')\"")


def test_accidental_blocks_stay(proj: Project) -> None:
    _blocked_protected(proj, f"python3 -c \"from os import remove; remove('{P}')\"")
    _blocked_protected(proj, "cd progress; bash -lc 'cd ..'; echo x > review_1.md")


# ── Acceptance 2: variants ──────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        "F={t}; echo x > ${{F}}",
        'F={t}; echo x > "$F"',
        "export F={t}; echo x > $F",
        "F={t} tee $F < /dev/null",
        "echo x > `echo {t}`",
        "bash -ec 'echo x > {t}'",
        "sh -xc 'echo x > {t}'",
        'echo {t} | xargs -I {{}} sh -c "echo x > {{}}"',
        "echo x | xargs sh -c 'cat > \"$1\"' _ {t}",
        "cat <(echo x > {t})",
    ],
)
def test_variants(proj: Project, cmd: str) -> None:
    _blocked_protected(proj, cmd.format(t=P))
    _blocked_harness(proj, cmd.format(t=H))


def test_chained_variable(proj: Project) -> None:
    _blocked_harness(proj, "D=.claude; F=$D/settings.local.json; echo x > $F")
    _blocked_protected(proj, "D=progress; F=$D/review_1.md; echo x > $F")


def test_home_variable(proj: Project) -> None:
    _blocked_harness(proj, 'echo x > "$CLAUDE_HOME/skills/foo"')


def test_project_dir_variable(proj: Project) -> None:
    proj.env["CLAUDE_PROJECT_DIR"] = str(proj.root)
    _blocked_harness(proj, "echo x > $CLAUDE_PROJECT_DIR/.claude/settings.local.json")


def test_cd_then_unmatched_glob_protected(proj: Project) -> None:
    _blocked_protected(proj, "cd progress; echo x > revi?w_9*.md")


def test_toolkit_file_through_harness_variable(proj: Project) -> None:
    _blocked_harness(proj, "F=harness/new_file.py; echo x > $F")


# ── Acceptance 3: nested frames ─────────────────────────────────────────────


def test_nested_cd_does_not_leak_out(proj: Project) -> None:
    _allowed(proj, "cd progress; bash -lc 'cd ..; echo x > out.txt'")


def test_unexported_variable_in_child(proj: Project) -> None:
    _blocked_protected(proj, f"F={P}; bash -c 'echo x > $F'")
    _blocked_protected(proj, f"export F={P}; bash -c 'echo x > $F'")


def test_substitutions_see_unexported(proj: Project) -> None:
    _blocked_protected(proj, 'F=progress; echo x > >(tee "$F/review_1.md")')
    _blocked_protected(proj, "F=progress; echo $(echo x > $F/review_1.md)")


def test_python_unresolved_mode(proj: Project) -> None:
    _blocked_protected(proj, "python3 -c \"p='pro'+'gress/review_1.md'; open(p, mode=get_mode())\"")


def test_deep_nesting_blocked(proj: Project) -> None:
    cmd = "echo ok"
    for _ in range(7):
        cmd = f"bash -c {subprocess.list2cmdline([cmd])}"
    res = _bash(proj, cmd)
    assert res.returncode == BLOCKED, res.stderr


def test_quoted_nested_target(proj: Project) -> None:
    _blocked_protected(proj, f"bash -c \"echo x > '{P}'\"")


# ── Acceptance 4: interpreters ──────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "code",
    [
        "from pathlib import Path; Path('{t}').write_text('x')",
        "import os; os.rename('a', '{t}')",
        "import shutil; shutil.copy('a', '{t}')",
        "p='{t}'; m='w'; open(p, mode=m)",
        "with open('{t}', 'w') as f: pass",
        "from os import remove; remove('{t}')",
        "import shutil as s; s.move('x', '{t}')",
        "import shutil; shutil.move('{t}', 'out.txt')",
        "import os; os.rename('{t}', 'out.txt')",
        "from pathlib import Path; Path('{t}').rename('out.txt')",
        "import os; os.open('{t}', os.O_WRONLY | os.O_CREAT)",
    ],
)
def test_python_writes(proj: Project, code: str) -> None:
    _blocked_protected(proj, f'python3 -c "{code.format(t=P)}"')
    _blocked_harness(proj, f'python3 -c "{code.format(t=H)}"')


def test_python_path_division(proj: Project) -> None:
    _blocked_harness(
        proj,
        "python3 -c \"from pathlib import Path; (Path('.claude') / 'settings.local.json')"
        ".write_text('x')\"",
    )


def test_python_fstring_and_join(proj: Project) -> None:
    _blocked_protected(proj, "python3 -c \"d='progress'; open(f'{d}/review_1.md', 'w')\"")
    _blocked_protected(
        proj, "python3 -c \"import os; open(os.path.join('progress', 'review_1.md'), 'w')\""
    )


def test_python_project_script(proj: Project) -> None:
    proj.write("scripts/w.py", f"open('{H}', 'w').write('x')\n")
    _blocked_harness(proj, "python3 scripts/w.py")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "code",
    [
        "print(open('harness/_guard.py').read()); open('out.txt', 'w')",
        "import os; os.open('{h}', os.O_RDONLY); open('out.txt', 'w')",
        "import os; os.open('harness/_guard.py', os.O_RDONLY); open('out.txt', 'w')",
    ],
)
def test_python_reads_allowed(proj: Project, code: str) -> None:
    _allowed(proj, f'python3 -c "{code.format(h=H)}"')


def test_python_unparsable_falls_back(proj: Project) -> None:
    _blocked_harness(proj, f"python3 -c \"open('{H}', 'w') +++ (\"")


def test_other_interpreters_regex(proj: Project) -> None:
    _blocked_protected(proj, f'perl -e \'open(my $f, ">", "{P}")\'')
    _blocked_harness(proj, f"node -e \"require('fs').writeFileSync('{H}','x')\"")


# ── Acceptance 5: seals ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "./harness/review_with_codex.sh seal 1.1.1",
        "python3 harness/_guard.py seal 1.1.1",
        "bash -c './harness/review_with_codex.sh seal 1.1.1'",
        "bash -lc './harness/review_with_codex.sh seal 1.1.1'",
        "echo x | xargs -I{} sh -c './harness/review_with_codex.sh seal {}'",
        "echo $(./harness/review_with_codex.sh seal 1.1.1)",
    ],
)
def test_seals(proj: Project, cmd: str) -> None:
    _blocked_seal(proj, cmd)


# ── Acceptance 6: not blocked ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "F=out.txt; echo x > $F",
        "echo x > $LOG",
        'echo x > "$(mktemp)"',
        'bash -lc "echo ok"',
        'ls | xargs -I{} sh -c "echo {}"',
        "echo x > build/*.log",
        "python3 -c \"open('out.txt','w')\"",
        "cat harness/_guard.py",
    ],
)
def test_allowed(proj: Project, cmd: str) -> None:
    _allowed(proj, cmd)


# ── Dependency (#30): supersede ticket through dynamic targets ──────────────


def test_supersede_ticket_dynamic(proj: Project) -> None:
    ticket = ".git/harness-supersede-ticket/ISS-1"
    for cmd in (f"F={ticket}; echo x > $F", f"bash -lc 'echo x > {ticket}'"):
        res = _bash(proj, cmd)
        assert res.returncode == BLOCKED, (cmd, res.stderr)


# ── Found replaying real sessions (codexion / guakamole) ────────────────────


def test_python_target_from_argv(proj: Project) -> None:
    code = "import sys\nopen(sys.argv[1], 'w')"
    _blocked_protected(proj, f"python3 - {P} <<'EOF'\n{code}\nEOF")
    _allowed(proj, f"python3 - out.txt <<'EOF'\n{code}\nEOF")


@pytest.mark.parametrize(
    "cmd",
    [
        'B=x; sed -i "s/a/$B/" notes.md',
        'n=3; sed -i "${n}s/a/b/" notes.md',
        'perl -pi -e "s/$X/y/" notes.md',
    ],
)
def test_inplace_script_is_not_a_target(proj: Project, cmd: str) -> None:
    proj.write("notes.md", "a\n")
    _allowed(proj, f"{cmd} && cat {P}")


def test_inplace_files_still_checked(proj: Project) -> None:
    _blocked_protected(proj, f"sed -i -e 's/a/b/' notes.md {P}")
    _blocked_protected(proj, f"perl -pi -e 's/a/b/' {P}")
    _blocked_protected(proj, f"sed -i 's/a/b/' {P}")


def test_python_heredoc_into_toolkit(proj: Project) -> None:
    from conftest import TOOLKIT

    code = "from pathlib import Path\nPath('install.sh').write_text('x')"
    _blocked_harness(proj, f"cd {TOOLKIT} && python3 - <<'EOF'\n{code}\nEOF")


# ── D1 refinement: executing a harness script is not a mention ──────────────

RUN_HARNESS = [
    'f=$(ls docs/*.md) && sed -i s/a/b/ "$f" && ./harness/review_with_codex.sh plan 6.3.4',
    'f=$(ls docs/*.md); echo x > "$f"; python3 harness/_harness.py branch 7.2',
    'B="$(python3 harness/_harness.py branch 7.2)" && f=$(ls docs/*.md) && sed -i x "$f"',
    "L=$(mktemp); bash harness/run.sh; echo done > $L",
]


@pytest.mark.parametrize("cmd", RUN_HARNESS)
def test_executed_harness_script_is_not_a_mention(proj: Project, cmd: str) -> None:
    _allowed(proj, cmd)


STILL_HARNESS = [
    'f=harness/x; ./harness/review_with_codex.sh; echo x > "$f"',
    'cat harness/x; ./harness/x; echo > "$f"',
    './harness/x; echo > "$f"; cp harness/x.bak y',
    "python3 harness/_harness.py; python3 -c \"open(harness_path(), 'w')\" harness/y",
]


@pytest.mark.parametrize("cmd", STILL_HARNESS)
def test_harness_argument_still_a_mention(proj: Project, cmd: str) -> None:
    _blocked_harness(proj, cmd)


# ── Review round 1 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        f"sed -i -e's/a/b/' {P}",
        f"sed -i -es/a/b/ {P}",
        f"sed -i --expression=s/a/b/ {P}",
        f"perl -i -pe's/a/b/' {P}",
    ],
)
def test_inplace_attached_script(proj: Project, cmd: str) -> None:
    _blocked_protected(proj, cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        f"F={P}; python3 - <<EOF\nopen('$F', 'w')\nEOF",
        f"F={P}; python3 - <<EOF\nopen('${{F}}', 'w')\nEOF",
        f"F={P}; python3 - <<< \"open('$F', 'w')\"",
        "python3 - <<EOF\nopen('$(echo progress/review_1.md)', 'w')\nEOF",
        f"F={H}; python3 - <<EOF\nopen('$F', 'w')\nEOF",
    ],
)
def test_expanding_heredoc(proj: Project, cmd: str) -> None:
    res = _bash(proj, cmd)
    assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_quoted_heredoc_is_not_expanded(proj: Project) -> None:
    _allowed(proj, f"F={P}; python3 - <<'EOF'\nopen('$F', 'w')\nEOF")


def test_expanding_heredoc_unrelated_write_allowed(proj: Project) -> None:
    _allowed(proj, "D=/tmp/x; python3 - <<EOF\nopen('$D/out.log', 'w')\nEOF")


@pytest.mark.parametrize(
    "name",
    [
        "progress/close_1.msg",
        "progress/x.sha256",
        "progress/review_history/r.md",
        "progress/issue_migrations_1.json",
        "progress/oracle_migrations/1.json",
        ".git/harness-verdict-slot",
    ],
)
def test_unresolved_mentions_every_protected_name(proj: Project, name: str) -> None:
    _blocked_protected(proj, f'echo x > "$(printf {name})"')


# ── Review round 2 ──────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        f"python3 - <<EOF\nprint('$(touch {P})')\nEOF",
        f"cat <<EOF\n$(echo x > {P})\nEOF",
        f"cat <<EOF\n`rm {P}`\nEOF",
        f'cat <<< "$(touch {P})"',
        f"cat <<EOF\n$(bash -c 'echo x > {P}')\nEOF",
        f"cat <<EOF\n$(( $(touch {P}; echo 1) + 1 ))\nEOF",
    ],
)
def test_heredoc_substitution_is_analysed(proj: Project, cmd: str) -> None:
    _blocked_protected(proj, cmd)


def test_quoted_heredoc_substitution_is_text(proj: Project) -> None:
    _allowed(proj, f"cat <<'EOF'\n$(touch {P})\nEOF")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        f"echo $(( $(touch {P}; echo 1) + 1 ))",
        f"x=$(( `rm {P}` ))",
        f'echo "${{X:-$(touch {P})}}"',
        f"echo ${{X:=`rm {P}`}}",
        f'echo "${{X:-$(echo }}; touch {P})}}"',
        f"echo ${{X:-`echo }}; rm {P}`}}",
        f'echo "${{X:-$(echo x # )\ntouch {P})}}"',
        f"echo $(echo x # )\ntouch {P})",
    ],
)
def test_substitution_inside_expansion(proj: Project, cmd: str) -> None:
    _blocked_protected(proj, cmd)


# ── Review round 6: constructs the lexer does not follow fail closed ────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        f'echo "$(cat <<EOF\n)\nEOF\ntouch {P}\n)"',
        f'echo "$(case x in x) touch {P} ;; esac)"',
        f'echo "$( (true)# )\ntouch {P}\n)"',
        f"echo \"$(printf %s $'\\')'; touch {P})\"",
        f'echo "`echo \\`touch {P}\\``"',
        f"cat <<EOF\nEO\\\nF\ntouch {P}",
        f"cat <<$X\n$X\ntouch {P}",
        f"cat <<$(printf EOF)\nEOF\ntouch {P}",
    ],
)
def test_unfollowed_construct_fails_closed(proj: Project, cmd: str) -> None:
    _blocked_protected(proj, cmd)


def test_unfollowed_construct_without_mention_allowed(proj: Project) -> None:
    _allowed(proj, 'echo "$(case x in x) echo hi ;; esac)" > /tmp/x.log')


def test_unfollowed_construct_executed_harness_allowed(proj: Project) -> None:
    _allowed(proj, "bash harness/run.sh <<$X\n$X")


@pytest.mark.parametrize(
    "cmd",
    [
        "bash -e harness/run.sh <<$X\n$X",
        "bash -o pipefail harness/run.sh <<$X\n$X",
        'python3 -B harness/_harness.py branch 7.2; f=$(ls docs/*.md); echo x > "$f"',
        'python3 -X dev harness/_harness.py; f=$(ls docs/*.md); echo x > "$f"',
    ],
)
def test_executed_harness_script_after_options(proj: Project, cmd: str) -> None:
    _allowed(proj, cmd)


def test_harness_after_c_is_code_not_script(proj: Project) -> None:
    _blocked_harness(proj, 'bash -c "cat harness/x"; echo > "$f"')


@pytest.mark.parametrize(
    "run",
    [
        "bash -n harness/review_with_codex.sh",
        "bash -en harness/review_with_codex.sh",
        "python3 --help harness/_harness.py",
        "python3 -V harness/_harness.py",
    ],
)
def test_non_executing_option_is_still_a_mention(proj: Project, run: str) -> None:
    _blocked_harness(proj, f'f=$(ls docs/*.md); echo x > "$f"; {run}')
