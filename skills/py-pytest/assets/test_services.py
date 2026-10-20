"""Unit tests for src/your_package/services.py — one file, one module."""

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from src.your_package.exceptions import UserNotFoundError
from src.your_package.models import User, UserStatus
from src.your_package.services import UserService


@pytest.mark.unit
def test_get_active_returns_user_when_found(mocker: MockerFixture) -> None:
    """Returns the user object when the repository finds it."""
    mock_user = mocker.MagicMock(spec=User)
    mock_user.status = UserStatus.ACTIVE
    mock_repo = mocker.MagicMock()
    mock_repo.get_by_id.return_value = mock_user

    service = UserService(repository=mock_repo)

    # Act
    result = service.get_active(user_id=1)

    # Assert
    assert result == mock_user
    mock_repo.get_by_id.assert_called_once_with(1)


@pytest.mark.unit
def test_get_active_raises_when_repository_throws(mocker: MockerFixture) -> None:
    """Propagates UserNotFoundError when the repository raises it."""
    mock_repo = mocker.MagicMock()
    mock_repo.get_by_id.side_effect = UserNotFoundError("not found")

    service = UserService(repository=mock_repo)

    with pytest.raises(UserNotFoundError):
        service.get_active(user_id=99)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("user_id",),
    [(0,), (-1,), (999_999,)],
    ids=["zero-id", "negative-id", "large-id"],
)
def test_get_active_passes_id_to_repository(
    mocker: MockerFixture,
    user_id: int,
) -> None:
    """Forwards any integer user_id directly to the repository."""
    mock_repo = mocker.MagicMock()
    mock_repo.get_by_id.return_value = mocker.MagicMock(spec=User)

    service = UserService(repository=mock_repo)
    service.get_active(user_id=user_id)

    mock_repo.get_by_id.assert_called_once_with(user_id)
