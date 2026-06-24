"""Shared data models and config-loading utilities.

Key models:

- Alert: Configuration for a CloudWatch log alert.
    Corresponds to a single entry under the top-level `alerts` key in an
    alert config YAML file.
- Result: Thin wrapper around an Alert that adds information about the status
    of the check. Statuses are recorded using the `ResultStatus` enum, which
    can be one of 'PASS' or 'FAIL'.
- ResultContainer: Container for one or more Result objects corresponding to
    Alerts that have been checked. This is the format that the `alerts.check`
    script produces when called with the `--format json` option.
"""

import dataclasses
import enum
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, Type

import yaml
from croniter import croniter

from alerts.constants import ALLOWED_SCHEDULE_HOURS, CHECK_WINDOW_HOURS


class DataclassType(Protocol):
    """Custom type to help us annotate functions that accept a dataclass."""

    __dataclass_fields__: dict


def required_fields(dataclass: Type[DataclassType]) -> list[str]:
    """Helper function that takes a dataclass and returns a list of strings
    representing the names of fields that are required to initialize an
    instance of that dataclass. Useful for validating dataclasses prior to
    initialization."""
    return [
        field.name
        for field in dataclasses.fields(dataclass)
        if field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    ]


@dataclasses.dataclass
class Alert:
    """Configuration for a single CloudWatch log alert.

    Attributes:
        id: Unique identifier for the alert, formatted as a slug.
        name: Unique human-readable name shown in output and failure messages.
        log_group: CloudWatch log group to search.
        log_query: Filter pattern passed to `filter_log_events`. Supports
            CloudWatch filter pattern syntax.
        fail_if: Condition that triggers a failure. `"no_match"` fails when
            no events are found (e.g. job hasn't run); `"match"` fails when
            events are found (e.g. errors were present in logs).
        schedule: 5-field UTC cron expression for when this alert should be
            evaluated. Checked against the past hour each time the workflow runs.
        lookback_hours: How far back (in hours) to search for matching log events.
            Must be a positive integer.
        aws_sns_topic: Optional SNS topic name to notify when this alert fails.
            The full ARN is constructed at runtime; only the topic name is stored
            here to avoid embedding the AWS account ID in config files.
        failure_message: Optional message to send for notification failures.
            When absent, the code will construct a simple default message based
            on the other configuration values for the Alert.
        source_file: Optional path to the config file this alert was loaded
            from. Not configured directly by the user in the alert config file;
            instead, the code that parses Alerts from config files sets this
            automatically.
    """

    id: str
    name: str
    log_group: str
    log_query: str
    fail_if: Literal["match", "no_match"]
    schedule: str
    lookback_hours: int
    aws_sns_topic: str | None = None
    failure_message: str | None = None
    source_file: str | None = None

    def __post_init__(self) -> None:
        """Field validation for Alert objects."""

        # Alert identifiers must be slugs (i.e. alphanumerics with hyphen
        # separators)
        for char in self.id:
            if not (char.isalnum() or char == "-"):
                raise ValueError(
                    f"id '{self.id}' is invalid, must contain only "
                    "alphanumeric characters or hyphens"
                )

        # We use alert names in the subject lines of the notification, so
        # prevent them from being too long
        if len(self.name) > 100:
            raise ValueError(
                f"name '{self.name}' is invald, must be < 100 characters"
            )

        if self.lookback_hours <= 0:
            raise ValueError(
                "lookback_hours must be a positive integer, "
                f"got {self.lookback_hours!r}"
            )

        valid_fail_if = frozenset({"match", "no_match"})
        if self.fail_if not in valid_fail_if:
            raise ValueError(
                f"invalid fail_if value '{self.fail_if}', must be one of: "
                f"{valid_fail_if}"
            )

        # Make sure important optional fields are not empty strings
        for optional_field in ["aws_sns_topic", "failure_message"]:
            if (
                getattr(self, optional_field) is not None
                and not getattr(self, optional_field, "").strip()
            ):
                raise ValueError(
                    f"{optional_field} must be a non-empty string if set, "
                    f"got {getattr(self, optional_field)!r}"
                )

        valid_schedule, reason = validate_schedule(self.schedule)
        if not valid_schedule:
            raise ValueError(reason)

    def as_dict(self) -> dict[str, Any]:
        """Serialize an Alert object as a dictionary."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, dct: dict) -> "Alert":
        """Deserialize an Alert object from a dictionary, validating all fields
        in the process."""
        required_alert_fields = required_fields(Alert)
        missing_alert_fields = [
            f for f in required_alert_fields if f not in dct
        ]
        if missing_alert_fields:
            raise ValueError(
                "Alert missing required fields: "
                f"{', '.join(missing_alert_fields)}"
            )

        return cls(
            id=dct["id"],
            name=dct["name"],
            log_group=dct["log_group"],
            log_query=dct["log_query"],
            fail_if=dct["fail_if"],
            schedule=dct["schedule"],
            lookback_hours=dct["lookback_hours"],
            source_file=dct.get("source_file"),
            aws_sns_topic=dct.get("aws_sns_topic"),
            failure_message=dct.get("failure_message"),
        )

    @classmethod
    def list_from_file(cls, path: Path) -> list["Alert"]:
        """Load, validate, and return a list of Alerts from a YAML config file.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If any alert is missing required fields or has invalid values.
        """

        def format_error(err: str) -> str:
            """Inner helper function to format error messages with a common
            prefix"""
            return f"{path}: {err}"

        if not path.exists():
            raise FileNotFoundError(format_error("Config file not found"))

        with path.open() as f:
            data = yaml.safe_load(f)

        raw_alerts = data.get("alerts", [])
        if not raw_alerts:
            raise ValueError(format_error("No top-level `alerts` key found"))

        alerts = []
        for i, raw in enumerate(raw_alerts):
            label = raw.get("id", f"#{i + 1}")
            error_prefix = f"Error loading Alert {label}"

            try:
                alert = Alert.from_dict(raw)
            except ValueError as exc:
                raise ValueError(
                    format_error(f"{error_prefix}: {exc}")
                ) from exc

            alert.source_file = str(path)
            alerts.append(alert)

        return alerts


class ResultStatus(enum.Enum):
    """Possible statuses for a Result object."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclasses.dataclass
