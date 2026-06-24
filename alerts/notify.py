"""Publish SNS notifications for failed alerts.

Usage:
    results=$(python -m alerts.check config/*.yml --format json) || exit $?
    python -m alerts.notify "$results" --account-id ACCOUNT_ID

Reads a JSON result object from the first positional argument (as produced by
`alerts.check --format json`) and publishes a notification to the configured
SNS topic for each failed alert that has an `aws_sns_topic` set.

Exit codes:
    0  All notifications published successfully (or none needed).
    1  One or more SNS publish calls failed.
"""

import argparse
import json
import json.decoder
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from alerts.constants import AWS_REGION
from alerts.models import Result, ResultContainer, ResultStatus


def build_topic_arn(account_id: str, topic_name: str) -> str:
    """Construct an SNS topic ARN from account ID and topic name."""
    return f"arn:aws:sns:{AWS_REGION}:{account_id}:{topic_name}"


def publish_notification(
    topic_arn: str,
    alert_name: str,
    message: str,
    client,  # boto3 SNS client
) -> None:
    """Publish an alert notification to an SNS topic.

    Raises:
        ClientError, BotoCoreError: If the SNS publish call fails.
    """
    subject = f"Alert: {alert_name}"
    client.publish(TopicArn=topic_arn, Subject=subject, Message=message)


def notify_alerts(
    results: list[Result],
    account_id: str,
    client,  # boto3 SNS client
) -> int:
    """Main entrypoint for the script logic. Publishes notifications for all
    failed alerts that have a topic configured.

    Return codes: 0 = all publishes succeeded; non-zero = any publishes failed.
    """
    failed_notifications: list[str] = []
    for result in results:
        if result.status == ResultStatus.PASS:
            print(
                f"Skipping notification for '{result.alert.name}' since it passed"
            )
            continue
        if not result.alert.aws_sns_topic:
            print(
                f"Skipping notification for '{result.alert.name}' "
                "since it has no aws_sns_topic configured"
            )
            continue

        topic_name = result.alert.aws_sns_topic
        topic_arn = build_topic_arn(account_id, topic_name)

        try:
            publish_notification(
                topic_arn, result.alert.name, result.status_message(), client
            )
            print(f"Notified [{result.alert.name}] → {topic_name}")
        except (ClientError, BotoCoreError) as exc:
            print(
                f"ERROR: Failed to notify [{result.alert.name}] → {topic_name}: {exc}"
            )
            failed_notifications.append(result.alert.name)

    if failed_notifications:
        print()
        print(
            f"{len(failed_notifications)} notification(s) failed: "
            f"{', '.join(failed_notifications)}"
        )
        return 1

    return 0


def main() -> int:
    """Parse args, read JSON, and publish SNS notifications."""
    parser = argparse.ArgumentParser(
        description="Publish SNS notifications for failed alerts"
    )
    parser.add_argument(
        "results_json",
        type=str,
        help="JSON string containing results to notify",
    )
    parser.add_argument(
        "--account-id",
        required=True,
        metavar="ACCOUNT_ID",
        help="AWS account ID used to construct SNS topic ARNs",
    )
    args = parser.parse_args()

    try:
        results_dict = json.loads(args.results_json)
    except json.decoder.JSONDecodeError:
        if args.results_json:
            raise ValueError(f"Unable to parse JSON: {args.results_json}")
        else:
            raise ValueError(
                "Expected to read JSON results from first positional arg, "
                "but it is empty"
            )

    result_container = ResultContainer.from_dict(results_dict)
    client = boto3.client("sns", region_name=AWS_REGION)
    return notify_alerts(result_container.results, args.account_id, client)


if __name__ == "__main__":
    sys.exit(main())
