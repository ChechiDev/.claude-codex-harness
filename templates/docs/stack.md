# Stack

> Decided in the **stack step** (leader ↔ user, one question at a time), before the
> discovery; written by the claude-planner and reviewed by Codex
> (`./harness/review_with_codex.sh stack` → `progress/stack_review.md`), then
> approved by the user. The discovery and the plan build on what is here and never
> ask for the stack again.
>
> - Each category records a **decision and why**. A category that does not apply
>   says `not applicable` and gives the reason; nothing is invented.
> - **Exact versions** stay in the package manifest or lock file
>   (`pyproject.toml` / `uv.lock`, `package.json`, `Cargo.lock`…), never here.
> - **Any change** to this file voids its approval: the stack gate runs again.

## Project type
- type: ‹api | cli | library | scraper | firmware | front end | …›
- language: ‹language and its major version›
- why: ‹who uses it and how›

## Architecture
- decision: ‹hexagonal | layered | none | … | not applicable›
- why: ‹the reason, or why it does not apply›

## Persistence
- decision: ‹the database / storage and its access layer, or not applicable›
- why: ‹the reason, or why it does not apply›

## Framework
- decision: ‹the main framework, or not applicable›
- why: ‹the reason, or why it does not apply›

## Toolchain
- tests: ‹test runner›
- lint: ‹linter / formatter›
- types: ‹type checker, or not applicable›
- build: ‹package / build tool›
- why: ‹the reason›

## Deploy
- decision: ‹where and how it runs, or not applicable›
- why: ‹the reason, or why it does not apply›

## Key dependencies
<!-- One line per first-level runtime dependency of the package manifest:
     `- <name>: <why>` (`- none: <why>` when there is none). For Python,
     `make check` warns when pyproject.toml [project] dependencies and this
     list differ. -->
- ‹name›: ‹why›
