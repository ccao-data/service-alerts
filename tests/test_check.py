"""Unit tests for alerts/check.py."""

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_mock
import yaml

from alerts.check import check_alerts, evaluate_alert, main, query_cloudwatch
from alerts.models import Alert, Result, ResultStatus
from tests.conftest import make_alert, make_paginator, write_config

UTC = timezone.utc

_NOW = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
_FIND_NOW = datetime(2026, 6, 16, 12, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Test query_cloudwatch()
# ---------------------------------------------------------------------------


class TestQueryCloudWatch:
    @pytest.mark.parametrize(
        "pages,expected",
        [
            ([{"events": [{"message": "hit"}]}], True),  # events on only page
            ([{"events": []}], False),  # empty events list
            ([{}], False),  # events key missing
            (
                [{"events": [{"message": "hit"}]}, {"events": []}],
                True,
            ),  # events on first page
            (
                [{"events": []}, {"events": [{"message": "hit"}]}],
                True,
            ),  # events on second page
        ],
    )
    def test_query_cloudwatch_match(self, pages: list, expected: bool):
        client = make_paginator(*pages)
        assert (
            query_cloudwatch("/test/logs", "info", 12, _NOW, client)
            is expected
        )

    def test_passes_correct_time_window(self):
        client = make_paginator({"events": []})
        query_cloudwatch("/test/logs", "info", 12, _NOW, client)
        _, kwargs = client.get_paginator.return_value.paginate.call_args
        assert kwargs["startTime"] == int(
            (_NOW.timestamp() - 12 * 3600) * 1000
        )
        assert kwargs["endTime"] == int(_NOW.timestamp() * 1000)

    def test_passes_correct_filter_pattern_and_log_group(self):
        client = make_paginator({"events": []})
        query_cloudwatch("/test/logs", "my_pattern", 6, _NOW, client)
        _, kwargs = client.get_paginator.return_value.paginate.call_args
        assert kwargs["filterPattern"] == "my_pattern"
        assert kwargs["logGroupName"] == "/test/logs"


# ---------------------------------------------------------------------------
# Test evaluate_alert()
# ---------------------------------------------------------------------------


class TestEvaluateAlert:
    @pytest.mark.parametrize(
        "fail_if,found,expected_status",
        [
            (
                "no_match",
                False,
                ResultStatus.FAIL,
            ),  # no logs found when required → fail
            (
                "no_match",
                True,
                ResultStatus.PASS,
            ),  # logs found when required → pass
            (
                "match",
                True,
                ResultStatus.FAIL,
            ),  # logs found when forbidden → fail
            (
                "match",
                False,
                ResultStatus.PASS,
            ),  # no logs found when forbidden → pass
        ],
    )
    def test_evaluate_alert_pass_fail(
        self,
        fail_if: str,
        found: bool,
        expected_status: bool,
    ):
        alert = make_alert(fail_if=fail_if)
        events = [{"message": "hit"}] if found else []
        client = make_paginator({"events": events})
        result = evaluate_alert(alert, _NOW, client)
        assert result.alert == alert
        assert result.status == expected_status


# ---------------------------------------------------------------------------
# Test check_alerts()
# ---------------------------------------------------------------------------


class TestCheckAlerts:
    def test_dry_run_prints_due_alert_names(
        self, find_due_config: Path, capsys: pytest.CaptureFixture
    ):
        exit_code = check_alerts(
            config_files=[find_due_config], dry_run=True, now=_FIND_NOW
        )
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Due alert" in captured.out
        assert "Not due alert" not in captured.out

    def test_dry_run_prints_nothing_when_none_due(
        self, find_due_config: Path, capsys: pytest.CaptureFixture
    ):
        exit_code = check_alerts(
            config_files=[find_due_config],
            dry_run=True,
            now=datetime(2026, 6, 16, 15, 0, tzinfo=UTC),
        )
        captured = capsys.readouterr()
        assert exit_code == 0
        assert captured.out == ""

    def test_dry_run_prints_one_name_per_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        config = {
            "alerts": [
                make_alert(
                    id="alert-a", name="Alert A", schedule="0 12 * * *"
                ).as_dict(),
                make_alert(
                    id="alert-b", name="Alert B", schedule="0 12 * * *"
                ).as_dict(),
            ]
        }
        config_file = tmp_path / "svc.yml"
        config_file.write_text(yaml.dump(config))
        check_alerts(config_files=[config_file], dry_run=True, now=_FIND_NOW)
        lines = capsys.readouterr().out.splitlines()
        assert lines == ["Alert A", "Alert B"]

    def test_returns_0_and_prints_message_when_no_alerts_due(
        self, find_due_config: Path, capsys: pytest.CaptureFixture
    ):
        now = datetime(2026, 6, 16, 15, 0, tzinfo=UTC)
        exit_code = check_alerts(
            config_files=[find_due_config], dry_run=False, now=now
        )
        assert exit_code == 0
        assert "No alerts due" in capsys.readouterr().out

    def test_does_not_call_boto3_when_no_alerts_due(
        self, find_due_config: Path, mocker: pytest_mock.MockerFixture
    ):
        mock_boto3 = mocker.patch("alerts.check.boto3")
        now = datetime(2026, 6, 16, 15, 0, tzinfo=UTC)
        check_alerts(config_files=[find_due_config], dry_run=False, now=now)
        mock_boto3.client.assert_not_called()

    def test_returns_0_when_all_alerts_pass(
        self, find_due_config: Path, mocker: pytest_mock.MockerFixture
    ):
        # "Due alert" is fail_if=no_match; events found → pass
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": [{"message": "hit"}]}),
        )
        exit_code = check_alerts(
            config_files=[find_due_config], dry_run=False, now=_FIND_NOW
        )
        assert exit_code == 0

    def test_returns_0_when_any_alert_fails(
        self, find_due_config: Path, mocker: pytest_mock.MockerFixture
    ):
        # "Due alert" is fail_if=no_match; no events found → fail, but exit 0
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": []}),
        )
        exit_code = check_alerts(
            config_files=[find_due_config], dry_run=False, now=_FIND_NOW
        )
        assert exit_code == 0

    def test_prints_summary_on_all_pass(
        self,
        find_due_config: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": [{"message": "hit"}]}),
        )
        check_alerts(
            config_files=[find_due_config], dry_run=False, now=_FIND_NOW
        )
        assert "1/1 alerts passed" in capsys.readouterr().out

    def test_prints_summary_on_failure(
        self,
        find_due_config: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": []}),
        )
        check_alerts(
            config_files=[find_due_config], dry_run=False, now=_FIND_NOW
        )
        assert "0/1 alerts passed" in capsys.readouterr().out

    def test_all_alerts_checked_despite_failure(
        self,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        config = {
            "alerts": [
                make_alert(
                    id="failing-alert",
                    name="Failing alert",
                    fail_if="no_match",
                    schedule="0 12 * * *",
                ).as_dict(),
                make_alert(
                    id="passing-alert",
                    name="Passing alert",
                    fail_if="match",
                    schedule="0 12 * * *",
                ).as_dict(),
            ]
        }
        config_file = tmp_path / "svc.yml"
        config_file.write_text(yaml.dump(config))

        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": []}, {"events": []}),
        )
        exit_code = check_alerts(
            config_files=[config_file], dry_run=False, now=_FIND_NOW
        )
        assert exit_code == 0
        assert "1/2 alerts passed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Test check_alerts() --format json
