"""Canonical conftest.py structure — one per level, narrowest scope first."""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# tests/conftest.py — global fixtures, available to all test types
# ─────────────────────────────────────────────────────────────────────────────

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def app_settings():
    """Load test-specific application settings once for the entire session.

    Returns:
        AppSettings instance pointing to test environment.
    """
    from src.your_package.config import AppSettings
    return AppSettings(env="test")  # type: ignore[call-arg]


@pytest_asyncio.fixture(scope="session")
async def http_client():
    """Provide a shared async HTTP client for the full test session.

    Returns:
        An async HTTP client instance, closed after the session ends.
    """
    import httpx
    async with httpx.AsyncClient() as client:
        yield client


# ─────────────────────────────────────────────────────────────────────────────
# tests/unit/conftest.py — fixtures scoped to unit tests only
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_user(mocker):
    """Return a MagicMock shaped like a User domain object.

    Returns:
        MagicMock with User spec and ACTIVE status.
    """
    from src.your_package.models import User, UserStatus
    user = mocker.MagicMock(spec=User)
    user.status = UserStatus.ACTIVE
    user.full_name = "Ada Lovelace"
    return user


# ─────────────────────────────────────────────────────────────────────────────
# tests/integration/conftest.py — fixtures scoped to integration tests only
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def db_session():
    """Provide a real async DB session, rolled back after each test.

    Yields:
        An active AsyncSession connected to the test database.
    """
    from src.your_package.database import create_test_session
    async with create_test_session() as session:
        yield session
        await session.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# tests/e2e/conftest.py — fixtures scoped to e2e tests only
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def live_client():
    """Provide an async HTTP client for live network calls during e2e tests.

    Returns:
        httpx.AsyncClient with a realistic timeout for external requests.
    """
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client