class Result:
    """Thin wrapper around an Alert object that adds information about the
    status of the alert after checking."""

    status: ResultStatus
    alert: Alert

    def status_message(self) -> str:
        """Return a status message for the Result.

        If the Alert passed, returns a simple 'PASS' message. If the Alert
        failed, checks to see whether the Alert is configured with a custom
        `failure_message`, and prefers that message when present; otherwise,
        falls back to a default error message based on the Alert config."""
        if self.status == ResultStatus.FAIL:
            common_prefix = f"FAIL [{self.alert.name}]"

            # Prefer the customized failure message, if one exists
            if self.alert.failure_message:
                return f"{common_prefix}: {self.alert.failure_message}"

            # If no customized failure message is set, fall back to a simple
            # default based on the type of check
            if self.alert.fail_if == "match":
                return (
                    f"{common_prefix}: Logs matching '{self.alert.log_query}' found in "
                    f"'{self.alert.log_group}' in the past {self.alert.lookback_hours}h"
                )
            if self.alert.fail_if == "no_match":
                return (
                    f"{common_prefix}: No logs matching '{self.alert.log_query}' found in "
                    f"'{self.alert.log_group}' in the past {self.alert.lookback_hours}h"
                )
            else:
                # It should not be possible to reach this conditional branch
                # since we validate accepted `fail_if` values when initializing
                # Alert objects, but raise error so that we fail loudly if
                # somehow the code breaks that assumption
                raise ValueError(
                    f"Got unexpected `fail_if` value: {self.alert.fail_if}"
                )

        return f"PASS [{self.alert.name}]"

    def as_dict(self) -> dict[str, Any]:
        """Serialize a Result object as a dictionary."""
        return {"alert": self.alert.as_dict(), "status": self.status.name}

    @classmethod
    def from_dict(cls, dct: dict) -> "Result":
        """Deserialize a Result object from a dictionary, validating all fields
        in the process."""
        required_result_fields = required_fields(Result)
        missing_result_fields = [
            f for f in required_result_fields if f not in dct
        ]
        if missing_result_fields:
            raise ValueError(
                "Result missing required fields: "
                f"{', '.join(missing_result_fields)}"
            )

        status_str = dct["status"]
        try:
            status = ResultStatus[status_str]
        except KeyError:
            raise ValueError(
                f"status '{status_str}' is invalid, must be one of: "
                f"{', '.join(status.name for status in ResultStatus)}",
            )

        alert = Alert.from_dict(dct["alert"])

        return cls(status=status, alert=alert)


@dataclasses.dataclass
class ResultContainer:
    """Container for one or more Result objects corresponding to Alerts that
    have been checked."""

    any_failed: bool  # Whether any of the Results failed
    results: list[Result]

    def as_dict(self) -> dict[str, Any]:
        """Serialize a ResultContainer as a dictionary."""
        return {
            "any_failed": self.any_failed,
            "results": [result.as_dict() for result in self.results],
        }

    @classmethod
    def from_dict(cls, dct: dict) -> "ResultContainer":
        """Deserialize a ResultContainer object from a dictionary, validating
        all fields in the process."""
        required_result_container_fields = required_fields(cls)
        missing_result_container_fields = [
            f for f in required_result_container_fields if f not in dct
        ]
        if missing_result_container_fields:
            raise ValueError(
                "ResultContainer object missing required fields: "
                f"{', '.join(missing_result_container_fields)}"
            )

        results: list[Result] = []
        for i, result_dict in enumerate(dct["results"]):
            label = result_dict.get("alert", {}).get("id", f"#{i + 1}")
            error_prefix = f"Error loading Result for Alert {label}"

            try:
                # This inner `from_dict()` call will in turn validate the
                # required fields on Result and its associated Alert object
                result = Result.from_dict(result_dict)
            except ValueError as exc:
                raise ValueError(f"{error_prefix}: {exc}") from exc

            results.append(result)

        return cls(
            any_failed=any(
                result.status == ResultStatus.FAIL for result in results
            ),
            results=results,
        )


def validate_schedule(schedule: str) -> tuple[bool, str]:
    """Validate a cron expression representing an Alert schedule.

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
            f"Schedule fires at disallowed hours: {invalid_hours}. "
            f"Must only fire at: {sorted(ALLOWED_SCHEDULE_HOURS)}"
        )

    return True, ""


def is_due(schedule: str, now: datetime) -> bool:
    """Return True if a cron `schedule` is due at the time `now`."""
    cron = croniter(schedule, now)
    prev_fire = cron.get_prev(datetime)
    elapsed_seconds = (now - prev_fire).total_seconds()
    return elapsed_seconds < CHECK_WINDOW_HOURS * 3600


def find_due_alerts(config_files: list[Path], now: datetime) -> list[Alert]:
    """Load config files and return only the alerts due at `now`."""
    all_alerts: list[Alert] = []
    for path in config_files:
        all_alerts.extend(Alert.list_from_file(path))
    return [a for a in all_alerts if is_due(a.schedule, now)]
