"""I3b (#32): unit tests of the shell lexer, the Python write analysis and the glob
intersection used for unmatched globs."""

from __future__ import annotations

import _pywrites
import _shell
import pytest
from _guard import _glob_meet, _path_meet, _shell_body, _xargs_options

# ── _shell ──────────────────────────────────────────────────────────────────


def _kinds(word: _shell.Word) -> list[tuple[str, str]]:
    return [(p.kind, p.text) for p in word.parts]


def test_lexer_parts() -> None:
    (cmd,) = _shell.split("""echo a"$F"'$G' ${H} $(ls) `pwd` ${X%y} $1""")
    words = cmd.words
    assert _kinds(words[1]) == [("lit", "a"), ("var", "F"), ("quoted", "$G")]
    assert _kinds(words[2]) == [("var", "H")]
    assert _kinds(words[3]) == [("cmd", "ls")]
    assert _kinds(words[4]) == [("cmd", "pwd")]
    assert _kinds(words[5]) == [("var", "")]  # parameter expansion: never resolved
    assert _kinds(words[6]) == [("var", "")]


def test_lexer_redirects_and_separators() -> None:
    cmds = _shell.split("a > x 2>&1; b >> y | c 2> z && d < in")
    assert [c.words[0].raw for c in cmds] == ["a", "b", "c", "d"]
    assert [w.raw for w in cmds[0].out_targets] == ["x"]
    assert [w.raw for w in cmds[1].out_targets] == ["y"]
    assert [w.raw for w in cmds[2].out_targets] == ["z"]
    assert [w.raw for w in cmds[3].in_targets] == ["in"]


def test_lexer_process_substitution() -> None:
    (cmd,) = _shell.split("echo x > >(tee f) <(cat g)")
    assert cmd.out_targets[0].is_proc
    assert _kinds(cmd.out_targets[0]) == [("proc", "tee f")]
    assert cmd.words[2].is_proc


def test_lexer_heredoc_and_herestring() -> None:
    cmds = _shell.split("python3 - <<'EOF'\nprint(1)\nEOF\ncat <<< hi")
    assert cmds[0].stdin == ["print(1)"]
    assert cmds[1].stdin == ["hi"]


def test_lexer_comment_and_nested_quotes() -> None:
    (cmd,) = _shell.split("""bash -c "echo '$(echo ")")'" # tail""")
    assert [w.raw for w in cmd.words][:2] == ["bash", "-c"]
    assert len(cmd.words) == 3


# ── option parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("args", "body"),
    [
        (["-c", "x"], 0 + 1),
        (["-lc", "x"], 1),
        (["-e", "-c", "x"], 2),
        (["-o", "pipefail", "-c", "x"], 3),
        (["script.sh"], None),
        (["-l"], None),
    ],
)
def test_shell_body(args: list[str], body: int | None) -> None:
    assert _shell_body(args) == body


@pytest.mark.parametrize(
    ("args", "index", "replace"),
    [
        (["-I{}", "sh"], 1, "{}"),
        (["-I", "%", "sh"], 2, "%"),
        (["-0", "-n", "1", "sh"], 3, None),
        (["--replace", "sh"], 1, "{}"),
        (["-i", "sh"], 1, "{}"),
    ],
)
def test_xargs_options(args: list[str], index: int, replace: str | None) -> None:
    assert _xargs_options(args) == (index, replace)


# ── glob intersection ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b", "meet"),
    [
        ("settings.loc*.json", "settings.local.json", True),
        ("rev*_1.md", "review_*.md", True),
        ("revi?w_9*.md", "review_*.md", True),
        ("*.log", "review_*.md", False),
        ("[rs]eview_1.md", "review_*.md", True),
        ("[xy]eview_1.md", "review_*.md", False),
    ],
)
def test_glob_meet(a: str, b: str, meet: bool) -> None:
    assert _glob_meet(a, b) is meet


def test_path_meet_double_star() -> None:
    assert _path_meet(("a", "tests", "acceptance", "x*"), ("**", "tests", "acceptance", "**"))
    assert _path_meet((".harness",), (".harness", "**"))
    assert not _path_meet(("build", "*.log"), ("progress", "*.sha256"))


# ── _pywrites ───────────────────────────────────────────────────────────────


def _writes(code: str) -> list[tuple[str | None, bool | None]]:
    found = _pywrites.analyze(code)
    assert found is not None
    return [(w.target, w.writes) for w in found]


def test_open_modes() -> None:
    assert _writes("open('a')") == [("a", False)]
    assert _writes("open('a', 'rb+')") == [("a", True)]
    assert _writes("m = 'w'\nopen('a', mode=m)") == [("a", True)]
    assert _writes("open('a', mode=get())") == [("a", None)]


def test_constant_evaluation() -> None:
    assert _writes("p = 'pro' + 'gress'\nopen(p + '/x', 'w')") == [("progress/x", True)]
    assert _writes("d = 'a'\nopen(f'{d}/b', 'w')") == [("a/b", True)]
    code = "import os\nopen(os.path.join('a', 'b'), 'w')"
    assert _writes(code) == [("a/b", True)]
    code = "from pathlib import Path\n(Path('a') / 'b').write_text('x')"
    assert _writes(code) == [("a/b", True)]


def test_reassigned_name_is_unresolved() -> None:
    assert _writes("p = 'a'\np = 'b'\nopen(p, 'w')") == [(None, True)]
    assert _writes("for p in x:\n    open(p, 'w')") == [(None, True)]


def test_bindings() -> None:
    assert _writes("import shutil as s\ns.move('a', 'b')") == [("a", True), ("b", True)]
    assert _writes("from os import remove as r\nr('a')") == [("a", True)]
    assert _writes("from pathlib import Path as P\nP('a').unlink()") == [("a", True)]


def test_os_open_flags() -> None:
    assert _writes("import os\nos.open('a', os.O_RDONLY)") == [("a", False)]
    assert _writes("import os\nos.open('a', os.O_WRONLY | os.O_CREAT)") == [("a", True)]
    assert _writes("import os\nos.open('a', flags)") == [("a", None)]


def test_not_writes() -> None:
    assert _writes("'abc'.replace('a', 'b')") == []
    assert _writes("import webbrowser\nwebbrowser.open('www.example.com')") == []
    assert _writes("x.open()") == []


def test_unparsable() -> None:
    assert _pywrites.analyze("open(") is None


def test_lexer_heredoc_expansion_flag() -> None:
    cmds = _shell.split("a <<EOF\nx\nEOF\nb <<'EOF'\ny\nEOF\nc <<\\EOF\nz\nEOF\nd <<< \"$F\"")
    assert [c.stdin_expands for c in cmds] == [[True], [False], [False], [True]]
    assert cmds[3].stdin == ["${F}"]


def test_heredoc_substitutions() -> None:
    body = "a '$(x 'q' y)' `z` \\$(no) $((1+2)) $(p $(q)) $((1+$(r)))"
    assert _shell.substitutions(body) == ["x 'q' y", "z", "p $(q)", "r"]
