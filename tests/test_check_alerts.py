"""Unit tests for check_alerts.py."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from check_alerts import evaluate_alert, is_due, load_config, query_cloudwatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def make_alert(**overrides) -> dict:
    """Return a minimal valid alert dict, with optional field overrides."""
    base = {
        "name": "Test alert",
        "log_group": "/test/logs",
        "log_query": "info",
        "error_if": "no_match",
        "lookback_hours": 12,
        "schedule": "0 12 * * *",
        "_source_file": "test.yml",
    }
    return {**base, **overrides}


def make_paginator(*pages) -> MagicMock:
    """Return a mock CloudWatch client whose paginator yields *pages*."""
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = iter(pages)
    return client


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


@pytest.fixture
def multi_alert_config(tmp_path: Path) -> Path:
    """Write a two-alert config to a temp file and return its path."""
    config = {
        "alerts": [
            {
                "name": "A",
                "log_group": "/g",
                "log_query": "x",
                "error_if": "match",
                "schedule": "0 * * * *",
                "lookback_hours": 1,
            },
            {
                "name": "B",
                "log_group": "/g",
                "log_query": "y",
                "error_if": "no_match",
                "schedule": "0 * * * *",
                "lookback_hours": 1,
            },
        ]
    }
    path = tmp_path / "svc.yml"
    path.write_text(yaml.dump(config))
    return path


class TestLoadConfig:
    def test_returns_alerts_with_source_file(self, tmp_path: Path):
        config = {
            "alerts": [
                {
                    "name": "My alert",
                    "log_group": "/my/group",
                    "log_query": "error",
                    "error_if": "match",
                    "schedule": "0 12 * * *",
                    "lookback_hours": 6,
                }
            ]
        }
        config_file = tmp_path / "my-service.yml"
        config_file.write_text(yaml.dump(config))

        alerts = load_config(config_file)

        assert len(alerts) == 1
        assert alerts[0]["name"] == "My alert"
        assert alerts[0]["_source_file"] == str(config_file)

    def test_returns_multiple_alerts(self, multi_alert_config: Path):
        alerts = load_config(multi_alert_config)

        assert len(alerts) == 2
        assert [a["name"] for a in alerts] == ["A", "B"]

    def test_returns_empty_list_when_no_alerts_key(self, tmp_path: Path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text(yaml.dump({}))

        alerts = load_config(config_file)

        assert alerts == []

    def test_all_alerts_get_source_file(self, multi_alert_config: Path):
        alerts = load_config(multi_alert_config)

        for alert in alerts:
            assert alert["_source_file"] == str(multi_alert_config)


# ---------------------------------------------------------------------------
# is_due
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "schedule,now,expected",
    [
        # Due: prev fire within the past hour
        (
            "0 12 * * *",
            datetime(2026, 6, 16, 12, 30, tzinfo=UTC),
            True,
        ),  # 30 min ago
        (
            "0 12 * * *",
            datetime(2026, 6, 16, 12, 59, tzinfo=UTC),
            True,
        ),  # 59 min ago
        (
            "0 * * * *",
            datetime(2026, 6, 16, 12, 45, tzinfo=UTC),
            True,
        ),  # hourly, 45 min ago
        (
            "0 */2 * * *",
            datetime(2026, 6, 16, 12, 30, tzinfo=UTC),
            True,
        ),  # every 2h, 30 min ago
        # Not due: prev fire more than an hour ago
        (
            "0 12 * * *",
            datetime(2026, 6, 16, 13, 30, tzinfo=UTC),
            False,
        ),  # 90 min ago
        (
            "0 12 1 * *",
            datetime(2026, 6, 16, 12, 30, tzinfo=UTC),
            False,
        ),  # monthly, 15 days ago
        (
            "0 */2 * * *",
            datetime(2026, 6, 16, 13, 30, tzinfo=UTC),
            False,
        ),  # every 2h, 90 min ago
    ],
)
def test_is_due(schedule: str, now: datetime, expected: bool):
    assert is_due(schedule, now) is expected


# ---------------------------------------------------------------------------
# query_cloudwatch
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "pages,expected",
    [
        ([{"events": [{"message": "hit"}]}], True),  # events on only page
        ([{"events": []}], False),  # empty events list
        ([{}], False),  # events key missing
        (
            [{"events": [{"message": "hit"}]}, {"events": []}],
            True,
        ),  # events on first page
        (
            [{"events": []}, {"events": [{"message": "hit"}]}],
            True,
        ),  # events on second page
    ],
)
def test_query_cloudwatch_match(pages: list, expected: bool):
    client = make_paginator(*pages)
    assert query_cloudwatch("/test/logs", "info", 12, _NOW, client) is expected


class TestQueryCloudwatchArgs:
    """Tests that verify the correct arguments are forwarded to the paginator."""

    def test_passes_correct_time_window(self):
        client = make_paginator({"events": []})

        query_cloudwatch("/test/logs", "info", 12, _NOW, client)

        _, kwargs = client.get_paginator.return_value.paginate.call_args
        assert kwargs["startTime"] == int(
            (_NOW.timestamp() - 12 * 3600) * 1000
        )
        assert kwargs["endTime"] == int(_NOW.timestamp() * 1000)

    def test_passes_correct_filter_pattern_and_log_group(self):
        client = make_paginator({"events": []})

        query_cloudwatch("/test/logs", "my_pattern", 6, _NOW, client)

        _, kwargs = client.get_paginator.return_value.paginate.call_args
        assert kwargs["filterPattern"] == "my_pattern"
        assert kwargs["logGroupName"] == "/test/logs"


# ---------------------------------------------------------------------------
# evaluate_alert
# ---------------------------------------------------------------------------

_EVAL_NOW = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "error_if,found,expected_passed,expected_prefix",
    [
        (
            "no_match",
            False,
            False,
            "FAIL",
        ),  # no logs found when required → fail
        ("no_match", True, True, "PASS"),  # logs found when required → pass
        ("match", True, False, "FAIL"),  # logs found when forbidden → fail
        ("match", False, True, "PASS"),  # no logs found when forbidden → pass
    ],
)
def test_evaluate_alert_pass_fail(
    error_if: str, found: bool, expected_passed: bool, expected_prefix: str
):
    alert = make_alert(error_if=error_if)
    events = [{"message": "hit"}] if found else []
    client = make_paginator({"events": events})

    passed, message = evaluate_alert(alert, _EVAL_NOW, client)

    assert passed is expected_passed
    assert expected_prefix in message
    if not expected_passed:
        assert alert["name"] in message


@pytest.mark.parametrize(
    "alert_override,expected_in_message",
    [
        ({"log_group": "/my/group"}, "/my/group"),
        ({"lookback_hours": 6}, "6"),
        ({"log_query": "my_pattern"}, "my_pattern"),
    ],
)
def test_evaluate_alert_fail_message_content(
    alert_override: dict, expected_in_message: str
):
    alert = make_alert(error_if="no_match", **alert_override)
    client = make_paginator({"events": []})

    _, message = evaluate_alert(alert, _EVAL_NOW, client)

    assert expected_in_message in message
