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
    make_result,
    write_raw_config,
)

UTC = timezone.utc


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
# Test Alert dataclass
# ---------------------------------------------------------------------------


class TestAlert:
    def test_init_valid_alert_constructs(self):
        alert = make_alert()
        assert alert.name == "Test alert"

    def test_init_errors_include_common_prefix(self):
        alert_id = "test-id"
        with pytest.raises(ValueError, match=f"Alert '{alert_id}':"):
            # Negative lookback hours should raise
            make_alert(id=alert_id, lookback_hours=-1)

    @pytest.mark.parametrize("id", ["test_alert", "test alert"])
    def test_init_id_with_invalid_separator_raises(self, id: str):
        with pytest.raises(ValueError, match="alphanumeric characters"):
            make_alert(id=id)

    def test_init_long_name_raises(self):
        with pytest.raises(ValueError, match="must be <"):
            make_alert(name="a" * 101)

    @pytest.mark.parametrize("lookback_hours", [0, -1, -100])
    def test_init_nonpositive_lookback_hours_raises(self, lookback_hours: int):
        with pytest.raises(
            ValueError, match="lookback_hours must be a positive integer"
        ):
            make_alert(lookback_hours=lookback_hours)

    @pytest.mark.parametrize("fail_if", ["match", "no_match"])
    def test_init_valid_fail_if_values(self, fail_if: str):
        alert = make_alert(fail_if=fail_if)
        assert alert.fail_if == fail_if

    def test_init_invalid_fail_if_raises(self):
        with pytest.raises(ValueError, match="invalid fail_if value"):
            make_alert(fail_if="bad_value")

    @pytest.mark.parametrize("param", ["aws_sns_topic", "failure_message"])
    def test_init_empty_optional_param_raises(self, param: str):
        with pytest.raises(
            ValueError, match=f"{param} must be a non-empty string"
        ):
            make_alert(**{param: ""})

    def test_init_invalid_schedule_raises(self):
        with pytest.raises(
            ValueError, match="must be a 5-field cron expression"
        ):
            make_alert(schedule="0 * * *")

    def test_asdict_returns_correctly_populated_dictionary(self):
        alert_dict = SINGLE_ALERT.asdict()
        for field in dataclasses.fields(Alert):
            assert alert_dict[field.name] == getattr(SINGLE_ALERT, field.name)


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

    def test_sets_source_file_for_multiple_alerts(
        self, multi_alert_config: Path
    ):
        alerts = load_config(multi_alert_config)
        for alert in alerts:
            assert alert.source_file == str(multi_alert_config)

    def test_all_fields_match(self, single_alert_config: Path):
        alerts = load_config(single_alert_config)
        for field in dataclasses.fields(Alert):
            # Skip `source_file` because it is not set directly upon
            # Alert instantiation, and rather is set during `load_config()`.
            # We test this field specifically in other tests
            if field.name != "source_file":
                assert getattr(alerts[0], field.name) == getattr(
                    SINGLE_ALERT, field.name
                )

    def test_raises_when_no_alerts_key(self, tmp_path: Path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text(yaml.dump({}))
        with pytest.raises(ValueError, match="No top-level"):
            load_config(config_file)

    def test_raises_on_missing_required_field(self, tmp_path: Path):
        required_alert_fields = required_fields(Alert)
        for missing_field in required_alert_fields:
            raw_dict = {
                k: v
                for k, v in dataclasses.asdict(SINGLE_ALERT).items()
                if k != missing_field
            }
            config_file = write_raw_config(tmp_path / "svc.yml", [raw_dict])

            # Error prefix will differ depending on whether `Alert.id` is
            # the missing field
            match_prefix = (
                "alert #1" if missing_field == "id" else f"{SINGLE_ALERT.id}"
            )
            with pytest.raises(
                ValueError,
                match=(
                    f"{match_prefix}: missing required fields: {missing_field}"
                ),
            ):
                load_config(config_file)

    def test_error_message_includes_file_path(self, tmp_path: Path):
        config_file = write_raw_config(tmp_path / "empty.yml", [{}])
        with pytest.raises(ValueError, match=str(config_file)):
            # Empty alert should raise
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
# Test Result dataclass
# ---------------------------------------------------------------------------


class TestResult:
    def test_asdict_returns_correctly_populated_dictionary(self):
        result = make_result(status=ResultStatus.PASS)
        result_dict = result.asdict()

        for field in dataclasses.fields(Result):
            # Skip checking inner `alert` field, because it is an object and
            # so can't be compared directly. We'll check it separately below
            if field.name != "alert":
                # Serialize status for comparison
                if field.name == "status":
                    assert result_dict["status"] == result.status.name
                else:
                    assert result_dict[field.name] == getattr(
                        result, field.name
                    )

        for field in dataclasses.fields(Alert):
            assert result_dict["alert"][field.name] == getattr(
                SINGLE_ALERT, field.name
            )

    def test_get_message_prefers_configured_failure_message(self):
        failure_message = "test message"
        result = make_result(
            status=ResultStatus.FAIL, failure_message=failure_message
        )
        assert result.get_message() == failure_message

    @pytest.mark.parametrize(
        "fail_if,alert_override,expected_in_message",
        [
            ("no_match", {"log_group": "/my/group"}, "/my/group"),
            ("no_match", {"lookback_hours": 6}, "6"),
            ("no_match", {"log_query": "my_pattern"}, "my_pattern"),
            ("match", {"log_group": "/my/group"}, "/my/group"),
            ("match", {"lookback_hours": 6}, "6"),
            ("match", {"log_query": "my_pattern"}, "my_pattern"),
        ],
    )
    def test_get_message_falls_back_to_default_error_message(
        self, fail_if: str, alert_override: dict, expected_in_message: str
    ):
        result = make_result(
            status=ResultStatus.FAIL, fail_if=fail_if, **alert_override
        )
        message = result.get_message()
        assert expected_in_message in message

        if fail_if == "no_match":
            assert "No logs matching" in message

        if fail_if == "match":
            assert "Logs matching" in message


# ---------------------------------------------------------------------------
# Test ResultContainer dataclass
# ---------------------------------------------------------------------------


class TestResultContainer:
    def test_asdict_returns_correctly_populated_dictionary(self):
        result = make_result()
        result_container = ResultContainer(any_failed=False, results=[result])
        result_container_dict = result_container.asdict()
        result_dict = result_container_dict["results"][0]

        assert result_container_dict["any_failed"] is False
        for field in dataclasses.fields(Result):
            # Skip checking inner `alert` field, because it is an object and
            # so can't be compared directly. We'll check it separately below
            if field.name != "alert":
                # Serialize status for comparison
                if field.name == "status":
                    assert result_dict["status"] == result.status.name
                else:
                    assert result_dict[field.name] == getattr(
                        result, field.name
                    )

        for field in dataclasses.fields(Alert):
            assert result_dict["alert"][field.name] == getattr(
                SINGLE_ALERT, field.name
            )


# ---------------------------------------------------------------------------
# Test load_results()
# ---------------------------------------------------------------------------


class TestLoadResults:
    def test_loads_results_from_container_dict(self):
        container_dict = ResultContainer(
            any_failed=True,
            results=[
                Result(
                    alert=MULTI_ALERT_A,
                    status=ResultStatus.FAIL,
                ),
                Result(
                    alert=MULTI_ALERT_B,
                    status=ResultStatus.PASS,
                ),
            ],
        ).asdict()
        results = load_results(container_dict)

        assert len(results) == 2
        assert all(isinstance(r, Result) for r in results)
        assert results[0].alert.name == MULTI_ALERT_A.name
        assert results[0].status == ResultStatus.FAIL
        assert results[1].alert.name == MULTI_ALERT_B.name
        assert results[1].status == ResultStatus.PASS

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
        result = make_result()
        for missing_field in required_fields(Result):
            result_dict = {
                k: v for k, v in result.asdict().items() if k != missing_field
            }
            container_dict = {"any_failed": False, "results": [result_dict]}
            # Error message prefix will differ if `alert` is missing
            match_prefix = (
                "Result for Alert #1"
                if missing_field == "alert"
                else f"Result for Alert {SINGLE_ALERT.id}"
            )
            with pytest.raises(
                ValueError,
                match=f"{match_prefix} missing required fields",
            ):
                load_results(container_dict)

    def test_raises_on_missing_required_alert_fields(self):
        result = make_result()
        for missing_field in required_fields(Alert):
            result_dict = result.asdict()
            result_dict["alert"] = {
                k: v
                for k, v in SINGLE_ALERT.asdict().items()
                if k != missing_field
            }
            container_dict = {"any_failed": False, "results": [result_dict]}
            # Error message prefix will differ if `Alert.id` is missing
            match_prefix = (
                "Alert #1"
                if missing_field == "id"
                else f"Alert {SINGLE_ALERT.id}"
            )
            with pytest.raises(
                ValueError,
                match=f"{match_prefix} missing required fields",
            ):
                load_results(container_dict)

    def test_raises_on_invalid_result_status(self):
        result = make_result()
        result_dict = result.asdict()
        result_dict["status"] = "FOOBAR"
        container_dict = {"any_failed": False, "results": [result_dict]}
        with pytest.raises(ValueError, match="invalid status"):
            load_results(container_dict)
