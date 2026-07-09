# Workflows API

The Workflows API allows you to create, retrieve, and delete workflows that orchestrate one or more [pipelines](pipelines.md) as AWS Step Functions state machines. You can execute workflows against specific assets and track execution history.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Workflows are subject to two-tier Casbin authorization.
:::

---

## List all workflows

Retrieves all workflows across all databases.

```
GET /workflows
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                             |
| --------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`      | number | No       | `10000` | Maximum number of items to return       |
| `pageSize`      | number | No       | `10000` | Number of items per page                |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |
| `showDeleted`   | string | No       | `false` | Include soft-deleted workflows          |

### Response

```json
{
    "message": {
        "Items": [
            {
                "workflowId": "convert-and-preview",
                "databaseId": "my-database",
                "description": "Convert 3D files and generate preview thumbnails",
                "specifiedPipelines": {
                    "functions": [
                        {
                            "name": "3d-conversion-pipeline",
                            "databaseId": "GLOBAL",
                            "pipelineType": "standardFile",
                            "pipelineExecutionType": "Lambda",
                            "outputType": ".gltf",
                            "waitForCallback": "Disabled",
                            "userProvidedResource": "{\"resourceId\": \"vams-3dconversion\", \"resourceType\": \"Lambda\", \"isProvided\": false}"
                        }
                    ]
                },
                "workflow_arn": "arn:aws:states:us-east-1:123456789012:stateMachine:vams-convert-and-preview",
                "autoTriggerOnFileExtensionsUpload": ".fbx,.obj",
                "dateCreated": "\"March 15 2026 - 10:30:00\"",
                "dateModified": "\"March 16 2026 - 14:20:00\""
            }
        ],
        "NextToken": null
    }
}
```

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## List workflows for a database

Retrieves all workflows for a specific database.

