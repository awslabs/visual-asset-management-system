---
sidebar_label: Workflows
title: Workflow Commands
---

# Workflow Commands

Manage and execute processing workflows on assets. Workflows are automated pipelines that process asset files through various stages.

---

## workflow list

List workflows in a database or all workflows across all databases.

```bash
vamscli workflow list [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-d`, `--database-id` | TEXT | No | Database ID to filter workflows |
| `--show-deleted` | Flag | No | Include deleted workflows |
| `--page-size` | INTEGER | No | Items per page |
| `--max-items` | INTEGER | No | Max items (with `--auto-paginate`) |
| `--starting-token` | TEXT | No | Pagination token |
| `--auto-paginate` | Flag | No | Fetch all items automatically |
| `--json-output` | Flag | No | Output raw JSON response |

```bash
vamscli workflow list
vamscli workflow list -d my-database
vamscli workflow list -d my-database --auto-paginate --json-output
```

---

## workflow list-executions

List workflow executions for a specific asset.

```bash
vamscli workflow list-executions [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-d`, `--database-id` | TEXT | Yes | Database ID containing the asset |
| `-a`, `--asset-id` | TEXT | Yes | Asset ID |
| `-w`, `--workflow-id` | TEXT | No | Filter by workflow ID |
| `--workflow-database-id` | TEXT | No | Workflow's database ID |
| `--page-size` | INTEGER | No | Items per page (max 50) |
| `--max-items` | INTEGER | No | Max items (with `--auto-paginate`) |
| `--starting-token` | TEXT | No | Pagination token |
| `--auto-paginate` | Flag | No | Fetch all items automatically |
| `--json-output` | Flag | No | Output raw JSON response |

:::warning[API Throttling]
Page size is limited to 50 items per page due to AWS Step Functions API throttling. Use `--auto-paginate` to fetch more items across multiple pages.
:::

### Execution statuses

| Status | Description |
|---|---|
| `NEW` | Execution created, not yet started |
| `RUNNING` | Currently in progress |
| `SUCCEEDED` | Completed successfully |
| `FAILED` | Failed with errors |
| `TIMED_OUT` | Exceeded time limit |
| `ABORTED` | Manually aborted |

```bash
vamscli workflow list-executions -d my-db -a my-asset
vamscli workflow list-executions -d my-db -a my-asset -w workflow-123
vamscli workflow list-executions -d my-db -a my-asset --auto-paginate
```

---

## workflow execute

Execute a workflow on an asset.

```bash
vamscli workflow execute [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-d`, `--database-id` | TEXT | Yes | Database ID containing the asset |
| `-a`, `--asset-id` | TEXT | Yes | Asset ID to execute on |
| `-w`, `--workflow-id` | TEXT | Yes | Workflow ID to execute |
| `--workflow-database-id` | TEXT | Yes | Workflow's database ID |
| `--file-key` | TEXT | No | Specific file key to run workflow on |
| `--json-output` | Flag | No | Output raw JSON response |

:::note
The command prevents duplicate executions. If the workflow is already running on the specified file, execution is blocked. All pipelines in the workflow must be enabled and accessible.
:::

```bash
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global --file-key "/models/building.gltf"
```

---

## Workflow Example

### Monitor execution

```bash
# Execute the workflow
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global

# Check execution status
vamscli workflow list-executions -d my-db -a my-asset -w workflow-123

# Get only running executions
vamscli workflow list-executions -d my-db -a my-asset --json-output | jq '.Items[] | select(.executionStatus == "RUNNING")'
```

## Related Pages

- [Asset Commands](assets.md)
- [File Commands](files.md)
- [Database Commands](database.md)
