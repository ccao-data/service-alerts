"""Unit tests for check_alerts.py."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from check_alerts import evaluate_alert, is_due, load_config, query_cloudwatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def make_alert(**overrides) -> dict:
    """Return a minimal valid alert dict, with optional field overrides."""
    base = {
        "name": "Test alert",
        "log_group": "/test/logs",
        "log_query": "info",
        "error_if": "no_match",
        "lookback_hours": 12,
        "schedule": "0 12 * * *",
        "_source_file": "test.yml",
    }
    return {**base, **overrides}


def make_paginator(*pages) -> MagicMock:
    """Return a mock CloudWatch client whose paginator yields *pages*."""
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = iter(pages)
    return client


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_alerts_with_source_file(self, tmp_path: Path):
        config = {
            "alerts": [
                {
                    "name": "My alert",
                    "log_group": "/my/group",
                    "log_query": "error",
                    "error_if": "match",
                    "schedule": "0 12 * * *",
                    "lookback_hours": 6,
                }
            ]
        }
        config_file = tmp_path / "my-service.yml"
        config_file.write_text(yaml.dump(config))

        alerts = load_config(config_file)

        assert len(alerts) == 1
        assert alerts[0]["name"] == "My alert"
        assert alerts[0]["_source_file"] == str(config_file)

    def test_returns_multiple_alerts(self, tmp_path: Path):
        config = {
            "alerts": [
                {
                    "name": "A",
                    "log_group": "/g",
                    "log_query": "x",
                    "error_if": "match",
                    "schedule": "0 * * * *",
                    "lookback_hours": 1,
                },
                {
                    "name": "B",
                    "log_group": "/g",
                    "log_query": "y",
                    "error_if": "no_match",
                    "schedule": "0 * * * *",
                    "lookback_hours": 1,
                },
            ]
        }
        config_file = tmp_path / "svc.yml"
        config_file.write_text(yaml.dump(config))

        alerts = load_config(config_file)

        assert len(alerts) == 2
        assert [a["name"] for a in alerts] == ["A", "B"]

    def test_returns_empty_list_when_no_alerts_key(self, tmp_path: Path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text(yaml.dump({}))

        alerts = load_config(config_file)

        assert alerts == []

    def test_all_alerts_get_source_file(self, tmp_path: Path):
        config = {
            "alerts": [
                {
                    "name": "A",
                    "log_group": "/g",
                    "log_query": "x",
                    "error_if": "match",
                    "schedule": "0 * * * *",
                    "lookback_hours": 1,
                },
                {
                    "name": "B",
                    "log_group": "/g",
                    "log_query": "y",
                    "error_if": "no_match",
                    "schedule": "0 * * * *",
                    "lookback_hours": 1,
                },
            ]
        }
        config_file = tmp_path / "svc.yml"
        config_file.write_text(yaml.dump(config))

        alerts = load_config(config_file)

        for alert in alerts:
            assert alert["_source_file"] == str(config_file)


# ---------------------------------------------------------------------------
# is_due
# ---------------------------------------------------------------------------


class TestIsDue:
    # Schedule: daily at 12:00 UTC — "0 12 * * *"
    # prev_fire at 12:00; elapsed from 12:00 to now

    def test_due_when_fired_30_minutes_ago(self):
        # 12:30 UTC — fired 30 min ago, well within 1-hour window
        now = datetime(2026, 6, 16, 12, 30, tzinfo=UTC)
        assert is_due("0 12 * * *", now) is True

    def test_due_when_fired_59_minutes_ago(self):
        # 12:59 UTC — fired 59 min ago, just inside window
        now = datetime(2026, 6, 16, 12, 59, tzinfo=UTC)
        assert is_due("0 12 * * *", now) is True

    def test_not_due_when_fired_more_than_one_hour_ago(self):
        # 13:30 UTC — fired at 12:00, elapsed = 90 min
        now = datetime(2026, 6, 16, 13, 30, tzinfo=UTC)
        assert is_due("0 12 * * *", now) is False

    def test_not_due_when_not_scheduled_today(self):
        # Schedule: monthly on the 1st — "0 12 1 * *"
        # now is the 16th; prev fire was 15+ days ago
        now = datetime(2026, 6, 16, 12, 30, tzinfo=UTC)
        assert is_due("0 12 1 * *", now) is False

    def test_hourly_schedule_due_within_same_hour(self):
        # Schedule: every hour — "0 * * * *"
        # prev fire was at the top of this hour, elapsed < 60 min
        now = datetime(2026, 6, 16, 12, 45, tzinfo=UTC)
        assert is_due("0 * * * *", now) is True

    def test_every_two_hours_due_within_window(self):
        # Schedule fires every 2 hours: "0 */2 * * *" → 12:00, 14:00, ...
        # now = 12:30 — prev fire at 12:00, elapsed = 30 min → due
        now = datetime(2026, 6, 16, 12, 30, tzinfo=UTC)
        assert is_due("0 */2 * * *", now) is True

    def test_every_two_hours_not_due_outside_window(self):
        # now = 13:30 — prev fire at 12:00, elapsed = 90 min → not due
        now = datetime(2026, 6, 16, 13, 30, tzinfo=UTC)
        assert is_due("0 */2 * * *", now) is False


# ---------------------------------------------------------------------------
# query_cloudwatch
# ---------------------------------------------------------------------------


class TestQueryCloudwatch:
    NOW = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

    def test_returns_true_when_events_found(self):
        client = make_paginator({"events": [{"message": "info: job started"}]})

        result = query_cloudwatch("/test/logs", "info", 12, self.NOW, client)

        assert result is True

    def test_returns_false_when_no_events(self):
        client = make_paginator({"events": []})

        result = query_cloudwatch("/test/logs", "info", 12, self.NOW, client)

        assert result is False

    def test_returns_false_when_events_key_missing(self):
        client = make_paginator({})

        result = query_cloudwatch("/test/logs", "info", 12, self.NOW, client)

        assert result is False

    def test_returns_true_on_first_page_with_events(self):
        # Two pages: first has events, second is empty — should short-circuit
        client = make_paginator(
            {"events": [{"message": "hit"}]},
            {"events": []},
        )

        result = query_cloudwatch("/test/logs", "info", 12, self.NOW, client)

        assert result is True

    def test_returns_true_when_events_on_second_page(self):
        # First page empty, second page has events
        client = make_paginator(
            {"events": []},
            {"events": [{"message": "hit"}]},
        )

        result = query_cloudwatch("/test/logs", "info", 12, self.NOW, client)

        assert result is True

    def test_passes_correct_time_window_to_paginator(self):
        now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
        client = make_paginator({"events": []})

        query_cloudwatch("/test/logs", "info", 12, now, client)

        _, kwargs = client.get_paginator.return_value.paginate.call_args
        expected_start = int((now.timestamp() - 12 * 3600) * 1000)
        expected_end = int(now.timestamp() * 1000)
        assert kwargs["startTime"] == expected_start
        assert kwargs["endTime"] == expected_end

    def test_passes_correct_filter_pattern(self):
        client = make_paginator({"events": []})

        query_cloudwatch("/test/logs", "my_pattern", 6, self.NOW, client)

        _, kwargs = client.get_paginator.return_value.paginate.call_args
        assert kwargs["filterPattern"] == "my_pattern"
        assert kwargs["logGroupName"] == "/test/logs"


# ---------------------------------------------------------------------------
# evaluate_alert
# ---------------------------------------------------------------------------


class TestEvaluateAlert:
    NOW = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

    def _client_with_match(self, found: bool) -> MagicMock:
        events = [{"message": "hit"}] if found else []
        return make_paginator({"events": events})

    # error_if = no_match
    def test_no_match_alert_fails_when_no_logs_found(self):
        alert = make_alert(error_if="no_match")
        client = self._client_with_match(found=False)

        passed, message = evaluate_alert(alert, self.NOW, client)

        assert passed is False
        assert "FAIL" in message
        assert alert["name"] in message

    def test_no_match_alert_passes_when_logs_found(self):
        alert = make_alert(error_if="no_match")
        client = self._client_with_match(found=True)

        passed, message = evaluate_alert(alert, self.NOW, client)

        assert passed is True
        assert "PASS" in message

    # error_if = match
    def test_match_alert_fails_when_logs_found(self):
        alert = make_alert(error_if="match")
        client = self._client_with_match(found=True)

        passed, message = evaluate_alert(alert, self.NOW, client)

        assert passed is False
        assert "FAIL" in message
        assert alert["name"] in message

    def test_match_alert_passes_when_no_logs_found(self):
        alert = make_alert(error_if="match")
        client = self._client_with_match(found=False)

        passed, message = evaluate_alert(alert, self.NOW, client)

        assert passed is True
        assert "PASS" in message

    # Message content
    def test_fail_message_includes_log_group(self):
        alert = make_alert(error_if="no_match", log_group="/my/group")
        client = self._client_with_match(found=False)

        _, message = evaluate_alert(alert, self.NOW, client)

        assert "/my/group" in message

    def test_fail_message_includes_lookback_hours(self):
        alert = make_alert(error_if="no_match", lookback_hours=6)
        client = self._client_with_match(found=False)

        _, message = evaluate_alert(alert, self.NOW, client)

        assert "6" in message

    def test_fail_message_includes_log_query(self):
        alert = make_alert(error_if="no_match", log_query="my_pattern")
        client = self._client_with_match(found=False)

        _, message = evaluate_alert(alert, self.NOW, client)

        assert "my_pattern" in message
