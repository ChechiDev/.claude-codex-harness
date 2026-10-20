---
name: py-pytest
description: "Shared standard: Trigger: pytest, test, unit, integration, e2e, fixture, mock, coverage, async test, conftest. Enforce pytest conventions: structure, mocking strategy, coverage, async, fixtures, markers."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.3"
---

## Activation Contract

Shared standard. Load alongside `py-style` on any test-related request. Apply its rules directly — this skill defines conventions; it does not route or execute.

Load `py-pydantic` when testing models or validators.

## Hard Rules

1. **Test location and file structure**: All tests live in `tests/` at the project root. Mirror the project's packages structure exactly — one test file per source module. Never put all tests for a domain in a single file. Example: `src/your_package/services.py` → `tests/unit/your_package/test_services.py`. Each test file imports only the module it tests.
   ```
   src/your_package/
   ├── models.py
   ├── services.py
   └── repositories.py

   tests/
   ├── conftest.py
   ├── unit/
   │   ├── conftest.py
   │   └── your_package/
   │       ├── test_models.py
   │       ├── test_services.py
   │       └── test_repositories.py
   ├── integration/
   │   ├── conftest.py
   │   └── your_package/
   │       └── test_repositories.py
   └── e2e/
       ├── conftest.py
       └── your_package/
           └── test_scraper.py
   ```
2. **Naming**: Test files: `test_<module>.py`. Test functions: `test_<behavior>_when_<condition>`. Test classes: `Test<ClassName>`. Never use generic names like `test_1` or `test_it`.
3. **Markers**: Every test MUST be marked with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.e2e`. Markers must be registered in `pyproject.toml`.
4. **Mocking strategy by type**:
   - `unit`: mock ALL external dependencies using `pytest-mock`'s `mocker` fixture. Tests must be fast and deterministic.
   - `integration`: NO mocks — hit real controlled services (test DB, internal API).
   - `e2e`: NO mocks — full real flow, real data, real network.
5. **AAA pattern**: Every test MUST follow Arrange → Act → Assert. One logical assertion per test. Never test multiple behaviors in one function.
6. **Fixtures over setup/teardown**: Never use `setup_method` or `teardown_method`. Always use pytest fixtures. Prefer the narrowest scope possible (`function` > `class` > `module` > `session`).
7. **`conftest.py` per level**: Global fixtures (HTTP client, settings) in `tests/conftest.py`. Type-specific fixtures in `tests/unit/conftest.py`, `tests/integration/conftest.py`, `tests/e2e/conftest.py`. Never put fixtures in test files directly.
8. **Parametrize for multiple cases**: Use `@pytest.mark.parametrize` instead of writing identical tests with different inputs. Each parameter set MUST include a descriptive `id`.
9. **Async tests**: Use `pytest-asyncio` with `asyncio_mode = "auto"` in `pyproject.toml`. Define async tests as `async def test_...` — no decorator needed per test.
10. **Coverage on unit tests only**: Run `pytest-cov` only over `tests/unit/`. Minimum threshold: 80%. Integration and e2e tests are excluded from coverage enforcement.
11. **No shared mutable state**: Tests MUST NOT depend on execution order or shared mutable state. Each test sets up its own state via fixtures.
12. **Docstring on every test**: Every test function MUST have a one-line docstring describing the scenario: `"""Returns 404 when user does not exist."""`
13. **Imports at the top of the file**: All imports MUST be at the module level — never inside test functions or inside `with` blocks. Each test file declares exactly what it imports from the module under test.
14. **Float assertions use `pytest.approx`**: Never use `==` to compare floats directly. Always use `assert result == pytest.approx(expected, rel=1e-6)`. Direct float equality fails silently due to floating-point precision.
15. **`pytest.raises` requires `match=`**: When testing an exception that has a meaningful message, always assert the message with `pytest.raises(ErrorType, match=r"expected pattern")`. A bare `pytest.raises(ErrorType)` passes for any instance of the error — including the wrong one.
16. **A test must fail if the behavior it covers is broken.** Tests that only mirror the implementation without asserting observable behavior are invalid. Write tests against the contract, not the code.
17. **e2e tests never run automatically** (never on push or PR triggers). Run them only manually: `pytest -m e2e` locally, or via `workflow_dispatch` in GitHub Actions. Never add e2e to push/PR triggers — they rely on real networks and are not deterministic in automated environments.

> **Ruff enforcement**: Rules 8 (parametrize — `PT006`/`PT007`), 12 (test docstrings — `D103` via `per-file-ignores`), 15 (`pytest.raises match=` — `PT011`) are enforced at commit time. Rule 6 (no `setup_method`) has no active ruff rule — enforced at review. All other rules are guidance enforced at review.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Testing a class or function in isolation | `tests/unit/` + `mocker` for all external deps |
| Testing interaction with a real DB or API | `tests/integration/` + no mock |
| Testing a full user flow or scraping | `tests/e2e/` + no mock |
| Same test logic with different inputs | `@pytest.mark.parametrize` with `ids=` |
| Shared resource across tests in a file | Fixture with `scope="module"` in `conftest.py` |
| Shared resource across the entire suite | Fixture with `scope="session"` in `tests/conftest.py` |
| Async function under test | `async def test_...` — asyncio_mode = "auto" handles it |
| Testing a Pydantic model | Unit test — mock nothing, validation is pure |
| Testing expected exception with message | `pytest.raises(ErrorType, match=r"pattern")` |
| Testing expected exception (any message) | `pytest.raises(ErrorType)` only if message is irrelevant |
| Asserting float equality | `assert result == pytest.approx(expected, rel=1e-6)` |
| Run a single test function | `pytest tests/unit/pkg/test_module.py::test_function_name -v` |
| Run a single test class | `pytest tests/unit/pkg/test_module.py::TestClass -v` |
| Run all tests matching a name pattern | `pytest -k "pattern" -v` |
| Run all tests with a specific marker | `pytest -m unit` / `pytest -m integration` |
| Run only failed tests from last run | `pytest --lf` |
| Coverage with terminal missing lines | `pytest tests/unit/ --cov=<project-packages> --cov-report=term-missing` |
| Coverage with HTML report | `pytest tests/unit/ --cov=<project-packages> --cov-report=html` — opens `htmlcov/index.html` |
| Coverage enforced in CI | `pytest tests/unit/ --cov=<project-packages> --cov-fail-under=80` |

## Output Contract

- Every test file has its correct subdirectory, mirrors the project's package structure, and covers exactly one source module.
- All imports at module level — never inside test functions.
- Every test has a marker, a docstring, and follows AAA.
- Mocks only in unit tests — never in integration or e2e.
- Fixtures in `conftest.py`, never inline in test files.
- Async tests use `async def` — no per-test decorator.
- `@pytest.mark.parametrize` used for multiple-input scenarios.
- Coverage command targets `tests/unit/` only with `--cov-fail-under=80`.
- Float comparisons use `pytest.approx` — never `==` on floats.
- `pytest.raises` includes `match=` whenever the error message is part of the contract.
- e2e tests never appear in CI workflows.

## References

- `assets/test_services.py` — unit tests for a service (mocker, AAA, parametrize)
- `assets/test_models.py` — unit tests for Pydantic models
- `assets/test_scraper.py` — async unit tests + e2e tests without mock
- `assets/conftest_example.py` — conftest.py structure per level
- `../py-style/SKILL.md` — core style rules
- `../py-pydantic/SKILL.md` — Pydantic validation patterns
- `../py-typing/SKILL.md` — typed fixtures, TypeGuard in assertions, generic mocks
