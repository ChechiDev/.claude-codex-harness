"""Shell lexer for the Bash guard (I3b).

Splits one Bash call into simple commands whose words keep their structure: quoted
and unquoted literal text, parameter references, command substitutions and process
substitutions. Expansion happens later, in the analyser, against its variable frame.
Here-doc and here-string bodies are attached to their command. Best effort: input bash
itself would reject (unbalanced quotes) is read up to its end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LIT, QUOTED, VAR, CMD, PROC = "lit", "quoted", "var", "cmd", "proc"
RE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SEPARATORS = ";&|()\n"
REDIRECT_START = "<>"


@dataclass
class Part:
    kind: str  # LIT | QUOTED | VAR | CMD | PROC
    text: str  # literal text, variable name ("" = not plain), or substitution body


@dataclass
class Word:
    parts: list[Part] = field(default_factory=list)
    raw: str = ""

    @property
    def is_proc(self) -> bool:
        return len(self.parts) == 1 and self.parts[0].kind == PROC


@dataclass
class Command:
    words: list[Word] = field(default_factory=list)
    out_targets: list[Word] = field(default_factory=list)
    in_targets: list[Word] = field(default_factory=list)
    stdin: list[str] = field(default_factory=list)  # here-doc / here-string bodies
    # per body: Bash expands `$…` and backticks in it (unquoted here-doc, here-string)
    stdin_expands: list[bool] = field(default_factory=list)


def _respell(part: Part) -> str:
    """A here-string part as Bash would expand it: substitutions keep their `$` form."""
    if part.kind == VAR:
        return f"${{{part.text}}}" if part.text else "${?}"
    if part.kind == CMD:
        return f"$({part.text})"
    return part.text


def substitutions(body: str) -> list[str]:
    """The `$(…)` and backtick commands Bash runs while expanding an unquoted here-doc
    body. Quotes are literal there; `\\$` and arithmetic `$((…))` itself are not commands."""
    found: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
        elif body.startswith("$((", i):
            i += 3  # arithmetic: not a command, but a `$(…)` inside it runs
        elif body.startswith("$(", i):
            end = _closing(body, i + 2, "(", ")")
            found.append(body[i + 2 : end - 1])
            i = end
        elif body[i] == "`":
            end = body.find("`", i + 1)
            end = len(body) if end < 0 else end
            found.append(body[i + 1 : end])
            i = end + 1
        else:
            i += 1
    return found


def _upto(text: str, needle: str, i: int, past: int) -> int:
    """Index of `needle` from `i` plus `past`, or the end of `text`."""
    end = text.find(needle, i)
    return len(text) if end < 0 else end + past


def _skip(text: str, i: int, command: bool) -> int | None:
    """Index past a quoted span, a nested substitution or (in a command) a comment
    starting at `i`; None when `text[i]` starts none of them."""
    ch = text[i]
    if ch == "\\":
        return i + 2
    if ch in "'`":
        return _upto(text, ch, i + 1, 1)
    if ch == '"':
        return _skip_double(text, i + 1)
    if ch == "#" and command and (i == 0 or text[i - 1] in " \t\n;&|()"):
        return _upto(text, "\n", i, 0)  # a comment inside a command: `)` there is text
    if text.startswith("$(", i):  # a nested substitution: its brackets are its own
        return _closing(text, i + 2, "(", ")")
    return None


def _closing(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the `close_ch` matching the `open_ch` before `i` (quote-aware)."""
    depth = 1
    while i < len(text):
        skipped = _skip(text, i, command=open_ch == "(")
        if skipped is not None:
            i = skipped
            continue
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


# Inside a substitution body, constructs this lexer does not follow the way Bash does
# (review round 6): a here-doc, `case`, `$'…\\'…'`, an escaped nested backtick (the
# body then ends in a backslash). The body may have been cut short.
RE_UNSURE_BODY = re.compile(r"<<(?!<)|(^|[\s;&|(])case\s|\$'[^']*\\|\\`|\\$")


def _skip_double(text: str, i: int) -> int:
    """Index just past the closing `"` of a double-quoted string starting at `i`."""
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
        elif ch == '"':
            return i + 1
        elif text.startswith("$(", i):
            i = _closing(text, i + 2, "(", ")")
        elif ch == "`":
            end = text.find("`", i + 1)
            i = len(text) if end < 0 else end + 1
        else:
            i += 1
    return len(text)


class Lexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.commands: list[Command] = [Command()]
        self.unsure = False  # a construct read differently from Bash (fail closed)
        # (command, delimiter, strip tabs, body expands)
        self.heredocs: list[tuple[Command, str, bool, bool]] = []

    # ── words ────────────────────────────────────────────────────────────────

    def _dollar(self, parts: list[Part], quoted: bool) -> None:
        text, i = self.text, self.i
        nxt = text[i + 1 : i + 2]
        if text.startswith("$((", i):
            self.i = _closing(text, i + 3, "(", ")")
            if self.text[self.i : self.i + 1] == ")":
                self.i += 1
            # arithmetic is never resolved, but a `$(…)` inside it runs
            parts += [Part(CMD, c) for c in substitutions(text[i + 3 : self.i])]
            parts.append(Part(VAR, ""))
        elif nxt == "(":
            end = _closing(text, i + 2, "(", ")")
            parts.append(Part(CMD, text[i + 2 : end - 1]))
            self.i = end
        elif nxt == "{":
            end = _closing(text, i + 2, "{", "}")
            inner = text[i + 2 : end - 1]
            if not RE_NAME.fullmatch(inner):  # `${X:-$(cmd)}`: the command runs
                parts += [Part(CMD, c) for c in substitutions(inner)]
            parts.append(Part(VAR, inner if RE_NAME.fullmatch(inner) else ""))
            self.i = end
        elif nxt and (nxt.isalpha() or nxt == "_"):
            match = RE_NAME.match(text, i + 1)
            assert match is not None
            parts.append(Part(VAR, match.group()))
            self.i = match.end()
        elif nxt and (nxt.isdigit() or nxt in "@*#?$!-"):
            parts.append(Part(VAR, ""))  # positional or special: never resolved
            self.i = i + 2
        elif nxt == "'" and not quoted:  # $'...' ANSI-C quoting: literal enough
            end = text.find("'", i + 2)
            end = len(text) if end < 0 else end
            parts.append(Part(QUOTED, text[i + 2 : end]))
            self.i = end + 1
        else:
            parts.append(Part(QUOTED if quoted else LIT, "$"))
            self.i = i + 1

    def _backtick(self, parts: list[Part]) -> None:
        end = self.text.find("`", self.i + 1)
        end = len(self.text) if end < 0 else end
        parts.append(Part(CMD, self.text[self.i + 1 : end]))
        self.i = end + 1

    def _double(self, parts: list[Part]) -> None:
        text = self.text
        self.i += 1
        buf: list[str] = []

        def flush() -> None:
            if buf:
                parts.append(Part(QUOTED, "".join(buf)))
                buf.clear()

        while self.i < len(text) and text[self.i] != '"':
            ch = text[self.i]
            if ch == "\\" and self.i + 1 < len(text):
                nxt = text[self.i + 1]
                buf.append(nxt if nxt in '$`"\\\n' else ch + nxt)
                self.i += 2
            elif ch == "$":
                flush()
                self._dollar(parts, quoted=True)
            elif ch == "`":
                flush()
                self._backtick(parts)
            else:
                buf.append(ch)
                self.i += 1
        flush()
        self.i += 1
        if not parts:
            parts.append(Part(QUOTED, ""))

    def word(self) -> Word:
        """Read one word starting at self.i (not at a separator or blank)."""
        text, start = self.text, self.i
        parts: list[Part] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                parts.append(Part(LIT, "".join(buf)))
                buf.clear()

        while self.i < len(text):
            ch = text[self.i]
            if ch in " \t\r" or ch in SEPARATORS:
                break
            if ch in REDIRECT_START and text[self.i + 1 : self.i + 2] != "(":
                break
            if ch in "<>\\'\"$`":
                flush()
                self._special(ch, parts)
            else:
                buf.append(ch)
                self.i += 1
        flush()
        return Word(parts, text[start : self.i])

    def _special(self, ch: str, parts: list[Part]) -> None:
        """One quoted, escaped or substituted piece of a word, starting at self.i."""
        text = self.text
        if ch in REDIRECT_START:  # process substitution <(…) / >(…)
            end = _closing(text, self.i + 2, "(", ")")
            parts.append(Part(PROC, text[self.i + 2 : end - 1]))
            self.i = end
        elif ch == "\\":
            if self.i + 1 < len(text) and text[self.i + 1] != "\n":
                parts.append(Part(QUOTED, text[self.i + 1]))
            self.i += 2
        elif ch == "'":
            end = text.find("'", self.i + 1)
            end = len(text) if end < 0 else end
            parts.append(Part(QUOTED, text[self.i + 1 : end]))
            self.i = end + 1
        elif ch == '"':
            self._double(parts)
        elif ch == "$":
            self._dollar(parts, quoted=False)
        else:
            self._backtick(parts)

    # ── commands ─────────────────────────────────────────────────────────────

    def _redirect(self) -> None:
        """At `<` or `>` (possibly after an fd number): read the operator and its target."""
        text = self.text
        match = re.compile(r"<<<|<<-|<<|<>|<&|<|>>|>&|>\||>").match(text, self.i)
        assert match is not None
        op = match.group()
        self.i = match.end()
        while self.i < len(text) and text[self.i] in " \t":
            self.i += 1
        if self.i >= len(text) or text[self.i] in SEPARATORS:
            return
        target = self.word()
        cmd = self.commands[-1]
        if op in ("<<", "<<-"):
            delim = "".join(p.text for p in target.parts)
            if any(p.kind in (VAR, CMD, PROC) for p in target.parts) or "$" in target.raw:
                self.unsure = True  # Bash takes such a delimiter literally
            expands = not any(p.kind == QUOTED for p in target.parts) and "\\" not in target.raw
            self.heredocs.append((cmd, delim, op == "<<-", expands))
        elif op == "<<<":
            cmd.stdin.append("".join(_respell(p) for p in target.parts))
            cmd.stdin_expands.append(True)
            cmd.in_targets.append(target)
        elif op.endswith("&") and re.fullmatch(r"\d+-?|-", target.raw):
            return  # fd duplication
        elif ">" in op:
            cmd.out_targets.append(target)
        else:
            cmd.in_targets.append(target)

    def _read_heredocs(self) -> None:
        """At a newline with pending here-docs: consume their bodies."""
        text = self.text
        for cmd, delim, strip, expands in self.heredocs:
            lines: list[str] = []
            while self.i < len(text):
                end = text.find("\n", self.i)
                end = len(text) if end < 0 else end
                line = text[self.i : end]
                self.i = end + 1
                if (line.lstrip("\t") if strip else line) == delim:
                    break
                if expands and line.endswith("\\"):
                    self.unsure = True  # Bash joins the lines before matching the delimiter
                lines.append(line)
            cmd.stdin.append("\n".join(lines))
            cmd.stdin_expands.append(expands)
        self.heredocs.clear()

    def run(self) -> list[Command]:
        text = self.text
        while self.i < len(text):
            ch = text[self.i]
            if ch in " \t\r":
                self.i += 1
            elif ch == "#" and (self.i == 0 or text[self.i - 1] in " \t\n;&|()"):
                end = text.find("\n", self.i)
                self.i = len(text) if end < 0 else end
            elif ch == "\n":
                self.i += 1
                self.commands.append(Command())
                if self.heredocs:
                    self._read_heredocs()
            elif ch in SEPARATORS:
                self.i += 1
                self.commands.append(Command())
            elif ch == "&" or (ch in REDIRECT_START and text[self.i + 1 : self.i + 2] != "("):
                self._redirect()
            else:
                fd = re.compile(r"\d+(?=[<>])|&(?=>)").match(text, self.i)
                if fd and text[fd.end() + 1 : fd.end() + 2] != "(":
                    self.i = fd.end()
                    self._redirect()
                else:
                    self.commands[-1].words.append(self.word())
        if self.heredocs:
            self._read_heredocs()
        return [c for c in self.commands if c.words or c.out_targets or c.in_targets]


def split(text: str) -> list[Command]:
    return Lexer(text).run()


def split_checked(text: str) -> tuple[list[Command], bool]:
    """The commands, and whether the lexer met a construct it may misread."""
    lexer = Lexer(text)
    return lexer.run(), lexer.unsure
