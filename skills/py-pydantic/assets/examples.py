"""Canonical Pydantic v2 patterns for the py-pydantic skill."""

from __future__ import annotations

__all__ = [
    "UserStatus",
    "UserError",
    "UserValidationError",
    "ShortStr",
    "BoundedAge",
    "UserInput",
    "UserInternal",
    "build_user_from_raw",
    "get_user_payload",
]

import logging
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field
from pydantic import field_validator, model_validator

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────────────────

class UserStatus(StrEnum):
    """Fixed set of valid lifecycle states for a user account."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


# ── Domain exceptions ─────────────────────────────────────────────────────────

class UserError(Exception):
    """Base exception for all user-domain errors."""


class UserValidationError(UserError):
    """Raised when a user model fails a domain invariant check."""


# ── Annotated type aliases — reusable constraints (DRY) ──────────────────────

ShortStr = Annotated[str, Field(min_length=1, max_length=50)]
BoundedAge = Annotated[int, Field(ge=0, le=120)]


# ── External input model — extra="forbid" ─────────────────────────────────────

class UserInput(BaseModel):
    """Validates raw external input at the API/CLI boundary.

    Rejects unknown fields via extra="forbid" to prevent silent data injection.

    Args:
        first_name: User's first name, 1–50 characters.
        last_name: User's last name, 1–50 characters.
        email: Valid email address.
        age: Integer age between 0 and 120.

    Returns:
        None
    """

    # 1. model_config — frozen + forbid unknown fields on external input
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 2. Fields — reuse Annotated aliases instead of repeating constraints
    first_name: ShortStr
    last_name: ShortStr
    email: EmailStr
    age: BoundedAge
    status: UserStatus = Field(default=UserStatus.PENDING)

    # 3. @classmethod — normalization before parsing
    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def parse_name_strip(cls, value: str) -> str:
        """Strip and title-case name fields before Pydantic parses them.

        Args:
            value: Raw string value for the field.

        Returns:
            Stripped and title-cased string.
        """
        return value.strip().title()

    # 4. @model_validator — cross-field invariant
    @model_validator(mode="after")
    def validate_adult_email_domain(self) -> "UserInput":
        """Enforce that minors cannot use .adult email domains.

        Returns:
            Self if the invariant holds.
        """
        if self.age < 18 and self.email.endswith(".adult"):
            raise UserValidationError("Minors cannot use .adult email domains.")
        return self

    # 5. @classmethod — alternate constructor
    @classmethod
    def from_dict(cls, data: dict) -> "UserInput":
        """Deserialize from a raw dict using Pydantic's own validator.

        Args:
            data: Raw dictionary from an external source.

        Returns:
            A validated UserInput instance.
        """
        return cls.model_validate(data)

    # 6. @computed_field — derived, never stored manually
    @computed_field
    @property
    def full_name(self) -> str:
        """Derive full name from first and last name fields.

        Returns:
            Space-joined full name string.
        """
        return f"{self.first_name} {self.last_name}"

    # 7. Public methods — produce new instance, never mutate
    def activate(self) -> "UserInput":
        """Return a copy of this user with status set to ACTIVE.

        Returns:
            A new UserInput instance with status=ACTIVE.
        """
        return self.model_copy(update={"status": UserStatus.ACTIVE})


# ── Internal model — extra="ignore" is acceptable ────────────────────────────

class UserInternal(BaseModel):
    """Internal representation passed between domain services.

    Not exposed to external input — extra="ignore" is acceptable here.

    Args:
        user_id: Internal integer identifier.
        full_name: Pre-computed display name.
        status: Current lifecycle status.

    Returns:
        None
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    user_id: int
    full_name: ShortStr
    status: UserStatus = UserStatus.PENDING


# ── Mutable model — frozen=False + validate_assignment=True ──────────────────

class UserDraft(BaseModel):
    """Mutable draft model for progressive form-filling or builder patterns.

    Opting out of frozen is justified here — the model is built incrementally.
    validate_assignment=True ensures mutations still pass through validators.
    Without it, direct assignment bypasses all Pydantic validation silently.

    Args:
        first_name: Draft first name, 1–50 characters.
        last_name: Draft last name, 1–50 characters.
        email: Valid email address.

    Returns:
        None
    """

    # frozen=False is intentional — this is a mutable draft.
    # validate_assignment=True is REQUIRED to keep validators active on mutation.
    model_config = ConfigDict(frozen=False, validate_assignment=True, extra="forbid")

    first_name: ShortStr = ""
    last_name: ShortStr = ""
    email: str = ""

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def parse_name_strip(cls, value: str) -> str:
        """Strip whitespace from name fields before parsing.

        Args:
            value: Raw string value for the field.

        Returns:
            Stripped string.
        """
        return value.strip()

    def is_complete(self) -> bool:
        """Check whether all required fields have been filled.

        Returns:
            True if first_name, last_name, and email are all non-empty.
        """
        return bool(self.first_name and self.last_name and self.email)


# ── BaseSettings — config from env vars ──────────────────────────────────────
# Requires: uv add pydantic-settings
#
# from pydantic_settings import BaseSettings, SettingsConfigDict
#
# class AppSettings(BaseSettings):
#     model_config = SettingsConfigDict(env_file=".env", extra="forbid")
#
#     db_url: str = Field(..., alias="DATABASE_URL")
#     debug: bool = Field(False)
#     api_key: str = Field(..., min_length=32)

AppSettings = None  # replace with BaseSettings subclass above


# ── Serialization / deserialization ──────────────────────────────────────────

def build_user_from_raw(data: dict) -> UserInput:
    """Parse and validate external data into a UserInput model.

    Args:
        data: Raw dict from an external source (API, CLI, file).

    Returns:
        A validated, immutable UserInput instance.
    """
    return UserInput.model_validate(data)


def get_user_payload(user: UserInput) -> dict:
    """Serialize a UserInput model to a plain dict for transport.

    Args:
        user: A validated UserInput domain model.

    Returns:
        Dictionary representation of the model.
    """
    return user.model_dump()
