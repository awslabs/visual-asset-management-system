# Pipelines API

The Pipelines API allows you to create, retrieve, update, and delete processing pipelines. Pipelines define transformation or analysis steps that can be composed into [workflows](workflows.md) and executed against assets.

VAMS supports these pipeline execution types: **Lambda** (synchronous or asynchronous invocation of an AWS Lambda function), **SQS** (asynchronous message to an Amazon SQS queue), **EventBridge** (asynchronous event to an Amazon EventBridge bus), and **DeadlineCloud** (asynchronous submission to AWS Deadline Cloud, with a mandatory task token callback, available only when the deployment enables it).

Pipelines are scoped to a database. A pipeline can carry one or more **templates** — reusable configuration bodies (for example JSON, YAML, OpenJD, or XML) that supply the parameters an execution passes to the pipeline. Each template can define a **tag schema** describing the typed tags that resolve `{{tagName}}` placeholders in the template body.

:::info[Authorization]
All pipeline endpoints require a valid JWT token in the `Authorization` header. Pipelines are subject to two-tier authorization: API-level access is checked first, followed by object-level Casbin policy enforcement on each pipeline resource. A template and tag-schema endpoint is authorized on its owning pipeline, with the object action mirroring the HTTP method.

Reconfiguring the `GLOBAL` scope carries one additional requirement. A `GLOBAL` pipeline is visible and runnable from every database, so creating one — or creating, updating, or deleting one of its templates or tag schemas — additionally requires the pipeline-management action (`PUT`) on it. The pipeline object's `POST` action covers both "run this pipeline" and "create a pipeline", so a role scoped to running global pipelines holds `POST` on them; requiring `PUT` as well is what keeps such a role from reconfiguring the shared catalog.

An update is enforced twice: once on the pipeline as stored and again on the pipeline as changed. `pipelineName` and `category` are policy-evaluated attributes, so a request moving a pipeline into a name or category scope the caller's own constraints deny is rejected even when the caller may write the pipeline it read.
:::

---

## List all pipelines

Retrieves all pipelines across all databases.

```
GET /pipelines
```

### Query parameters

