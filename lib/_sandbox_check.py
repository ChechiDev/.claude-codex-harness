"""Doctor check of Claude Code's native Bash sandbox (I3c, WARN phase).

Run by `make env-check` (harness.mk adds it as a prerequisite). It never fails:
every problem is a [WARN] with its fix, and the exit code is always 0. FAIL is
part 2 (docs/design/I3c-sandbox.md).

The sandbox is opt-in: the toolkit settings ship `sandbox.enabled: false` and a
project switches it on in .claude/settings.local.json. While the effective
`enabled` is not true the check prints nothing (make check runs env-check in
every project). A settings file it cannot parse counts as absent, as Claude
Code skips it (the guard reports a broken settings.local.json); once the
sandbox is on, it is a WARN. Managed settings are read from the managed file
only: an MDM or server-managed opt-in is not seen (docs/design/I3c-sandbox.md).

    python3 -B harness/_sandbox_check.py      # from the project root

Checks:
  1. Platform: the harness verifies the sandbox on Linux/WSL2 only (bubblewrap);
     anywhere else it is a permanent WARN.
  2. Host tools: bwrap and socat are on PATH and bwrap can start a namespace.
     Inside Claude Code's own sandbox (SANDBOX_RUNTIME=1) the probe is skipped.
  3. Effective settings (user < project < local < managed; arrays merge; a false
     allowUnsandboxedCommands in user or managed settings holds):
     allowUnsandboxedCommands is false, denyWrite
     still holds the harness-owned paths, filesystem isolation is not disabled,
     and no file adds excludedCommands or allowWrite entries beyond the
     toolkit's own (an exclusion runs that command unsandboxed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _status import Kind, say

REQUIRED_DENY = ("./progress", "./harness", "./.claude", "./tests/acceptance")
REQUIRED_TOOLS = ("bwrap", "socat")
INSTALL_HINT = "sudo apt-get install bubblewrap socat"
DESIGN = "docs/design/I3c-sandbox.md"
# The opt-in: the check reports only when its effective value is true.
ENABLED = "enabled"
# The scalars the barrier depends on once it is on, with the value it needs.
REQUIRED_SCALARS: tuple[tuple[str, bool], ...] = (("allowUnsandboxedCommands", False),)
TRACKED_SCALARS = (ENABLED, *(key for key, _ in REQUIRED_SCALARS))
STRICT_WINS = frozenset({"allowUnsandboxedCommands"})
MANAGED = {
    "linux": Path("/etc/claude-code/managed-settings.json"),
    "darwin": Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
}

# The toolkit's own settings, the baseline for the lists other files may widen.
TOOLKIT_SETTINGS = Path(__file__).resolve().parent.parent / "settings.json"
WIDENING = (("excludedCommands", ()), ("allowWrite", ("filesystem",)))

Finding = tuple[Kind, str]


@dataclass(frozen=True)
class Source:
    label: str  # the file, as the user would look for it
    sandbox: dict[str, Any]
    strict: bool = False  # user or managed: its false allowUnsandboxedCommands holds


@dataclass
class Sources:
    """Settings files in ascending precedence, plus the ones that could not be read."""

    files: list[Source] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Effective:
    scalars: dict[str, tuple[Any, str]]  # key -> (value, label of the file that set it)
    deny_write: frozenset[str]
    errors: tuple[str, ...]
    # list key -> {entry: label of the first file adding it}, beyond the toolkit's
    extras: dict[str, dict[str, str]] = field(default_factory=dict)
    fs_disabled: str | None = None  # label of the file that set filesystem.disabled


@dataclass(frozen=True)
class Host:
    platform: str
    tools: dict[str, str]  # name -> path, only the ones found
    bwrap_works: bool
    inside_sandbox: bool


def _read(path: Path, label: str, sources: Sources, strict: bool = False) -> None:
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sources.errors.append(f"{label}: unreadable ({exc.__class__.__name__})")
        return
    if not isinstance(data, dict):
        sources.errors.append(f"{label}: not a JSON object")
        return
    sandbox = data.get("sandbox", {})
    if not isinstance(sandbox, dict):
        sources.errors.append(f"{label}: 'sandbox' is not an object")
        return
    sources.files.append(Source(label, sandbox, strict))


def load_sources(project: Path, user: Path, managed: Path) -> Sources:
    """The files Claude Code merges for a session in `project`, lowest precedence first."""
    sources = Sources()
    _read(user, f"user settings ({user})", sources, strict=True)
    _read(project / ".claude/settings.json", ".claude/settings.json", sources)
    _read(project / ".claude/settings.local.json", ".claude/settings.local.json", sources)
    _read(managed, f"managed settings ({managed})", sources, strict=True)
    return sources


def _norm(path: str) -> str:
    path = path.rstrip("/") or path
    return path if path.startswith(("./", "/", "~/")) else f"./{path}"


def _list(sandbox: dict[str, Any], key: str, parents: tuple[str, ...]) -> list[str]:
    node: Any = sandbox
    for name in parents:
        node = node.get(name, {}) if isinstance(node, dict) else {}
    value = node.get(key, []) if isinstance(node, dict) else []
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def toolkit_baseline(path: Path = TOOLKIT_SETTINGS) -> dict[str, frozenset[str]]:
    """The toolkit's own excludedCommands / allowWrite (empty if unreadable)."""
    try:
        sandbox = json.loads(path.read_text(encoding="utf-8")).get("sandbox", {})
    except (OSError, ValueError, AttributeError):
        sandbox = {}
    if not isinstance(sandbox, dict):
        sandbox = {}
    return {key: frozenset(_list(sandbox, key, parents)) for key, parents in WIDENING}


