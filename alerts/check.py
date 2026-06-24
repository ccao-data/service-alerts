"""Check CloudWatch Logs for alerts defined in YAML config files.

Usage:
    # Check all YAML files in the default `config/` subdir
    python -m alerts.check
    # Check only files with the `.yml` extension in the `config/` subdir
    python -m alerts.check config/*.yml
    # Print the names of alerts that are due, and exit without checking them
    python -m alerts.check --dry-run
    # Run checks on all alerts in the default subdir and print output as JSON
    python -m alerts.check --format json

For each alert in each config file, checks whether the alert's cron schedule
fired within the past hour. If so, queries CloudWatch Logs and evaluates the
result against the alert's `fail_if` condition. All alerts are checked before
the script exits.

Exit codes:
    0  Checks ran to completion (regardless of pass/fail results).
    1  Script error (e.g. bad config, AWS authentication failure).

With --format json, outputs a JSON object to stdout with keys `any_failed`
(bool) and `results` (list of per-alert outcomes). The workflow that runs
this script reads from the `any_failed` key in the JSON output to decide
whether to notify and whether to fail the job.

With --dry-run, only checks to see which alerts are due for checking, prints
their human-readable names, and then exits. A dry run will print no output if
no alerts are due. The workflow that runs this script uses this option to skip
AWS authentication if no alerts are due for checking.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import boto3

from alerts.constants import ALERT_CONFIG_DIR, AWS_REGION
from alerts.models import (
    Alert,
    Result,
    ResultContainer,
    ResultStatus,
    find_due_alerts,
)


def query_cloudwatch(
    log_group: str,
    log_query: str,
    lookback_hours: int,
    now: datetime,
    client,  # boto3 CloudWatch Logs client
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


def evaluate_alert(
    alert: Alert,
    now: datetime,
    client,  # boto3 CloudWatch Logs client
) -> Result:
    """Evaluate a single Alert at a given time `now`, returning a Result object
    containing the alert status and message."""
    found_match = query_cloudwatch(
        alert.log_group, alert.log_query, alert.lookback_hours, now, client
    )

    status = ResultStatus.PASS
    if alert.fail_if == "no_match" and not found_match:
        status = ResultStatus.FAIL
    if alert.fail_if == "match" and found_match:
        status = ResultStatus.FAIL

    return Result(alert=alert, status=status)


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

    Return codes: 0 = ran to completion; non-zero = script error.
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
        print()

    results: list[Result] = []
    for alert in due_alerts:
        result = evaluate_alert(alert, now, client)
        results.append(result)
        if output_format == "text":
            print(result.status_message())

    failures = [
        result for result in results if not result.status == ResultStatus.PASS
    ]

    if output_format == "json":
        print(
            json.dumps(
                ResultContainer(
                    any_failed=bool(failures), results=results
                ).as_dict()
            )
        )
    else:
        print()
        print(f"{len(results) - len(failures)}/{len(results)} alerts passed.")

    return 0


def main() -> int:
    """Thin wrapper around `check_alerts()` that parses function args from
    command-line options."""
    parser = argparse.ArgumentParser(description="Check CloudWatch log alerts")
    parser.add_argument(
        "config_files",
        nargs="*",
        type=Path,
        metavar="CONFIG",
        help=(
            "One or more alert YAML config files. "
            "Defaults to all .yml/.yaml files in config/ if not provided."
        ),
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

    config_files = args.config_files or sorted(
        [
            *ALERT_CONFIG_DIR.glob("*.yml"),
            *ALERT_CONFIG_DIR.glob("*.yaml"),
        ]
    )
    if not config_files:
        raise ValueError(
            f"No config files provided, and none found in default "
            f"{str(ALERT_CONFIG_DIR)}/ subdir",
        )

    return check_alerts(config_files, args.dry_run, args.output_format)


if __name__ == "__main__":
    sys.exit(main())
