#!/usr/bin/env python3
"""Codex review effort and checklists by risk and surface (issue #44).

A subtask spec may declare, among its `- key: value` lines (before its first `##`):

  - risk: high                 low | medium | high
  - surfaces: security, data   names of harness.toml [review.surfaces.<name>] (or none)

harness.toml [review.risk.<level>] / [review.surfaces.<name>] (see _config.py) map them
to a codex_effort and extra checklists (codex/checklist-<name>.md) that the subtask's
plan and code gates apply. The policy is conservative, so a low rating cannot buy a
lighter review (risk washing):

- the effort is the highest of the reviewer role's own and every rule that applies:
  never below the role's, whatever the rating or the table says;
- the rating is a floor raised by the files, not only by the spec: a `files:` entry
  matching [sessions] high_risk_paths makes the risk high, one matching a surface's
  `paths` adds that surface;
- the rating sealed by the phase/task/issue gate (_plan_seal.py `rating:` entries) is
  a floor too: a lowered rating fails `make check` until that gate re-seals it;
- an invalid value or an unknown surface fails closed (the gate stops, `make check`
  fails). A spec without the fields reviews exactly as before.

Issue #107: the phase, task and issue gates review the specs of their subtasks before
any of them reaches its plan gate, so they take the policy of the strictest rating
among those subtasks (each resolved as above); an open subtask whose rating cannot
be trusted stops them, a done/baseline one is exempt (as in `make check`). Their
subtasks and statuses come from docs/plan/ itself, not from feature_list.json, so a
stale index cannot hide a spec the gate reviews.

Usage: python3 _review_risk.py policy <N.M.K|ISS-n.k> <role effort> <codex dir>
       python3 _review_risk.py gate-policy <plan|code|phase|task|issue> <id> <role effort>
                                <codex dir>
  prints the effort, then the reasons (one line, may be empty), then one checklist
  path per line; [FAIL] and exit 1 when the policy cannot be trusted.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import _config
import _harness
import _session_launch
from _status import say

RE_FIELD = re.compile(r"^-\s+(risk|surfaces):[ \t]*(.*?)[ \t]*$", re.IGNORECASE)
RE_FIRST_SECTION = re.compile(r"^## ", re.MULTILINE)
RISK_RANK = {risk: n for n, risk in enumerate(_config.RISKS)}
EFFORT_RANK = {effort: n for n, effort in enumerate(_config.CODEX_EFFORTS)}
COMPLETE = ("done", "baseline")
LEAF_GATES = ("plan", "code")
CONTAINER_GATES = ("phase", "task", "issue")


class RatingError(Exception):
    """A rating or a review policy that cannot be trusted: the gate fails closed."""


@dataclass(frozen=True)
class Rating:
    risk: str | None = None
    surfaces: tuple[str, ...] = ()  # sorted, unique

    def empty(self) -> bool:
        return self.risk is None and not self.surfaces

    def token(self) -> str:
        """The rating as one word, as the plan seals store it: `high;data,security`."""
        return f"{self.risk or '-'};{','.join(self.surfaces) or '-'}"

    @classmethod
    def from_token(cls, token: str) -> Rating:
        risk, sep, surfaces = token.partition(";")
        names = () if surfaces == "-" else tuple(surfaces.split(","))
        if (
            not sep
            or (risk != "-" and risk not in RISK_RANK)
            or not all(_config.RE_RULE_NAME.fullmatch(n) for n in names)
        ):
            raise RatingError(f"invalid sealed rating {token!r}")
        return cls(None if risk == "-" else risk, tuple(sorted(set(names))))

    def covers(self, other: Rating) -> bool:
        """At least as strict as `other`: same or higher risk, every surface of it."""
        mine = -1 if self.risk is None else RISK_RANK[self.risk]
        theirs = -1 if other.risk is None else RISK_RANK[other.risk]
        return mine >= theirs and set(other.surfaces) <= set(self.surfaces)

    def merge(self, other: Rating) -> Rating:
        risk = self.risk if self.covers(Rating(other.risk)) else other.risk
        return Rating(risk, tuple(sorted({*self.surfaces, *other.surfaces})))


@dataclass(frozen=True)
class Policy:
    effort: str
    checklists: tuple[Path, ...]
    reasons: tuple[str, ...]


def _surfaces(value: str) -> tuple[str, ...]:
    if value.lower() == "none":
        return ()
    names = [n.strip() for n in value.split(",")]
    for name in names:
        if not _config.RE_RULE_NAME.fullmatch(name):
            raise RatingError(f"`- surfaces:` has an invalid name {name!r} (a-z, 0-9, -)")
    return tuple(sorted(set(names)))


def parse_rating(text: str) -> Rating:
    """The `- risk:` / `- surfaces:` lines of a spec (before its first `##`)."""
    found: dict[str, str] = {}
    for line in RE_FIRST_SECTION.split(text, maxsplit=1)[0].splitlines():
        m = RE_FIELD.match(line)
        if not m:
            continue
        key = m.group(1).lower()
        if key in found:
            raise RatingError(f"`- {key}:` is declared twice")
        found[key] = m.group(2)
    risk = found.get("risk")
    if risk is not None and risk not in RISK_RANK:
        raise RatingError(f"`- risk: {risk}` must be one of {', '.join(_config.RISKS)}")
    return Rating(risk, _surfaces(found["surfaces"]) if "surfaces" in found else ())


def spec_rating(root: Path, spec: str) -> Rating:
    """The rating a spec declares now; none when the spec file is missing."""
    if not spec:
        return Rating()
    try:
        text = (root / spec).read_text(encoding="utf-8")
    except FileNotFoundError:
        return Rating()
    except (OSError, UnicodeDecodeError) as exc:
        raise RatingError(f"{spec} is unreadable: {exc}") from exc
    try:
        return parse_rating(text)
    except RatingError as exc:
        raise RatingError(f"{spec}: {exc}") from exc


def _files(leaf: _harness.Item) -> list[str]:
    out = []
    for rel in leaf.get("files") or []:
        try:
            out.append(_session_launch._normalize(str(rel)))
        except ValueError as exc:
            raise RatingError(f"{leaf['id']}: {exc}") from exc
    return out


def from_files(leaf: _harness.Item, cfg: _config.Config) -> list[tuple[Rating, str]]:
    """What the leaf's files: imply, whatever its spec says, with the reason."""
    found: list[tuple[Rating, str]] = []
    files = _files(leaf)
    for glob in cfg.session_high_risk_paths:
        path = next((p for p in files if _session_launch.matches(p, glob)), None)
        if path is not None:
            why = f"risk high (files: {path} matches [sessions] high_risk_paths {glob})"
            found.append((Rating("high"), why))
            break
    for name, rule in sorted(cfg.review_surfaces.items()):
        hit = next(
            ((p, g) for g in rule.paths for p in files if _session_launch.matches(p, g)), None
        )
        if hit is not None:
            why = f"surface {name} (files: {hit[0]} matches its paths {hit[1]})"
            found.append((Rating(None, (name,)), why))
    return found


