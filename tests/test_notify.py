"""Unit tests for alerts/notify.py."""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from alerts.models import Result, ResultContainer, ResultStatus
from alerts.notify import (
    build_topic_arn,
    main,
    notify_alerts,
    publish_notification,
)
from tests.conftest import make_alert

ACCOUNT_ID = "123456789012"
TOPIC_NAME = "my-topic"
TOPIC_ARN = f"arn:aws:sns:us-east-1:{ACCOUNT_ID}:{TOPIC_NAME}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sns_client() -> MagicMock:
    """Return a mock SNS client."""
    return MagicMock()


def make_results(
    *,
    status: ResultStatus = ResultStatus.FAIL,
    aws_sns_topic: str | None = TOPIC_NAME,
    name: str = "Test alert",
    failure_message: str | None = None,
) -> list[Result]:
    """Return a minimal check results list with one result entry."""
    alert = make_alert(
        name=name, aws_sns_topic=aws_sns_topic, failure_message=failure_message
    )
    return [
        Result(
            alert=alert,
            status=status,
        )
    ]


def make_result_container(
    *,
    status: ResultStatus = ResultStatus.FAIL,
    aws_sns_topic: str | None = TOPIC_NAME,
    name: str = "Test alert",
    failure_message: str | None = None,
) -> ResultContainer:
    """Return a minimal check result container with one result entry."""
    return ResultContainer(
        any_failed=status != ResultStatus.FAIL,
        results=make_results(
            status=status,
            aws_sns_topic=aws_sns_topic,
            name=name,
            failure_message=failure_message,
        ),
    )


# ---------------------------------------------------------------------------
# Test build_topic_arn()
# ---------------------------------------------------------------------------


class TestBuildTopicArn:
    def test_build_topic_arn_format(self):
        arn = build_topic_arn(ACCOUNT_ID, TOPIC_NAME)
        assert arn == TOPIC_ARN

    def test_build_topic_arn_uses_us_east_1(self):
        arn = build_topic_arn(ACCOUNT_ID, TOPIC_NAME)
        assert "us-east-1" in arn


# ---------------------------------------------------------------------------
# Test publish_notification()
# ---------------------------------------------------------------------------


class TestPublishNotification:
    def test_publish_notification_calls_sns_publish(self):
        client = make_sns_client()
        publish_notification(
            TOPIC_ARN, "My alert", "FAIL [My alert]: ...", client
        )
        client.publish.assert_called_once()

    def test_publish_notification_passes_correct_arn(self):
        client = make_sns_client()
        publish_notification(
            TOPIC_ARN, "My alert", "FAIL [My alert]: ...", client
        )
        _, kwargs = client.publish.call_args
        assert kwargs["TopicArn"] == TOPIC_ARN

    def test_publish_notification_subject_contains_alert_name(self):
        client = make_sns_client()
        publish_notification(
            TOPIC_ARN, "My alert", "FAIL [My alert]: ...", client
        )
        _, kwargs = client.publish.call_args
        assert "My alert" in kwargs["Subject"]

    def test_publish_notification_message_is_full_message(self):
        client = make_sns_client()
        msg = "FAIL [My alert]: No logs matching 'info' found in '/g' in the past 12h"
        publish_notification(TOPIC_ARN, "My alert", msg, client)
        _, kwargs = client.publish.call_args
        assert kwargs["Message"] == msg


# ---------------------------------------------------------------------------
# Test notify_alerts()
# ---------------------------------------------------------------------------


