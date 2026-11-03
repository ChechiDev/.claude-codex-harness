#!/usr/bin/env python3
"""Stand-in for `systemctl show <unit> -p ...`. Optional overrides in SYSTEMD_JSON
(next to the wrapper): {"docker.socket": {"Listen": ...}, "docker.service": {...}}."""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULTS = {
    "docker.socket": {"Listen": "/run/docker.sock (Stream)", "ActiveState": "active"},
    "docker.service": {"ActiveState": "active", "MainPID": "1"},
}


def main() -> int:
    overrides_path = Path(sys.argv[1])
    args = sys.argv[2:]
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    if args[:1] != ["show"]:
        return 2
    unit = args[1]
    props = {**DEFAULTS.get(unit, {}), **overrides.get(unit, {})}
    wanted = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-p"]
    for key in wanted:
        print(f"{key}={props.get(key, '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
