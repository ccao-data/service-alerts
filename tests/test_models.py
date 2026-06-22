"""Unit tests for alerts/models.py."""

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from alerts.models import (
    Alert,
    Result,
    ResultContainer,
    ResultStatus,
    find_due_alerts,
    is_due,
    load_config,
    load_results,
    required_fields,
    validate_schedule,
)
from tests.conftest import (
    MULTI_ALERT_A,
    MULTI_ALERT_B,
    SINGLE_ALERT,
    make_alert,
    write_raw_config,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Test Alert dataclass
# ---------------------------------------------------------------------------


class TestAlert:
    def test_valid_alert_constructs(self):
        alert = make_alert()
        assert alert.name == "Test alert"

    def test_long_name_raises(self):
        with pytest.raises(ValueError, match="must be <"):
            make_alert(name="a" * 101)

    @pytest.mark.parametrize("fail_if", ["match", "no_match"])
    def test_valid_fail_if_values(self, fail_if: str):
        alert = make_alert(fail_if=fail_if)
        assert alert.fail_if == fail_if

    def test_invalid_fail_if_raises(self):
        with pytest.raises(ValueError, match="invalid fail_if value"):
            make_alert(fail_if="bad_value")

    @pytest.mark.parametrize("lookback_hours", [0, -1, -100])
    def test_nonpositive_lookback_hours_raises(self, lookback_hours: int):
        with pytest.raises(
            ValueError, match="lookback_hours must be a positive integer"
        ):
            make_alert(lookback_hours=lookback_hours)

    def test_empty_aws_sns_topic_raises(self):
        with pytest.raises(
            ValueError, match="aws_sns_topic must be a non-empty string"
        ):
            make_alert(aws_sns_topic="")

    def test_invalid_schedule_raises(self):
        with pytest.raises(
            ValueError, match="must be a 5-field cron expression"
        ):
            make_alert(schedule="0 * * *")


# ---------------------------------------------------------------------------
# Test Result dataclass
# ---------------------------------------------------------------------------


class TestResult:
    @pytest.mark.parametrize(
        "alert_override,expected_in_message",
        [
            ({"log_group": "/my/group"}, "/my/group"),
            ({"lookback_hours": 6}, "6"),
            ({"log_query": "my_pattern"}, "my_pattern"),
        ],
    )
    def test_get_message_returns_expected_content(
        self, alert_override: dict, expected_in_message: str
    ):
        alert = make_alert(fail_if="no_match", **alert_override)
        result = Result(alert=alert, status=ResultStatus.FAIL)

        assert expected_in_message in result.get_message()


# ---------------------------------------------------------------------------
# Test validate_schedule()
# ---------------------------------------------------------------------------


class TestValidateSchedule:
    @pytest.mark.parametrize(
        "schedule",
        [
            "0 0 * * *",  # midnight daily
            "0 12 * * *",  # noon daily
            "0 */3 * * *",  # every 3h
            "0 0,12 * * *",  # twice daily at aligned hours
            "0 12 1 * *",  # monthly at noon
            "0 0 * * 1",  # weekly Mondays at midnight
        ],
    )
    def test_valid_schedule_constructs(self, schedule: str):
        is_valid, message = validate_schedule(schedule)
        assert is_valid
        assert message == ""

    @pytest.mark.parametrize(
        "schedule",
        [
            "0 0 * *",  # Missing fifth operator
            "99 99 99 99 99",  # Invalid field values
        ],
    )
    def test_invalid_cron_expressions_raise(self, schedule: str):
        is_valid, reason = validate_schedule(schedule)
        assert not is_valid
        assert "cron expression" in reason

    def test_nonzero_minute_raises(self):
        is_valid, reason = validate_schedule("30 12 * * *")
        assert not is_valid
        assert "top of the hour" in reason

    @pytest.mark.parametrize(
        "schedule",
        [
            "0 * * * *",  # every hour (includes non-aligned hours)
            "0 1 * * *",  # 1am
            "0 */2 * * *",  # every 2h (includes 2, 4, 8, ...)
            "0 1,12 * * *",  # 1am and noon
        ],
    )
    def test_disallowed_hours_raises(self, schedule: str):
        is_valid, reason = validate_schedule(schedule)
        assert not is_valid
        assert "disallowed hours" in reason


# ---------------------------------------------------------------------------
# Test required_fields()
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_returns_required_fields(self):
        @dataclasses.dataclass
        class TestClass:
            required: str
            optional: str | None = None

        assert required_fields(TestClass) == ["required"]


# ---------------------------------------------------------------------------
# Test load_config()
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_alert_objects(self, single_alert_config: Path):
        alerts = load_config(single_alert_config)

        assert len(alerts) == 1
        for field in required_fields(Alert):
            assert getattr(alerts[0], field) == getattr(SINGLE_ALERT, field)

    def test_sets_source_file(self, single_alert_config: Path):
        alerts = load_config(single_alert_config)

        assert alerts[0].source_file == str(single_alert_config)

    def test_returns_multiple_alerts(self, multi_alert_config: Path):
        alerts = load_config(multi_alert_config)

        for field in required_fields(Alert):
            assert getattr(alerts[0], field) == getattr(MULTI_ALERT_A, field)
            assert getattr(alerts[1], field) == getattr(MULTI_ALERT_B, field)

    def test_all_alerts_get_source_file(self, multi_alert_config: Path):
        alerts = load_config(multi_alert_config)

        for alert in alerts:
            assert alert.source_file == str(multi_alert_config)

    def test_raises_when_no_alerts_key(self, tmp_path: Path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text(yaml.dump({}))

        with pytest.raises(ValueError, match="No top-level"):
            load_config(config_file)

    def test_raises_on_missing_required_field(self, tmp_path: Path):
        # We can't parameterize this test because it requires extracting
        # required fields from the Alert dataclass at runtime
        required_alert_fields = required_fields(Alert)
        for missing_field in required_alert_fields:
            raw_dict = {
                k: v
                for k, v in dataclasses.asdict(SINGLE_ALERT).items()
                if k != missing_field
            }
            config_file = write_raw_config(tmp_path / "svc.yml", [raw_dict])

            with pytest.raises(ValueError, match=missing_field):
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
        # Due: prev fire within the past 3 hours
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
            "0 */3 * * *",
            datetime(2026, 6, 16, 12, 45, tzinfo=UTC),
            True,
        ),  # 45 min ago (last fire at 12:00)
        (
            "0 12 * * *",
            datetime(2026, 6, 16, 14, 30, tzinfo=UTC),
            True,
        ),  # 2h30m ago — within 3h window
        # Not due: prev fire 3 hours or more ago
        (
            "0 12 * * *",
            datetime(2026, 6, 16, 15, 30, tzinfo=UTC),
            False,
        ),  # 3h30m ago
        (
            "0 12 * * *",
            datetime(2026, 6, 16, 15, 0, tzinfo=UTC),
            False,
        ),  # exactly 3h ago — strict < means boundary is not due
        (
            "0 12 1 * *",
            datetime(2026, 6, 16, 12, 30, tzinfo=UTC),
            False,
        ),  # monthly, 15 days ago
        (
            "0 0 * * *",
            datetime(2026, 6, 16, 12, 30, tzinfo=UTC),
            False,
        ),  # midnight daily, 12.5h ago
    ],
)
def test_is_due(schedule: str, now: datetime, expected: bool):
    assert is_due(schedule, now) is expected


