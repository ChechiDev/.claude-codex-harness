"""Write calls of a piece of Python code, found with `ast` (I3b, issue #32).

Pure: no I/O, no imports of the analysed code. `analyze(code)` returns every call that
may write a file, with its target and whether its mode writes, resolved by bounded
constant evaluation over the same code; None when the code does not parse.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass

RE_MODE = re.compile(r"[rwaxbtU+]{1,4}")

OS_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
PATH_TYPES = frozenset(
    {"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.PurePosixPath"}
)
# Path methods that always change the file (receiver of any type: distinctive names).
PATH_WRITES = frozenset({"write_text", "write_bytes", "touch", "unlink", "symlink_to"})
# Path methods whose names clash with str/other APIs: only on a known Path receiver.
PATH_WRITES_TYPED = frozenset({"mkdir", "chmod", "rmdir", "hardlink_to"})
PATH_MOVES = frozenset({"rename", "replace"})
OS_FIRST = frozenset(
    {"os.remove", "os.unlink", "os.chmod", "os.makedirs", "os.mkdir", "os.rmdir",
     "os.truncate", "os.removedirs", "os.chown", "os.lchmod", "os.mkfifo"}
)  # fmt: skip
OS_MOVES = frozenset({"os.rename", "os.replace", "os.renames"})
OS_SECOND = frozenset({"os.symlink", "os.link"})
SHUTIL_DEST = frozenset(
    {"shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree", "shutil.copymode",
     "shutil.copystat"}
)  # fmt: skip
OPENERS = frozenset({"open", "io.open", "builtins.open", "codecs.open"})


@dataclass(frozen=True)
class Write:
    op: str
    target: str | None  # None: could not be resolved
    writes: bool | None  # None: the mode could not be resolved
    destructive: bool = False


class _Unknown:
    pass


UNKNOWN = _Unknown()


class PathValue(str):
    """A resolved pathlib.Path value (kept as its string)."""


Value = str | int | _Unknown


class Analyzer(ast.NodeVisitor):
    def __init__(self, tree: ast.AST) -> None:
        self.bindings: dict[str, str] = {}
        self.consts: dict[str, Value] = {}
        self.writes: list[Write] = []
        self._collect(tree)

    # ── bindings and constants ───────────────────────────────────────────────

    def _collect(self, tree: ast.AST) -> None:
        assigned: dict[str, list[ast.expr | None]] = {}
        for node in ast.walk(tree):
            self._import(node)
            for name, value in _assignments(node):
                assigned.setdefault(name, []).append(value)
        # Bounded fixpoint: a name has a value only when every assignment agrees.
        for _ in range(4):
            for name, values in assigned.items():
                results = [UNKNOWN if v is None else self.eval(v) for v in values]
                first = results[0]
                same = all(type(r) is type(first) and r == first for r in results)
                self.consts[name] = first if same and not isinstance(first, _Unknown) else UNKNOWN
        for name in assigned:
            self.bindings.pop(name, None)

    def _import(self, node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                self.bindings[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                self.bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def qualname(self, node: ast.expr) -> str | None:
        """Dotted name of a callee through import bindings (`o.remove` -> os.remove)."""
        if isinstance(node, ast.Name):
            if node.id in self.consts:
                return None
            return self.bindings.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.qualname(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def eval(self, node: ast.expr) -> Value:
        """Bounded constant evaluation; UNKNOWN for anything else."""
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
                return value
            return UNKNOWN
        if isinstance(node, ast.Name):
            return self.consts.get(node.id, UNKNOWN)
        handler = {
            ast.JoinedStr: self._eval_fstring,
            ast.BinOp: self._eval_binop,
            ast.Attribute: self._eval_attribute,
            ast.Call: self._eval_call,
        }.get(type(node))
        return handler(node) if handler else UNKNOWN

    def _eval_fstring(self, node: ast.expr) -> Value:
        assert isinstance(node, ast.JoinedStr)
        pieces = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue) and value.format_spec is None:
                part = self.eval(value.value)
            else:
                part = self.eval(value) if isinstance(value, ast.Constant) else UNKNOWN
            if isinstance(part, _Unknown):
                return UNKNOWN
            pieces.append(str(part))
        return "".join(pieces)

    def _eval_binop(self, node: ast.expr) -> Value:
        assert isinstance(node, ast.BinOp)
        left, right = self.eval(node.left), self.eval(node.right)
        if isinstance(node.op, ast.Div) and isinstance(left, PathValue):
            return (
                UNKNOWN
                if isinstance(right, _Unknown)
                else PathValue(os.path.join(left, str(right)))
            )
        if isinstance(node.op, ast.Add) and isinstance(left, str) and isinstance(right, str):
            return UNKNOWN if isinstance(left, PathValue) else left + right
        if isinstance(node.op, ast.BitOr) and isinstance(left, int) and isinstance(right, int):
            return left | right
        return UNKNOWN

    def _eval_attribute(self, node: ast.expr) -> Value:
        assert isinstance(node, ast.Attribute)
        name = self.qualname(node)
        if name and name.startswith("os.O_"):
            value = getattr(os, name[3:], None)
            return value if isinstance(value, int) else UNKNOWN
        return UNKNOWN

    def _eval_call(self, node: ast.expr) -> Value:
        assert isinstance(node, ast.Call)
        name = self.qualname(node.func)
        if node.keywords:
            return UNKNOWN
        args = [self.eval(a) for a in node.args]
        if any(isinstance(a, _Unknown) or not isinstance(a, str) for a in args):
            return UNKNOWN
        strs = [str(a) for a in args]
        if name in PATH_TYPES:
            return PathValue(os.path.join(*strs) if strs else ".")
        if name == "os.path.join" and strs:
            return os.path.join(*strs)
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("joinpath",):
            base = self.eval(node.func.value)
            if isinstance(base, PathValue):
                return PathValue(os.path.join(base, *strs))
        return UNKNOWN

    # ── write calls ──────────────────────────────────────────────────────────

    def _target(self, node: ast.expr | None) -> str | None:
        if node is None:
            return None
        value = self.eval(node)
        return str(value) if isinstance(value, str) else None

    def _arg(self, call: ast.Call, pos: int, kw: str) -> ast.expr | None:
        for keyword in call.keywords:
            if keyword.arg == kw:
                return keyword.value
        return call.args[pos] if len(call.args) > pos else None

    def _mode_writes(self, node: ast.expr | None) -> bool | None:
        if node is None:
            return False
        mode = self.eval(node)
        if isinstance(mode, str):
            return any(ch in mode for ch in "wax+")
        return None

    def _flags_write(self, node: ast.expr | None) -> bool | None:
        if node is None:
            return None
        flags = self.eval(node)
        if isinstance(flags, int) and not isinstance(flags, str):
            return bool(flags & OS_WRITE_FLAGS)
        return None

    def _add(self, op: str, node: ast.expr | None, writes: bool | None = True,
             destructive: bool = False) -> None:  # fmt: skip
        self.writes.append(Write(op, self._target(node), writes, destructive))

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        name = self.qualname(node.func)
        if name in OPENERS:
            self._add(
                name, self._arg(node, 0, "file"), self._mode_writes(self._arg(node, 1, "mode"))
            )
        elif name == "os.open":
            self._add(
                name, self._arg(node, 0, "path"), self._flags_write(self._arg(node, 1, "flags"))
            )
        elif name in OS_FIRST:
            self._add(name, self._arg(node, 0, "path"), destructive=name != "os.chmod")
        elif name in OS_MOVES:
            self._add(name, self._arg(node, 0, "src"), destructive=True)
            self._add(name, self._arg(node, 1, "dst"))
        elif name in OS_SECOND or name in SHUTIL_DEST:
            self._add(name, self._arg(node, 1, "dst"))
        elif name == "shutil.move":
            self._add(name, self._arg(node, 0, "src"), destructive=True)
            self._add(name, self._arg(node, 1, "dst"))
        elif name == "shutil.rmtree":
            self._add(name, self._arg(node, 0, "path"), destructive=True)
        elif isinstance(node.func, ast.Attribute):
            self._method(node, node.func)

    def _method(self, node: ast.Call, func: ast.Attribute) -> None:
        receiver = self.eval(func.value)
        is_path = isinstance(receiver, PathValue) or (
            isinstance(func.value, ast.Call) and self.qualname(func.value.func) in PATH_TYPES
        )
        target = func.value if is_path else None
        attr = func.attr
        if attr == "open" and (is_path or node.args or node.keywords):
            mode = self._arg(node, 0, "mode")
            if not is_path:  # another `.open` (webbrowser, tarfile…): only a mode-shaped arg
                value = self.eval(mode) if mode is not None else ""
                if not (isinstance(value, str) and RE_MODE.fullmatch(value)):
                    return
            writes = self._mode_writes(mode)
            if writes is not False:
                self._add(f"Path.{attr}", target, writes)
        elif attr in PATH_WRITES or (is_path and attr in PATH_WRITES_TYPED):
            self._add(f"Path.{attr}", target, destructive=attr == "unlink")
        elif is_path and attr in PATH_MOVES:
            self._add(f"Path.{attr}", target, destructive=True)
            self._add(f"Path.{attr}", self._arg(node, 0, "target"))


def _assignments(node: ast.AST) -> list[tuple[str, ast.expr | None]]:
    """(name, value) for every binding `node` makes; None: not a constant value."""
    if isinstance(node, ast.Assign):
        return [
            (name, node.value if isinstance(tgt, ast.Name) else None)
            for tgt in node.targets
            for name in _names(tgt)
        ]
    if isinstance(node, ast.AugAssign | ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.value if isinstance(node, ast.AnnAssign) else None)]
    if isinstance(node, ast.NamedExpr):
        return [(node.target.id, node.value)]
    unknown: list[str] = []  # names bound to values the evaluator never knows
    if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
        unknown = _names(node.target)
    elif isinstance(node, ast.With | ast.AsyncWith):
        unknown = [n for item in node.items for n in _names(item.optional_vars)]
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        unknown = [a.arg for a in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs)]
    return [(name, None) for name in unknown]


def _names(node: ast.expr | None) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [n for elt in node.elts for n in _names(elt)]
    if isinstance(node, ast.Starred):
        return _names(node.value)
    return []


def analyze(code: str) -> list[Write] | None:
    """The write calls of `code`, or None when it does not parse."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    analyzer = Analyzer(tree)
    analyzer.visit(tree)
    return analyzer.writes
