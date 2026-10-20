---
name: py-init
description: "Claude only: Trigger: init project, new project, uv init, create project, scaffold python, start project. Full Python project initialization: git, uv, src layout, tests, pyproject.toml, CI workflows."
model: sonnet
effort: low
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.3"
---

## Activation Contract

Claude only. Load when:
- User asks to initialize, create, or scaffold a new Python project
- User says "init project", "new project", or "start with uv"

Load all companion skills as reference — do not apply their rules during init, only use their assets:
`py-style`, `py-pydantic`, `py-pytest`, `py-typing`, `ci-setup`, `ci-lint`, `ci-unit`, `ci-integration`, `ci-e2e`.

## Hard Rules

1. **`uv init --package`**: Always use `--package` flag. This enforces `src/` layout from the start. Never `uv init` without it.
2. **Python version**: Always `--python 3.12`. Explicit, never rely on system default.
3. **Branch `main` from the start**: `git init -b main`. Never init without `-b main` and rename after.
4. **Initial commit on `main` before branching**: Scaffold goes on `main` as the project baseline. CI configuration goes on `chore/project-setup` branch as the first PR.
5. **`pyproject.toml` from template**: Replace uv's generated `pyproject.toml` immediately with the one from `py-style/assets/pyproject.toml`. Update `name`, `description`, and `known-first-party`.
6. **Full test structure from day one**: Create `tests/unit/`, `tests/integration/`, `tests/e2e/` with `__init__.py` and `conftest.py` at each level. Empty is fine — structure is not.
7. **`uv sync --all-groups` locally**: Always install all groups on local setup. CI installs per group.
8. **Conventional commit for first commit**: Always `chore: initial project scaffolding`. No exceptions.
9. **GitHub config on the setup branch**: `ci.yml`, `e2e.yml`, and issue templates go on `chore/project-setup`, not on `main` directly. They get reviewed via PR.
10. **Clean uv-generated boilerplate**: `uv init --package` generates a `main()` function in `__init__.py`. Clear it — leave only the module docstring.

## Execution Steps

### Preflight

Before any phase, verify:
```bash
command -v uv   || { echo "uv not found — install from https://docs.astral.sh/uv/"; exit 1; }
command -v node || { echo "node not found — install from https://nodejs.org/ or via fnm"; exit 1; }
```
Never install global tools from py-init.

### Phase 1 — Local scaffolding

> All commands from step 2 onward run from inside `<project-name>/`. Use absolute paths or prefix each command with `cd <project-name> &&` since `cd` does not persist between Bash calls.

1. `mkdir <project-name>`
2. `cd <project-name> && git init -b main`
3. `cd <project-name> && uv init --package --python 3.12 --name <project-name>`
4. Replace `pyproject.toml` with `py-style/assets/pyproject.toml` — update `name`, `description`, `known-first-party`, and add `packages = ["<package_name>"]` to `[tool.mypy]` (replacing the placeholder comment). This lets `uv run mypy` find the package without explicit paths or flags.
5. Clear `src/<package_name>/__init__.py` — remove generated boilerplate, keep empty or add module docstring.
6. Add `__version__` to `src/<package_name>/__init__.py` (no module-level docstring — see `py-style` rule 20):
   ```python
   __version__: str = "0.1.0"
   ```
   The typed annotation is required for mypy strict mode; `__version__` is used by the smoke test.
7. `touch src/<package_name>/py.typed` — PEP 561 marker required for mypy's `packages = [...]` mode. Without it, mypy exits with code 2 when `packages` is specified in `[tool.mypy]`.
8. Verify `.python-version` contains `3.12` — `uv init --python 3.12` creates it automatically. Create it only if missing.
9. Create tests structure:
   ```
   tests/
   ├── __init__.py
   ├── conftest.py
   ├── unit/
   │   ├── __init__.py
   │   └── conftest.py
   ├── integration/
   │   ├── __init__.py
   │   └── conftest.py
   └── e2e/
       ├── __init__.py
       └── conftest.py
   ```
