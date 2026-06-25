# alerts

Scheduled GitHub Actions workflow and config files for alerting when AWS jobs
haven't produced expected CloudWatch log output.

## Motivation

CloudWatch's native alerting for "job failed to run" scenarios is brittle: it
relies on an invisible _evaluation range_ config variable that makes it hard
to understand whether an alarm will or won't fire when log data is missing.
For details, and for a sense of how complicated the logic is, see the
AWS docs on [Configuring how CloudWatch alarms treat missing
data](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarms-and-missing-data.html).

This repo is intended to replace our CloudWatch alarms with custom code
that is easier to understand and more closely tailored to our alerting needs.
It makes use of a scheduled GitHub Actions workflow that runs every three hours:
On each run, it determines which configured alerts are due to run
based on their cron schedule, and it queries CloudWatch Logs directly to look
for signs that something is wrong. When alerts fail, the workflow publishes
to [AWS SNS topics](https://docs.aws.amazon.com/sns/latest/dg/sns-create-topic.html)
configured for each alert, so that the stakeholders for each service can recieve
alert notifications via email.

## A note on terminology

The code and the docs in this repo all use the term "alert" to refer to a check
that runs on a schedule in order to search for irregularities in CloudWatch logs
during specific windows of time. One "alert" typically corresponds to one type
of irregularity in one particular service. When we evaluate an alert at a
specific moment in time, we say that we "check" the alert. We say that an alert
"fails" if there are irregularities in its logs during the time window, and it
"passes" otherwise. Each alert has a specific schedule on which it must be
checked, and if at a given point in time an alert should be checked, then we say
it is "due".

## How it works

1. The `check-alerts` workflow runs at midnight, 3am, 6am, 9am, noon, 3pm,
   6pm, and 9pm UTC (i.e. `0 */3 * * *`).
2. The workflow calls `alerts/check.py`, which parses all YAML files in the
   `config/` subdirectory.
3. For each alert, the script determines whether the alert's `schedule` was due
   within the past 3 hours using [croniter](https://github.com/kiorky/croniter).
4. If the alert is due, it queries the specified CloudWatch log group for
   events matching `log_query` within the `lookback_hours` window.
5. The result is evaluated against `fail_if`, a config value that determines
   the failure condition of the alert. All alerts run before the script exits,
   so that a single failure doesn't short-circuit the rest.
6. The workflow fails if any alert fails, surfacing the issue in GitHub.
   The workflow will also notify any configured [AWS SNS
   topics](https://docs.aws.amazon.com/sns/latest/dg/sns-create-topic.html)
   for each alert that has failed, so that a wider group of stakeholders can
   receive notifications for each failure. This is important since scheduled
   GitHub workflows will only notify the original workflow author when they
   fail ([docs](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)).

## Repo structure

```
alerts/
├── .github/
│   └── workflows/
│       ├── check-alerts.yml      # Runs every 3h; evaluates all alert configs
│       └── pre-commit.yml        # Runs pre-commit hooks on PRs and pushes
│       └── test.yml              # Runs unit tests on PRs and pushes
├── alerts/
│   ├── check.py                  # Alert evaluation logic + CLI
│   ├── constants.py              # Shared constant values
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

We organize alerts into config files by service. For example, the
[`service-spark-iasworld`
service](https://github.com/ccao-data/service-spark-iasworld/) has
two alerts: One that confirms the ingest job ran at the expected time,
and one that confirms the ingest job ran without encountering an error.

### Alert config example

```yaml
alerts:
  - id: my-job-not-run           # Required: Unique slug, acts as the alert identifier.
    name: "My job not run"       # Required. Unique, human-readable alert name.
    log_group: /ccao/jobs/my-job # Required. CloudWatch log group to query.
    log_query: "info"            # Required. String to search for in log events.
    fail_if: "no_match"          # Required. "no_match" or "match" (see below).
    schedule: "0 12 * * 1-5"     # Required. Cron expression indicating when an alert should be checked.
    lookback_hours: 12           # Required. How far back to search for logs.
    aws_sns_topic: topic-name    # Optional. Name of the AWS SNS topic to notify on failure.
    failure_message: Job failed  # Optional. Custom error message to show in notifications.
```

### Alert config field reference

| Field             | Type                      | Description                                                                                                                                                                                                |
|-------------------|---------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `id`              | string                    | Unique slug to act as an identifier for the alert. Must be formatted using only alphanumeric characters and hyphens.                                                                                       |
| `name`            | string                    | Unique human-readable name shown in workflow output and failure messages.                                                                                                                                  |
| `log_group`       | string                    | Name of the CloudWatch log group to search.                                                                                                                                                                |
| `log_query`       | string                    | Filter pattern passed to [`filter_log_events`](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_FilterLogEvents.html). Supports CloudWatch filter pattern syntax.                  |
| `fail_if`         | `"no_match"` \| `"match"` | Alert failure condition. `"no_match"` means the alert fails if **no** events match (used to detect a job that hasn't run). `"match"` means an alert fails if **any** events match (used to detect errors). |
| `schedule`        | string                    | Cron expression (5-field, UTC) for when the alert should be evaluated. See [scheduling constraints](#scheduling-constraints) below.                                                                        |
| `lookback_hours`  | integer                   | Number of hours back from the check time to search for matching log events.                                                                                                                                |
| `aws_sns_topic`   | string                    | Optional. Name of the AWS SNS topic to notify on failure. This should _not_ be an ARN, since ARNs contain our AWS account ID, which is not public.                                                         |
| `failure_message` | string                    | Optional. Custom error message to show in notifications. When absent, the code will construct a simple error message fit for internal use.                                                                 |

### Choosing `fail_if`

- Use `fail_if: "no_match"` to assert a job **ran** — the alert fires if no
  matching log events are found (i.e. the job did not emit the expected logs).
- Use `fail_if: "match"` to assert a job ran **without errors** — the alert
  fires if matching log events are found (i.e. errors are present in the logs).

### Scheduling constraints

Alert schedules are validated against two constraints:

1. **Top-of-hour only**: The minute field must be `0`. Alerts may only fire at
   the top of an hour.

2. **Workflow-aligned hours only**: The hour field may only contain values from
   `{0, 3, 6, 9, 12, 15, 18, 21}` — the same hours that the `check-alerts`
   workflow is scheduled to run (midnight, 3am, 6am, 9am, noon, 3pm, 6pm,
   9pm UTC).

**Why this matters:** GitHub Actions scheduled workflows can be delayed by up
to three hours due to runner availability. To prevent duplicate notifications
from back-to-back delayed and on-time runs, the code uses a 3-hour window that
exactly matches the workflow's 3-hour run interval when it checks for due
alerts. This non-overlapping property only holds when alert schedules are
aligned to the same hours as the workflow:

- Assume a workflow can run at its scheduled time, or up to three hours later,
  but never before its scheduled time.
- If a workflow run is delayed, and an alert is due at the exact time the
  workflow was scheduled to run, then our code will still consider the alert to
  be due because the window covers the full 3-hour interval.
- The _next_ workflow run (3 hours later) cannot also consider the same alert
  to be due, because the next workflow run can't start any sooner than it is
  scheduled to run, and the due window check is strict (`elapsed_time < 3h`).

An example might help illustrate this logic:

- Assume that the `check-alerts` workflow is scheduled to run every three hours.
  This means it is scheduled to run at noon and 3pm.
- Assume that the noon workflow run is delayed, and actually runs at 1:30pm.
  Assume that the 3pm workflow run is on time.
- If an alert is scheduled outside the workflow's hours, e.g. 1pm, then both
  the 1:30pm workflow run and the 3pm workflow run will consider it to be due.
  If the alert fails, both workflow runs will notify stakeholders of the same
  failure, thereby producing a duplicate notification.
- If an alert is scheduled _on_ the workflow's hours, e.g. noon, then the 1:30pm
  workflow run will consider it to be due, but the 3pm workflow run will _not_
  consider it to be due

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

# Check all alerts
uv run python -m alerts.check

# Check alerts in one config file
uv run python -m alerts.check config/service-spark-iasworld.yml
```
