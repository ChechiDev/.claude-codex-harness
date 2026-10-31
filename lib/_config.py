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

Placeholders: {task} (1.1), {slug} (the task title in kebab-case), {ticket} (the
task's `- ticket:` line in its phase file). Every section and key is optional; a
project without the file behaves exactly like one with no [git] section.
"""

from __future__ import annotations

import re
import string
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_FILE = "harness.toml"
DEFAULT_BRANCH = "feat/{task}-{slug}"
DEFAULT_SETUP_BRANCH = "chore/project-setup"
PLACEHOLDERS = frozenset({"task", "slug", "ticket"})
# What a rendered branch may contain: a conservative subset of git ref names.
RE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
RE_TICKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


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
    return Config(src_dirs=_src_dirs(scope), git_configured=git is not None, **git_values)
