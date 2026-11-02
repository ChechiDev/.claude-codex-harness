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
  deps-cmd --phase init|fetch|install    print (JSON) one dependency-prep container
  prepare-lock INSTALL_ROOT OUT          copy ONLY pyproject.toml + uv.lock into OUT
  prepare-deps ...                       create the run's deps volume and run the three
                                         prep phases; print the volume name
  run --project P ... --baseline B --candidate C --origin O
                                         full compatibility run: baseline vs candidate on
                                         isolated copies; prints the JSON report; exit 1
                                         on any regression
  report RESULTS.json                    verdicts from raw baseline/candidate results
  cleanup --run-id ID                    remove ONLY containers/volumes labelled with ID
  purge-images --state DIR               remove ONLY recorded ids still labelled
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
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
    _clear_stop_cache(target)
    return target / install_root.relative_to(top)


def _clear_stop_cache(repo: Path) -> None:
    """The Stop hook's 'already green' marker must never carry over into a copy."""
    res = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "harness-check.ok"],
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        (repo / res.stdout.strip()).unlink(missing_ok=True)


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
# exec: projects compile and run probes in /tmp (Docker defaults tmpfs to noexec).
# /home as a whole: tools that ignore $HOME and use the uid's passwd home can write.
TMPFS_TMP = "/tmp:rw,exec,nosuid,nodev,size=512m"
TMPFS_HOME = "/home:rw,exec,nosuid,nodev,size=256m,mode=1777"
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
    session: str | None = None


def _run_labels(run_label: str) -> list[str]:
    """Every resource of a run carries these labels; cleanup filters on the second one."""
    _check_run_id(run_label)
    return ["--label", f"{LABEL}=1", "--label", f"{LABEL}.run={run_label}"]


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
        # The venv comes from the read-only deps volume; uv still writes to its cache on
        # start-up, so the cache is throwaway (tmpfs) and never the volume.
        env["UV_CACHE_DIR"] = "/tmp/uv-cache"
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
    argv += ["--tmpfs", TMPFS_TMP]
    argv += ["--tmpfs", TMPFS_HOME]
    argv += _run_labels(spec.session or spec.run_id)
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


# ── dependency prep (Python projects): init, fetch, install ──────────────────

PREP_ENTRYPOINT = "/usr/local/bin/compat-entrypoint-prep"
LOCK_FILES = ("pyproject.toml", "uv.lock")
FETCH_SYNC = [
    "uv", "sync", "--frozen", "--no-build",
    "--no-install-project", "--no-install-workspace", "--no-install-local",
]  # fmt: skip
INSTALL_SYNC = ["uv", "sync", "--frozen", "--offline"]
PHASES = ("init", "fetch", "install")


@dataclass(frozen=True)
class DepsSpec:
    phase: str
    run_id: str
    uid: int
    gid: int
    image: str
    docker_config: Path
    volume: str
    lock_dir: Path | None = None
    repo: Path | None = None
    workdir: str = "."
    session: str | None = None


def deps_volume(run_id: str) -> str:
    return f"{LABEL}-deps-{run_id}"


def _check_lock_dir(lock_dir: Path | None) -> Path:
    if lock_dir is None:
        raise CompatError("phase fetch needs --lock-dir")
    _check_mount_source(lock_dir)
    names = sorted(p.name for p in lock_dir.iterdir())
    if names != sorted(LOCK_FILES):
        raise CompatError(f"lock dir must hold only {', '.join(LOCK_FILES)}; found {names}")
    return lock_dir


