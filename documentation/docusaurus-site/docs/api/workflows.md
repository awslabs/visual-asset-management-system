# Workflows API

The Workflows API allows you to create, retrieve, and delete workflows that orchestrate one or more [pipelines](pipelines.md) as AWS Step Functions state machines. A workflow executes against a set of input files (which may span assets) and tracks execution history.

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

| Parameter         | Type   | Required | Default | Description                             |
| ----------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`        | number | No       | `100`   | Maximum number of items to return       |
| `pageSize`        | number | No       | `100`   | Number of items per page                |
| `startingToken`   | string | No       | `null`  | Pagination token from previous response |
| `includeArchived` | string | No       | `false` | Include archived workflows              |

### Response

```json
{
    "message": {
        "Items": [
            {
                "databaseId": "my-database",
                "workflowId": "convert-and-preview",
                "workflowName": "Convert and preview",
                "category": "conversion",
                "description": "Convert 3D files and generate preview thumbnails",
                "specifiedPipelines": [
                    {
                        "pipelineDatabaseId": "GLOBAL",
                        "pipelineId": "3d-conversion-pipeline",
                        "jobName": ""
                    },
                    {
                        "pipelineDatabaseId": "GLOBAL",
                        "pipelineId": "3d-thumbnail-preview",
                        "jobName": ""
                    }
                ],
                "systemConfig": {
                    "inputFileArity": "one",
                    "outputTarget": { "locationType": "asset", "allowOverride": false }
                },
                "subDashboardUrl": "",
                "enabled": true,
                "archived": false,
                "workflow_arn": "arn:aws:states:us-east-1:123456789012:stateMachine:vams-convert-and-preview",
                "dateCreated": "2026-03-15T10:30:00Z",
                "dateModified": "2026-03-16T14:20:00Z",
                "executionCount": 42
            }
        ],
        "NextToken": null
    }
}
```

Each item in a list response includes an `executionCount` — the total number of executions recorded for that workflow. It is computed per page from the workflow-executions index, so it reflects the full execution history, not just the current page of executions. The value is omitted (or `null`) when the count could not be computed.

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

Same as [List all workflows](#list-all-workflows), plus:

| Parameter         | Type   | Required | Default | Description                |
| ----------------- | ------ | -------- | ------- | -------------------------- |
| `includeArchived` | string | No       | `false` | Include archived workflows |

:::note[Archived workflows]
Archived workflows are hidden by default. Set `includeArchived=true` to include workflows whose `archived` flag is set.
:::

### Response

Same structure as [List all workflows](#list-all-workflows).

---

## Get a workflow

Retrieves a single workflow by its identifier. The response includes the workflow's `category`, `specifiedPipelines`, `systemConfig`, `subDashboardUrl`, and `archived` fields, along with a `triggers` array describing the workflow's configured triggers.

```
GET /database/{databaseId}/workflows/{workflowId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `workflowId` | string | Yes      | Workflow identifier |

### Query parameters

| Parameter         | Type   | Required | Default | Description                                                    |
| ----------------- | ------ | -------- | ------- | -------------------------------------------------------------- |
| `includeArchived` | string | No       | `false` | Return the workflow even when it is archived (`true`/`false`). |

:::note[Archived workflows]
Archived workflows are hidden by default. Set `includeArchived=true` to retrieve a workflow whose `archived` flag is set.
:::

### Response

Returns a single workflow object, including its `triggers` array and `systemConfig`. See [System configuration](#system-configuration) for the shape of `systemConfig`.

### Error responses

| Status | Description             |
| ------ | ----------------------- |
| `400`  | Invalid path parameters |
| `403`  | Not authorized          |
| `404`  | Workflow not found      |
| `500`  | Internal server error   |

---

## Create a workflow

Creates a workflow in the specified database. The workflow is identified by the `databaseId` path parameter and the `workflowId` supplied in the body (or a generated one when omitted). A workflow references an ordered list of pipelines and carries optional triggers and input-handling defaults.

```
POST /database/{databaseId}/workflows
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |

### Request body

| Field                | Type    | Required | Description                                                                                                 |
| -------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------- |
| `databaseId`         | string  | Yes      | Database identifier. Must match the `databaseId` path parameter. Use `GLOBAL` for a global workflow.        |
| `workflowId`         | string  | No       | Workflow identifier. Send `null` or omit to have one generated. Must be unique across all databases.        |
| `workflowName`       | string  | Yes      | Human-readable workflow name.                                                                               |
| `category`           | string  | No       | Optional grouping label.                                                                                    |
| `description`        | string  | No       | Workflow description.                                                                                       |
| `specifiedPipelines` | array   | Yes      | Ordered, non-empty list of pipeline references. See [Specified pipelines](#specified-pipelines).            |
| `systemConfig`       | object  | No       | Input handling, asset-scope gating, and output defaults. See [System configuration](#system-configuration). |
| `subDashboardUrl`    | string  | No       | URL of an external dashboard associated with the workflow.                                                  |
| `enabled`            | boolean | No       | Whether the workflow is enabled (default `true`).                                                           |

:::note[Pipeline reference rules]

-   **Global workflows** (`databaseId: "GLOBAL"`) may reference only global pipelines.
-   **Database workflows** may reference global pipelines or pipelines from the same database.
    :::

### Request body example

```json
{
    "databaseId": "my-database",
    "workflowName": "Convert and preview",
    "category": "conversion",
    "description": "Convert 3D files and generate preview thumbnails",
    "specifiedPipelines": [
        {
            "pipelineId": "3d-conversion-pipeline",
            "pipelineDatabaseId": "GLOBAL",
            "jobName": "convert"
        },
        {
            "pipelineId": "preview-pipeline",
            "jobName": "preview"
        }
    ],
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
        "inputFileFilters": {
            "allow": ["*.fbx"],
            "exclude": []
        },
        "concurrencyRestriction": "perInputFile",
        "outputTarget": {
            "locationType": "asset",
            "allowOverride": false
        }
    },
    "enabled": true
}
```

:::note[Results-only workflows]
Set `outputTarget.locationType` to `none` for a results-only workflow that records only results text and logs and writes no asset output. A results-only workflow may still take input files (`inputFileArity` `none`, `one`, or `multi`) — for example, reading files to emit a metadata report. See [Output target](#output-target).
:::

### Response

Returns the created workflow, in the same shape as [Get a workflow](#get-a-workflow), plus a `warnings` array. See [Consistency warnings](#consistency-warnings).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Validation error      |
| `403`  | Not authorized        |
| `404`  | Database not found    |
| `500`  | Internal server error |

---

## Update a workflow

Updates a workflow. Supply any subset of the mutable fields; omitted fields are left unchanged.

```
PUT /database/{databaseId}/workflows/{workflowId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `workflowId` | string | Yes      | Workflow identifier |

### Request body

| Field                | Type    | Required | Description                                                                                                 |
| -------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------- |
| `workflowName`       | string  | No       | Human-readable workflow name.                                                                               |
| `category`           | string  | No       | Grouping label.                                                                                             |
| `description`        | string  | No       | Workflow description.                                                                                       |
| `specifiedPipelines` | array   | No       | Ordered, non-empty list of pipeline references. See [Specified pipelines](#specified-pipelines).            |
| `systemConfig`       | object  | No       | Input handling, asset-scope gating, and output defaults. See [System configuration](#system-configuration). |
| `subDashboardUrl`    | string  | No       | URL of an external dashboard associated with the workflow.                                                  |
| `enabled`            | boolean | No       | Whether the workflow is enabled.                                                                            |

:::tip[Enable or disable a workflow]
Set `enabled` to `true` or `false` to enable or disable a workflow without changing any other field.
:::

### Request body example

```json
{
    "description": "Convert 3D files and generate preview thumbnails (updated)",
    "enabled": false
}
```

### Response

Returns the updated workflow, in the same shape as [Get a workflow](#get-a-workflow), plus a `warnings` array. See [Consistency warnings](#consistency-warnings).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Validation error      |
| `403`  | Not authorized        |
| `404`  | Workflow not found    |
| `500`  | Internal server error |

---

## Delete a workflow

Archives a workflow. The delete is a soft-delete that sets the workflow's `archived` flag to `true`; the record is retained but hidden from listings and lookups unless `includeArchived=true` is supplied.

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
    "message": "Workflow archived"
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

## Triggers

Triggers auto-launch a workflow in response to an event. The `fileUpload` trigger runs the workflow when files matching its filters are uploaded. A trigger's `defaultTemplateIds` map supplies the template each included pipeline uses when the trigger launches the workflow, keyed by the composite `<pipelineDatabaseId>:<pipelineId>`.

Trigger endpoints are authorized on the parent workflow: API-level access is checked first, followed by object-level Casbin policy enforcement on the owning workflow.

A trigger-launched execution runs as the reserved system identity rather than as the user whose action fired the trigger, and its execution record reflects this (`triggerType` `File-Upload`, `triggeredByUserId` set to the system identity). This is intentional: the user who uploaded a file may not hold permission to run the workflow, but the trigger must still process the upload reliably, so the execution is decoupled from the acting user's permissions. Executions started directly through the [execute endpoint](#execute-a-workflow) run as the calling user.

### List triggers

Retrieves the triggers configured on a workflow.

```
GET /database/{databaseId}/workflows/{workflowId}/triggers
```

#### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `workflowId` | string | Yes      | Workflow identifier |

#### Response

```json
{
    "message": {
        "Items": [
            {
                "workflowDatabaseId": "GLOBAL",
                "workflowId": "convert-and-preview",
                "triggerType": "fileUpload",
                "triggerConfig": {
                    "inputFileFilters": {
                        "allow": ["*.fbx", "*.obj"],
                        "exclude": []
                    },
                    "defaultTemplateIds": {
                        "GLOBAL:3d-conversion-pipeline": "high-quality"
                    }
                },
                "enabled": true,
                "dateCreated": "2026-03-15T10:30:00Z",
                "dateModified": "2026-03-15T10:30:00Z"
            }
        ]
    }
}
```

#### Error responses

| Status | Description                    |
| ------ | ------------------------------ |
| `403`  | Not authorized                 |
| `404`  | Database or workflow not found |
| `500`  | Internal server error          |

### Get a trigger

Retrieves a single trigger by its type.

```
GET /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}
```

#### Path parameters

| Parameter     | Type   | Required | Description                            |
| ------------- | ------ | -------- | -------------------------------------- |
| `databaseId`  | string | Yes      | Database identifier                    |
| `workflowId`  | string | Yes      | Workflow identifier                    |
| `triggerType` | string | Yes      | Trigger type. Supported: `fileUpload`. |

#### Response

```json
{
    "message": {
        "workflowDatabaseId": "GLOBAL",
        "workflowId": "convert-and-preview",
        "triggerType": "fileUpload",
        "triggerConfig": {
            "inputFileFilters": {
                "allow": ["*.fbx", "*.obj"],
                "exclude": []
            },
            "defaultTemplateIds": {
                "GLOBAL:3d-conversion-pipeline": "high-quality"
            }
        },
        "enabled": true,
        "dateCreated": "2026-03-15T10:30:00Z",
        "dateModified": "2026-03-15T10:30:00Z"
    }
}
```

#### Error responses

| Status | Description                              |
| ------ | ---------------------------------------- |
| `403`  | Not authorized                           |
| `404`  | Database, workflow, or trigger not found |
| `500`  | Internal server error                    |

### Set a trigger

Sets or replaces a trigger of the given type on a workflow.

```
PUT /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}
```

#### Path parameters

| Parameter     | Type   | Required | Description                            |
| ------------- | ------ | -------- | -------------------------------------- |
| `databaseId`  | string | Yes      | Database identifier                    |
| `workflowId`  | string | Yes      | Workflow identifier                    |
| `triggerType` | string | Yes      | Trigger type. Supported: `fileUpload`. |

#### Request body

| Field                | Type    | Required | Description                                                                                                                                              |
| -------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileFilters`   | object  | No       | `allow` and `exclude` arrays matching by extension (`*.glb`), path, name, or wildcard (`*.previewFile.*`); case-insensitive. Omitted means no filtering. |
| `defaultTemplateIds` | object  | No       | Template used for each included pipeline when the trigger launches, keyed by `<pipelineDatabaseId>:<pipelineId>`.                                        |
| `enabled`            | boolean | No       | Whether the trigger is enabled (default `true`).                                                                                                         |

#### Request body example

```json
{
    "inputFileFilters": {
        "allow": ["*.fbx", "*.obj"],
        "exclude": ["*_draft.*"]
    },
    "defaultTemplateIds": {
        "GLOBAL:3d-conversion-pipeline": "high-quality",
        "my-database:preview-pipeline": "default-preview"
    },
    "enabled": true
}
```

#### Response

Returns the stored trigger, in the same shape as [Get a trigger](#get-a-trigger).

:::note[Trigger default templates must be headless-runnable]
A trigger fires headless executions, which cannot supply template tags interactively. When a template named in `defaultTemplateIds` has a required tag with no default value, the request is rejected with `400` and a `triggerTemplateErrors` list under `message`, identifying each offending template, its pipeline, and the tag keys at fault. Give each such tag a default value or make it optional, or choose a different default template. `defaultTemplateIds` is optional — a trigger need not name a default template for a pipeline.

```json
{
    "message": {
        "triggerTemplateErrors": [
            "template 'high-quality' (pipeline '3d-conversion-pipeline') is chosen as a trigger default but has required tag(s) with no default value: scale. A triggered (headless) execution cannot supply these, so give each a default value or make it optional."
        ]
    }
}
```

:::

#### Error responses

| Status | Description                                                                                                        |
| ------ | ------------------------------------------------------------------------------------------------------------------ |
| `400`  | Validation error, or a chosen default template has a required tag with no default value (`triggerTemplateErrors`). |
| `403`  | Not authorized                                                                                                     |
| `404`  | Database or workflow not found                                                                                     |
| `500`  | Internal server error                                                                                              |

### Delete a trigger

Deletes a trigger of the given type.

```
DELETE /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}
```

#### Path parameters

| Parameter     | Type   | Required | Description                            |
| ------------- | ------ | -------- | -------------------------------------- |
| `databaseId`  | string | Yes      | Database identifier                    |
| `workflowId`  | string | Yes      | Workflow identifier                    |
| `triggerType` | string | Yes      | Trigger type. Supported: `fileUpload`. |

#### Response

```json
{
    "message": "Trigger deleted"
}
```

#### Error responses

| Status | Description                              |
| ------ | ---------------------------------------- |
| `400`  | Invalid path parameters                  |
| `403`  | Not authorized                           |
| `404`  | Database, workflow, or trigger not found |
| `500`  | Internal server error                    |

---

## Specified pipelines

The `specifiedPipelines` array lists, in order, the pipelines a workflow runs. Each entry references one pipeline:

| Field                | Type   | Required | Description                                                                      |
| -------------------- | ------ | -------- | -------------------------------------------------------------------------------- |
| `pipelineId`         | string | Yes      | Identifier of the referenced pipeline.                                           |
| `pipelineDatabaseId` | string | No       | Database that owns the referenced pipeline. Defaults to the workflow's database. |
| `jobName`            | string | No       | Label for this pipeline step within the workflow.                                |

## System configuration

The `systemConfig` object describes how a workflow consumes input, which asset selections it accepts, and where it writes output.

| Field                                         | Type    | Description                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileArity`                              | string  | Number of input files the workflow consumes: `none` (no input file), `one` (exactly one), or `multi` (one or more).                                                                                                                                                                                                                                                                                                                      |
| `assetScope`                                  | object  | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, and `folderAllowed` controlling accepted asset selections. See [Asset scope](#asset-scope).                                                                                                                                                                                                                                                                        |
| `metadataInputs`                              | object  | Booleans `assetMetadata`, `fileMetadata`, and `fileAttributes` — which metadata is gathered from the input assets/files and passed to the pipelines.                                                                                                                                                                                                                                                                                     |
| `inputFileFilters`                            | object  | `allow` and `exclude` arrays. Each entry matches by extension (`*.glb`, with `.glb` also accepted as shorthand), exact path (`/models/x.glb`), file name, or wildcard (`*.previewFile.*`, `/models/*`). Matching is case-insensitive. A non-empty `allow` restricts inputs to matching files; `exclude` removes matches and takes precedence over `allow`.                                                                               |
| `concurrencyRestriction`                      | string  | How concurrent executions are limited: `none`, `perAsset`, or `perInputFile`.                                                                                                                                                                                                                                                                                                                                                            |
| `outputTarget`                                | object  | Where the workflow writes its output. See [Output target](#output-target).                                                                                                                                                                                                                                                                                                                                                               |
| `allowWorkflowTriggerChaining`                | boolean | Whether a file written by **another** workflow's execution may fire this workflow's triggers -- for example generating a preview or metadata from a conversion pipeline's output. A workflow never fires on output it wrote itself, whatever this is set to, so it cannot re-trigger itself in a loop. A chained file must still match the trigger's `inputFileFilters`. Defaults to `false`. See [Trigger chaining](#trigger-chaining). |
| `defaultOutputFileBaseExecutionPathExtension` | string  | The output path prefix an execution uses when its request supplies none. Stored **unresolved**, so `{{tag}}` placeholders resolve per run — one stored `/{{jobName}}/` gives every execution its own output folder. Empty means no default. See [Output path prefix](#output-path-prefix).                                                                                                                                               |

### Asset scope

`assetScope` constrains which input-file selections an execution may make; each rule is enforced at execute time:

-   **`crossAssetAllowed`** — permit input files spanning more than one asset. When `false`, all input files must belong to a single asset.
-   **`singleAssetOnly`** — reject an execution whose input files reference more than one asset. This is the inverse of `crossAssetAllowed`; set exactly one of the two intents (see [Field rules](#field-rules-and-restrictions)).
-   **`wholeAssetAllowed`** — permit a `/` selection meaning every file in the asset.
-   **`folderAllowed`** — permit a `/folder/` selection meaning every file under a folder.

### Output target

`outputTarget` is an object of `locationType` and `allowOverride` that controls where an execution writes its output.

-   **`locationType`** — `asset` (default) writes the workflow's asset files and metadata to a VAMS asset. `none` is results-only: the workflow writes no asset files or metadata and records only results text and logs against the execution transaction — for example, analyzing input files and emitting a metadata report. A results-only (`none`) workflow **may still take input files** (its `inputFileArity` can be `none`, `one`, or `multi`); its executions write no asset output and supply no `outputAssetId`/`outputDatabaseId`. When `locationType` is `asset` and `inputFileArity` is `none` (no input file to lock the output to), `allowOverride` must be `true` so an output asset can be chosen at execution time.
-   **`allowOverride`** — gates redirecting the output when an execution's input files resolve to exactly one input asset. With a single input asset the output is locked to that asset; `allowOverride` `true` lets the execute request redirect it via `outputAssetId`/`outputDatabaseId` (an omitted `outputDatabaseId` falls back to the input asset's database), while `allowOverride` `false` ignores an explicit output and writes to the single input asset. When the input files resolve to zero or multiple input assets there is no asset to lock to, so an explicit output target — both `outputAssetId` and `outputDatabaseId` — is honored regardless of `allowOverride`; supply both, or configure the workflow as results-only.

### Field rules and restrictions

These constraints govern valid `systemConfig` combinations. Some are enforced at create/update time (the request is rejected); others are enforced per execution.

-   **Asset span is one intent.** `crossAssetAllowed` and `singleAssetOnly` express opposite intents. Setting both `crossAssetAllowed: true` and `singleAssetOnly: true` is contradictory — `singleAssetOnly` wins and cross-asset inputs are rejected. Set `singleAssetOnly: true` (with `crossAssetAllowed: false`) for single-asset workflows, or `crossAssetAllowed: true` (with `singleAssetOnly: false`) to allow multiple assets.
-   **Whole-asset / folder selections** require `wholeAssetAllowed` / `folderAllowed` respectively; otherwise a `/` or `/folder/` selection is rejected at execute time.
-   **Results-only may take input files.** `outputTarget.locationType: none` writes no asset output but places no restriction on `inputFileArity` — it may be `none`, `one`, or `multi` (e.g. a workflow that reads files and emits only a metadata report). A results-only execution supplies no `outputAssetId`/`outputDatabaseId`.
-   **Asset output with no input files needs override.** When `outputTarget.locationType: asset` and `inputFileArity: none`, there is no input asset to lock the output to, so `outputTarget.allowOverride` must be `true` (an output asset is then chosen at execute time). Create/update rejects `asset` + `none` + `allowOverride: false`.
-   **`inputFileArity` at execute time** — `none` rejects any supplied input file; `one` requires exactly one; `multi` requires at least one.
-   **Input-file filters** — a non-empty `allow` list means only matching files are eligible; `exclude` is applied after `allow`. A pipeline whose filters exclude every selected input fails the execution.
-   **Pipeline references** — a `GLOBAL` workflow may reference only `GLOBAL` pipelines; a database workflow may reference `GLOBAL` or its own database's pipelines.

### Trigger chaining

By default a workflow does not fire on files produced by workflow executions -- only on user uploads
and other direct writes. This keeps automated output from re-entering the trigger system.

`allowWorkflowTriggerChaining` opts a workflow in to running on **another** workflow's output, which is
what lets a preview or metadata workflow act on a conversion pipeline's result:

| `allowWorkflowTriggerChaining` | File written by _this_ workflow | File written by _another_ workflow |
| ------------------------------ | ------------------------------- | ---------------------------------- |
| `false` (default)              | does not fire                   | does not fire                      |
| `true`                         | does not fire                   | fires when the filters match       |

A workflow never fires on its own output in either case, so a single workflow cannot loop on files it
produces. Enabling the setting on two or more workflows that each write a file the others accept can
still make them trigger one another indefinitely, so review the `inputFileFilters` of every workflow in
a chain before turning it on.

The built-in Potree point-cloud preview, 3D preview thumbnail, and GenAI 3D metadata labeling workflows
ship with chaining enabled, so a converted mesh or point cloud still receives a preview and metadata.

### Output path prefix

`outputFileBaseExecutionPathExtension` is the sub-path an execution's output files are written under,
relative to the output asset. It is inserted immediately **before each output file's own name**, so the
folder structure a pipeline creates is preserved and the prefix names the file's parent folder:

| Output file's relative path | Prefix    | Written to                   |
| --------------------------- | --------- | ---------------------------- |
| `/path1/path2/file.txt`     | `/YOLO/`  | `/path1/path2/YOLO/file.txt` |
| `/path1/path2/file.txt`     | `YOLO`    | `/path1/path2/YOLOfile.txt`  |
| `/a/b/c/d.glb`              | `/j-123/` | `/a/b/c/j-123/d.glb`         |

The trailing `/` is significant: with one the prefix is a folder, without one it is joined onto the
file name.

A workflow may declare a default in `systemConfig.defaultOutputFileBaseExecutionPathExtension`. The
default is stored **unresolved**, so its template tags are substituted per execution — a single stored
`/{{jobName}}/` gives every run its own output folder. Resolution follows the request:

| `outputFileBaseExecutionPathExtension` on the request | Prefix used                  |
| ----------------------------------------------------- | ---------------------------- |
| omitted (or `null`)                                   | the workflow's default       |
| `""` or `/`                                           | none — outputs at asset root |
| a value                                               | that value                   |

Sending an empty string is therefore how a caller opts out of a workflow's default. The recorded
output provenance uses the same placement as the write, so an output's `relativeFilePath` always
matches where the file actually landed.

## Consistency warnings

Create and update return a `warnings` array of non-fatal workflow-to-pipeline consistency notices. Warnings do not block the operation; they surface mismatches to review, such as a referenced pipeline that needs a metadata source the workflow gate has disabled, or a multi-file workflow that references a single-file pipeline.

```json
{
    "message": {
        "databaseId": "my-database",
        "workflowId": "convert-and-preview",
        "workflowName": "Convert and preview",
        "specifiedPipelines": [
            {
                "pipelineId": "3d-conversion-pipeline",
                "pipelineDatabaseId": "GLOBAL",
                "jobName": "convert"
            }
        ],
        "enabled": true,
        "archived": false,
        "warnings": [
            "Pipeline 'preview-pipeline' requests fileMetadata, but the workflow metadata gate has fileMetadata disabled."
        ]
    }
}
```

---

## Execute a workflow

Executes a workflow over a selected set of input files. The request is asset-less: input files are supplied in the body and may span assets (subject to the workflow's configuration). This starts a new Step Functions execution.

```
POST /workflows/{workflowDatabaseId}/{workflowId}/execute
```

Executing requires access to this route plus `GET` permission on the workflow, `GET` on every referenced pipeline, and `GET` on every input-file asset. The output asset is the only object the execution writes, so it requires `POST`. Because the execution does not change the workflow or pipeline definitions, no `POST` or `PUT` permission on those objects is needed — on a workflow or pipeline, `POST` grants creation.

### Path parameters

| Parameter            | Type   | Required | Description                                           |
| -------------------- | ------ | -------- | ----------------------------------------------------- |
| `workflowDatabaseId` | string | Yes      | Database ID of the workflow (use `GLOBAL` for global) |
| `workflowId`         | string | Yes      | Workflow identifier                                   |

### Request body

| Field                                  | Type   | Required | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| -------------------------------------- | ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFiles`                           | array  | No       | Selected input files (`0..N`; arity is enforced against the workflow/pipeline configuration). Each item has `databaseId`, `assetId`, `relativeFileKey`, and optional `versionId`.                                                                                                                                                                                                                                                                                   |
| `outputAssetId`                        | string | No       | Output asset. Honored whenever the input files do not resolve to a single input asset (regardless of override); for a single input asset only when the workflow's `outputTarget` allows override, otherwise the output is locked to the input asset. Omit for a results-only workflow. See [Output target](#output-target).                                                                                                                                         |
| `outputDatabaseId`                     | string | No       | Output database. When the input files resolve to zero or multiple assets, supply it together with `outputAssetId`. For a single-input-asset override it falls back to the input asset's database when omitted.                                                                                                                                                                                                                                                      |
| `outputFileBaseExecutionPathExtension` | string | No       | Base path (under the output asset) that output files are written beneath, inserted immediately before each output file's own name. May contain dynamic tag placeholders (e.g. `{{firstAssetFileFileNameNoExt}}`) resolved at launch. **Omit** to inherit the workflow's `defaultOutputFileBaseExecutionPathExtension`; send `""` or `/` to write at the asset root regardless. Must not contain `..` or backslashes. See [Output path prefix](#output-path-prefix). |
| `pipelineExecutionParameters`          | object | No       | Per-pipeline execution parameters, keyed by `pipelineId`. Each value may set `templateId`, `templateTags`, or a `customTemplateOverride`.                                                                                                                                                                                                                                                                                                                           |
| `executionGroupId`                     | string | No       | Group id for bulk grouping / abort-by-group.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `triggerType`                          | string | No       | `manual` (default) or `fileUpload`.                                                                                                                                                                                                                                                                                                                                                                                                                                 |

`relativeFileKey` is asset-relative (leading `/`); `/` selects the whole asset and `/folder/` a folder.

### Request body example

```json
{
    "inputFiles": [
        {
            "databaseId": "engineering",
            "assetId": "building-01",
            "relativeFileKey": "/models/building.fbx"
        }
    ],
    "pipelineExecutionParameters": {
        "convert-to-glb": {
            "templateId": "fbx-to-glb",
            "templateTags": [{ "key": "scale", "value": "1.0" }]
        }
    },
    "executionGroupId": "nightly-batch-2026-07"
}
```

:::note[Execution constraints]

-   Input files must exist; each is read from its own asset bucket.
-   Per-pipeline template resolution and tag validation run before launch, followed by cross-entity validation (input-file arity, asset scope, and file filters).
-   Every referenced pipeline must be enabled and not archived, and the workflow must be enabled and not archived.
-   The workflow's `concurrencyRestriction` may block a launch that conflicts with an already-running execution.
-   When the input files resolve to zero or multiple input assets, supply an explicit output target — both `outputAssetId` and `outputDatabaseId` — or configure the workflow as results-only (`outputTarget.locationType` `none`); otherwise the request is rejected. A results-only workflow rejects a supplied `outputAssetId`/`outputDatabaseId` as a contradiction. See [Output target](#output-target).
    :::

### Response

```json
{
    "message": {
        "executionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "executionGroupId": "nightly-batch-2026-07",
        "warnings": []
    }
}
```

### Error responses

| Status | Description                                                                  |
| ------ | ---------------------------------------------------------------------------- |
| `400`  | Validation, template-resolution, or cross-entity validation error            |
| `403`  | Not authorized (API, input asset, output asset, workflow, or pipeline level) |
| `429`  | Throttling -- too many requests                                              |
| `500`  | Internal server error or execution limit exceeded                            |

---

## List workflow executions for an asset

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

| Parameter         | Type   | Required | Default            | Description                                                                                                                      |
| ----------------- | ------ | -------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
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
                "workflowExecutionId": "a1b2c3d4-e5f6-7890",
                "executionStatus": "SUCCEEDED",
                "startDate": "2026-03-15T10:30:00Z",
                "stopDate": "2026-03-15T10:32:15Z",
                "executionStartDate": "2026-03-15T10:30:00Z",
                "executionStopDate": "2026-03-15T10:32:15Z",
                "triggerType": "Manual",
                "executionGroupId": "",
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

## List all executions (global)

Lists executions across all assets, not scoped to one asset. Results are permission-filtered: an execution is visible when the caller has `GET` on its workflow **and** `GET` on any of its input assets or its output asset.

The list shows recent executions by default — those started within the last 90 days. Supply `filterStartDate` (and optionally `filterEndDate`) to query an explicit date range. The applied window is echoed back as `filterStartDate` (and `filterEndDate` when supplied).

```
GET /workflows/executions
```

### Query parameters

| Parameter                     | Type    | Required | Description                                                                                                                      |
| ----------------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `maxItems` / `pageSize`       | integer | No       | Page size (capped; excess pages via `NextToken`).                                                                                |
| `startingToken` / `NextToken` | string  | No       | Pagination continuation token.                                                                                                   |
| `filterStartDate`             | string  | No       | ISO-8601 lower bound on execution start date; only executions started on or after this date are listed. Defaults to 90 days ago. |
| `filterEndDate`               | string  | No       | ISO-8601 upper bound on execution start date; only executions started on or before this date are listed.                         |
| `workflowId`                  | string  | No       | Filter by workflow id.                                                                                                           |
| `workflowDatabaseId`          | string  | No       | Filter by workflow database id.                                                                                                  |
| `status`                      | string  | No       | Filter by execution status.                                                                                                      |
| `triggerType`                 | string  | No       | Filter by trigger type (`Manual`, `File-Upload`).                                                                                |
| `groupId`                     | string  | No       | Filter by execution group id.                                                                                                    |
| `triggeredByUserId`           | string  | No       | Filter by the user who triggered the execution.                                                                                  |

### Response

```json
{
    "message": {
        "Items": [
            {
                "workflowExecutionId": "a1b2c3d4-e5f6-7890",
                "workflowId": "convert-and-preview",
                "workflowDatabaseId": "GLOBAL",
                "executionStatus": "SUCCEEDED",
                "executionStartDate": "2026-03-15T10:30:00Z",
                "executionStopDate": "2026-03-15T10:32:15Z",
                "triggerType": "Manual",
                "triggeredByUserId": "user@example.com",
                "executionGroupId": "",
                "outputLocationType": "asset",
                "outputAssetId": "x1a2b3c4-d5e6-7890",
                "outputDatabaseId": "my-database"
            }
        ],
        "filterStartDate": "2025-12-15T10:30:00Z",
        "NextToken": "…"
    }
}
```

Each row reports the run's output target: `outputLocationType` (`asset`, or `none` for a results-only
execution that writes no files), and `outputAssetId` / `outputDatabaseId` naming the destination. These
are empty strings for a results-only run. They are read from the execution's configuration record,
which the endpoint loads at most once per listed row and shares with the output-asset visibility check,
so reporting them costs no extra lookup.

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

| Parameter     | Type   | Required | Description                          |
| ------------- | ------ | -------- | ------------------------------------ |
| `executionId` | string | Yes      | Identifier of the execution to abort |

### Query parameters

| Parameter | Type   | Required | Description                                                                                                                                                                          |
| --------- | ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `groupId` | string | No       | When set, abort every active execution in this group (the path `executionId` is ignored). Group members the caller is not authorized on are skipped and counted, not reported by id. |

### Response

Aborting a single execution returns:

```json
{
    "message": "Execution aborted"
}
```

A single-execution abort may include a `warnings` array when a best-effort inner sub-process abort failed.

When `groupId` is supplied, every active execution in the group is aborted and the response reports each member's outcome:

```json
{
    "message": {
        "groupId": "nightly-batch-2026-07",
        "results": [
            { "executionId": "a1b2c3d4-e5f6-7890", "status": "aborted" },
            { "executionId": "b2c3d4e5-f6a7-8901", "status": "skipped-terminal" }
        ],
        "skippedInaccessibleCount": 1,
        "moreRemaining": true
    }
}
```

`skippedInaccessibleCount` (members the caller is not authorized on, counted but not identified) and `moreRemaining` (more active authorized members remain beyond this request's cap — re-invoke to continue) are present only when non-zero/applicable.

:::note[Authorization]
Aborting an execution requires `GET` permission on the execution's workflow and `POST` permission on every input-file asset tied to the execution. Because the execution does not modify the workflow definition, only read access to the workflow is required; because it affects the processed assets, write (`POST`) access to those assets is required.
:::

### Error responses

| Status | Description                                                     |
| ------ | --------------------------------------------------------------- |
| `400`  | Invalid or missing `executionId`                                |
| `403`  | Not authorized (API, workflow, or one of the input-file assets) |
| `404`  | Execution not found                                             |
| `429`  | Throttling -- too many requests                                 |
| `500`  | Internal server error                                           |

---

## Re-run an execution

Reconstructs the execute request from an execution's stored records and launches a new execution (new `executionId`). The caller must be able to view the original execution; the re-launch re-validates permissions against every referenced asset, workflow, and pipeline.

```
POST /workflows/executions/{executionId}/rerun
```

### Path parameters

| Parameter     | Type   | Required | Description                           |
| ------------- | ------ | -------- | ------------------------------------- |
| `executionId` | string | Yes      | Identifier of the execution to re-run |

### Request body

| Field              | Type   | Required | Description                              |
| ------------------ | ------ | -------- | ---------------------------------------- |
| `executionGroupId` | string | No       | Group id to assign to the new execution. |

### Error responses

| Status | Description                                   |
| ------ | --------------------------------------------- |
| `400`  | The reconstructed execution failed validation |
| `403`  | Not authorized                                |
| `404`  | Execution not found                           |
| `500`  | Internal server error                         |

---

## Permanently delete an execution

Removes only the DynamoDB records for an execution across all sub-tables. It does not touch the Step Functions execution history. The execution must not be in progress (abort it first). This is an admin-only action.

```
DELETE /workflows/executions/{executionId}/permanent
```

### Path parameters

| Parameter     | Type   | Required | Description                                       |
| ------------- | ------ | -------- | ------------------------------------------------- |
| `executionId` | string | Yes      | Identifier of the execution to permanently delete |

### Request body

| Field           | Type    | Required | Description                                       |
| --------------- | ------- | -------- | ------------------------------------------------- |
| `confirmDelete` | boolean | Yes      | Must be `true` to permanently delete the records. |

### Error responses

| Status | Description                                              |
| ------ | -------------------------------------------------------- |
| `400`  | Missing `confirmDelete`, or the execution is in progress |
| `403`  | Not authorized                                           |
| `404`  | Execution not found                                      |
| `500`  | Internal server error                                    |

---

## Get execution details

Returns the full detail and input/output traceability for a single execution, including the underlying pipelines (with status, timing, and the exact rendered configuration each pipeline received), input files, input metadata, input configurations, the execution's output target, and a listing of all outputs (files, metadata, and results). Each output file/metadata entry carries the `pipelineId` of the pipeline that produced it. Pipeline names and descriptions are resolved from the pipeline definitions, and the workflow description from the workflow definition.

```
GET /workflows/executions/{executionId}/details
```

The route is keyed on the execution identifier because an execution may span input files across multiple assets.

### Path parameters

| Parameter     | Type   | Required | Description          |
| ------------- | ------ | -------- | -------------------- |
| `executionId` | string | Yes      | Execution identifier |

### Response

```json
{
    "message": {
        "executionId": "a1b2c3d4e5f6",
        "workflowId": "convert-and-preview",
        "workflowDatabaseId": "GLOBAL",
        "workflowName": "Convert and preview",
        "workflowDescription": "Convert 3D files and generate preview thumbnails",
        "executionStatus": "SUCCEEDED",
        "executionStartDate": "2026-06-16T00:00:00Z",
        "executionStopDate": "2026-06-16T00:05:00Z",
        "triggerType": "Manual",
        "triggeredByUserId": "user@example.com",
        "executionError": "",
        "outputLocationType": "asset",
        "outputDatabaseId": "my-database",
        "outputAssetId": "a1b2c3",
        "outputFileBaseExecutionPathExtension": "/",
        "pipelines": [
            {
                "pipelineId": "3d-conversion-pipeline",
                "pipelineDatabaseId": "GLOBAL",
                "pipelineExecutionId": "p1a2b3",
                "name": "3d-conversion-pipeline",
                "description": "Converts 3D files to glTF",
                "pipelineType": "conversion",
                "pipelineExecutionType": "Lambda",
                "endStatePipeline": true,
                "executionStatus": "SUCCEEDED",
                "executionStartDate": "2026-06-16T00:00:05Z",
                "executionStopDate": "2026-06-16T00:04:50Z",
                "renderedConfig": "{\"outputFormat\": \"gltf\"}",
                "renderedConfigTruncated": false,
                "templateId": "high-quality",
                "templateTags": [{ "key": "scale", "value": "1.0" }],
                "customTemplateOverrideUsed": false,
                "configFormat": "json"
            }
        ],
        "inputFiles": [
            {
                "databaseId": "my-database",
                "assetId": "a1b2c3",
                "inputAssetFileKey": "/models/building.fbx",
                "versionId": "PvT3.K9mZ0xq1aBcd2EfGhI"
            }
        ],
        "inputMetadata": [
            {
                "databaseId": "my-database",
                "assetId": "a1b2c3",
                "filePath": "/",
                "metadata": { "site": "north" }
            }
        ],
        "inputConfigurations": [
            {
                "pipelineId": "3d-conversion-pipeline",
                "inputConfiguration": "",
                "inputConfigurationTruncated": false
            }
        ],
        "outputs": {
            "files": [
                {
                    "relativeFilePath": "/models/building.gltf",
                    "fileType": "file",
                    "fileSize": 20480,
                    "contentType": "model/gltf-binary",
                    "assetId": "building-001",
                    "databaseId": "default",
                    "assetFileVersionId": "PvT3.K9mZ0xq1aBcd2EfGhI"
                }
            ],
            "metadata": [],
            "results": [
                {
                    "relativeFilePath": "/models/building.report.json",
                    "resultsContent": "{\"triangles\": 18204, \"status\": \"ok\"}",
                    "resultsContentTruncated": false
                }
            ]
        },
        "truncatedCollections": []
    }
}
```

:::note[Running executions and per-pipeline status]
The details endpoint works for both running and completed executions. The top-level `executionStatus` reflects the live state: `RUNNING` while the workflow is in progress, then a terminal status (`SUCCEEDED`, `FAILED`, `ABORTED`, `TIMED_OUT`) when it finishes; a non-terminal record is reconciled against AWS Step Functions on read so a stopped run never remains `RUNNING`.

Each entry in `pipelines[]` carries its own `executionStatus` that advances through the workflow: a pipeline is `NEW` (queued) until it starts, `RUNNING` while it executes, and `SUCCEEDED`/`FAILED` when it finishes. This lets a client show which pipeline of the workflow is currently running. Outputs appear as each pipeline completes.

Each entry also carries `pipelineType`, which reports the referenced pipeline's free-text `category` label (empty when the pipeline sets no category, or when its definition no longer exists). It is a display label, not an enumerated value. `pipelineExecutionType` carries the pipeline's execution type (`Lambda`, `SQS`, `EventBridge`, or `DeadlineCloud`).
:::

:::note[Traceability, not internals]
The response is scoped to input/output traceability. Internal details — Step Functions and resource ARNs, temporary and auxiliary S3 input/output locations, and credential-vending fields — are intentionally omitted. Output file size and content type are included when still available; a lifecycle policy may expire temporary output files, in which case only the relative path and type are returned.

Each input file carries the concrete S3 `versionId` the run read, resolved when the execution launched — the exact version processed. It is empty for folder or whole-asset selections, which have no single version.

For executions whose output target is an asset, each output file carries the target asset identity — `assetId` and `databaseId` — derived from the execution's output target. When a matching file version-history record exists, `assetFileVersionId` is also added, identifying the specific S3 file version the execution wrote. `assetFileVersionId` is absent for outputs with no history record (for example, executions that ran before file version history was recorded).

`results` lists structured result files a pipeline emits to the execution's `results/` output folder (as opposed to asset files). Each entry carries the file's path relative to that folder (`relativeFilePath`), the file content (`resultsContent`), and `resultsContentTruncated`, which is `true` when the stored content was truncated to fit the field limit.
:::

### Error responses

| Status | Description                                                     |
| ------ | --------------------------------------------------------------- |
| `400`  | Invalid or missing `executionId`                                |
| `403`  | Not authorized (API, workflow, or one of the input-file assets) |
| `404`  | Execution not found                                             |
| `500`  | Internal server error                                           |

---

## Get execution logs

Returns logs for an execution in one of two modes. Logs are always scoped to the requested execution; supplying a `pipelineExecutionId` narrows the result to that single pipeline execution.

Returned log text is redacted: credential-bearing values — authorization headers, bearer tokens, AWS access-key IDs, JSON web tokens, and labelled secret fields such as `SecretAccessKey` and `SessionToken` — are replaced with `<redacted>` before the logs are stored or returned.

```
GET /workflows/executions/{executionId}/logs
```

### Path parameters

| Parameter     | Type   | Required | Description          |
| ------------- | ------ | -------- | -------------------- |
| `executionId` | string | Yes      | Execution identifier |

### Query parameters

| Parameter             | Type   | Required | Default     | Description                                                                                                                                      |
| --------------------- | ------ | -------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mode`                | string | No       | `truncated` | `truncated` returns the stored log text, falling back to a live search when it is empty; `full` always runs a live Amazon CloudWatch Logs search |
| `pipelineExecutionId` | string | No       | —           | Narrow the logs to a single pipeline execution of this execution                                                                                 |
| `filterPattern`       | string | No       | —           | (`full` mode) Additional CloudWatch Logs filter pattern, AND-ed with the execution/pipeline scope                                                |
| `startTime`           | number | No       | —           | (`full` mode) Start of the time range, epoch milliseconds                                                                                        |
| `endTime`             | number | No       | —           | (`full` mode) End of the time range, epoch milliseconds                                                                                          |
| `limit`               | number | No       | `100`       | (`full` mode) Maximum number of events to return                                                                                                 |
| `nextToken`           | string | No       | —           | (`full` mode) Pagination token from a previous response                                                                                          |

### Response (truncated mode)

```json
{
    "message": {
        "mode": "truncated",
        "executionLog": "...execution log text...",
        "executionError": "",
        "logsSource": "stored"
    }
}
```

The stored execution log is captured as the run completes, before Amazon CloudWatch Logs finishes ingesting the run's events, so it is frequently empty even for a succeeded run. When the stored log for the requested scope is empty, truncated mode transparently falls back to a live CloudWatch Logs search for the same scope. For the whole execution, when that search is also empty it falls back to the Step Functions execution history — the authoritative record of the run's state transitions, available immediately with no ingestion lag. `logsSource` reports the origin of the returned text: `"stored"`, `"live"` (CloudWatch), or `"sfnHistory"` (Step Functions execution history).

When `pipelineExecutionId` is supplied in truncated mode, the stored per-pipeline log is returned instead (with the same live fallback and `logsSource`):

```json
{
    "message": {
        "mode": "truncated",
        "pipelineExecutionId": "p1a2b3",
        "resultLog": "...",
        "errorLog": "",
        "logsSource": "stored"
    }
}
```

### Response (full mode)

```json
{
    "message": {
        "mode": "full",
        "pipelineExecutionId": "",
        "events": [{ "timestamp": 1718496000000, "message": "..." }],
        "sfnHistoryEvents": [
            { "timestamp": 1718496000000, "message": "TaskStateEntered: Convert" }
        ],
        "subProcessEvents": [
            { "timestamp": 1718496000000, "message": "...", "logGroupArn": "..." }
        ],
        "nextToken": null
    }
}
```

For the whole execution (no `pipelineExecutionId`), a full-mode response also includes `sfnHistoryEvents` — the Step Functions execution history rendered as a state-transition timeline. When `pipelineExecutionId` is supplied, `subProcessEvents` carries any logs the pipeline registered, plus — for a pipeline step that runs its own Step Functions sub-execution — that sub-execution's history and the resolved log group of its state machine.

:::note[Scope]
A full-mode CloudWatch search is always restricted to the requested execution within the shared workflow log group. When `pipelineExecutionId` is supplied, the search is further restricted to that single pipeline execution — logs from other pipelines or executions are never returned.
:::

### Error responses

| Status | Description                                                     |
| ------ | --------------------------------------------------------------- |
| `400`  | Invalid or missing `executionId`, or invalid `mode`             |
| `403`  | Not authorized (API, workflow, or one of the input-file assets) |
| `404`  | Execution (or specified pipeline execution) not found           |
| `500`  | Internal server error                                           |

---

## Related resources

-   [Pipelines API](pipelines.md) -- Define the individual pipeline steps used in workflows
-   [Assets API](assets.md) -- Manage the assets that workflows process
-   [Asset Versions API](asset-versions.md) -- Manage version snapshots of processed assets
-   [Subscriptions API](subscriptions.md) -- Subscribe to asset version change notifications
