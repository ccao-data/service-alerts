"""Unit tests for check_alerts.py."""

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from check_alerts import (
    Alert,
    evaluate_alert,
    is_due,
    load_config,
    query_cloudwatch,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def make_alert(**overrides) -> Alert:
    """Return a minimal valid Alert, with optional field overrides."""
    base = {
        "name": "Test alert",
        "log_group": "/test/logs",
        "log_query": "info",
        "error_if": "no_match",
        "lookback_hours": 12,
        "schedule": "0 12 * * *",
        "source_file": "test.yml",
    }
    return Alert(**{**base, **overrides})


def make_paginator(*pages) -> MagicMock:
    """Return a mock CloudWatch client whose paginator yields *pages*."""
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = iter(pages)
    return client


def write_config(path: Path, alerts: list[Alert]) -> Path:
    """Write an alerts config YAML to *path* and return it."""
    path.write_text(
        yaml.dump({"alerts": [dataclasses.asdict(alert) for alert in alerts]})
    )
    return path


def write_raw_config(path: Path, alerts: list[dict]) -> Path:
    """Alternate version of `write_config()` that writes a raw dict to the
    config YAML. Useful when writing"""
    path.write_text(yaml.dump({"alerts": alerts}))
    return path


# ---------------------------------------------------------------------------
# Test Alert dataclass
# ---------------------------------------------------------------------------


class TestAlert:
    def test_valid_alert_constructs(self):
        alert = make_alert()
        assert alert.name == "Test alert"

    @pytest.mark.parametrize("error_if", ["match", "no_match"])
    def test_valid_error_if_values(self, error_if: str):
        alert = make_alert(error_if=error_if)
        assert alert.error_if == error_if

    def test_invalid_error_if_raises(self):
        with pytest.raises(ValueError, match="invalid error_if value"):
            make_alert(error_if="bad_value")

    @pytest.mark.parametrize("lookback_hours", [0, -1, -100])
    def test_nonpositive_lookback_hours_raises(self, lookback_hours: int):
        with pytest.raises(
            ValueError, match="lookback_hours must be a positive integer"
        ):
            make_alert(lookback_hours=lookback_hours)


# ---------------------------------------------------------------------------
# Test load_config()
# ---------------------------------------------------------------------------

SINGLE_ALERT = make_alert()


@pytest.fixture
def single_alert_config(tmp_path: Path) -> Path:
    """Write a single-alert config to a temp file and return its path."""
    return write_config(tmp_path / "svc.yml", [SINGLE_ALERT])


MULTI_ALERT_A = make_alert(
    name="A",
    log_group="/g",
    log_query="x",
    error_if="match",
    schedule="0 * * * *",
    lookback_hours=1,
)
MULTI_ALERT_B = make_alert(
    name="B",
    log_group="/g",
    log_query="y",
    error_if="no_match",
    schedule="0 * * * *",
    lookback_hours=1,
)


@pytest.fixture
def multi_alert_config(tmp_path: Path) -> Path:
    """Write a two-alert config to a temp file and return its path."""
    return write_config(
        tmp_path / "svc.yml",
        [MULTI_ALERT_A, MULTI_ALERT_B],
    )


class TestLoadConfig:
    def test_returns_alert_objects(self, single_alert_config: Path):
        alerts = load_config(single_alert_config)

        assert len(alerts) == 1
        assert isinstance(alerts[0], Alert)
        assert alerts[0].name == SINGLE_ALERT.name

    def test_sets_source_file(self, single_alert_config: Path):
        alerts = load_config(single_alert_config)

        assert alerts[0].source_file == str(single_alert_config)

    def test_returns_multiple_alerts(self, multi_alert_config: Path):
        alerts = load_config(multi_alert_config)

        assert len(alerts) == 2
        assert [a.name for a in alerts] == [
            MULTI_ALERT_A.name,
            MULTI_ALERT_B.name,
        ]

    def test_all_alerts_get_source_file(self, multi_alert_config: Path):
        alerts = load_config(multi_alert_config)

        for alert in alerts:
            assert alert.source_file == str(multi_alert_config)

    def test_raises_when_no_alerts_key(self, tmp_path: Path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text(yaml.dump({}))

        with pytest.raises(ValueError, match="No top-level"):
            load_config(config_file)

    @pytest.mark.parametrize(
        "missing_field",
        [
            "name",
            "log_group",
            "log_query",
            "error_if",
            "schedule",
            "lookback_hours",
        ],
    )
    def test_raises_on_missing_required_field(
        self, tmp_path: Path, missing_field: str
    ):
        raw_dict = {
            k: v
            for k, v in dataclasses.asdict(SINGLE_ALERT).items()
            if k != missing_field
        }
        config_file = write_raw_config(tmp_path / "svc.yml", [raw_dict])

        with pytest.raises(ValueError, match=missing_field):
            load_config(config_file)

    def test_raises_on_invalid_error_if(self, tmp_path: Path):
        raw = dataclasses.replace(SINGLE_ALERT)
        raw.error_if = "bad_value"
        config_file = write_config(tmp_path / "svc.yml", [raw])

        with pytest.raises(ValueError, match="invalid error_if value"):
            load_config(config_file)

    def test_raises_on_nonpositive_lookback_hours(self, tmp_path: Path):
        raw = dataclasses.replace(SINGLE_ALERT)
        raw.lookback_hours = 0
        config_file = write_config(tmp_path / "svc.yml", [raw])

        with pytest.raises(
            ValueError, match="lookback_hours must be a positive integer"
        ):
            load_config(config_file)

    def test_error_message_includes_file_path(self, tmp_path: Path):
        raw_dict = {
            k: v
            for k, v in dataclasses.asdict(SINGLE_ALERT).items()
            if k != "lookback_hours"
        }
        config_file = write_raw_config(tmp_path / "svc.yml", [raw_dict])

        with pytest.raises(ValueError, match=str(config_file)):
            load_config(config_file)


# ---------------------------------------------------------------------------
# Test is_due()
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
# Test query_cloudwatch()
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
# Test evaluate_alert()
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
        assert alert.name in message


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
