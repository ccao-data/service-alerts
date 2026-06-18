"""Shared data models and config-loading utilities."""

import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, Type

import yaml
from croniter import croniter

from alerts.constants import ALLOWED_SCHEDULE_HOURS, CHECK_WINDOW_HOURS


class DataclassType(Protocol):
    """Custom type to help us annotate functions that accept a class that is
    defined as a dataclass."""

    __dataclass_fields__: dict


@dataclasses.dataclass
class Alert:
    """Configuration for a single CloudWatch log alert.

    Attributes:
        name: Unique human-readable name shown in output and failure messages.
        log_group: CloudWatch log group to search.
        log_query: Filter pattern passed to `filter_log_events`. Supports
            CloudWatch filter pattern syntax.
        error_if: Condition that triggers a failure. `"no_match"` fails when
            no events are found (e.g. job hasn't run); `"match"` fails when
            events are found (e.g. errors were present in logs).
        schedule: 5-field UTC cron expression for when this alert should be
            evaluated. Checked against the past hour each time the workflow runs.
        lookback_hours: How far back (in hours) to search for matching log events.
            Must be a positive integer.
        source_file: Path to the config file this alert was loaded from.
        aws_sns_topic: Optional SNS topic name to notify when this alert fails.
            The full ARN is constructed at runtime; only the topic name is stored
            here to avoid embedding the AWS account ID in config files.
    """

    name: str
    log_group: str
    log_query: str
    error_if: Literal["match", "no_match"]
    schedule: str
    lookback_hours: int
    source_file: str | None = None
    aws_sns_topic: str | None = None

    def __post_init__(self) -> None:
        """Field validation for this dataclass."""
        # We use alert names in the subject lines of the notification, so
        # prevent them from being too long
        if len(self.name) > 100:
            raise ValueError(
                f"Alert '{self.name}': name must be < 100 characters"
            )
        if self.lookback_hours <= 0:
            raise ValueError(
                f"Alert '{self.name}': lookback_hours must be a positive integer, "
                f"got {self.lookback_hours!r}"
            )
        if self.aws_sns_topic is not None and not self.aws_sns_topic.strip():
            raise ValueError(
                f"Alert '{self.name}': aws_sns_topic must be a non-empty string "
                f"if set, got {self.aws_sns_topic!r}"
            )

        valid_error_if = frozenset({"match", "no_match"})
        if self.error_if not in valid_error_if:
            raise ValueError(
                f"Alert '{self.name}': invalid error_if value '{self.error_if}'. "
                f"Must be one of: {sorted(valid_error_if)}"
            )

        valid_schedule, reason = validate_schedule(self.schedule)
        if not valid_schedule:
            raise ValueError(f"Alert '{self.name}': {reason}")


def validate_schedule(schedule) -> tuple[bool, str]:
    """Validate that a cron expression representing an Alert schedule only fires
    at permitted times.

    If a schedule is valid, returns a tuple with two elements: True, and an
    empty string. If a schedule is invalid, returns a tuple containing False,
    and a string representing the reason why the schedule is invalid."""
    parts = schedule.split()
    if len(parts) != 5:
        return False, (
            f"Schedule must be a 5-field cron expression, "
            f"got {len(parts)} fields"
        )

    try:
        expanded, _ = croniter.expand(schedule)
    except Exception as exc:
        return False, f"Invalid cron expression: {exc}"

    minute_values = set(expanded[0])
    hour_values = set(expanded[1])
    if minute_values != {0}:
        return False, (
            f"Schedule must only fire at the top of the "
            f"hour (minute field must be 0), got minutes: {sorted(minute_values)}"
        )
    if not hour_values.issubset(ALLOWED_SCHEDULE_HOURS):
        invalid_hours = sorted(hour_values - ALLOWED_SCHEDULE_HOURS)
        return False, (
            f"Schedule fires at disallowed hours: {invalid_hours}."
            f"Must only fire at: {sorted(ALLOWED_SCHEDULE_HOURS)}"
        )

    return True, ""


def required_fields(dataclass: Type[DataclassType]) -> list[str]:
    return [
        field.name
        for field in dataclasses.fields(dataclass)
        if field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING  # type: ignore[misc]
    ]


def load_config(path: Path) -> list[Alert]:
    """Load, validate, and return the list of alerts from a YAML config file.

    Raises:
        ValueError: If any alert is missing required fields or has invalid values.
    """
    with path.open() as f:
        data = yaml.safe_load(f)

    raw_alerts = data.get("alerts", [])
    if not raw_alerts:
        raise ValueError(f"{path}: No top-level `alerts` key found")

    alerts = []
    # Exclude optional fields from the required-field check
    required_alert_fields = required_fields(Alert)
    for i, raw in enumerate(raw_alerts):
        missing = [f for f in required_alert_fields if f not in raw]
        if missing:
            label = raw.get("name", f"alert #{i + 1}")
            raise ValueError(
                f"{path}: {label}: missing required fields: {missing}"
            )
        try:
            alert = Alert(
                name=raw["name"],
                log_group=raw["log_group"],
                log_query=raw["log_query"],
                error_if=raw["error_if"],
                schedule=raw["schedule"],
                lookback_hours=raw["lookback_hours"],
                source_file=str(path),
                aws_sns_topic=raw.get("aws_sns_topic"),
            )
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        alerts.append(alert)

    return alerts


def is_due(schedule: str, now: datetime) -> bool:
    """Return True if the cron schedule fired within the past CHECK_WINDOW_HOURS."""
    cron = croniter(schedule, now)
    prev_fire = cron.get_prev(datetime)
    elapsed_seconds = (now - prev_fire).total_seconds()
    return elapsed_seconds < CHECK_WINDOW_HOURS * 3600


def find_due_alerts(config_files: list[Path], now: datetime) -> list[Alert]:
    """Load all config files and return only the alerts due at `now`."""
    all_alerts: list[Alert] = []
    for path in config_files:
        all_alerts.extend(load_config(path))
    return [a for a in all_alerts if is_due(a.schedule, now)]


@dataclasses.dataclass
class Result:
    """Expected structure for a status result for an alert that has been
    checked."""

    name: str
    passed: bool
    message: str
    aws_sns_topic: str | None = None


@dataclasses.dataclass
class ResultContainer:
    """Container for one or more status results that have been checked."""

    any_failed: bool
    results: list[Result]


def load_results(result_container_dict: dict) -> list[Result]:
    """Load a list of status results from a dictionary representation of the
    result container."""
    required_result_container_fields = required_fields(ResultContainer)
    missing_result_container_fields = [
        f
        for f in required_result_container_fields
        if f not in result_container_dict
    ]
    if missing_result_container_fields:
        raise ValueError(
            "ResultContainer object missing required fields: "
            f"{missing_result_container_fields}"
        )

    required_result_fields = required_fields(Result)
    results: list[Result] = []
    for i, result_dict in enumerate(result_container_dict["results"]):
        missing_result_fields = [
            f for f in required_result_fields if f not in result_dict
        ]
        if missing_result_fields:
            label = result_dict.get("name", f"result #{i + 1}")
            raise ValueError(
                f"Result '{label}' missing required fields: "
                f"{missing_result_fields}"
            )
        result = Result(
            name=result_dict["name"],
            passed=result_dict["passed"],
            message=result_dict["message"],
            aws_sns_topic=result_dict.get("aws_sns_topic"),
        )
        results.append(result)
    return results