def _extras(sources: Sources, baseline: dict[str, frozenset[str]]) -> dict[str, dict[str, str]]:
    extras: dict[str, dict[str, str]] = {}
    for key, parents in WIDENING:
        for src in sources.files:
            for entry in _list(src.sandbox, key, parents):
                if entry not in baseline.get(key, frozenset()):
                    extras.setdefault(key, {}).setdefault(entry, src.label)
    return extras


def effective(sources: Sources, baseline: dict[str, frozenset[str]] | None = None) -> Effective:
    scalars: dict[str, tuple[Any, str]] = {}
    deny: set[str] = set()
    fs_disabled = None
    for src in sources.files:
        fs_node = src.sandbox.get("filesystem", {})
        if isinstance(fs_node, dict) and fs_node.get("disabled") is True:
            fs_disabled = src.label
        for key in TRACKED_SCALARS:
            if key in src.sandbox:
                scalars[key] = (src.sandbox[key], src.label)
        fs = src.sandbox.get("filesystem", {})
        entries = fs.get("denyWrite", []) if isinstance(fs, dict) else []
        if isinstance(entries, list):
            deny |= {_norm(e) for e in entries if isinstance(e, str)}
    # Documented: a false allowUnsandboxedCommands in user or managed settings holds
    # even when a project file sets true. Any other mix: the last file wins.
    for src in sources.files:
        for key in STRICT_WINS:
            if src.strict and src.sandbox.get(key) is False:
                scalars[key] = (False, src.label)
    extras = _extras(sources, toolkit_baseline() if baseline is None else baseline)
    return Effective(scalars, frozenset(deny), tuple(sources.errors), extras, fs_disabled)


def _platform_findings(host: Host) -> list[Finding]:
    if host.platform.startswith("linux"):
        return []
    if host.platform == "darwin":
        return [
            (
                "WARN",
                "Bash sandbox: macOS uses Seatbelt; the harness verifies it only on Linux/WSL2 "
                f"(permanent WARN, see {DESIGN})",
            )
        ]
    return [
        (
            "WARN",
            f"Bash sandbox: not available on {host.platform} (Linux/WSL2 only): Bash runs "
            "unsandboxed and the guard is the only barrier (permanent WARN)",
        )
    ]


