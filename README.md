# alerts

Scheduled GitHub Actions workflow and config files for alerting when AWS jobs
haven't produced expected CloudWatch log output.

## Motivation

CloudWatch's native alerting for "job hasn't run" scenarios is brittle: it
relies on an invisible _evaluation range_ config variable that makes it hard
to reason about when an alarm will or won't fire when log data is missing.

This repo replaces that pattern with a scheduled GitHub Actions workflow that
runs every three hours, checks which alerts are due based on their cron
schedule, and queries CloudWatch Logs directly. Failures surface as failed
workflow runs and optional AWS SNS notifications.

## How it works

1. The `check-alerts` workflow runs at midnight, 3am, 6am, 9am, noon, 3pm,
   6pm, and 9pm UTC (i.e. `0 */3 * * *`).
2. It calls `alerts/check.py`, passing all files in `config/*.yml`.
3. For each alert, the script checks whether the alert's `schedule` fired
   within the past 3 hours using [croniter](https://github.com/kiorky/croniter).
4. If the alert is due, it queries the specified CloudWatch log group for
   events matching `log_query` within the `lookback_hours` window.
5. The result is evaluated against `error_if`. All alerts run before the
   script exits, so that a single failure doesn't short-circuit the rest.
6. The workflow fails if any alert fails, surfacing the issue in GitHub.
   The workflow will also notify configured [AWS SNS
   topics](https://docs.aws.amazon.com/sns/latest/dg/sns-create-topic.html)
   for each alert that has failed, in order to notify stakeholders of any
   issues.

## Repo structure

```
alerts/
├── .github/
│   └── workflows/
│       ├── check-alerts.yml      # Runs every 3h; evaluates all alert configs
│       └── pre-commit.yml        # Runs pre-commit hooks on PRs
├── alerts/
│   ├── check.py                  # Alert evaluation logic + CLI
│   ├── models.py                 # Shared data models and config loading
│   ├── notify.py                 # SNS notification logic + CLI
│   └── validate.py               # Config validation (used as pre-commit hook)
├── config/
│   └── *.yml                     # One config file per monitored service
├── tests/
│   └── test_*.py                 # One unit test file per module
├── pyproject.toml                # Python config, including dependencies
└── uv.lock                       # uv lockfile
```

## Adding a new alert

Create a new YAML file in `config/` (one file per service is the convention)
and define one or more alerts under the `alerts` key.

### Alert config example

```yaml
alerts:
  - name: "My job not run"       # Required. Unique, human-readable alert name.
    log_group: /ccao/jobs/my-job # Required. CloudWatch log group to search.
    log_query: "info"            # Required. String to search for in log events.
    error_if: "no_match"         # Required. "no_match" or "match" (see below).
    schedule: "0 12 * * 1-5"     # Required. Cron expression for when to check.
    lookback_hours: 12           # Required. How far back to search for logs.
    aws_sns_topic: topic-name    # Optional. Name of the AWS SNS topic to notify on failure.
```

### Alert config field reference

| Field            | Type                      | Description                                                                                                                                                                               |
|------------------|---------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `name`           | string                    | Unique human-readable name shown in workflow output and failure messages.                                                                                                                 |
| `log_group`      | string                    | Name of the CloudWatch log group to search.                                                                                                                                               |
| `log_query`      | string                    | Filter pattern passed to [`filter_log_events`](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_FilterLogEvents.html). Supports CloudWatch filter pattern syntax. |
| `error_if`       | `"no_match"` \| `"match"` | `"no_match"`: fail if **no** events match (use to detect a job that hasn't run). `"match"`: fail if **any** events match (use to detect errors).                                          |
| `schedule`       | string                    | Cron expression (5-field, UTC) for when the alert should be evaluated. See [scheduling constraints](#scheduling-constraints) below.                                                       |
| `lookback_hours` | integer                   | Number of hours back from the check time to search for matching log events.                                                                                                               |
| `aws_sns_topic`  | string                    | Optional. Name of the AWS SNS topic to notify on failure. This should _not_ be an ARN, since ARNs contain our AWS account ID, which is not public.                                        |

### Choosing `error_if`

- Use `error_if: "no_match"` to assert a job **ran** — the alert fires if no
  matching log events are found (i.e. the job was silent).
- Use `error_if: "match"` to assert a job ran **without errors** — the alert
  fires if matching log events are found (i.e. errors were logged).

### Scheduling constraints

Alert schedules are validated against two constraints:

1. **Top-of-hour only**: The minute field must be `0`. Alerts may only fire at
   the top of an hour.

2. **Workflow-aligned hours only**: The hour field may only contain values from
   `{0, 3, 6, 9, 12, 15, 18, 21}` — the same hours that the `check-alerts`
   workflow is scheduled to run (midnight, 3am, 6am, 9am, noon, 3pm, 6pm,
   9pm UTC).

**Why this matters:** GitHub Actions scheduled workflows can be delayed by up
to ~2 hours due to runner availability. To prevent duplicate notifications
from back-to-back delayed and on-time runs, the `is_due()` check uses a
3-hour window that exactly matches the workflow's 3-hour run interval. This
non-overlapping property only holds when alert schedules are aligned to the
same hours as the workflow:

- A workflow run fires at its scheduled hour or later (never before).
- If a run is delayed, `is_due()` still catches the alert because the window
  covers the full 3-hour interval.
- The _next_ workflow run (3 hours later) cannot also catch the same alert,
  because the elapsed time since that alert's last fire is exactly 3 hours at
  the next run's earliest possible start — and the window check is strict
  (`elapsed < 3h`).
- If an alert is scheduled outside the workflow's hours (e.g. 1pm), a delayed
  noon run and an on-time 3pm run could both see it as due, producing
  duplicate notifications.

Valid schedule examples:

```yaml
schedule: "0 0 * * *"      # Midnight daily
schedule: "0 12 * * *"     # Noon daily
schedule: "0 */3 * * *"    # Every 3 hours (all workflow-aligned hours)
schedule: "0 0,12 * * *"   # Midnight and noon
schedule: "0 9 * * 1-5"    # 9am on weekdays
schedule: "0 12 1 * *"     # Noon on the 1st of each month
```

Invalid schedule examples:

```yaml
schedule: "0 1 * * *"      # 1am is not a workflow-aligned hour
schedule: "30 12 * * *"    # Minute is not 0
schedule: "0 */2 * * *"    # Fires at 2am, 4am, etc. — not all aligned
```

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.
[Authenticating with
AWS](https://github.com/ccao-data/wiki/blob/master/How-To/Setup-the-AWS-Command-Line-Interface-and-Multi-factor-Authentication.md)
is necessary in order to check alerts.

```bash
# Install dependencies (including dev tools)
uv sync --extra dev

# Run tests
uv run pytest

# Run checks for one config file
uv run python -m alerts.check config/service-spark-iasworld.yml

# Run the script on all .yml files in the config dir
uv run python -m alerts.check config/*.yml
```
