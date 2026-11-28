# Releases

> Written in the **release discovery** (after the roadmap; leader ↔ user, one
> question at a time) by the claude-planner, reviewed by Codex
> (`./harness/review_with_codex.sh releases` → `progress/releases_review.md`) and
> approved by the user. Any change voids the approval (a WARN only: no gate of the
> work depends on it; publishing a release will).
>
> - `applies: no: <why>` when the project never ships releases (nothing else then).
> - SemVer by default; in 0.x an incompatible change bumps MINOR.
> - Phases mark *planned* releases; patches and early releases are allowed.

- applies: ‹yes | no: <why>›
- versioning: ‹semver | <scheme>: <why>›
- version source: pyproject.toml
- audience: ‹who receives the releases and how›

## Planned releases
| release | phases | goal |
|---------|--------|------|
| ‹0.1.0› | ‹01›   | ‹what the user gets› |

## Compatibility
‹the version policy: what counts as incompatible and how it bumps the version›

## Patches and early releases
‹when a patch or an early release is cut›