def _describe(rating: Rating, source: str) -> list[str]:
    out = [f"risk {rating.risk} ({source})"] if rating.risk else []
    return out + [f"surface {s} ({source})" for s in rating.surfaces]


def _leaf(item_id: str, data: _harness.Item | None = None) -> _harness.Item:
    leaf = next((x for x in _harness.leaves(data) if str(x["id"]) == item_id), None)
    if leaf is None:
        raise RatingError(f"unknown subtask {item_id}")
    return leaf


def resolve(
    root: Path, item_id: str, cfg: _config.Config, data: _harness.Item | None = None
) -> tuple[Rating, list[str]]:
    """The effective rating of a subtask's gates and why: the highest of its spec, its
    sealed rating and its files. `data`: the plan to read (default feature_list.json)."""
    import _plan_seal  # lazy: _plan_seal imports this module

    leaf = _leaf(item_id, data)
    spec = str(leaf.get("spec") or "")
    rating = spec_rating(root, spec)
    reasons = _describe(rating, "declared")
    owner = str(leaf.get("issue") or leaf.get("phase") or "")
    sealed = _plan_seal.sealed_rating(root, spec, owner) if spec and owner else None
    if sealed is not None and not rating.covers(sealed):
        reasons += _describe(sealed, "sealed by its phase/task/issue gate")
        rating = rating.merge(sealed)
    for implied, why in from_files(leaf, cfg):
        if not rating.covers(implied):
            rating = rating.merge(implied)
        reasons.append(why)
    unknown = sorted(set(rating.surfaces) - set(cfg.review_surfaces))
    if unknown:
        raise RatingError(
            f"{item_id}: unknown surface(s) {', '.join(unknown)} "
            f"(known: {', '.join(sorted(cfg.review_surfaces))}; harness.toml "
            "[review.surfaces.<name>] adds one)"
        )
    return rating, reasons


