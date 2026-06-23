"""Unit tests for alerts/validate.py."""

from pathlib import Path

import pytest
import yaml

from alerts.validate import validate_configs
from tests.conftest import (
    SINGLE_ALERT,
    make_alert,
    write_config,
    write_raw_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_invalid_yaml(path: Path) -> Path:
    """Write a syntactically invalid YAML file."""
    path.write_text(":\n  bad: [unclosed\n")
    return path


# ---------------------------------------------------------------------------
# Test validate_configs()
# ---------------------------------------------------------------------------


class TestValidateConfigs:
    def test_valid_config_passes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        config = write_config(tmp_path / "svc.yml", [SINGLE_ALERT])
        exit_code = validate_configs([config])
        assert exit_code == 0
        assert "OK" in capsys.readouterr().out

    def test_valid_config_prints_alert_count(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        alerts = [
            make_alert(name="Alert A"),
            make_alert(name="Alert B"),
        ]
        config = write_config(tmp_path / "svc.yml", alerts)
        validate_configs([config])
        assert "2 alert(s)" in capsys.readouterr().out

    def test_missing_required_field_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        raw = make_alert().asdict()
        del raw["lookback_hours"]
        config = write_raw_config(tmp_path / "svc.yml", [raw])
        exit_code = validate_configs([config])
        assert exit_code == 1
        assert "FAIL" in capsys.readouterr().out

    def test_invalid_field_value_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        raw = make_alert().asdict()
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
        config = write_invalid_yaml(tmp_path / "bad.yml")
        exit_code = validate_configs([config])
        assert exit_code == 1
        assert "FAIL" in capsys.readouterr().out

    def test_multiple_valid_configs_all_pass(self, tmp_path: Path):
        config_a = write_config(
            tmp_path / "a.yml", [make_alert(id="alert-a", name="Alert A")]
        )
        config_b = write_config(
            tmp_path / "b.yml", [make_alert(id="alert-b", name="Alert B")]
        )
        assert validate_configs([config_a, config_b]) == 0

    def test_one_failing_config_returns_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        valid = write_config(tmp_path / "valid.yml", [SINGLE_ALERT])
        invalid = write_raw_config(
            tmp_path / "invalid.yml", [{"name": "incomplete"}]
        )
        exit_code = validate_configs([valid, invalid])
        assert exit_code == 1

    def test_all_files_checked_despite_failure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        valid = write_config(tmp_path / "valid.yml", [SINGLE_ALERT])
        invalid = write_raw_config(
            tmp_path / "invalid.yml", [{"name": "incomplete"}]
        )
        validate_configs([invalid, valid])
        out = capsys.readouterr().out
        assert "OK" in out
        assert "FAIL" in out

    def test_failure_summary_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        invalid = write_raw_config(
            tmp_path / "invalid.yml", [{"name": "incomplete"}]
        )
        validate_configs([invalid])
        assert "failed validation" in capsys.readouterr().out
