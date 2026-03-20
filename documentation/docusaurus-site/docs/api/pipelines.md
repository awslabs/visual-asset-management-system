# Pipelines API

The Pipelines API allows you to create, retrieve, update, and delete processing pipelines. Pipelines define transformation or analysis steps that can be composed into [workflows](workflows.md) and executed against assets.

VAMS supports three pipeline execution types: **Lambda** (synchronous or asynchronous invocation of an AWS Lambda function), **SQS** (asynchronous message to an Amazon SQS queue), and **EventBridge** (asynchronous event to an Amazon EventBridge bus).

:::info[Authorization]
All pipeline endpoints require a valid JWT token in the `Authorization` header. Pipelines are subject to two-tier authorization: API-level access is checked first, followed by object-level Casbin policy enforcement on each pipeline resource.
:::


---

## List all pipelines

Retrieves all pipelines across all databases.

```
GET /pipelines
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                           |
|-----------------|--------|----------|---------|---------------------------------------|
| `maxItems`      | number | No       | `30000` | Maximum number of items to return     |
| `pageSize`      | number | No       | `3000`  | Number of items per page              |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |
| `showDeleted`   | string | No       | `false` | Include soft-deleted pipelines (`true`/`false`) |

### Response

```json
{
  "message": {
    "Items": [
      {
        "pipelineId": "my-conversion-pipeline",
        "databaseId": "my-database",
        "pipelineType": "standardFile",
        "pipelineExecutionType": "Lambda",
        "description": "Converts 3D files to glTF format",
        "assetType": ".fbx",
        "outputType": ".gltf",
        "waitForCallback": "Disabled",
        "taskTimeout": null,
        "taskHeartbeatTimeout": null,
        "lambdaName": "vams-myconversionpipelinea1b2c3d4",
        "sqsQueueUrl": null,
        "eventBridgeBusArn": null,
        "eventBridgeSource": null,
        "eventBridgeDetailType": null,
        "inputParameters": "",
        "enabled": true,
        "dateCreated": "\"March 15 2026 - 10:30:00\"",
        "dateUpdated": "\"March 15 2026 - 10:30:00\""
      }
    ],
    "NextToken": null
  }
}
```

### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `403`  | Not authorized                  |
| `500`  | Internal server error           |

---

## List pipelines for a database

Retrieves all pipelines associated with a specific database.

```
GET /database/{databaseId}/pipelines
```

### Path parameters

| Parameter    | Type   | Required | Description                                     |
|-------------|--------|----------|-------------------------------------------------|
| `databaseId` | string | Yes      | Database identifier (3-63 chars, alphanumeric, hyphens, underscores). Use `GLOBAL` for global pipelines. |

### Query parameters

| Parameter       | Type   | Required | Default | Description                           |
|-----------------|--------|----------|---------|---------------------------------------|
| `maxItems`      | number | No       | `30000` | Maximum number of items to return     |
| `pageSize`      | number | No       | `3000`  | Number of items per page              |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |
| `showDeleted`   | string | No       | `false` | Include soft-deleted pipelines        |

### Response

Same structure as [List all pipelines](#list-all-pipelines).

### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `400`  | Invalid `databaseId` format     |
| `403`  | Not authorized                  |
| `500`  | Internal server error           |

---

## Get a pipeline

Retrieves a single pipeline by its identifier.

```
GET /database/{databaseId}/pipelines/{pipelineId}
```

### Path parameters

| Parameter    | Type   | Required | Description              |
|-------------|--------|----------|--------------------------|
| `databaseId` | string | Yes      | Database identifier      |
| `pipelineId` | string | Yes      | Pipeline identifier (3-63 chars, alphanumeric, hyphens, underscores) |

### Response

```json
{
  "message": {
    "pipelineId": "my-conversion-pipeline",
    "databaseId": "my-database",
    "pipelineType": "standardFile",
    "pipelineExecutionType": "Lambda",
    "description": "Converts 3D files to glTF format",
    "assetType": ".fbx",
    "outputType": ".gltf",
    "waitForCallback": "Disabled",
    "taskTimeout": null,
    "taskHeartbeatTimeout": null,
    "lambdaName": "vams-myconversionpipelinea1b2c3d4",
    "sqsQueueUrl": null,
    "eventBridgeBusArn": null,
    "eventBridgeSource": null,
    "eventBridgeDetailType": null,
    "inputParameters": "",
    "enabled": true,
    "dateCreated": "\"March 15 2026 - 10:30:00\"",
    "dateUpdated": "\"March 15 2026 - 10:30:00\""
  }
}
```

### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `400`  | Invalid path parameters         |
| `403`  | Not authorized                  |
| `404`  | Pipeline not found              |
| `500`  | Internal server error           |

---

## Create or update a pipeline

Creates a new pipeline or updates an existing one. This endpoint uses `PUT` semantics -- if a pipeline with the given `pipelineId` and `databaseId` already exists, it is updated in place.

```
PUT /pipelines
```

:::warning[Immutable execution type]
The `pipelineExecutionType` cannot be changed after a pipeline is created. To change the execution type, delete the pipeline and create a new one.
:::


### Request body

| Field                       | Type    | Required | Description                                                                                       |
|-----------------------------|---------|----------|---------------------------------------------------------------------------------------------------|
| `pipelineId`                | string  | Yes      | Unique pipeline identifier (4-64 chars, alphanumeric, hyphens, underscores)                       |
| `databaseId`                | string  | Yes      | Database to associate with (or `GLOBAL` for cross-database pipelines)                             |
| `pipelineType`              | string  | Yes      | `standardFile` or `previewFile`                                                                   |
| `pipelineExecutionType`     | string  | Yes      | `Lambda`, `SQS`, or `EventBridge`                                                                 |
| `description`               | string  | Yes      | Pipeline description (4-256 chars)                                                                |
| `assetType`                 | string  | Yes      | Input file extension (e.g., `.fbx`)                                                               |
| `outputType`                | string  | Yes      | Output file extension (e.g., `.gltf`)                                                             |
| `waitForCallback`           | string  | No       | `Enabled` or `Disabled` (default). When enabled, Step Functions waits for a task token callback.  |
| `taskTimeout`               | string  | No       | Timeout in seconds for callback (max 604800 = 1 week). Required when `waitForCallback` is `Enabled`. |
| `taskHeartbeatTimeout`      | string  | No       | Heartbeat timeout in seconds. Must be smaller than `taskTimeout`.                                 |
| `lambdaName`                | string  | No       | Lambda function name. Required for `Lambda` execution type if providing your own function. If omitted, VAMS auto-creates a sample Lambda. |
| `sqsQueueUrl`               | string  | No       | SQS queue URL. **Required** when `pipelineExecutionType` is `SQS`.                               |
| `eventBridgeBusArn`         | string  | No       | EventBridge bus ARN. Optional for `EventBridge` type (defaults to the default bus).               |
| `eventBridgeSource`         | string  | No       | EventBridge event source string. Optional for `EventBridge` type.                                 |
| `eventBridgeDetailType`     | string  | No       | EventBridge detail type string. Optional for `EventBridge` type.                                  |
| `inputParameters`           | string  | No       | JSON string of additional parameters passed to the pipeline.                                      |
| `updateAssociatedWorkflows` | boolean | No       | When `true`, updates all workflows that reference this pipeline.                                  |
| `enabled`                   | boolean | No       | Whether the pipeline is enabled (default `true`).                                                 |

### Request body example (Lambda)

```json
{
  "pipelineId": "my-conversion-pipeline",
  "databaseId": "my-database",
  "pipelineType": "standardFile",
  "pipelineExecutionType": "Lambda",
  "description": "Converts FBX files to glTF format",
  "assetType": ".fbx",
  "outputType": ".gltf",
  "lambdaName": "my-custom-converter-lambda",
  "inputParameters": "{\"quality\": \"high\"}",
  "updateAssociatedWorkflows": false
}
```

### Request body example (SQS with callback)

```json
{
  "pipelineId": "my-sqs-pipeline",
  "databaseId": "GLOBAL",
  "pipelineType": "standardFile",
  "pipelineExecutionType": "SQS",
  "description": "Sends processing jobs to an external SQS queue",
  "assetType": ".e57",
  "outputType": ".las",
  "sqsQueueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/my-processing-queue",
  "waitForCallback": "Enabled",
  "taskTimeout": "3600",
  "taskHeartbeatTimeout": "300"
}
```

### Request body example (EventBridge)

```json
{
  "pipelineId": "my-eventbridge-pipeline",
  "databaseId": "GLOBAL",
  "pipelineType": "standardFile",
  "pipelineExecutionType": "EventBridge",
  "description": "Publishes processing events to EventBridge",
  "assetType": ".ifc",
  "outputType": ".gltf",
  "eventBridgeBusArn": "arn:aws:events:us-east-1:123456789012:event-bus/my-custom-bus",
  "eventBridgeSource": "com.mycompany.pipeline",
  "eventBridgeDetailType": "PipelineExecution",
  "waitForCallback": "Enabled",
  "taskTimeout": "7200"
}
```

### Response

```json
{
  "message": "Succeeded"
}
```

### Error responses

| Status | Description                                                                    |
|--------|--------------------------------------------------------------------------------|
| `400`  | Validation error (missing fields, invalid format, execution type change attempt) |
| `403`  | Not authorized                                                                 |
| `500`  | Internal server error                                                          |

---

## Delete a pipeline

Soft-deletes a pipeline. The pipeline record is moved to the `#deleted` namespace and can no longer be used in workflows.

