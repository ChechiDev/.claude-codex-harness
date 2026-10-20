"""Async unit + e2e tests for src/your_package/scraper.py — one file, one module."""

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from src.your_package.scraper import fetch_page, scrape


# ── Unit tests — mock the HTTP client ────────────────────────────────────────

@pytest.mark.unit
async def test_fetch_page_returns_text_on_success(mocker: MockerFixture) -> None:
    """Returns page text when the HTTP client responds with 200."""
    mock_client = mocker.AsyncMock()
    mock_client.get.return_value.text = "<html>hello</html>"

    result = await fetch_page(client=mock_client, url="https://example.com")

    assert "hello" in result
    mock_client.get.assert_awaited_once_with("https://example.com")


@pytest.mark.unit
async def test_fetch_page_raises_on_http_error(mocker: MockerFixture) -> None:
    """Propagates HTTPError when the client raises it."""
    import httpx

    mock_client = mocker.AsyncMock()
    mock_client.get.side_effect = httpx.HTTPError("timeout")

    with pytest.raises(httpx.HTTPError):
        await fetch_page(client=mock_client, url="https://example.com")


# ── E2E tests — real network, no mock ─────────────────────────────────────────

@pytest.mark.e2e
async def test_scrape_returns_at_least_one_result(live_client) -> None:
    """Scraper returns a non-empty result list against the live target."""
    results = await scrape(client=live_client, url="https://example.com")

    assert len(results) > 0


@pytest.mark.e2e
async def test_scrape_results_have_required_fields(live_client) -> None:
    """Each scraped item contains the expected keys."""
    results = await scrape(client=live_client, url="https://example.com")

    for item in results:
        assert "title" in item
        assert "url" in item
