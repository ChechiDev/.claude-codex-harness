# Project Name

Brief one-line description of what this project does.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — package and virtual environment manager

## Installation

```bash
uv sync
uv run graphify hook install  # reinstall git hooks after clone or venv recreation
make hooks                    # optional: pre-commit hooks (lint + type, check-only)
```

## Usage

```bash
# Run the project
uv run python -m <package_name>
```

## Development

The Makefile is the single source of the commands (CI calls the same targets).

```bash
make verify            # lint + type + test (coverage >= 80%) + deps (deptry)
make fix               # safe autofixes + formatting
make test              # every test except integration/e2e markers, with coverage
make test-integration  # tests marked integration (needs the services)
make test-e2e          # tests marked e2e (by hand: real network)
make audit             # known vulnerabilities (pip-audit; needs network)

# Validate workflow YAML locally (pyyaml not in project deps — use --with)
for f in .github/workflows/*.yml; do uv run --with pyyaml python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" "$f" && echo "OK: $f"; done
```

## Project Structure

```
src/
└── <package_name>/    # Application source
tests/
├── unit/              # Isolated unit tests (mocked dependencies)
├── integration/       # @pytest.mark.integration (real services; required here)
└── e2e/               # @pytest.mark.e2e (full system; required here)
```