def _deps_env(phase: str) -> dict[str, str]:
    env = {"LANG": "C.UTF-8", "HOME": HOME_IN}
    if phase != "init":
        env |= {
            "UV_CACHE_DIR": "/deps/cache",
            "UV_PROJECT_ENVIRONMENT": "/deps/venv",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    if phase == "install":
        env["UV_OFFLINE"] = "1"
    return env


def _deps_phase(spec: DepsSpec) -> tuple[list[str], list[str]]:
    """(phase-specific docker options, entrypoint args + command) of one prep phase."""
    volume = f"type=volume,src={spec.volume},dst=/deps"
    if spec.phase == "init":
        opts = ["--network", "none", "--user", "0:0", "--cap-add", "CHOWN", "--mount", volume]
        return opts, ["--rw", "/deps", "--", "chown", f"{spec.uid}:{spec.gid}", "/deps"]
    user = ["--user", f"{spec.uid}:{spec.gid}"]
    if spec.phase == "fetch":
        lock = _check_lock_dir(spec.lock_dir)
        mounts = ["--mount", f"type=bind,src={lock},dst=/lock,readonly", "--mount", volume]
        opts = ["--network", "bridge", *user, *mounts, "--workdir", "/lock"]
        return opts, ["--allow-network", "--rw", "/deps", "--", *FETCH_SYNC]
    if spec.repo is None:
        raise CompatError("phase install needs --repo")
    _check_mount_source(spec.repo)
    workdir = _check_workdir(spec.workdir)
    mounts = ["--mount", f"type=bind,src={spec.repo},dst=/work,readonly", "--mount", volume]
    opts = ["--network", "none", *user, *mounts, "--workdir", workdir]
    return opts, ["--rw", "/deps", "--", *INSTALL_SYNC]


def deps_argv(spec: DepsSpec) -> list[str]:
    """Validate, then build one dependency-prep container (never run here)."""
    if spec.phase not in PHASES:
        raise CompatError(f"unknown phase {spec.phase!r}: use {', '.join(PHASES)}")
    _check_run_id(spec.run_id)
    if spec.volume != deps_volume(spec.run_id):
        raise CompatError(f"deps volume must be the run's own: {deps_volume(spec.run_id)}")
    _check_docker_config(spec.docker_config)
    opts, tail = _deps_phase(spec)
    argv = ["docker", "--host", DOCKER_SOCKET, "run", "--rm", "--read-only"]
    argv += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "512"]
    argv += ["--tmpfs", TMPFS_TMP]
    argv += ["--tmpfs", TMPFS_HOME]
    argv += _run_labels(spec.session or spec.run_id)
    argv += ["--name", f"{LABEL}-{spec.run_id}-deps-{spec.phase}"]
    argv += ["--entrypoint", PREP_ENTRYPOINT, *opts]
    for key, value in _deps_env(spec.phase).items():
        argv += ["-e", f"{key}={value}"]
    return [*argv, spec.image, *tail]


def prepare_lock(install_root: Path, out: Path) -> bool:
    """Copy only pyproject.toml + uv.lock; False when the project has no Python deps."""
    if not (install_root / "pyproject.toml").is_file():
        return False
    if not (install_root / "uv.lock").is_file():
        raise CompatError(f"uv.lock missing in {install_root}: lock the project first")
    out.mkdir(parents=True)
    for name in LOCK_FILES:
        shutil.copyfile(install_root / name, out / name)
    return True


