"""Issue #30: a re-gated issue that changes an approved `migrates:` list supersedes the
stale oracle migration record, and the next plan gate rebuilds it from the archives.

Projects are built with the conftest Plan (real docs/plan, real sync_plan), turned into
git repos with the harness linked; the gates run the real review_with_codex.sh with a
fake reviewer CLI on PATH (as tests/test_i2_verdict_gate.py), HARNESS_CHECK_CMD=true.
A fake reviewer edits files during its run (FAKE_EDIT), as Codex does at a plan gate.
Crash points and "changed after the commit" cases drive the same protocol by hand:
`_verdict.py begin/commit` (the gate's own commands), then the gate's `seal`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan, Project, bash_payload, write_payload
from i3a_helpers import BLOCKED, git

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

IID = "ISS-14"
LEAF = "ISS-14.1"
LEAF2 = "ISS-14.2"
A = "tests/acceptance/test_1.1.1.py"
B = "tests/acceptance/test_1.1.2.py"
C = "tests/acceptance/test_1.1.3.py"
D = "tests/acceptance/test_1.1.4.py"
OWNER = {A: "1.1.1", B: "1.1.2", C: "1.1.3", D: "1.1.4"}
ISSUE_DIR = f"docs/plan/issues/{IID}-i14"
SPEC = {LEAF: f"{ISSUE_DIR}/{LEAF}-s.md", LEAF2: f"{ISSUE_DIR}/{LEAF2}-s.md"}
OWN = {LEAF: f"tests/acceptance/test_{LEAF}.py", LEAF2: f"tests/acceptance/test_{LEAF2}.py"}
RECORDS = "progress/oracle_migrations"
TICKETS = "harness-supersede-ticket"

SUPERSEDED = "{}: its migration record was superseded; re-run its plan gate"
INTERRUPTED = "{}: interrupted migration seal; finish it before re-gating the issue"
CHANGED = f"issue {IID} changed after its approval; re-run its gate"
MISMATCH = "{}: the migration record does not match its approved migrates:"

FAKE = r"""#!/usr/bin/env bash
cat > /dev/null
[ -z "${FAKE_EDIT:-}" ] || bash -c "$FAKE_EDIT"
printf '# Review\n\n**Verdict:** %s\n\n%s\n' "$FAKE_TOKEN" "${FAKE_NOTE:-}" > "$FAKE_VERDICT"
exit 0
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── the project ──────────────────────────────────────────────────────────────


