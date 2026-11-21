#!/usr/bin/env python3
"""Project config: harness.toml at the project root. Human-owned; Claude never edits it.

  [scope]
  src_dirs = ["app", "scripts"]          production-code dirs (default: any src/ or include/)

  [git]
  branch = "feature/{ticket}-{slug}"     task branch (default: feat/{task}-{slug})
  commit_prefix = "{ticket} "            prefix of the close commit subject (default: none)
  setup_branch = "chore/project-setup"   branch that must reach main before the first subtask
  auto_pr = true                         push and open the task PR when its last subtask closes
  pr_template = ".github/pr.md"          PR body template (default: the harness's pr_template.md)

  [review]
  max_rounds = 3                         consecutive CHANGES_REQUESTED rounds of one gate on
                                         one id before the gate stops and needs the human
                                         (default 3; 0 = no cap). No env override, by design.

  [codex]
  mode = "auto"                          auto (runs codex) | manual (prints the command)
  sandbox = "workspace-write"            workspace-write | read-only | danger-full-access
  network = false                        allow network inside workspace-write

  [pr]
  auto = true                            false: no push/PR at the last close (with [git] auto_pr)
  gh = "gh"                              the GitHub CLI the gates use

  [sessions]                             parallel Claude sessions (harness/session_launch.sh)
  model = "sonnet"                       model of a session that executes a task/subtask
  effort = "medium"                      its effort: low | medium | high | xhigh | max
  high_risk_model = "opus"               model when the task's files: match high_risk_paths
  high_risk_effort = "high"              effort in that case
  high_risk_paths = ["migrations/**"]    project-relative globs (* within a dir, ** across);
                                         only * ? ** (no [..] {..}); default none. Unknown
                                         keys fail closed, so a typo
                                         cannot silently drop the escalation.

The gates ignore the old HARNESS_CODEX_* / HARNESS_GH / HARNESS_AUTO_PR environment
overrides (issue #90): these values live here, where Claude cannot change them.

Placeholders: {task} (1.1), {slug} (the task title in kebab-case), {ticket} (the
task's `- ticket:` line in its phase file). Every section and key is optional; a
project without the file behaves exactly like one with no [git] section.
"""

from __future__ import annotations

import re
import string
import sys
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_FILE = "harness.toml"
DEFAULT_BRANCH = "feat/{task}-{slug}"
DEFAULT_SETUP_BRANCH = "chore/project-setup"
DEFAULT_MAX_ROUNDS = 3
CODEX_MODES = ("auto", "manual")
CODEX_SANDBOXES = ("workspace-write", "read-only", "danger-full-access")
PLACEHOLDERS = frozenset({"task", "slug", "ticket"})
# What a rendered branch may contain: a conservative subset of git ref names.
RE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
RE_TICKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# A model alias or id (sonnet, claude-opus-5-5, opus[1m]): no spaces, no leading dash.
RE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9]+\])?$")
SESSION_EFFORTS = ("low", "medium", "high", "xhigh", "max")
SESSION_KEYS = frozenset(
    {"model", "effort", "high_risk_model", "high_risk_effort", "high_risk_paths"}
)


class ConfigError(Exception):
    """harness.toml exists but cannot be trusted: the harness fails closed."""


@dataclass(frozen=True)
class Config:
    src_dirs: tuple[str, ...] | None = None
    branch: str = DEFAULT_BRANCH
    commit_prefix: str = ""
    setup_branch: str = DEFAULT_SETUP_BRANCH
    auto_pr: bool = True
    pr_template: str = ""  # empty: the harness's default template
    git_configured: bool = False  # a [git] section: the task branch is enforced
    max_rounds: int = DEFAULT_MAX_ROUNDS  # review round cap per gate and id; 0 = off
    codex_mode: str = "auto"
    codex_sandbox: str = "workspace-write"
    codex_network: bool = False
    gh: str = "gh"
    session_model: str = "sonnet"
    session_effort: str = "medium"
    session_high_risk_model: str = "opus"
    session_high_risk_effort: str = "high"
    session_high_risk_paths: tuple[str, ...] = ()

    @property
    def needs_ticket(self) -> bool:
        return "{ticket}" in self.branch or "{ticket}" in self.commit_prefix


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def issue_slug(title: str) -> str:
    """The slug of an issue's branch and header file (a title with no letter or digit
    still gets a valid one). The single source for _harness and _issue."""
    return slugify(title) or "issue"