| Parameter         | Type   | Required | Default | Description                                                                                                                                  |
| ----------------- | ------ | -------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `maxItems`        | number | No       | `100`   | Maximum number of items to return. Clamped to 500 — a larger request is served a 500-row page; the remainder is reached through `NextToken`. |
| `pageSize`        | number | No       | `100`   | Number of items per page, clamped to `maxItems`.                                                                                             |
| `startingToken`   | string | No       | `null`  | Continuation token from a previous response's `NextToken`.                                                                                   |
| `includeArchived` | string | No       | `false` | Include archived pipelines (`true`/`false`)                                                                                                  |

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
                "dateModified": "2026-03-15T10:30:00Z",
                "createdBy": "user@example.com",
                "modifiedBy": "user@example.com",
                "schemaVersion": 1
            }
        ],
        "NextToken": null
    }
}
```

`templates` is absent from a list item — it is returned only by [Get a pipeline](#get-a-pipeline). `templateCount` is best-effort and is `null` when the count could not be computed.

`NextToken` is `null` on the last page. Pipelines the caller cannot read are dropped after the page is read, so a page may hold fewer items than requested while a token still remains — page until it is absent.

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Invalid parameters    |
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

Same as [List all pipelines](#list-all-pipelines): `maxItems`, `pageSize`, `startingToken`, and `includeArchived`, under the same defaults and caps.

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

The inline `templates` array holds at most the first 10 descriptors, and each carries only `templateId`, `templateName`, `configFormat`, and `allowCustomEdit`. `templateCount` always reports the pipeline's true total, so a pipeline with more templates than that shows fewer entries than the count — read the full set from [List templates](#list-templates), which pages with a `NextToken`.

To read a template's `configBody` and `webFormJson`, call [Get a template](#get-a-template). It is the only response that rehydrates a body VAMS offloaded to Amazon S3; the list response returns an offloaded body as an empty string.

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
                "fileAttributes": false,
                "databaseMetadata": true
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

| Field             | Type    | Required | Description                                                                                                                            |
| ----------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`      | string  | Yes      | Database identifier. Must match the `databaseId` path parameter. Use `GLOBAL` for a global pipeline.                                   |
| `pipelineId`      | string  | No       | Pipeline identifier. Send `null` or omit to have one generated. Must be unique across all databases.                                   |
| `pipelineName`    | string  | Yes      | Human-readable pipeline name, at most 256 characters.                                                                                  |
| `category`        | string  | No       | Optional grouping label, at most 256 characters.                                                                                       |
| `description`     | string  | No       | Pipeline description, at most 1,024 characters.                                                                                        |
| `executionConfig` | object  | No       | Execution binding. Omitted, it defaults to a `Lambda` binding with no target. See [Execution configuration](#execution-configuration). |
| `systemConfig`    | object  | No       | Input handling and templating defaults. See [System configuration](#system-configuration).                                             |
| `enabled`         | boolean | No       | Whether the pipeline is enabled (default `true`).                                                                                      |

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

| Status | Description                                                                                                                                    |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Validation error, a body `databaseId` that does not match the path, a pipeline ID already in use, or a disabled `DeadlineCloud` execution type |
| `403`  | Not authorized                                                                                                                                 |
| `500`  | Internal server error                                                                                                                          |

:::note[A pipeline is created without reading the database record]
Create writes the pipeline row under the `databaseId` in the path without looking that database up, so a mistyped identifier returns `200` and a pipeline nobody finds in a database listing. Confirm the database exists with [Get a database](databases.md#get-a-database) before creating a pipeline under it.
:::

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

At least one field must be supplied.

| Field             | Type    | Required | Description                                                                                                                       |
| ----------------- | ------- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `pipelineName`    | string  | No       | Human-readable pipeline name, at most 256 characters.                                                                             |
| `category`        | string  | No       | Grouping label, at most 256 characters.                                                                                           |
| `description`     | string  | No       | Pipeline description, at most 1,024 characters.                                                                                   |
| `executionConfig` | object  | No       | Execution binding, replaced wholesale. See [Execution configuration](#execution-configuration).                                   |
| `systemConfig`    | object  | No       | Input handling and templating defaults, replaced wholesale. See [System configuration](#system-configuration).                    |
| `enabled`         | boolean | No       | Whether the pipeline is enabled.                                                                                                  |
| `archived`        | boolean | No       | The soft-delete flag. Send `false` to restore a pipeline archived by [Delete a pipeline](#delete-a-pipeline); `true` archives it. |

:::tip[Enable or disable a pipeline]
Set `enabled` to `true` or `false` to enable or disable a pipeline without changing any other field.
:::

:::tip[Restore an archived pipeline]
`PUT` with `\{"archived": false\}` returns an archived pipeline to the active listings under its original identifier, together with every workflow reference and execution record that names it. Set `enabled` back to `true` in the same request — the archive also disables the pipeline.
:::

### Request body example

```json
{
    "description": "Converts FBX files to glTF format (v2 preset)",
    "enabled": false
}
```

:::warning[`executionConfig` and `systemConfig` replace the stored block]
Both are stored whole. A request that supplies either one persists exactly the keys it sends, and any key it omits is gone rather than retained — send the complete block, not the subset being changed. `executionConfig.lambda.resourceId` is the one exception: when a `Lambda` binding names no function, the update keeps the function the pipeline already runs, so a partial execution-config edit does not repoint a deployed state machine at an empty target.
:::

:::note[Changing the execution binding needs each referencing workflow re-saved]
A pipeline's execution target and its callback and timeout values are compiled into the AWS Step Functions definition of every workflow that references it when that workflow is saved. Changing `executionConfig` returns a warning naming those workflows: their deployed state machines keep invoking the previous target until each workflow is saved again.
:::

### Response

Returns the updated pipeline, in the same shape as [Get a pipeline](#get-a-pipeline), plus an optional `warnings` array. See [Trigger consistency warnings](#trigger-consistency-warnings).

### Error responses

| Status | Description                                                                       |
| ------ | --------------------------------------------------------------------------------- |
| `400`  | Validation error, no field supplied, or a disabled `DeadlineCloud` execution type |
| `403`  | Not authorized                                                                    |
| `404`  | Pipeline not found                                                                |
| `500`  | Internal server error                                                             |

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

Archives a pipeline. The delete is a soft-delete that sets the pipeline's `archived` flag to `true` and its `enabled` flag to `false`; the record is retained but hidden from listings and lookups unless `includeArchived=true` is supplied. The archive is reversible — see [Update a pipeline](#update-a-pipeline) for the restore.

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

Templates are reusable configuration bodies attached to a pipeline. A template holds a `configBody` (the template text, which may contain `{{tagName}}` placeholders) and an optional `webFormJson` (opaque web form markup used to render an input form). Clients always send `configBody` and `webFormJson` inline; VAMS stores them on the template record while their combined size stays at or below 320 KB and offloads them to Amazon S3 beyond that. [Get a template](#get-a-template) rehydrates an offloaded body, so the storage choice is transparent there; the [List templates](#list-templates) view returns an offloaded body as an empty string rather than reading each row's object.

The combined `configBody` and `webFormJson` may be at most 5 MB. A larger body is rejected with `400` on create and update.

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

| Parameter       | Type   | Required | Default | Description                                                                                |
| --------------- | ------ | -------- | ------- | ------------------------------------------------------------------------------------------ |
| `pageSize`      | number | No       | `10`    | Templates per page. Clamped to a maximum of 10; a larger request is served a 10-row page.  |
| `maxItems`      | number | No       | `10`    | Accepted as a synonym for `pageSize`, under the same maximum. `pageSize` takes precedence. |
| `startingToken` | string | No       | --      | Continuation token from a previous response's `NextToken`.                                 |
| `NextToken`     | string | No       | --      | Accepted as a synonym for `startingToken`.                                                 |

`NextToken` is `null` once the walk is complete. Page until it is `null` rather than until a page looks short: a page filled to the requested size returns a token even when nothing follows it, so the last page of a pipeline holding an exact multiple of the page size is an empty one. A token that cannot be decoded returns `400` rather than serving the first page again.

The page size is capped because each row carries its inline `configBody`, which can reach the 320 KB inline threshold, so a wider page would breach the AWS Lambda synchronous-response limit.

#### Response

Each item is a full template record. A body VAMS offloaded to Amazon S3 comes back as an empty `configBody` and `webFormJson` here; [Get a template](#get-a-template) rehydrates it. `tagSchema` is `null` in this view — read a template individually for its tag definitions.

```json
{
    "message": {
        "Items": [
            {
                "pipelineDatabaseId": "my-database",
                "pipelineId": "my-conversion-pipeline",
                "templateId": "high-quality",
                "templateName": "High quality",
                "description": "High-fidelity conversion preset",
                "configFormat": "json",
                "configBody": "{\"quality\": \"{{quality}}\", \"draco\": true}",
                "webFormJson": "",
                "allowCustomEdit": false,
                "inputInstructions": "Choose the conversion quality preset.",
                "overrides": {},
                "isDefault": false,
                "tagSchema": null,
                "dateCreated": "2026-03-15T10:30:00Z",
                "dateModified": "2026-03-15T10:30:00Z",
                "createdBy": "user@example.com",
                "modifiedBy": "user@example.com",
                "schemaVersion": 1
            }
        ],
        "NextToken": null
    }
}
```

#### Error responses

| Status | Description                                           |
| ------ | ----------------------------------------------------- |
| `400`  | Invalid path parameters or an invalid `startingToken` |
| `403`  | Not authorized                                        |
| `404`  | Pipeline not found                                    |
| `500`  | Internal server error                                 |

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

| Field               | Type    | Required | Description                                                                                                                                                                                                                                             |
| ------------------- | ------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `templateId`        | string  | No       | Template identifier, at most 64 characters. A GUID is generated when omitted.                                                                                                                                                                           |
| `templateName`      | string  | Yes      | Human-readable template name, at most 256 characters.                                                                                                                                                                                                   |
| `description`       | string  | No       | Template description, at most 1,024 characters.                                                                                                                                                                                                         |
| `configFormat`      | string  | No       | Format of `configBody`: `json` (default), `yaml`, `openjd`, `xml`, or `raw`.                                                                                                                                                                            |
| `configBody`        | string  | No       | The template text. May contain `{{tagName}}` placeholders resolved from tags at execution time. When `configFormat` is `json`, it must be valid JSON around those placeholders (validated at save — see [System template tags](#system-template-tags)). |
| `webFormJson`       | string  | No       | Serialized web-form definition used to render the template's input form. When present, must be valid JSON (validated at save).                                                                                                                          |
| `allowCustomEdit`   | boolean | No       | Whether the template config may be edited inline at execution time.                                                                                                                                                                                     |
| `isDefault`         | boolean | No       | Marks this template as the pipeline's default. At most one template per pipeline is the default; setting it clears the flag on any other template. See [Default template](#default-template).                                                           |
| `inputInstructions` | string  | No       | Guidance shown to the user when supplying template inputs, at most 4,096 characters.                                                                                                                                                                    |
| `overrides`         | object  | No       | Per-template overrides of the pipeline's `systemConfig`. See [Template overrides](#template-overrides).                                                                                                                                                 |
| `tagSchema`         | array   | No       | Tag field definitions for the template. See [Tag schema fields](#tag-schema-fields).                                                                                                                                                                    |

A `templateId` already in use on this pipeline is rejected with `400` rather than replacing the template — use [Update a template](#update-a-template) instead.

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

Returns the created template with `configBody` and `webFormJson` inline, in the same shape as [Get a template](#get-a-template).

:::note[A bad tag definition returns `tagSchemaErrors`]
When an entry in the supplied `tagSchema` fails validation, the response is `400` with a `tagSchemaErrors` array under `message` — one entry per offending definition — and no template is written. See [Set a template's tag schema](#set-a-templates-tag-schema) for the body shape.
:::

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

| Status | Description                                                                                                                                                                             |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Validation error, a body over the 5 MB combined cap, a bad tag definition (`tagSchemaErrors`), or a trigger default with a required tag with no default value (`triggerTemplateErrors`) |
| `403`  | Not authorized                                                                                                                                                                          |
| `404`  | Pipeline not found                                                                                                                                                                      |
| `500`  | Internal server error                                                                                                                                                                   |

#### Template overrides

`overrides` is an object that overrides the parent pipeline's `systemConfig` for executions that use this template. It may contain only these four keys (any other key is rejected at save); each is validated at save time and each is optional — an omitted key inherits the pipeline's value:

| Key                | Type   | Value                                                                                                                                                                                                                                                                         |
| ------------------ | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileArity`   | string | `none`, `one`, or `multi`.                                                                                                                                                                                                                                                    |
| `assetScope`       | object | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, `folderAllowed`.                                                                                                                                                                                        |
| `metadataInputs`   | object | Booleans `assetMetadata`, `fileMetadata`, `fileAttributes`, `databaseMetadata`.                                                                                                                                                                                               |
| `inputFileFilters` | object | `allow` and `exclude` arrays of strings. Each entry is an extension (`*.glb`), exact path, file name, or wildcard (`*.previewFile.*`); matching is case-insensitive. An omitted, empty, or `*` allow list accepts any file; a match-everything `exclude` is rejected on save. |

```json
{
    "overrides": {
        "inputFileArity": "multi",
        "assetScope": { "crossAssetAllowed": true, "singleAssetOnly": false },
        "metadataInputs": {
            "assetMetadata": true,
            "fileMetadata": false,
            "fileAttributes": false,
            "databaseMetadata": true
        },
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
| Metadata content (JSON)            | `inputMetadataObject`, `assetMetadataObject`, `fileMetadataObject`, `fileAttributesObject`, `assetDataObject`, `databaseMetadataObject`                                                                                                                                                                                                                                          |
| AWS Deadline Cloud                 | `deadlineFarmId`, `deadlineQueueId`, `deadlineStorageProfileId` (empty until the pipeline's Deadline Cloud configuration supplies them)                                                                                                                                                                                                                                          |

Dynamic metadata placeholders of the form `{{metadata_<key>}}` are also reserved for a metadata value keyed by `<key>`. The web template editor shows this same catalog inline beneath the config-body editor so authors can reference it without leaving the form.

##### Placeholder quoting in a JSON config body

Each tag renders one of two shapes, and where the placeholder sits in a `json` config body has to match:

-   **Text tags** — every tag outside the two groups marked `(JSON)`, plus the template's own `tagSchema` fields. The tag renders an escaped text value that fills a JSON string, so the placeholder belongs **inside** the string's quotes: `"assetId": "{{firstAssetFileAssetId}}"`. It may also sit within a longer string: `"uri": "s3://{{outputBucket}}/{{outputFilesPrefix}}out.glb"`.
-   **JSON tags** — the **Input-file collections (JSON)** and **Metadata content (JSON)** groups. The tag renders a JSON object, array, or number, which is the whole value and takes **no** quotes: `"files": {{assetFileKeyArray}}` or `"metadata": {{databaseMetadataObject}}`.

A `json` config body is parse-checked at save with each placeholder stood in for the shape its tag renders, so a body carrying placeholders is validated for the JSON around them. Two forms are rejected with a 400: a text placeholder used as a bare value (`"prompt": {{PROMPT}}`), and an object- or array-valued placeholder written inside quotes (`"metadata": "{{databaseMetadataObject}}"`) — the latter would render an object literal inside the string's own quotes and hand the pipeline malformed JSON at run time.

Bodies in the `yaml`, `openjd`, `xml`, and `raw` formats are passed through as text, so this check does not apply to them.

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
        "webFormJson": "",
        "allowCustomEdit": false,
        "inputInstructions": "Choose the conversion quality preset.",
        "overrides": {},
        "isDefault": false,
        "tagSchema": [
            {
                "tagKey": "quality",
                "type": "enum",
                "required": true,
                "default": "high",
                "label": "Quality",
                "description": "Conversion quality preset",
                "enumValues": ["low", "medium", "high"]
            }
        ],
        "dateCreated": "2026-03-15T10:30:00Z",
        "dateModified": "2026-03-15T10:30:00Z",
        "createdBy": "user@example.com",
        "modifiedBy": "user@example.com",
        "schemaVersion": 1
    }
}
```

#### Error responses

| Status | Description                    |
| ------ | ------------------------------ |
| `403`  | Not authorized                 |
| `404`  | Pipeline or template not found |
| `500`  | Internal server error          |

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

Any subset of `templateName`, `description`, `configFormat`, `configBody`, `webFormJson`, `allowCustomEdit`, `isDefault`, `inputInstructions`, `overrides`, and `tagSchema` (see [Create a template](#create-a-template)). At least one field must be supplied.

A supplied `overrides` or `tagSchema` **replaces** the stored one rather than merging into it, so send the complete set. Send `tagSchema` as an empty array to remove a template's tags.

#### Response

Returns the updated template with `configBody` and `webFormJson` inline, in the same shape as [Get a template](#get-a-template).

:::note[Templates used as a trigger default must be headless-runnable]
As with [Create a template](#create-a-template), when the template is referenced by a workflow trigger as a default and its tag schema has a required tag with no default value, the update is rejected with `400` and a `triggerTemplateErrors` list under `message`. Give each such tag a default value or make it optional.
:::

:::note[A tag's type is validated against the stored body]
A tag schema and a configuration body are one contract: a tag's declared type determines whether its placeholder renders into a valid document. `\{"steps": \{\{PARAM\}\}\}` is valid JSON when `PARAM` is an integer and invalid when it is a string, because the substituted value lands in an unquoted position.

Supplying `tagSchema` therefore re-checks the schema against the body currently stored, even when the request changes no body of its own, and a retype that would invalidate it is rejected with `400`. [Set a template's tag schema](#set-a-templates-tag-schema) applies the same check, so both routes reach the same verdict for the same change. Send the new `configBody` alongside `tagSchema` when a retype requires the body to change with it.
:::

#### Error responses

| Status | Description                                                                                                                                                                             |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Validation error, a body over the 5 MB combined cap, a bad tag definition (`tagSchemaErrors`), or a trigger default with a required tag with no default value (`triggerTemplateErrors`) |
| `403`  | Not authorized                                                                                                                                                                          |
| `404`  | Pipeline or template not found                                                                                                                                                          |
| `500`  | Internal server error                                                                                                                                                                   |

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

| Status | Description                    |
| ------ | ------------------------------ |
| `403`  | Not authorized                 |
| `404`  | Pipeline or template not found |
| `500`  | Internal server error          |

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

The response identifies the schema by the template that owns it. `tagSchemaId`, `dateCreated`, and `dateModified` are present in the shape but always empty — a tag schema is addressed through its template, so it carries no identifier or timestamps of its own. Read `dateModified` on [Get a template](#get-a-template) for when the template and its schema were last saved.

```json
{
    "message": {
        "pipelineDatabaseId": "my-database",
        "pipelineId": "my-conversion-pipeline",
        "templateId": "high-quality",
        "tagSchemaId": "",
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
        ],
        "dateCreated": "",
        "dateModified": ""
    }
}
```

A template with no tag schema returns an empty `fields` array rather than a `404`.

#### Error responses

| Status | Description                    |
| ------ | ------------------------------ |
| `403`  | Not authorized                 |
| `404`  | Pipeline or template not found |
| `500`  | Internal server error          |

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

| Field    | Type  | Required | Description                                                                      |
| -------- | ----- | -------- | -------------------------------------------------------------------------------- |
| `fields` | array | Yes      | Tag field definitions, at most 250. See [Tag schema fields](#tag-schema-fields). |

:::warning[Reserved tag keys]
Reserved system tag keys (the built-in template tags, such as `executionId` and `workflowId`) and any key using the `metadata_` prefix are rejected.
:::

This route applies the same cross-checks the template `PUT` applies, because the schema and the stored `configBody` are one contract:

-   **The stored body is re-validated against the new schema.** A tag's declared type decides where its `{{tagKey}}` placeholder may sit in a `json` body, so a type change that leaves the body rendering invalid JSON is rejected with `tagSchemaErrors` rather than stored. A body in a non-`json` format, or one referencing no tags, is not affected.
-   **Trigger defaults must stay headless-runnable.** When the template is named as a default by a workflow trigger and the new schema gives a required tag no default value, the request is rejected with `400` and a `triggerTemplateErrors` list under `message`.

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

Returns the stored tag schema wrapped in `message`, in the same shape as [Get a template's tag schema](#get-a-templates-tag-schema) — including the empty `tagSchemaId` and timestamps.

A tag definition that fails validation returns `400` with a `tagSchemaErrors` array under `message`, one entry per offending definition:

```json
{
    "message": {
        "tagSchemaErrors": [
            "tag 'executionId': tagKey collides with a reserved system tag name or the reserved 'metadata_' prefix; choose a different key"
        ]
    }
}
```

#### Error responses

| Status | Description                                                                                                                                                                                                              |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `400`  | Validation error, reserved tag key, reserved `metadata_` prefix, or a body the new schema invalidates (`tagSchemaErrors`); or a trigger default left with a required tag with no default value (`triggerTemplateErrors`) |
| `403`  | Not authorized                                                                                                                                                                                                           |
| `404`  | Pipeline or template not found                                                                                                                                                                                           |
| `500`  | Internal server error                                                                                                                                                                                                    |

### Tag schema fields

Each entry in a template's tag schema defines one tag:

| Field         | Type    | Required | Description                                                                                           |
| ------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------- |
| `tagKey`      | string  | Yes      | Tag key, at most 128 characters. Reserved system tag keys and the `metadata_` prefix are not allowed. |
| `type`        | string  | No       | One of `string`, `integer`, `number`, `boolean`, `string-list`, or `enum`. Defaults to `string`.      |
| `required`    | boolean | No       | Whether a value must be supplied.                                                                     |
| `default`     | any     | No       | Default value (type matches `type`), at most 4,096 characters when serialized.                        |
| `label`       | string  | No       | Human-readable label shown in forms, at most 1,024 characters.                                        |
| `description` | string  | No       | Field description, at most 1,024 characters.                                                          |
| `enumValues`  | array   | No       | Allowed values, at most 250 entries of 256 characters each. Required when `type` is `enum`.           |

A tag schema holds at most 250 entries. Exceeding any of these bounds rejects the request with a `400`. See [Service Quotas and Limits](../additional/quotas.md#pipeline-template-and-tag-schema-limits) for the full set.

#### Placeholders in a json config body

When `configFormat` is `json`, the declared `type` determines where a tag's `{{tagKey}}` placeholder may sit, because it determines what the tag renders.

| Tag type                                      | Renders                          | Placement in the body                                    |
| --------------------------------------------- | -------------------------------- | -------------------------------------------------------- |
| `integer`, `number`, `boolean`, `string-list` | A JSON number, boolean, or array | The whole value, unquoted: `"steps": \{\{STEPS\}\}`      |
| `string`, `enum`                              | Text                             | Inside the string it fills: `"prompt": "\{\{PROMPT\}\}"` |

A body is validated against its own `tagSchema` when it is saved, and the reverse of either placement is rejected. Quoting a typed placeholder is the case worth knowing: `"steps": "\{\{STEPS\}\}"` is valid JSON, so nothing downstream complains — the pipeline simply receives the string `"150"` where its schema promised the number `150`. A quoted `string-list` is worse, rendering a body that does not parse at all.

The same check applies to a `customTemplateOverride` supplied at execute time, since that body reaches the pipeline without having passed through a template save.

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

Those eight are the only keys `executionConfig` accepts. Any other top-level key is rejected with `400`, so a misspelled setting is reported rather than stored and never read. The whole block may be at most **327,680 bytes** (320 KB) serialized — an allowance sized for a `DeadlineCloud` block carrying an OpenJD job template, which is itself capped at 262,144 characters (256 KB).

Each execution type requires its own target. `sqs.queueUrl`, and both `deadlineCloud.farmId` and `deadlineCloud.queueId`, are mandatory for their type; `eventBridge.busArn` is optional and resolves to the account's default event bus when absent.

### The Lambda target on create and update

`executionConfig.lambda.resourceId` is the function the pipeline's AWS Step Functions task invokes. It accepts either an AWS Lambda function ARN or a bare function name; anything else is rejected with `400`. An **absent** value is resolved rather than stored empty, and how depends on the operation:

| Operation | `lambda.resourceId` absent                                                                                                                          |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Create    | VAMS provisions a new function seeded from the sample pipeline package and stores its name.                                                         |
| Update    | The function the pipeline already runs is kept, so a partial execution-config edit does not repoint its deployed state machines at an empty target. |

An update that switches a pipeline **into** the `Lambda` type has no stored function to keep, so it provisions one the same way a create does. Either way the row is never saved with an empty invoke target: a deployment that cannot auto-create a function answers `400` with a message asking for an existing function in `executionConfig.lambda.resourceId`, and no pipeline is written.

Because `executionConfig` is replaced wholesale, `lambda.resourceId` is the only value carried over from the stored block. Every other setting an update omits — `waitForCallback`, the timeouts, the other execution-type blocks — is dropped.

## System configuration

The `systemConfig` object describes how a pipeline consumes input and whether it uses templates.

| Field                         | Type    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `inputFileArity`              | string  | Number of input files the pipeline consumes: `none` (no input file), `one` (exactly one), or `multi` (one or more).                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `assetScope`                  | object  | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, and `folderAllowed` controlling accepted asset selections.                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `metadataInputs`              | object  | Booleans `assetMetadata`, `fileMetadata`, `fileAttributes`, and `databaseMetadata` — which metadata is gathered and passed to the pipeline. See [Metadata inputs](#metadata-inputs).                                                                                                                                                                                                                                                                                                                                                                                           |
| `requireTemplate`             | boolean | When `true`, every execution of this pipeline must select one of its configuration templates.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `allowCustomTemplateOverride` | boolean | When `true`, an execution may supply its own raw configuration body in place of a saved template.                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `auxPreviewPipelineSuffix`    | string  | Suffix used to associate an auxiliary preview pipeline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `inputFileFilters`            | object  | `allow` and `exclude` arrays. Each entry matches by extension (`*.glb`, with `.glb` also accepted as shorthand), exact path, file name, or wildcard (`*.previewFile.*`, `/models/*`). Matching is case-insensitive. A non-empty `allow` restricts inputs to matching files; `exclude` removes matches and takes precedence. An omitted, empty, or match-everything `allow` list accepts any file and defers the decision to the workflow/template chain; a match-everything `exclude` (`*`, `**`, `*.*`, `/*`, `/**`) is rejected on save because it would exclude everything. |

A pipeline's `systemConfig` accepts those seven keys plus the four a workflow uses — `concurrencyRestriction`, `outputTarget`, `allowWorkflowTriggerChaining`, and `defaultOutputFileBaseExecutionPathExtension` (see [System configuration](workflows.md#system-configuration) in the Workflows API). The two records share one key set, so a block is validated the same way whichever it is sent to; a pipeline reads only the seven above. Any other top-level key is rejected with `400` — the block is stored whole and every reader resolves a named key, so an unrecognized one would be stored, returned, and never acted on.

The whole block may be at most **65,536 bytes** (64 KB) serialized. That is roughly a hundred times the largest block a built-in pipeline ships, and it bounds the filter lists as a group: `inputFileFilters.allow` and `.exclude` each hold at most 250 patterns of at most 512 characters, and the byte ceiling admits the full pattern count at any realistic pattern length. `auxPreviewPipelineSuffix` is at most 256 characters.

### Metadata inputs

`metadataInputs` is four independent booleans naming the metadata a pipeline is handed as input. Each is gathered from the execution's own entities and written into the metadata file the pipeline reads:

| Key                | Metadata gathered                      |
| ------------------ | -------------------------------------- |
| `assetMetadata`    | Each involved asset's own metadata.    |
| `fileMetadata`     | Each input file's metadata.            |
| `fileAttributes`   | Each input file's attributes.          |
| `databaseMetadata` | Each involved database's own metadata. |

Every key defaults to `true`, so the map is a list of opt-outs rather than opt-ins: a key the map omits is gathered. Create and update store `systemConfig` as sent, so a request naming only some keys persists exactly those and the rest keep their default — sending `{"fileMetadata": false}` suppresses file metadata and leaves the other three on.

A key a pipeline sets to `false` suppresses that metadata for the pipeline even when the execution captured it, and the workflow's own `metadataInputs` gate is the outer bound: a type reaches a pipeline only when both the workflow gate and the pipeline have it on. See [Metadata inputs](workflows.md#metadata-inputs) in the Workflows API for which entities an execution captures and the per-entity limits it applies.

`databaseMetadata` is read-only. A database's metadata is supplied to a pipeline as input; a pipeline never writes metadata back to a database. Pipeline metadata write-back targets assets and files only.

`fileMetadata` and `fileAttributes` describe an input file, so a pipeline with `inputFileArity: none` has nothing to gather them from and they are inert. Create and update accept the combination and return a warning naming it. `assetMetadata` and `databaseMetadata` describe an entity rather than a file and are gathered at every arity.

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

The default execution type. VAMS invokes an AWS Lambda function as an AWS Step Functions task.

-   If you provide `executionConfig.lambda.resourceId`, VAMS uses your existing Lambda function.
-   If you omit `executionConfig.lambda.resourceId`, VAMS auto-creates a sample Lambda function with a unique name. See [The Lambda target on create and update](#the-lambda-target-on-create-and-update) for how an omitted value resolves on each operation.
-   Deleting a pipeline is a soft-archive; any auto-created Lambda function is left in place, and restoring the pipeline reuses it rather than provisioning a second one.

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