def _host_findings(host: Host) -> list[Finding]:
    if host.inside_sandbox:
        return [("OK", "Bash sandbox: running inside Claude Code's Bash sandbox")]
    missing = [t for t in REQUIRED_TOOLS if t not in host.tools]
    if missing:
        names = ", ".join("bubblewrap (bwrap)" if t == "bwrap" else t for t in missing)
        return [
            (
                "WARN",
                f"Bash sandbox: {names} not found: Claude Code runs Bash unsandboxed "
                f"(failIfUnavailable is false). Fix: {INSTALL_HINT}",
            )
        ]
    if not host.bwrap_works:
        return [
            (
                "WARN",
                "Bash sandbox: bwrap cannot create a namespace (unprivileged user namespaces "
                f"disabled? e.g. Ubuntu's AppArmor): Bash runs unsandboxed. See {DESIGN}",
            )
        ]
    return []


def _settings_findings(eff: Effective) -> list[Finding]:
    found: list[Finding] = [("WARN", f"Bash sandbox: {e}") for e in eff.errors]
    for key, wanted in REQUIRED_SCALARS:
        value, label = eff.scalars.get(key, (None, "no settings file"))
        if value is not wanted:
            found.append(
                (
                    "WARN",
                    f"Bash sandbox: sandbox.{key} is {json.dumps(value)} (from {label}), "
                    f"the harness needs {json.dumps(wanted)}. Fix: remove the override, or "
                    "re-link .claude/settings.json with install.sh <project>",
                )
            )
    if eff.fs_disabled:
        found.append(
            (
                "WARN",
                f"Bash sandbox: filesystem.disabled is true (from {eff.fs_disabled}): no "
                "write barrier at all. Fix: remove it",
            )
        )
    for key, entries in eff.extras.items():
        for entry, label in sorted(entries.items()):
            found.append(
                (
                    "WARN",
                    f"Bash sandbox: {key} adds {json.dumps(entry)} (from {label}), beyond "
                    "the toolkit's: "
                    + (
                        "that command runs unsandboxed"
                        if key == "excludedCommands"
                        else "sandboxed commands can write there"
                    )
                    + ". Fix: remove it unless the team agreed on it",
                )
            )
    missing = [p for p in REQUIRED_DENY if p not in eff.deny_write]
    if missing:
        found.append(
            (
                "WARN",
                f"Bash sandbox: filesystem.denyWrite lacks {' '.join(missing)}. Fix: re-link "
                ".claude/settings.json with install.sh <project>",
            )
        )
    return found


def is_on(eff: Effective) -> bool:
    """The effective sandbox.enabled is true (the project opted in)."""
    return eff.scalars.get(ENABLED, (None, ""))[0] is True


def findings(eff: Effective, host: Host) -> list[Finding]:
    """Every finding for this host and these settings; one OK line when all is well.
    Nothing at all while the sandbox is off."""
    if not is_on(eff):
        return []
    found = _platform_findings(host)
    if not found:
        found = _host_findings(host)
    found += _settings_findings(eff)
    if not any(kind == "WARN" for kind, _ in found):
        bwrap = host.tools.get("bwrap", "bwrap")
        detail = "" if host.inside_sandbox else f" (bwrap: {bwrap})"
        found.append(
            (
                "OK",
                f"Bash sandbox: on{detail}, denyWrite {' '.join(REQUIRED_DENY)}",
            )
        )
    return found


def _bwrap_works(bwrap: str) -> bool:
    probe = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"]
    try:
        res = subprocess.run(probe, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def detect_host() -> Host:
    tools = {t: p for t in REQUIRED_TOOLS if (p := shutil.which(t))}
    inside = os.environ.get("SANDBOX_RUNTIME") == "1"
    works = "bwrap" in tools and not inside and _bwrap_works(tools["bwrap"])
    return Host(sys.platform, tools, works, inside)


def _user_settings() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "settings.json"


def main() -> int:
    try:
        project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        managed = MANAGED.get(sys.platform, MANAGED["linux"])
        sources = load_sources(project, _user_settings(), managed)
        report: list[Finding] = findings(effective(sources), detect_host())
    except Exception as exc:  # a doctor check never breaks env-check
        report = [("WARN", f"Bash sandbox: the check could not run ({exc!r})")]
    for kind, message in report:
        say(kind, message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
