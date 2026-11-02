"""C0 compatibility runner: isolated copies of real projects.

Toolkit-only tool: it lives outside lib/, so it is never linked into projects.

Subcommands:
  copy SRC DEST_DIR                      copy the whole git repo that contains SRC into
                                         DEST_DIR/<repo>; print the install root in the copy
  relink ROOT --toolkit DIR --mount PATH re-point the harness links of a COPY to PATH
  snapshot ROOT                          print every symlink under ROOT as JSON
  docker-cmd ... -- CMD...               print (JSON) the locked-down docker invocation
                                         of a check container and its client env;
                                         never runs docker
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
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


# ── docker command (built, never executed here) ──────────────────────────────

DOCKER_SOCKET = "unix:///var/run/docker.sock"
SOCKET_PATHS = ("/var/run/docker.sock", "/run/docker.sock")
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
LABEL = "harness-compat"
HOME_IN = "/home/compat"
CLIENT_ENV_KEYS = ("PATH",)


@dataclass(frozen=True)
class RunSpec:
    repo: Path
    workdir: str
    toolkit: Path
    image: str
    run_id: str
    uid: int
    gid: int
    docker_config: Path
    command: list[str]
    deps_volume: str | None = None


def _check_run_id(run_id: str) -> None:
    if not RUN_ID.match(run_id):
        raise CompatError(f"invalid run-id {run_id!r}: use [a-z0-9-], max 32, no leading dash")


def _check_mount_source(path: Path) -> None:
    """Refuse host paths that would expose the host: root, HOME (or above), ~/.claude, sockets."""
    real = Path(os.path.realpath(path))
    home = Path(os.path.realpath(Path.home()))
    claude = home / ".claude"
    if str(path) in SOCKET_PATHS or str(real) in SOCKET_PATHS:
        raise CompatError(f"refusing to mount the docker socket: {path}")
    if real == Path("/") or home.is_relative_to(real) or real.is_relative_to(claude):
        raise CompatError(f"refusing to mount a host path that exposes the host: {path}")
    if not real.is_dir():
        raise CompatError(f"mount source must be an existing directory: {path}")


def _check_workdir(workdir: str) -> str:
    rel = Path(workdir)
    if rel.is_absolute() or ".." in rel.parts:
        raise CompatError(f"workdir must be relative and stay inside the copy: {workdir}")
    return "/work" if workdir in ("", ".") else f"/work/{rel.as_posix()}"


def _check_docker_config(path: Path) -> None:
    if not path.is_dir() or any(path.iterdir()):
        raise CompatError(f"docker-config must be an existing EMPTY directory: {path}")


def container_env(spec: RunSpec) -> dict[str, str]:
    env = {
        "LANG": "C.UTF-8",
        "HOME": HOME_IN,
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_OFFLINE": "1",
        "UV_NO_SYNC": "1",
    }
    if spec.deps_volume:
        env["UV_CACHE_DIR"] = "/deps/cache"
        env["UV_PROJECT_ENVIRONMENT"] = "/deps/venv"
    return env


def _mount_args(spec: RunSpec) -> list[str]:
    mounts = [
        f"type=bind,src={spec.repo},dst=/work",
        f"type=bind,src={spec.toolkit},dst=/toolkit,readonly",
    ]
    if spec.deps_volume:
        mounts.append(f"type=volume,src={spec.deps_volume},dst=/deps,readonly")
    return [arg for m in mounts for arg in ("--mount", m)]


def docker_argv(spec: RunSpec) -> list[str]:
    """Validate the spec, then build the full docker invocation for the check container."""
    _check_run_id(spec.run_id)
    for source in (spec.repo, spec.toolkit):
        _check_mount_source(source)
    workdir = _check_workdir(spec.workdir)
    _check_docker_config(spec.docker_config)
    if not spec.command:
        raise CompatError("a command to run in the container is required (after --)")
    argv = ["docker", "--host", DOCKER_SOCKET, "run", "--rm", "--read-only"]
    argv += ["--network", "none", "--cap-drop", "ALL"]
    argv += ["--security-opt", "no-new-privileges", "--pids-limit", "512"]
    argv += ["--user", f"{spec.uid}:{spec.gid}"]
    argv += ["--tmpfs", "/tmp:rw,nosuid,nodev,size=512m"]
    argv += ["--tmpfs", f"{HOME_IN}:rw,nosuid,nodev,size=256m"]
    argv += ["--label", f"{LABEL}=1", "--label", f"{LABEL}.run={spec.run_id}"]
    argv += ["--name", f"{LABEL}-{spec.run_id}-check"]
    argv += _mount_args(spec) + ["--workdir", workdir]
    for key, value in container_env(spec).items():
        argv += ["-e", f"{key}={value}"]
    return [*argv, spec.image, *spec.command]


def client_env(spec: RunSpec, host_env: dict[str, str]) -> dict[str, str]:
    """Minimal env for the docker CLI: no DOCKER_HOST/CONTEXT/TLS, empty DOCKER_CONFIG."""
    env = {k: host_env[k] for k in CLIENT_ENV_KEYS if k in host_env}
    env["DOCKER_CONFIG"] = str(spec.docker_config)
    return env


def _spec_from_args(args: argparse.Namespace) -> RunSpec:
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    return RunSpec(
        repo=args.repo,
        workdir=args.workdir,
        toolkit=args.toolkit,
        image=args.image,
        run_id=args.run_id,
        uid=args.uid,
        gid=args.gid,
        docker_config=args.docker_config,
        command=command,
        deps_volume=args.deps_volume,
    )


def _docker_cmd(args: argparse.Namespace) -> None:
    spec = _spec_from_args(args)
    out = {"argv": docker_argv(spec), "client_env": client_env(spec, dict(os.environ))}
    print(json.dumps(out, indent=2))


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
    dk = sub.add_parser("docker-cmd")
    dk.add_argument("--repo", type=Path, required=True)
    dk.add_argument("--workdir", required=True)
    dk.add_argument("--toolkit", type=Path, required=True)
    dk.add_argument("--image", required=True)
    dk.add_argument("--run-id", required=True)
    dk.add_argument("--uid", type=int, required=True)
    dk.add_argument("--gid", type=int, required=True)
    dk.add_argument("--docker-config", type=Path, required=True)
    dk.add_argument("--deps-volume")
    dk.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "copy":
            print(copy_repo(args.src, args.dest_dir))
        elif args.cmd == "relink":
            relink(args.root, args.toolkit, args.mount)
        elif args.cmd == "snapshot":
            print(json.dumps(snapshot(args.root), indent=2))
        else:
            _docker_cmd(args)
    except CompatError as exc:
        print(f"compat: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
