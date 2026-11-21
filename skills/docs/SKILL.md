---
name: docs
description: "Shared standard: Trigger: docs, documentation, README, readme, project docs, docs/, document the project, architecture docs, usage guide. Standard for README and project docs/ structure, sections, and tone."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.0"
---

## Activation Contract

Shared standard. Load when:
- Writing or updating a project `README.md`
- Writing or reviewing files under `docs/`
- Asked to document a project, feature, or architecture
- Reviewing whether existing documentation is well-formed

README and `docs/` are **distinct artifacts** — see Decision Gates before writing.

## Hard Rules

1. **README is the entry point, not the manual.** It answers: what is this, why it exists, how to run it in under 5 minutes, where to go next. Deep detail lives in `docs/`, linked from the README.
2. **`docs/` is the manual.** One topic per file, named by topic (`docs/architecture.md`, `docs/configuration.md`, `docs/deployment.md`). No dumping everything into one file.
3. **All documentation in English.** Headings, prose, code comments in snippets.
4. **Show, then tell.** Every "how" section has a runnable command or minimal code block. No instruction without an executable example.
5. **No marketing prose.** State what it does and how. No "blazing-fast", "powerful", "simple" adjectives.
6. **Keep it current or delete it.** Stale docs are worse than none. Do not document aspirational behavior that does not exist yet.
7. **Link, don't duplicate.** A fact lives in exactly one file; everything else links to it.
8. **Every code block is copy-paste runnable** with real commands/paths, not placeholders unless clearly marked `<like-this>`.

## Decision Gates

| Content | Goes in |
|---------|---------|
| What/why, quickstart, install, first run, links | `README.md` |
| Architecture, design decisions, module maps | `docs/architecture.md` |
| Config options, env vars, settings | `docs/configuration.md` |
| Deploy, CI/CD, release process | `docs/deployment.md` |
| API/usage reference, examples, recipes | `docs/usage.md` |
| Long rationale behind a README claim | `docs/` file, linked from README |

## README Structure

```
# Project Name

One-sentence description of what it does.

## Install
<exact commands>

## Quickstart
<minimal runnable example — first success in under 5 min>

## Documentation
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)

## License
```

## Execution Steps

1. Decide README vs `docs/` using Decision Gates.
2. For a README: write description → install → quickstart → links. Stop; do not inline deep detail.
3. For `docs/`: one topic per file; open with the problem it addresses, then the concrete how.
4. Add a runnable command/code block to every "how" section.
5. Link related files both ways; verify every link resolves.
6. Read it as a newcomer: can they run it from zero? If not, fix the gap.

## Output Contract

- README: description, install, quickstart, documentation links, license — no deep detail inlined.
- `docs/`: one topic per file, topic-named, each "how" has a runnable example.
- All English, no marketing adjectives, every link resolves, no stale/aspirational claims.

## References

- `../py-style/SKILL.md` — code-level docstring and comment conventions (distinct from project docs)
