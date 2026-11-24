---
name: py-pytest
description: "Shared standard: Trigger: pytest, test, unit, integration, e2e, fixture, mock, coverage, async test, conftest. Enforce pytest conventions: structure, mocking strategy, coverage, async, fixtures, markers."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "2.0"
---

## Activation Contract

Shared standard. Load alongside `py-style` on any test-related request. Apply its rules directly — this skill defines conventions; it does not route or execute.

Load `py-pydantic` when testing models or validators. `pydantic` and `pydantic-settings` are runtime dependencies of the profile: tests import them with no `uv add`.

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
3. **Markers decide the job**: `@pytest.mark.integration` and `@pytest.mark.e2e` are the ONLY criterion that takes a test out of `make test` (selection `not integration and not e2e`, defined once in the Makefile and reused by CI). A test under `tests/integration/` or `tests/e2e/` MUST carry that marker — the project's root `conftest.py` fails the collection otherwise. A marked test elsewhere still goes to its marker's job. `@pytest.mark.unit` is optional documentation. Markers are registered in `pyproject.toml` (`--strict-markers`).
4. **Mocking strategy by type**:
   - `unit`: mock ALL external dependencies using `pytest-mock`'s `mocker` fixture. Tests must be fast and deterministic.
   - `integration`: NO mocks — hit real controlled services (test DB, internal API).
   - `e2e`: NO mocks — full real flow, real data, real network.
5. **AAA pattern**: Every test MUST follow Arrange → Act → Assert. One logical assertion per test. Never test multiple behaviors in one function.
6. **Fixtures over setup/teardown**: Never use `setup_method` or `teardown_method`. Always use pytest fixtures. Prefer the narrowest scope possible (`function` > `class` > `module` > `session`).
7. **`conftest.py` per level**: Global fixtures (HTTP client, settings) in `tests/conftest.py`. Type-specific fixtures in `tests/unit/conftest.py`, `tests/integration/conftest.py`, `tests/e2e/conftest.py`. Never put fixtures in test files directly.
8. **Parametrize for multiple cases**: Use `@pytest.mark.parametrize` instead of writing identical tests with different inputs. Each parameter set MUST include a descriptive `id`.
9. **Async tests (optional, async projects only)**: the base profile has no async plugin. An async project adds both the dependency and the setting: `uv add --dev pytest-asyncio`, then `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`. Async tests are then plain `async def test_...` — no decorator needed per test.
10. **Coverage over `make test`'s selection**: `make test` measures branch coverage of `src/` over every test except `integration`/`e2e`, and enforces `[tool.coverage.report] fail_under = 80` — the single source of the threshold (never a threshold flag on a command line). No test selected (a new project, or only integration/e2e tests) is green; a suite emptied by any other deselection (a `-k` typo) is red. `HARNESS_NO_COV_THRESHOLD=1` drops only the threshold, for the harness's issue re-check that excludes tests by design.
11. **No shared mutable state**: Tests MUST NOT depend on execution order or shared mutable state. Each test sets up its own state via fixtures.
12. **Docstring on every test**: Every test function SHOULD have a one-line docstring describing the scenario: `"""Returns 404 when user does not exist."""`
13. **Imports at the top of the file**: All imports MUST be at the module level — never inside test functions or inside `with` blocks. Each test file declares exactly what it imports from the module under test.
14. **Float assertions use `pytest.approx`**: Never use `==` to compare floats directly. Always use `assert result == pytest.approx(expected, rel=1e-6)`. Direct float equality fails silently due to floating-point precision.
15. **`pytest.raises` requires `match=`**: When testing an exception that has a meaningful message, always assert the message with `pytest.raises(ErrorType, match=r"expected pattern")`. A bare `pytest.raises(ErrorType)` passes for any instance of the error — including the wrong one.
16. **A test must fail if the behavior it covers is broken.** Tests that only mirror the implementation without asserting observable behavior are invalid. Write tests against the contract, not the code.
17. **Where each kind runs**: `make test` (and the CI `test` job) — everything else, with coverage; `make test-integration` — the CI `integration` job inside `ci.yml`, with service containers (Postgres/Redis), for changes that target main; `make test-e2e` — by hand (or the manual `e2e.yml` CI file): real networks, not deterministic.

> **Enforcement**: Rules 8 (parametrize — `PT006`/`PT007`) and 15 (`pytest.raises match=` — `PT011`) are enforced by ruff; rule 3 by the root `conftest.py`; rule 10 by `make test`. Rules 6 and 12 are enforced at review. All other rules are guidance enforced at review.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Testing a class or function in isolation | `tests/unit/` + `mocker` for all external deps |
| Testing interaction with a real DB or API | `tests/integration/` + no mock |
| Testing a full user flow or scraping | `tests/e2e/` + no mock |
| Same test logic with different inputs | `@pytest.mark.parametrize` with `ids=` |
| Shared resource across tests in a file | Fixture with `scope="module"` in `conftest.py` |
| Shared resource across the entire suite | Fixture with `scope="session"` in `tests/conftest.py` |
| Async function under test | `async def test_...` once the project opted into pytest-asyncio (rule 9) |
| Testing a Pydantic model | Unit test — mock nothing, validation is pure |
| Testing expected exception with message | `pytest.raises(ErrorType, match=r"pattern")` |
| Testing expected exception (any message) | `pytest.raises(ErrorType)` only if message is irrelevant |
| Asserting float equality | `assert result == pytest.approx(expected, rel=1e-6)` |
| Run a single test function | `pytest tests/unit/pkg/test_module.py::test_function_name -v` |
| Run a single test class | `pytest tests/unit/pkg/test_module.py::TestClass -v` |
| Run all tests matching a name pattern | `pytest -k "pattern" -v` |
| Run what CI runs | `make test` / `make test-integration` / `make test-e2e` |
| Run only failed tests from last run | `pytest --lf` |
| Coverage with missing lines (and the threshold) | `make test` |
| Coverage XML (CI comment) | `make test COVERAGE_XML=coverage.xml` |
| Coverage HTML after `make test` | `uv run coverage html` — opens `htmlcov/index.html` |
| Ad-hoc coverage of one file | `uv run pytest tests/unit/pkg/test_x.py --cov --cov-report=term-missing` |

## Output Contract

- Every test file has its correct subdirectory, mirrors the project's package structure, and covers exactly one source module.
- All imports at module level — never inside test functions.
- Every test under tests/integration/ or tests/e2e/ carries its marker; every test has a docstring and follows AAA.
- Mocks only in unit tests — never in integration or e2e.
- Fixtures in `conftest.py`, never inline in test files.
- Async tests (async projects only) use `async def` — no per-test decorator.
- `@pytest.mark.parametrize` used for multiple-input scenarios.
- Coverage runs through `make test`; the 80% threshold lives only in `[tool.coverage.report]`.
- Float comparisons use `pytest.approx` — never `==` on floats.
- `pytest.raises` includes `match=` whenever the error message is part of the contract.
- e2e tests are excluded from every automated run; integration tests run only in their CI job.

## References

- `assets/test_services.py` — unit tests for a service (mocker, AAA, parametrize)
- `assets/test_models.py` — unit tests for Pydantic models
- `assets/test_scraper.py` — async unit tests + e2e tests without mock
- `assets/conftest_example.py` — conftest.py structure per level
- `assets/root_conftest.py` — the project's root `conftest.py` (scaffolded): marker and selection rules
- `../py-style/SKILL.md` — core style rules
- `../py-pydantic/SKILL.md` — Pydantic validation patterns
- `../py-typing/SKILL.md` — typed fixtures, TypeGuard in assertions, generic mocks
