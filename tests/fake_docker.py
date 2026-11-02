#!/usr/bin/env python3
"""A tiny stand-in for the docker CLI: the subset the compat runner uses.

Called as: fake_docker.py STATE_JSON <docker args...>
State: {"engine": "linux" | null, "images": {"<ref or id>": {inspect Config...}},
        "build_id": "sha256:...", "containers": {"<id>": {"<label>": "<value>"}},
        "volumes": {"<name>": {"<label>": "<value>"}}, "calls": [[...argv...]],
        "os": "Ubuntu 24.04" (docker info OperatingSystem), "run_exit": 0,
        "run_stdout": "", "create_drops_labels": false}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _info(state: dict[str, Any]) -> int:
    if not state.get("engine"):
        print("Cannot connect to the Docker daemon", file=sys.stderr)
        return 1
    info = {"OSType": state["engine"], "ServerVersion": "29.9.2"}
    info["OperatingSystem"] = state.get("os", "Ubuntu 24.04.4 LTS")
    print(json.dumps(info))
    return 0


def _inspect(state: dict[str, Any], ref: str) -> int:
    image = state.get("images", {}).get(ref)
    if image is None:
        print(f"Error: No such image: {ref}", file=sys.stderr)
        return 1
    print(json.dumps([{"Id": image.get("Id", "sha256:feed"), "Config": image["Config"]}]))
    return 0


def _build(state: dict[str, Any], args: list[str]) -> int:
    iidfile = Path(args[args.index("--iidfile") + 1])
    iidfile.write_text(state.get("build_id", "sha256:b1d"))
    return 0


def _matches(labels: dict[str, str], args: list[str]) -> bool:
    for i, arg in enumerate(args):
        if arg == "--filter" and args[i + 1].startswith("label="):
            key, _, value = args[i + 1][len("label=") :].partition("=")
            if labels.get(key) != value:
                return False
    return True


def _list(items: dict[str, dict[str, str]], args: list[str]) -> int:
    for name, labels in items.items():
        if _matches(labels, args):
            print(name)
    return 0


def _remove(items: dict[str, Any], names: list[str]) -> int:
    for name in names:
        items.pop(name, None)
    return 0


def _labels_of(args: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, arg in enumerate(args[:-1]):
        if arg == "--label":
            key, _, value = args[i + 1].partition("=")
            out[key] = value
    return out


def _inspect_named(items: dict[str, dict[str, str]], name: str, kind: str) -> int:
    if name not in items:
        print(f"Error: No such {kind}: {name}", file=sys.stderr)
        return 1
    print(json.dumps([{"Name": name, "Labels": items[name], "Config": {"Labels": items[name]}}]))
    return 0


def _volume_create(state: dict[str, Any], args: list[str]) -> int:
    name = args[-1]
    labels = {} if state.get("create_drops_labels") else _labels_of(args)
    state["volumes"].setdefault(name, labels)  # like docker: existing volume is reused
    print(name)
    return 0


def _run(state: dict[str, Any]) -> int:
    print(state.get("run_stdout", ""), end="")
    return int(state.get("run_exit", 0))


def _named(state: dict[str, Any], args: list[str]) -> int | None:
    if args[:2] == ["volume", "create"]:
        return _volume_create(state, args)
    if args[:2] == ["volume", "inspect"]:
        return _inspect_named(state["volumes"], args[2], "volume")
    if args[:2] == ["container", "inspect"]:
        return _inspect_named(state["containers"], args[2], "container")
    if args[:1] == ["run"]:
        return _run(state)
    if args[:2] == ["image", "rm"]:
        return _remove(state.setdefault("images", {}), args[2:])
    return None


def _resources(state: dict[str, Any], args: list[str]) -> int | None:
    containers = state.setdefault("containers", {})
    volumes = state.setdefault("volumes", {})
    if (named := _named(state, args)) is not None:
        return named
    if args[:1] == ["ps"]:
        return _list(containers, args)
    if args[:1] == ["rm"]:
        return _remove(containers, [a for a in args[1:] if not a.startswith("-")])
    if args[:2] == ["volume", "ls"]:
        return _list(volumes, args)
    if args[:2] == ["volume", "rm"]:
        return _remove(volumes, [a for a in args[2:] if not a.startswith("-")])
    return None


def main() -> int:
    state_path = Path(sys.argv[1])
    args = sys.argv[2:]
    state: dict[str, Any] = json.loads(state_path.read_text())
    state.setdefault("calls", []).append(args)
    if args[:2] == ["--host", "unix:///var/run/docker.sock"]:
        args = args[2:]
    handled = _resources(state, args)
    state_path.write_text(json.dumps(state))
    if handled is not None:
        return handled
    if args[:1] == ["info"]:
        return _info(state)
    if args[:2] == ["image", "inspect"]:
        return _inspect(state, args[2])
    if args[:1] == ["build"]:
        return _build(state, args)
    print(f"fake docker: unsupported {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
