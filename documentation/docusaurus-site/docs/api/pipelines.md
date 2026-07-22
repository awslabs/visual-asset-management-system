# Pipelines API

The Pipelines API allows you to create, retrieve, update, and delete processing pipelines. Pipelines define transformation or analysis steps that can be composed into [workflows](workflows.md) and executed against assets.

VAMS supports these pipeline execution types: **Lambda** (synchronous or asynchronous invocation of an AWS Lambda function), **SQS** (asynchronous message to an Amazon SQS queue), **EventBridge** (asynchronous event to an Amazon EventBridge bus), and **DeadlineCloud** (asynchronous submission to AWS Deadline Cloud, with a mandatory task token callback).

Pipelines are scoped to a database. A pipeline can carry one or more **templates** — reusable configuration bodies (for example JSON, YAML, OpenJD, or XML) that supply the parameters an execution passes to the pipeline. Each template can define a **tag schema** describing the typed tags that resolve `{{tagName}}` placeholders in the template body.

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

| Parameter       | Type   | Required | Default | Description                                     |
| --------------- | ------ | -------- | ------- | ----------------------------------------------- |
| `maxItems`      | number | No       | `10000` | Maximum number of items to return               |
| `pageSize`      | number | No       | `10000` | Number of items per page                        |
| `startingToken` | string | No       | `null`  | Pagination token from previous response         |
| `showDeleted`   | string | No       | `false` | Include soft-deleted pipelines (`true`/`false`) |

### Response

```json
{
    "message": {
        "Items": [
            {
                "databaseId": "my-database",
                "pipelineId": "my-conversion-pipeline",
                "pipelineName": "Convert to glTF",
                "category": "conversion",
                "description": "Converts 3D files to glTF format",
                "executionConfig": {
                    "executionType": "Lambda",
                    "waitForCallback": "Disabled",
                    "lambda": { "resourceId": "vams-myconversionpipelinea1b2c3d4" }
                },
                "systemConfig": {
                    "inputFileArity": "one",
                    "requireTemplate": false,
                    "allowCustomTemplateOverride": true
                },
                "enabled": true,
                "archived": false,
                "dateCreated": "2026-03-15T10:30:00Z",
                "dateModified": "2026-03-15T10:30:00Z"
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

## List pipelines for a database

Retrieves all pipelines associated with a specific database.

```
GET /database/{databaseId}/pipelines
```

### Path parameters

| Parameter    | Type   | Required | Description                                                                                              |
| ------------ | ------ | -------- | -------------------------------------------------------------------------------------------------------- |
| `databaseId` | string | Yes      | Database identifier (3-63 chars, alphanumeric, hyphens, underscores). Use `GLOBAL` for global pipelines. |

### Query parameters

| Parameter         | Type   | Required | Default | Description                             |
| ----------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`        | number | No       | `10000` | Maximum number of items to return       |
| `pageSize`        | number | No       | `10000` | Number of items per page                |
| `startingToken`   | string | No       | `null`  | Pagination token from previous response |
| `showDeleted`     | string | No       | `false` | Include soft-deleted pipelines          |
| `includeArchived` | string | No       | `false` | Include archived pipelines              |

:::note[Archived pipelines]
Archived pipelines are hidden by default. Set `includeArchived=true` to include pipelines whose `archived` flag is set.
:::

### Response

