#!/usr/bin/env python3
"""A tiny stand-in for the docker CLI: the subset the compat runner uses.

Called as: fake_docker.py STATE_JSON <docker args...>
State: {"engine": "linux" | null, "images": {"<ref>": {inspect Config...}},
        "build_id": "sha256:...", "calls": [[...argv...]]}
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


def main() -> int:
    state_path = Path(sys.argv[1])
    args = sys.argv[2:]
    state: dict[str, Any] = json.loads(state_path.read_text())
    state.setdefault("calls", []).append(args)
    state_path.write_text(json.dumps(state))
    if args[:2] == ["--host", "unix:///var/run/docker.sock"]:
        args = args[2:]
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
