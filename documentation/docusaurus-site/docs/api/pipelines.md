# Pipelines API

The Pipelines API allows you to create, retrieve, update, and delete processing pipelines. Pipelines define transformation or analysis steps that can be composed into [workflows](workflows.md) and executed against assets.

VAMS supports these pipeline execution types: **Lambda** (synchronous or asynchronous invocation of an AWS Lambda function), **SQS** (asynchronous message to an Amazon SQS queue), **EventBridge** (asynchronous event to an Amazon EventBridge bus), and **DeadlineCloud** (asynchronous submission to AWS Deadline Cloud, with a mandatory task token callback, available only when the deployment enables it).

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

| Parameter         | Type   | Required | Default | Description                                 |
| ----------------- | ------ | -------- | ------- | ------------------------------------------- |
| `maxItems`        | number | No       | `100`   | Maximum number of items to return           |
| `pageSize`        | number | No       | `100`   | Number of items per page                    |
| `startingToken`   | string | No       | `null`  | Pagination token from previous response     |
| `includeArchived` | string | No       | `false` | Include archived pipelines (`true`/`false`) |

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
                "templateCount": 2,
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
| `maxItems`        | number | No       | `100`   | Maximum number of items to return       |
| `pageSize`        | number | No       | `100`   | Number of items per page                |
| `startingToken`   | string | No       | `null`  | Pagination token from previous response |
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

