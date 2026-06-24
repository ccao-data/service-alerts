"""Shared constants"""

from pathlib import Path

# Number of hours to look back when determining whether an alert is due for
# checking. See README.md for details about why we set this constant to this
# particular value.
#
# This window must match the `on.schedule` configuration for the workflow that
# runs the checks. If you update this constant, make sure to update the
# workflow schedule as well.
CHECK_WINDOW_HOURS = 3

# Alerts must only be scheduled at hours that align with the workflow's own
# scheduled run times. Scheduling at any other hour risks duplicate notifications
# if a workflow run is delayed past the next scheduled hour. See README.md for
# details.
ALLOWED_SCHEDULE_HOURS = frozenset({0, 3, 6, 9, 12, 15, 18, 21})

# AWS region to use for API calls
AWS_REGION = "us-east-1"

# Directory where we store alert config files
ALERT_CONFIG_DIR = Path("config")
