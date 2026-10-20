"""Canonical advanced typing patterns for the py-typing skill."""

from __future__ import annotations

__all__ = [
    "T",
    "T_co",
    "T_contra",
    "P",
    "Repository",
    "ReadOnlyContainer",
    "EventSink",
    "Builder",
    "retry",
    "is_active_user",
    "get_value",
    "handle_status",
    "UserId",
    "Marker",
]

from typing import (
    Annotated,
    Any,
    Literal,
    Never,
    ParamSpec,
    Protocol,
    Self,
    TypeGuard,
    assert_never,
    overload,
    runtime_checkable,
)
from collections.abc import Callable
from functools import wraps
from typing import TypeVar


# ── TypeVar — invariant, covariant, contravariant ─────────────────────────────
# Note: TypeVar uses classic assignment syntax, not the `type` keyword.
# The `type` keyword is for type aliases only (Python 3.12+).

T = TypeVar("T")                                    # read + write → invariant
T_co = TypeVar("T_co", covariant=True)              # return-only → covariant
T_contra = TypeVar("T_contra", contravariant=True)  # input-only → contravariant
P = ParamSpec("P")                                  # for decorators
R = TypeVar("R")                                    # return type in decorators
TBound = TypeVar("TBound", bound="Comparable")      # constrained to Comparable


# ── Generic Protocol — structural, not owned ──────────────────────────────────

@runtime_checkable
class Repository(Protocol[T]):  # type: ignore[misc]
    """Structural contract for any persistence adapter over type T.

    Use Protocol[T] here because we don't own implementors —
    external adapters satisfy it without inheriting from us.

    Returns:
        None (interface marker)
    """

    def get_by_id(self, entity_id: int) -> T | None: ...
    def save(self, entity: T) -> None: ...


# ── Covariant Protocol — read-only container ──────────────────────────────────

class ReadOnlyContainer(Protocol[T_co]):  # type: ignore[misc]
    """Covariant: safe to return T_co but not accept it as input.

    Returns:
        None (interface marker)
    """

    def get(self) -> T_co: ...


# ── Contravariant Protocol — write-only sink ──────────────────────────────────

class EventSink(Protocol[T_contra]):  # type: ignore[misc]
    """Contravariant: accepts T_contra as input but never returns it.

    Returns:
        None (interface marker)
    """

    def emit(self, event: T_contra) -> None: ...


# ── Self — fluent builder, correct subclass return ───────────────────────────

class Builder:
    """Demonstrates Self for fluent interfaces that subclasses inherit safely."""

    def __init__(self) -> None:
        """Initialize empty builder state."""
        self._parts: list[str] = []

    def add(self, part: str) -> Self:
        """Append a part and return self for chaining.

        Args:
            part: String component to add to the build sequence.

        Returns:
            Self — the same instance, enabling method chaining.
        """
        self._parts.append(part)
        return self

    @classmethod
    def create(cls) -> Self:
        """Factory that returns the correct subclass type automatically.

        Returns:
            A new instance of whichever class this classmethod is called on.
        """
        return cls()

    def build(self) -> str:
        """Join all parts into the final output string.

        Returns:
            Space-joined string of all added parts.
        """
        return " ".join(self._parts)


# ── ParamSpec — decorator that preserves the wrapped signature ────────────────

def retry(times: int) -> Callable[[Callable[P, R]], Callable[P, R]]:  # type: ignore[misc]
    """Retry decorator that preserves the wrapped function's full type signature.

    Args:
        times: Number of retry attempts before re-raising the last exception.

    Returns:
        A decorator that wraps the function with retry logic.
    """
    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_exc: Exception | None = None
            for _ in range(times):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


# ── TypeGuard — narrowing is_* function ──────────────────────────────────────

class ActiveUser:
    """Represents a user with ACTIVE status."""

    status: str = "active"


def is_active_user(obj: object) -> TypeGuard[ActiveUser]:
    """Narrow obj to ActiveUser when its status attribute equals 'active'.

    Args:
        obj: Any object to inspect.

    Returns:
        TypeGuard[ActiveUser] — True narrows the type in the calling scope.
    """
    return isinstance(obj, ActiveUser) and obj.status == "active"


# ── @overload — different return types per input ──────────────────────────────

@overload
def get_value(key: str, default: None = ...) -> str | None: ...
@overload
def get_value(key: str, default: str) -> str: ...

def get_value(key: str, default: str | None = None) -> str | None:
    """Return a config value by key, with an optional typed default.

    Args:
        key: The configuration key to look up.
        default: Fallback value; return type narrows based on whether it is None.

    Returns:
        The value if found, otherwise the default.
    """
    store: dict[str, str] = {"host": "localhost"}
    return store.get(key, default)


# ── Literal + Never + assert_never — exhaustive match ────────────────────────

type Status = Literal["active", "inactive", "pending"]


def handle_status(status: Status) -> str:
    """Handle every possible Status value; mypy fails if a variant is unhandled.

    Args:
        status: One of the Literal Status values.

    Returns:
        Human-readable description of the status.
    """
    match status:
        case "active":
            return "User is active."
        case "inactive":
            return "User is inactive."
        case "pending":
            return "User is awaiting activation."
        case _ as unreachable:
            assert_never(unreachable)  # mypy fails here if Status grows


# ── Annotated — runtime metadata beyond Pydantic ─────────────────────────────

type UserId = Annotated[int, "domain:user-id"]
type Marker = Annotated[str, "di:injectable"]


def get_user(user_id: UserId) -> None:
    """Accept a UserId — the Annotated metadata is available at runtime via hints.

    Args:
        user_id: An integer annotated as a user domain identifier.

    Returns:
        None
    """


# ── type alias (3.12+) — named complex types ─────────────────────────────────

type Vector = list[float]
type Matrix = list[Vector]
type EntityMap[K, V] = dict[K, list[V]]


class Comparable(Protocol):
    """Protocol marking types that support less-than comparison."""

    def __lt__(self, other: Self) -> bool: ...


# ── TypedDict — typed dict shapes without Pydantic overhead ──────────────────

from typing import NotRequired, Required, TypedDict  # noqa: E402


class UserPayload(TypedDict):
    """Typed shape for a user dict received from an external API response.

    Use TypedDict when the dict shape is known but full Pydantic validation
    is not needed — e.g. intermediate processing, typed kwargs, or JSON blobs.

    Returns:
        None (structural type only)
    """

    id: int
    first_name: str
    last_name: str
    email: str


class UserPatchPayload(TypedDict, total=False):
    """Partial update payload — all keys are optional.

    Returns:
        None (structural type only)
    """

    first_name: str
    last_name: str
    email: str


class UserResponsePayload(TypedDict):
    """API response with a mix of required and optional fields.

    Returns:
        None (structural type only)
    """

    id: Required[int]
    email: Required[str]
    nickname: NotRequired[str]


def build_display_name(payload: UserPayload) -> str:
    """Derive a display name from a typed UserPayload dict.

    Args:
        payload: A TypedDict with known user fields from an external source.

    Returns:
        Full name string combining first and last name.
    """
    return f"{payload['first_name']} {payload['last_name']}"
