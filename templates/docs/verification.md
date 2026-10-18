# Verification — how to prove it works

> In multi-agent systems you do not evaluate the path, you evaluate the
> destination. Nothing is `done` without **executable** verification.

## Test levels

1. **Acceptance** (`tests/acceptance/`, Codex): black box, derived from the
   feature's `acceptance`. They are the oracle; Claude does not edit them. They must pass.
2. **Unit** (`tests/unit/`, Claude): white box, they guide the design (TDD).

## The contract: `make check`

```
make verify   # uv run ruff check + ruff format --check + mypy src + pytest
make check    # verify + ./harness/check.sh (invariants + gate consistency)
```

A feature is verifiable when:

- `make verify` is green (lint + type + test, including acceptance and unit).
- The Codex review is `APPROVED` (`progress/review_<id>.md`).
- The relevant `CHECKPOINTS.md` items are `[x]`.

## Manual verification (when applicable) ‹fill in›

- ‹start the service: `make run` / `docker compose up` and hit the endpoint›
- ‹test data, fixtures, seeds›
