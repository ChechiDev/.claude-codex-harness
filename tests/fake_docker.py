#!/usr/bin/env python3
"""A tiny stand-in for the docker CLI: the subset the compat runner uses.

Called as: fake_docker.py STATE_JSON <docker args...>
State: {"engine": "linux" | null, "images": {"<ref or id>": {inspect Config...}},
        "build_id": "sha256:...", "containers": {"<id>": {"<label>": "<value>"}},
        "volumes": {"<name>": {"<label>": "<value>"}}, "calls": [[...argv...]]}
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
    print(json.dumps({"OSType": state["engine"], "ServerVersion": "29.9.2"}))
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


def _remove(items: dict[str, dict[str, str]], names: list[str]) -> int:
    for name in names:
        items.pop(name, None)
    return 0


def _resources(state: dict[str, Any], args: list[str]) -> int | None:
    containers = state.setdefault("containers", {})
    volumes = state.setdefault("volumes", {})
    if args[:1] == ["ps"]:
        return _list(containers, args)
    if args[:1] == ["rm"]:
        return _remove(containers, [a for a in args[1:] if not a.startswith("-")])
    if args[:2] == ["volume", "ls"]:
        return _list(volumes, args)
    if args[:2] == ["volume", "rm"]:
        return _remove(volumes, [a for a in args[2:] if not a.startswith("-")])
    if args[:2] == ["image", "rm"]:
        for ref in args[2:]:
            state.get("images", {}).pop(ref, None)
        return 0
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