```
GET /database/{databaseId}/workflows
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |

### Query parameters

Same as [List all workflows](#list-all-workflows).

### Response

Same structure as [List all workflows](#list-all-workflows).

---

## Get a workflow

Retrieves a single workflow by its identifier.

```
GET /database/{databaseId}/workflows/{workflowId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `workflowId` | string | Yes      | Workflow identifier |

### Response

Returns a single workflow object in the same format as the items in the list response.

### Error responses

| Status | Description             |
| ------ | ----------------------- |
| `400`  | Invalid path parameters |
| `403`  | Not authorized          |
| `404`  | Workflow not found      |
| `500`  | Internal server error   |

---

## Create or update a workflow

Creates a new workflow or updates an existing one. When updating, the underlying Step Functions state machine definition is updated in place, preserving execution history.

```
PUT /workflows
```

### Request body

| Field                               | Type   | Required | Description                                                                                                  |
| ----------------------------------- | ------ | -------- | ------------------------------------------------------------------------------------------------------------ |
| `workflowId`                        | string | Yes      | Unique workflow identifier (4-64 chars, alphanumeric, hyphens, underscores)                                  |
| `databaseId`                        | string | Yes      | Database to associate with (or `GLOBAL` for cross-database workflows)                                        |
| `description`                       | string | Yes      | Workflow description (4-256 chars)                                                                           |
| `specifiedPipelines`                | object | Yes      | Object containing a `functions` array of pipeline definitions                                                |
| `autoTriggerOnFileExtensionsUpload` | string | No       | Comma-delimited file extensions to auto-trigger on upload (e.g., `jpg,png,pdf`), or `all` for all extensions |

Each entry in `specifiedPipelines.functions` must include:

| Field                   | Type   | Required | Description                                          |
| ----------------------- | ------ | -------- | ---------------------------------------------------- |
| `name`                  | string | Yes      | Pipeline ID to reference                             |
| `databaseId`            | string | Yes      | Database ID of the pipeline                          |
| `pipelineType`          | string | Yes      | `standardFile` or `previewFile`                      |
| `outputType`            | string | Yes      | Output file extension                                |
| `pipelineExecutionType` | string | No       | `Lambda`, `SQS`, or `EventBridge` (default `Lambda`) |
| `waitForCallback`       | string | No       | `Enabled` or `Disabled` (default `Disabled`)         |
| `userProvidedResource`  | string | No       | JSON string of the pipeline resource config          |
| `taskTimeout`           | string | No       | Timeout in seconds (when callback is enabled)        |
| `taskHeartbeatTimeout`  | string | No       | Heartbeat timeout in seconds                         |
| `inputParameters`       | string | No       | JSON string of additional parameters                 |

:::note[Pipeline scoping rules]

-   **Global workflows** (`databaseId: "GLOBAL"`) can only reference global pipelines.
-   **Database-specific workflows** can reference global pipelines or pipelines from the same database.
    :::

### Request body example

```json
{
    "workflowId": "convert-and-preview",
    "databaseId": "my-database",
    "description": "Convert 3D files and generate preview thumbnails",
    "specifiedPipelines": {
        "functions": [
            {
                "name": "3d-conversion-pipeline",
                "databaseId": "GLOBAL",
                "pipelineType": "standardFile",
                "pipelineExecutionType": "Lambda",
                "outputType": ".gltf",
                "waitForCallback": "Disabled",
                "userProvidedResource": "{\"resourceId\": \"vams-3dconversion\", \"resourceType\": \"Lambda\", \"isProvided\": false}"
            }
        ]
    },
    "autoTriggerOnFileExtensionsUpload": ".fbx,.obj"
}
```

### Response

```json
{
    "message": "Succeeded"
}
```

### Error responses

| Status | Description                                                    |
| ------ | -------------------------------------------------------------- |
| `400`  | Validation error (missing fields, invalid pipeline references) |
| `403`  | Not authorized (API, workflow, or pipeline level)              |
| `500`  | Internal server error                                          |

---

## Delete a workflow

Soft-deletes a workflow and deletes the underlying Step Functions state machine.

```
DELETE /database/{databaseId}/workflows/{workflowId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `workflowId` | string | Yes      | Workflow identifier |

### Response

```json
{
    "message": "Workflow deleted"
}
```

### Error responses

| Status | Description             |
| ------ | ----------------------- |
| `400`  | Invalid path parameters |
| `403`  | Not authorized          |
| `404`  | Workflow not found      |
| `500`  | Internal server error   |

---

## Execute a workflow

Executes a workflow against a specific asset. This starts a new Step Functions execution.

```
POST /database/{databaseId}/assets/{assetId}/workflows/{workflowId}
```

### Path parameters

| Parameter    | Type   | Required | Description                      |
| ------------ | ------ | -------- | -------------------------------- |
| `databaseId` | string | Yes      | Database identifier of the asset |
| `assetId`    | string | Yes      | Asset identifier                 |
| `workflowId` | string | Yes      | Workflow identifier              |

### Request body

| Field                            | Type   | Required | Description                                                                                                                                                                              |
| -------------------------------- | ------ | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `workflowDatabaseId`             | string | Yes      | Database ID of the workflow (use `GLOBAL` for global workflows)                                                                                                                         |
| `fileKey`                        | string | No       | Specific file path within the asset to process. If omitted, uses the asset's base prefix.                                                                                              |
| `pipelineInputParameters`        | object | No       | Per-pipeline `inputParameters` override for this run, keyed by pipeline name. Each value is a JSON string. A pipeline not listed keeps its stored `inputParameters`.                     |
| `fileBaseExecutionPathExtension` | string | No       | Asset-relative path segment (leading `/`) inserted between the output asset's location key and each output file's relative path. Defaults to `/` (no extra segment) when omitted. |

### Request body example

```json
{
    "workflowDatabaseId": "GLOBAL",
    "fileKey": "models/building.fbx",
    "fileBaseExecutionPathExtension": "/exec-2026/"
}
```

:::note[Execution constraints]

-   A workflow cannot be executed on a file that already has a running execution of the same workflow.
-   The workflow's `workflowDatabaseId` must be `GLOBAL` or match the asset's `databaseId`.
-   All pipelines in the workflow must be enabled and accessible to the user.
    :::

### Response

```json
{
    "message": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

The response body contains the Step Functions execution ID.

### Error responses

| Status | Description                                                       |
| ------ | ----------------------------------------------------------------- |
| `400`  | Validation error, pipeline disabled, or execution already running |
| `403`  | Not authorized (API, asset, workflow, or pipeline level)          |
| `404`  | Asset or workflow not found                                       |
| `429`  | Throttling -- too many requests                                   |
| `500`  | Internal server error or execution limit exceeded                 |

---

## List workflow executions

Retrieves execution history for workflows on a specific asset.

```
GET /database/{databaseId}/assets/{assetId}/workflows/executions
```

To filter by a specific workflow:

```
GET /database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}
```

### Path parameters

| Parameter    | Type   | Required | Description           |
| ------------ | ------ | -------- | --------------------- |
| `databaseId` | string | Yes      | Database identifier   |
| `assetId`    | string | Yes      | Asset identifier      |
| `workflowId` | string | No       | Filter by workflow ID |

### Query parameters

| Parameter         | Type   | Required | Default            | Description                                                                                                                  |
| ----------------- | ------ | -------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `filterStartDate` | string | No       | 90 days before now | ISO-8601 lower bound on execution start date; only executions started on or after this date are listed. Defaults to 90 days ago. |

### Response

The applied lower bound is echoed back as `filterStartDate`.

```json
{
    "message": {
        "Items": [
            {
                "workflowDatabaseId": "GLOBAL",
                "workflowId": "convert-and-preview",
                "executionId": "a1b2c3d4-e5f6-7890",
                "executionStatus": "SUCCEEDED",
                "startDate": "03/15/2026, 10:30:00",
                "stopDate": "03/15/2026, 10:32:15",
                "inputAssetFileKey": "models/building.fbx"
            }
        ],
        "filterStartDate": "2025-12-15T10:30:00Z"
    }
}
```

:::note
All executions are returned, both completed and running. Completed executions use the stored `startDate`, `stopDate`, and `executionStatus`; executions without a stored stop date are refreshed from AWS Step Functions, and once found to have stopped their status and dates are persisted.
:::

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Abort a workflow execution

Aborts a running workflow execution. Any still-running inner pipeline executions are stopped first, then the outer Step Functions execution is stopped. The execution's individual pipeline records that had not yet finished are marked `ABORTED`, and the overall execution status is set to `ABORTED`.

```
DELETE /workflows/executions/{executionId}
```

The route is keyed on the execution identifier because an execution may span input files across multiple assets.

### Path parameters

| Parameter     | Type   | Required | Description                       |
| ------------- | ------ | -------- | --------------------------------- |
| `executionId` | string | Yes      | Identifier of the execution to abort |

### Response

```json
{
    "message": "Execution aborted"
}
```

:::note[Authorization]
Aborting an execution requires `GET` permission on the execution's workflow and `POST` permission on every input-file asset tied to the execution. Because the execution does not modify the workflow definition, only read access to the workflow is required; because it affects the processed assets, write (`POST`) access to those assets is required.
:::

### Error responses

| Status | Description                                                       |
| ------ | ----------------------------------------------------------------- |
| `400`  | Invalid or missing `executionId`                                  |
| `403`  | Not authorized (API, workflow, or one of the input-file assets)   |
| `404`  | Execution not found                                               |
| `429`  | Throttling -- too many requests                                   |
| `500`  | Internal server error                                             |

---

## Get execution details

Returns the full detail and input/output traceability for a single execution, including the underlying pipelines (with status and timing), input files, input metadata, input configurations, and a listing of all outputs (files, metadata, and results). Pipeline names and descriptions are resolved from the pipeline definitions, and the workflow description from the workflow definition.

```
GET /workflows/executions/{executionId}/details
```

The route is keyed on the execution identifier because an execution may span input files across multiple assets.

### Path parameters

| Parameter     | Type   | Required | Description                  |
| ------------- | ------ | -------- | ---------------------------- |
| `executionId` | string | Yes      | Execution identifier         |

### Response

```json
{
    "message": {
        "executionId": "a1b2c3d4e5f6",
        "workflowId": "convert-and-preview",
        "workflowDatabaseId": "GLOBAL",
        "workflowDescription": "Convert 3D files and generate preview thumbnails",
        "executionStatus": "SUCCEEDED",
        "executionStartDate": "2026-06-16T00:00:00Z",
        "executionStopDate": "2026-06-16T00:05:00Z",
        "triggerType": "Manual",
        "triggeredByUserId": "user@example.com",
        "executionError": "",
        "pipelines": [
            {
                "pipelineId": "3d-conversion-pipeline",
                "pipelineDatabaseId": "GLOBAL",
                "name": "3d-conversion-pipeline",
                "description": "Converts 3D files to glTF",
                "pipelineType": "standardFile",
                "pipelineExecutionType": "Lambda",
                "endStatePipeline": true,
                "executionStatus": "SUCCEEDED",
                "executionStartDate": "2026-06-16T00:00:05Z",
                "executionStopDate": "2026-06-16T00:04:50Z"
            }
        ],
        "inputFiles": [
            { "databaseId": "my-database", "assetId": "a1b2c3", "inputAssetFileKey": "/models/building.fbx" }
        ],
        "inputMetadata": [
            { "databaseId": "my-database", "assetId": "a1b2c3", "filePath": "/", "metadata": { "site": "north" } }
        ],
        "inputConfigurations": [
            { "pipelineId": "3d-conversion-pipeline", "inputConfiguration": "", "inputConfigurationTruncated": false }
        ],
        "outputs": {
            "files": [
                { "relativeFilePath": "/models/building.gltf", "fileType": "file", "fileSize": 20480, "contentType": "model/gltf-binary", "assetId": "building-001", "databaseId": "default", "assetFileVersionId": "PvT3.K9mZ0xq1aBcd2EfGhI" }
            ],
            "metadata": [],
            "results": [
                { "relativeFilePath": "/models/building.report.json", "resultsContent": "{\"triangles\": 18204, \"status\": \"ok\"}", "resultsContentTruncated": false }
            ]
        }
    }
}
```

:::note[Running executions]
The details endpoint works for both running and completed executions. While an execution is still running, not all fields are populated yet — pipelines that have not started have empty status and timing, and outputs appear as each pipeline completes.
:::

:::note[Traceability, not internals]
The response is scoped to input/output traceability. Internal details — Step Functions and resource ARNs, temporary and auxiliary S3 input/output locations, and credential-vending fields — are intentionally omitted. Output file size and content type are included when still available; a lifecycle policy may expire temporary output files, in which case only the relative path and type are returned.

For executions whose output target is an asset, each output file carries the target asset identity — `assetId` and `databaseId` — derived from the execution's output target. When a matching file version-history record exists, `assetFileVersionId` is also added, identifying the specific S3 file version the execution wrote. `assetFileVersionId` is absent for outputs with no history record (for example, executions that ran before file version history was recorded).

`results` lists structured result files a pipeline emits to the execution's `results/` output folder (as opposed to asset files). Each entry carries the file's path relative to that folder (`relativeFilePath`), the file content (`resultsContent`), and `resultsContentTruncated`, which is `true` when the stored content was truncated to fit the field limit.
:::

### Error responses

| Status | Description                                                       |
| ------ | ----------------------------------------------------------------- |
| `400`  | Invalid or missing `executionId`                                  |
| `403`  | Not authorized (API, workflow, or one of the input-file assets)   |
| `404`  | Execution not found                                               |
| `500`  | Internal server error                                             |

---

## Get execution logs

Returns logs for an execution in one of two modes. Logs are always scoped to the requested execution; supplying a `pipelineExecutionId` narrows the result to that single pipeline execution.

```
GET /workflows/executions/{executionId}/logs
```

### Path parameters

| Parameter     | Type   | Required | Description          |
| ------------- | ------ | -------- | -------------------- |
| `executionId` | string | Yes      | Execution identifier |

### Query parameters

| Parameter             | Type   | Required | Default     | Description                                                                                       |
| --------------------- | ------ | -------- | ----------- | ------------------------------------------------------------------------------------------------- |
| `mode`                | string | No       | `truncated` | `truncated` returns the stored log text; `full` runs a live Amazon CloudWatch Logs search          |
| `pipelineExecutionId` | string | No       | —           | Narrow the logs to a single pipeline execution of this execution                                  |
| `filterPattern`       | string | No       | —           | (`full` mode) Additional CloudWatch Logs filter pattern, AND-ed with the execution/pipeline scope |
| `startTime`           | number | No       | —           | (`full` mode) Start of the time range, epoch milliseconds                                          |
| `endTime`             | number | No       | —           | (`full` mode) End of the time range, epoch milliseconds                                            |
| `limit`               | number | No       | `100`       | (`full` mode) Maximum number of events to return                                                  |
| `nextToken`           | string | No       | —           | (`full` mode) Pagination token from a previous response                                           |

### Response (truncated mode)

```json
{
    "message": {
        "mode": "truncated",
        "executionLog": "...stored execution log text...",
        "executionError": ""
    }
}
```

When `pipelineExecutionId` is supplied in truncated mode, the stored per-pipeline log is returned instead:

```json
{
    "message": {
        "mode": "truncated",
        "pipelineExecutionId": "p1a2b3",
        "resultLog": "...",
        "errorLog": ""
    }
}
```

### Response (full mode)

```json
{
    "message": {
        "mode": "full",
        "pipelineExecutionId": "",
        "events": [
            { "timestamp": 1718496000000, "message": "..." }
        ],
        "nextToken": null
    }
}
```

:::note[Scope]
A full-mode search is always restricted to the requested execution within the shared workflow log group. When `pipelineExecutionId` is supplied, the search is further restricted to that single pipeline execution — logs from other pipelines or executions are never returned.
:::

### Error responses

| Status | Description                                                       |
| ------ | ----------------------------------------------------------------- |
| `400`  | Invalid or missing `executionId`, or invalid `mode`               |
| `403`  | Not authorized (API, workflow, or one of the input-file assets)   |
| `404`  | Execution (or specified pipeline execution) not found             |
| `500`  | Internal server error                                             |

---

## Related resources

-   [Pipelines API](pipelines.md) -- Define the individual pipeline steps used in workflows
-   [Assets API](assets.md) -- Manage the assets that workflows process
-   [Asset Versions API](asset-versions.md) -- Manage version snapshots of processed assets
-   [Subscriptions API](subscriptions.md) -- Subscribe to asset version change notifications
