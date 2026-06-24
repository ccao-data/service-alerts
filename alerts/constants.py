from pathlib import Path

# The workflow runs every CHECK_WINDOW_HOURS hours, and is_due() uses the same
# window. When interval == window, the next workflow run sees any alert fire at
# exactly `window` hours ago, which fails the strict `<` check and prevents
# duplicate notifications. See README.md for details.
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
