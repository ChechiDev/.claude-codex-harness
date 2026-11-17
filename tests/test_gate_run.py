"""Code-gate evidence bound to the reviewed tree (_gate_run, factory 1C)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import _gate_run as gr
import pytest


def _attempt(**over: object) -> gr.GateAttempt:
    base = gr.GateAttempt(
        mode="code",
        subtask="1.2.3",
        check_rc=0,
        log_tail="ok",
        head="a" * 40,
        impl_fp="i" * 64,
        toolkit_fp="t" * 64,
        created_at="2026-11-16T00:00:00+00:00",
    )
    return replace(base, **over)  # type: ignore[arg-type]


# ── should_skip_review ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "rc", "skip"),
    [
        ("code", 0, False),
        ("code", 2, True),
        ("plan", 2, False),
        ("regate", 2, False),
        ("plan", 0, False),
    ],
)
def test_should_skip_review(mode: gr.Mode, rc: int, skip: bool) -> None:
    assert gr.should_skip_review(_attempt(mode=mode, check_rc=rc)) is skip


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError):
        _attempt(mode="bogus")


# ── implementation fingerprint ──────────────────────────────────────────────


def _tree(tmp_path: Path) -> list[str]:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src/app.py").write_text("x = 1\n")
    (tmp_path / "tests/test_app.py").write_text("def test(): pass\n")
    (tmp_path / "docs/review_1.2.3.md").write_text("APPROVED\n")
    return ["src/app.py", "tests/test_app.py", "docs/review_1.2.3.md"]


def test_impl_fp_ignores_docs(tmp_path: Path) -> None:
    files = _tree(tmp_path)
    fp = gr.implementation_fingerprint(tmp_path, files)
    (tmp_path / "docs/review_1.2.3.md").write_text("changed\n")
    (tmp_path / "docs/progress.md").write_text("new\n")
    assert gr.implementation_fingerprint(tmp_path, [*files, "docs/progress.md"]) == fp


@pytest.mark.parametrize("rel", ["src/app.py", "tests/test_app.py"])
def test_impl_fp_tracks_code_and_tests(tmp_path: Path, rel: str) -> None:
    files = _tree(tmp_path)
    fp = gr.implementation_fingerprint(tmp_path, files)
    (tmp_path / rel).write_text("changed\n")
    assert gr.implementation_fingerprint(tmp_path, files) != fp


def test_impl_fp_tracks_deletion(tmp_path: Path) -> None:
    files = _tree(tmp_path)
    fp = gr.implementation_fingerprint(tmp_path, files)
    (tmp_path / "src/app.py").unlink()
    assert gr.implementation_fingerprint(tmp_path, files) != fp


def test_impl_fp_order_independent(tmp_path: Path) -> None:
    files = _tree(tmp_path)
    assert gr.implementation_fingerprint(tmp_path, files) == gr.implementation_fingerprint(
        tmp_path, list(reversed(files))
    )


def test_impl_fp_rejects_escaping_paths(tmp_path: Path) -> None:
    _tree(tmp_path)
    with pytest.raises(ValueError):
        gr.implementation_fingerprint(tmp_path, ["../outside.py"])


def test_toolkit_fp_tracks_lib(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.py").write_text("1")
    fp = gr.toolkit_fingerprint(tmp_path)
    (lib / "a.py").write_text("2")
    assert gr.toolkit_fingerprint(tmp_path) != fp


# ── is_stale ────────────────────────────────────────────────────────────────


def test_not_stale_when_only_head_moves() -> None:
    approved = _attempt()
    assert not gr.is_stale(approved, replace(approved, head="b" * 40, log_tail="x"))


@pytest.mark.parametrize(
    "field", [{"impl_fp": "j" * 64}, {"toolkit_fp": "u" * 64}, {"subtask": "1.2.4"}]
)
def test_stale_when_reviewed_inputs_change(field: dict[str, str]) -> None:
    approved = _attempt()
    assert gr.is_stale(approved, replace(approved, **field))  # type: ignore[arg-type]


def test_stale_when_approved_on_red_check() -> None:
    approved = _attempt(check_rc=1)
    assert gr.is_stale(approved, approved)


# ── record / load ───────────────────────────────────────────────────────────


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "gate_run_1.2.3.json"
    att = _attempt()
    gr.record_attempt(path, att)
    assert gr.load_attempt(path) == att
    assert list(path.parent.iterdir()) == [path]


def test_atomic_write_keeps_old_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "gate_run.json"
    gr.record_attempt(path, _attempt())
    before = path.read_bytes()

    def boom(src: object, dst: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr("_gate_run.os.replace", boom)
    with pytest.raises(OSError):
        gr.record_attempt(path, _attempt(check_rc=1))
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", json.dumps({"mode": "code"}), json.dumps({"x": 1})],
)
def test_load_rejects_invalid(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "g.json"
    path.write_text(payload)
    with pytest.raises(gr.GateRunError):
        gr.load_attempt(path)


def test_load_rejects_wrong_types(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    gr.record_attempt(path, _attempt())
    data = json.loads(path.read_text())
    data["check_rc"] = "0"
    path.write_text(json.dumps(data))
    with pytest.raises(gr.GateRunError):
        gr.load_attempt(path)


def test_tail_trims() -> None:
    log = "\n".join(f"line{i}" for i in range(500))
    tail = gr.tail(log, lines=10)
    assert tail.splitlines() == [f"line{i}" for i in range(490, 500)]
    assert len(gr.tail("x" * 100_000, lines=10, max_chars=1000)) == 1000


# ── Codex review follow-ups ─────────────────────────────────────────────────


@pytest.mark.parametrize(("approved_mode", "current_mode"), [("plan", "code"), ("regate", "code")])
def test_stale_when_approval_is_not_a_code_review(approved_mode: str, current_mode: str) -> None:
    approved = _attempt(mode=approved_mode)
    assert gr.is_stale(approved, replace(approved, mode=current_mode))  # type: ignore[arg-type]


def test_impl_fp_ignores_progress(tmp_path: Path) -> None:
    files = _tree(tmp_path)
    (tmp_path / "progress").mkdir()
    (tmp_path / "progress/review_1.2.3.md").write_text("a\n")
    files = [*files, "progress/review_1.2.3.md"]
    fp = gr.implementation_fingerprint(tmp_path, files)
    (tmp_path / "progress/review_1.2.3.md").write_text("b\n")
    assert gr.implementation_fingerprint(tmp_path, files) == fp


def test_impl_fp_tracks_exec_bit(tmp_path: Path) -> None:
    files = _tree(tmp_path)
    fp = gr.implementation_fingerprint(tmp_path, files)
    (tmp_path / "src/app.py").chmod(0o755)
    assert gr.implementation_fingerprint(tmp_path, files) != fp


def test_toolkit_fp_tracks_reviewer_instructions(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex/reviewer.md").write_text("v1")
    fp = gr.toolkit_fingerprint(tmp_path)
    (tmp_path / "codex/reviewer.md").write_text("v2")
    assert gr.toolkit_fingerprint(tmp_path) != fp