@dataclass
class Proj:
    plan: Plan
    tmp: Path

    @property
    def root(self) -> Path:
        return self.plan.root

    def path(self, rel: str) -> Path:
        return self.root / rel

    def read(self, rel: str) -> str:
        return self.path(rel).read_text()

    def write(self, rel: str, text: str) -> None:
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def env(self, **extra: str) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
        return {
            **base,
            "PATH": f"{self.tmp / 'fakebin'}:{os.environ['PATH']}",
            "CLAUDE_PROJECT_DIR": str(self.root),
            "HARNESS_CHECK_CMD": "true",
            "TMPDIR": str(self.tmp / "logs"),
            **extra,
        }

    def run(self, *argv: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv), cwd=self.root, env=self.env(**extra), text=True,
            capture_output=True, check=False, timeout=600,
        )  # fmt: skip

    def guard(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run("python3", "harness/_guard.py", *args)

    def sync(self) -> None:
        res = self.run("python3", "harness/sync_plan.py")
        assert res.returncode == 0, res.stdout + res.stderr

    def git_path(self, name: str) -> Path:
        out = git(self.root, "rev-parse", "--git-path", name)
        return (self.root / out).resolve()

    def ticket(self, item: str) -> Path:
        return self.git_path(TICKETS) / item

    # ── gates ────────────────────────────────────────────────────────────────

    def gate(
        self, mode: str, item: str, token: str, edit: str = "", note: str = ""
    ) -> subprocess.CompletedProcess[str]:
        verdict = f"progress/issue_review_{item}.md" if mode == "issue" else (
            f"progress/plan_review_{item}.md")  # fmt: skip
        return self.run(
            "bash", "harness/review_with_codex.sh", mode, item,
            FAKE_TOKEN=token, FAKE_VERDICT=verdict, FAKE_EDIT=edit, FAKE_NOTE=note,
        )  # fmt: skip

    def issue_gate(self, token: str = "ISSUE_APPROVED", note: str = "") -> str:
        res = self.gate("issue", IID, token, note=note)
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        return out

    def plan_gate(self, leaf: str = LEAF, *changed: str, note: str = "") -> str:
        """The plan gate, its reviewer writing the oracle and editing `changed` files."""
        res = self.gate("plan", leaf, "PLAN_APPROVED", _edits(leaf, changed), note)
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        return out

    def commit_by_hand(self, mode: str, item: str, verdict_text: str) -> None:
        """The gate's protocol without its seal: begin, the verdict, commit."""
        verdict = f"progress/issue_review_{item}.md" if mode == "issue" else (
            f"progress/plan_review_{item}.md")  # fmt: skip
        owner = str(os.getpid())
        for step in ("begin",):
            res = self.run("python3", "harness/_verdict.py", step, "--owner", owner, mode, verdict)
            assert res.returncode == 0, res.stdout + res.stderr
        self.write(verdict, verdict_text)
        res = self.run("python3", "harness/_verdict.py", "commit", "--owner", owner, mode, verdict)
        assert res.returncode == 0, res.stdout + res.stderr

    # ── specs ────────────────────────────────────────────────────────────────

    def set_migrates(self, leaf: str, paths: list[str]) -> None:
        spec = self.path(SPEC[leaf])
        lines = spec.read_text().splitlines(keepends=True)
        out, skip = [], False
        for line in lines:
            if line.startswith("- migrates:"):
                skip = True
                continue
            if skip and line.startswith("  - "):
                continue
            skip = False
            if line.startswith("- acceptance:") and paths:
                out.append("- migrates:\n" + "".join(f"  - {p}\n" for p in paths))
            out.append(line)
        spec.write_text("".join(out))
        self.sync()

    # ── state ────────────────────────────────────────────────────────────────

    def record(self, leaf: str = LEAF) -> dict[str, object] | None:
        path = self.path(f"{RECORDS}/{leaf}.json")
        return json.loads(path.read_text()) if path.is_file() else None

    def archives(self, leaf: str = LEAF) -> list[str]:
        folder = self.path(RECORDS)
        return sorted(p.name for p in folder.glob(f"{leaf}.superseded-*.json"))

    def owner_entry(self, rel: str, sid: str | None = None) -> str:
        manifest = self.read(f"progress/acceptance_{sid or OWNER[rel]}.sha256")
        return next(line.split()[0] for line in manifest.splitlines() if line.endswith(rel))

    def state(self) -> dict[str, bytes]:
        """Every byte the supersede and seal steps may touch."""
        files = [*self.path("progress").rglob("*")]
        tickets = self.git_path(TICKETS)
        files += list(tickets.rglob("*")) if tickets.is_dir() else []
        return {str(p): p.read_bytes() for p in sorted(files) if p.is_file()}


def _edits(leaf: str, changed: tuple[str, ...]) -> str:
    own = f"mkdir -p tests/acceptance; printf 'def test_ac1() -> None: ...\\n' > {OWN[leaf]}"
    rest = "".join(f"; printf '# migrated by {leaf}\\n' >> {rel}" for rel in changed)
    return own + rest


def _seal_oracle(proj: Proj, rel: str) -> None:
    proj.write(rel, f"def test_ac1_{OWNER[rel].replace('.', '')}(): assert True\n")
    sid = OWNER[rel]
    proj.write(f"progress/acceptance_{sid}.sha256", f"{_sha(proj.path(rel))}  {rel}\n")


def _build(
    plan: Plan, tmp: Path, migrates: dict[str, list[str]] | None = None, second: bool = False
) -> Proj:
    """Phase 01 done with four sealed oracles; issue ISS-14 approved with the given
    `migrates:` (default: ISS-14.1 -> [A]); a git repo with the harness linked."""
    plan.phase("01", "1.1")
    for sid in OWNER.values():
        plan.sub(sid, "done")
    plan.approve_roadmap().approve_phase("01")
    plan.issue(14).isub(LEAF, "in_progress")
    if second:
        plan.isub(LEAF2, "in_progress")
    plan.sync()
    proj = Proj(plan, tmp)
    (tmp / "logs").mkdir()
    fakebin = tmp / "fakebin"
    fakebin.mkdir()
    (fakebin / "codex").write_text(FAKE)
    (fakebin / "codex").chmod(0o755)
    (proj.root / "harness").symlink_to(TOOLKIT / "lib")
    (proj.root / ".claude").mkdir()
    (proj.root / ".claude/codex").symlink_to(TOOLKIT / "codex")
    proj.write(".gitignore", "harness\n.claude/\n")
    for rel in OWNER:
        _seal_oracle(proj, rel)
    for leaf, paths in (migrates or {LEAF: [A]}).items():
        proj.set_migrates(leaf, paths)
    git(proj.root, "init", "-q", "-b", f"fix/{IID}-issue-14")  # the issue branch
    git(proj.root, "config", "user.email", "dev@example.com")
    git(proj.root, "config", "user.name", "dev")
    proj.issue_gate()
    git(proj.root, "add", "-A")
    git(proj.root, "commit", "-qm", "approved issue")
    return proj


def _with_record(
    plan: Plan, tmp: Path, paths: list[str] | None = None
) -> tuple[Proj, dict[str, str]]:
    """ISS-14.1 plan-gated once with `migrates: paths` (default [A]): its record exists.
    Returns the project and the hashes of every oracle before that plan gate."""
    paths = paths if paths is not None else [A]
    proj = _build(plan, tmp, {LEAF: paths})
    before = {rel: _sha(proj.path(rel)) for rel in OWNER}
    proj.plan_gate(LEAF, *paths)
    assert proj.record() is not None
    return proj, before


def _regate(proj: Proj, paths: list[str], leaf: str = LEAF) -> str:
    proj.set_migrates(leaf, paths)
    return proj.issue_gate()


def _expect_record(proj: Proj, olds: dict[str, str], leaf: str = LEAF) -> None:
    """The active record lists exactly `olds` with their old hashes and current files;
    every owner manifest holds the current hash of each path."""
    record = proj.record(leaf)
    assert record is not None
    assert record["complete"] is True
    paths = record["paths"]
    assert isinstance(paths, dict)
    assert sorted(paths) == sorted(olds)
    for rel, old in olds.items():
        current = _sha(proj.path(rel))
        assert paths[rel]["old"] == old, rel
        assert paths[rel]["new"] == current, rel
        assert paths[rel]["owners"] == [f"progress/acceptance_{OWNER[rel]}.sha256"], rel
        assert proj.owner_entry(rel) == current, rel
    res = proj.guard("integrity")
    assert res.returncode == 0, res.stdout + res.stderr


# ── 1-2. The reported sequence ───────────────────────────────────────────────


def test_regate_supersedes_and_plan_gate_rebuilds(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    old_record = proj.path(f"{RECORDS}/{LEAF}.json").read_bytes()
    _regate(proj, [A, B])
    assert proj.record() is None
    assert proj.archives() == [f"{LEAF}.superseded-1.json"]
    assert proj.path(f"{RECORDS}/{LEAF}.superseded-1.json").read_bytes() == old_record
    assert not proj.ticket(IID).exists()  # used
    proj.plan_gate(LEAF, B)
    assert not proj.ticket(LEAF).exists()
    _expect_record(proj, {A: before[A], B: before[B]})


def test_regate_rebuild_with_the_migrated_path_changed_again(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    _regate(proj, [A, B])
    proj.plan_gate(LEAF, A, B)
    _expect_record(proj, {A: before[A], B: before[B]})


def test_a_record_of_the_current_format_is_superseded(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    record = proj.record()
    assert record is not None and set(record) == {"leaf", "complete", "paths"}
    rel = f"{RECORDS}/{LEAF}.json"
    proj.write(rel, json.dumps(record, indent=2) + "\n")  # exactly as _new_record writes it
    _regate(proj, [A, B])
    assert proj.archives() == [f"{LEAF}.superseded-1.json"]
    proj.plan_gate(LEAF, B)
    _expect_record(proj, {A: before[A], B: before[B]})


# ── 3-4. Validation before any change ────────────────────────────────────────


def _regate_refused(proj: Proj, paths: list[str]) -> str:
    seal = proj.read(f"progress/plan_issue_{IID}.sha256")
    snapshot = proj.read(f"progress/issue_migrations_{IID}.json")
    state = {k: v for k, v in proj.state().items() if "issue_review" not in k}
    proj.set_migrates(LEAF, paths)
    res = proj.gate("issue", IID, "ISSUE_APPROVED")
    out = res.stdout + res.stderr
    assert res.returncode != 0, out
    assert proj.read(f"progress/plan_issue_{IID}.sha256") == seal
    assert proj.read(f"progress/issue_migrations_{IID}.json") == snapshot
    after = {
        k: v for k, v in proj.state().items()
        if "issue_review" not in k and "review_history" not in k and TICKETS not in k
    }  # fmt: skip
    assert after == {k: v for k, v in state.items() if "review_history" not in k}
    assert proj.archives() == []
    return out


def test_interrupted_seal_is_not_superseded(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    proj.write(f"progress/acceptance_{OWNER[A]}.sha256", f"{before[A]}  {A}\n")
    out = _regate_refused(proj, [A, B])
    assert INTERRUPTED.format(LEAF) in out


def _owner_digest(proj: Proj) -> None:
    proj.write(f"progress/acceptance_{OWNER[A]}.sha256", f"{'0' * 64}  {A}\n")


def _owner_set(proj: Proj) -> None:
    manifest = proj.path(f"progress/acceptance_{OWNER[B]}.sha256")
    manifest.write_text(manifest.read_text() + f"{_sha(proj.path(A))}  {A}\n")


def _file_changed(proj: Proj) -> None:
    proj.write(A, proj.read(A) + "# edited after the record\n")


@pytest.mark.parametrize("mutate", [_owner_digest, _owner_set, _file_changed])
def test_a_record_failing_validation_blocks_the_issue_seal(
    plan: Plan, tmp_path: Path, mutate: object
) -> None:
    proj, _ = _with_record(plan, tmp_path)
    assert callable(mutate)
    mutate(proj)
    out = _regate_refused(proj, [A, B])
    assert LEAF in out and A in out


# ── 5. Dropped path ──────────────────────────────────────────────────────────


def test_a_dropped_path_seals_when_untouched(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path, [A, B])
    _regate(proj, [A])
    proj.plan_gate(LEAF)
    _expect_record(proj, {A: before[A]})
    assert proj.owner_entry(B) == _sha(proj.path(B))


def _dropped_file(proj: Proj) -> None:
    proj.write(B, proj.read(B) + "# edited after the drop\n")


def _dropped_owner_entry(proj: Proj) -> None:
    proj.write(f"progress/acceptance_{OWNER[B]}.sha256", f"{'0' * 64}  {B}\n")


def _dropped_owner_set(proj: Proj) -> None:
    manifest = proj.path(f"progress/acceptance_{OWNER[A]}.sha256")
    manifest.write_text(manifest.read_text() + f"{_sha(proj.path(B))}  {B}\n")


@pytest.mark.parametrize("mutate", [_dropped_file, _dropped_owner_entry, _dropped_owner_set])
def test_a_dropped_path_that_changed_fails_the_plan_seal(
    plan: Plan, tmp_path: Path, mutate: object
) -> None:
    proj, _ = _with_record(plan, tmp_path, [A, B])
    _regate(proj, [A])
    assert callable(mutate)
    mutate(proj)
    archive = proj.path(f"{RECORDS}/{LEAF}.superseded-1.json").read_bytes()
    res = proj.gate("plan", LEAF, "PLAN_APPROVED", _edits(LEAF, ()))
    out = res.stdout + res.stderr
    assert res.returncode != 0, out
    assert B in out
    assert proj.record() is None
    assert proj.path(f"{RECORDS}/{LEAF}.superseded-1.json").read_bytes() == archive


# ── 6. Crash points ──────────────────────────────────────────────────────────


def _crashed_issue_seal(proj: Proj, remove_record: bool) -> bytes:
    """A committed ISSUE_APPROVED (ticket written), then a seal that died after the
    archive copy (and after the record removal when `remove_record`)."""
    proj.set_migrates(LEAF, [A, B])
    proj.commit_by_hand("issue", IID, "# Review\n\n**Verdict:** ISSUE_APPROVED\n\nround 2\n")
    assert proj.ticket(IID).is_file()
    active = proj.path(f"{RECORDS}/{LEAF}.json")
    data = active.read_bytes()
    proj.path(f"{RECORDS}/{LEAF}.superseded-1.json").write_bytes(data)
    if remove_record:
        active.unlink()
    return data


@pytest.mark.parametrize("remove_record", [False, True], ids=["after-copy", "after-remove"])
def test_a_crashed_issue_seal_is_finished_by_a_retry(
    plan: Plan, tmp_path: Path, remove_record: bool
) -> None:
    proj, before = _with_record(plan, tmp_path)
    data = _crashed_issue_seal(proj, remove_record)
    res = proj.guard("seal", "issue", IID)
    assert res.returncode == 0, res.stdout + res.stderr
    assert proj.archives() == [f"{LEAF}.superseded-1.json"]  # reused, no duplicate
    assert proj.path(f"{RECORDS}/{LEAF}.superseded-1.json").read_bytes() == data
    assert proj.record() is None
    assert not proj.ticket(IID).exists()
    assert json.loads(proj.read(f"progress/issue_migrations_{IID}.json")) == {LEAF: [A, B]}
    proj.plan_gate(LEAF, B)
    _expect_record(proj, {A: before[A], B: before[B]})


# ── 7. Repeated re-gates ─────────────────────────────────────────────────────


def test_add_then_drop_keeps_two_archives(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    _regate(proj, [A, B])
    proj.plan_gate(LEAF, B)
    _regate(proj, [A])
    proj.plan_gate(LEAF)
    assert proj.archives() == [f"{LEAF}.superseded-1.json", f"{LEAF}.superseded-2.json"]
    _expect_record(proj, {A: before[A]})
    assert proj.owner_entry(B) == _sha(proj.path(B))


def test_drop_and_re_add_finds_the_older_archive(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    _regate(proj, [A, B])
    proj.plan_gate(LEAF, B)
    _regate(proj, [A])
    proj.plan_gate(LEAF)
    _regate(proj, [A, B])
    proj.plan_gate(LEAF)  # B unchanged since it was dropped
    assert len(proj.archives()) == 3
    _expect_record(proj, {A: before[A], B: before[B]})


def test_empty_list_keeps_the_archive_for_a_later_re_add(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    _regate(proj, [])
    stale = proj.guard("seal", LEAF)  # the old PLAN_APPROVED: no ticket
    assert stale.returncode != 0
    assert SUPERSEDED.format(LEAF) in stale.stdout + stale.stderr
    proj.plan_gate(LEAF)
    assert proj.record() is None
    assert proj.archives() == [f"{LEAF}.superseded-1.json"]
    _regate(proj, [A])
    assert proj.archives() == [f"{LEAF}.superseded-1.json"]
    proj.plan_gate(LEAF)
    _expect_record(proj, {A: before[A]})


def test_a_regate_without_a_plan_seal_archives_nothing_new(plan: Plan, tmp_path: Path) -> None:
    proj, before = _with_record(plan, tmp_path)
    _regate(proj, [A, B])
    _regate(proj, [A, B, C])
    assert proj.archives() == [f"{LEAF}.superseded-1.json"]
    proj.plan_gate(LEAF, B, C)
    _expect_record(proj, {A: before[A], B: before[B], C: before[C]})


# ── 8. Issue-wide preflight ──────────────────────────────────────────────────


def test_one_failing_record_blocks_every_supersede(plan: Plan, tmp_path: Path) -> None:
    proj = _build(plan, tmp_path, {LEAF: [A], LEAF2: [C]}, second=True)
    proj.plan_gate(LEAF, A)
    proj.plan_gate(LEAF2, C)
    proj.write(f"progress/acceptance_{OWNER[C]}.sha256", f"{'0' * 64}  {C}\n")
    state = {k: v for k, v in proj.state().items() if "review" not in k}
    seal = proj.read(f"progress/plan_issue_{IID}.sha256")
    proj.set_migrates(LEAF, [A, B])
    proj.set_migrates(LEAF2, [C, D])
    res = proj.gate("issue", IID, "ISSUE_APPROVED")
    assert res.returncode != 0, res.stdout + res.stderr
    assert LEAF2 in res.stdout + res.stderr and C in res.stdout + res.stderr
    after = {k: v for k, v in proj.state().items() if "review" not in k and TICKETS not in k}
    assert after == state
    assert proj.read(f"progress/plan_issue_{IID}.sha256") == seal
    assert proj.archives(LEAF) == [] and proj.archives(LEAF2) == []


SUPERSEDE_CALL = """
import json, sys
from pathlib import Path
sys.path.insert(0, "harness")
import _migrate
print(json.dumps(_migrate.supersede(Path.cwd(), json.loads(sys.argv[1]), json.loads(sys.argv[2]))))
"""


CASES = {
    "lists-on-disk-differ": ([A, B], [LEAF], [A], False),
    "lists-in-memory-differ": ([A], [LEAF], [A, B], True),
    "subtask-not-in-memory": ([A, B], [], [A, B], False),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_supersede_decides_from_the_subtasks_and_lists_it_is_given(
    plan: Plan, tmp_path: Path, case: str
) -> None:
    """Scope 1: the supersede step never re-reads feature_list.json."""
    on_disk, leaves, in_memory, archived = CASES[case]
    proj, _ = _with_record(plan, tmp_path)
    proj.set_migrates(LEAF, on_disk)
    lists = json.dumps({LEAF: in_memory})
    res = proj.run("python3", "-c", SUPERSEDE_CALL, json.dumps(leaves), lists)
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout) == []
    assert (proj.archives() == [f"{LEAF}.superseded-1.json"]) is archived
    assert (proj.record() is None) is archived


# ── 9. No supersede ──────────────────────────────────────────────────────────


def _changed_after_commit(proj: Proj, what: str) -> None:
    if what == "spec":
        spec = proj.path(SPEC[LEAF])
        spec.write_text(spec.read_text().replace("- AC1 | ", "- AC1 | still "))
    else:  # only feature_list.json
        data = json.loads(proj.read("feature_list.json"))
        text = json.dumps(data).replace(json.dumps(B), json.dumps(C))
        proj.write("feature_list.json", text)


@pytest.mark.parametrize("what", ["spec", "feature_list"])
def test_a_change_after_the_commit_voids_the_ticket(plan: Plan, tmp_path: Path, what: str) -> None:
    proj, _ = _with_record(plan, tmp_path)
    seal = proj.read(f"progress/plan_issue_{IID}.sha256")
    snapshot = proj.read(f"progress/issue_migrations_{IID}.json")
    record = proj.read(f"{RECORDS}/{LEAF}.json")
    proj.set_migrates(LEAF, [A, B])
    proj.commit_by_hand("issue", IID, "# Review\n\n**Verdict:** ISSUE_APPROVED\n\nround 2\n")
    assert proj.ticket(IID).is_file()
    _changed_after_commit(proj, what)
    res = proj.guard("seal", "issue", IID)  # the gate's next step
    assert res.returncode != 0
    assert CHANGED in res.stdout + res.stderr
    assert not proj.ticket(IID).exists()
    assert proj.read(f"progress/plan_issue_{IID}.sha256") == seal
    assert proj.read(f"progress/issue_migrations_{IID}.json") == snapshot
    assert proj.read(f"{RECORDS}/{LEAF}.json") == record
    assert proj.archives() == []


def test_a_subtask_removed_after_the_commit_voids_the_ticket(plan: Plan, tmp_path: Path) -> None:
    """The approved list is empty, so only the subtask set changes after the commit."""
    proj, _ = _with_record(plan, tmp_path)
    seal = proj.read(f"progress/plan_issue_{IID}.sha256")
    snapshot = proj.read(f"progress/issue_migrations_{IID}.json")
    record = proj.read(f"{RECORDS}/{LEAF}.json")
    proj.set_migrates(LEAF, [])
    proj.commit_by_hand("issue", IID, "# Review\n\n**Verdict:** ISSUE_APPROVED\n\nround 2\n")
    assert proj.ticket(IID).is_file()
    data = json.loads(proj.read("feature_list.json"))
    for issue in data["issues"]:
        issue["subtasks"] = [s for s in issue["subtasks"] if s["id"] != LEAF]
    proj.write("feature_list.json", json.dumps(data))
    res = proj.guard("seal", "issue", IID)
    assert res.returncode != 0
    assert CHANGED in res.stdout + res.stderr
    assert not proj.ticket(IID).exists()
    assert proj.read(f"progress/plan_issue_{IID}.sha256") == seal
    assert proj.read(f"progress/issue_migrations_{IID}.json") == snapshot
    assert proj.read(f"{RECORDS}/{LEAF}.json") == record
    assert proj.archives() == []


def _stale_seal_as_today(proj: Proj, record: str) -> None:
    res = proj.guard("seal", "issue", IID)
    assert res.returncode == 0, res.stdout + res.stderr
    assert proj.read(f"{RECORDS}/{LEAF}.json") == record
    assert proj.archives() == []
    res = proj.guard("seal", LEAF)
    assert res.returncode != 0
    assert MISMATCH.format(LEAF) in res.stdout + res.stderr
    assert proj.read(f"{RECORDS}/{LEAF}.json") == record


def test_a_direct_issue_seal_without_a_ticket_never_supersedes(plan: Plan, tmp_path: Path) -> None:
    proj, _ = _with_record(plan, tmp_path)
    record = proj.read(f"{RECORDS}/{LEAF}.json")
    proj.set_migrates(LEAF, [A, B])
    assert not proj.ticket(IID).exists()
    _stale_seal_as_today(proj, record)


def test_a_ticket_of_another_verdict_never_supersedes(plan: Plan, tmp_path: Path) -> None:
    proj, _ = _with_record(plan, tmp_path)
    record = proj.read(f"{RECORDS}/{LEAF}.json")
    proj.set_migrates(LEAF, [A, B])
    proj.commit_by_hand("issue", IID, "# Review\n\n**Verdict:** ISSUE_APPROVED\n\nround 2\n")
    assert proj.ticket(IID).is_file()
    proj.write(f"progress/issue_review_{IID}.md", "# Review\n\n**Verdict:** ISSUE_APPROVED\n\nx\n")
    _stale_seal_as_today(proj, record)


def test_manual_mode_never_supersedes(plan: Plan, tmp_path: Path) -> None:
    proj, _ = _with_record(plan, tmp_path)
    record = proj.read(f"{RECORDS}/{LEAF}.json")
    proj.set_migrates(LEAF, [A, B])
    res = proj.run(
        "bash", "harness/review_with_codex.sh", "issue", IID, HARNESS_CODEX_MODE="manual"
    )
    assert res.returncode == 0, res.stdout + res.stderr
    proj.write(f"progress/issue_review_{IID}.md", "# Review\n\n**Verdict:** ISSUE_APPROVED\n")
    assert not proj.ticket(IID).exists()
    _stale_seal_as_today(proj, record)


def test_a_changes_requested_round_never_supersedes(plan: Plan, tmp_path: Path) -> None:
    proj, _ = _with_record(plan, tmp_path)
    record = proj.read(f"{RECORDS}/{LEAF}.json")
    proj.set_migrates(LEAF, [A, B])
    proj.issue_gate("CHANGES_REQUESTED")
    assert not proj.ticket(IID).exists()
    assert proj.read(f"{RECORDS}/{LEAF}.json") == record
    assert proj.archives() == []


# ── 10. Plan side after supersession ─────────────────────────────────────────


def _superseded(plan: Plan, tmp: Path) -> Proj:
    proj, _ = _with_record(plan, tmp)
    _regate(proj, [A, B])
    assert proj.record() is None and proj.archives()
    return proj


def _plan_refused(proj: Proj, run: Callable[[], subprocess.CompletedProcess[str]]) -> None:
    state = {k: v for k, v in proj.state().items() if "plan_review" not in k and TICKETS not in k}
    assert callable(run)
    res = run()
    out = res.stdout + res.stderr
    assert res.returncode != 0, out
    assert SUPERSEDED.format(LEAF) in out
    after = {
        k: v for k, v in proj.state().items()
        if "plan_review" not in k and TICKETS not in k and "review_history" not in k
        and "verdict_plan_review" not in k
    }  # fmt: skip
    assert after == {k: v for k, v in state.items() if "verdict_plan_review" not in k
                     and "review_history" not in k}  # fmt: skip
    assert proj.record() is None


def test_a_standalone_seal_with_an_older_plan_approval_fails(plan: Plan, tmp_path: Path) -> None:
    proj = _superseded(plan, tmp_path)
    _plan_refused(proj, lambda: proj.guard("seal", LEAF))


@pytest.mark.parametrize("changed", ["spec", "acceptance", "carried", "declared"])
def test_a_plan_seal_after_a_change_since_the_commit_fails(
    plan: Plan, tmp_path: Path, changed: str
) -> None:
    proj = _superseded(plan, tmp_path)
    proj.write(OWN[LEAF], "def test_ac1() -> None: ...\n")
    proj.write(B, proj.read(B) + "# migrated\n")
    proj.commit_by_hand("plan", LEAF, "# Review\n\n**Verdict:** PLAN_APPROVED\n\nround 2\n")
    assert proj.ticket(LEAF).is_file()
    rel = {"spec": SPEC[LEAF], "acceptance": OWN[LEAF], "carried": A, "declared": B}[changed]
    if changed == "spec":
        proj.write(rel, proj.read(rel).replace("- AC1 | ", "- AC1 | also "))
    else:
        proj.write(rel, proj.read(rel) + "# after the commit\n")
    _plan_refused(proj, lambda: proj.guard("seal", LEAF))


# ── 11. Unchanged records and the guard ──────────────────────────────────────


@pytest.mark.parametrize("content", ['{"leaf": "ISS-14.1", "complete": false}', "{not json"])
def test_an_incomplete_or_unreadable_record_is_refused(
    plan: Plan, tmp_path: Path, content: str
) -> None:
    proj, _ = _with_record(plan, tmp_path)
    proj.write(f"{RECORDS}/{LEAF}.json", content)
    _regate_refused(proj, [A, B])
    assert proj.read(f"{RECORDS}/{LEAF}.json") == content
    proj.set_migrates(LEAF, [A])  # back to the approved list: today's plain seal
    res = proj.guard("seal", LEAF)
    assert res.returncode != 0
    word = "incomplete" if "complete" in content else "unreadable"
    assert f"{word} migration record" in res.stdout + res.stderr


@pytest.fixture
def linked(project: Project, tmp_path: Path) -> Project:
    wt = tmp_path / "wt"
    git(project.root, "worktree", "add", "-q", str(wt), "-b", "feat")
    (wt / "harness").symlink_to((project.root / "harness").resolve())
    (wt / ".claude").mkdir(exist_ok=True)
    return Project(wt)


def _blocked(proj: Project, payload: dict[str, object], hook: str) -> None:
    res = proj.run(hook, payload)
    assert res.returncode == BLOCKED, (hook, payload, res.stdout, res.stderr)


@pytest.mark.parametrize("rel", [f"{RECORDS}/{LEAF}.json", f"{RECORDS}/{LEAF}.superseded-1.json"])
def test_the_agent_cannot_write_or_remove_records_or_archives(project: Project, rel: str) -> None:
    _blocked(project, write_payload(rel), "guard_write.sh")
    _blocked(project, bash_payload(f"echo x > {rel}"), "guard_bash.sh")
    _blocked(project, bash_payload(f"rm -f {rel}"), "guard_bash.sh")


def test_the_agent_cannot_run_the_issue_seal(project: Project) -> None:
    _blocked(project, bash_payload(f"python3 harness/_guard.py seal issue {IID}"), "guard_bash.sh")


@pytest.mark.parametrize("where", ["main", "linked"])
def test_the_agent_cannot_touch_a_ticket(project: Project, linked: Project, where: str) -> None:
    proj = project if where == "main" else linked
    raw = git(proj.root, "rev-parse", "--git-path", TICKETS)
    folder = (proj.root / raw).resolve()
    if where == "linked":
        assert not folder.is_relative_to(proj.root)  # precondition: outside the root
    folder.mkdir(parents=True, exist_ok=True)
    for target in (str(folder), f"{folder}/{IID}"):
        _blocked(proj, write_payload(target), "guard_write.sh")
        _blocked(proj, bash_payload(f"echo x > {target}"), "guard_bash.sh")
        _blocked(proj, bash_payload(f"rm -rf {target}"), "guard_bash.sh")
