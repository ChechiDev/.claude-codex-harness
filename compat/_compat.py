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
  preflight --image REF                  check (on the host) the local Engine and the image
  build-image --tag T --state DIR        build the image from compat/ (needs network once)
                                         and record its id in DIR/images.json
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
from typing import Any

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


DOCKER_USER_CONFIG = ("config.json", "contexts", "cli-plugins")


def _check_docker_config(path: Path) -> None:
    """A throwaway DOCKER_CONFIG holding no user configuration.

    config.json (credentials, proxies, default context), contexts/ (remote engines) and
    cli-plugins/ (executables) are refused; the CLI's own runtime state (buildx/,
    .token_seed) is harmless and accepted.
    """
    if not path.is_dir():
        raise CompatError(f"docker-config must be an existing directory: {path}")
    found = [name for name in DOCKER_USER_CONFIG if (path / name).exists()]
    if found:
        raise CompatError(f"docker-config holds user configuration ({', '.join(found)}): {path}")


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


def client_env(docker_config: Path, host_env: dict[str, str]) -> dict[str, str]:
    """Minimal env for the docker CLI: no DOCKER_HOST/CONTEXT/TLS, empty DOCKER_CONFIG."""
    env = {k: host_env[k] for k in CLIENT_ENV_KEYS if k in host_env}
    env["DOCKER_CONFIG"] = str(docker_config)
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
    env = client_env(spec.docker_config, dict(os.environ))
    out = {"argv": docker_argv(spec), "client_env": env}
    print(json.dumps(out, indent=2))


# ── host side: preflight and image build ─────────────────────────────────────

COMPAT_DIR = Path(__file__).resolve().parent
ENTRYPOINT = ["/usr/local/bin/compat-entrypoint"]
IMAGE_ENV_NAMES = {"PATH"}
TAG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _docker(docker_config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the docker CLI against the local socket only, with a minimal environment."""
    _check_docker_config(docker_config)
    return subprocess.run(
        ["docker", "--host", DOCKER_SOCKET, *args],
        env=client_env(docker_config, dict(os.environ)),
        text=True,
        capture_output=True,
        check=False,
    )


def _image_config(docker_config: Path, image: str) -> dict[str, Any]:
    res = _docker(docker_config, "image", "inspect", image)
    if res.returncode != 0:
        raise CompatError(f"image {image} not found: run build-image first")
    data: list[dict[str, Any]] = json.loads(res.stdout)
    config: dict[str, Any] = data[0].get("Config") or {}
    return config


def _check_image(config: dict[str, Any], image: str) -> None:
    names = {entry.split("=", 1)[0] for entry in config.get("Env") or []}
    if names != IMAGE_ENV_NAMES:
        raise CompatError(f"image {image} env drifted: {sorted(names)} (expected only PATH)")
    if (config.get("Labels") or {}).get(LABEL) != "1":
        raise CompatError(f"image {image} label {LABEL}=1 missing")
    if config.get("Entrypoint") != ENTRYPOINT:
        raise CompatError(f"image {image} entrypoint drifted: {config.get('Entrypoint')}")


def preflight(image: str, docker_config: Path) -> None:
    """Before any container: the local Engine answers and the image is the expected one."""
    info = _docker(docker_config, "info", "--format", "{{json .}}")
    if info.returncode != 0:
        raise CompatError(f"docker engine not reachable at {DOCKER_SOCKET}: {info.stderr.strip()}")
    if json.loads(info.stdout).get("OSType") != "linux":
        raise CompatError("docker engine is not a local linux engine")
    _check_image(_image_config(docker_config, image), image)


def _record_image(state: Path, image_id: str, tag: str) -> None:
    path = state / "images.json"
    records: list[dict[str, str]] = json.loads(path.read_text()) if path.exists() else []
    entry = {"id": image_id, "tag": tag}
    if entry not in records:
        records.append(entry)
    path.write_text(json.dumps(records, indent=2) + "\n")


def build_image(tag: str, state: Path, docker_config: Path) -> str:
    """Build compat/ into harness-compat:<tag>; record the image id for scoped cleanup."""
    if not TAG.match(tag):
        raise CompatError(f"invalid tag {tag!r}: use [a-z0-9._-], max 64")
    state.mkdir(parents=True, exist_ok=True)
    ref = f"{LABEL}:{tag}"
    iidfile = state / f"iid-{tag}"
    res = _docker(
        docker_config,
        "build", "--label", f"{LABEL}=1", "-t", ref, "--iidfile", str(iidfile), str(COMPAT_DIR),
    )  # fmt: skip
    if res.returncode != 0:
        raise CompatError(f"docker build failed:\n{res.stderr.strip()[-2000:]}")
    image_id = iidfile.read_text().strip()
    _record_image(state, image_id, ref)
    return image_id


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
    pf = sub.add_parser("preflight")
    pf.add_argument("--image", required=True)
    pf.add_argument("--docker-config", type=Path, required=True)
    bi = sub.add_parser("build-image")
    bi.add_argument("--tag", required=True)
    bi.add_argument("--state", type=Path, required=True)
    bi.add_argument("--docker-config", type=Path, required=True)
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
        elif args.cmd == "docker-cmd":
            _docker_cmd(args)
        elif args.cmd == "preflight":
            preflight(args.image, args.docker_config)
        else:
            print(build_image(args.tag, args.state, args.docker_config))
    except CompatError as exc:
        print(f"compat: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