def _run_phase(spec: DepsSpec) -> None:
    res = subprocess.run(
        deps_argv(spec),
        env=client_env(spec.docker_config, dict(os.environ)),
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        tail = (res.stdout + res.stderr).strip()[-2000:]
        raise CompatError(f"deps phase {spec.phase} failed:\n{tail}")


def prepare_deps(base: DepsSpec, scratch: Path) -> str | None:
    """Create the run's volume and run init, fetch, install; None if no Python deps."""
    if base.repo is None:
        raise CompatError("prepare-deps needs --repo")
    lock_dir = scratch / f"lock-{base.run_id}"
    if not prepare_lock(base.repo / base.workdir, lock_dir):
        return None
    labels = _run_labels(base.session or base.run_id)
    res = _docker(base.docker_config, "volume", "create", *labels, base.volume)
    if res.returncode != 0:
        raise CompatError(f"cannot create volume {base.volume}: {res.stderr.strip()}")
    for phase in PHASES:
        _run_phase(_replace(base, phase=phase, lock_dir=lock_dir))
    return base.volume


def _replace(spec: DepsSpec, **changes: Any) -> DepsSpec:
    return DepsSpec(**{**spec.__dict__, **changes})


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


# ── report: baseline vs candidate ────────────────────────────────────────────

WARN_LINE = re.compile(r"\bWARN\b")
FAIL_LINE = re.compile(r"\[FAIL\]|\bFAIL(?:ED)?\b")
OUTPUT_KEEP = 20000


def _new_lines(base: str, cand: str, pattern: re.Pattern[str]) -> list[str]:
    seen = {ln.strip() for ln in base.splitlines() if pattern.search(ln)}
    out: list[str] = []
    for line in (ln.strip() for ln in cand.splitlines()):
        if pattern.search(line) and line not in seen and line not in out:
            out.append(line)
    return out


def _verdict(base_ok: bool, cand_ok: bool, new_failures: list[str]) -> str:
    if (base_ok and not cand_ok) or new_failures:
        return "regression"
    if not base_ok:
        return "fixed" if cand_ok else "still-failing"
    return "same"


def build_report(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    projects: dict[str, Any] = {}
    for name, sides in results.items():
        base, cand = sides["baseline"], sides["candidate"]
        new_failures = _new_lines(base["output"], cand["output"], FAIL_LINE)
        projects[name] = {
            "verdict": _verdict(base["exit"] == 0, cand["exit"] == 0, new_failures),
            "new_warnings": _new_lines(base["output"], cand["output"], WARN_LINE),
            "new_failures": new_failures,
            "baseline": base,
            "candidate": cand,
        }
    regressions = [n for n, p in projects.items() if p["verdict"] == "regression"]
    return {"projects": projects, "regressions": regressions}


# ── cleanup: only what a run created ─────────────────────────────────────────


def _ids(docker_config: Path, *args: str) -> list[str]:
    res = _docker(docker_config, *args)
    if res.returncode != 0:
        raise CompatError(f"docker {' '.join(args[:2])} failed: {res.stderr.strip()}")
    return res.stdout.split()


def cleanup(run_id: str, docker_config: Path) -> list[str]:
    """Remove the containers and volumes labelled with this run; never anything else."""
    _check_run_id(run_id)
    label = f"label={LABEL}.run={run_id}"
    removed: list[str] = []
    containers = _ids(docker_config, "ps", "-aq", "--filter", label)
    if containers:
        _ids(docker_config, "rm", "-f", *containers)
        removed += containers
    volumes = _ids(docker_config, "volume", "ls", "-q", "--filter", label)
    if volumes:
        _ids(docker_config, "volume", "rm", "-f", *volumes)
        removed += volumes
    return removed


def _purge_one(docker_config: Path, image_id: str) -> tuple[bool, str]:
    """(keep the record?, message) for one recorded image id."""
    res = _docker(docker_config, "image", "inspect", image_id)
    if res.returncode != 0:
        return False, f"{image_id}: not found (already removed)"
    config = (json.loads(res.stdout)[0].get("Config") or {}).get("Labels") or {}
    if config.get(LABEL) != "1":
        return True, f"{image_id}: not labelled {LABEL}=1, kept"
    rm = _docker(docker_config, "image", "rm", image_id)
    if rm.returncode != 0:
        return True, f"{image_id}: could not be removed: {rm.stderr.strip()}"
    return False, f"{image_id}: removed"


def purge_images(state: Path, docker_config: Path) -> list[str]:
    """Remove the image ids C0 recorded, only if they still carry the label."""
    path = state / "images.json"
    records: list[dict[str, str]] = json.loads(path.read_text()) if path.exists() else []
    kept: list[dict[str, str]] = []
    messages: list[str] = []
    for record in records:
        keep, message = _purge_one(docker_config, record["id"])
        messages.append(message)
        if keep:
            kept.append(record)
    if path.exists():
        path.write_text(json.dumps(kept, indent=2) + "\n")
    return messages


# ── run: the whole compatibility check ───────────────────────────────────────


@dataclass(frozen=True)
class RunPlan:
    projects: list[Path]
    baseline: Path
    candidate: Path
    origin: Path
    image: str
    docker_config: Path
    scratch: Path
    session: str


def _side(plan: RunPlan, index: int, project: Path, side: str) -> dict[str, Any]:
    """Copy, relink, prepare deps and run make check for one project on one side."""
    sub = f"{plan.session}-{index}{side[0]}"
    dest = plan.scratch / plan.session / sub
    install_root = copy_repo(project, dest)
    repo_top = dest / git_toplevel(project.resolve()).resolve().name
    workdir = install_root.relative_to(repo_top).as_posix() or "."
    relink(install_root, plan.origin, "/toolkit")
    uid, gid = os.getuid(), os.getgid()
    deps = DepsSpec(
        phase="init", run_id=sub, image=plan.image, docker_config=plan.docker_config,
        volume=deps_volume(sub), repo=repo_top, workdir=workdir, session=plan.session,
        uid=uid, gid=gid,
    )  # fmt: skip
    volume = prepare_deps(deps, dest)
    toolkit = plan.baseline if side == "baseline" else plan.candidate
    spec = RunSpec(
        repo=repo_top, workdir=workdir, toolkit=toolkit, image=plan.image, run_id=sub,
        docker_config=plan.docker_config, command=["make", "check"], deps_volume=volume,
        session=plan.session, uid=uid, gid=gid,
    )  # fmt: skip
    env = client_env(plan.docker_config, dict(os.environ))
    res = subprocess.run(
        docker_argv(spec), env=env, text=True, capture_output=True, check=False, timeout=1800
    )
    return {"exit": res.returncode, "output": (res.stdout + res.stderr)[-OUTPUT_KEEP:]}


def _run_project(plan: RunPlan, index: int, project: Path) -> dict[str, Any]:
    """Both sides start from ONE point-in-time snapshot of the project, so another
    session editing the real project during the run cannot create a fake delta."""
    try:
        snapshot = copy_repo(project, plan.scratch / plan.session / f"{index}-src")
    except CompatError as exc:
        failed = {"exit": 99, "output": f"[FAIL] compat snapshot: {exc}"}
        return {"baseline": failed, "candidate": failed}
    sides: dict[str, Any] = {}
    for side in ("baseline", "candidate"):
        try:
            sides[side] = _side(plan, index, snapshot, side)
        except CompatError as exc:
            sides[side] = {"exit": 99, "output": f"[FAIL] compat {side}: {exc}"}
    return sides


def run(plan: RunPlan) -> dict[str, Any]:
    """Baseline vs candidate on isolated copies; run-scoped cleanup always happens."""
    _check_run_id(plan.session)
    preflight(plan.image, plan.docker_config)
    results: dict[str, Any] = {}
    try:
        for index, project in enumerate(plan.projects):
            results[str(project)] = _run_project(plan, index, project)
    finally:
        cleanup(plan.session, plan.docker_config)
    report = build_report(results)
    session_dir = plan.scratch / plan.session
    if report["regressions"]:
        report["kept"] = [str(session_dir)]
    else:
        shutil.rmtree(session_dir, ignore_errors=True)
        report["kept"] = []
    report["run_id"] = plan.session
    return report


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
    for name in ("deps-cmd", "prepare-deps"):
        dp = sub.add_parser(name)
        if name == "deps-cmd":
            dp.add_argument("--phase", required=True)
            dp.add_argument("--volume", required=True)
            dp.add_argument("--lock-dir", type=Path)
        else:
            dp.add_argument("--scratch", type=Path, required=True)
        dp.add_argument("--repo", type=Path)
        dp.add_argument("--workdir", default=".")
        dp.add_argument("--run-id", required=True)
        dp.add_argument("--uid", type=int, required=True)
        dp.add_argument("--gid", type=int, required=True)
        dp.add_argument("--image", required=True)
        dp.add_argument("--docker-config", type=Path, required=True)
    _add_run_parsers(sub)
    return parser


def _add_run_parsers(sub: Any) -> None:
    rn = sub.add_parser("run")
    rn.add_argument("--project", type=Path, action="append", required=True)
    for name in ("--baseline", "--candidate", "--origin", "--scratch", "--docker-config"):
        rn.add_argument(name, type=Path, required=True)
    rn.add_argument("--image", required=True)
    rn.add_argument("--run-id")
    rp = sub.add_parser("report")
    rp.add_argument("results", type=Path)
    cl = sub.add_parser("cleanup")
    cl.add_argument("--run-id", required=True)
    cl.add_argument("--docker-config", type=Path, required=True)
    pi = sub.add_parser("purge-images")
    pi.add_argument("--state", type=Path, required=True)
    pi.add_argument("--docker-config", type=Path, required=True)
    pl = sub.add_parser("prepare-lock")
    pl.add_argument("install_root", type=Path)
    pl.add_argument("out", type=Path)


def _deps_spec(args: argparse.Namespace) -> DepsSpec:
    return DepsSpec(
        phase=getattr(args, "phase", "init"),
        run_id=args.run_id,
        uid=args.uid,
        gid=args.gid,
        image=args.image,
        docker_config=args.docker_config,
        volume=getattr(args, "volume", None) or deps_volume(args.run_id),
        lock_dir=getattr(args, "lock_dir", None),
        repo=args.repo,
        workdir=args.workdir,
    )


def _deps_main(args: argparse.Namespace) -> None:
    if args.cmd == "prepare-lock":
        if not prepare_lock(args.install_root, args.out):
            print("no python deps to prepare (no pyproject.toml)")
        return
    spec = _deps_spec(args)
    if args.cmd == "deps-cmd":
        env = client_env(spec.docker_config, dict(os.environ))
        print(json.dumps({"argv": deps_argv(spec), "client_env": env}, indent=2))
        return
    volume = prepare_deps(spec, args.scratch)
    print(volume if volume else "no python deps to prepare (no pyproject.toml)")


def _run_main(args: argparse.Namespace) -> int:
    if args.cmd == "report":
        report = build_report(json.loads(args.results.read_text()))
    elif args.cmd == "cleanup":
        for item in cleanup(args.run_id, args.docker_config):
            print(f"removed {item}")
        return 0
    elif args.cmd == "purge-images":
        print("\n".join(purge_images(args.state, args.docker_config)))
        return 0
    else:
        plan = RunPlan(
            projects=args.project, baseline=args.baseline, candidate=args.candidate,
            origin=args.origin, image=args.image, docker_config=args.docker_config,
            scratch=args.scratch, session=args.run_id or "c" + uuid.uuid4().hex[:10],
        )  # fmt: skip
        report = run(plan)
    print(json.dumps(report, indent=2))
    return 1 if report["regressions"] else 0


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
        elif args.cmd == "build-image":
            print(build_image(args.tag, args.state, args.docker_config))
        elif args.cmd in ("run", "report", "cleanup", "purge-images"):
            return _run_main(args)
        else:
            _deps_main(args)
    except CompatError as exc:
        print(f"compat: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