```
DELETE /database/{databaseId}/pipelines/{pipelineId}
```

:::warning[Workflow dependency check]
A pipeline cannot be deleted if it is referenced by any active workflow. You must first remove the pipeline from all workflows before deleting it. The API returns a `400` error with a list of referencing workflows if this constraint is violated.
:::


### Path parameters

| Parameter    | Type   | Required | Description              |
|-------------|--------|----------|--------------------------|
| `databaseId` | string | Yes      | Database identifier      |
| `pipelineId` | string | Yes      | Pipeline identifier      |

### Response

```json
{
  "message": "Pipeline deleted"
}
```

### Error responses

| Status | Description                                                              |
|--------|--------------------------------------------------------------------------|
| `400`  | Pipeline is in use by one or more workflows                              |
| `403`  | Not authorized                                                           |
| `404`  | Pipeline not found                                                       |
| `500`  | Internal server error                                                    |

---

## Pipeline execution types

VAMS pipelines support three execution types, each suited for different integration patterns.

### Lambda

The default execution type. VAMS invokes an AWS Lambda function synchronously as a Step Functions task.

- If you provide a `lambdaName`, VAMS uses your existing Lambda function.
- If you omit `lambdaName`, VAMS auto-creates a sample Lambda function with a unique name.
- Auto-created Lambda functions are deleted when the pipeline is deleted.

### SQS

VAMS sends a message to an Amazon SQS queue. This is ideal for integrating with external processing systems that poll an SQS queue.

- Requires `sqsQueueUrl` in the pipeline definition.
- The SQS queue must be pre-created and accessible. VAMS does not create SQS queues.
- Supports `waitForCallback` for asynchronous processing with task token callback.

### EventBridge

VAMS publishes an event to an Amazon EventBridge bus. This is ideal for event-driven architectures and fan-out patterns.

- Optionally accepts `eventBridgeBusArn` (defaults to the account's default event bus).
- Optionally accepts `eventBridgeSource` and `eventBridgeDetailType` for event filtering.
- Supports `waitForCallback` for asynchronous processing with task token callback.

:::tip[Callback pattern]
When `waitForCallback` is set to `Enabled`, the Step Functions workflow pauses and waits for the external system to call back with a task token. The token is included in the pipeline payload. Set `taskTimeout` to define how long to wait before the task is considered failed.
:::


---

## Related resources

- [Workflows API](workflows.md) -- Compose pipelines into executable workflows
- [Assets API](assets.md) -- Manage the assets that pipelines process
