---
name: py-typing
description: "Shared standard: Trigger: TypeVar, Generic, Annotated, Protocol, overload, TypeGuard, ParamSpec, Self, Literal, Never, type alias, advanced typing. Enforce Python typing patterns for generics, protocols, and type narrowing."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.1"
---

## Activation Contract

Load this skill when:
- Writing generic classes, generic protocols, or generic functions
- Using `TypeVar`, `ParamSpec`, `Annotated`, `overload`, `TypeGuard`, `Self`, `Literal`, or `Never`
- Typing a decorator that must preserve the wrapped function's signature
- Narrowing types with `isinstance` guards or exhaustive match arms
- Defining reusable type aliases

Always load alongside `py-style`. Load `py-pydantic` when `Annotated` is used for field constraints.

## Hard Rules

1. **`TypeVar` with `bound`**: Always constrain `TypeVar` with `bound=` when the type parameter must support specific operations. Unconstrained `TypeVar` can only use operations common to all types. Classic syntax: `T = TypeVar("T", bound=SomeBase)` — do not use the `type` keyword for TypeVar (that is for type aliases, see rule 10).
2. **Covariance and contravariance**: Use `covariant=True` (`T_co`) for read-only containers (return-only positions). Use `contravariant=True` (`T_contra`) for write-only positions (callbacks, sinks). Default `TypeVar` is invariant — use it for read+write positions.
3. **Generic protocols over generic ABCs**: When the abstraction is structural (not owned), define a `Protocol[T]` instead of `Generic[T]` + `ABC`. Prefer `@runtime_checkable` only when `isinstance` checks are genuinely needed at runtime.
4. **`ParamSpec` for decorators**: Any decorator that wraps a callable MUST use `ParamSpec` + `TypeVar` to preserve the original function's signature. Never use `*args: Any, **kwargs: Any` in a typed decorator.
5. **`@overload` for variant return types**: Use `@overload` when a function returns meaningfully different types based on argument type or value — not for optional arguments. Each overload is a contract; the implementation is untyped.
6. **`Self` for fluent interfaces and classmethods**: Use `Self` (from `typing`) instead of quoting the class name in return types for methods that return `self` or `cls`. This ensures subclasses inherit the correct return type.
7. **`TypeGuard` for narrowing functions**: Any `is_*` function that narrows a type for the type checker MUST return `TypeGuard[T]`, not `bool`. This propagates the narrowing into the `if` branch.
8. **`Literal` for discriminated unions**: Use `Literal["value"]` for fields or parameters that accept only specific string/int values that are not worth a full `StrEnum`. Combine with `Union` for discriminated union patterns.
9. **`Never` for exhaustive checks**: Use `Never` (or `assert_never`) as the default arm in `match` or `if/elif` chains that must be exhaustive. This makes mypy fail if a new variant is added without handling it.
10. **`type` keyword for aliases (3.12+)**: Prefer `type Vector = list[float]` over `Vector: TypeAlias = list[float]`. For complex reusable types, always name them — never inline long unions or generics.
11. **`Annotated` for non-Pydantic metadata**: Use `Annotated[T, Metadata]` to attach runtime metadata (DI markers, validator hints, documentation tags) without coupling to Pydantic. The first argument is always the type; subsequent arguments are metadata.
12. **No `Any` without comment**: Using `Any` disables type checking for that expression. It is only acceptable for untyped third-party libraries or `cast()` calls. Every `Any` MUST have a `# type: ignore[...]` comment explaining why.
13. **`TypedDict` for typed dict structures**: Use `TypedDict` when working with JSON-like dicts, external API responses, or typed `**kwargs` — where Pydantic's full validation overhead is not justified. `TypedDict` is a structural type: it tells mypy the shape of a dict without runtime cost. Use `total=False` for optional keys. Never use `dict[str, Any]` when the shape is known.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Type param used in return + input | Invariant `TypeVar` |
| Type param only in return position | `covariant=True` TypeVar |
| Type param only in input position | `contravariant=True` TypeVar |
| Structural abstraction over external types | `Protocol[T]` |
| Owned hierarchy with shared behavior | `Generic[T]` + `ABC` |
| Decorator must preserve signature | `ParamSpec` + `TypeVar` |
| Function returns different types per input type | `@overload` |
| Method returns `self` or subclass | `Self` return type |
| `is_*` function that narrows a type | Return `TypeGuard[T]` |
| Fixed small set of values, no full enum needed | `Literal[...]` |
| Match/if-elif must be exhaustive | `Never` + `assert_never` in default arm |
| Complex reusable type expression | Named `type` alias (3.12+) |
| Runtime metadata on a type | `Annotated[T, metadata]` |
| Known dict shape, no validation needed | `TypedDict` |
| Dict with optional keys | `TypedDict` with `total=False` or `Required`/`NotRequired` |
| Untyped third-party lib | `Any` with `# type: ignore` comment |

## Execution Steps

1. Identify what varies — the type parameter — and what stays fixed.
2. Choose the correct TypeVar variance for its position (invariant / covariant / contravariant).
3. If structural: define a `Protocol[T]`. If owned: use `Generic[T]` + `ABC`.
4. Add `@overload` signatures before the implementation when return type varies by input.
5. Replace `bool` with `TypeGuard[T]` on narrowing functions.
6. Replace quoted class names with `Self` on builder/factory methods.
7. Use `assert_never` on exhaustive match default arms.
8. Name every complex type expression with `type Alias = ...`.
9. See `assets/examples.py` for canonical patterns.

## Output Contract

- Every `TypeVar` has a `bound=` or explicit constraints unless intentionally unconstrained.
- Decorators use `ParamSpec` — never untyped `*args, **kwargs`.
- `is_*` narrowing functions return `TypeGuard[T]`.
- Methods returning `self` or `cls` use `Self`.
- Exhaustive branches end with `assert_never`.
- No inline complex type expressions — named aliases only.
- No bare `Any` without a comment.
- `dict[str, Any]` never used when the shape is known — use `TypedDict`.

## References

- `assets/examples.py` — canonical patterns for all rules
- `../py-style/SKILL.md` — core style (rule 9: type hints, rule 24: Protocol vs ABC)
- `../py-pydantic/SKILL.md` — Annotated + Field for validation constraints (rule 12)
- `../py-pytest/SKILL.md` — typed fixtures and mocks in tests