def _highest(base: str, efforts: list[str]) -> str:
    best = base
    for effort in efforts:
        if effort and EFFORT_RANK[effort] > EFFORT_RANK.get(best, -1):
            best = effort
    return best


def source_plan(root: Path) -> _harness.Item | None:
    """The plan as docs/plan/ says now (what the phase/task/issue gate reviews), parsed
    as `make plan` would, without writing anything; None without docs/plan/."""
    import sync_plan  # lazy: sync_plan imports _plan_seal, which imports this module

    plan_dir = root / sync_plan.PLAN_DIR
    if not plan_dir.is_dir():
        return None
    parser = sync_plan.PlanParser(plan_dir)
    parser.parse()
    if parser.errors:
        raise RatingError(f"docs/plan does not parse ({parser.errors[0]}): fix it, then make plan")
    data = sync_plan.build(parser)
    # Spec paths project-relative, as feature_list.json and the plan seals name them
    # (the sealed rating is looked up by that exact path).
    for owner in [*data.get("features", []), *data.get("issues", [])]:
        for sub in owner.get("subtasks") or []:
            spec = Path(str(sub.get("spec") or ""))
            if spec.is_absolute():
                sub["spec"] = spec.relative_to(root).as_posix()
    return data


def members(gate: str, cid: str, data: _harness.Item | None = None) -> list[_harness.Item]:
    """The subtasks a phase (NN), task (N.M) or issue (ISS-n) gate reviews."""
    every = _harness.leaves(data)
    if gate == "phase":
        return [x for x in every if str(x["phase"]) == cid]
    if gate == "task":
        return [x for x in every if _harness.parse_id(str(x["id"])) == ("task", cid)]
    return [x for x in every if str(x["issue"]) == cid]


def resolve_container(
    root: Path, gate: str, cid: str, cfg: _config.Config, codex_dir: Path
) -> tuple[Rating, list[str]]:
    """The strictest rating among the subtasks of a phase/task/issue gate, and why
    (each reason names its subtask), read from docs/plan/ (feature_list.json without
    it). An open subtask whose policy cannot be trusted stops the gate; a done/baseline
    one is left out, as problems() exempts it."""
    data = source_plan(root)
    rating, reasons = Rating(), []
    for leaf in members(gate, cid, data):
        sid = str(leaf["id"])
        try:
            found, why = resolve(root, sid, cfg, data)
            _apply(found, cfg, "", codex_dir)  # its own checklists must exist too
        except (RatingError, OSError, ValueError):
            if leaf["status"] in COMPLETE:
                continue
            raise
        rating = rating.merge(found)
        reasons += [f"{sid}: {w}" for w in why]
    return rating, reasons


def gate_policy(root: Path, gate: str, cid: str, base_effort: str, codex_dir: Path) -> Policy:
    """The effort and checklists of a plan/code gate (one subtask) or of a phase/task/
    issue gate (its subtasks). Raises RatingError."""
    if gate not in LEAF_GATES + CONTAINER_GATES:
        raise RatingError(f"the {gate} gate has no review policy")
    try:
        cfg = _config.load(root)
    except _config.ConfigError as exc:
        raise RatingError(str(exc)) from exc
    if gate in LEAF_GATES:
        rating, reasons = resolve(root, cid, cfg)
    else:
        rating, reasons = resolve_container(root, gate, cid, cfg, codex_dir)
    return _apply(rating, cfg, base_effort, codex_dir, tuple(reasons))


