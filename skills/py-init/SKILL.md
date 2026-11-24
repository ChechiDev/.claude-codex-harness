---
name: py-init
description: "Claude only: Trigger: init project, new project, uv init, create project, scaffold python, start project. Full Python project initialization: git, uv, src layout, tests, pyproject.toml, CI workflows."
model: sonnet
effort: low
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "3.0"
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
4. **Harness-neutral**: py-init never writes role files (`CLAUDE.md`, `AGENTS.md`), a `Makefile`, or anything under `.claude/`. Those belong to the harness `install.sh`. One exception: when the user declines the harness, step 26 copies the harness's template `Makefile` (standalone without `harness/`), because the CI workflows call its targets. In a harness project `.claude/skills` links to the shared toolkit: writing there would change every project.
4b. **Initial commit on `main` before branching**: one initial commit holds the scaffold (plus the harness when installed in the guided flow). CI configuration goes on `chore/project-setup` as the first PR. On a repo that already had history when py-init started, py-init never commits, branches or pushes: it leaves the files for the human's authorised commit flow.
5. **`pyproject.toml` from template**: Replace uv's generated `pyproject.toml` immediately with the one from `py-style/assets/pyproject.toml`. Update `name`, `description`, and `known-first-party`.
6. **Full test structure from day one**: Create `tests/unit/`, `tests/integration/`, `tests/e2e/` with `__init__.py` and `conftest.py` at each level. Empty is fine — structure is not.
7. **`uv sync`**: every tool is in the `dev` group (synced by default), locally and in CI (`uv sync --locked`).
8. **Conventional commit for first commit**: `chore: initial project scaffolding` when py-init commits it; `chore: install harness` when `install.sh` makes it (guided flow).
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