# ---------------------------------------------------------------------------


class TestCheckAlertsJsonFormat:
    def test_outputs_valid_json(
        self,
        find_due_config: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": [{"message": "hit"}]}),
        )
        check_alerts(
            config_files=[find_due_config],
            output_format="json",
            now=_FIND_NOW,
        )
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_any_failed_false_when_all_pass(
        self,
        find_due_config: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": [{"message": "hit"}]}),
        )
        check_alerts(
            config_files=[find_due_config],
            output_format="json",
            now=_FIND_NOW,
        )
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["any_failed"] is False

    def test_any_failed_true_when_alert_fails(
        self,
        find_due_config: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": []}),
        )
        check_alerts(
            config_files=[find_due_config],
            output_format="json",
            now=_FIND_NOW,
        )
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["any_failed"] is True

    def test_results_contain_expected_fields(
        self,
        find_due_config: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": []}),
        )
        check_alerts(
            config_files=[find_due_config],
            output_format="json",
            now=_FIND_NOW,
        )
        parsed = json.loads(capsys.readouterr().out)
        result = parsed["results"][0]
        assert set(result.keys()) == set(
            field.name for field in dataclasses.fields(Result)
        )

    def test_results_include_sns_topic(
        self,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        config = {"alerts": [make_alert(aws_sns_topic="my-topic").as_dict()]}
        config_file = tmp_path / "svc.yml"
        config_file.write_text(yaml.dump(config))
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": []}),
        )
        check_alerts(
            config_files=[config_file],
            output_format="json",
            now=_FIND_NOW,
        )
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["results"][0]["alert"]["aws_sns_topic"] == "my-topic"

    def test_exit_code_0_on_failure_with_json_format(
        self, find_due_config: Path, mocker: pytest_mock.MockerFixture
    ):
        mocker.patch(
            "alerts.check.boto3.client",
            return_value=make_paginator({"events": []}),
        )
        exit_code = check_alerts(
            config_files=[find_due_config],
            output_format="json",
            now=_FIND_NOW,
        )
        assert exit_code == 0

    def test_no_alerts_due_outputs_empty_results(
        self, find_due_config: Path, capsys: pytest.CaptureFixture
    ):
        now = datetime(2026, 6, 16, 15, 0, tzinfo=UTC)
        exit_code = check_alerts(
            config_files=[find_due_config],
            output_format="json",
            now=now,
        )
        parsed = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert parsed == {"any_failed": False, "results": []}


# ---------------------------------------------------------------------------
# Test main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_falls_back_to_config_dir_when_no_files_given(
        self,
        tmp_path: Path,
        alert: Alert,
        alert_config: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        """When no config files are passed, main() should glob the config dir"""
        mocker.patch("alerts.check.ALERT_CONFIG_DIR", tmp_path)
        mocker.patch("sys.argv", ["check", "--dry-run"])
        mocker.patch("alerts.check.datetime").now.return_value = _FIND_NOW
        exit_code = main()
        assert exit_code == 0
        assert alert.name in capsys.readouterr().out

    def test_fallback_glob_picks_up_yaml_extension(
        self,
        tmp_path: Path,
        alert: Alert,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        """Fallback glob should pick up both .yml and .yaml files."""
        write_config(tmp_path / "svc.yaml", [alert])
        mocker.patch("alerts.check.ALERT_CONFIG_DIR", tmp_path)
        mocker.patch("sys.argv", ["check", "--dry-run"])
        mocker.patch("alerts.check.datetime").now.return_value = _FIND_NOW
        exit_code = main()
        assert exit_code == 0
        assert alert.name in capsys.readouterr().out

    def test_raises_when_no_config_files_found(
        self,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ):
        """When no files are passed and CONFIG_DIR is empty, exit 1 with a message."""
        mocker.patch("alerts.check.ALERT_CONFIG_DIR", tmp_path)
        mocker.patch("sys.argv", ["check", "--dry-run"])
        with pytest.raises(ValueError, match="No config files"):
            main()
