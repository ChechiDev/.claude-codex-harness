---
name: readme
description: "Shared standard: Trigger: README, readme, root README, README.md, document the project, project entry point, write a readme. Standard + template for the root README.md of an internal GitHub repo."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.2"
---

## Activation Contract

Shared standard. Load when:
- Writing or updating the root `README.md` of a project
- Reviewing whether a README is well-formed
- Asked to document a project for its GitHub users

Scope: the single root `README.md`. Deep topics (architecture, config, deploy)
live under `docs/` and are linked, not inlined. For `docs/` conventions see the
sibling `guide` standard (planned, not built yet).

## Hard Rules

1. **Entry point, not the manual.** README answers: what is this, what it needs, how to install, how to run it once, where to go next. Depth lives in `docs/`, linked.
2. **Mandatory sections, in order**: Description (the lead paragraph directly under the `# Title` — NOT a `## Description` heading) → Prerequisites → Install → Quickstart → Architecture → License (always, a single line, last). Documentation, Configuration and Development are optional (include only when they apply).
3. **Humanized technical tone.** Write as if onboarding a new teammate out loud: full sentences, plain language, precise. Technical accuracy first, but never telegraphic. No filler, no marketing adjectives ("blazing-fast", "powerful", "simple" are banned).
4. **No icons or emoji anywhere** — not in headings, not in lists, not as status markers.
5. **Internal repo — no public dressing.** No badges, no shields, no public "Contributing" section. License line is `Proprietary — internal use only.` unless told otherwise.
6. **Quickstart reaches first success in under 5 minutes** with copy-paste commands and real paths.
7. **Architecture carries two Mermaid diagrams** (see Decision Gates): a components diagram and a request/data-flow diagram. Then link to `docs/architecture.md` for detail — the README holds the map, not the territory.
8. **English only** — headings, prose, and in-snippet comments.
9. **Link, never duplicate.** Any fact that also lives in `docs/` is linked, not copied.
10. **Start from `assets/README.template.md`** and fill it; do not invent a new layout.

## Decision Gates

Adapt the **Quickstart** block to the project type — the only section that varies by type:

| Project type | Quickstart shows |
|--------------|------------------|
| CLI | the command invocation + expected output |
| Library | `import` + the smallest useful call |
| Service / API | how to run locally + one sample request (`curl`/httpie) |
| App | how to launch + reach the first screen |

Mandatory **Mermaid** diagrams in Architecture:

| Diagram | Shows | Typical type |
|---------|-------|--------------|
| Components | main modules/services and their relations | `graph TD` |
| Flow | how a request or data moves end to end | `sequenceDiagram` / `flowchart` |

Optional sections — include only when the trigger holds:

| Section | Include when |
|---------|--------------|
| Documentation | the repo has a populated `docs/` to link out to |
| Configuration | the project reads env vars / settings |
| Development | contributors need the local test/lint workflow |

## Update Discipline

The README must never go stale. Any change that affects what the README documents
(prerequisites, install, quickstart, architecture, config) updates the README in
the same change.

## Execution Steps

1. Copy `assets/README.template.md` as the base.
2. Fill Description (2–3 human sentences), Prerequisites (exact versions/deps), Install (exact commands).
3. Detect the project type; write the Quickstart per the Decision Gate.
4. Write the Architecture prose + both mandatory Mermaid diagrams; link to `docs/architecture.md`.
5. Add Documentation / Configuration / Development only when their trigger holds; else drop them.
6. Verify every link resolves, every command runs from a clean clone, and every Mermaid block parses.

## Output Contract

- Sections present and ordered: Description, Prerequisites, Install, Quickstart, Architecture, License (single line, last).
- Quickstart matches the project type and reaches success in under 5 minutes.
- Architecture contains at least the two mandatory Mermaid diagrams (components + flow), all valid; add an ER/state/sequence diagram only when the project genuinely needs it.
- No icons/emoji, no badges, no public contribution section.
- Humanized technical tone, all English, every link resolves.

## References

- `assets/README.template.md` — the template to fill
- `../SKILL.md` — root `docs` standard (README vs `docs/` split)
