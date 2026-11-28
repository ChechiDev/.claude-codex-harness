# Design: releases and CHANGELOG (delivery 1)

Status: approved by the user (2026-11-26). Delivery 1 covers install and
activation, changelog fragments, the release discovery and its gate, the guard
protections and the consolidation into `CHANGELOG.md`. Delivery 2 (version bump,
release PR, publication, GitHub Release, the "merged but not published" warning)
is out of scope here.

## 1. Files

| Path | Owner | Written by | Notes |
|------|-------|------------|-------|
| `CHANGELOG.md` | project | install / activation (once), consolidation | never overwritten, not even with `--force`; not a managed file in `MANIFEST.toml` |
| `changelog.d/README.md` | project | install / activation (once) | explains the fragment format |
| `changelog.d/<id>.md` | project | Codex, at the last close of task `N.M` or issue `ISS-<n>` | one fragment per task or issue, part of its PR |
| `.harness/changelog.json` | harness | install / activation | the activation record: the project is **activated** when it exists |
| `docs/releases.md` | project | claude-planner (release discovery) | reviewed by Codex, approved by the user |
| `progress/releases_review.md` | harness | Codex | `RELEASES_APPROVED` \| `CHANGES_REQUESTED` |
| `progress/releases.sha256` | harness | the gate on approval (or the human) | seal: digest of `docs/releases.md` + the roadmap seal it was approved with |

Claude (leader, planner, implementer) can never write `.harness/changelog.json`,
the releases verdict or seal, and never runs the activation or the consolidation
command (guard, Write and Bash). In an **activated** project (§2; an unreadable
record counts as activated) it can't write `CHANGELOG.md` or `changelog.d/` either;
before activation a started project keeps its own `CHANGELOG.md` and `changelog.d/`
(towncrier, …) exactly as they were. The claude-planner may write `docs/releases.md`.

## 2. Activation

- **New install** (`install.sh <dir>` with no harness evidence: no
  `.harness/templates.json`, `feature_list.json` or `harness` link, and not both
  `docs/plan/PLAN.md` and `progress/current.md`):
  copies `CHANGELOG.md`, `changelog.d/README.md` and `docs/releases.md` (template)
  and writes the record (`"since": null`: everything is recorded from the start).
- **Started project**: `install.sh <project> --changelog` (dry-run: prints the
  plan) · `--apply` (shows the plan, confirms, then confirms the merge) ·
  `--apply --yes` (no questions, e.g. `!` in the Claude console). Human only.
  1. Works in a temporary git worktree of the base branch (`origin/HEAD`, else
     `main`): the user's checkout is never touched.
  2. Branch `chore/harness-changelog`; adds the files above (an existing
     `CHANGELOG.md` is kept and the command prints the lines to add) and the
     record with `since` = the base commit and `finished` = the tasks and issues
     already complete there (they never need a fragment).
  3. One commit `chore: activate the harness changelog` (no agent attribution),
     push, PR; merges it (`gh pr merge --match-head-commit <pushed sha>`) only once
     every check is green. A later push to the branch makes the merge refuse; a repo
     with no checks at all is never merged: the command prints the merge for the human.
  4. Relaunchable: an activated base is a no-op; an existing branch, commit or PR
     is reused, never duplicated. A branch of that name holding anything else
     (more than one commit, another subject, other paths) stops it untouched.
- **Not activated**: nothing changes but one `make check` WARN (section 8, with
  the migration level) that prints the command; `make status` is unchanged. The
  leader shows the command and keeps working.

A branch is activated when its tree has the record, or when the base branch has
it (`origin/HEAD`, `origin/main`, `main`): a task in flight at activation writes
its fragment without merging `main` first.

## 3. Declaration (kickoff) and fragment (last close)

Every subtask spec written at a kickoff in an activated project declares, after
`files:`/`acceptance:`:

```
- changelog: added, fixed        (added | changed | fixed | removed | security, or none: <why>)
- incompatible: no               (no | yes: <what breaks for users>)
```

The declaration of a task or issue is the union of its specs. The task and issue
gates refuse to run in an activated project while a non-baseline spec lacks it
(`_harness.py task-ready` / `issue-ready`; for a phase broken down whole, with no
task gate, the phase gate `phase-ready`); specs approved before activation are
never re-gated for it: a re-gate (replan, coupled re-gate) skips a `done` spec and
one first committed before the activation commit and not on top of it (a task in
flight at the activation), and asks only the specs written after. A malformed value
fails `make check` (activated only).

At the **last close** of a task or issue, Codex (documenter) contrasts the
declaration with what was delivered and writes `changelog.d/<id>.md`:

```
- id: 1.2
- incompatible: no                (or yes: <what breaks>)
- none: <why>                     (only when nothing user-visible; then no sections)

### Added
- One user-facing line per change.
### Fixed
- …
```

Sections: `Added`, `Changed`, `Fixed`, `Removed`, `Security`. A task in flight
at activation has no declaration: Codex writes the fragment from what was
delivered, with no re-gate and no seal touched.

`make check` (activated): a close that completes a task or issue (its leaves are
complete in the work tree but not in `HEAD`) **fails** without a valid fragment,
so the close stops before its commit. The close itself checks it too (`_changelog.py
fragment <id>`), and the doc repair mode (issue #133) lets Codex write only that
fragment (plus the doc if it also fails); out of repairs, `needs human`. A task or issue completed earlier without a
fragment (not in `finished`, not consolidated) only warns.

## 4. Consolidation

`python3 harness/_changelog.py consolidate [--apply]` (dry-run by default) moves
every fragment into the `## [Unreleased]` section of `CHANGELOG.md`, grouped by
section (`Incompatible`, then Added, Changed, Removed, Fixed, Security), each line
suffixed ` (<id>)`, plus one hidden marker `<!-- changelog.d/<id>.md <sha12> -->`
per fragment (the digest of the file it read); then deletes exactly the files it
read, only if unchanged since (one written or edited meanwhile stays).
`CHANGELOG.md` is replaced atomically before any fragment is deleted. A fragment
whose marker and digest are already there is only deleted (idempotent, no
duplicates, no loss after an interruption); one with the marker but another digest
(edited after it was consolidated) stops it. An invalid fragment stops it before
anything is written.

## 5. Release discovery and its gate

After the roadmap is approved, in an activated project only, `make status` adds
`Also: release discovery` (never the `Next:` line): the leader proposes it to the
user and continues if they postpone it. No execution gate depends on it.

`docs/releases.md`:

```
# Releases
- applies: yes                      (yes | no: <why>)
- versioning: semver                (semver | <scheme>: <why>)
- version source: pyproject.toml    (Python: pyproject.toml [project] version; C and others: VERSION)
- audience: <who receives the releases>

## Planned releases
| release | phases | goal |
|---------|--------|------|
| 0.1.0   | 01, 02 | <what the user gets> |

## Compatibility
<SemVer policy; in 0.x an incompatible change bumps MINOR>

## Patches and early releases
<when a patch or an early release is cut>
```

`applies: no: <why>` needs no other field. Gate:
`review_with_codex.sh releases` (codex-planner) → `RELEASES_APPROVED` → seal →
the user's OK. States: `missing`, `draft` (placeholders or a missing field),
`unreviewed`, `rejected`, `unsealed`, `approved`, `changed`. Any change after the
seal (`changed`) only warns in `make check`, `make status` and resume: only
publishing (delivery 2) requires an approved, current plan.

Review triggers (WARN, checked): `docs/releases.md` changed; the roadmap seal
changed since the releases seal (scope, phases); a fragment marked incompatible
whose id `docs/releases.md` does not mention. Declarative (the leader's rules):
a change of version policy or compatibility, a patch or early release, a switch
to or from `applies: no`.