Same structure as [List all pipelines](#list-all-pipelines).

### Error responses

| Status | Description                 |
| ------ | --------------------------- |
| `400`  | Invalid `databaseId` format |
| `403`  | Not authorized              |
| `500`  | Internal server error       |

---

## Get a pipeline

Retrieves a single pipeline by its identifier.

```
GET /database/{databaseId}/pipelines/{pipelineId}
```

### Path parameters

| Parameter    | Type   | Required | Description                                                          |
| ------------ | ------ | -------- | -------------------------------------------------------------------- |
| `databaseId` | string | Yes      | Database identifier                                                  |
| `pipelineId` | string | Yes      | Pipeline identifier (3-63 chars, alphanumeric, hyphens, underscores) |

### Query parameters

| Parameter         | Type   | Required | Default | Description                                                    |
| ----------------- | ------ | -------- | ------- | -------------------------------------------------------------- |
| `includeArchived` | string | No       | `false` | Return the pipeline even when it is archived (`true`/`false`). |

:::note[Archived pipelines]
Archived pipelines are hidden by default. Set `includeArchived=true` to retrieve a pipeline whose `archived` flag is set.
:::

The response includes the pipeline's `executionConfig` and `systemConfig`, the optional `category` label, the `archived` flag, and a `templates` array of lightweight descriptors for the templates that belong to the pipeline.

### Response

```json
{
    "message": {
        "databaseId": "my-database",
        "pipelineId": "my-conversion-pipeline",
        "pipelineName": "Convert to glTF",
        "category": "conversion",
        "description": "Converts 3D files to glTF format",
        "executionConfig": {
            "executionType": "Lambda",
            "waitForCallback": "Disabled",
            "lambda": {
                "resourceId": "vams-myconversionpipelinea1b2c3d4"
            }
        },
        "systemConfig": {
            "inputFileArity": "one",
            "assetScope": {
                "crossAssetAllowed": false,
                "singleAssetOnly": true,
                "wholeAssetAllowed": false,
                "folderAllowed": false
            },
            "metadataInputs": {
                "assetMetadata": true,
                "fileMetadata": false,
                "fileAttributes": false
            },
            "requireTemplate": false,
            "allowCustomTemplateOverride": true,
            "inputFileFilters": {
                "allow": ["*.fbx"],
                "exclude": []
            }
        },
        "enabled": true,
        "archived": false,
        "templates": [
            {
                "templateId": "high-quality",
                "templateName": "High quality",
                "description": "High-fidelity conversion preset",
                "configFormat": "json"
            }
        ]
    }
}
```

### Error responses

| Status | Description             |
| ------ | ----------------------- |
| `400`  | Invalid path parameters |
| `403`  | Not authorized          |
| `404`  | Pipeline not found      |
| `500`  | Internal server error   |

---

## Create a pipeline

Creates a pipeline in the specified database. The pipeline is identified by the `databaseId` path parameter and the `pipelineId` supplied in the body (or a generated one if omitted).

```
POST /database/{databaseId}/pipelines
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |

### Request body

| Field             | Type    | Required | Description                                                                                |
| ----------------- | ------- | -------- | ------------------------------------------------------------------------------------------ |
| `pipelineId`      | string  | No       | Pipeline identifier (GUID). Generated when omitted.                                        |
| `pipelineName`    | string  | Yes      | Human-readable pipeline name.                                                              |
| `category`        | string  | No       | Optional grouping label.                                                                   |
| `description`     | string  | No       | Pipeline description.                                                                      |
| `executionConfig` | object  | Yes      | Execution binding. See [Execution configuration](#execution-configuration).                |
| `systemConfig`    | object  | No       | Input handling and templating defaults. See [System configuration](#system-configuration). |
| `enabled`         | boolean | No       | Whether the pipeline is enabled (default `true`).                                          |

### Request body example

```json
{
    "pipelineName": "Convert to glTF",
    "category": "conversion",
    "description": "Converts FBX files to glTF format",
    "executionConfig": {
        "executionType": "Lambda",
        "lambda": {
            "resourceId": "my-custom-converter-lambda"
        }
    },
    "systemConfig": {
        "inputFileArity": "one",
        "requireTemplate": false,
        "allowCustomTemplateOverride": true,
        "inputFileFilters": {
            "allow": ["*.fbx"],
            "exclude": []
        }
    },
    "enabled": true
}
```

### Response

Returns the created pipeline, in the same shape as [Get a pipeline](#get-a-pipeline).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Validation error      |
| `403`  | Not authorized        |
| `404`  | Database not found    |
| `500`  | Internal server error |

---

## Update a pipeline

Updates a pipeline. Supply any subset of the mutable fields; omitted fields are left unchanged.

```
PUT /database/{databaseId}/pipelines/{pipelineId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |

### Request body

| Field             | Type    | Required | Description                                                                                |
| ----------------- | ------- | -------- | ------------------------------------------------------------------------------------------ |
| `pipelineName`    | string  | No       | Human-readable pipeline name.                                                              |
| `category`        | string  | No       | Grouping label.                                                                            |
| `description`     | string  | No       | Pipeline description.                                                                      |
| `executionConfig` | object  | No       | Execution binding. See [Execution configuration](#execution-configuration).                |
| `systemConfig`    | object  | No       | Input handling and templating defaults. See [System configuration](#system-configuration). |
| `enabled`         | boolean | No       | Whether the pipeline is enabled.                                                           |

:::tip[Enable or disable a pipeline]
Set `enabled` to `true` or `false` to enable or disable a pipeline without changing any other field.
:::

### Request body example

```json
{
    "description": "Converts FBX files to glTF format (v2 preset)",
    "enabled": false
}
```

### Response

Returns the updated pipeline, in the same shape as [Get a pipeline](#get-a-pipeline).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Validation error      |
| `403`  | Not authorized        |
| `404`  | Pipeline not found    |
| `500`  | Internal server error |

---

## Delete a pipeline

Archives a pipeline. The delete is a soft-delete that sets the pipeline's `archived` flag to `true`; the record is retained but hidden from listings and lookups unless `includeArchived=true` is supplied.

```
DELETE /database/{databaseId}/pipelines/{pipelineId}
```

:::warning[Workflow dependency check]
A pipeline cannot be deleted if it is referenced by any active workflow. You must first remove the pipeline from all workflows before deleting it. The API returns a `400` error with a list of referencing workflows if this constraint is violated.
:::

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |

### Response

```json
{
    "message": "Pipeline deleted"
}
```

### Error responses

| Status | Description                                 |
| ------ | ------------------------------------------- |
| `400`  | Pipeline is in use by one or more workflows |
| `403`  | Not authorized                              |
| `404`  | Pipeline not found                          |
| `500`  | Internal server error                       |

---

## Templates

Templates are reusable configuration bodies attached to a pipeline. A template holds a `configBody` (the template text, which may contain `{{tagName}}` placeholders) and an optional `webFormJson` (opaque web form markup used to render an input form). Clients always send `configBody` and `webFormJson` inline; VAMS transparently offloads large bodies to Amazon S3 and rehydrates them on read.

### List templates

Retrieves the templates that belong to a pipeline.

```
GET /database/{databaseId}/pipelines/{pipelineId}/templates
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |

#### Query parameters

| Parameter       | Type   | Required | Default | Description                             |
| --------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`      | number | No       | `10000` | Maximum number of items to return       |
| `pageSize`      | number | No       | `10000` | Number of items per page                |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |

#### Response

```json
{
    "templates": [
        {
            "templateId": "high-quality",
            "templateName": "High quality",
            "description": "High-fidelity conversion preset",
            "configFormat": "json"
        }
    ]
}
```

#### Error responses

| Status | Description                    |
| ------ | ------------------------------ |
| `403`  | Not authorized                 |
| `404`  | Database or pipeline not found |
| `500`  | Internal server error          |

### Create a template

Creates a template on a pipeline.

```
POST /database/{databaseId}/pipelines/{pipelineId}/templates
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |

#### Request body

| Field               | Type    | Required | Description                                                                                                                                                                                   |
| ------------------- | ------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `templateId`        | string  | No       | Template identifier (GUID). Generated when omitted.                                                                                                                                           |
| `templateName`      | string  | Yes      | Human-readable template name.                                                                                                                                                                 |
| `description`       | string  | No       | Template description.                                                                                                                                                                         |
| `configFormat`      | string  | No       | Format of `configBody`: `json` (default), `yaml`, `openjd`, `xml`, or `raw`.                                                                                                                  |
| `configBody`        | string  | No       | The template text. May contain `{{tagName}}` placeholders resolved from tags at execution time. When `configFormat` is `json`, it must be valid JSON (validated at save).                     |
| `webFormJson`       | string  | No       | Serialized web-form definition used to render the template's input form. When present, must be valid JSON (validated at save).                                                                |
| `allowCustomEdit`   | boolean | No       | Whether the template config may be edited inline at execution time.                                                                                                                           |
| `isDefault`         | boolean | No       | Marks this template as the pipeline's default. At most one template per pipeline is the default; setting it clears the flag on any other template. See [Default template](#default-template). |
| `inputInstructions` | string  | No       | Guidance shown to the user when supplying template inputs.                                                                                                                                    |
| `overrides`         | object  | No       | Per-template overrides of the pipeline's `systemConfig`. See [Template overrides](#template-overrides).                                                                                       |
| `tagSchema`         | array   | No       | Tag field definitions for the template. See [Tag schema fields](#tag-schema-fields).                                                                                                          |

#### Request body example

```json
{
    "templateName": "High quality",
    "description": "High-fidelity conversion preset",
    "configFormat": "json",
    "configBody": "{\"quality\": \"{{quality}}\", \"draco\": true}",
    "allowCustomEdit": false,
    "inputInstructions": "Choose the conversion quality preset.",
    "tagSchema": [
        {
            "tagKey": "quality",
            "type": "enum",
            "required": true,
            "default": "high",
            "label": "Quality",
            "enumValues": ["low", "medium", "high"]
        }
    ]
}
```

#### Response

Returns the created template with `configBody` and `webFormJson` inline.

#### Error responses

| Status | Description                    |
| ------ | ------------------------------ |
| `400`  | Validation error               |
| `403`  | Not authorized                 |
| `404`  | Database or pipeline not found |
| `500`  | Internal server error          |

#### Template overrides

`overrides` is an object that overrides the parent pipeline's `systemConfig` for executions that use this template. It may contain only these four keys (any other key is rejected at save); each is validated at save time and each is optional — an omitted key inherits the pipeline's value:

| Key                | Type   | Value                                                                                                                                                                |
| ------------------ | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileArity`   | string | `none`, `one`, or `multi`.                                                                                                                                           |
| `assetScope`       | object | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, `folderAllowed`.                                                                               |
| `metadataInputs`   | object | Booleans `assetMetadata`, `fileMetadata`, `fileAttributes`.                                                                                                          |
| `inputFileFilters` | object | `allow` and `exclude` arrays of strings. Each entry is an extension (`*.glb`), exact path, file name, or wildcard (`*.previewFile.*`); matching is case-insensitive. |

```json
{
    "overrides": {
        "inputFileArity": "multi",
        "assetScope": { "crossAssetAllowed": true, "singleAssetOnly": false },
        "metadataInputs": { "assetMetadata": true, "fileMetadata": false, "fileAttributes": false },
        "inputFileFilters": { "allow": ["*.glb"], "exclude": [] }
    }
}
```

`overrides` does **not** change the config body. It changes how an execution's inputs are accepted and validated (and what metadata is provided) when this template is chosen. See [System configuration](#system-configuration) for the meaning of each key.

#### Default template

A pipeline may designate one template as its default by setting `isDefault` to `true`. The default is pre-selected on the execute form, and when a pipeline that requires a template (`requireTemplate`) is executed without a `templateId`, the backend resolves the run against the default template. At most one template per pipeline is the default: setting `isDefault` on a template clears it on any other template of the same pipeline.

### Get a template

Retrieves a single template. The `configBody` and `webFormJson` are returned inline, and the response includes the template's `tagSchema`.

```
GET /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |
| `templateId` | string | Yes      | Template identifier |

#### Response

```json
{
    "databaseId": "my-database",
    "pipelineId": "my-conversion-pipeline",
    "templateId": "high-quality",
    "templateName": "High quality",
    "description": "High-fidelity conversion preset",
    "configFormat": "json",
    "configBody": "{\"quality\": \"{{quality}}\", \"draco\": true}",
    "allowCustomEdit": false,
    "inputInstructions": "Choose the conversion quality preset.",
    "tagSchema": [
        {
            "tagKey": "quality",
            "type": "enum",
            "required": true,
            "default": "high",
            "label": "Quality",
            "enumValues": ["low", "medium", "high"]
        }
    ]
}
```

#### Error responses

| Status | Description                               |
| ------ | ----------------------------------------- |
| `403`  | Not authorized                            |
| `404`  | Database, pipeline, or template not found |
| `500`  | Internal server error                     |

### Update a template

Updates a template. Supply any subset of the mutable fields; omitted fields are left unchanged.

```
PUT /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |
| `templateId` | string | Yes      | Template identifier |

#### Request body

Any subset of `templateName`, `description`, `configFormat`, `configBody`, `webFormJson`, `allowCustomEdit`, `isDefault`, `inputInstructions`, `overrides`, and `tagSchema` (see [Create a template](#create-a-template)).

#### Response

Returns the updated template with `configBody` and `webFormJson` inline.

#### Error responses

| Status | Description                               |
| ------ | ----------------------------------------- |
| `400`  | Validation error                          |
| `403`  | Not authorized                            |
| `404`  | Database, pipeline, or template not found |
| `500`  | Internal server error                     |

### Delete a template

Deletes a template and its tag schema.

```
DELETE /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |
| `templateId` | string | Yes      | Template identifier |

#### Response

```json
{
    "message": "Template deleted"
}
```

#### Error responses

| Status | Description                               |
| ------ | ----------------------------------------- |
| `403`  | Not authorized                            |
| `404`  | Database, pipeline, or template not found |
| `500`  | Internal server error                     |

### Get a template's tag schema

Retrieves the tag schema for a template.

```
GET /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |
| `templateId` | string | Yes      | Template identifier |

#### Response

```json
{
    "fields": [
        {
            "tagKey": "quality",
            "type": "enum",
            "required": true,
            "default": "high",
            "label": "Quality",
            "enumValues": ["low", "medium", "high"]
        }
    ]
}
```

#### Error responses

| Status | Description                               |
| ------ | ----------------------------------------- |
| `403`  | Not authorized                            |
| `404`  | Database, pipeline, or template not found |
| `500`  | Internal server error                     |

### Set a template's tag schema

Replaces a template's tag schema with the supplied set of fields.

```
PUT /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |
| `templateId` | string | Yes      | Template identifier |

#### Request body

| Field    | Type  | Required | Description                                                         |
| -------- | ----- | -------- | ------------------------------------------------------------------- |
| `fields` | array | Yes      | Tag field definitions. See [Tag schema fields](#tag-schema-fields). |

:::warning[Reserved tag keys]
Reserved system tag keys (the built-in template tags, such as `executionId` and `workflowId`) and any key using the `metadata_` prefix are rejected.
:::

#### Request body example

```json
{
    "fields": [
        {
            "tagKey": "quality",
            "type": "enum",
            "required": true,
            "default": "high",
            "label": "Quality",
            "description": "Conversion quality preset",
            "enumValues": ["low", "medium", "high"]
        }
    ]
}
```

#### Response

Returns the stored tag schema (`{ "fields": [ ... ] }`).

#### Error responses

| Status | Description                                                        |
| ------ | ------------------------------------------------------------------ |
| `400`  | Validation error, reserved tag key, or reserved `metadata_` prefix |
| `403`  | Not authorized                                                     |
| `404`  | Database, pipeline, or template not found                          |
| `500`  | Internal server error                                              |

### Tag schema fields

Each entry in a template's tag schema defines one tag:

| Field         | Type    | Required | Description                                                                   |
| ------------- | ------- | -------- | ----------------------------------------------------------------------------- |
| `tagKey`      | string  | Yes      | Tag key. Reserved system tag keys and the `metadata_` prefix are not allowed. |
| `type`        | string  | Yes      | One of `string`, `integer`, `number`, `boolean`, `string-list`, or `enum`.    |
| `required`    | boolean | No       | Whether a value must be supplied.                                             |
| `default`     | any     | No       | Default value (type matches `type`).                                          |
| `label`       | string  | No       | Human-readable label shown in forms.                                          |
| `description` | string  | No       | Field description.                                                            |
| `enumValues`  | array   | No       | Allowed values. Required when `type` is `enum`.                               |

---

## Execution configuration

The `executionConfig` object binds a pipeline to the resource that runs it. The `executionType` selects the binding, and the matching nested block supplies the target.

| Field                  | Type   | Description                                                                                                                     |
| ---------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `executionType`        | string | `Lambda`, `SQS`, `EventBridge`, or `DeadlineCloud`.                                                                             |
| `waitForCallback`      | string | `Enabled` to have Step Functions wait for a task token callback; `Disabled` otherwise. Mandatory `Enabled` for `DeadlineCloud`. |
| `taskTimeout`          | string | Timeout in seconds (string) for a callback (max 604800 = 1 week). Applies when `waitForCallback` is `Enabled`.                  |
| `taskHeartbeatTimeout` | string | Heartbeat timeout in seconds (string). Must be smaller than `taskTimeout`.                                                      |
| `lambda`               | object | `{ "resourceId": "<lambda name or ARN>" }` for the `Lambda` type.                                                               |
| `sqs`                  | object | `{ "queueUrl": "<queue URL>" }` for the `SQS` type.                                                                             |
| `eventBridge`          | object | `{ "busArn": "<bus ARN>", "source": "<source>", "detailType": "<detail-type>" }` for the `EventBridge` type.                    |
| `deadlineCloud`        | object | Target settings for the `DeadlineCloud` type.                                                                                   |

## System configuration

The `systemConfig` object describes how a pipeline consumes input and whether it uses templates.

| Field                         | Type    | Description                                                                                                                                                                                                                                                                                                                 |
| ----------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileArity`              | string  | Number of input files the pipeline consumes: `none` (no input file), `one` (exactly one), or `multi` (one or more).                                                                                                                                                                                                         |
| `assetScope`                  | object  | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, and `folderAllowed` controlling accepted asset selections.                                                                                                                                                                                            |
| `metadataInputs`              | object  | Booleans `assetMetadata`, `fileMetadata`, and `fileAttributes` — which metadata is gathered from the input assets/files and passed to the pipeline.                                                                                                                                                                         |
| `requireTemplate`             | boolean | When `true`, every execution of this pipeline must select one of its configuration templates.                                                                                                                                                                                                                               |
| `allowCustomTemplateOverride` | boolean | When `true`, an execution may supply its own raw configuration body in place of a saved template.                                                                                                                                                                                                                           |
| `auxPreviewPipelineSuffix`    | string  | Suffix used to associate an auxiliary preview pipeline.                                                                                                                                                                                                                                                                     |
| `inputFileFilters`            | object  | `allow` and `exclude` arrays. Each entry matches by extension (`*.glb`, with `.glb` also accepted as shorthand), exact path, file name, or wildcard (`*.previewFile.*`, `/models/*`). Matching is case-insensitive. A non-empty `allow` restricts inputs to matching files; `exclude` removes matches and takes precedence. |

### Field rules and restrictions

-   **Asset span is one intent.** `crossAssetAllowed` and `singleAssetOnly` are opposite intents. Both `true` is contradictory (`singleAssetOnly` wins and cross-asset input is rejected). Set `singleAssetOnly: true` (with `crossAssetAllowed: false`) for single-asset pipelines, or `crossAssetAllowed: true` (with `singleAssetOnly: false`) to allow multiple assets.
-   **`wholeAssetAllowed` / `folderAllowed`** gate `/` (whole-asset) and `/folder/` selections respectively.
-   **`inputFileArity`** at execute time: `none` rejects any input file; `one` requires exactly one; `multi` requires at least one.
-   **Input-file filters** — a non-empty `allow` list restricts eligibility to matching files; a pipeline whose filters exclude every selected input fails the execution.
-   **Templates** — a chosen template's `overrides` may replace only `inputFileArity`, `metadataInputs`, `assetScope`, and `inputFileFilters` (never `requireTemplate` / `allowCustomTemplateOverride`).

---

## Pipeline execution types

VAMS pipelines support several execution types, each suited for different integration patterns.

### Lambda

The default execution type. VAMS invokes an AWS Lambda function synchronously as a Step Functions task.

-   If you provide a `lambdaName`, VAMS uses your existing Lambda function.
-   If you omit `lambdaName`, VAMS auto-creates a sample Lambda function with a unique name.
-   Auto-created Lambda functions are deleted when the pipeline is deleted.

### SQS

VAMS sends a message to an Amazon SQS queue. This is ideal for integrating with external processing systems that poll an SQS queue.

-   Requires `sqsQueueUrl` in the pipeline definition.
-   The SQS queue must be pre-created and accessible. VAMS does not create SQS queues.
-   Supports `waitForCallback` for asynchronous processing with task token callback.

### EventBridge

VAMS publishes an event to an Amazon EventBridge bus. This is ideal for event-driven architectures and fan-out patterns.

-   Optionally accepts `eventBridgeBusArn` (defaults to the account's default event bus).
-   Optionally accepts `eventBridgeSource` and `eventBridgeDetailType` for event filtering.
-   Supports `waitForCallback` for asynchronous processing with task token callback.

### DeadlineCloud

VAMS submits work to AWS Deadline Cloud. This is ideal for render-farm style batch processing.

-   Asynchronous only. A task token callback is mandatory, so `waitForCallback` is always enabled.
-   The callback reports completion back to the Step Functions workflow.

:::tip[Callback pattern]
When `waitForCallback` is set to `Enabled`, the Step Functions workflow pauses and waits for the external system to call back with a task token. The token is included in the pipeline payload. Set `taskTimeout` to define how long to wait before the task is considered failed.
:::

---

## Related resources

-   [Workflows API](workflows.md) -- Compose pipelines into executable workflows
-   [Assets API](assets.md) -- Manage the assets that pipelines process
