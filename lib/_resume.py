#!/usr/bin/env python3
"""Deterministic resume and attempt budget (issue #46).

Attempt ledger: review_with_codex.sh records each attempt of a step on one id in
.git/harness-gate-runs/attempts_<step>_<id>.json (outside the tree, never committed;
the guard keeps Claude's tools out of .git/harness-*). Steps: every Codex gate, the
close (`doc`/`close`) and the PR. An attempt that ends in an outcome (a final
verdict, the close commit, the PR) resets the streak; anything else is a failed
attempt: a red check, a reviewer crash, an invalid verdict, a failed commit or
push, or a run that stopped with no outcome at all (killed, interrupted).

Budget: after harness.toml [review] max_attempts failed attempts in a row (default
3, 0 = no cap; no environment override) the step is refused before it launches and
needs the human. The count starts with this ledger: older gate records never count.

Resume: one next action derived from the artifacts (specs, verdicts, seals, gate
records, this ledger), never from memory: the `Next:` line of `make status`,
classified. Only an action the human already authorized carries a command: the
cycle of the single in_progress leaf (its gates, fixes, close and PR) and the
review gates, which never approve anything by themselves. Everything else (start a
subtask, a kickoff, a breakdown, a merge, a seal in manual mode, a cap) waits for
the human: resume never invents a human OK.

  _resume.py [--json]                       print the next action (read-only)
  _resume.py budget <step> <id>             exit 1 when the budget is exhausted
  _resume.py begin <step> <id> <tag>        record the start of an attempt
  _resume.py end <step> <id> <tag> ok|fail [reason]   record its outcome
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

import _config
from _gate_run import live_run
from _status import say

Outcome = Literal["pending", "ok", "fail"]
OUTCOMES: tuple[str, ...] = get_args(Outcome)
Live = Callable[[str], bool]
Kind = Literal["implement", "fix", "review", "seal", "close", "pr", "human"]
SCHEMA = 1
MAX_ENTRIES = 20
STEPS = (
    "roadmap",
    "phase",
    "task",
    "task-close",
    "phase-close",
    "issue",
    "issue-draft",
    "plan",
    "code",
    "close",
    "pr",
)
RE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,63}$")
PENDING = "running or interrupted: no outcome recorded"
INTERRUPTED = "interrupted: the run ended with no outcome recorded"
GATE = "./harness/review_with_codex.sh"


class LedgerError(ValueError):
    """An attempt ledger is unreadable, malformed or addressed by a bad name."""


@dataclass(frozen=True, slots=True)
class Entry:
    tag: str
    outcome: Outcome
    reason: str
    at: str


# ── the ledger ──────────────────────────────────────────────────────────────


def ledger_path(runs: Path, step: str, fid: str) -> Path:
    if step not in STEPS:
        raise LedgerError(f"unknown step: {step!r}")
    if not RE_ID.fullmatch(fid) or ".." in fid:
        raise LedgerError(f"bad id: {fid!r}")
    return runs / f"attempts_{step}_{fid}.json"


@dataclass(frozen=True, slots=True)
class Ledger:
    entries: list[Entry]
    # Failed attempts of the current streak that no longer fit in `entries` (the
    # history is bounded, the streak is not: a cap above MAX_ENTRIES still works).
    streak_base: int = 0
    # A seal failure of the current streak that no longer fits in `entries`: the seal
    # stays missing until an outcome, however many retries failed since.
    streak_seal: str = ""


def read_ledger(path: Path) -> Ledger:
    """The ledger at <path>, oldest entry first; empty when it does not exist."""
    if not path.exists():
        return Ledger([])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerError(f"unreadable attempt ledger {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise LedgerError(f"bad attempt ledger {path}: wrong shape or schema")
    items = raw.get("entries")
    base = raw.get("streak_base", 0)
    seal = raw.get("streak_seal", "")
    if not isinstance(items, list) or type(base) is not int or base < 0:
        raise LedgerError(f"bad attempt ledger {path}: no entries list or bad streak_base")
    if not isinstance(seal, str):
        raise LedgerError(f"bad attempt ledger {path}: bad streak_seal")
    out = []
    for item in items:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(k), str) for k in ("tag", "outcome", "reason", "at")
        ):
            raise LedgerError(f"bad attempt ledger {path}: malformed entry")
        if item["outcome"] not in OUTCOMES:
            raise LedgerError(f"bad attempt ledger {path}: outcome {item['outcome']!r}")
        out.append(Entry(item["tag"], item["outcome"], item["reason"], item["at"]))
    return Ledger(out, base, seal)


def read_entries(path: Path) -> list[Entry]:
    return read_ledger(path).entries


def _trim(ledger: Ledger) -> Ledger:
    """Keep the newest MAX_ENTRIES; carry the dropped part of the streak in its base."""
    if len(ledger.entries) <= MAX_ENTRIES:
        return ledger
    old, keep = ledger.entries[:-MAX_ENTRIES], ledger.entries[-MAX_ENTRIES:]
    # An attempt still pending has no outcome yet: it is never trimmed (nor counted).
    keep = [e for e in old if e.outcome == "pending"] + keep
    drop = [e for e in old if e.outcome != "pending"]
    if any(e.outcome == "ok" for e in keep):
        return Ledger(keep)
    n, seal = 0, ""
    for entry in reversed(drop):
        if entry.outcome == "ok":
            return Ledger(keep, n, seal)
        n += 1
        if not seal and entry.reason.startswith("seal failed"):
            seal = entry.reason
    return Ledger(keep, ledger.streak_base + n, seal or ledger.streak_seal)


def _write(path: Path, ledger: Ledger) -> None:
    """Atomic write (temp file in the same dir, fsync, os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    step, _, fid = path.name.removeprefix("attempts_").removesuffix(".json").partition("_")
    kept = _trim(ledger)
    data = {
        "schema": SCHEMA,
        "step": step,
        "id": fid,
        "streak_base": kept.streak_base,
        "streak_seal": kept.streak_seal,
        "entries": [asdict(e) for e in kept.entries],
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """One writer (and budget decision) at a time per ledger: flock on a side file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / f".{path.stem}.lock"
    with lock.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _never(_: str) -> bool:
    return False


def failures(entries: list[Entry], base: int = 0, live: Live = _never) -> int:
    """Failed (or abandoned) attempts in a row since the last outcome. A pending
    attempt whose run is still alive is not counted."""
    n = 0
    for entry in reversed(entries):
        if entry.outcome == "ok":
            return n
        if not (entry.outcome == "pending" and live(entry.tag)):
            n += 1
    return n + base


def last_failure(entries: list[Entry]) -> Entry | None:
    return entries[-1] if entries and entries[-1].outcome != "ok" else None


def exhausted(ledger: Ledger, cap: int, live: Live = _never) -> int | None:
    """The streak once it reached the cap (0 = no cap), else None."""
    n = failures(ledger.entries, ledger.streak_base, live)
    return n if cap and n >= cap else None


def normalized(ledger: Ledger, live: Live) -> Ledger:
    """The ledger as every reader must see it: an abandoned attempt (pending, its run
    gone) is a failure whose outcome is known only now, so it moves to the end, after
    the outcomes of later runs that ended before it was found dead."""
    abandoned = [e for e in ledger.entries if e.outcome == "pending" and not live(e.tag)]
    if not abandoned:
        return ledger
    entries = [e for e in ledger.entries if e not in abandoned]
    entries += [Entry(e.tag, "fail", INTERRUPTED, e.at) for e in abandoned]
    return Ledger(entries, ledger.streak_base, ledger.streak_seal)


def begin(path: Path, tag: str, cap: int = 0, live: Live = _never) -> int | None:
    """Start an attempt, atomically with the budget decision: returns the streak and
    writes nothing when the budget is used up. A pending attempt whose run is gone
    never ended, so it failed; a live one is left as it is."""
    with _locked(path):
        ledger = normalized(read_ledger(path), live)
        entries = ledger.entries
        if (n := exhausted(ledger, cap, live)) is not None:
            return n
        _write(
            path,
            Ledger(
                [*entries, Entry(tag, "pending", PENDING, _now())],
                ledger.streak_base,
                ledger.streak_seal,
            ),
        )
        return None


def end(path: Path, tag: str, outcome: str, reason: str) -> None:
    if outcome not in ("ok", "fail"):
        raise LedgerError(f"bad outcome: {outcome!r}")
    with _locked(path):
        ledger = read_ledger(path)
        entries = list(ledger.entries)
        # Outcome order, not start order: the attempt moves to the end when it ends,
        # so the streak reads the outcomes as they happened (overlapping runs).
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].tag == tag and entries[i].outcome == "pending":
                del entries[i]
                break
        entries.append(Entry(tag, "ok" if outcome == "ok" else "fail", reason, _now()))
        _write(path, Ledger(entries, ledger.streak_base, ledger.streak_seal))


# ── the project's ledger ────────────────────────────────────────────────────


def runs_dir() -> Path | None:
    """.git/harness-gate-runs of the project in the cwd, or None outside a git repo."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-path", "harness-gate-runs"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    return Path(res.stdout.strip())


def _project_path(step: str, fid: str) -> Path | None:
    runs = runs_dir()
    return None if runs is None else ledger_path(runs, step, fid)


def entries_of(step: str, fid: str) -> list[Entry]:
    """The project's ledger of <step> on <fid> ([] outside git), normalized (see
    normalized()). Raises LedgerError."""
    path = _project_path(step, fid)
    return [] if path is None else normalized(read_ledger(path), live_run).entries


def needs_human(step: str, fid: str) -> str | None:
    """`needs human (N failed attempts) ...` once <step> on <fid> used its budget.
    Raises ConfigError for an untrustworthy harness.toml. A corrupt ledger fails
    closed (it is harness state: only the human can repair it)."""
    cap = _config.load(Path.cwd()).max_attempts
    path = _project_path(step, fid)
    if path is None:
        return None
    try:  # read even with no cap (0): a corrupt ledger always needs the human
        ledger = normalized(read_ledger(path), live_run)
    except LedgerError as exc:
        return f"needs human: {exc}; the human checks it and removes {path} to resume"
    n = exhausted(ledger, cap, live_run)
    if n is None:
        return None
    return _budget_message(step, fid, n, ledger, path)


def _budget_message(step: str, fid: str, n: int, ledger: Ledger, path: Path) -> str:
    last = ledger.entries[-1].reason if ledger.entries else "older attempts"
    return (
        f"needs human ({n} failed attempts): the {step} step on {fid} failed {n} times in "
        f"a row with no outcome (last: {last}; {path}); the human fixes the cause, then "
        f"raises [review] max_attempts in {_config.CONFIG_FILE} (0 = no cap) or removes "
        f"{path} to resume"
    )


def last_failure_reason(step: str, fid: str) -> str | None:
    """The reason of the last attempt when it did not end well; a run still in
    progress has no failure (yet)."""
    try:
        last = last_failure(entries_of(step, fid))
    except LedgerError:
        return None  # needs_human reports it
    if last is None or (last.outcome == "pending" and live_run(last.tag)):
        return None
    return last.reason


def running() -> tuple[str, str, str] | None:
    """(step, id, pid) of any attempt still in progress in this project: an entry
    pending in any ledger (not only the last one: runs may overlap) whose
    review_with_codex.sh is alive. None when nothing runs."""
    runs = runs_dir()
    if runs is None or not runs.is_dir():
        return None
    # Any gate run marks itself (run_<pid>) before its preconditions, in either mode.
    for mark in sorted(runs.glob("run_*")):
        pid = mark.name.removeprefix("run_")
        if live_run(pid):
            return "gate", "this project", pid
    for path in sorted(runs.glob("attempts_*.json")):
        step, _, fid = path.name.removeprefix("attempts_").removesuffix(".json").partition("_")
        try:
            entries = read_entries(path)
        except LedgerError:
            continue  # needs_human reports it
        for entry in entries:
            if entry.outcome == "pending" and live_run(entry.tag):
                return step, fid, entry.tag
    return None


def ledger_error(step: str, fid: str) -> str | None:
    """The error of an unreadable ledger of <step> on <fid>, else None."""
    try:
        entries_of(step, fid)
    except LedgerError as exc:
        return str(exc)
    return None


def wait_text(run: tuple[str, str, str]) -> str:
    step, fid, pid = run
    return f"wait: a {step} run on {fid} is still in progress (pid {pid}); resume when it ends"


def seal_failure(step: str, fid: str) -> str | None:
    """The reason of a seal failure since the step's last outcome, if any: a later
    failed retry (a crash, an interruption) does not make the seal any less missing."""
    path = _project_path(step, fid)
    try:
        ledger = Ledger([]) if path is None else normalized(read_ledger(path), live_run)
    except LedgerError:
        return None  # needs_human reports it
    for entry in reversed(ledger.entries):
        if entry.outcome == "ok":
            return None
        if entry.reason.startswith("seal failed"):
            return entry.reason
    return ledger.streak_seal or None


def has_attempts(step: str, fid: str) -> bool:
    try:
        return bool(entries_of(step, fid))
    except LedgerError:
        return True


def failed_prs() -> list[str]:
    """The ids whose last PR attempt did not open (or update) the PR."""
    runs = runs_dir()
    if runs is None or not runs.is_dir():
        return []
    out = []
    for path in sorted(runs.glob("attempts_pr_*.json")):
        fid = path.name.removeprefix("attempts_pr_").removesuffix(".json")
        try:
            if RE_ID.fullmatch(fid) and last_failure(
                normalized(read_ledger(path), live_run).entries
            ):
                out.append(fid)
        except LedgerError:
            out.append(fid)  # needs_human reports it
    return out


# ── resume: classify the next step ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Action:
    kind: Kind
    text: str
    command: list[str] | None
    authorized: bool


GATES = ("roadmap", "phase", "task", "task-close", "phase-close", "issue", "plan", "code")
_G = re.escape(GATE)
_LEAF = r"\d+\.\d+\.\d+|ISS-\d+\.\d+"
_TASK = r"\d+\.\d+"
_PHASE = r"\d+"
_ISSUE = r"ISS-\d+"
_WHY = r"\(last attempt: .*\)"


def _rule(pattern: str, kind: Kind) -> tuple[re.Pattern[str], Kind]:
    """A complete phrase of plan_status; (?P<cmd>...) is the only command it may carry."""
    return re.compile(pattern), kind


# Every phrase plan_status emits for an authorized step, matched whole, its command
# captured by the same rule and its ids required to agree (backreferences). Anything
# else (a new phrase, an altered suffix, a mismatched id) waits for the human.
RULES: tuple[tuple[re.Pattern[str], Kind], ...] = (
    _rule(rf"plan gate: (?P<cmd>{_G} plan (?P<id>{_LEAF}))", "review"),
    _rule(rf"plan gate again {_WHY}: (?P<cmd>{_G} plan (?P<id>{_LEAF}))", "review"),
    _rule(
        rf"fix the spec of (?P<id>{_LEAF}) \(progress/plan_review_(?P=id)\.md\), "
        rf"then plan gate(?: {_WHY})?",
        "fix",
    ),
    _rule(
        rf"finish the seal of (?P<id>{_LEAF}) \(PLAN_APPROVED, last attempt: .*\): "
        rf"plan gate again: (?P<cmd>{_G} plan (?P=id))",
        "seal",
    ),
    _rule(
        rf"fix (?P<id>{_LEAF}): the check was red at its last code gate \(.*\), "
        rf"then code gate: (?P<cmd>{_G} code (?P=id))",
        "fix",
    ),
    _rule(rf"code gate again {_WHY}: (?P<cmd>{_G} code (?P<id>{_LEAF}))", "review"),
    _rule(
        rf"fix (?P<id>{_LEAF}) \(progress/review_(?P=id)\.md\), then code gate(?: {_WHY})?", "fix"
    ),
    _rule(
        rf"implement (?P<id>{_LEAF}) \(implementer\), then code gate: (?P<cmd>{_G} code (?P=id))",
        "implement",
    ),
    _rule(
        rf"the code approval of (?P<id>{_LEAF}) (?:no longer covers the tree|has no gate record): "
        rf"code gate again: (?P<cmd>{_G} code (?P=id))",
        "review",
    ),
    _rule(
        rf"retry the close of (?P<id>{_LEAF}) \(progress/close_(?P=id)\.msg is waiting, last "
        rf"attempt: .*\): (?P<cmd>{_G} close (?P=id))",
        "close",
    ),
    _rule(rf"close: (?P<cmd>{_G} doc (?P<id>{_LEAF}))(?: {_WHY})?", "close"),
    _rule(rf"retry the PR of (?P<id>{_TASK}|{_ISSUE}) {_WHY}: (?P<cmd>{_G} pr (?P=id))", "pr"),
    _rule(
        rf"finish the seal of (?:the roadmap {_WHY}: roadmap gate again: (?P<cmd>{_G} roadmap)"
        rf"|phase (?P<p>{_PHASE}) {_WHY}: phase gate again: (?P<pcmd>{_G} phase (?P=p))"
        rf"|task (?P<t>{_TASK}) {_WHY}: task gate again: (?P<tcmd>{_G} task (?P=t))"
        rf"|issue (?P<i>{_ISSUE}) {_WHY}: issue gate again: (?P<icmd>{_G} issue (?P=i)))",
        "seal",
    ),
    _rule(rf"roadmap gate: (?P<cmd>{_G} roadmap)", "review"),
    _rule(rf"phase gate: (?P<cmd>{_G} phase (?P<id>{_PHASE}))", "review"),
    _rule(rf"task gate: (?P<cmd>{_G} task (?P<id>{_TASK})) \(then the user's OK\)", "review"),
    _rule(rf"task-close gate: (?P<cmd>{_G} task-close (?P<id>{_TASK}))", "review"),
    _rule(rf"phase-close gate: (?P<cmd>{_G} phase-close (?P<id>{_PHASE}))", "review"),
    _rule(rf"issue gate: (?P<cmd>{_G} issue (?P<id>{_ISSUE}))", "review"),
    _rule(
        rf"fix the roadmap \(progress/roadmap_review\.md\), then roadmap gate: "
        rf"(?P<cmd>{_G} roadmap)",
        "fix",
    ),
    _rule(
        rf"fix the breakdown of phase (?P<id>{_PHASE}) \(progress/phase_review_(?P=id)\.md\), "
        r"then phase gate",
        "fix",
    ),
    _rule(
        rf"fix the breakdown of task (?P<id>{_TASK}) \(progress/task_review_(?P=id)\.md\), "
        rf"then task gate: (?P<cmd>{_G} task (?P=id))",
        "fix",
    ),
    _rule(
        rf"fix the breakdown of issue (?P<id>{_ISSUE}) \(progress/issue_review_(?P=id)\.md\), "
        r"then issue gate",
        "fix",
    ),
    _rule(
        re.escape(
            "the index is stale vs the specs (docs/plan/ is the source of truth): run make plan, "
            "then resume"
        ),
        "fix",
    ),
)
# The literal start of every phrase a rule recognizes (known_prefix, for the test).
AUTHORIZED_STEPS = (
    "fix the spec of",
    "fix ",
    "fix the roadmap",
    "fix the breakdown of",
    "implement ",
    "finish the seal",
    "retry the close",
    "close: ",
    "retry the PR",
    "code gate again",
    "plan gate again",
    "the code approval of",
    "the index is stale vs the specs",
    *(f"{gate} gate: " for gate in GATES),
)
# Steps that need the human's OK (or the human's own work): no command, ever.
HUMAN_STEPS = (
    "needs human",
    "wait: ",
    "fix the gaps of",
    f"fix {_config.CONFIG_FILE}",
    "start subtask",
    "start issue",
    "kickoff task",
    "break down",
    "ask the leader",
    "resume task",
    "issue ",
    "resolve blocked",
    "all planned work is done",
)


def known_prefix(prefix: str) -> bool:
    """True when a `Next:` phrase starting with <prefix> has a rule (tests use it)."""
    return any(r.startswith(prefix) or prefix.startswith(r) for r in AUTHORIZED_STEPS + HUMAN_STEPS)


def classify(text: str) -> Action:
    for rule, kind in RULES:
        found = rule.fullmatch(text)
        if found:
            cmd = next((v for k, v in found.groupdict().items() if k.endswith("cmd") and v), None)
            return Action(kind, text, cmd.split() if cmd else None, True)
    return Action("human", text, None, False)


STALE_INDEX = (
    "the index is stale vs the specs (docs/plan/ is the source of truth): run make plan, "
    "then resume"
)


def index_stale() -> bool:
    """True when feature_list.json does not match the specs (or they do not parse):
    a step derived from it could be one the specs already moved past."""
    import sync_plan

    if not sync_plan.PLAN_DIR.is_dir():
        return False  # no docs/plan/: feature_list.json is all there is
    parser = sync_plan.PlanParser(sync_plan.PLAN_DIR)
    parser.parse()
    if parser.errors:
        return True
    outputs = sync_plan._outputs(sync_plan.build(parser))
    return any(sync_plan._read(path) != text for path, text in outputs.items())


def next_action() -> Action:
    import _harness
    import plan_status

    if run := running():  # before anything else, the stale index included
        return classify(wait_text(run))
    if index_stale():
        return Action("fix", STALE_INDEX, None, True)

    data = _harness.plan_data()
    data.setdefault("phases", [])
    data.setdefault("features", [])
    lane = (_harness.FEATURES_FILE.parent / "docs/plan/issues").is_dir()
    queue = plan_status.github_queue() if lane else None
    return classify(plan_status.next_step(data, queue))


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cmd_next(as_json: bool) -> int:
    import _harness

    if not _harness.FEATURES_FILE.exists():
        say("FAIL", "no feature_list.json (run: make plan)", sys.stderr)
        return 1
    action = next_action()
    if as_json:
        print(json.dumps(asdict(action), indent=2))
        return 0
    print(f"Resume: {action.kind} · {action.text}")
    if action.authorized and action.command:
        print(f"Run: {' '.join(action.command)}")
    elif not action.authorized:
        print("Wait for the human: resume never takes this step on its own.")
    return 0


def _cmd_budget(step: str, fid: str) -> int:
    message = needs_human(step, fid)
    if message is None:
        return 0
    say("FAIL", f"{message}; the {step} step was not launched", sys.stderr)
    return 1


def _cmd_begin(step: str, fid: str, tag: str) -> int:
    """Budget and start in one locked step (fail closed: a ledger that cannot be read
    or written stops the step, so a broken ledger cannot lift the budget)."""
    path = _project_ledger(step, fid)
    cap = _config.load(Path.cwd()).max_attempts
    n = begin(path, tag, cap, live_run)
    if n is None:
        return 0
    message = _budget_message(step, fid, n, read_ledger(path), path)
    say("FAIL", f"{message}; the {step} step was not launched", sys.stderr)
    return 1


def _project_ledger(step: str, fid: str) -> Path:
    path = _project_path(step, fid)
    if path is None:
        raise LedgerError("not a git repository: no .git/harness-gate-runs")
    return path


def _check_tag(tag: str) -> str:
    if not tag.isdigit():
        raise LedgerError(f"bad attempt tag: {tag!r}")
    return tag


def main(argv: list[str]) -> int:
    try:
        if argv in ([], ["--json"]):
            return _cmd_next(argv == ["--json"])
        if len(argv) == 3 and argv[0] == "budget":
            return _cmd_budget(argv[1], argv[2])
        if len(argv) == 4 and argv[0] == "begin":
            return _cmd_begin(argv[1], argv[2], _check_tag(argv[3]))
        if len(argv) in (5, 6) and argv[0] == "end":
            path = _project_ledger(argv[1], argv[2])
            reason = argv[5] if len(argv) == 6 else ""
            end(path, _check_tag(argv[3]), argv[4], reason[:500])
            return 0
    except (LedgerError, _config.ConfigError, OSError) as exc:
        say("FAIL", f"resume: {exc}", sys.stderr)
        return 1
    print(
        "usage: _resume.py [--json] | budget <step> <id> | begin <step> <id> <tag>"
        " | end <step> <id> <tag> ok|fail [reason]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
