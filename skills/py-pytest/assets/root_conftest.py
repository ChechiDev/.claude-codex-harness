"""Test selection rules of the Python profile (installed as the project's conftest.py).

Markers are the only criterion that sends a test to a job: `make test` runs
everything except `integration` and `e2e`; `make test-integration` and
`make test-e2e` run those. Two rules keep that honest:

- A test under tests/integration/ or tests/e2e/ must carry that marker: without it,
  it would run in `make test` (and its coverage) with no services. Collection fails.
- `make test` (REQUIRE_SELECTED_TESTS=1) fails when nothing ran because tests were
  deselected by anything other than the integration/e2e markers (a `-k` typo, a
  wrong `-m`): a suite deselected by mistake is red, never "no tests yet". A project
  whose only tests are integration/e2e stays green.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

DIR_MARKERS = {"integration": "integration", "e2e": "e2e"}
NO_TESTS_COLLECTED = 5
_DESELECTED: list[pytest.Item] = []


def _required_marker(item: pytest.Item, rootpath: Path) -> str | None:
    """The marker a test must carry because of where it lives, if any."""
    try:
        parts = item.path.relative_to(rootpath).parts
    except ValueError:
        return None
    if len(parts) > 2 and parts[0] == "tests":  # tests/<dir>/<file>
        return DIR_MARKERS.get(parts[1])
    return None


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Fail the collection when a test sits in a marker directory without it."""
    unmarked = [
        f"{item.nodeid} (add @pytest.mark.{marker})"
        for item in items
        if (marker := _required_marker(item, config.rootpath))
        and item.get_closest_marker(marker) is None
    ]
    if unmarked:
        raise pytest.UsageError(
            "tests outside make test's selection must carry their marker:\n  "
            + "\n  ".join(unmarked)
        )


def pytest_deselected(items: list[pytest.Item]) -> None:
    """Remember what -m/-k deselected, to judge an empty run."""
    _DESELECTED.extend(items)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """An empty run is red when tests other than integration/e2e were deselected."""
    if (
        exitstatus != NO_TESTS_COLLECTED
        or os.environ.get("REQUIRE_SELECTED_TESTS") != "1"
    ):
        return
    lost = [
        item.nodeid
        for item in _DESELECTED
        if not any(item.get_closest_marker(m) for m in DIR_MARKERS.values())
    ]
    if lost:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                f"no test ran: {len(lost)} test(s) deselected that make test must run "
                f"(e.g. {lost[0]}); check -k/-m/PYTEST_ADDOPTS",
                red=True,
            )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
