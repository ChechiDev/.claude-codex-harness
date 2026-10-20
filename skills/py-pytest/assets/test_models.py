"""Unit tests for src/your_package/models.py — one file, one module."""

from __future__ import annotations

import pytest

from src.your_package.exceptions import UserValidationError
from src.your_package.models import User, UserStatus


@pytest.mark.unit
def test_full_name_combines_first_and_last() -> None:
    """computed_field full_name joins first and last with a space."""
    user = User(first_name="Ada", last_name="Lovelace", email="ada@example.com", age=30)

    assert user.full_name == "Ada Lovelace"


@pytest.mark.unit
def test_activate_returns_new_instance_with_active_status() -> None:
    """activate() produces a new User with ACTIVE status, original unchanged."""
    user = User(first_name="Ada", last_name="Lovelace", email="ada@example.com", age=30)

    activated = user.activate()

    assert activated.status == UserStatus.ACTIVE
    assert user.status == UserStatus.PENDING


@pytest.mark.unit
def test_model_is_immutable_after_creation() -> None:
    """Direct field assignment raises ValidationError because model is frozen."""
    from pydantic import ValidationError

    user = User(first_name="Ada", last_name="Lovelace", email="ada@example.com", age=30)

    with pytest.raises(ValidationError):
        user.first_name = "Changed"  # type: ignore[misc]


@pytest.mark.unit
def test_raises_validation_error_with_invalid_email() -> None:
    """Raises ValidationError with a message mentioning 'email' for bad input."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match=r"email"):
        User(first_name="Ada", last_name="Lovelace", email="not-an-email", age=30)


@pytest.mark.unit
def test_age_field_accepts_boundary_values() -> None:
    """Accepts age=0 and age=120 as valid boundary values."""
    young = User(first_name="Ada", last_name="L", email="a@b.com", age=0)
    old = User(first_name="Ada", last_name="L", email="a@b.com", age=120)

    assert young.age == pytest.approx(0)
    assert old.age == pytest.approx(120)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("age", "email", "should_raise"),
    [
        (17, "minor@example.adult", True),
        (18, "adult@example.adult", False),
        (30, "user@example.com", False),
    ],
    ids=["minor-adult-domain", "adult-adult-domain", "normal-case"],
)
def test_validate_adult_email_domain(
    age: int,
    email: str,
    should_raise: bool,
) -> None:
    """Raises UserValidationError only when age < 18 and email ends with .adult."""
    from pydantic import ValidationError

    if should_raise:
        with pytest.raises((ValidationError, UserValidationError)):
            User(first_name="Test", last_name="User", email=email, age=age)
    else:
        user = User(first_name="Test", last_name="User", email=email, age=age)
        assert user.age == age