# ---------------------------------------------------------------------------
# Test find_due_alerts()
# ---------------------------------------------------------------------------

# Schedule fires at 12:00 UTC daily; _FIND_NOW is 12:30 → alert is due.
_FIND_NOW = datetime(2026, 6, 16, 12, 30, tzinfo=UTC)


class TestFindDueAlerts:
    def test_returns_only_due_alerts(self, find_due_config: Path):
        due = find_due_alerts([find_due_config], _FIND_NOW)
        assert len(due) == 1
        assert due[0].name == "Due alert"

    def test_returns_empty_when_none_due(self, find_due_config: Path):
        now = datetime(2026, 6, 16, 15, 0, tzinfo=UTC)
        due = find_due_alerts([find_due_config], now)
        assert due == []

    def test_aggregates_across_multiple_config_files(
        self, tmp_path: Path, find_due_config: Path
    ):
        second_config = {
            "alerts": [
                {
                    "id": "another-due-alert",
                    "name": "Another due alert",
                    "log_group": "/h",
                    "log_query": "error",
                    "fail_if": "match",
                    "schedule": "0 12 * * *",
                    "lookback_hours": 6,
                }
            ]
        }
        second_file = tmp_path / "other.yml"
        second_file.write_text(yaml.dump(second_config))

        due = find_due_alerts([find_due_config, second_file], _FIND_NOW)

        assert len(due) == 2
        assert {a.name for a in due} == {"Due alert", "Another due alert"}


# ---------------------------------------------------------------------------
# Test load_results()
# ---------------------------------------------------------------------------


class TestLoadResults:
    def test_raises_on_missing_required_result_container_fields(self):
        for missing_field in required_fields(ResultContainer):
            container_dict = {
                k: v
                for k, v in {"any_failed": False, "results": []}.items()
                if k != missing_field
            }
            with pytest.raises(
                ValueError,
                match="ResultContainer object missing required fields",
            ):
                load_results(container_dict)

    def test_raises_on_missing_required_result_fields(self):
        valid_result = {
            "id": "test-alert",
            "name": "Test alert",
            "passed": True,
            "message": "PASS [Test alert]",
            "aws_sns_topic": None,
        }
        for missing_field in required_fields(Result):
            result_dict = {
                k: v for k, v in valid_result.items() if k != missing_field
            }
            container_dict = {"any_failed": False, "results": [result_dict]}
            with pytest.raises(ValueError, match="missing required fields"):
                load_results(container_dict)

    def test_loads_results_from_container_dict(self):
        container_dict = {
            "any_failed": True,
            "results": [
                {
                    "id": "alert-a",
                    "name": "Alert A",
                    "passed": False,
                    "message": "FAIL [Alert A]: ...",
                    "aws_sns_topic": "my-topic",
                },
                {
                    "id": "alert-b",
                    "name": "Alert B",
                    "passed": True,
                    "message": "PASS [Alert B]",
                    "aws_sns_topic": None,
                },
            ],
        }
        results = load_results(container_dict)

        assert len(results) == 2
        assert all(isinstance(r, Result) for r in results)
        assert results[0].alert.name == "Alert A"
        assert results[0].status == ResultStatus.FAIL
        assert results[0].alert.aws_sns_topic == "my-topic"
        assert results[1].alert.name == "Alert B"
        assert results[1].status == ResultStatus.PASS
        assert results[1].alert.aws_sns_topic is None
