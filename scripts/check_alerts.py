"""Check CloudWatch Logs for alerts defined in YAML config files.

Usage:
    python check_alerts.py alerts/*.yml

For each alert in each config file, checks whether the alert's cron schedule
fired within the past hour. If so, queries CloudWatch Logs and evaluates the
result against the alert's `error_if` condition. All alerts are checked before
the script exits; it exits non-zero if any alert failed.
"""

import argparse
import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import boto3
import yaml
from croniter import croniter


@dataclasses.dataclass
class Alert:
    """Configuration for a single CloudWatch log alert.

    Attributes:
        name: Unique human-readable name shown in output and failure messages.
        log_group: CloudWatch log group to search.
        log_query: Filter pattern passed to `filter_log_events`. Supports
            CloudWatch filter pattern syntax.
        error_if: Condition that triggers a failure. `"no_match"` fails when
            no events are found (e.g. job hasn't run); `"match"` fails when events
            are found (e.g. errors were present in logs).
        schedule: 5-field UTC cron expression for when this alert should be
            evaluated. Checked against the past hour each time the workflow runs.
        lookback_hours: How far back (in hours) to search for matching log events.
            Must be a positive integer.
        source_file: Path to the config file this alert was loaded from.
    """

    name: str
    log_group: str
    log_query: str
    error_if: Literal["match", "no_match"]
    schedule: str
    lookback_hours: int
    source_file: str

    def __post_init__(self) -> None:
        """Field validation for this dataclass."""
        valid_error_if = frozenset({"match", "no_match"})
        if self.error_if not in valid_error_if:
            raise ValueError(
                f"Alert '{self.name}': invalid error_if value '{self.error_if}'. "
                f"Must be one of: {sorted(valid_error_if)}"
            )
        if self.lookback_hours <= 0:
            raise ValueError(
                f"Alert '{self.name}': lookback_hours must be a positive integer, "
                f"got {self.lookback_hours!r}"
            )


def load_config(path: Path) -> list[Alert]:
    """Load, validate, and return the list of alerts from a YAML config file.

    Raises:
        ValueError: If any alert is missing required fields or has invalid values.
    """
    with path.open() as f:
        data = yaml.safe_load(f)

    alerts = []
    required_alert_fields = [
        field.name
        for field in dataclasses.fields(Alert)
        # Exclude `source_file` from the list of required fields, since we'll
        # specify it manually during parsing
        if field.name != "source_file"
    ]
    for i, raw in enumerate(data.get("alerts", [])):
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
            )
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        alerts.append(alert)

    if not alerts:
        raise ValueError(f"{path}: No top-level `alerts` key found")

    return alerts


def is_due(schedule: str, now: datetime) -> bool:
    """Return True if the cron schedule fired within the past hour."""
    cron = croniter(schedule, now)
    prev_fire = cron.get_prev(datetime)
    elapsed_seconds = (now - prev_fire).total_seconds()
    return elapsed_seconds < 3600


def find_due_alerts(config_files: list[Path], now: datetime) -> list[Alert]:
    """Load all config files and return only the alerts due at `now`."""
    all_alerts: list[Alert] = []
    for path in config_files:
        all_alerts.extend(load_config(path))
    return [a for a in all_alerts if is_due(a.schedule, now)]


def query_cloudwatch(
    log_group: str,
    log_query: str,
    lookback_hours: int,
    now: datetime,
    client,
) -> bool:
    """Return True if any log events match log_query in the lookback window."""
    start_ms = int((now.timestamp() - lookback_hours * 3600) * 1000)
    end_ms = int(now.timestamp() * 1000)

    paginator = client.get_paginator("filter_log_events")
    pages = paginator.paginate(
        logGroupName=log_group,
        filterPattern=log_query,
        startTime=start_ms,
        endTime=end_ms,
    )
    for page in pages:
        if page.get("events"):
            return True
    return False


def evaluate_alert(alert: Alert, now: datetime, client) -> tuple[bool, str]:
    """Evaluate a single alert. Returns (passed, message).

    passed=True means no error condition was triggered.
    """
    found_match = query_cloudwatch(
        alert.log_group, alert.log_query, alert.lookback_hours, now, client
    )

    if alert.error_if == "no_match" and not found_match:
        return False, (
            f"FAIL [{alert.name}]: No logs matching '{alert.log_query}' found in "
            f"'{alert.log_group}' in the past {alert.lookback_hours}h"
        )
    if alert.error_if == "match" and found_match:
        return False, (
            f"FAIL [{alert.name}]: Logs matching '{alert.log_query}' found in "
            f"'{alert.log_group}' in the past {alert.lookback_hours}h"
        )

    return True, f"PASS [{alert.name}]"


def check_alerts(
    config_files: list[Path],
    dry_run: bool,
    now: datetime | None = None,
) -> int:
    if now is None:
        now = datetime.now(tz=timezone.utc)

    due_alerts = find_due_alerts(config_files, now)

    if dry_run:
        for alert in due_alerts:
            print(alert.name)
        return 0

    if not due_alerts:
        print(f"No alerts due at {now.strftime('%Y-%m-%d %H:%M UTC')}.")
        return 0

    client = boto3.client("logs")

    print(
        f"Checking {len(due_alerts)} alert(s) due at {now.strftime('%Y-%m-%d %H:%M UTC')}..."
    )

    results: list[tuple[bool, str]] = []
    for alert in due_alerts:
        passed, message = evaluate_alert(alert, now, client)
        results.append((passed, message))
        print(message)

    failures = [msg for passed, msg in results if not passed]
    print(f"\n{len(results) - len(failures)}/{len(results)} alerts passed.")

    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check CloudWatch log alerts")
    parser.add_argument(
        "config_files",
        nargs="+",
        type=Path,
        metavar="CONFIG",
        help="One or more alert YAML config files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print a newline-separated list of alert names that are due right "
            "now, then exit. Prints nothing if no alerts are due. Does not "
            "authenticate with AWS or query CloudWatch."
        ),
    )
    args = parser.parse_args()

    return check_alerts(args.config_files, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
