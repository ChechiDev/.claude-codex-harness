# Changelog fragments

One file per finished task or issue, written by Codex at its last close and part
of its PR: `changelog.d/<N.M>.md` or `changelog.d/<ISS-n>.md`. The release
consolidates them into `CHANGELOG.md` (`python3 harness/_changelog.py consolidate`)
and removes them. Never edit them by hand.

```
- id: 1.2
- incompatible: no                (or yes: <what breaks for users>)

### Added
- One user-facing line per change.
### Fixed
- …
```

Sections: `Added`, `Changed`, `Fixed`, `Removed`, `Security`. A task or issue with
nothing user-visible writes `- none: <why>` instead of sections.
