# Project Name

Brief one-line description of what this project does.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — package and virtual environment manager

## Installation

```bash
uv sync --all-groups
uv run graphify hook install  # reinstall git hooks after clone or venv recreation
```

## Usage

```bash
# Run the project
uv run python -m <package_name>
```

## Development

```bash
# Lint
uv run ruff check .
uv run ruff format --check .
uv run flake8 .
uv run mypy

# Validate workflow YAML locally (pyyaml not in project deps — use --with)
for f in .github/workflows/*.yml; do uv run --with pyyaml python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" "$f" && echo "OK: $f"; done

# Tests
uv run pytest tests/unit/ --cov=<package_name> --cov-report=term-missing
uv run pytest tests/integration/ -v
uv run pytest tests/e2e/ -v

# Format (auto-fix)
uv run ruff format .
uv run ruff check --fix .
```

## Project Structure

```
src/
└── <package_name>/    # Application source
tests/
├── unit/              # Isolated unit tests (mocked dependencies)
├── integration/       # Integration tests (real dependencies)
└── e2e/               # End-to-end tests (full system)
```