Then detect the harness and the git state (`<skills-dir>` is the directory holding this skill's siblings):
```bash
bash <skills-dir>/py-init/scripts/detect_harness.sh <project-dir>
# harness=installed|absent
# history=yes|no
```
- `harness=installed` → **Phase 1 only** (Python scaffolding, minus the steps marked *harness: skip*). Skip Phase 2 and the commit/branch/push steps of Phase 3: the harness flow owns them. Report which steps were skipped.
- `history=yes` → py-init never commits, branches or pushes (steps 25–37 become "leave the files for the human to commit").
- Both `absent`/`no` → the full guided flow below.

### Phase 1 — Local scaffolding

> All commands from step 2 onward run from inside `<project-name>/`. Use absolute paths or prefix each command with `cd <project-name> &&` since `cd` does not persist between Bash calls.

1. `mkdir -p <project-name>` (it may already exist: harness installed or a repo with history)
2. `cd <project-name> && { git rev-parse --git-dir >/dev/null 2>&1 || git init -b main; }` — never re-init an existing repo
3. `cd <project-name> && { test -f pyproject.toml || uv init --package --python 3.12 --name <project-name>; }` — with an existing `pyproject.toml`, step 4 still applies the canonical template (keep a backup: `cp pyproject.toml pyproject.toml.bak`)
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
11. Create `.gitignore` from `assets/.gitignore` (if one exists, append only the missing lines — never duplicate an entry).
12. Create `.editorconfig` from `assets/.editorconfig`.
13. Replace `README.md` with `assets/README.md` — update project name and description.
14. Create the profile's root files, only if missing: `conftest.py` from `../py-pytest/assets/root_conftest.py` (marker and test-selection rules) and `.pre-commit-config.yaml` from `../github/assets/.pre-commit-config.yaml` (opt-in hooks: `make hooks`).
15. Create `.github/pull_request_template.md` from `../github/assets/pull_request_template.md`: `mkdir -p .github && cp <skills-dir>/github/assets/pull_request_template.md .github/pull_request_template.md`
16. Copy `.gitmessage` to project root and configure git commit template:
    ```bash
    cp <skills-dir>/github/assets/.gitmessage .gitmessage
    git config commit.template .gitmessage
    ```
17. *(removed)* — no role files. `CLAUDE.md` / `AGENTS.md` come from the harness `install.sh` (Phase 2). Never create a `CLAUDE.md` symlink.
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
21. Install Archify — only into the project's `.agents/skills/`, never `.claude/skills`:
    ```bash
    bash <skills-dir>/py-init/scripts/install_archify.sh <project-dir>
    ```
    The script runs the Archify installer (npx) in a throwaway directory (that command also creates `.claude/skills/archify`, which in a harness project is the shared toolkit) and copies only `.agents/skills/archify/` into the project. It exits non-zero if the skill was not produced.
    Ask the user: "Should `.agents/skills/archify/` be committed to the repo? (Recommended: yes — pins the version. No → add `.agents/skills/archify/` to `.gitignore`.)"
    **Invoking it (until per-project skills exist):** Claude does not auto-load `.agents/skills/`. Ask for it explicitly: "Read `.agents/skills/archify/SKILL.md` and follow it". Never link it into `.claude/skills`.
22. Verify the setup — each command must succeed (a non-zero exit stops the init):
    ```bash
    uv run graphify --help >/dev/null
    test -f .agents/skills/archify/SKILL.md
    ```
23. `uv sync`

### Phase 2 — Harness, remote and the initial commit on main

*Skipped entirely when `harness=installed`. With `history=yes`, only step 24 runs (offer install.sh); no commit.*

24. Offer the harness: "Install the claude-codex harness now? It adds `CLAUDE.md`, `AGENTS.md`, the `Makefile` and the workflow." Only with the user's OK:
    ```bash
    bash <toolkit>/install.sh <project-dir>
    ```
    Always without `--python`: py-init's `pyproject.toml` stays (the scaffold is already there). On a repo without history `install.sh` makes the single initial commit (`chore: install harness`, scaffold + harness) and switches to `chore/project-setup`, and its final `make check` must be green. Check the outcome, don't assume it: `git log --oneline main` must show `chore: install harness` and `git branch --show-current` must print `chore/project-setup`. Only then skip steps 26–29; if install.sh skipped the commit (e.g. no git identity, it says so in its `[git]` line), run steps 26–29 yourself (the commit then also includes the harness files).
    On a repo with history `install.sh` commits nothing: report the files and stop — the human commits them through the authorised flow.
25. Set up GitHub remote — two options:
    - **With `gh` CLI**: `gh repo create <project-name> --private --source=. --remote=origin`
    - **Manual**: ask user for the remote URL → `git remote add origin <url>`
26. *(harness declined only)* Copy the Makefile (the CI calls its targets; its harness block is inert without `harness/`) and verify the scaffold before committing:
    ```bash
    test -f Makefile || cp <skills-dir>/../templates/Makefile Makefile
    make verify
    ```
    `make verify` = lint + type + test (with the 80% coverage threshold) + deps. The smoke test must run and pass. Do not commit a broken scaffold. On the empty scaffold `make deps` warns that pydantic/pydantic-settings are unused (DEP002, tolerated until `src/` has a module) and exits 0; once `src/` has a module, deptry is fully strict.
27. *(harness declined only)* `git add .`
28. *(harness declined only)* `git commit -m "chore: initial project scaffolding"`
29. *(harness declined only)* `git checkout -b chore/project-setup`
30. `git push -u origin main`

### Phase 3 — Setup branch for CI

*With `harness=installed` or `history=yes`: copy the files (steps 31–33) and stop — no commit, push or PR.*

31. `mkdir -p .github/workflows` and copy `github/assets/ci.yml` → `.github/workflows/ci.yml`. Its jobs call the Makefile targets the harness's `make check` uses (`make lint`, `make type`, `make deps`, `make test` with the coverage XML), plus `make audit` and the service-backed `make test-integration` — no placeholders to fill (drop the services the project does not use).
32. Copy `github/assets/e2e.yml` → `.github/workflows/e2e.yml`
33. Copy `github/assets/issue-templates/` → `.github/ISSUE_TEMPLATE/` — update `area` options in `bug.yml` to match project modules.
34. Confirm you are on `chore/project-setup` (`git branch --show-current`), then `git add .github`
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
    - \`ci.yml\`: lint, types, deptry, pip-audit + tests with coverage (make test) + integration tests with services on PR to main
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
- `conftest.py` (from `py-pytest/assets/root_conftest.py`) and `.pre-commit-config.yaml` are present in the project root.
- `README.md` is in English with minimal structure.
- `.github/pull_request_template.md` is present with the canonical PR body sections.
- `.gitmessage` is present in the project root and configured as `commit.template`.
- py-init wrote no `CLAUDE.md`, `AGENTS.md` or `.claude/` entry, and a `Makefile` only when the harness was declined (with the harness, those are `install.sh`'s).
- `docs/diagrams/.gitkeep` is present.
- Graphify dev dependency in `pyproject.toml`; git hooks installed (`post-commit`, `post-checkout`, `post-merge`, `post-rewrite`).
- `.agents/skills/archify/SKILL.md` present (project-relative — archify is per-project); nothing written under `.claude/skills`.
- `graphify-out/` and/or `.agents/skills/archify/` in `.gitignore` only if user chose not to version them.
- Fresh repo: `main` has one commit (`chore: install harness` with the harness, else `chore: initial project scaffolding`); `chore/project-setup` has CI workflows, issue templates, and a draft PR against `main`.
- Harness already installed or pre-existing history: no commit, branch or push made by py-init.

## References

- `assets/.gitignore` — canonical Python + uv gitignore
- `assets/.editorconfig` — canonical editor config (UTF-8, LF, 4-space Python)
- `assets/README.md` — minimal README template
- `../py-style/assets/pyproject.toml` — canonical pyproject.toml template (the Python profile)
- `../github/assets/.pre-commit-config.yaml` — opt-in pre-commit hooks (`make hooks`)
- `../py-pytest/assets/root_conftest.py` — the project's root `conftest.py`: marker and selection rules
- `scripts/detect_harness.sh` — prints `harness=` / `history=` for the preflight
- `scripts/install_archify.sh` — Archify into `.agents/skills/` only
- `../github/assets/ci.yml` — CI workflow
- `../github/assets/e2e.yml` — E2E workflow
- `../github/assets/pull_request_template.md` — PR body template (copied to `.github/`)
- `../github/assets/.gitmessage` — commit message template (copied to project root)
- `../github/assets/issue-templates/` — YAML issue forms (copied to `.github/ISSUE_TEMPLATE/`)
- `graphify hook install` — installs post-commit/checkout/merge/rewrite hooks; hooks call venv Python directly via `_PINNED`; no global registration needed
