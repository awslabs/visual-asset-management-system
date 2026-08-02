---
sidebar_label: Executions
title: Execution Commands
---

# Execution Commands

Inspect and manage workflow executions across all assets. Executions may span files from multiple
assets, so these commands are keyed on the execution ID rather than an asset. To start an execution,
use [`workflow execute`](workflows.md#workflow-execute); for a single asset's execution history, use
[`workflow list-executions`](workflows.md#workflow-list-executions).

---

## execution list

List workflow executions globally, permission-filtered. You only see executions whose workflow you
can read and whose input or output asset you can read. Supports rich filters and pagination. By
default only recent executions — those started within the last 90 days — are listed; use
`--filter-start-date` and `--filter-end-date` to query an explicit date range. The applied window is
returned as `filterStartDate` (and `filterEndDate` when supplied) in the response.

Each execution reports its output target — `Output Type` (`asset`, or `none` for a results-only run)
and `Output Asset` as `databaseId:assetId`. Both lines are omitted for a results-only execution, which
writes no files and therefore has no destination asset.

```bash
vamscli execution list
vamscli execution list -w my-workflow --status RUNNING
vamscli execution list --filter-start-date 2026-01-01T00:00:00Z --filter-end-date 2026-02-01T00:00:00Z
vamscli execution list --group-id batch-2026-01 --auto-paginate
vamscli execution list --triggered-by user@example.com --json-output
```

| Option                            | Description                                                                      |
| --------------------------------- | -------------------------------------------------------------------------------- |
| `-w, --workflow-id`               | Filter by workflow ID                                                            |
| `--workflow-database-id`          | Filter by workflow database ID                                                   |
| `--status`                        | Filter by execution status (e.g. `RUNNING`, `SUCCEEDED`, `FAILED`)               |
| `--trigger-type`                  | Filter by trigger type (`Manual` / `File-Upload`)                                |
| `--group-id`                      | Filter by `executionGroupId`                                                     |
| `--triggered-by`                  | Filter by the user ID that triggered the execution                               |
| `--filter-start-date`             | Only executions started on/after this ISO-8601 date-time (default: 90 days ago)  |
| `--filter-end-date`               | Only executions started on/before this ISO-8601 date-time (optional upper bound) |
| `--page-size`                     | Items per page (max 100)                                                         |
| `--auto-paginate` / `--max-items` | Fetch all pages (up to max-items)                                                |
| `--starting-token`                | Continuation token for manual pagination                                         |

---

## execution details

Show an execution's full detail and traceability: per-pipeline status, input files, input metadata,
input configurations, and outputs (files, metadata, results).

```bash
vamscli execution details my-execution-id
vamscli execution details my-execution-id --json-output
```

---

## execution logs

Retrieve an execution's logs. `truncated` mode returns the stored log text, and — because the stored
log is often empty (it is captured before CloudWatch finishes ingesting the run's events) — falls back
to a live CloudWatch search for the same scope when the stored copy is empty. `full` mode always runs a
live CloudWatch search scoped to the execution (and optionally a single pipeline execution). The output
reports `Source: stored` or `Source: live` so you can tell which was returned.

Returned log text is redacted: credential-bearing values — authorization headers, bearer tokens, AWS
access-key IDs, JSON web tokens, and labelled secret fields such as `SecretAccessKey` and
`SessionToken` — are replaced with `<redacted>` before the logs are stored or returned.

```bash
vamscli execution logs my-execution-id
vamscli execution logs my-execution-id --pipeline-execution-id my-pipeline-exec
vamscli execution logs my-execution-id --mode full --limit 200
```

`full` mode prints each group of logs under its own heading, and omits a heading it has nothing for:

| Section                 | Contents                                                                                                                                                                                 |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Events`                | The CloudWatch search over the workflow log group, scoped to the execution.                                                                                                              |
| `State Machine History` | The Step Functions state-transition timeline (whole-execution requests). Available immediately, with no ingestion lag.                                                                   |
| `Sub-Process Logs`      | With `--pipeline-execution-id`: the step invocation log, any logs the pipeline registered, and any sub-execution history. Each line names the log group it came from.                    |
| `Warnings`              | Logs that could not be read — a missing permission, or a registration list beyond the per-request cap. Shown rather than dropped, so partial output is not mistaken for complete output. |

The **step invocation log** is the log of the resource the workflow invoked for that step — for a
`Lambda` step, that function's own CloudWatch log group. It holds the reason a launch failed before
the pipeline's own logging began. `SQS`, `EventBridge`, and `DeadlineCloud` steps have no derivable
invocation log, so nothing is reported for them.

```bash
# Everything reachable for one step, including that step's own invocation log
vamscli execution logs my-execution-id --pipeline-execution-id my-pipeline-exec --mode full
```

| Option                        | Description                                 |
| ----------------------------- | ------------------------------------------- |
| `--mode`                      | `truncated` (default) or `full`             |
| `--pipeline-execution-id`     | Scope logs to one pipeline execution        |
| `--filter-pattern`            | (full) additional CloudWatch filter pattern |
| `--limit`                     | (full) max events (capped at 1000)          |
| `--start-time` / `--end-time` | (full) epoch-millisecond window             |
| `--next-token`                | (full) CloudWatch pagination token          |

---

## execution abort

Abort a running execution, or an entire execution group with `--group-id`. When aborting a group,
pass any member execution ID (the route is keyed on an execution ID); the group abort is bounded per
request and reports `moreRemaining` when more members remain.

```bash
# Abort one execution
vamscli execution abort my-execution-id

# Abort every active execution in a group
vamscli execution abort my-execution-id --group-id batch-2026-01
```

---

## execution rerun

Re-run an execution, reconstructed from its stored records. This launches a new execution (new
execution ID); optionally reuse or assign an execution group.

```bash
vamscli execution rerun my-execution-id
vamscli execution rerun my-execution-id --execution-group-id batch-2026-02
```

---

## execution permanent-delete

Permanently delete an execution's DynamoDB records (admin only). This does not touch Step Functions
history and requires the execution to not be in progress. It is irreversible; the CLI prompts for
confirmation unless `--yes` (or `--json-output`) is passed.

```bash
vamscli execution permanent-delete my-execution-id --yes
```

:::warning
Permanent delete removes the execution's traceability records (inputs, outputs, metadata, logs) from
DynamoDB. Abort a running execution before attempting to permanently delete it.
:::

---

## Related pages

-   [Workflows](workflows.md) — create workflows and start executions
-   [Pipelines](pipelines.md) — pipeline and template definitions
