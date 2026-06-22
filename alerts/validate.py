"""Validate alert YAML config files by loading them with load_config().

Usage:
    python -m alerts.validate config/*.yml

Intended for use as a pre-commit hook. Accepts one or more YAML file paths
as positional arguments, runs load_config() on each, and reports success or
failure per file. Exits non-zero if any file fails validation.
"""

import argparse
import collections
import sys
from pathlib import Path

from alerts.models import Alert, load_config


def validate_configs(paths: list[Path]) -> int:
    """Validate each config file by loading it. Returns 0 if all pass, 1 if any fail."""
    all_alerts: list[Alert] = []
    failed: list[str] = []
    for path in paths:
        try:
            alerts = load_config(path)
            all_alerts += alerts
            print(f"OK ({len(alerts)} alert(s)): {path}")
        except (ValueError, Exception) as exc:
            print(f"FAIL [{str(path)}]: {exc}")
            failed.append(str(path))

    # Make sure alert IDs are globally unique
    id_counts = collections.Counter(alert.id for alert in all_alerts)
    dupe_ids = {id: count for id, count in id_counts.items() if count > 1}
    if dupe_ids:
        for id, count in dupe_ids.items():
            print(f"FAIL: Alert ID is duplicated {count} times: '{id}'")
        failed.append("")

    if failed:
        print(f"\n{len(failed)} config file(s) failed validation.")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate alert YAML config files"
    )
    parser.add_argument(
        "config_files",
        nargs="+",
        type=Path,
        metavar="CONFIG",
        help="One or more alert YAML config files to validate",
    )
    args = parser.parse_args()
    return validate_configs(args.config_files)


if __name__ == "__main__":
    sys.exit(main())
