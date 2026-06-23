"""Shared test helpers and fixtures for the alerts test suite."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from alerts.models import Alert, Result, ResultStatus

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
    *, status: ResultStatus = ResultStatus.FAIL, **alert_overrides
) -> Result:
    """Return a minimal alert result with optional overrides."""
    alert = make_alert(**alert_overrides)
    return Result(
        alert=alert,
        status=status,
    )


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

SINGLE_ALERT = make_alert()

MULTI_ALERT_A = make_alert(
    id="a",
    name="A",
    log_group="/g",
    log_query="x",
    fail_if="match",
    schedule="0 0 * * *",
    lookback_hours=1,
)
MULTI_ALERT_B = make_alert(
    id="b",
    name="B",
    log_group="/g",
    log_query="y",
    fail_if="no_match",
    schedule="0 0 * * *",
    lookback_hours=1,
)


@pytest.fixture
def single_alert_config(tmp_path: Path) -> Path:
    """Write a single-alert config to a temp file and return its path."""
    return write_config(tmp_path / "svc.yml", [SINGLE_ALERT])


@pytest.fixture
def multi_alert_config(tmp_path: Path) -> Path:
    """Write a two-alert config to a temp file and return its path."""
    return write_config(tmp_path / "svc.yml", [MULTI_ALERT_A, MULTI_ALERT_B])


@pytest.fixture
def find_due_config(tmp_path: Path) -> Path:
    """Config with one due alert (daily at 12:00) and one not-due alert (monthly)."""
    config = {
        "alerts": [
            {
                "id": "due-alert",
                "name": "Due alert",
                "log_group": "/g",
                "log_query": "info",
                "fail_if": "no_match",
                "schedule": "0 12 * * *",
                "lookback_hours": 12,
            },
            {
                "id": "not-due-alert",
                "name": "Not due alert",
                "log_group": "/g",
                "log_query": "info",
                "fail_if": "no_match",
                "schedule": "0 12 1 * *",  # monthly — not due on the 16th
                "lookback_hours": 12,
            },
        ]
    }
    path = tmp_path / "svc.yml"
    path.write_text(yaml.dump(config))
    return path
