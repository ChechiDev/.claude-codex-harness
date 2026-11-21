---
name: py-style
description: "Shared standard: Trigger: python, py, class, function, def, module, refactor, SOLID, PEP8, uv, ruff, mypy, flake8, pyproject, linter, toolchain. Enforce Python style: SOLID, PEP8, naming, ABCs, DI, logging, generators, uv + ruff + mypy toolchain."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "2.5"
---

## Activation Contract

Load this skill when:
- Writing or reviewing any Python code (`.py` files)
- User asks to create a class, function, module, or refactor Python code
- User asks about Python architecture or conventions

Also load `py-pydantic` when the code involves data validation, API models, or config.
Also load `py-pytest` when writing or reviewing tests.
Also load `py-typing` when writing generics, TypeVar, Protocol[T], overload, TypeGuard, or ParamSpec.

## Hard Rules

1. **SOLID**: One responsibility per class. Depend on abstractions. Open for extension, closed for modification.
2. **PEP 8**: 4-space indent, `snake_case` functions/variables, `PascalCase` classes, `SCREAMING_SNAKE` constants. Max 79 chars per line.
3. **Naming prefixes**: `is_` (bool), `get_` (retrieval), `build_` (construction), `load_` (I/O), `parse_` (transform), `validate_` (check + raise), `create_` (factory), `handle_` (event/error).
4. **Abstract first**: Define `ABC` or `Protocol` before any concrete implementation. Never expose a concrete class where an abstraction fits.
5. **Atomic functions**: One function = one responsibility. Split if it does two things.
6. **No duplication**: Extract shared logic into reusable functions or mixins. DRY at all times.
7. **Decorators**: Use `@property`, `@classmethod`, `@staticmethod`, `@abstractmethod` to eliminate boilerplate. Never write getters/setters without `@property`.
8. **Generators over lists**: Use `yield` when the caller only iterates once. Prefer generator expressions over list comprehensions in that case.
9. **Type hints everywhere**: All public signatures must have type annotations. Use `from __future__ import annotations` at the top.
10. **Custom exception hierarchy**: Every module defines its own exception base. Never raise bare `Exception` or `ValueError` from domain logic. Catch the narrowest exception possible.
11. **Enums over raw strings**: Model any fixed value set as `StrEnum` (Python 3.11+). No magic strings in domain logic.
12. **Explicit dependency injection**: Dependencies enter via `__init__`. Never instantiate collaborators inside a class body.
13. **Structured logging**: Declare `logger = logging.getLogger(__name__)` at module level. Use `logger.debug/info/warning/error`. `print` is forbidden in production code.
14. **Context managers for resources**: DB connections, files, HTTP clients, locks — always managed via `with`. Implement `__enter__`/`__exit__` or use `@contextlib.contextmanager`. Never rely on GC for cleanup.
15. **Canonical class member order**: `model_config` / class constants → fields → `__init__` → `@classmethod` → `@staticmethod` → `@property` → public methods → private methods (`_name`).
16. **Explicit public API**: Every module MUST declare `__all__`. Anything outside it is internal.
17. **Virtual environment — `uv` only**: Always use `uv` to manage environments and dependencies. Never use `pip`, `virtualenv`, or `conda` directly. Lock file (`uv.lock`) MUST be under version control. Separate production from dev using `[dependency-groups]` (PEP 735): production deps in `[project.dependencies]`, tooling in named groups (`lint`, `test`). Use a `dev` group that includes both via `{include-group = ...}`. Commands: `uv add <pkg>` (prod), `uv add --group lint <pkg>` (dev group), `uv sync --no-dev` (prod-only install), `uv sync --all-groups` (full dev install).
18. **Toolchain — `ruff` + `flake8` + `mypy --strict`**: All three MUST be configured in `pyproject.toml` and pass cleanly. `ruff` handles formatting and fast lint; `flake8` catches what `ruff` misses; `mypy --strict` enforces full type safety. No exceptions.
19. **`pyproject.toml` structure**: The file MUST be organized in this group order: `[project]` → `[dependency-groups]` → `[tool.uv]` → `[tool.ruff]` → `[tool.ruff.lint]` → `[tool.mypy]` → `[tool.pytest.ini_options]` → `[tool.coverage.*]`. Never use `[project.optional-dependencies]` for dev tooling — that is for pip extras published with the package. Each tool block is self-contained. **flake8 config lives in a separate `.flake8` file at the project root** — flake8 does not read `pyproject.toml` natively (that would require the `flake8-pyproject` plugin). See `assets/pyproject.toml` for the canonical template.
20. **Docstrings — required, English, structured**: Every class, function, and method MUST have a docstring immediately below its definition. Format: 1–3 lines describing behavior → `Args:` block (one line per param: `name: description`) → `Returns:` line. No docstring at the module/file level. Never describe the "what" the name already says — describe the "why" or the contract.
21. **Import grouping — no blank lines within groups**: Imports MUST be grouped in this order: (1) stdlib, (2) third-party, (3) internal/local. One blank line between groups. No blank lines inside a group. `from __future__ import annotations` always goes first, alone, before all groups.
22. **`src/` layout — canonical project structure**: All source code MUST live under `src/<package_name>/`. Never place importable modules at the project root. This prevents accidental imports without installation and aligns with `uv`, `mypy` (`src = ["src"]`), and `ruff` (`src = ["src"]`) configuration already in `pyproject.toml`.
23. **No magic literals — named constants**: Any literal with domain meaning (timeouts, limits, codes, thresholds) MUST be extracted as a `SCREAMING_SNAKE_CASE` constant at the top of the module or inside a dedicated constants class. No bare `3`, `"pending"`, `30.0` scattered through logic.
24. **`Protocol` vs `ABC` — explicit choice**: Use `Protocol` for structural subtyping — duck typing, external types you don't control, or test doubles. Use `ABC` for nominal hierarchies you own and where shared base behaviour or `super()` calls are needed. Never choose arbitrarily.
25. **`Final[T]` for module-level constants**: All named constants MUST be typed with `Final`. Use `from typing import Final`. This prevents accidental reassignment and makes intent explicit to both mypy and readers.
26. **No mutable default arguments**: Default parameter values MUST NOT be mutable (`list`, `dict`, `set`). Always use `None` as the default and initialize inside the function body. Mutable defaults are shared across all calls — a silent, hard-to-trace bug.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Sequence iterated once | `yield` / generator expression |
| Shared behavior across classes | ABC or Protocol |
| Multiple implementations of one concept | Protocol + inject dependency |
| Read-only external state | `@property` |
| Alternate constructor | `@classmethod` |
| Utility with no instance state | `@staticmethod` |
| Repeated code block | Extract with correct naming prefix |
| Domain logic raises an error | Typed domain exception, never bare `Exception` |
| Class needs a collaborator | Inject via `__init__` |
| Resource needs cleanup | `__enter__`/`__exit__` or `@contextmanager` |
| Logging or debug output | `logger = logging.getLogger(__name__)` |
| Fixed set of values | `StrEnum` |
| New project / environment setup | `uv sync --all-groups` |
| Adding a production dependency | `uv add <pkg>` |
| Adding a dev/lint/test dependency | `uv add --group <lint\|test\|dev> <pkg>` |
| Production-only install (CI/deploy) | `uv sync --no-dev` |
| Linting locally | `ruff check .` + `flake8 .` + `mypy --strict .` |
| Setting up tooling | Follow `assets/pyproject.toml` group order |
| Organizing a new project | `src/<package>/` layout, never root-level modules |
| Literal with domain meaning | Named `SCREAMING_SNAKE` constant, never inline |
| Abstraction over external / duck-typed code | `Protocol` |
| Abstraction over owned inheritance hierarchy | `ABC` |
| Module-level constant | `NAME: Final[T] = value` |
| Parameter with a mutable default | Use `None` default, initialize inside body |

