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
| `maxItems`      | number | No       | `30000` | Maximum number of items to return       |
| `pageSize`      | number | No       | `3000`  | Number of items per page                |
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

| Field                   | Type   | Required | Description                                   |
| ----------------------- | ------ | -------- | --------------------------------------------- |
| `name`                  | string | Yes      | Pipeline ID to reference                      |
| `databaseId`            | string | Yes      | Database ID of the pipeline                   |
| `pipelineType`          | string | Yes      | `standardFile` or `previewFile`               |
| `pipelineExecutionType` | string | Yes      | `Lambda`, `SQS`, or `EventBridge`             |
| `outputType`            | string | Yes      | Output file extension                         |
| `waitForCallback`       | string | Yes      | `Enabled` or `Disabled`                       |
| `userProvidedResource`  | string | Yes      | JSON string of the pipeline resource config   |
| `taskTimeout`           | string | No       | Timeout in seconds (when callback is enabled) |
| `taskHeartbeatTimeout`  | string | No       | Heartbeat timeout in seconds                  |
| `inputParameters`       | string | No       | JSON string of additional parameters          |

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

| Field                | Type   | Required | Description                                                                               |
| -------------------- | ------ | -------- | ----------------------------------------------------------------------------------------- |
| `workflowDatabaseId` | string | Yes      | Database ID of the workflow (use `GLOBAL` for global workflows)                           |
| `fileKey`            | string | No       | Specific file path within the asset to process. If omitted, uses the asset's base prefix. |

### Request body example

```json
{
    "workflowDatabaseId": "GLOBAL",
    "fileKey": "models/building.fbx"
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

| Status | Description                                                                                 |
| ------ | ------------------------------------------------------------------------------------------- |
| `400`  | Validation error, asset/workflow not found, pipeline disabled, or execution already running |
| `403`  | Not authorized (API, asset, workflow, or pipeline level)                                    |
| `429`  | Throttling -- too many requests                                                             |
| `500`  | Internal server error or execution limit exceeded                                           |

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

### Response

```json
{
    "message": {
        "Items": [
            {
                "workflowDatabaseId": "GLOBAL",
                "workflowId": "convert-and-preview",
                "executionId": "a1b2c3d4-e5f6-7890",
                "executionStatus": "RUNNING",
                "startDate": "03/15/2026, 10:30:00"
            }
        ]
    }
}
```

:::note
Only currently running executions (without a stop date) are returned. Completed executions are not included.
:::

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Related resources

-   [Pipelines API](pipelines.md) -- Define the individual pipeline steps used in workflows
-   [Assets API](assets.md) -- Manage the assets that workflows process
-   [Asset Versions API](asset-versions.md) -- Manage version snapshots of processed assets
-   [Subscriptions API](subscriptions.md) -- Subscribe to asset version change notifications
