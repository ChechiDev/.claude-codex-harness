---
name: py-pydantic
description: "Shared standard: Trigger: pydantic, BaseModel, validation, field_validator, model_validator, computed_field, BaseSettings, schema, serialization. Enforce Pydantic v2 patterns: models, validators, settings, immutability, serialization."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.2"
---

## Activation Contract

Load this skill when:
- Writing or reviewing code that uses Pydantic (`BaseModel`, `BaseSettings`, validators)
- User asks about data validation, API schemas, config models, or serialization
- Any data crosses a system boundary (API input/output, CLI args, env config, file parsing)

Always load alongside `py-style` — this skill extends it, not replaces it.

## Hard Rules

1. **BaseModel for boundary data**: Any data that crosses a boundary (API, CLI, config, file) MUST use `BaseModel`. Never use `@dataclass` for validated domain models.
2. **Frozen by default**: All `BaseModel` classes MUST set `model_config = ConfigDict(frozen=True)`. To produce a modified copy use `.model_copy(update={...})`. Opt out explicitly with a comment when mutation is justified.
3. **Field constraints over manual checks**: Use `Field(...)` arguments (`min_length`, `max_length`, `ge`, `le`, `pattern`) instead of hand-rolled validators wherever possible.
4. **`@field_validator` for single-field logic**: Use for normalization (strip, lowercase) or checks that only need one field. Always use `mode="before"` for normalization, `mode="after"` for checks on the parsed value.
5. **`@model_validator` for cross-field logic**: Use `mode="after"` for invariants involving multiple fields. Never duplicate field-level checks here.
6. **`@computed_field` for derived attributes**: Derived values exposed as attributes MUST use `@computed_field` + `@property`. Never store computed state as a mutable field.
7. **`BaseSettings` for configuration**: App config from env vars or `.env` files MUST use `pydantic-settings` `BaseSettings`. Never read `os.environ` directly in domain code.
8. **Domain exceptions from validators**: Validators that fail MUST raise typed domain exceptions (defined per the `py-style` hierarchy), not raw `ValueError` — unless the `ValueError` is intentionally converted by Pydantic into a `ValidationError`.
9. **`model_dump()` / `model_validate()`**: Use Pydantic's own serialization methods. Never manually serialize a `BaseModel` to dict.
10. **No raw `dict` in domain logic**: Once data enters through a `BaseModel`, pass the model — never re-extract it to a plain `dict` and pass that around.
11. **`extra="forbid"` on external input models**: Any `BaseModel` receiving data from outside the system (API, CLI, file) MUST set `extra="forbid"` in `ConfigDict`. This rejects unknown fields instead of silently ignoring them. Internal-only models may use `extra="ignore"`.
12. **`Annotated[T, Field(...)]` for reusable constraints**: When the same field constraint appears in more than one model, extract it as a named type alias using `Annotated`. Example: `ShortStr = Annotated[str, Field(min_length=1, max_length=50)]`. This applies the DRY principle to validation constraints.
13. **`validate_assignment=True` when opting out of `frozen`**: If a model explicitly needs mutation (opt-out of `frozen=True` with justification), it MUST set `validate_assignment=True` in `ConfigDict`. Without it, direct field assignment bypasses all Pydantic validators silently. `frozen=False` + `validate_assignment=True` is the only safe mutable model configuration.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Data crosses a system boundary | `BaseModel` with `Field` constraints |
| Model needs to change after creation | `.model_copy(update={...})`, keep `frozen=True` |
| Single-field normalization (strip, lowercase) | `@field_validator(..., mode="before")` |
| Single-field post-parse check | `@field_validator(..., mode="after")` |
| Invariant across multiple fields | `@model_validator(mode="after")` |
| Derived / computed attribute | `@computed_field` + `@property` |
| App configuration from env vars | `BaseSettings` + `SettingsConfigDict(env_file=".env")` |
| Serializing a model | `.model_dump()` — never manual dict construction |
| Deserializing from dict / JSON | `.model_validate(data)` — never `Model(**data)` directly |
| Internal data structure, no validation needed | `@dataclass` (see `py-style`) |
| Model receiving external input (API, CLI, file) | Add `extra="forbid"` to `ConfigDict` |
| Model for internal use only | `extra="ignore"` is acceptable |
| Model needs intentional mutation | `frozen=False` + `validate_assignment=True` |
| Same `Field` constraint in 2+ models | Extract as `Annotated[T, Field(...)]` type alias |

## Execution Steps

1. Identify every data boundary — what enters and leaves the system.
2. Define a `BaseModel` with `frozen=True` for each boundary type.
3. Add `Field(...)` constraints first — use validators only for what `Field` can't express.
4. Add `@field_validator` for normalization and single-field checks.
5. Add `@model_validator` only for cross-field invariants.
6. Add `@computed_field` for any derived attribute.
7. Raise typed domain exceptions from validators that signal domain errors.
8. Use `BaseSettings` for any environment-driven configuration.
9. See `assets/examples.py` for canonical patterns.

## Output Contract

- Every boundary-crossing model uses `BaseModel` with `ConfigDict(frozen=True)`.
- `Field(...)` constraints used before reaching for validators.
- `@field_validator` and `@model_validator` never overlap in responsibility.
- `@computed_field` for all derived attributes.
- `BaseSettings` for all app configuration.
- No raw `dict` passed through domain logic after initial parsing.
- Serialization via `.model_dump()`, deserialization via `.model_validate()`.
- All external input models have `extra="forbid"` in `ConfigDict`.
- Repeated `Field` constraints extracted as `Annotated` type aliases.
- Mutable models use `frozen=False` + `validate_assignment=True` — never `frozen=False` alone.

## References

- `assets/examples.py` — canonical Pydantic v2 patterns
- `../py-style/SKILL.md` — core Python style rules this skill extends
- `../py-typing/SKILL.md` — Annotated beyond Pydantic: TypeVar, Generic Protocol, TypeGuard