class TestNotifyAlerts:
    def test_returns_0_when_no_failures(self):
        client = make_sns_client()
        data = make_results(status=ResultStatus.PASS)
        assert notify_alerts(data, ACCOUNT_ID, client) == 0
        client.publish.assert_not_called()

    def test_returns_0_when_failure_has_no_topic(self):
        client = make_sns_client()
        data = make_results(status=ResultStatus.FAIL, aws_sns_topic=None)
        assert notify_alerts(data, ACCOUNT_ID, client) == 0
        client.publish.assert_not_called()

    def test_publishes_for_failed_alert_with_topic(self):
        client = make_sns_client()
        data = make_results(status=ResultStatus.FAIL, aws_sns_topic=TOPIC_NAME)
        notify_alerts(data, ACCOUNT_ID, client)
        client.publish.assert_called_once()

    def test_constructs_correct_arn_for_publish(self):
        client = make_sns_client()
        data = make_results(status=ResultStatus.FAIL, aws_sns_topic=TOPIC_NAME)
        notify_alerts(data, ACCOUNT_ID, client)
        _, kwargs = client.publish.call_args
        assert kwargs["TopicArn"] == TOPIC_ARN

    def test_returns_0_on_successful_publish(self):
        client = make_sns_client()
        data = make_results(status=ResultStatus.FAIL)
        assert notify_alerts(data, ACCOUNT_ID, client) == 0

    def test_returns_1_on_publish_failure(self):
        client = make_sns_client()
        client.publish.side_effect = ClientError(
            {"Error": {"Code": "NotFound", "Message": "Topic not found"}},
            "Publish",
        )
        data = make_results(status=ResultStatus.FAIL)
        assert notify_alerts(data, ACCOUNT_ID, client) == 1

    def test_continues_publishing_after_one_failure(self):
        client = make_sns_client()
        client.publish.side_effect = [
            ClientError(
                {"Error": {"Code": "NotFound", "Message": "Topic not found"}},
                "Publish",
            ),
            None,  # second call succeeds
        ]
        data = [
            *make_results(status=ResultStatus.FAIL),
            *make_results(status=ResultStatus.FAIL),
        ]
        assert notify_alerts(data, ACCOUNT_ID, client) == 1
        assert client.publish.call_count == 2

    def test_skips_passed_alerts(self):
        client = make_sns_client()
        data = [
            *make_results(status=ResultStatus.PASS, name="Passing alert"),
            *make_results(status=ResultStatus.FAIL, name="Failing alert"),
        ]
        notify_alerts(data, ACCOUNT_ID, client)
        assert client.publish.call_count == 1
        _, kwargs = client.publish.call_args
        assert "Failing alert" in kwargs["Subject"]

    def test_empty_results_returns_0(self):
        client = make_sns_client()
        assert notify_alerts([], ACCOUNT_ID, client) == 0
        client.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Test main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_raises_on_missing_results_json(self, mocker):
        mocker.patch("sys.argv", ["notify", "--account-id", ACCOUNT_ID])
        with pytest.raises(SystemExit):
            main()

    def test_main_raises_on_missing_account_id(self, mocker):
        mocker.patch(
            "sys.argv",
            ["notify", '{"any_failed": false, "results": []}'],
        )
        with pytest.raises(SystemExit):
            main()

    def test_main_raises_on_malformed_results_json(self, mocker):
        mocker.patch(
            "sys.argv",
            ["notify", "not-valid-json", "--account-id", ACCOUNT_ID],
        )
        with pytest.raises(ValueError, match="Unable to parse JSON"):
            main()

    def test_main_raises_on_empty_results_json(self, mocker):
        mocker.patch("sys.argv", ["notify", "", "--account-id", ACCOUNT_ID])
        with pytest.raises(ValueError, match="empty"):
            main()

    def test_main_publishes_to_boto_client_for_all_failing_alerts(
        self, mocker
    ):
        results_json = json.dumps(
            {
                "any_failed": True,
                "results": [
                    {
                        "alert": {
                            "id": "alert-a",
                            "name": "Alert A",
                            "aws_sns_topic": TOPIC_NAME,
                        },
                        "status": "FAIL",
                    },
                    {
                        "alert": {
                            "id": "alert-b",
                            "name": "Alert B",
                            "aws_sns_topic": TOPIC_NAME,
                        },
                        "status": "FAIL",
                    },
                ],
            }
        )
        mocker.patch(
            "sys.argv", ["notify", results_json, "--account-id", ACCOUNT_ID]
        )
        mock_client = make_sns_client()
        mocker.patch("alerts.notify.boto3.client", return_value=mock_client)

        main()

        assert mock_client.publish.call_count == 2