The response includes the pipeline's `executionConfig` and `systemConfig`, the optional `category` label, the `archived` flag, a `templateCount` of saved templates, and a `templates` array of lightweight descriptors for the templates that belong to the pipeline. `templateCount` is also present on each entry of the list responses.

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
        "templateCount": 1,
        "templates": [
            {
                "templateId": "high-quality",
                "templateName": "High quality",
                "configFormat": "json",
                "allowCustomEdit": false
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

| Field             | Type    | Required | Description                                                                                          |
| ----------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------- |
| `databaseId`      | string  | Yes      | Database identifier. Must match the `databaseId` path parameter. Use `GLOBAL` for a global pipeline. |
| `pipelineId`      | string  | No       | Pipeline identifier. Send `null` or omit to have one generated. Must be unique across all databases. |
| `pipelineName`    | string  | Yes      | Human-readable pipeline name.                                                                        |
| `category`        | string  | No       | Optional grouping label.                                                                             |
| `description`     | string  | No       | Pipeline description.                                                                                |
| `executionConfig` | object  | Yes      | Execution binding. See [Execution configuration](#execution-configuration).                          |
| `systemConfig`    | object  | No       | Input handling and templating defaults. See [System configuration](#system-configuration).           |
| `enabled`         | boolean | No       | Whether the pipeline is enabled (default `true`).                                                    |

### Request body example

```json
{
    "databaseId": "my-database",
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

Returns the created pipeline, in the same shape as [Get a pipeline](#get-a-pipeline), plus an optional `warnings` array. See [Trigger consistency warnings](#trigger-consistency-warnings).

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

Returns the updated pipeline, in the same shape as [Get a pipeline](#get-a-pipeline), plus an optional `warnings` array. See [Trigger consistency warnings](#trigger-consistency-warnings).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Validation error      |
| `403`  | Not authorized        |
| `404`  | Pipeline not found    |
| `500`  | Internal server error |

### Trigger consistency warnings

Creating or updating a pipeline succeeds (`200`) even when it is inconsistent with an auto-trigger that references it, returning a non-blocking `warnings` array alongside `message`. When a pipeline requires a template (`systemConfig.requireTemplate` is `true`) and is part of an auto-triggered workflow whose trigger has chosen no default template for it, the save succeeds with a warning: triggered executions of that workflow fail until the trigger picks a default template for this pipeline. The warning (rather than a rejection) avoids an ordering problem between saving a workflow's triggers and updating its pipelines.

```json
{
    "message": {
        "databaseId": "my-database",
        "pipelineId": "my-conversion-pipeline",
        "pipelineName": "Convert to glTF"
    },
    "warnings": [
        "pipeline 'Convert to glTF' requires a template and is part of auto-triggered workflow 'my-database:convert-and-preview' (trigger 'fileUpload'), but that trigger has not chosen a default template for it. Triggered executions will fail until the trigger picks a default template for this pipeline."
    ]
}
```

---

## Delete a pipeline

Archives a pipeline. The delete is a soft-delete that sets the pipeline's `archived` flag to `true`; the record is retained but hidden from listings and lookups unless `includeArchived=true` is supplied.

```
DELETE /database/{databaseId}/pipelines/{pipelineId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `pipelineId` | string | Yes      | Pipeline identifier |

### Response

```json
{
    "message": "Pipeline archived"
}
```

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `404`  | Pipeline not found    |
| `500`  | Internal server error |

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

#### Response

```json
{
    "message": {
        "Items": [
            {
                "templateId": "high-quality",
                "templateName": "High quality",
                "configFormat": "json",
                "allowCustomEdit": false,
                "isDefault": false,
                "inputInstructions": "Choose the conversion quality preset.",
                "dateCreated": "2026-03-15T10:30:00Z",
                "dateModified": "2026-03-15T10:30:00Z"
            }
        ],
        "NextToken": null
    }
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

:::note[Templates used as a trigger default must be headless-runnable]
When a template is referenced by a workflow trigger as a default (see [Set a trigger](workflows.md#set-a-trigger)) and its tag schema has a required tag with no default value, the save is rejected with `400` and a `triggerTemplateErrors` list under `message`. A trigger fires headless executions, which cannot supply template tags interactively, so each required tag on a trigger-default template needs a default value or must be optional. A template not referenced by any trigger is unaffected — a missing required tag is instead caught at run time for an interactive execution.

```json
{
    "message": {
        "triggerTemplateErrors": [
            "this template is a trigger default for workflow(s) [my-database:convert-and-preview] and has required tag(s) with no default value: scale. Give each a default value or make it optional."
        ]
    }
}
```

:::

#### Error responses

| Status | Description                                                                                                                 |
| ------ | --------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Validation error, or the template is a trigger default with a required tag with no default value (`triggerTemplateErrors`). |
| `403`  | Not authorized                                                                                                              |
| `404`  | Database or pipeline not found                                                                                              |
| `500`  | Internal server error                                                                                                       |

#### Template overrides

`overrides` is an object that overrides the parent pipeline's `systemConfig` for executions that use this template. It may contain only these four keys (any other key is rejected at save); each is validated at save time and each is optional — an omitted key inherits the pipeline's value:

| Key                | Type   | Value                                                                                                                                                                                                                                                                         |
| ------------------ | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileArity`   | string | `none`, `one`, or `multi`.                                                                                                                                                                                                                                                    |
| `assetScope`       | object | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, `folderAllowed`.                                                                                                                                                                                        |
| `metadataInputs`   | object | Booleans `assetMetadata`, `fileMetadata`, `fileAttributes`.                                                                                                                                                                                                                   |
| `inputFileFilters` | object | `allow` and `exclude` arrays of strings. Each entry is an extension (`*.glb`), exact path, file name, or wildcard (`*.previewFile.*`); matching is case-insensitive. An omitted, empty, or `*` allow list accepts any file; a match-everything `exclude` is rejected on save. |

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

:::tip[Recommended: let the template decide whether an input file is needed]
When one pipeline supports several modes that differ in what they consume, set the pipeline's
`inputFileArity` to the LOWEST value any of its templates needs — usually `none` — and let each
template raise it through `overrides`. A text-to-video template then needs no input file, while an
image-to-video template on the same pipeline overrides `inputFileArity` to `one` and narrows
`inputFileFilters` to the image types it accepts.

This keeps one pipeline per model instead of one per mode, and it means the execute form asks for a
file only when the chosen template actually consumes one. The built-in NVIDIA Cosmos 3 pipelines are
configured this way.

A workflow's own `inputFileArity` is the outer gate and is authored, not derived — templates are chosen
per execution, so a workflow cannot know at save time which one a run will pick. Set it to the MAXIMUM
arity any pipeline/template combination in the workflow can require, otherwise the workflow gate
rejects a selection a template would have accepted.
:::

#### Default template

A pipeline may designate one template as its default by setting `isDefault` to `true`. The default is pre-selected on the execute form, and when a pipeline that requires a template (`requireTemplate`) is executed without a `templateId`, the backend resolves the run against the default template. At most one template per pipeline is the default: setting `isDefault` on a template clears it on any other template of the same pipeline.

#### System template tags

A template's config body may contain `{{tagName}}` placeholders. There are two kinds:

-   **Template tags** — the typed fields defined by the template's own `tagSchema` (see [Set a template's tag schema](#set-a-templates-tag-schema)). A person supplies their values when running an execution.
-   **System tags** — a fixed set of placeholders the engine resolves automatically per pipeline task when the config body renders. A caller never supplies them, and a template's own tag key may **not** collide with a system tag name (or use the reserved `metadata_` prefix) — such a key is rejected. System tags expose the execution's identity, input files, output/auxiliary locations, and resolved metadata.

The complete set of system tags, grouped by category:

| Group                              | Tags                                                                                                                                                                                                                                                                                                                                                                             |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Execution & workflow identity      | `executionId`, `workflowId`, `workflowDatabaseId`, `triggerType`, `executingUserName`                                                                                                                                                                                                                                                                                            |
| Pipeline-task identity             | `pipelineExecutionId`, `pipelineId`, `pipelineName`, `pipelineDatabaseId`, `jobName`                                                                                                                                                                                                                                                                                             |
| Timestamps                         | `jobStartTimestamp`, `jobStartTimestampUnix`, `jobStartDate`, `executionStartTimestamp`                                                                                                                                                                                                                                                                                          |
| First input file                   | `firstAssetFileDatabaseId`, `firstAssetFileAssetId`, `firstAssetFileAssetBucket`, `firstAssetFileAssetRootS3Key`, `firstAssetFileRelativePath`, `firstAssetFileKey`, `firstAssetFileVersionId`, `firstAssetFileAuxPreviewPrefix`, `firstAssetFileS3Uri`, `firstAssetFileAuxPreviewS3Uri`, `firstAssetFileFileName`, `firstAssetFileFileNameNoExt`, `firstAssetFileFileExtension` |
| Input-file collections (JSON)      | `assetFileKeyArray`, `assetFileRelativePathArray`, `assetFileS3UriArray`, `assetFileVersionIdArray`, `assetFileObjectArray`, `assetFileAssetIdArray`, `assetFileUniqueAssetIdArray`, `assetFileDatabaseIdArray`, `assetFileUniqueDatabaseIdArray`, `assetFileCount`                                                                                                              |
| Output locations                   | `outputBucket`, `outputFilesPrefix`, `outputFilesS3Uri`, `outputPreviewsPrefix`, `outputPreviewsS3Uri`, `outputMetadataPrefix`, `outputMetadataS3Uri`, `outputResultsPrefix`, `outputResultsS3Uri`, `outputTargetAssetId`, `outputTargetDatabaseId`, `outputTargetLocationType`, `outputTargetAssetRootS3Key`, `outputFileBaseExecutionPathExtension`                            |
| Auxiliary locations                | `auxBucket`, `auxTempPrefix`, `auxTempS3Uri`, `auxPreviewPipelineSuffix`                                                                                                                                                                                                                                                                                                         |
| Metadata / configuration locations | `inputMetadataS3Location`, `inputConfigurationS3Location`                                                                                                                                                                                                                                                                                                                        |
| System / orchestration             | `orchestrationBusArn`, `orchestrationEventPrefix`                                                                                                                                                                                                                                                                                                                                |
| Metadata content (JSON)            | `inputMetadataObject`, `assetMetadataObject`, `fileMetadataObject`, `fileAttributesObject`, `assetDataObject`                                                                                                                                                                                                                                                                    |
| AWS Deadline Cloud                 | `deadlineFarmId`, `deadlineQueueId`, `deadlineStorageProfileId` (empty until the pipeline's Deadline Cloud configuration supplies them)                                                                                                                                                                                                                                          |

Dynamic metadata placeholders of the form `{{metadata_<key>}}` are also reserved for a metadata value keyed by `<key>`. The web template editor shows this same catalog inline beneath the config-body editor so authors can reference it without leaving the form.

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
    "message": {
        "pipelineDatabaseId": "my-database",
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

:::note[Templates used as a trigger default must be headless-runnable]
As with [Create a template](#create-a-template), when the template is referenced by a workflow trigger as a default and its tag schema has a required tag with no default value, the update is rejected with `400` and a `triggerTemplateErrors` list under `message`. Give each such tag a default value or make it optional.
:::

#### Error responses

| Status | Description                                                                                                                 |
| ------ | --------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Validation error, or the template is a trigger default with a required tag with no default value (`triggerTemplateErrors`). |
| `403`  | Not authorized                                                                                                              |
| `404`  | Database, pipeline, or template not found                                                                                   |
| `500`  | Internal server error                                                                                                       |

### Delete a template

Deletes a template and its tag schema. This is a permanent delete: the template row, any offloaded S3
config bodies, and the tag schema are all removed. Unlike deleting a pipeline or a workflow — a soft
archive that leaves a restorable record — a deleted template cannot be recovered.

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
    "message": {
        "pipelineDatabaseId": "my-database",
        "pipelineId": "my-conversion-pipeline",
        "templateId": "high-quality",
        "tagSchemaId": "a1b2c3d4",
        "fields": [
            {
                "tagKey": "quality",
                "type": "enum",
                "required": true,
                "default": "high",
                "label": "Quality",
                "enumValues": ["low", "medium", "high"]
            }
        ],
        "dateCreated": "2026-03-15T10:30:00Z",
        "dateModified": "2026-03-15T10:30:00Z"
    }
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

Returns the stored tag schema wrapped in `message`, alongside `pipelineDatabaseId`, `pipelineId`, `templateId`, `tagSchemaId`, and the create/modify timestamps — the same shape as [Get a template's tag schema](#get-a-templates-tag-schema).

#### Error responses

| Status | Description                                                        |
| ------ | ------------------------------------------------------------------ |
| `400`  | Validation error, reserved tag key, or reserved `metadata_` prefix |
| `403`  | Not authorized                                                     |
| `404`  | Database, pipeline, or template not found                          |
| `500`  | Internal server error                                              |

### Tag schema fields

Each entry in a template's tag schema defines one tag:

| Field         | Type    | Required | Description                                                                                      |
| ------------- | ------- | -------- | ------------------------------------------------------------------------------------------------ |
| `tagKey`      | string  | Yes      | Tag key. Reserved system tag keys and the `metadata_` prefix are not allowed.                    |
| `type`        | string  | No       | One of `string`, `integer`, `number`, `boolean`, `string-list`, or `enum`. Defaults to `string`. |
| `required`    | boolean | No       | Whether a value must be supplied.                                                                |
| `default`     | any     | No       | Default value (type matches `type`).                                                             |
| `label`       | string  | No       | Human-readable label shown in forms.                                                             |
| `description` | string  | No       | Field description.                                                                               |
| `enumValues`  | array   | No       | Allowed values. Required when `type` is `enum`.                                                  |

---

## Execution configuration

The `executionConfig` object binds a pipeline to the resource that runs it. The `executionType` selects the binding, and the matching nested block supplies the target.

| Field                  | Type   | Description                                                                                                                                                          |
| ---------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `executionType`        | string | `Lambda`, `SQS`, `EventBridge`, or `DeadlineCloud` (the last requires the deployment switch — see [DeadlineCloud](#deadlinecloud)).                                  |
| `waitForCallback`      | string | `Enabled` to have Step Functions wait for a task token callback; `Disabled` otherwise. Mandatory `Enabled` for `DeadlineCloud`.                                      |
| `taskTimeout`          | string | Timeout in seconds (string) for a callback (max 604800 = 1 week). Applies when `waitForCallback` is `Enabled`.                                                       |
| `taskHeartbeatTimeout` | string | Heartbeat timeout in seconds (string, max 604800 = 1 week). Set it below `taskTimeout` so a stalled task is caught by the heartbeat rather than the overall timeout. |
| `lambda`               | object | `{ "resourceId": "<lambda name or ARN>" }` for the `Lambda` type.                                                                                                    |
| `sqs`                  | object | `{ "queueUrl": "<queue URL>" }` for the `SQS` type.                                                                                                                  |
| `eventBridge`          | object | `{ "busArn": "<bus ARN>", "source": "<source>", "detailType": "<detail-type>" }` for the `EventBridge` type.                                                         |
| `deadlineCloud`        | object | Target settings for the `DeadlineCloud` type.                                                                                                                        |

## System configuration

The `systemConfig` object describes how a pipeline consumes input and whether it uses templates.

| Field                         | Type    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `inputFileArity`              | string  | Number of input files the pipeline consumes: `none` (no input file), `one` (exactly one), or `multi` (one or more).                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `assetScope`                  | object  | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, and `folderAllowed` controlling accepted asset selections.                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `metadataInputs`              | object  | Booleans `assetMetadata`, `fileMetadata`, and `fileAttributes` — which metadata is gathered from the input assets/files and passed to the pipeline.                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `requireTemplate`             | boolean | When `true`, every execution of this pipeline must select one of its configuration templates.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `allowCustomTemplateOverride` | boolean | When `true`, an execution may supply its own raw configuration body in place of a saved template.                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `auxPreviewPipelineSuffix`    | string  | Suffix used to associate an auxiliary preview pipeline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `inputFileFilters`            | object  | `allow` and `exclude` arrays. Each entry matches by extension (`*.glb`, with `.glb` also accepted as shorthand), exact path, file name, or wildcard (`*.previewFile.*`, `/models/*`). Matching is case-insensitive. A non-empty `allow` restricts inputs to matching files; `exclude` removes matches and takes precedence. An omitted, empty, or match-everything `allow` list accepts any file and defers the decision to the workflow/template chain; a match-everything `exclude` (`*`, `**`, `*.*`, `/*`, `/**`) is rejected on save because it would exclude everything. |

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

-   If you provide `executionConfig.lambda.resourceId`, VAMS uses your existing Lambda function.
-   If you omit `executionConfig.lambda.resourceId`, VAMS auto-creates a sample Lambda function with a unique name.
-   Deleting a pipeline is a soft-archive; any auto-created Lambda function is left in place.

### SQS

VAMS sends a message to an Amazon SQS queue. This is ideal for integrating with external processing systems that poll an SQS queue.

-   Requires `executionConfig.sqs.queueUrl` in the pipeline definition.
-   The SQS queue must be pre-created and accessible. VAMS does not create SQS queues.
-   Supports `waitForCallback` for asynchronous processing with task token callback.

### EventBridge

VAMS publishes an event to an Amazon EventBridge bus. This is ideal for event-driven architectures and fan-out patterns.

-   Optionally accepts `executionConfig.eventBridge.busArn` (defaults to the account's default event bus).
-   Optionally accepts `executionConfig.eventBridge.source` and `executionConfig.eventBridge.detailType` for event filtering.
-   Supports `waitForCallback` for asynchronous processing with task token callback.

### DeadlineCloud

VAMS submits work to AWS Deadline Cloud. This is ideal for render-farm style batch processing.

-   Asynchronous only. A task token callback is mandatory, so `waitForCallback` is always enabled.
-   The callback reports completion back to the Step Functions workflow.

:::warning[Deployment gate]
`DeadlineCloud` is selectable only when the deployment enables `app.pipelines.deadlineCloudExecutionTypeEnabled` (default `false`). Create and update reject the type with `400` on a deployment that has it off. It is unavailable in the GovCloud and EU Sovereign partitions. See the [configuration reference](../deployment/configuration-reference.md).
:::

:::tip[Callback pattern]
When `waitForCallback` is set to `Enabled`, the Step Functions workflow pauses and waits for the external system to call back with a task token. The token is included in the pipeline payload. Set `taskTimeout` to define how long to wait before the task is considered failed.
:::

---

## Related resources

-   [Workflows API](workflows.md) -- Compose pipelines into executable workflows
-   [Assets API](assets.md) -- Manage the assets that pipelines process