10. Create `tests/unit/test_smoke.py` (no module-level docstring — see `py-style` rule 20):
    ```python
    import pytest

    import <package_name>


    @pytest.mark.unit
    def test_package_is_importable() -> None:
        """Verify the package can be imported and exposes __version__."""
        # Arrange / Act
        version = <package_name>.__version__

        # Assert
        assert isinstance(version, str)
        assert version != ""
    ```
11. Create `.gitignore` from `assets/.gitignore`.
12. Create `.editorconfig` from `assets/.editorconfig`.
13. Replace `README.md` with `assets/README.md` — update project name and description.
14. Create `.flake8` from `assets/.flake8`.
15. Create `.github/pull_request_template.md` from `../github/assets/pull_request_template.md`: `mkdir -p .github && cp <skills-dir>/github/assets/pull_request_template.md .github/pull_request_template.md`
16. Copy `.gitmessage` to project root and configure git commit template:
    ```bash
    cp <skills-dir>/github/assets/.gitmessage .gitmessage
    git config commit.template .gitmessage
    ```
17. Copy `AGENTS.md` to the project root and create the `CLAUDE.md` symlink:
    ```bash
    cp <skills-dir>/github/assets/AGENTS.md AGENTS.md
    ln -s AGENTS.md CLAUDE.md
    ```
    Ask: "What modules are planned for this project? (e.g. scraper, parser, queue — press Enter to skip.)"
    Then replace the three init-time placeholders in `AGENTS.md`:
    - `<project-name>` → actual project name
    - `<package-name>` → actual package name
    - `<project-modules>` → user's module list if provided; otherwise `<package_name> (add modules here as they are created)`
18. Create the diagrams directory:
    ```bash
    mkdir -p docs/diagrams && touch docs/diagrams/.gitkeep
    ```
19. Add Graphify as a dev dependency:
    ```bash
    uv add --dev graphifyy
    ```
20. Install the Graphify git hooks:
    ```bash
    uv run graphify hook install
    ```
    The hooks call the project venv Python directly (`_PINNED = .venv/bin/python`) — no patching needed. Never build the graph manually; the git hooks handle it.
    Ask the user: "Should `graphify-out/` be committed to the repo? (Yes → leave out of `.gitignore`. No → add `graphify-out/` to `.gitignore`.)"
21. Install Archify:
    ```bash
    npx skills add tt-a1i/archify
    ```
    Installs to `.agents/skills/archify/` (Codex/OpenCode primary) and auto-creates `.claude/skills/archify → ../../.agents/skills/archify` (Claude Code symlink). Both agents covered — no manual symlink needed.
    Ask the user: "Should `.agents/skills/archify/` be committed to the repo? (Recommended: yes — pins the version. No → add `.agents/skills/archify/` to `.gitignore`.)"
22. Verify the setup:
    ```bash
    uv run graphify --help
    test -f .claude/skills/archify/SKILL.md || echo "archify skill not found"
    ```
    Both must succeed before continuing.
23. `uv sync --all-groups`

### Phase 2 — Remote + initial commit on main

24. Set up GitHub remote — two options:
    - **With `gh` CLI**: `gh repo create <project-name> --private --source=. --remote=origin`
    - **Manual**: ask user for the remote URL → `git remote add origin <url>`
25. Verify the scaffold is clean before committing:
    ```bash
    uv run ruff check .
    uv run mypy
    uv run pytest tests/unit/ --co -q
    grep -n '<project-name>\|<package-name>\|<project-modules>' AGENTS.md
    ```
    The grep must return nothing — any match means an init-time placeholder was not replaced. Fix before committing.
    `--co -q` runs collect-only — no tests executed, no coverage. Exit code 0 (smoke test is collected) is expected; exit code 5 (no tests) is now a failure since `test_smoke.py` was added. Fix any ruff or mypy errors before proceeding. Do not commit a broken scaffold.
26. `git add .`
27. `git commit -m "chore: initial project scaffolding"`
28. `git push -u origin main`

### Phase 3 — Setup branch for CI