def render(pattern: str, task: str, title: str, ticket: str) -> str:
    return pattern.format(task=task, slug=slugify(title), ticket=ticket)


def valid_branch(name: str) -> bool:
    return bool(RE_BRANCH.match(name)) and ".." not in name and not name.endswith((".", "/"))


def _placeholders(pattern: str, key: str) -> None:
    try:
        names = {field for _, field, _, _ in string.Formatter().parse(pattern) if field is not None}
    except ValueError as exc:
        raise ConfigError(f"{CONFIG_FILE}: [git] {key} is not a valid pattern: {exc}") from exc
    unknown = names - PLACEHOLDERS
    if unknown:
        raise ConfigError(f"{CONFIG_FILE}: [git] {key} uses unknown {sorted(unknown)}")


def _string(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{CONFIG_FILE}: [git] {key} must be a string")
    return value


def _pr(section: dict[str, Any]) -> dict[str, Any]:
    auto_pr = section.get("auto_pr", True)
    if not isinstance(auto_pr, bool):
        raise ConfigError(f"{CONFIG_FILE}: [git] auto_pr must be true or false")
    return {"auto_pr": auto_pr, "pr_template": _string(section, "pr_template", "")}


def _git(section: dict[str, Any]) -> dict[str, str]:
    branch = _string(section, "branch", DEFAULT_BRANCH)
    prefix = _string(section, "commit_prefix", "")
    setup = _string(section, "setup_branch", DEFAULT_SETUP_BRANCH)
    _placeholders(branch, "branch")
    _placeholders(prefix, "commit_prefix")
    if not valid_branch(render(branch, "1.1", "sample task", "T-1")):
        raise ConfigError(f"{CONFIG_FILE}: [git] branch does not render a valid branch name")
    if "\n" in prefix or "\r" in prefix:
        raise ConfigError(f"{CONFIG_FILE}: [git] commit_prefix must be one line")
    if not valid_branch(setup):
        raise ConfigError(f"{CONFIG_FILE}: [git] setup_branch is not a valid branch name")
    return {"branch": branch, "commit_prefix": prefix, "setup_branch": setup}


def _src_dirs(section: dict[str, Any]) -> tuple[str, ...] | None:
    dirs = section.get("src_dirs")
    if dirs is None:
        return None
    if (
        not isinstance(dirs, list)
        or not dirs
        or not all(isinstance(d, str) and d.strip("/") for d in dirs)
    ):
        raise ConfigError(f"{CONFIG_FILE}: [scope] src_dirs must be a non-empty list of dirs")
    return tuple("/".join(p for p in Path(d.strip("/")).parts if p != ".") for d in dirs)


def _max_rounds(section: dict[str, Any]) -> int:
    value = section.get("max_rounds", DEFAULT_MAX_ROUNDS)
    # bool is an int subclass: `max_rounds = true` is a mistake, not 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{CONFIG_FILE}: [review] max_rounds must be an integer >= 0")
    return value


def _choice(section: dict[str, Any], name: str, key: str, allowed: tuple[str, ...]) -> str:
    value = section.get(key, allowed[0])
    if value not in allowed:
        raise ConfigError(f"{CONFIG_FILE}: [{name}] {key} must be one of {', '.join(allowed)}")
    return str(value)


def _flag(section: dict[str, Any], name: str, key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{CONFIG_FILE}: [{name}] {key} must be true or false")
    return value


def _codex(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "codex_mode": _choice(section, "codex", "mode", CODEX_MODES),
        "codex_sandbox": _choice(section, "codex", "sandbox", CODEX_SANDBOXES),
        "codex_network": _flag(section, "codex", "network", False),
    }


def _gh(section: dict[str, Any]) -> str:
    gh = section.get("gh", "gh")
    if not isinstance(gh, str) or not gh or any(c in gh for c in "\t\n\r\0"):
        raise ConfigError(f"{CONFIG_FILE}: [pr] gh must be a non-empty one-line path")
    return gh


def _session_model(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or len(value) > 100 or not RE_MODEL.fullmatch(value):
        raise ConfigError(f"{CONFIG_FILE}: [sessions] {key} must be a model alias or id")
    return value


def _session_effort(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if value not in SESSION_EFFORTS:
        raise ConfigError(
            f"{CONFIG_FILE}: [sessions] {key} must be one of {', '.join(SESSION_EFFORTS)}"
        )
    return str(value)


def _risk_glob(glob: object) -> str | None:
    """The glob without `.` segments, or None if invalid. Only `*`, `?` and `**` are
    glob syntax: `[...]`/`{...}` would match literally, so they are refused."""
    if not isinstance(glob, str) or glob.startswith("/"):
        return None
    if any(c in "\\[]{}" or unicodedata.category(c).startswith("C") for c in glob):
        return None
    parts = [p for p in glob.split("/") if p not in ("", ".")]
    return "/".join(parts) if parts and ".." not in parts else None


def _sessions(section: dict[str, Any]) -> dict[str, Any]:
    unknown = set(section) - SESSION_KEYS
    if unknown:
        raise ConfigError(f"{CONFIG_FILE}: [sessions] unknown key(s) {sorted(unknown)}")
    raw = section.get("high_risk_paths", [])
    paths = [_risk_glob(g) for g in raw] if isinstance(raw, list) else [None]
    if None in paths:
        raise ConfigError(
            f"{CONFIG_FILE}: [sessions] high_risk_paths must be a list of project-relative "
            "globs (* ? ** only)"
        )
    return {
        "session_model": _session_model(section, "model", "sonnet"),
        "session_effort": _session_effort(section, "effort", "medium"),
        "session_high_risk_model": _session_model(section, "high_risk_model", "opus"),
        "session_high_risk_effort": _session_effort(section, "high_risk_effort", "high"),
        "session_high_risk_paths": tuple(p for p in paths if p is not None),
    }


def _section(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    section = data.get(name)
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError(f"{CONFIG_FILE}: [{name}] must be a table")
    return section


def load(root: Path) -> Config:
    """The project's config; defaults when the file is absent. Raises ConfigError."""
    path = root / CONFIG_FILE
    if not path.is_file():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{CONFIG_FILE} is unreadable: {exc}") from exc
    scope = _section(data, "scope") or {}
    git = _section(data, "git")
    git_values: dict[str, Any] = {} if git is None else {**_git(git), **_pr(git)}
    review = _section(data, "review") or {}
    pr = _section(data, "pr") or {}
    if not _flag(pr, "pr", "auto", True):
        git_values["auto_pr"] = False
    return Config(
        src_dirs=_src_dirs(scope),
        git_configured=git is not None,
        max_rounds=_max_rounds(review),
        gh=_gh(pr),
        **_codex(_section(data, "codex") or {}),
        **_sessions(_section(data, "sessions") or {}),
        **git_values,
    )


def control(root: Path) -> str:
    """The gate controls, one tab-separated line for review_with_codex.sh:
    mode, sandbox, network (0/1), auto PR (0/1), gh."""
    cfg = load(root)
    fields = (
        cfg.codex_mode,
        cfg.codex_sandbox,
        str(int(cfg.codex_network)),
        str(int(cfg.auto_pr)),
        cfg.gh,
    )
    return "\t".join(fields)


def main(argv: list[str]) -> int:
    if argv != ["control"]:
        print("usage: _config.py control", file=sys.stderr)
        return 2
    try:
        print(control(Path.cwd()))
    except ConfigError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
