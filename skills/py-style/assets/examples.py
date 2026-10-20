from __future__ import annotations

__all__ = [
    "UserStatus",
    "UserError",
    "UserNotFoundError",
    "Exportable",
    "BaseRepository",
    "InMemoryUserRepository",
    "UserService",
    "DatabaseConnection",
    "get_transaction",
    "get_active_users",
    "parse_full_name",
]

import contextlib
import logging
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Final, Generator, Iterator, Protocol

# ── Named constants — Final[T], no magic literals ─────────────────────────────
MAX_NAME_LENGTH: Final[int] = 50
MIN_NAME_LENGTH: Final[int] = 1
MAX_AGE: Final[int] = 120
MIN_AGE: Final[int] = 0

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────────────────

class UserStatus(StrEnum):
    """Fixed set of valid lifecycle states for a user account."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


# ── Exception hierarchy ───────────────────────────────────────────────────────

class UserError(Exception):
    """Base exception for all user-domain errors."""


class UserNotFoundError(UserError):
    """Raised when a user lookup returns no result."""


# ── Protocol — structural / duck-typed contract ───────────────────────────────

class Exportable(Protocol):
    """Structural contract for any object that can serialize itself to a dict.

    Use Protocol (not ABC) here because we don't own the implementors —
    external types or test doubles satisfy it without inheriting from us.

    Returns:
        None (interface marker only)
    """

    def build_dict(self) -> dict: ...


# ── ABC — owned nominal hierarchy ─────────────────────────────────────────────

class BaseRepository[T](ABC):
    """Contract for persistence adapters. Depend on this, never on concretions."""

    @abstractmethod
    def get_by_id(self, entity_id: int) -> T | None:
        """Retrieve a single entity by its primary key.

        Args:
            entity_id: The integer primary key of the entity.

        Returns:
            The entity if found, otherwise None.
        """

    @abstractmethod
    def save(self, entity: T) -> None:
        """Persist an entity, inserting or updating as needed.

        Args:
            entity: The domain object to persist.

        Returns:
            None
        """


# ── Concrete class — canonical member order, DI via __init__ ─────────────────

class InMemoryUserRepository(BaseRepository[object]):
    """In-memory repository used for testing and prototyping."""

    def __init__(self) -> None:
        """Initialize an empty in-memory store."""
        self._store: dict[int, object] = {}

    def get_by_id(self, entity_id: int) -> object | None:
        """Look up an entity by id, raising if absent.

        Args:
            entity_id: The integer key to look up.

        Returns:
            The stored entity.
        """
        entity = self._store.get(entity_id)
        if entity is None:
            logger.warning("Entity %d not found", entity_id)
            raise UserNotFoundError(f"Entity {entity_id} not found.")
        return entity

    def save(self, entity: object) -> None:
        """Store the entity under its object identity key.

        Args:
            entity: Any domain object to persist.

        Returns:
            None
        """
        self._store[id(entity)] = entity
        logger.info("Entity saved: %r", entity)


# ── Service — explicit DI ─────────────────────────────────────────────────────

class UserService:
    """Orchestrates user retrieval through the repository abstraction."""

    def __init__(self, repository: BaseRepository[object]) -> None:
        """Inject the persistence adapter.

        Args:
            repository: Any BaseRepository[object] implementation.

        Returns:
            None
        """
        self._repository = repository

    def get_active(self, user_id: int) -> object:
        """Return a user only if it exists in the repository.

        Args:
            user_id: The integer id of the user to retrieve.

        Returns:
            The user domain object.
        """
        return self._repository.get_by_id(user_id)


# ── Generator ─────────────────────────────────────────────────────────────────

def get_active_users(
    users: list[object] | None = None,
) -> Generator[object, None, None]:
    """Yield users whose status is ACTIVE, without materializing the full list.

    Args:
        users: Iterable of user domain objects. Defaults to empty list if None.

    Returns:
        Generator yielding active user objects.
    """
    # Never use mutable default: `users: list = []` shares the list across calls.
    resolved = users if users is not None else []
    for user in resolved:
        if is_active(user):
            yield user


def is_active(user: object) -> bool:
    """Check whether a user's status equals ACTIVE.

    Args:
        user: Any object with an optional `status` attribute.

    Returns:
        True if status is UserStatus.ACTIVE, False otherwise.
    """
    return getattr(user, "status", None) == UserStatus.ACTIVE


# ── Atomic functions with correct prefixes ────────────────────────────────────

def parse_full_name(raw: str) -> tuple[str, str]:
    """Split a raw full-name string into first and last components.

    Args:
        raw: A whitespace-padded string in 'First Last' format.

    Returns:
        A (first_name, last_name) tuple; last_name is empty if absent.
    """
    parts = raw.strip().split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


# ── Context manager ───────────────────────────────────────────────────────────

class DatabaseConnection:
    """Manages a single database connection lifecycle via the context manager protocol."""

    def __init__(self, url: str) -> None:
        """Store the connection URL without opening the connection yet.

        Args:
            url: The database connection string.

        Returns:
            None
        """
        self._url = url
        self._conn: object | None = None

    def __enter__(self) -> "DatabaseConnection":
        """Open the connection and return self for use in the `with` block.

        Returns:
            This DatabaseConnection instance.
        """
        logger.debug("Opening connection to %s", self._url)
        self._conn = object()
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Close the connection regardless of whether an exception occurred.

        Args:
            exc_type: Exception class if raised inside the block, else None.
            exc_val: Exception instance if raised inside the block, else None.
            exc_tb: Traceback object if raised inside the block, else None.

        Returns:
            None
        """
        logger.debug("Closing connection to %s", self._url)
        self._conn = None


@contextlib.contextmanager
def get_transaction(url: str) -> Iterator[DatabaseConnection]:
    """Yield an open DatabaseConnection wrapped in a logged transaction boundary.

    Args:
        url: The database connection string.

    Returns:
        Iterator yielding a DatabaseConnection for use in a `with` block.
    """
    with DatabaseConnection(url) as conn:
        logger.info("Transaction started")
        yield conn
        logger.info("Transaction committed")
