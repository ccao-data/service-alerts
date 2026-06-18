"""Check CloudWatch Logs for alerts defined in YAML config files.

Usage:
    python -m alerts.check config/*.yml
    python -m alerts.check config/*.yml --format json

For each alert in each config file, checks whether the alert's cron schedule
fired within the past hour. If so, queries CloudWatch Logs and evaluates the
result against the alert's `error_if` condition. All alerts are checked before
the script exits.

Exit codes:
    0  Checks ran to completion (regardless of pass/fail results).
    1  Script error (e.g. bad config, AWS authentication failure).

With --format json, outputs a JSON object to stdout with keys `any_failed`
(bool) and `results` (list of per-alert outcomes). The workflow reads
`any_failed` to decide whether to notify and whether to fail the job.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import boto3

from alerts.constants import AWS_REGION
from alerts.models import Alert, find_due_alerts


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
    dry_run: bool = False,
    output_format: Literal["text", "json"] = "text",
    now: datetime | None = None,
) -> int:
    """Main entrypoint for the script logic. Takes a list of config files and
    parses them to run checks, printing what it finds to stdout and returning
    an integer representing the exit code for the script.

    When `dry_run` is True, prints the names of due alerts then exits without
    querying CloudWatch. Does not authenticate with AWS.

    With `output_format="json"`, prints a JSON object instead of human-readable
    text. The object contains `any_failed` (bool) and `results` (list of
    per-alert outcomes).

    The `now` parameter is provided as a helper for unit tests, to allow them
    to control the time that the check runs without mocking.

    Exit codes: 0 = ran to completion; non-zero = script error only.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    due_alerts = find_due_alerts(config_files, now)

    if dry_run:
        for alert in due_alerts:
            print(alert.name)
        return 0

    if not due_alerts:
        if output_format == "json":
            print(json.dumps({"any_failed": False, "results": []}))
        else:
            print(f"No alerts due at {now.strftime('%Y-%m-%d %H:%M UTC')}.")
        return 0

    client = boto3.client("logs", region_name=AWS_REGION)

    if output_format == "text":
        print(
            f"Checking {len(due_alerts)} alert(s) due at "
            f"{now.strftime('%Y-%m-%d %H:%M UTC')}..."
        )

    results: list[tuple[bool, str, Alert]] = []
    for alert in due_alerts:
        passed, message = evaluate_alert(alert, now, client)
        results.append((passed, message, alert))
        if output_format == "text":
            print(message)

    failures = [msg for passed, msg, _ in results if not passed]

    if output_format == "json":
        print(
            json.dumps(
                {
                    "any_failed": bool(failures),
                    "results": [
                        {
                            "name": alert.name,
                            "passed": passed,
                            "message": message,
                            "aws_sns_topic": alert.aws_sns_topic,
                        }
                        for passed, message, alert in results
                    ],
                }
            )
        )
    else:
        print(
            f"\n{len(results) - len(failures)}/{len(results)} alerts passed."
        )

    return 0


def main() -> int:
    """Thin wrapper around `check_alerts()` that parses function args from
    command-line options."""
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
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        dest="output_format",
        help="Output format: 'text' (default) or 'json'.",
    )
    args = parser.parse_args()

    return check_alerts(args.config_files, args.dry_run, args.output_format)


if __name__ == "__main__":
    sys.exit(main())