## Execution Steps

1. Identify the domain concept and abstraction boundary.
2. Define the Protocol or ABC first.
3. Implement concrete classes satisfying the protocol — inject all deps via `__init__`.
4. Apply correct naming prefix to every function and method.
5. Verify each function is atomic.
6. Add type hints to all public signatures.
7. Declare `__all__` with the public API.
8. Review for duplication — extract before finishing.
9. See `assets/examples.py` for canonical patterns.

## Output Contract

- At least one ABC or Protocol per output.
- All methods follow naming prefix conventions.
- No function exceeds ~20 lines.
- Full type annotations on public APIs.
- Generator used wherever a list is only iterated once.
- No bare `Exception` in domain logic — typed hierarchy only.
- All fixed value sets use `StrEnum`.
- All dependencies injected via `__init__`.
- No `print`; `logger` at module level.
- All resource-managing classes use `with`.
- Class members in canonical order.
- `__all__` declared in every module.
- All source code under `src/<package_name>/`.
- No magic literals — every meaningful constant is named.
- `Protocol` used for structural/external abstractions; `ABC` for owned hierarchies.
- All module-level constants typed with `Final[T]`.
- No mutable default arguments anywhere.

## References

- `assets/examples.py` — canonical Python patterns
- `assets/pyproject.toml` — canonical toolchain configuration template
- `../py-pydantic/SKILL.md` — companion skill for Pydantic validation
- `../py-typing/SKILL.md` — advanced typing: TypeVar, Generic, Protocol, overload, TypeGuard
