"""Unit tests for alerts/notify.py."""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from alerts.constants import AWS_REGION
from alerts.models import ResultStatus
from alerts.notify import (
    build_topic_arn,
    main,
    notify_alerts,
    publish_notification,
)
from tests.conftest import (
    ACCOUNT_ID,
    TOPIC_ARN,
    TOPIC_NAME,
    make_result,
)

# ---------------------------------------------------------------------------
# Test build_topic_arn()
# ---------------------------------------------------------------------------


class TestBuildTopicArn:
    def test_build_topic_arn_format(self):
        arn = build_topic_arn(ACCOUNT_ID, TOPIC_NAME)
        assert arn == TOPIC_ARN

    def test_build_topic_arn_uses_aws_region_constant(self):
        arn = build_topic_arn(ACCOUNT_ID, TOPIC_NAME)
        assert AWS_REGION in arn


# ---------------------------------------------------------------------------
# Test publish_notification()
# ---------------------------------------------------------------------------


class TestPublishNotification:
    def test_publish_notification_calls_sns_publish(
        self, sns_client: MagicMock
    ):
        publish_notification(
            TOPIC_ARN, "My alert", "FAIL [My alert]: ...", sns_client
        )
        sns_client.publish.assert_called_once()

    def test_publish_notification_passes_correct_arn(
        self, sns_client: MagicMock
    ):
        publish_notification(
            TOPIC_ARN, "My alert", "FAIL [My alert]: ...", sns_client
        )
        _, kwargs = sns_client.publish.call_args
        assert kwargs["TopicArn"] == TOPIC_ARN

    def test_publish_notification_subject_contains_alert_name(
        self, sns_client: MagicMock
    ):
        publish_notification(
            TOPIC_ARN, "My alert", "FAIL [My alert]: ...", sns_client
        )
        _, kwargs = sns_client.publish.call_args
        assert "My alert" in kwargs["Subject"]

    def test_publish_notification_message_is_full_message(
        self, sns_client: MagicMock
    ):
        msg = "FAIL [My alert]: No logs matching 'info' found in '/g' in the past 12h"
        publish_notification(TOPIC_ARN, "My alert", msg, sns_client)
        _, kwargs = sns_client.publish.call_args
        assert kwargs["Message"] == msg


# ---------------------------------------------------------------------------
# Test notify_alerts()
# ---------------------------------------------------------------------------


class TestNotifyAlerts:
    def test_returns_0_when_no_failures(self, sns_client: MagicMock):
        result = make_result(status=ResultStatus.PASS)
        assert notify_alerts([result], ACCOUNT_ID, sns_client) == 0
        sns_client.publish.assert_not_called()

    def test_returns_0_when_failure_has_no_topic(self, sns_client: MagicMock):
        result = make_result(status=ResultStatus.FAIL, aws_sns_topic=None)
        assert notify_alerts([result], ACCOUNT_ID, sns_client) == 0
        sns_client.publish.assert_not_called()

    def test_publishes_for_failed_alert_with_topic(
        self, sns_client: MagicMock
    ):
        result = make_result(
            status=ResultStatus.FAIL, aws_sns_topic=TOPIC_NAME
        )
        notify_alerts([result], ACCOUNT_ID, sns_client)
        sns_client.publish.assert_called_once()

    def test_constructs_correct_arn_for_publish(self, sns_client: MagicMock):
        result = make_result(
            status=ResultStatus.FAIL, aws_sns_topic=TOPIC_NAME
        )
        notify_alerts([result], ACCOUNT_ID, sns_client)
        _, kwargs = sns_client.publish.call_args
        assert kwargs["TopicArn"] == TOPIC_ARN

    def test_returns_0_on_successful_publish(self, sns_client: MagicMock):
        result = make_result(status=ResultStatus.FAIL)
        assert notify_alerts([result], ACCOUNT_ID, sns_client) == 0

    def test_returns_1_on_publish_failure(self, sns_client: MagicMock):
        sns_client.publish.side_effect = ClientError(
            {"Error": {"Code": "NotFound", "Message": "Topic not found"}},
            "Publish",
        )
        result = make_result(status=ResultStatus.FAIL)
        assert notify_alerts([result], ACCOUNT_ID, sns_client) == 1

    def test_continues_publishing_after_one_failure(
        self, sns_client: MagicMock
    ):
        sns_client.publish.side_effect = [
            ClientError(
                {"Error": {"Code": "NotFound", "Message": "Topic not found"}},
                "Publish",
            ),
            None,  # second call succeeds
        ]
        results = [
            make_result(status=ResultStatus.FAIL),
            make_result(status=ResultStatus.FAIL),
        ]
        assert notify_alerts(results, ACCOUNT_ID, sns_client) == 1
        assert sns_client.publish.call_count == 2

    def test_skips_passed_alerts(self, sns_client: MagicMock):
        results = [
            make_result(status=ResultStatus.PASS, name="Passing alert"),
            make_result(status=ResultStatus.FAIL, name="Failing alert"),
        ]
        notify_alerts(results, ACCOUNT_ID, sns_client)
        assert sns_client.publish.call_count == 1
        _, kwargs = sns_client.publish.call_args
        assert "Failing alert" in kwargs["Subject"]

    def test_empty_results_returns_0(self, sns_client: MagicMock):
        assert notify_alerts([], ACCOUNT_ID, sns_client) == 0
        sns_client.publish.assert_not_called()


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

    def test_main_publishes_to_boto_sns_client_for_all_failing_alerts(
        self, mocker, sns_client: MagicMock
    ):
        results_json = json.dumps(
            {
                "any_failed": True,
                "results": [
                    make_result(
                        status=ResultStatus.FAIL,
                        id="alert-a",
                        name="Alert A",
                    ).as_dict(),
                    make_result(
                        status=ResultStatus.FAIL,
                        id="alert-b",
                        name="Alert B",
                    ).as_dict(),
                ],
            }
        )
        mocker.patch(
            "sys.argv", ["notify", results_json, "--account-id", ACCOUNT_ID]
        )
        mocker.patch("alerts.notify.boto3.client", return_value=sns_client)

        main()

        assert sns_client.publish.call_count == 2
