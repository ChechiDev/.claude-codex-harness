"""_issue.py: queue, atomic claim, release, resume and publish, against a real git
origin (a bare repo) and a fake gh (tests/fake_gh.py)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

TOOLKIT = Path(__file__).resolve().parents[1]
FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
TASK_BRANCH = "feat/1.1-task-1-1"
ISSUE_BRANCH = "refactor/ISS-14-replace-the-mask"
HEADER = "docs/plan/issues/ISS-14-replace-the-mask.md"


def _plan(status: str = "done", issues: list[dict[str, Any]] | None = None) -> str:
    sub = {"id": "1.1.1", "title": "s", "status": status, "depends": [], "files": []}
    task = {"id": "1.1", "title": "Task 1.1", "phase": "01", "subtasks": [sub]}
    data: dict[str, Any] = {"phases": [{"id": "01", "tasks": ["1.1"]}], "features": [task]}
    if issues:
        data["issues"] = issues
    return json.dumps(data)


@dataclass
class Repo:
    root: Path
    state: Path
    env: dict[str, str]

    def git(self, *args: str, check: bool = True) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )
        if check:
            assert res.returncode == 0, res.stderr
        return res.stdout.strip()

    def issue(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(TOOLKIT / "lib/_issue.py"), *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            env={**self.env, **env},
            check=False,
        )

    def gh_state(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.state.read_text())
        return data

    def set_gh(self, **issue: Any) -> None:
        data = self.gh_state()
        data["issues"]["14"].update(issue)
        self.state.write_text(json.dumps(data))

    def commit(self, rel: str, text: str, msg: str = "c") -> str:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")

    def remote_tag(self) -> str:
        return self.git("ls-remote", "origin", "refs/tags/claim/ISS-14")


def _clone(tmp: Path, origin: Path, name: str, state: Path, me: str) -> Repo:
    root = tmp / name
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp),
        "GIT_AUTHOR_NAME": me,
        "GIT_AUTHOR_EMAIL": f"{me}@x",
        "GIT_COMMITTER_NAME": me,
        "GIT_COMMITTER_EMAIL": f"{me}@x",
        "HARNESS_GH": str(FAKE_GH),
        "FAKE_GH_STATE": str(state),
    }
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True, env=env)
    return Repo(root, state, env)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    FAKE_GH.chmod(0o755)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    state = tmp_path / "gh.json"
    state.write_text(
        json.dumps(
            {
                "me": "alice",
                "next": 15,
                "issues": {
                    "14": {
                        "title": "Replace the mask",
                        "labels": ["refactor", "priority:next"],
                        "assignees": [],
                        "body": "## Problem\nThe mask is hard to read.\n",
                    }
                },
            }
        )
    )
    work = _clone(tmp_path, origin, "work", state, "alice")
    work.commit("feature_list.json", _plan(), "init")
    work.git("push", "-q", "origin", "HEAD:main")
    work.git("fetch", "-q", "origin")
    return work


def _open_task(repo: Repo) -> str:
    """A task branch with an unmerged commit, pushed to origin."""
    repo.git("switch", "-q", "-c", TASK_BRANCH)
    tip = repo.commit("src/a.py", "a = 1\n")
    repo.git("push", "-q", "origin", TASK_BRANCH)
    return tip


# ── queue ────────────────────────────────────────────────────────────────────


def test_queue_sets_one_priority_label(repo: Repo) -> None:
    res = repo.issue("queue", "14", "backlog")
    assert res.returncode == 0, res.stdout + res.stderr
    labels = repo.gh_state()["issues"]["14"]["labels"]
    assert sorted(labels) == ["priority:backlog", "refactor"]
    assert "priority:backlog" in repo.gh_state()["labels"]


def test_queue_defaults_to_next(repo: Repo) -> None:
    repo.set_gh(labels=["refactor"])
    assert repo.issue("queue", "14").returncode == 0
    assert "priority:next" in repo.gh_state()["issues"]["14"]["labels"]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"state": "CLOSED"}, "issue #14 is closed"),
        ({"labels": ["question"]}, "needs exactly one type label"),
        ({"labels": ["bug", "refactor"]}, "it has 2"),
        ({"labels": ["refactor", "status:in-progress"]}, "already in progress"),
    ],
)
def test_queue_refuses(repo: Repo, change: dict[str, Any], error: str) -> None:
    repo.set_gh(**change)
    res = repo.issue("queue", "14")
    assert res.returncode == 1
    assert error in res.stdout + res.stderr


def test_queue_rejects_an_unknown_priority(repo: Repo) -> None:
    res = repo.issue("queue", "14", "soon")
    assert res.returncode == 1
    assert "PRIORITY must be one of now, next, backlog" in res.stdout + res.stderr


# ── start: claim ─────────────────────────────────────────────────────────────


def test_start_claims_the_issue(repo: Repo) -> None:
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    issue = repo.gh_state()["issues"]["14"]
    assert issue["assignees"] == ["alice"]
    assert "status:in-progress" in issue["labels"]
    assert "priority:next" not in issue["labels"]
    assert repo.remote_tag()
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH
    header = (repo.root / HEADER).read_text()
    assert header.startswith("# ISS-14 Replace the mask\n")
    for line in (
        "- type: refactor",
        "- priority: next",
        "- issue: https://github.com/acme/app/issues/14",
        "The mask is hard to read.",
    ):
        assert line in header
    assert "interrupts" not in header


def test_start_refuses_an_issue_that_is_not_queued(repo: Repo) -> None:
    repo.set_gh(labels=["refactor"])
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "is not queued: run make issue N=14 first" in res.stdout + res.stderr
    assert not repo.remote_tag()


def test_start_refuses_an_issue_someone_else_holds(repo: Repo) -> None:
    repo.set_gh(assignees=["bob"])
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "already in progress (bob)" in res.stdout + res.stderr


def test_second_claimant_loses_the_race_on_the_tag(repo: Repo, tmp_path: Path) -> None:
    # alice's tag lands but her GitHub update fails: GitHub still looks unclaimed
    res = repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1")
    assert res.returncode == 1
    assert repo.remote_tag()
    bob = _clone(tmp_path, Path(repo.git("remote", "get-url", "origin")), "bob", repo.state, "bob")
    state = repo.gh_state()
    state["me"] = "bob"
    repo.state.write_text(json.dumps(state))
    res = bob.issue("start", "14")
    assert res.returncode == 1
    assert "issue #14 is already claimed by alice" in res.stdout + res.stderr
    assert bob.git("branch", "--show-current") == "main"


def test_start_retries_only_the_missing_steps(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    tag = repo.remote_tag()
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.remote_tag() == tag  # same claim, no second tag
    assert repo.gh_state()["issues"]["14"]["assignees"] == ["alice"]
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH


# ── start: task open? ────────────────────────────────────────────────────────


def test_next_waits_for_an_open_task(repo: Repo) -> None:
    _open_task(repo)
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert f"a task is open ({TASK_BRANCH})" in res.stdout + res.stderr
    assert not repo.remote_tag()


def test_next_starts_once_the_task_is_merged(repo: Repo) -> None:
    _open_task(repo)
    repo.git("push", "-q", "origin", f"{TASK_BRANCH}:main")
    repo.git("switch", "-q", "main")
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr


def test_a_squash_merge_closes_the_task(repo: Repo) -> None:
    _open_task(repo)
    repo.git("switch", "-q", "main")
    repo.git("merge", "-q", "--squash", TASK_BRANCH)
    repo.git("commit", "-qm", "squash")
    repo.git("push", "-q", "origin", "main")
    assert repo.issue("start", "14").returncode == 0


def test_a_pr_merged_into_main_at_the_tip_closes_the_task(repo: Repo) -> None:
    tip = _open_task(repo)
    repo.git("switch", "-q", "main")
    state = repo.gh_state()
    state["prs"] = [{"head": TASK_BRANCH, "base": "main", "state": "merged", "headRefOid": tip}]
    repo.state.write_text(json.dumps(state))
    assert repo.issue("start", "14").returncode == 0


def test_a_pr_merged_into_another_base_does_not(repo: Repo) -> None:
    tip = _open_task(repo)
    repo.git("switch", "-q", "main")
    state = repo.gh_state()
    state["prs"] = [{"head": TASK_BRANCH, "base": "dev", "state": "merged", "headRefOid": tip}]
    repo.state.write_text(json.dumps(state))
    assert repo.issue("start", "14").returncode == 1


def test_a_local_tip_ahead_of_the_merged_remote_keeps_the_task_open(repo: Repo) -> None:
    _open_task(repo)
    repo.git("push", "-q", "origin", f"{TASK_BRANCH}:main")
    repo.commit("src/b.py", "b = 1\n")  # local only, unmerged
    repo.git("switch", "-q", "main")
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "a task is open" in res.stdout + res.stderr


def test_an_empty_task_branch_is_not_open(repo: Repo) -> None:
    repo.git("branch", TASK_BRANCH)
    assert repo.issue("start", "14").returncode == 0


def test_now_interrupts_from_the_open_task_branch(repo: Repo) -> None:
    _open_task(repo)
    repo.set_gh(labels=["refactor", "priority:now"])
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    header = (repo.root / HEADER).read_text()
    assert f"- interrupts: {TASK_BRANCH}" in header
    assert "- priority: now" in header


def test_now_refuses_from_another_branch(repo: Repo) -> None:
    _open_task(repo)
    repo.git("switch", "-q", "main")
    repo.set_gh(labels=["refactor", "priority:now"])
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert f"run it from the open task branch it interrupts ({TASK_BRANCH})" in (
        res.stdout + res.stderr
    )


def test_start_refuses_a_dirty_tree(repo: Repo) -> None:
    (repo.root / "feature_list.json").write_text(_plan("in_progress"))
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "uncommitted changes" in res.stdout + res.stderr


def test_start_refuses_an_in_progress_leaf_committed_on_a_local_branch(repo: Repo) -> None:
    repo.git("switch", "-q", "-c", "wip")
    repo.commit("feature_list.json", _plan("in_progress"))
    repo.git("switch", "-q", "main")
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "1.1.1 on wip" in res.stdout + res.stderr


# ── release ──────────────────────────────────────────────────────────────────


def test_release_gives_the_issue_back(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    res = repo.issue("release", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    issue = repo.gh_state()["issues"]["14"]
    assert issue["assignees"] == []
    assert sorted(issue["labels"]) == ["priority:next", "refactor"]
    assert not repo.remote_tag()
    assert repo.git("branch", "--show-current") == "main"
    assert not repo.git("branch", "--list", ISSUE_BRANCH)
    assert not (repo.root / HEADER).exists()


@pytest.mark.parametrize("edit", ["header", "spec"])
def test_release_refuses_to_lose_work(repo: Repo, edit: str) -> None:
    assert repo.issue("start", "14").returncode == 0
    if edit == "header":
        (repo.root / HEADER).write_text("# ISS-14 Replace the mask\n- goal: written\n")
    else:
        spec = repo.root / "docs/plan/issues/ISS-14-replace-the-mask/ISS-14.1-x.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("# ISS-14.1 X\n")
    res = repo.issue("release", "14")
    assert res.returncode == 1
    assert "release would lose work" in res.stdout + res.stderr
    assert repo.remote_tag()
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH


def test_release_interrupted_after_the_tag_is_finished_by_a_rerun(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    claim_file = repo.root / repo.git("rev-parse", "--git-path", "harness-claim-ISS-14")
    claim = json.loads(claim_file.read_text())
    claim["releasing"] = True
    claim_file.write_text(json.dumps(claim))
    repo.git("push", "-q", "origin", ":refs/tags/claim/ISS-14")  # the remote tag went first
    res = repo.issue("release", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not claim_file.exists()
    assert not repo.git("tag", "--list", "claim/ISS-14")


def test_release_needs_our_claim(repo: Repo) -> None:
    res = repo.issue("release", "14")
    assert res.returncode == 1
    assert "not claimed from this working copy" in res.stdout + res.stderr


# ── resume ───────────────────────────────────────────────────────────────────


def _interrupting_issue() -> dict[str, Any]:
    return {
        "id": "ISS-14",
        "number": 14,
        "interrupts": TASK_BRANCH,
        "status": "done",
        "subtasks": [],
    }


def test_resume_waits_for_the_issue_merge(repo: Repo) -> None:
    _open_task(repo)
    repo.git("switch", "-q", "-c", ISSUE_BRANCH, "origin/main")
    repo.commit("feature_list.json", _plan(issues=[_interrupting_issue()]))
    res = repo.issue("resume")
    assert res.returncode == 1
    assert "ISS-14 is not merged into main yet" in res.stdout + res.stderr


def test_resume_switches_back_and_merges_main(repo: Repo) -> None:
    _open_task(repo)
    repo.git("switch", "-q", "-c", ISSUE_BRANCH, "origin/main")
    fix = repo.commit("feature_list.json", _plan(issues=[_interrupting_issue()]))
    repo.git("push", "-q", "origin", f"{ISSUE_BRANCH}:main")
    res = repo.issue("resume")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.git("branch", "--show-current") == TASK_BRANCH
    assert repo.git("merge-base", "--is-ancestor", fix, "HEAD", check=False) == ""


def test_resume_reports_a_conflict_and_leaves_the_merge(repo: Repo) -> None:
    _open_task(repo)
    repo.git("switch", "-q", "-c", ISSUE_BRANCH, "origin/main")
    repo.commit("src/a.py", "a = 2\n")
    repo.commit("feature_list.json", _plan(issues=[_interrupting_issue()]))
    repo.git("push", "-q", "origin", f"{ISSUE_BRANCH}:main")
    res = repo.issue("resume")
    assert res.returncode == 1
    assert "conflicts: the merge is left in progress for the user" in res.stdout + res.stderr


# ── new + publish ────────────────────────────────────────────────────────────

GOOD_DRAFT_BODY = (TOOLKIT / "lib/issue_template.md").read_text()


def _draft(repo: Repo, text: str, verdict: str = "DRAFT_APPROVED", seal: bool = True) -> None:
    progress = repo.root / "progress"
    progress.mkdir(exist_ok=True)
    (progress / "issue_draft.md").write_text(text)
    (progress / "issue_draft_review.md").write_text(f"# r\n\n**Verdict:** {verdict}\n")
    if seal:
        digest = hashlib.sha256(text.encode()).hexdigest()
        (progress / "issue_draft.sha256").write_text(f"{digest}  progress/issue_draft.md\n")


FRONT = "---\ntitle: fix the parser\nlabels: [bug]\npriority: now\n---\n"


def test_new_writes_the_draft_from_the_template(repo: Repo) -> None:
    res = repo.issue("new")
    assert res.returncode == 0, res.stdout + res.stderr
    text = (repo.root / "progress/issue_draft.md").read_text()
    assert text.startswith("---\ntitle: \nlabels: []\npriority: next\n---\n## Problem")
    assert repo.issue("new").returncode == 1  # never overwrites a draft


def test_publish_creates_the_approved_draft(repo: Repo) -> None:
    _draft(repo, FRONT + GOOD_DRAFT_BODY)
    res = repo.issue("publish")
    assert res.returncode == 0, res.stdout + res.stderr
    created = repo.gh_state()["issues"]["15"]
    assert created["title"] == "fix the parser"
    assert sorted(created["labels"]) == ["bug", "priority:now"]
    # the approved body, plus only the hidden attempt token (an HTML comment)
    assert created["body"].startswith(GOOD_DRAFT_BODY)  # the reviewed body, unchanged
    marker = created["body"][len(GOOD_DRAFT_BODY) :]
    assert re.fullmatch(r"<!-- harness-publish-[0-9a-f-]{36} -->\n", marker)
    assert not (repo.root / "progress/issue_draft.md").exists()


@pytest.mark.parametrize(
    ("text", "verdict", "seal", "error"),
    [
        (FRONT + GOOD_DRAFT_BODY, "CHANGES_REQUESTED", True, "not DRAFT_APPROVED"),
        (FRONT + GOOD_DRAFT_BODY, "DRAFT_APPROVED", False, "changed after its approval"),
        (
            FRONT + GOOD_DRAFT_BODY + "\nCo-Authored-By: x\n",
            "DRAFT_APPROVED",
            True,
            "agent attribution",
        ),
        (
            FRONT.replace("[bug]", "[question]") + GOOD_DRAFT_BODY,
            "DRAFT_APPROVED",
            True,
            "exactly one type label",
        ),
        (FRONT + "## Problem\nx\n", "DRAFT_APPROVED", True, "the draft is incomplete"),
    ],
)
def test_publish_refuses(repo: Repo, text: str, verdict: str, seal: bool, error: str) -> None:
    _draft(repo, text, verdict, seal)
    res = repo.issue("publish")
    assert res.returncode == 1
    assert error in res.stdout + res.stderr
    assert "15" not in repo.gh_state()["issues"]


def test_publish_refuses_a_draft_edited_after_its_seal(repo: Repo) -> None:
    _draft(repo, FRONT + GOOD_DRAFT_BODY)
    (repo.root / "progress/issue_draft.md").write_text(FRONT + GOOD_DRAFT_BODY + "more\n")
    res = repo.issue("publish")
    assert res.returncode == 1
    assert "changed after its approval" in res.stdout + res.stderr


# ── Codex review of increment 6: interruption and safety cases ──────────────


def _claim(repo: Repo) -> dict[str, Any]:
    path = repo.root / repo.git("rev-parse", "--git-path", "harness-claim-ISS-14")
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _write_claim(repo: Repo, claim: dict[str, Any]) -> None:
    path = repo.root / repo.git("rev-parse", "--git-path", "harness-claim-ISS-14")
    path.write_text(json.dumps(claim))


def test_queue_refuses_an_issue_assigned_to_someone_else(repo: Repo) -> None:
    repo.set_gh(assignees=["bob"])
    res = repo.issue("queue", "14")
    assert res.returncode == 1
    assert "already in progress (bob)" in res.stdout + res.stderr


def test_an_unreachable_origin_keeps_the_claim(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    token = _claim(repo)["token"]
    repo.git("remote", "set-url", "origin", str(repo.root / "nowhere.git"))
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "cannot reach origin" in res.stdout + res.stderr
    assert _claim(repo)["token"] == token


def test_start_interrupted_after_the_branch_is_resumed(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    claim = _claim(repo)
    claim["steps"] = [s for s in claim["steps"] if s not in ("branch", "header")]
    _write_claim(repo, claim)
    (repo.root / HEADER).unlink()  # crashed after the branch, before the header
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH
    assert (repo.root / HEADER).read_text().startswith("# ISS-14 Replace the mask\n")
    assert repo.issue("release", "14").returncode == 0  # and it can still be released


def test_start_refuses_an_untracked_file_in_the_way(repo: Repo) -> None:
    (repo.root / HEADER).parent.mkdir(parents=True)
    (repo.root / HEADER).write_text("mine\n")
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "uncommitted changes" in res.stdout + res.stderr
    assert (repo.root / HEADER).read_text() == "mine\n"
    assert not repo.remote_tag()


def test_release_failing_on_github_is_finished_by_a_rerun(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    res = repo.issue("release", "14", FAKE_GH_FAIL_EDIT="1")
    assert res.returncode == 1
    assert repo.remote_tag()  # the claim is still held: the tag goes last
    res = repo.issue("release", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    issue = repo.gh_state()["issues"]["14"]
    assert issue["assignees"] == []
    assert sorted(issue["labels"]) == ["priority:next", "refactor"]
    assert not repo.remote_tag()


def test_release_rerun_completes_the_missing_steps_even_without_the_tag(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    claim = _claim(repo)
    claim["releasing"] = True
    _write_claim(repo, claim)
    repo.git("push", "-q", "origin", ":refs/tags/claim/ISS-14")
    res = repo.issue("release", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    issue = repo.gh_state()["issues"]["14"]
    assert issue["assignees"] == []
    assert "status:in-progress" not in issue["labels"]
    assert repo.git("branch", "--show-current") == "main"
    assert not repo.git("branch", "--list", ISSUE_BRANCH)


def test_resume_refuses_a_dirty_tree(repo: Repo) -> None:
    _open_task(repo)
    repo.git("switch", "-q", "-c", ISSUE_BRANCH, "origin/main")
    repo.commit("feature_list.json", _plan(issues=[_interrupting_issue()]))
    repo.git("push", "-q", "origin", f"{ISSUE_BRANCH}:main")
    (repo.root / "scratch.txt").write_text("wip\n")
    res = repo.issue("resume")
    assert res.returncode == 1
    assert "uncommitted changes" in res.stdout + res.stderr
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH


def test_resume_refuses_an_in_progress_leaf_elsewhere(repo: Repo) -> None:
    _open_task(repo)
    repo.git("switch", "-q", "-c", "wip", "origin/main")
    repo.commit("feature_list.json", _plan("in_progress"))
    repo.git("switch", "-q", "-c", ISSUE_BRANCH, "origin/main")
    repo.commit("feature_list.json", _plan(issues=[_interrupting_issue()]))
    repo.git("push", "-q", "origin", f"{ISSUE_BRANCH}:main")
    res = repo.issue("resume")
    assert res.returncode == 1
    assert "1.1.1 on wip" in res.stdout + res.stderr


def test_a_squash_merge_with_spaces_in_file_names_closes_the_task(repo: Repo) -> None:
    repo.git("switch", "-q", "-c", TASK_BRANCH)
    repo.commit("src/a b.py", "a = 1\n")
    repo.git("push", "-q", "origin", TASK_BRANCH)
    repo.git("switch", "-q", "main")
    repo.git("merge", "-q", "--squash", TASK_BRANCH)
    repo.git("commit", "-qm", "squash")
    repo.git("push", "-q", "origin", "main")
    assert repo.issue("start", "14").returncode == 0


def test_pr_evidence_for_only_one_of_two_divergent_tips_keeps_the_task_open(repo: Repo) -> None:
    remote_tip = _open_task(repo)
    repo.commit("src/b.py", "b = 1\n")  # local tip, never pushed nor merged
    repo.git("switch", "-q", "main")
    state = repo.gh_state()
    state["prs"] = [
        {"head": TASK_BRANCH, "base": "main", "state": "merged", "headRefOid": remote_tip}
    ]
    repo.state.write_text(json.dumps(state))
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "a task is open" in res.stdout + res.stderr


# ── Codex review of increment 6, round 2 ─────────────────────────────────────


def test_release_rechecks_for_work_before_a_retried_branch_deletion(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    claim = _claim(repo)
    claim["releasing"] = True  # interrupted before its branch step
    _write_claim(repo, claim)
    (repo.root / HEADER).write_text("# ISS-14 Replace the mask\n- goal: written meanwhile\n")
    res = repo.issue("release", "14")
    assert res.returncode == 1
    assert "release would lose work" in res.stdout + res.stderr
    assert "written meanwhile" in (repo.root / HEADER).read_text()


def test_release_clears_a_staged_header(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    repo.git("add", HEADER)
    res = repo.issue("release", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.git("status", "--porcelain") == ""
    assert repo.git("branch", "--show-current") == "main"


def test_start_refuses_a_pre_existing_issue_branch(repo: Repo) -> None:
    repo.git("branch", ISSUE_BRANCH)
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert f"a branch named {ISSUE_BRANCH} already exists" in res.stdout + res.stderr
    assert not repo.remote_tag()
    assert repo.git("branch", "--list", ISSUE_BRANCH)  # untouched


def test_start_after_an_unknown_push_outcome_keeps_its_claim(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    claim = _claim(repo)
    claim["steps"] = []  # the tag push landed, but its outcome was never recorded
    _write_claim(repo, claim)
    tag = repo.remote_tag()
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.remote_tag() == tag


def test_release_with_an_unreachable_origin_keeps_the_claim(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    token = _claim(repo)["token"]
    repo.git("remote", "set-url", "origin", str(repo.root / "nowhere.git"))
    res = repo.issue("release", "14")
    assert res.returncode == 1
    assert "cannot reach origin" in res.stdout + res.stderr
    assert _claim(repo)["token"] == token
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH


def test_a_deleted_unmerged_task_branch_is_abandoned_work(repo: Repo) -> None:
    repo.git("switch", "-q", "-c", TASK_BRANCH)
    repo.commit("src/a.py", "a = 1\n")
    repo.git("switch", "-q", "main")
    repo.git("branch", "-q", "-D", TASK_BRANCH)  # never pushed, deleted unmerged
    assert repo.issue("start", "14").returncode == 0


def test_a_branch_created_by_someone_else_meanwhile_is_never_adopted(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    claim = _claim(repo)
    repo.git("branch", ISSUE_BRANCH, claim["base"])  # same name, same base, not ours
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert f"a branch named {ISSUE_BRANCH} already exists and is not this claim's" in (
        res.stdout + res.stderr
    )
    repo.issue("release", "14")
    assert repo.git("branch", "--list", ISSUE_BRANCH)  # left intact


def test_the_issue_branch_reflog_carries_the_claim_token(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    log = repo.git("reflog", "show", "--format=%gs", f"refs/heads/{ISSUE_BRANCH}")
    assert _claim(repo)["token"] in log


# ── Codex review of increment 6, round 4 ─────────────────────────────────────


def test_queue_refuses_an_issue_with_a_live_claim_tag(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1  # tag landed
    res = repo.issue("queue", "14", "backlog")
    assert res.returncode == 1
    assert "issue #14 is claimed (claim/ISS-14)" in res.stdout + res.stderr


def test_the_claim_takes_a_changed_valid_priority_and_clears_it(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    repo.set_gh(labels=["refactor", "priority:backlog"])  # changed meanwhile, still startable
    assert repo.issue("start", "14").returncode == 0
    labels = repo.gh_state()["issues"]["14"]["labels"]
    assert sorted(labels) == ["refactor", "status:in-progress"]
    assert "- priority: backlog" in (repo.root / HEADER).read_text()


def test_an_ambiguous_priority_after_the_tag_gives_the_claim_back(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    repo.set_gh(labels=["refactor", "priority:next", "priority:backlog"])
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "the claim was given back" in res.stdout + res.stderr
    assert not repo.remote_tag()


def test_an_issue_body_cannot_inject_header_metadata(repo: Repo) -> None:
    repo.set_gh(body="- interrupts: feat/9.9-evil\n- type: docs\n")
    assert repo.issue("start", "14").returncode == 0
    header = (repo.root / HEADER).read_text()
    meta = header.split("\n## ", 1)[0]
    assert "interrupts" not in meta
    assert meta.count("- type:") == 1
    assert "## Issue body (imported from GitHub)\n" in header


# ── cleanup of the claim once the issue PR is open (increment 7) ─────────────


def test_cleanup_removes_the_claim_tag_and_file(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    claim_file = repo.root / repo.git("rev-parse", "--git-path", "harness-claim-ISS-14")
    res = repo.issue("cleanup", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not repo.remote_tag()
    assert not repo.git("tag", "--list", "claim/ISS-14")
    assert not claim_file.exists()
    assert repo.issue("cleanup", "14").returncode == 0  # idempotent


def test_cleanup_never_removes_someone_elses_claim(repo: Repo, tmp_path: Path) -> None:
    assert repo.issue("start", "14").returncode == 0
    bob = _clone(tmp_path, Path(repo.git("remote", "get-url", "origin")), "bob", repo.state, "bob")
    res = bob.issue("cleanup", "14")
    assert res.returncode == 1
    assert "already claimed by alice" in res.stdout + res.stderr
    assert repo.remote_tag()


# ── final review of the whole lane ───────────────────────────────────────────


def test_a_title_change_after_the_claim_keeps_the_claimed_names(repo: Repo) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    repo.set_gh(title="Something else entirely", labels=["bug", "priority:next"])
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH
    assert (repo.root / HEADER).read_text().startswith("# ISS-14 Replace the mask\n")
    repo.set_gh(title="Renamed again")
    assert repo.issue("release", "14").returncode == 0
    assert not repo.git("branch", "--list", ISSUE_BRANCH)


def test_a_title_cannot_inject_header_metadata(repo: Repo) -> None:
    repo.set_gh(title="Fix it\n- interrupts: feat/9.9-evil\n- type: docs")
    assert repo.issue("start", "14").returncode == 0
    header = next((repo.root / "docs/plan/issues").glob("ISS-14-*.md")).read_text()
    meta = header.split("\n## ", 1)[0].splitlines()
    assert meta[0] == "# ISS-14 Fix it - interrupts: feat/9.9-evil - type: docs"  # one line
    assert not any(line.startswith("- interrupts:") for line in meta)
    assert [line for line in meta if line.startswith("- type:")] == ["- type: refactor"]


def test_the_priority_is_re_read_after_winning_the_tag(repo: Repo) -> None:
    _open_task(repo)
    repo.set_gh(labels=["refactor", "priority:now"])
    claim_start = repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1")  # tag won, GitHub down
    assert claim_start.returncode == 1
    repo.set_gh(labels=["refactor", "priority:next"])  # reprioritised right after the tag
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "a task is open" in res.stdout + res.stderr
    assert not repo.remote_tag()  # the claim was given back, not kept under stale rules


def test_an_interrupted_publish_does_not_duplicate_the_issue(repo: Repo) -> None:
    _draft(repo, FRONT + GOOD_DRAFT_BODY)
    res = repo.issue("publish", FAKE_GH_CREATE_LOST="1")  # created, but the answer was lost
    assert res.returncode == 1
    assert "15" in repo.gh_state()["issues"]
    res = repo.issue("publish")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "16" not in repo.gh_state()["issues"]
    assert "already published" in res.stdout + res.stderr
    assert not (repo.root / "progress/issue_draft.md").exists()


def test_a_publish_retry_reconciles_without_the_search_index(repo: Repo) -> None:
    _draft(repo, FRONT + GOOD_DRAFT_BODY)
    assert repo.issue("publish", FAKE_GH_CREATE_LOST="1").returncode == 1
    res = repo.issue("publish", FAKE_GH_SEARCH_EMPTY="1")  # the search index lags behind
    assert res.returncode == 0, res.stdout + res.stderr
    assert "16" not in repo.gh_state()["issues"]
    creates = [c for c in repo.gh_state()["calls"] if c[:2] == ["issue", "create"]]
    assert len(creates) == 1


@pytest.mark.parametrize(
    "labels", [["refactor", "priority:soon"], ["refactor", "priority:now", "priority:next"]]
)
def test_start_needs_exactly_one_valid_priority_label(repo: Repo, labels: list[str]) -> None:
    repo.set_gh(labels=labels)
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "exactly one priority label (now, next or backlog)" in res.stdout + res.stderr
    assert not repo.remote_tag()


# ── final review, round 3 ────────────────────────────────────────────────────


def test_a_publish_retry_finds_its_issue_behind_many_newer_ones(repo: Repo) -> None:
    _draft(repo, FRONT + GOOD_DRAFT_BODY)
    assert repo.issue("publish", FAKE_GH_CREATE_LOST="1").returncode == 1
    state = repo.gh_state()
    for n in range(16, 140):  # more than 100 newer issues, by someone else
        state["issues"][str(n)] = {"title": f"other {n}", "body": "x", "author": "bob"}
    state["next"] = 140
    repo.state.write_text(json.dumps(state))
    res = repo.issue("publish")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "already published" in res.stdout + res.stderr
    assert "140" not in repo.gh_state()["issues"]


@pytest.mark.parametrize("change", [{"state": "CLOSED"}, {"assignees": ["bob"]}])
def test_an_issue_closed_or_taken_after_the_tag_gives_the_claim_back(
    repo: Repo, change: dict[str, Any]
) -> None:
    assert repo.issue("start", "14", FAKE_GH_FAIL_EDIT="1").returncode == 1
    repo.set_gh(**change)
    res = repo.issue("start", "14")
    assert res.returncode == 1
    assert "the claim was given back" in res.stdout + res.stderr
    assert not repo.remote_tag()


def test_a_title_without_letters_gets_a_fallback_slug(repo: Repo) -> None:
    repo.set_gh(title="!!! ???")
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.git("branch", "--show-current") == "refactor/ISS-14-issue"
    assert (repo.root / "docs/plan/issues/ISS-14-issue.md").is_file()


# ── final review, round 4 ────────────────────────────────────────────────────


def test_the_fallback_slug_is_the_branch_the_guard_expects(repo: Repo) -> None:
    repo.set_gh(title="!!! ???")
    assert repo.issue("start", "14").returncode == 0
    (repo.root / "docs/plan/PLAN.md").write_text("# Plan\n")
    sync = subprocess.run(
        ["python3", str(TOOLKIT / "lib/sync_plan.py")],
        cwd=repo.root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert sync.returncode == 0, sync.stdout + sync.stderr
    res = subprocess.run(
        ["python3", str(TOOLKIT / "lib/_harness.py"), "branch", "ISS-14"],
        cwd=repo.root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.stdout.strip() == "refactor/ISS-14-issue" == repo.git("branch", "--show-current")


def test_release_keeps_a_branch_whose_commits_already_reached_main(repo: Repo) -> None:
    assert repo.issue("start", "14").returncode == 0
    repo.commit("src/fix.py", "x = 1\n", "work")
    repo.git("push", "-q", "origin", f"{ISSUE_BRANCH}:main")  # the work is on main now
    repo.git("fetch", "-q", "origin")
    res = repo.issue("release", "14")
    assert res.returncode == 1
    assert "has commits" in res.stdout + res.stderr
    assert repo.git("branch", "--list", ISSUE_BRANCH)
    assert repo.remote_tag()


def test_a_github_edit_applied_but_unanswered_is_finished_by_the_retry(repo: Repo) -> None:
    res = repo.issue("start", "14", FAKE_GH_EDIT_LOST="1")
    assert res.returncode == 1
    issue = repo.gh_state()["issues"]["14"]
    assert "status:in-progress" in issue["labels"]  # it was applied
    res = repo.issue("start", "14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert repo.remote_tag()
    assert repo.git("branch", "--show-current") == ISSUE_BRANCH


def test_publish_sends_the_reviewed_title_and_body_unchanged(repo: Repo) -> None:
    body = GOOD_DRAFT_BODY + "\n\n"  # trailing blank lines are part of the reviewed text
    _draft(repo, "---\ntitle: Fix #14 parsing\nlabels: [bug]\npriority: next\n---\n" + body)
    res = repo.issue("publish")
    assert res.returncode == 0, res.stdout + res.stderr
    created = repo.gh_state()["issues"]["15"]
    assert created["title"] == "Fix #14 parsing"
    assert created["body"].startswith(body)
    assert re.fullmatch(r"<!-- harness-publish-[0-9a-f-]{36} -->\n", created["body"][len(body) :])
