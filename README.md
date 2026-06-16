# alerts

Scheduled GitHub Actions workflow and config files for alerting when AWS jobs
haven't produced expected CloudWatch log output.

## Motivation

CloudWatch's native alerting for "job hasn't run" scenarios is brittle: it
relies on an invisible _evaluation range_ config variable that makes it hard
to reason about when an alarm will or won't fire when log data is missing.

This repo replaces that pattern with a scheduled GitHub Actions workflow that
runs every hour, checks which alerts are due based on their cron schedule, and
queries CloudWatch Logs directly. Failures surface as failed workflow runs.

## How it works

1. The `check-alerts` workflow runs at the top of every hour.
2. It calls `scripts/check_alerts.py`, passing all files in `alerts/*.yml`.
3. For each alert, the script checks whether the alert's `schedule` fired
   within the past hour using [croniter](https://github.com/kiorky/croniter).
4. If the alert is due, it queries the specified CloudWatch log group for
   events matching `log_query` within the `lookback_hours` window.
5. The result is evaluated against `error_if`. All alerts run before the
   script exits — a single failure doesn't short-circuit the rest.
6. The workflow fails if any alert fails, surfacing the issue in GitHub.

## Repo structure

```
alerts/
├── .github/
│   └── workflows/
│       ├── check-alerts.yml      # Runs hourly; evaluates all alert configs
│       └── pre-commit.yml        # Runs pre-commit hooks on PRs
├── alerts/
│   └── *.yml                     # One config file per monitored service
├── scripts/
│   └── check_alerts.py           # Alert evaluation logic
├── tests/
│   └── test_*.py                 # One unit test file per module
├── pyproject.toml                # Python config, including dependencies
└── uv.lock                       # uv lockfile
```

## Adding a new alert

Create a new file in `alerts/` (one file per service is the convention) and
define one or more alerts under the `alerts` key:

```yaml
alerts:
  - name: "My job not run"       # Required. Unique, human-readable alert name.
    log_group: /ccao/jobs/my-job # Required. CloudWatch log group to search.
    log_query: "info"            # Required. String to search for in log events.
    error_if: "no_match"         # Required. "no_match" or "match" (see below).
    schedule: "0 14 * * 1-5"     # Required. Cron expression for when to check.
    lookback_hours: 12           # Required. How far back to search for logs.
```

### Field reference

| Field            | Type                      | Description                                                                                                                                                                               |
|------------------|---------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `name`           | string                    | Unique human-readable name shown in workflow output and failure messages.                                                                                                                 |
| `log_group`      | string                    | Name of the CloudWatch log group to search.                                                                                                                                               |
| `log_query`      | string                    | Filter pattern passed to [`filter_log_events`](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_FilterLogEvents.html). Supports CloudWatch filter pattern syntax. |
| `error_if`       | `"no_match"` \| `"match"` | `"no_match"`: fail if **no** events match (use to detect a job that hasn't run). `"match"`: fail if **any** events match (use to detect errors).                                          |
| `schedule`       | string                    | Cron expression (5-field, UTC) for when the alert should be evaluated. The workflow runs hourly; alerts whose most recent scheduled time falls within the past hour are checked.          |
| `lookback_hours` | integer                   | Number of hours back from the check time to search for matching log events.                                                                                                               |

### Choosing `error_if`

- Use `error_if: "no_match"` to assert a job **ran** — the alert fires if no
  matching log events are found (i.e. the job was silent).
- Use `error_if: "match"` to assert a job ran **without errors** — the alert
  fires if matching log events are found (i.e. errors were logged).

## Required secrets

The workflow reads AWS credentials from repository secrets:

| Secret | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS access key with CloudWatch Logs read permissions. |
| `AWS_SECRET_ACCESS_KEY` | Corresponding AWS secret key. |
| `AWS_DEFAULT_REGION` | AWS region where the log groups reside (e.g. `us-east-1`). |

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies (including dev tools)
uv sync --extra dev

# Run tests
uv run pytest

# Run the script
uv run python scripts/check_alerts.py alerts/*.yml
```
