"""C0 compatibility runner: isolated copies of real projects.

Toolkit-only tool: it lives outside lib/, so it is never linked into projects.

Subcommands:
  copy SRC DEST_DIR                      copy the whole git repo that contains SRC into
                                         DEST_DIR/<repo>; print the install root in the copy
  relink ROOT --toolkit DIR --mount PATH re-point the harness links of a COPY to PATH
  snapshot ROOT                          print every symlink under ROOT as JSON
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HARNESS_LINKS = ("harness", ".claude/agents", ".claude/codex", ".claude/settings.json")
SKILLS = ".claude/skills"
FORBIDDEN_TOP = ("catalog",)


class CompatError(Exception):
    """A precondition of the compatibility runner is not met."""


# ── copy ─────────────────────────────────────────────────────────────────────


def git_toplevel(path: Path) -> Path:
    res = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        raise CompatError(f"not inside a git repository: {path}")
    return Path(res.stdout.strip())


def copy_repo(install_root: Path, dest_dir: Path) -> Path:
    """Copy the whole repo (with .git, links kept as links); return the copied install root."""
    install_root = install_root.resolve()
    top = git_toplevel(install_root).resolve()
    target = dest_dir / top.name
    if target.exists():
        raise CompatError(f"destination exists: {target}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(top, target, symlinks=True)
    return target / install_root.relative_to(top)


# ── relink ───────────────────────────────────────────────────────────────────


def _toolkit_rel(link: Path, toolkit: Path) -> Path | None:
    """Target of link relative to the toolkit, or None if it points elsewhere."""
    raw = Path(os.readlink(link))
    target = Path(os.path.normpath(raw if raw.is_absolute() else link.parent / raw))
    for base in (toolkit, toolkit.resolve()):
        if target.is_relative_to(base):
            return target.relative_to(base)
    return None


def _plan_link(link: Path, name: str, toolkit: Path, mount: str) -> tuple[Path, str]:
    rel = _toolkit_rel(link, toolkit)
    if rel is None:
        raise CompatError(f"{name}: points outside the toolkit ({os.readlink(link)})")
    if rel.parts and rel.parts[0] in FORBIDDEN_TOP:
        raise CompatError(f"{name}: points into {rel.parts[0]}/, which is never linked")
    return link, f"{mount.rstrip('/')}/{rel.as_posix()}"


def _skill_links(root: Path) -> list[tuple[Path, str]]:
    skills = root / SKILLS
    if skills.is_symlink():
        return [(skills, SKILLS)]
    if not skills.is_dir():
        raise CompatError(f"{SKILLS}: missing")
    found = []
    for child in sorted(skills.iterdir()):
        name = f"{SKILLS}/{child.name}"
        if not child.is_symlink():
            raise CompatError(f"{name}: not a link")
        found.append((child, name))
    return found


def plan_relink(root: Path, toolkit: Path, mount: str) -> list[tuple[Path, str]]:
    """Validate every expected link first; nothing is changed if one is wrong."""
    links: list[tuple[Path, str]] = []
    for name in HARNESS_LINKS:
        link = root / name
        if not link.is_symlink():
            raise CompatError(f"{name}: missing link")
        links.append((link, name))
    links += _skill_links(root)
    return [_plan_link(link, name, toolkit, mount) for link, name in links]


def relink(root: Path, toolkit: Path, mount: str) -> None:
    for link, new_target in plan_relink(root, toolkit, mount):
        tmp = link.with_name(f".{link.name}.compat-tmp")
        tmp.symlink_to(new_target)
        os.replace(tmp, link)


# ── snapshot ─────────────────────────────────────────────────────────────────


def snapshot(root: Path) -> dict[str, str]:
    """Every symlink under root (not followed, .git skipped) -> its raw target."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                out[path.relative_to(root).as_posix()] = os.readlink(path)
    return dict(sorted(out.items()))


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="compat")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("copy")
    cp.add_argument("src", type=Path)
    cp.add_argument("dest_dir", type=Path)
    rl = sub.add_parser("relink")
    rl.add_argument("root", type=Path)
    rl.add_argument("--toolkit", type=Path, required=True)
    rl.add_argument("--mount", required=True)
    sn = sub.add_parser("snapshot")
    sn.add_argument("root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "copy":
            print(copy_repo(args.src, args.dest_dir))
        elif args.cmd == "relink":
            relink(args.root, args.toolkit, args.mount)
        else:
            print(json.dumps(snapshot(args.root), indent=2))
    except CompatError as exc:
        print(f"compat: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
