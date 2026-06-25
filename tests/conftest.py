"""Shared test helpers and fixtures for the alerts test suite."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from alerts.models import Alert, Result, ResultContainer, ResultStatus

ACCOUNT_ID = "123456789012"
TOPIC_NAME = "my-topic"
TOPIC_ARN = f"arn:aws:sns:us-east-1:{ACCOUNT_ID}:{TOPIC_NAME}"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def make_alert(**overrides) -> Alert:
    """Return a minimal valid Alert, with optional field overrides."""
    base = {
        "id": "test-alert",
        "name": "Test alert",
        "log_group": "/test/logs",
        "log_query": "info",
        "fail_if": "no_match",
        "lookback_hours": 12,
        "schedule": "0 12 * * *",
        "aws_sns_topic": TOPIC_NAME,
    }
    return Alert(**{**base, **overrides})  # ty: ignore[invalid-argument-type]


def make_result(
    *,
    status: ResultStatus = ResultStatus.FAIL,
    alert: Alert | None = None,
    **alert_overrides,
) -> Result:
    """Return a minimal alert result with optional overrides. If the `alert`
    param is present, populates the Result with that Alert object directly;
    otherwise, uses any remaining kwargs to populate an alert."""
    parsed_alert = alert or make_alert(**alert_overrides)
    return Result(
        alert=parsed_alert,
        status=status,
    )


def make_result_container(
    *,
    any_failed: bool = True,
    results: list[Result] | None = None,
    result_status: ResultStatus = ResultStatus.FAIL,
    **alert_overrides,
) -> ResultContainer:
    """Return a ResultContainer object with optional overrides. If the
    `results` param is present, populates the container with those results
    directly; otherwise, uses `result_status` to populate a single result."""
    parsed_results = results or [make_result(status=result_status)]
    return ResultContainer(any_failed=any_failed, results=parsed_results)


def make_paginator(*pages) -> MagicMock:
    """Return a mock CloudWatch client whose paginator yields *pages*."""
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = iter(pages)
    return client


def make_sns_client() -> MagicMock:
    """Return a mock AWS SNS client."""
    return MagicMock()


def write_config(path: Path, alerts: list[Alert]) -> Path:
    """Write an alerts config YAML to *path* and return it."""
    path.write_text(
        yaml.dump({"alerts": [alert.as_dict() for alert in alerts]})
    )
    return path


def write_raw_config(path: Path, alerts: list[dict]) -> Path:
    """Alternate version of `write_config()` that writes a raw dict to the
    config YAML. Useful when writing invalid or partial configs for error testing."""
    path.write_text(yaml.dump({"alerts": alerts}))
    return path


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alert() -> Alert:
    """Simple fixture for a simple Alert object. Use this fixture whenever
    you need a default alert with no modification; since it is a
    function-scoped fixture, you can mutate it as necessary."""
    return make_alert()


@pytest.fixture
def two_alerts() -> tuple[Alert, Alert]:
    """Simple fixture returning a tuple of two Alert objects. Similar
    to the `alert` fixture, but includes two alerts."""
    return (
        make_alert(
            id="a",
            name="A",
            log_group="/g",
            log_query="x",
            fail_if="match",
            schedule="0 0 * * *",
            lookback_hours=1,
        ),
        make_alert(
            id="b",
            name="B",
            log_group="/g",
            log_query="y",
            fail_if="no_match",
            schedule="0 0 * * *",
            lookback_hours=1,
        ),
    )


@pytest.fixture
def alert_config(alert: Alert, tmp_path: Path) -> Path:
    """Write a single-alert config to a temp file and return its path."""
    return write_config(tmp_path / "svc.yml", [alert])


@pytest.fixture
def two_alert_config(two_alerts: tuple[Alert, Alert], tmp_path: Path) -> Path:
    """Write a two-alert config to a temp file and return its path."""
    return write_config(tmp_path / "svc.yml", list(two_alerts))


@pytest.fixture
def find_due_config(tmp_path: Path) -> Path:
    """Config with one due alert (daily at 12:00) and one not-due alert (monthly)."""
    config = {
        "alerts": [
            make_alert(
                id="due-alert",
                name="Due alert",
                schedule="0 12 * * *",
            ).as_dict(),
            make_alert(
                id="not-due-alert",
                name="Not due alert",
                schedule="0 12 1 * *",  # monthly — not due on the 16th
            ).as_dict(),
        ]
    }
    path = tmp_path / "svc.yml"
    path.write_text(yaml.dump(config))
    return path


@pytest.fixture
def result() -> Result:
    """Simple fixture for a simple Result object."""
    return make_result()


@pytest.fixture
def result_container() -> ResultContainer:
    """Simple fixture for a sipmle ResultContainer object with one
    failing result."""
    return make_result_container()


@pytest.fixture
def sns_client() -> MagicMock:
    """Simple fixture returning a mocked AWS SNS client."""
    return make_sns_client()