def _apply(
    rating: Rating,
    cfg: _config.Config,
    base_effort: str,
    codex_dir: Path,
    reasons: tuple[str, ...] = (),
) -> Policy:
    """The policy a rating gets from the table. Raises RatingError (missing checklist)."""
    if rating.empty():
        return Policy(base_effort, (), ())
    # Every level up to the effective one applies: a higher risk never reviews lighter
    # than a lower one, whatever the table gives each level.
    levels = _config.RISKS[: RISK_RANK[rating.risk] + 1] if rating.risk else ()
    rules = [cfg.review_risk[level] for level in levels]
    rules += [cfg.review_surfaces[s] for s in rating.surfaces]
    names = list(dict.fromkeys(c for rule in rules for c in rule.checklists))
    paths = tuple(codex_dir / f"checklist-{name}.md" for name in names)
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise RatingError(f"review checklist(s) not found: {', '.join(missing)}")
    effort = _highest(base_effort, [rule.effort for rule in rules])
    return Policy(effort, paths, reasons)


def policy(root: Path, item_id: str, base_effort: str, codex_dir: Path) -> Policy:
    """The effort and checklists of a subtask's plan/code gate. Raises RatingError."""
    return gate_policy(root, "code", item_id, base_effort, codex_dir)


def default_codex_dir(root: Path) -> Path:
    """Where the gate reads the checklists: the project's .claude/codex (the toolkit's
    codex/ when the project has no link, as in the toolkit's own tests)."""
    linked = root / ".claude" / "codex"
    return linked if linked.is_dir() else Path(__file__).resolve().parent.parent / "codex"


def problems(units: list[_harness.Item], root: Path) -> list[str]:
    """`make check` FAILs: the policy of an open subtask cannot be computed (invalid
    rating, unknown surface, missing checklist file). Done/baseline leaves are exempt:
    their specs predate the fields or never go through a gate again."""
    try:
        _config.load(root)
    except _config.ConfigError:
        return []  # config_problems reports it
    codex_dir = default_codex_dir(root)
    out = []
    for leaf in units:
        if leaf["status"] in COMPLETE:
            continue
        try:
            policy(root, str(leaf["id"]), "", codex_dir)
        except RatingError as exc:
            message = str(exc)
            out.append(message if message.startswith(f"{leaf['id']}") else f"{leaf['id']}: {exc}")
    return out


def warnings(units: list[_harness.Item], root: Path) -> list[str]:
    """WARN: an open subtask whose files: imply more than its spec declares (its gates
    apply the higher rating anyway; the spec should say so)."""
    try:
        cfg = _config.load(root)
    except _config.ConfigError:
        return []
    out = []
    for leaf in units:
        if leaf["status"] in COMPLETE:
            continue
        try:
            declared = spec_rating(root, str(leaf.get("spec") or ""))
            implied = from_files(leaf, cfg)
        except RatingError:
            continue  # problems() or the gate reports it
        for rating, why in implied:
            if not declared.covers(rating):
                out.append(
                    f"{leaf['id']}: its spec understates its review rating: {why}; "
                    "declare it in the spec (its gates apply it anyway)"
                )
    return out


def main(argv: list[str]) -> int:
    if argv[:1] == ["policy"] and len(argv) == 4:
        argv = ["gate-policy", "code", *argv[1:]]
    if len(argv) != 5 or argv[0] != "gate-policy":
        print(
            "usage: _review_risk.py policy <id> <role effort> <codex dir>\n"
            "       _review_risk.py gate-policy <gate> <id> <role effort> <codex dir>",
            file=sys.stderr,
        )
        return 2
    try:
        result = gate_policy(Path.cwd(), argv[1], argv[2], argv[3], Path(argv[4]))
    except RatingError as exc:
        say("FAIL", f"review policy: {exc}", sys.stderr)
        return 1
    print(result.effort)
    print("; ".join(result.reasons))
    for path in result.checklists:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
