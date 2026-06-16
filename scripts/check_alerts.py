"""Check CloudWatch Logs for alerts defined in YAML config files.

Usage:
    python check_alerts.py alerts/*.yml

For each alert in each config file, checks whether the alert's cron schedule
fired within the past hour. If so, queries CloudWatch Logs and evaluates the
result against the alert's `error_if` condition. All alerts are checked before
the script exits; it exits non-zero if any alert failed.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml
from croniter import croniter


def load_config(path: Path) -> list[dict]:
    """Load and return the list of alerts from a YAML config file."""
    with path.open() as f:
        data = yaml.safe_load(f)
    alerts = data.get("alerts", [])
    for alert in alerts:
        alert["_source_file"] = str(path)
    return alerts


def is_due(schedule: str, now: datetime) -> bool:
    """Return True if the cron schedule fired within the past hour."""
    cron = croniter(schedule, now)
    prev_fire = cron.get_prev(datetime)
    elapsed_seconds = (now - prev_fire).total_seconds()
    return elapsed_seconds < 3600


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


def evaluate_alert(alert: dict, now: datetime, client) -> tuple[bool, str]:
    """
    Evaluate a single alert. Returns (passed, message).
    passed=True means no error condition was triggered.
    """
    name = alert["name"]
    log_group = alert["log_group"]
    log_query = alert["log_query"]
    error_if = alert["error_if"]
    lookback_hours = alert["lookback_hours"]

    found_match = query_cloudwatch(
        log_group, log_query, lookback_hours, now, client
    )

    if error_if == "no_match" and not found_match:
        return False, (
            f"FAIL [{name}]: No logs matching '{log_query}' found in "
            f"'{log_group}' in the past {lookback_hours}h"
        )
    if error_if == "match" and found_match:
        return False, (
            f"FAIL [{name}]: Logs matching '{log_query}' found in "
            f"'{log_group}' in the past {lookback_hours}h"
        )

    return True, f"PASS [{name}]"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check CloudWatch log alerts")
    parser.add_argument(
        "config_files",
        nargs="+",
        type=Path,
        metavar="CONFIG",
        help="One or more alert YAML config files",
    )
    args = parser.parse_args()

    now = datetime.now(tz=timezone.utc)
    client = boto3.client("logs")

    all_alerts: list[dict] = []
    for path in args.config_files:
        all_alerts.extend(load_config(path))

    due_alerts = [a for a in all_alerts if is_due(a["schedule"], now)]

    if not due_alerts:
        print("No alerts due at this time.")
        return 0

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


if __name__ == "__main__":
    sys.exit(main())
