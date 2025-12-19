# Workflow Management Commands

This guide covers VamsCLI commands for managing and executing workflows in VAMS.

## Table of Contents

-   [Overview](#overview)
-   [Prerequisites](#prerequisites)
-   [Commands](#commands)
    -   [workflow list](#workflow-list)
    -   [workflow list-executions](#workflow-list-executions)
    -   [workflow execute](#workflow-execute)
-   [Common Workflows](#common-workflows)
-   [Troubleshooting](#troubleshooting)

## Overview

Workflows in VAMS are automated processing pipelines that can be executed on assets. They consist of one or more pipelines that process asset files through various stages. VamsCLI provides commands to:

-   List available workflows
-   View workflow execution history
-   Execute workflows on assets

## Prerequisites

Before using workflow commands, ensure you have:

1. **Completed Setup**: Run `vamscli setup <api-gateway-url>`
2. **Authenticated**: Run `vamscli auth login -u <username>`
3. **Appropriate Permissions**: Your user account must have permissions to view and execute workflows

Verify your setup:

```bash
vamscli auth status
```

## Commands

### workflow list

List workflows in a database or all workflows across all databases.

#### Syntax

```bash
vamscli workflow list [OPTIONS]
```

#### Options

| Option                   | Description                                                              |
| ------------------------ | ------------------------------------------------------------------------ |
| `-d, --database-id TEXT` | Database ID to filter workflows (optional)                               |
| `--show-deleted`         | Include deleted workflows                                                |
| `--page-size INTEGER`    | Number of items per page                                                 |
| `--max-items INTEGER`    | Maximum total items to fetch (only with --auto-paginate, default: 10000) |
| `--starting-token TEXT`  | Token for pagination (manual pagination)                                 |
| `--auto-paginate`        | Automatically fetch all items                                            |
| `--json-output`          | Output raw JSON response                                                 |

#### Examples

**List all workflows:**

```bash
vamscli workflow list
```

**List workflows for a specific database:**

```bash
vamscli workflow list -d my-database
```

**List workflows with auto-pagination:**

```bash
vamscli workflow list -d my-database --auto-paginate
```

**List workflows with custom pagination:**

```bash
# First page
vamscli workflow list -d my-database --page-size 100

# Next page using token from previous response
vamscli workflow list -d my-database --page-size 100 --starting-token "token-from-previous-response"
```

**Include deleted workflows:**

```bash
vamscli workflow list -d my-database --show-deleted
```

**JSON output for scripting:**

```bash
vamscli workflow list -d my-database --json-output
```

#### Output Format

**CLI Output:**

```
Found 2 workflow(s):
--------------------------------------------------------------------------------
ID: workflow-1
Database: global
Description: Image Processing Workflow
Auto-trigger Extensions: .jpg,.png
ARN: arn:aws:states:us-east-1:123456789012:stateMachine:ImageProcessing
--------------------------------------------------------------------------------
ID: workflow-2
Database: my-database
Description: 3D Model Conversion
Auto-trigger Extensions: .gltf,.glb
ARN: arn:aws:states:us-east-1:123456789012:stateMachine:ModelConversion
--------------------------------------------------------------------------------
```

**JSON Output:**

```json
{
  "Items": [
    {
      "workflowId": "workflow-1",
      "databaseId": "global",
      "description": "Image Processing Workflow",
      "autoTriggerOnFileExtensionsUpload": ".jpg,.png",
      "workflow_arn": "arn:aws:states:us-east-1:123456789012:stateMachine:ImageProcessing",
      "specifiedPipelines": {
        "functions": [...]
      }
    }
  ]
}
```

---

### workflow list-executions

List workflow executions for a specific asset.

#### Syntax

```bash
vamscli workflow list-executions [OPTIONS]
```

#### Options

| Option                        | Description                                                              |
| ----------------------------- | ------------------------------------------------------------------------ |
| `-d, --database-id TEXT`      | **[REQUIRED]** Database ID containing the asset                          |
| `-a, --asset-id TEXT`         | **[REQUIRED]** Asset ID to list executions for                           |
| `-w, --workflow-id TEXT`      | Filter by specific workflow ID                                           |
| `--workflow-database-id TEXT` | Workflow's database ID (for filtering)                                   |
| `--page-size INTEGER`         | Number of items per page (max 50 due to API throttling)                  |
| `--max-items INTEGER`         | Maximum total items to fetch (only with --auto-paginate, default: 10000) |
| `--starting-token TEXT`       | Token for pagination (manual pagination)                                 |
| `--auto-paginate`             | Automatically fetch all items                                            |
| `--json-output`               | Output raw JSON response                                                 |

#### Important Notes

⚠️ **API Throttling Limit**: Due to AWS Step Functions API throttling, the page size is limited to **50 items per page**. Use `--auto-paginate` to fetch more items across multiple pages.

#### Examples

**List all executions for an asset:**

```bash
vamscli workflow list-executions -d my-database -a my-asset
```

**Filter by specific workflow:**

```bash
vamscli workflow list-executions -d my-database -a my-asset -w workflow-123
```

**Filter by workflow database:**

```bash
vamscli workflow list-executions -d my-database -a my-asset --workflow-database-id global
```

**Auto-pagination to fetch all executions:**

```bash
vamscli workflow list-executions -d my-database -a my-asset --auto-paginate
```

**Custom page size (max 50):**

```bash
vamscli workflow list-executions -d my-database -a my-asset --page-size 25
```

**Manual pagination:**

```bash
# First page
vamscli workflow list-executions -d my-database -a my-asset --page-size 50

# Next page
vamscli workflow list-executions -d my-database -a my-asset --starting-token "token-from-previous"
```

**JSON output:**

```bash
vamscli workflow list-executions -d my-database -a my-asset --json-output
```

#### Output Format

**CLI Output:**

```
Found 3 execution(s):
--------------------------------------------------------------------------------
Execution ID: exec-abc123
Workflow ID: workflow-1
Workflow Database: global
Status: SUCCEEDED
Start Date: 12/18/2024, 10:00:00
Stop Date: 12/18/2024, 10:05:00
Input File: /model.gltf
--------------------------------------------------------------------------------
Execution ID: exec-def456
Workflow ID: workflow-1
Workflow Database: global
Status: RUNNING
Start Date: 12/18/2024, 11:00:00
--------------------------------------------------------------------------------
Execution ID: exec-ghi789
Workflow ID: workflow-2
Workflow Database: global
Status: FAILED
Start Date: 12/18/2024, 09:00:00
Stop Date: 12/18/2024, 09:02:00
Input File: /texture.png
--------------------------------------------------------------------------------
```

**JSON Output:**

```json
{
    "Items": [
        {
            "executionId": "exec-abc123",
            "workflowId": "workflow-1",
            "workflowDatabaseId": "global",
            "executionStatus": "SUCCEEDED",
            "startDate": "12/18/2024, 10:00:00",
            "stopDate": "12/18/2024, 10:05:00",
            "inputAssetFileKey": "/model.gltf"
        }
    ]
}
```

#### Execution Statuses

| Status      | Description                             |
| ----------- | --------------------------------------- |
| `NEW`       | Execution just created, not yet started |
| `RUNNING`   | Execution is currently in progress      |
| `SUCCEEDED` | Execution completed successfully        |
| `FAILED`    | Execution failed with errors            |
| `TIMED_OUT` | Execution exceeded time limit           |
| `ABORTED`   | Execution was manually aborted          |

---

### workflow execute

Execute a workflow on an asset.

#### Syntax

```bash
vamscli workflow execute [OPTIONS]
```

#### Options

| Option                        | Description                                     |
| ----------------------------- | ----------------------------------------------- |
| `-d, --database-id TEXT`      | **[REQUIRED]** Database ID containing the asset |
| `-a, --asset-id TEXT`         | **[REQUIRED]** Asset ID to execute workflow on  |
| `-w, --workflow-id TEXT`      | **[REQUIRED]** Workflow ID to execute           |
| `--workflow-database-id TEXT` | **[REQUIRED]** Workflow's database ID           |
| `--file-key TEXT`             | Specific file key to run workflow on (optional) |
| `--json-output`               | Output raw JSON response                        |

#### Important Notes

-   **Duplicate Prevention**: The command checks if the workflow is already running on the specified file and prevents duplicate executions
-   **Pipeline Validation**: All pipelines in the workflow must be enabled and accessible
-   **Permissions**: You must have POST permissions on both the asset and the workflow
-   **File-Specific Execution**: Use `--file-key` to run the workflow on a specific file within the asset

#### Examples

**Execute workflow on entire asset:**

```bash
vamscli workflow execute \
  -d my-database \
  -a my-asset \
  -w workflow-123 \
  --workflow-database-id global
```

**Execute workflow on specific file:**

```bash
vamscli workflow execute \
  -d my-database \
  -a my-asset \
  -w workflow-123 \
  --workflow-database-id global \
  --file-key "/models/building.gltf"
```

**Execute with JSON output:**

```bash
vamscli workflow execute \
  -d my-database \
  -a my-asset \
  -w workflow-123 \
  --workflow-database-id global \
  --json-output
```

#### Output Format

**CLI Output:**

```
✓ Workflow execution started successfully!
Execution ID: exec-xyz789
Workflow ID: workflow-123
Workflow Database: global
Asset ID: my-asset
Database ID: my-database
File Key: /models/building.gltf

The workflow has been started successfully.
Use 'vamscli workflow list-executions -d my-database -a my-asset' to check execution status.
```

**JSON Output:**

```json
{
    "message": "exec-xyz789"
}
```

---

## Common Workflows

### Monitor Workflow Execution

Execute a workflow and monitor its progress:

```bash
# 1. Execute the workflow
vamscli workflow execute \
  -d my-database \
  -a my-asset \
  -w workflow-123 \
  --workflow-database-id global

# 2. Check execution status
vamscli workflow list-executions \
  -d my-database \
  -a my-asset \
  -w workflow-123

# 3. Continue checking until status is SUCCEEDED or FAILED
```

### Find Workflows for a Database

```bash
# List all workflows in a database
vamscli workflow list -d my-database

# Find workflows with auto-trigger extensions
vamscli workflow list -d my-database --json-output | jq '.Items[] | select(.autoTriggerOnFileExtensionsUpload != "")'
```

### Batch Execute Workflows

Execute a workflow on multiple files:

```bash
# List files in asset
vamscli file list -d my-database -a my-asset --json-output > files.json

# Execute workflow on each file (using jq and bash)
cat files.json | jq -r '.items[] | select(.isFolder == false) | .relativePath' | while read file; do
  echo "Executing workflow on $file"
  vamscli workflow execute \
    -d my-database \
    -a my-asset \
    -w workflow-123 \
    --workflow-database-id global \
    --file-key "$file"
  sleep 2  # Avoid rate limiting
done
```

### Check Execution History

```bash
# Get all executions for an asset
vamscli workflow list-executions \
  -d my-database \
  -a my-asset \
  --auto-paginate

# Filter by workflow
vamscli workflow list-executions \
  -d my-database \
  -a my-asset \
  -w workflow-123 \
  --auto-paginate

# Get only running executions (using jq)
vamscli workflow list-executions \
  -d my-database \
  -a my-asset \
  --json-output | jq '.Items[] | select(.executionStatus == "RUNNING")'
```

---

## Troubleshooting

### Workflow Not Found

**Error:**

```
✗ Workflow Not Found: Workflow 'workflow-123' not found
```

**Solutions:**

1. Verify the workflow ID:

    ```bash
    vamscli workflow list -d <workflow-database-id>
    ```

2. Check if the workflow is deleted:

    ```bash
    vamscli workflow list -d <workflow-database-id> --show-deleted
    ```

3. Verify you have permissions to view the workflow

---

### Workflow Already Running

**Error:**

```
✗ Workflow Already Running: Workflow has a currently running execution on this file
```

**Solutions:**

1. Check current executions:

    ```bash
    vamscli workflow list-executions -d <database-id> -a <asset-id> -w <workflow-id>
    ```

2. Wait for the current execution to complete

3. If the execution is stuck, contact your administrator to check the Step Functions console

---

### Pipeline Not Enabled

**Error:**

```
✗ Workflow Execution Error: Pipeline not enabled: Pipeline 'test-pipeline' is disabled
```

**Solutions:**

1. Contact your administrator to enable the required pipeline
2. Verify pipeline status in the VAMS web interface
3. Check if you have permissions to execute the pipeline

---

### Asset Not Found

**Error:**

```
✗ Asset Not Found: Asset 'my-asset' not found in database 'my-database'
```

**Solutions:**

1. Verify the asset exists:

    ```bash
    vamscli assets get -d <database-id> <asset-id>
    ```

2. Check if the asset is archived:

    ```bash
    vamscli assets get -d <database-id> <asset-id> --show-archived
    ```

3. Verify you have permissions to view the asset

---

### Page Size Limit Exceeded

**Error:**

```
Maximum page size for workflow executions is 50 due to API throttling limits.
```

**Solution:**
Use auto-pagination to fetch more items:

```bash
vamscli workflow list-executions \
  -d <database-id> \
  -a <asset-id> \
  --auto-paginate
```

---

### Rate Limiting

If you encounter rate limiting errors when executing multiple workflows:

1. **Add delays between executions:**

    ```bash
    vamscli workflow execute ... && sleep 2
    ```

2. **Use smaller batch sizes**

3. **Monitor execution status** before starting new executions

---

## API Throttling Notes

### Step Functions API Limits

The `workflow list-executions` command is subject to AWS Step Functions API throttling:

-   **Maximum page size**: 50 items per page
-   **Reason**: Each execution requires a `describe_execution` API call to Step Functions
-   **Solution**: Use `--auto-paginate` to fetch more items across multiple pages

### Best Practices

1. **Use auto-pagination** for large result sets:

    ```bash
    vamscli workflow list-executions -d db -a asset --auto-paginate
    ```

2. **Filter by workflow** to reduce result set:

    ```bash
    vamscli workflow list-executions -d db -a asset -w workflow-123
    ```

3. **Use appropriate page sizes** based on your needs:

    ```bash
    # Smaller pages for faster initial response
    vamscli workflow list-executions -d db -a asset --page-size 10

    # Larger pages for efficiency (up to 50)
    vamscli workflow list-executions -d db -a asset --page-size 50
    ```

---

## Workflow Execution Lifecycle

### 1. Pre-Execution Checks

Before executing a workflow, the system validates:

-   Asset exists and is accessible
-   Workflow exists and is accessible
-   All pipelines in the workflow are enabled
-   No duplicate execution is running on the same file
-   User has appropriate permissions

### 2. Execution Start

When a workflow is executed:

-   A new execution ID is generated
-   The execution is recorded in the workflow execution table
-   Step Functions state machine is started
-   Initial status is set to "NEW"

### 3. Execution Progress

During execution:

-   Status updates to "RUNNING"
-   Start date is recorded
-   Each pipeline in the workflow processes the asset
-   Metadata is passed between pipeline stages

### 4. Execution Completion

When execution completes:

-   Status updates to "SUCCEEDED", "FAILED", or other final state
-   Stop date is recorded
-   Results are stored in the asset
-   Execution record is updated

### 5. Monitoring

Use `list-executions` to monitor:

-   Current execution status
-   Start and stop times
-   Input file that was processed
-   Execution history

---

## Related Commands

-   [`vamscli assets list`](asset-management.md#assets-list) - List assets that can have workflows executed
-   [`vamscli assets get`](asset-management.md#assets-get) - Get asset details before executing workflow
-   [`vamscli database list`](database-admin.md#database-list) - List databases containing workflows
-   [`vamscli file list`](file-operations.md#file-list) - List files in an asset for file-specific execution

---

## See Also

-   [Setup and Authentication](setup-auth.md)
-   [Asset Management](asset-management.md)
-   [Database Administration](database-admin.md)
-   [Global Options](global-options.md)