29. `git checkout -b chore/project-setup`
30. `mkdir -p .github/workflows`
31. Copy `github/assets/ci.yml` → `.github/workflows/ci.yml` — update `known-first-party` if needed.
32. Copy `github/assets/e2e.yml` → `.github/workflows/e2e.yml`
33. Copy `github/assets/issue-templates/` → `.github/ISSUE_TEMPLATE/` — update `area` options in `bug.yml` to match project modules.
34. `git add .`
35. ```bash
    git commit -m "chore(ci): add github actions workflows and issue templates" \
      -m "CI pipelines for lint, unit, integration and e2e. YAML issue forms for bugs and feature requests."
    ```
36. `git push -u origin chore/project-setup`
37. Open as draft:
    ```bash
    gh pr create --base main --head chore/project-setup --draft \
      --title "chore: project setup" \
      --body "## Context
    Initial CI/CD configuration for the project.

    ## Approach
    CI runs on a separate branch so the workflows are reviewed via PR before landing on main.

    ## Implementation
    - \`ci.yml\`: lint (ruff, flake8, mypy) + unit tests with coverage + integration tests on PR to main
    - \`e2e.yml\`: manual dispatch for end-to-end tests
    - \`.github/ISSUE_TEMPLATE/\`: YAML forms for bug reports and feature requests

    ## Changes
    - chore(ci): add github actions workflows and issue templates"
    ```

## Output Contract

- `src/<package_name>/` exists with `__init__.py` containing `__version__: str = "0.1.0"`.
- `src/<package_name>/py.typed` is present (PEP 561 marker — required for mypy's `packages = [...]` mode).
- `tests/` has three subdirectories, each with `__init__.py` and `conftest.py`.
- `tests/unit/test_smoke.py` is present with a passing `@pytest.mark.unit` smoke test.
- `pyproject.toml` is the canonical template, not uv's default.
- `.python-version` contains `3.12`.
- `.gitignore` covers Python, uv, mypy, ruff, pytest, and IDE files.
- `.editorconfig` is present with UTF-8, LF, 4-space indent for Python.
- `.flake8` is present in the project root with flake8 configuration (flake8 does not read `pyproject.toml` natively).
- `README.md` is in English with minimal structure.
- `.github/pull_request_template.md` is present with the canonical PR body sections.
- `.gitmessage` is present in the project root and configured as `commit.template`.
- `AGENTS.md` is present in the project root with placeholders filled in.
- `CLAUDE.md` is a symlink to `AGENTS.md`.
- `docs/diagrams/.gitkeep` is present.
- Graphify dev dependency in `pyproject.toml`; git hooks installed (`post-commit`, `post-checkout`, `post-merge`, `post-rewrite`).
- `.agents/skills/archify/SKILL.md` present (project-relative — archify is per-project, unlike the shared standards in `~/.agents/skills/`); `.claude/skills/archify` is a symlink to `.agents/skills/archify`.
- `graphify-out/` and/or `.agents/skills/archify/` in `.gitignore` only if user chose not to version them.
- `main` has one commit: `chore: initial project scaffolding`.
- `chore/project-setup` has CI workflows, issue templates, and a PR open against `main`.

## References

- `assets/.gitignore` — canonical Python + uv gitignore
- `assets/.editorconfig` — canonical editor config (UTF-8, LF, 4-space Python)
- `assets/.flake8` — flake8 configuration (flake8 does not read pyproject.toml without flake8-pyproject plugin)
- `assets/README.md` — minimal README template
- `../py-style/assets/pyproject.toml` — canonical pyproject.toml template
- `../github/assets/ci.yml` — CI workflow
- `../github/assets/e2e.yml` — E2E workflow
- `../github/assets/pull_request_template.md` — PR body template (copied to `.github/`)
- `../github/assets/.gitmessage` — commit message template (copied to project root)
- `../github/assets/issue-templates/` — YAML issue forms (copied to `.github/ISSUE_TEMPLATE/`)
- `graphify hook install` — installs post-commit/checkout/merge/rewrite hooks; hooks call venv Python directly via `_PINNED`; no global registration needed
