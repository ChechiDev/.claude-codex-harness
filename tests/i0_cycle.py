"""I0 helpers: drive a whole task cycle on an installed throwaway project.

Codex and GitHub are replaced by fakes on PATH:
  codex  reads the gate prompt (Phase: / Feature: id=) and writes what Codex would:
         verdicts, the acceptance test (plan gate), and at the close the reference doc,
         the done status, the commit message and the PR narrative.
  gh     logs its calls; `pr view` fails (no PR yet), `pr create` keeps the body.
origin is a bare repo in the temp dir, so push never leaves the machine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from i0_projects import Installed

SID = "1.1.1"
TASK = "1.1"

PLAN_MD = "# Project plan\n\n## Vision\nA tiny greeting library.\n"
PHASE_MD = (
    "# Phase 01 — Greetings\n- goal: say hello.\n\n"
    "## 1.1 Greeting\n- layer: domain\n- goal: a greet function.\n"
)


def spec(files: list[str], test: str, status: str = "pending") -> str:
    listed = "".join(f"  - {f}\n" for f in files)
    return (
        f"# {SID} Greet\n- status: {status}\n- layer: domain\n- depends: none\n"
        f"- files:\n{listed}"
        f"- acceptance:\n  - AC1 | greets by name | {test}::test_ac1_greets\n"
    )


FAKE_CODEX = r"""#!/usr/bin/env bash
# Fake Codex for I0: acts per gate, like the real reviewer/documenter would.
prompt="$(cat)"
phase="$(printf '%s\n' "$prompt" | sed -n 's/^Phase: //p' | head -1)"
id="$(printf '%s\n' "$prompt" | sed -n 's/^Feature: id=\([^ ]*\).*/\1/p' | head -1)"
printf '%s\n' "$phase $id" >> "$I0_LOG"
printf '%s\n' "$prompt" > "$I0_LOG.prompt.$phase"
verdict() { printf '# Review\n\n**Verdict:** %s\n' "$2" > "$1"; }
case "$phase" in
  roadmap) verdict progress/roadmap_review.md ROADMAP_APPROVED ;;
  phase)   verdict "progress/phase_review_$id.md" PHASE_APPROVED ;;
  plan)    mkdir -p "$(dirname "$I0_ACCEPTANCE")"
           printf '%s' "$I0_ACCEPTANCE_BODY" > "$I0_ACCEPTANCE"
           verdict "progress/plan_review_$id.md" PLAN_APPROVED ;;
  code)    verdict "progress/review_$id.md" APPROVED ;;
  doc)     sed -i 's/^- status: in_progress$/- status: done/' "$I0_SPEC"
           mkdir -p docs/reference
           cat > "docs/reference/$id-greet.md" <<DOC
---
id: $id
date: 2026-11-03
alias: greet
tags: [i0]
---
<!-- section: concept -->
<!-- section: planning -->
<!-- section: execution -->
<!-- section: problems -->
<!-- section: files -->
DOC
           python3 harness/sync_plan.py >/dev/null
           msg='feat(domain): greet by name [%s]\n\nAdds greet().\n'
           printf "$msg" "$id" > "progress/close_$id.msg"
           printf '## Overview\nGreeting task.\n' > progress/pr_1.1.md ;;
esac
"""

FAKE_GH = r"""#!/usr/bin/env bash
echo "$*" >> "$I0_GH_LOG"
if [ "$1 $2" = "pr view" ]; then exit 1; fi
if [ "$1 $2" = "pr create" ]; then
  while [ $# -gt 0 ]; do [ "$1" = --body-file ] && cp "$2" "$I0_GH_LOG.body"; shift; done
fi
exit 0
"""


def fake_tools(
    project: Installed, tmp: Path, acceptance: str, body: str, spec_path: str
) -> dict[str, str]:
    """Fake codex + gh on PATH and a bare origin; returns the env for the gates."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    for name, script in (("codex", FAKE_CODEX), ("gh", FAKE_GH)):
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    remote = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(project, "remote", "add", "origin", str(remote))
    return {
        **project.env,
        "PATH": f"{bin_dir}:{project.env['PATH']}",
        "CLAUDE_PROJECT_DIR": str(project.root),
        "I0_LOG": str(tmp / "codex.log"),
        "I0_GH_LOG": str(tmp / "gh.log"),
        "I0_ACCEPTANCE": acceptance,
        "I0_ACCEPTANCE_BODY": body,
        "I0_SPEC": spec_path,
    }


def git(project: Installed, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=project.root,
        env=project.env,
        text=True,
        capture_output=True,
        check=True,
    )
    return res.stdout.strip()


def gate(project: Installed, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "harness/review_with_codex.sh", *args],
        cwd=project.root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )


def hook(project: Installed, script: str, payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", f"harness/{script}"],
        input=payload,
        cwd=project.root,
        env={**project.env, "CLAUDE_PROJECT_DIR": str(project.root)},
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
