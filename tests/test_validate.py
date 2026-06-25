"""Unit tests for alerts/validate.py."""

from pathlib import Path

import pytest
import yaml

from alerts.models import Alert
from alerts.validate import validate_configs
from tests.conftest import (
    make_alert,
    write_config,
    write_raw_config,
)

# ---------------------------------------------------------------------------
# Test validate_configs()
# ---------------------------------------------------------------------------


class TestValidateConfigs:
    def test_valid_config_passes(
        self, alert_config: Path, capsys: pytest.CaptureFixture
    ):
        exit_code = validate_configs([alert_config])
        assert exit_code == 0
        assert "OK" in capsys.readouterr().out

    def test_valid_config_prints_alert_count(
        self, two_alert_config: Path, capsys: pytest.CaptureFixture
    ):
        validate_configs([two_alert_config])
        assert "2 alert(s)" in capsys.readouterr().out

    def test_valid_config_prints_path(
        self, alert_config: Path, capsys: pytest.CaptureFixture
    ):
        validate_configs([alert_config])
        assert str(alert_config) in capsys.readouterr().out

    def test_multiple_valid_configs_all_pass(
        self,
        two_alerts: tuple[Alert, Alert],
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ):
        config_a = write_config(tmp_path / "a.yml", [two_alerts[0]])
        config_b = write_config(tmp_path / "b.yml", [two_alerts[1]])
        assert validate_configs([config_a, config_b]) == 0
        out = capsys.readouterr().out
        assert str(config_a) in out
        assert str(config_b) in out

    def test_missing_required_field_fails(
        self, alert: Alert, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        raw = alert.as_dict()
        del raw["lookback_hours"]
        config = write_raw_config(tmp_path / "svc.yml", [raw])
        exit_code = validate_configs([config])
        assert exit_code == 1
        assert "FAIL" in capsys.readouterr().out

    def test_invalid_field_value_fails(
        self, alert: Alert, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        raw = alert.as_dict()
        raw["fail_if"] = "bad_value"
        config = write_raw_config(tmp_path / "svc.yml", [raw])
        exit_code = validate_configs([config])
        assert exit_code == 1

    def test_empty_alerts_key_fails(self, tmp_path: Path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text(yaml.dump({}))
        assert validate_configs([config_file]) == 1

    def test_invalid_yaml_syntax_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        # Write a syntactically invalid YAML file
        config = tmp_path / "bad.yml"
        config.write_text(":\n  bad: [unclosed\n")
        exit_code = validate_configs([config])
        assert exit_code == 1
        assert "FAIL" in capsys.readouterr().out

    def test_one_failing_config_returns_1(
        self, alert: Alert, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        valid = write_config(tmp_path / "valid.yml", [alert])
        invalid = write_raw_config(
            tmp_path / "invalid.yml", [{"name": "incomplete"}]
        )
        exit_code = validate_configs([valid, invalid])
        assert exit_code == 1

    def test_all_files_checked_despite_failure(
        self, alert: Alert, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        valid = write_config(tmp_path / "valid.yml", [alert])
        invalid = write_raw_config(
            tmp_path / "invalid.yml", [{"name": "incomplete"}]
        )
        validate_configs([invalid, valid])
        out = capsys.readouterr().out
        assert "OK" in out
        assert "FAIL" in out

    def test_invalid_config_prints_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        config_file = tmp_path / "empty.yml"
        config_file.write_text(yaml.dump({}))
        validate_configs([config_file])
        assert str(config_file) in capsys.readouterr().out

    def test_failure_summary_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        invalid = write_raw_config(
            tmp_path / "invalid.yml", [{"name": "incomplete"}]
        )
        validate_configs([invalid])
        assert "failed validation" in capsys.readouterr().out

    def test_duplicate_alert_id_returns_1(
        self,
        alert: Alert,
        alert_config: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ):
        dupe_alert = make_alert(id=alert.id)
        dupe_config = write_config(tmp_path / "dupe.yml", [dupe_alert])
        exit_code = validate_configs([alert_config, dupe_config])
        out = capsys.readouterr().out
        assert exit_code == 1
        assert "duplicate" in out
        assert alert.id in out
