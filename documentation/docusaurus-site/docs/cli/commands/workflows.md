---
sidebar_label: Workflows
title: Workflow Commands
---

# Workflow Commands

Manage and execute processing workflows on assets. Workflows are automated pipelines that process asset files through one or more stages.

---

## workflow list

List workflows in a database or all workflows across all databases.

```bash
vamscli workflow list [OPTIONS]
```

| Option                | Type    | Required | Description                                                               |
| --------------------- | ------- | -------- | ------------------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT    | No       | Database ID to list workflows from (omit for all workflows)               |
| `--show-deleted`      | Flag    | No       | Include deleted workflows                                                 |
| `--page-size`         | INTEGER | No       | Number of items per page                                                  |
| `--max-items`         | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default 10000) |
| `--starting-token`    | TEXT    | No       | Token for manual pagination                                               |
| `--auto-paginate`     | Flag    | No       | Automatically fetch all items                                             |
| `--json-output`       | Flag    | No       | Output raw JSON response                                                  |

:::note
`--auto-paginate` and `--starting-token` are mutually exclusive. Use `--auto-paginate` for automatic pagination or `--starting-token` for manual pagination. `--max-items` applies only with `--auto-paginate` and is ignored otherwise.
:::

```bash
vamscli workflow list
vamscli workflow list -d my-database
vamscli workflow list -d my-database --auto-paginate
vamscli workflow list -d my-database --auto-paginate --max-items 5000
vamscli workflow list -d my-database --page-size 200
vamscli workflow list -d my-database --starting-token "token123" --page-size 200
vamscli workflow list -d my-database --show-deleted
vamscli workflow list --json-output
```

---

## workflow list-executions

List workflow executions for a specific asset, optionally filtered by workflow.

```bash
vamscli workflow list-executions [OPTIONS]
```

| Option                   | Type    | Required | Description                                                               |
| ------------------------ | ------- | -------- | ------------------------------------------------------------------------- |
| `-d`, `--database-id`    | TEXT    | Yes      | Database ID containing the asset                                          |
| `-a`, `--asset-id`       | TEXT    | Yes      | Asset ID to list executions for                                           |
| `-w`, `--workflow-id`    | TEXT    | No       | Filter by a specific workflow ID                                          |
| `--workflow-database-id` | TEXT    | No       | Workflow's database ID (used for filtering)                               |
| `--page-size`            | INTEGER | No       | Number of items per page (max 50; defaults to 50)                         |
| `--max-items`            | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default 10000) |
| `--starting-token`       | TEXT    | No       | Token for manual pagination                                               |
| `--auto-paginate`        | Flag    | No       | Automatically fetch all items                                             |
| `--json-output`          | Flag    | No       | Output raw JSON response                                                  |

:::warning[AWS Step Functions API throttling]
Page size is limited to 50 items per page because each execution requires a `describe_execution` call to AWS Step Functions. Requesting a larger `--page-size` fails with an error. Use `--auto-paginate` to fetch more items across multiple pages, and `-w`/`--workflow-id` to narrow large result sets.
:::

### Execution statuses

| Status      | Description                        |
| ----------- | ---------------------------------- |
| `NEW`       | Execution created, not yet started |
| `RUNNING`   | Execution is currently in progress |
| `SUCCEEDED` | Execution completed successfully   |
| `FAILED`    | Execution failed with errors       |
| `TIMED_OUT` | Execution exceeded its time limit  |
| `ABORTED`   | Execution was manually aborted     |

```bash
vamscli workflow list-executions -d my-db -a my-asset
vamscli workflow list-executions -d my-db -a my-asset -w workflow-123
vamscli workflow list-executions -d my-db -a my-asset --workflow-database-id global
vamscli workflow list-executions -d my-db -a my-asset --auto-paginate
vamscli workflow list-executions -d my-db -a my-asset --page-size 25
vamscli workflow list-executions -d my-db -a my-asset --starting-token "token123"
vamscli workflow list-executions -d my-db -a my-asset --json-output | jq '.Items[] | select(.executionStatus == "RUNNING")'
```

---

## workflow execute

Execute a workflow on an asset, optionally targeting a single file.

```bash
vamscli workflow execute [OPTIONS]
```

| Option                   | Type | Required | Description                              |
| ------------------------ | ---- | -------- | ---------------------------------------- |
| `-d`, `--database-id`    | TEXT | Yes      | Database ID containing the asset         |
| `-a`, `--asset-id`       | TEXT | Yes      | Asset ID to execute the workflow on      |
| `-w`, `--workflow-id`    | TEXT | Yes      | Workflow ID to execute                   |
| `--workflow-database-id` | TEXT | Yes      | Workflow's database ID                   |
| `--file-key`             | TEXT | No       | Specific file key to run the workflow on |
| `--json-output`          | Flag | No       | Output raw JSON response                 |

:::note
The workflow must be enabled and every pipeline it references must be accessible and enabled. Execution starts a Step Functions state machine and returns an execution ID. The command checks whether the workflow is already running on the specified file and blocks duplicate executions. Use `--file-key` to scope execution to a single file rather than the whole asset.
:::

```bash
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global --file-key "/models/building.gltf"
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global --json-output
```

The command returns the new execution ID. The JSON response carries it in the `message` field:

```json
{
    "message": "exec-xyz789"
}
```

---

## Execution lifecycle

A workflow execution progresses through a predictable sequence of states:

1. **Pre-execution checks** — the asset and workflow must exist and be accessible, every pipeline in the workflow must be enabled, the caller must have permissions, and no execution may already be running on the same file.
2. **Start** — an execution ID is generated, the execution is recorded, the Step Functions state machine starts, and the status is set to `NEW`.
3. **Progress** — the status moves to `RUNNING`, the start date is recorded, and each pipeline processes the asset in turn, passing metadata between stages.
4. **Completion** — the status moves to a final state (`SUCCEEDED`, `FAILED`, `TIMED_OUT`, or `ABORTED`), the stop date is recorded, and results are stored against the asset.

Use `workflow list-executions` to monitor status, start and stop times, and the input file processed.

---

## Monitor an execution

```bash
# Execute the workflow
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global

# Check execution status (poll until SUCCEEDED or FAILED)
vamscli workflow list-executions -d my-db -a my-asset -w workflow-123

# Show only running executions
vamscli workflow list-executions -d my-db -a my-asset --json-output | jq '.Items[] | select(.executionStatus == "RUNNING")'
```

:::tip
When executing many workflows in a loop, add a short delay between calls and poll execution status before starting new runs to avoid AWS Step Functions rate limiting.
:::

---

## Related Pages

-   [Asset Commands](assets.md)
-   [File Commands](files.md)
-   [Database Commands](database.md)
