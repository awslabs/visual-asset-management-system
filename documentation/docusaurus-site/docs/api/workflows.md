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

| Parameter         | Type   | Required | Default | Description                                                                                                                                                      |
| ----------------- | ------ | -------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `maxItems`        | number | No       | `100`   | Maximum number of items to return                                                                                                                                |
| `pageSize`        | number | No       | `100`   | Number of items per page                                                                                                                                         |
| `startingToken`   | string | No       | `null`  | Pagination token from previous response                                                                                                                          |
| `includeArchived` | string | No       | `false` | Include archived workflows                                                                                                                                       |
| `hasTriggers`     | string | No       | --      | `true` returns only workflows with at least one **enabled** trigger; `false` only those with none. Any other value is rejected with a `400` rather than ignored. |

Each returned workflow carries `triggerCount` and `triggersEnabledCount`. Both are reported because they
differ when a trigger exists but is switched off — the state behind a workflow that looks configured yet
never fires. They are best-effort: a workflow whose triggers could not be read returns `null` for both
and is never dropped from a filtered result.

:::note[Filtering happens after the page is read]
Triggers live in their own table, so "has an enabled trigger" is not expressible as a condition on the
workflow record. A filtered page can therefore return fewer items than `pageSize` while still reporting a
`NextToken` — page until the token is absent, exactly as with the authorization filter.
:::

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
                "executionCount": 42,
                "triggerCount": 1,
                "triggersEnabledCount": 1
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

| Parameter         | Type   | Required | Default | Description                                                      |
| ----------------- | ------ | -------- | ------- | ---------------------------------------------------------------- |
| `includeArchived` | string | No       | `false` | Include archived workflows                                       |
| `hasTriggers`     | string | No       | --      | Filter on enabled triggers (`true`/`false`), as described above. |

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
            "fileAttributes": false,
            "databaseMetadata": true
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

A workflow may carry **several triggers of one type**, each with its own input-file filters and its own default templates, so one workflow can respond differently to different uploads. An upload launches the workflow once for every trigger whose filters match it.

Each trigger is addressed by its key, which is the `triggerType` path parameter and the `triggerType` field in a response:

| Key form             | Addresses                                                   |
| -------------------- | ----------------------------------------------------------- |
| `fileUpload`         | The workflow's first trigger of that type                   |
| `fileUpload#nightly` | An additional trigger of that type, identified by `nightly` |

A response also reports `triggerBaseType` (the plain type, for grouping and display) and `triggerId` (empty for the first trigger of a type), so a client never has to parse the key.

Two conditions are rejected with `400`:

-   **A workflow that serializes runs per asset supports only one trigger of a type.** When `concurrencyRestriction` is `perAsset`, several triggers firing the same workflow would contend on that asset. `perInputFile` is not restricted this way — overlapping filters there are caught by the execution's own per-file check, which fails that trigger's execution rather than the save.
-   **Two triggers of one type may not name the same default templates.** The templates are what distinguish them, so the same set twice is the same trigger declared twice. This includes two triggers that both name no templates: naming none is a valid choice when no pipeline requires one, which makes it a comparable value.

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

| Parameter     | Type   | Required | Description                                                                                                                                   |
| ------------- | ------ | -------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`  | string | Yes      | Database identifier                                                                                                                           |
| `workflowId`  | string | Yes      | Workflow identifier                                                                                                                           |
| `triggerType` | string | Yes      | The trigger's key: the bare type (`fileUpload`) for the workflow's first trigger of that type, or `<type>#<triggerId>` for an additional one. |

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

| Parameter     | Type   | Required | Description                                                                                                                                   |
| ------------- | ------ | -------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`  | string | Yes      | Database identifier                                                                                                                           |
| `workflowId`  | string | Yes      | Workflow identifier                                                                                                                           |
| `triggerType` | string | Yes      | The trigger's key: the bare type (`fileUpload`) for the workflow's first trigger of that type, or `<type>#<triggerId>` for an additional one. |

#### Request body

| Field                | Type    | Required | Description                                                                                                                                                                                                                                                                                                      |
| -------------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileFilters`   | object  | No       | `allow` and `exclude` arrays matching by extension (`*.glb`), path, name, or wildcard (`*.previewFile.*`); case-insensitive. Omitted means the trigger fires on any uploaded file. Follows the same rules as [Input-file filters](#input-file-filters), including the rejection of a match-everything `exclude`. |
| `defaultTemplateIds` | object  | No       | Template used for each included pipeline when the trigger launches, keyed by `<pipelineDatabaseId>:<pipelineId>`.                                                                                                                                                                                                |
| `enabled`            | boolean | No       | Whether the trigger is enabled (default `true`).                                                                                                                                                                                                                                                                 |

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

| Parameter     | Type   | Required | Description                                                                                                                                   |
| ------------- | ------ | -------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`  | string | Yes      | Database identifier                                                                                                                           |
| `workflowId`  | string | Yes      | Workflow identifier                                                                                                                           |
| `triggerType` | string | Yes      | The trigger's key: the bare type (`fileUpload`) for the workflow's first trigger of that type, or `<type>#<triggerId>` for an additional one. |

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
| `jobName`            | string | No       | Label for this pipeline step within the workflow. See below.                     |

A `jobName` names the step in the workflow's state machine and becomes a segment of the step's output paths (`pipelines/{pipelineName}/{jobName}/output/{executionId}/files/`). When empty or omitted, the pipeline's id is used in its place, so each step's outputs stay in a distinct folder — omitting it is the normal choice.

Supply one when the pipeline id alone would not identify the step: a general-purpose pipeline used for a narrower purpose in this workflow, a pipeline whose id is generated or abbreviated, or output that downstream tooling locates by S3 prefix and therefore benefits from a stable, meaningful folder name.

The value is 3–63 characters of letters, numbers, hyphens, and underscores. It is a fixed label rather than a template: `{{tag}}` placeholders are not substituted in a `jobName` and are rejected, because the name is written into the state machine when the workflow is deployed rather than resolved per execution. Use the workflow's `defaultOutputFileBaseExecutionPathExtension`, or an execution's `outputFileBaseExecutionPathExtension`, to vary the output path per run.

:::warning[A job name is part of the output path]
Changing a `jobName` on an existing workflow redirects where subsequent output is written. Output already written remains at its original path, splitting the workflow's history across two folders.
:::

## System configuration

The `systemConfig` object describes how a workflow consumes input, which asset selections it accepts, and where it writes output.

| Field                                         | Type    | Description                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFileArity`                              | string  | Number of input files the workflow consumes: `none` (no input file), `one` (exactly one), or `multi` (one or more).                                                                                                                                                                                                                                                                                                                      |
| `assetScope`                                  | object  | Booleans `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed`, and `folderAllowed` controlling accepted asset selections. See [Asset scope](#asset-scope).                                                                                                                                                                                                                                                                        |
| `metadataInputs`                              | object  | Booleans `assetMetadata`, `fileMetadata`, `fileAttributes`, and `databaseMetadata` — which metadata is gathered and passed to the pipelines. See [Metadata inputs](#metadata-inputs).                                                                                                                                                                                                                                                    |
| `inputFileFilters`                            | object  | `allow` and `exclude` arrays. Each entry matches by extension (`*.glb`, with `.glb` also accepted as shorthand), exact path (`/models/x.glb`), file name, or wildcard (`*.previewFile.*`, `/models/*`). Matching is case-insensitive. See [Input-file filters](#input-file-filters).                                                                                                                                                     |
| `concurrencyRestriction`                      | string  | How concurrent executions are limited: `none`, `perAsset`, or `perInputFile`.                                                                                                                                                                                                                                                                                                                                                            |
| `outputTarget`                                | object  | Where the workflow writes its output. See [Output target](#output-target).                                                                                                                                                                                                                                                                                                                                                               |
| `allowWorkflowTriggerChaining`                | boolean | Whether a file written by **another** workflow's execution may fire this workflow's triggers -- for example generating a preview or metadata from a conversion pipeline's output. A workflow never fires on output it wrote itself, whatever this is set to, so it cannot re-trigger itself in a loop. A chained file must still match the trigger's `inputFileFilters`. Defaults to `false`. See [Trigger chaining](#trigger-chaining). |
| `defaultOutputFileBaseExecutionPathExtension` | string  | The output path prefix an execution uses when its request supplies none. Stored **unresolved**, so `{{tag}}` placeholders resolve per run — one stored `/{{jobName}}/` gives every execution its own output folder. Empty means no default. See [Output path prefix](#output-path-prefix).                                                                                                                                               |

### Input-file filters

Both lists in an `inputFileFilters` block are optional, and an absent list is not the same as an empty match:

| List      | Meaning                                                                                                                                            |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `allow`   | Eligible file types. Omitted, empty, or a match-everything pattern (`*`, `**`, `*.*`, `/*`, `/**`) means every file is eligible **at that level**. |
| `exclude` | Files removed from that set, applied after `allow` so an exclusion always wins. Omitted or empty excludes nothing.                                 |

A filter therefore only ever narrows eligibility, never grants it.

A match-everything pattern in an `exclude` list is **rejected** when the pipeline, template, workflow, or trigger is saved: because `exclude` is applied last, it would remove every file and leave the pipeline or workflow permanently unable to run. Leave the list empty to exclude nothing.

Filters resolve down a three-level chain, and an open list defers to the next level:

1. **Workflow** — when its `allow` list names specific types, that list bounds the whole execution and no pipeline can widen it. When it is open, eligibility comes from the pipelines instead, where a file is eligible if **any** step accepts it.
2. **Pipeline** — what that individual step accepts.
3. **Template `overrides.inputFileFilters`** — replaces its pipeline's list entirely for executions using that template.

`exclude` lists accumulate across all three levels.

At execute time the selected files are narrowed by the workflow's filters first, and each pipeline is then checked against that narrowed set. If any pipeline is left without the input it requires, the request is rejected rather than launching a step that cannot run.

A workflow response also reports `aggregateWorkflowPipelineInputFileFilters`, the restriction the workflow effectively imposes, with `source` naming whether it came from the workflow or its pipelines.

:::note[The aggregate excludes template overrides]
`includesTemplateOverrides` is always `false` — a template is chosen per execution, so its overrides cannot be known in advance. Use the aggregate to describe a workflow, and resolve the full chain including the chosen template when validating a specific file selection.
:::

### Asset scope

`assetScope` constrains which input-file selections an execution may make; each rule is enforced at execute time:

-   **`crossAssetAllowed`** — permit input files spanning more than one asset. When `false`, all input files must belong to a single asset.
-   **`singleAssetOnly`** — reject an execution whose input files reference more than one asset. This is the inverse of `crossAssetAllowed`; set exactly one of the two intents (see [Field rules](#field-rules-and-restrictions)).
-   **`wholeAssetAllowed`** — permit a `/` selection meaning every file in the asset.
-   **`folderAllowed`** — permit a `/folder/` selection meaning every file under a folder.

The two span keys — `crossAssetAllowed` and `singleAssetOnly` — bound the **metadata-source assets** as well as the input files. A workflow declaring `singleAssetOnly` accepts at most one asset in `metadataSourceAssets`, and one declaring `crossAssetAllowed` `false` likewise; an execution naming several is rejected with `400`. The span is evaluated by the same rule the input files pass through, so both selections read one interpretation of `assetScope`: a scope that sets both keys resolves to the stricter one, admitting a single source asset. `wholeAssetAllowed` and `folderAllowed` do not apply — a metadata source is an entity with no file key, so there is no selection shape for them to describe.

### Metadata inputs

`metadataInputs` is four independent booleans naming the metadata an execution gathers and hands to its pipelines:

| Key                | Metadata gathered                      |
| ------------------ | -------------------------------------- |
| `assetMetadata`    | Each involved asset's own metadata.    |
| `fileMetadata`     | Each input file's metadata.            |
| `fileAttributes`   | Each input file's attributes.          |
| `databaseMetadata` | Each involved database's own metadata. |

Every key defaults to `true`, so the map is a list of opt-outs rather than opt-ins: a key the map omits is gathered. Create and update store `systemConfig` as sent, so a request naming only some keys persists exactly those and the rest keep their default — sending `{"fileMetadata": false}` suppresses file metadata and leaves the other three on. This is also what keeps a configuration written before a key existed working: the key it cannot carry reads as its default rather than as an opt-out.

The workflow's booleans are the outer gate; each pipeline's own `metadataInputs` decides what that step receives. A type reaches a pipeline only when both have it on, so a workflow that gates a type off suppresses it for every step. A workflow response reports the resolved combination as `aggregateWorkflowPipelineMetadataInputs`, with `gatedOffByWorkflow` naming the types a pipeline asked for but the workflow suppresses.

`databaseMetadata` is read-only. Database metadata is supplied to a pipeline as input; an execution never writes metadata back to a database. Pipeline metadata write-back targets assets and files only.

#### Which entities an execution captures

The entities a run gathers metadata from follow from the run's own selection:

-   **Assets** — every asset an input file belongs to, plus every asset named in `metadataSourceAssets`, de-duplicated.
-   **Files** — every selected input file, for `fileMetadata` and `fileAttributes`.
-   **Databases** — when the run has input files, every distinct database of those files' assets together with the databases of any assets named as metadata sources. When the run has no input files, the single database named in `metadataSourceDatabaseId`.

`metadataSourceDatabaseId` applies only to a run with no input files. A run that selects input files derives its databases from those files, so naming one has no effect.

A run over three databases holding five assets and ten files therefore captures metadata for three databases, five assets, and ten files.

#### Naming metadata sources is optional

`metadataSourceDatabaseId` and `metadataSourceAssets` are always optional, at every arity. Nothing requires an execution to name a metadata source, and no configuration can make one mandatory — a pipeline that needs particular metadata to run checks for it itself and fails its own step when it is absent.

A metadata source is an entity, never a file. Sources carry no file key, are exempt from input-file arity, asset scope, and input-file filters, and take no part in resolving the output target. This is what lets a workflow with `inputFileArity: none` name the assets and database whose metadata its pipelines read.

Executing with metadata sources requires `GET` permission on every named source asset and on every database whose metadata the run captures. A named database the caller cannot read fails the request; a database derived from the input files that the caller cannot read is skipped, and the execution proceeds without that database's metadata.

#### Metadata limits

The metadata captured for one entity is bounded, so a run that spans many entities leaves each entity its own budget:

| Bound                       | Limit                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Metadata entries per entity | 1,000 — applied independently to each database, each asset, each file's metadata, and each file's attributes. |
| Metadata size per entity    | 300 KB, measured over the retained entries.                                                                   |
| Metadata size per execution | 128 MB across every entity together, plus the involved assets' own asset data.                                |
| Input files per execution   | 1,000 individually specified entries in `inputFiles`.                                                         |
| Metadata-source assets      | 1,000 in `metadataSourceAssets`.                                                                              |

A run over three databases, five assets, and ten files therefore captures up to 1,000 metadata entries for each of the three databases, up to 1,000 for each of the five assets, and up to 1,000 for each of the ten files — each entity is measured on its own, not against a shared per-entity total.

The input-file bound counts the **entries in the request**, not the files a run ultimately reads. A whole-asset selection (`relativeFileKey` of `/`) and a folder selection (a key ending in `/`) are one entry each and are expanded when the execution launches, so an asset holding tens of thousands of files is a single entry. Files a pipeline reads for itself from the asset directory once it is running are outside the request entirely and are not counted. Reach this bound only by enumerating more than 1,000 individual files in one request; select the folder or the whole asset instead.

Both entity bounds apply together: entries are retained in key order until either the entry count or the byte budget is reached, so the same entity yields the same subset on every run and on a re-run. A single entry too large for the whole byte budget is skipped rather than ending the walk, so one oversized value costs only itself.

##### The total bound

The per-entity bounds limit each row but not how many rows a run has, so a 128 MB bound covers one execution's metadata as a whole. It engages only on a genuinely pathological run: a production asset carries thousands of files with hundreds of metadata entries each, and the bound is sized to clear a run selecting the full 1,000-file input allowance at several hundred entries per file, so ordinary work — however heavily tagged — is captured whole.

When a run does exceed it, entity rows are emptied narrowest-first: each file's attributes, then each file's metadata, then the assets' own `/` rows, then the databases'. A row that does not fit is emptied rather than partially kept — a half-populated metadata map reads as complete to a pipeline, whereas an empty one carries the same meaning as an entity that simply holds no metadata. An emptied row writes no metadata row and is read as an entity with no metadata, so pipelines need no special handling. The walk continues past a row that does not fit, so one oversized entity does not discard the smaller rows after it.

An execution never truncates silently. When an entity is bounded, the execute response returns a warning naming the entity, and a run that bounds many entities returns one warning naming the first few with a count of the rest. Rows emptied by the total bound are reported the same way, as one warning naming up to five entities and counting the remainder. A metadata-source database whose metadata cannot be read is reported the same way too, and contributes no metadata rather than failing the launch.

### Output target

`outputTarget` is an object of `locationType` and `allowOverride` that controls where an execution writes its output.

-   **`locationType`** — `asset` (default) writes the workflow's asset files and metadata to a VAMS asset. `none` is results-only: the workflow writes no asset files or metadata and records only results text and logs against the execution transaction — for example, analyzing input files and emitting a metadata report. A results-only (`none`) workflow **may still take input files** (its `inputFileArity` can be `none`, `one`, or `multi`); its executions write no asset output and supply no `outputAssetId`/`outputDatabaseId`. When `locationType` is `asset` and `inputFileArity` is `none` (no input file to lock the output to), `allowOverride` must be `true` so an output asset can be chosen at execution time.
-   **`allowOverride`** — gates redirecting the output when an execution's input files resolve to exactly one input asset. With a single input asset the output is locked to that asset; `allowOverride` `true` lets the execute request redirect it via `outputAssetId`/`outputDatabaseId` (an omitted `outputDatabaseId` falls back to the input asset's database). With `allowOverride` `false`, an execute request that names a **different** output target is rejected rather than relocked to the input asset, so the caller is never told a run launched to a destination it did not ask for; naming the input asset itself is accepted as a no-op, which is what lets a re-run replay its recorded target. When the input files resolve to zero or multiple input assets there is no asset to lock to, so an explicit output target — both `outputAssetId` and `outputDatabaseId` — is honored regardless of `allowOverride`; supply both, or configure the workflow as results-only.

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

Executing requires access to this route plus `GET` permission on the workflow, `GET` on every referenced pipeline, `GET` on every input-file asset, and `GET` on every asset and database named as a metadata source. The output asset is the only object the execution writes, so it requires `POST`. Because the execution does not change the workflow or pipeline definitions, no `POST` or `PUT` permission on those objects is needed — on a workflow or pipeline, `POST` grants creation.

### Path parameters

| Parameter            | Type   | Required | Description                                           |
| -------------------- | ------ | -------- | ----------------------------------------------------- |
| `workflowDatabaseId` | string | Yes      | Database ID of the workflow (use `GLOBAL` for global) |
| `workflowId`         | string | Yes      | Workflow identifier                                   |

### Request body

| Field                                  | Type   | Required | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------------------------------- | ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inputFiles`                           | array  | No       | Selected input files (`0..N`; arity is enforced against the workflow/pipeline configuration). Each item has `databaseId`, `assetId`, `relativeFileKey`, and optional `versionId`. `versionId` is the **S3 object version of that file** (as returned by `GET /database/{databaseId}/assets/{assetId}/fileInfo?includeVersions=true`), not an asset version number; omit it to read whatever version is current when the execution launches. A `versionId` that names no readable version of the key — including a value from the asset-version list — is reported as a missing input. |
| `metadataSourceDatabaseId`             | string | No       | A database whose own metadata is captured as an execution input. Applies only to a run with **no** input files: a run that selects input files derives its databases from those files instead. `GLOBAL` is rejected — it is the unscoped keyword rather than a database whose metadata can be read. See [Metadata inputs](#metadata-inputs).                                                                                                                                                                                                                                          |
| `metadataSourceAssets`                 | array  | No       | Assets whose own metadata is captured as an execution input (`0..1000`). Each item has `databaseId` and `assetId` and carries no file key. A source is an entity, not an input file: it is exempt from arity, asset scope, and input-file filters, and takes no part in resolving the output target. See [Metadata inputs](#metadata-inputs).                                                                                                                                                                                                                                         |
| `outputAssetId`                        | string | No       | Output asset. Honored whenever the input files do not resolve to a single input asset (regardless of override); for a single input asset only when the workflow's `outputTarget` allows override, otherwise the output is locked to the input asset. Omit for a results-only workflow. See [Output target](#output-target).                                                                                                                                                                                                                                                           |
| `outputDatabaseId`                     | string | No       | Output database. When the input files resolve to zero or multiple assets, supply it together with `outputAssetId`. For a single-input-asset override it falls back to the input asset's database when omitted.                                                                                                                                                                                                                                                                                                                                                                        |
| `outputFileBaseExecutionPathExtension` | string | No       | Base path (under the output asset) that output files are written beneath, inserted immediately before each output file's own name. May contain dynamic tag placeholders (e.g. `{{firstAssetFileFileNameNoExt}}`) resolved at launch. **Omit** to inherit the workflow's `defaultOutputFileBaseExecutionPathExtension`; send `""` or `/` to write at the asset root regardless. Must not contain `..` or backslashes. See [Output path prefix](#output-path-prefix).                                                                                                                   |
| `pipelineExecutionParameters`          | object | No       | Per-pipeline execution parameters, keyed by `pipelineId`. Each value may set `templateId`, `templateTags`, or a `customTemplateOverride`.                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `executionGroupId`                     | string | No       | Group id for bulk grouping / abort-by-group.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `triggerType`                          | string | No       | `manual` (default) or `fileUpload`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

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
    "metadataSourceAssets": [{ "databaseId": "reference", "assetId": "site-survey" }],
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
-   Metadata sources are optional and take no part in arity or input-file filters. The `assetScope` **span** keys do bound them: a workflow declaring `singleAssetOnly` (or `crossAssetAllowed` `false`) rejects an execution naming more than one asset in `metadataSourceAssets`. See [Metadata inputs](#metadata-inputs) for which entities a run captures and the limits it applies, and [Asset scope](#asset-scope) for the span rule.
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

`warnings` carries the non-fatal observations about the launch:

-   a pipeline that declares a metadata type the run named no source for;
-   an entity whose metadata was bounded by the [per-entity limits](#metadata-limits), and an entity whose metadata was emptied by the [total bound](#the-total-bound);
-   a metadata-source database whose metadata could not be read;
-   a `metadataSourceDatabaseId` the run did not use. Sending it together with `inputFiles` succeeds — a run with input files derives its metadata-source databases from those files' assets — and the warning names the database that was ignored, so the setting is not silently dropped.

The execution starts in every case.

### Error responses

| Status | Description                                                                                                                                       |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Validation, template-resolution, or cross-entity validation error, including `metadataSourceAssets` spanning more assets than `assetScope` allows |
| `403`  | Not authorized (API, input asset, metadata-source asset or database, output asset, workflow, or pipeline level)                                   |
| `429`  | Throttling -- too many requests                                                                                                                   |
| `500`  | Internal server error or execution limit exceeded                                                                                                 |

---

## List workflow executions for an asset

Retrieves execution history for workflows on a specific asset, in both directions: executions that read the asset as an **input** and executions whose **output target** was the asset. A conversion that wrote a file into this asset without reading anything from it therefore appears here alongside the runs that consumed it.

Both directions are merged into one listing ordered newest-first by `executionStartDate`, so a run that wrote into the asset appears in date order among the runs that read from it. An execution that both read from and wrote to the asset is returned once.

```
GET /database/{databaseId}/assets/{assetId}/workflows/executions
```

To filter by a specific workflow, either name it in the path or pass it as a query parameter:

```
GET /database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}
GET /database/{databaseId}/assets/{assetId}/workflows/executions?workflowId={workflowId}&workflowDatabaseId={databaseId}
```

The two forms differ in how the workflow is matched. The path form takes its companion `workflowDatabaseId` from the request body and compares the two as a joined key, so it identifies exactly one workflow but requires both halves. The query form matches each parameter independently, so `workflowId` on its own lists that workflow's executions across every database. Prefer the query form from a browser: a `GET` request cannot carry a body.

A workflow ID is unique only within its database, so pass both parameters to narrow to a single workflow when the same ID exists in more than one.

### Path parameters

| Parameter    | Type   | Required | Description           |
| ------------ | ------ | -------- | --------------------- |
| `databaseId` | string | Yes      | Database identifier   |
| `assetId`    | string | Yes      | Asset identifier      |
| `workflowId` | string | No       | Filter by workflow ID |

### Query parameters

Each filter is optional and all supplied filters are AND-ed.

| Parameter            | Type   | Required | Default            | Description                                                                                                                                                                                 |
| -------------------- | ------ | -------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `filterStartDate`    | string | No       | 90 days before now | UTC lower bound on execution start date, of the form `YYYY-MM-DDTHH:MM:SSZ`; only executions started on or after this date are listed. Any other form returns 400. Defaults to 90 days ago. |
| `workflowId`         | string | No       | --                 | List only executions of this workflow. An ID that does not match the ID pattern returns 400 rather than an empty list, so a typo is distinguishable from no matching history.               |
| `workflowDatabaseId` | string | No       | --                 | List only executions of workflows in this database. Accepts `GLOBAL` for the shared workflow catalog.                                                                                       |
| `status`             | string | No       | --                 | List only executions with this `executionStatus` (for example `RUNNING`, `SUCCEEDED`, `FAILED`).                                                                                            |
| `triggerType`        | string | No       | --                 | List only executions with this trigger type (`Manual` or `File-Upload`).                                                                                                                    |
| `groupId`            | string | No       | --                 | List only executions in this execution group.                                                                                                                                               |

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

Lists executions across all assets, not scoped to one asset. Results are permission-filtered: an execution is visible when the caller has `GET` on its workflow **and** `GET` on **every** asset the run read — each input file's asset plus each asset named as a metadata source — since the list and the details endpoint return the metadata of all of them. A run with no inputs of either kind is associated with the asset it wrote to, so that asset carries the check instead; a results-only run has no asset at all, and workflow `GET` is the whole gate.

An asset that has been permanently deleted is authorized on the database it lived in, under the same action. Deleting an asset does not delete the executions that ran against it, and a database is never removed — deleting one archives the record — so the history of a deleted asset stays reachable by whoever can read that database. An **archived** asset is not affected: its record is retained, so it is still authorized on its own attributes and any asset-level constraint on it still applies.

The list shows recent executions by default — those started within the last 90 days. Supply `filterStartDate` (and optionally `filterEndDate`) to query an explicit date range. The applied window is echoed back as `filterStartDate` (and `filterEndDate` when supplied).

```
GET /workflows/executions
```

### Query parameters

| Parameter                     | Type    | Required | Description                                                                                                                                                                        |
| ----------------------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `maxItems` / `pageSize`       | integer | No       | Page size (capped; excess pages via `NextToken`).                                                                                                                                  |
| `startingToken` / `NextToken` | string  | No       | Pagination continuation token.                                                                                                                                                     |
| `filterStartDate`             | string  | No       | UTC lower bound on execution start date, as `YYYY-MM-DDTHH:MM:SSZ`; only executions started on or after this date are listed. Any other form returns 400. Defaults to 90 days ago. |
| `filterEndDate`               | string  | No       | UTC upper bound on execution start date, as `YYYY-MM-DDTHH:MM:SSZ`; only executions started on or before this date are listed. Any other form returns 400.                         |
| `workflowId`                  | string  | No       | Filter by workflow id.                                                                                                                                                             |
| `workflowDatabaseId`          | string  | No       | Filter by workflow database id.                                                                                                                                                    |
| `status`                      | string  | No       | Filter by execution status.                                                                                                                                                        |
| `triggerType`                 | string  | No       | Filter by trigger type (`Manual`, `File-Upload`).                                                                                                                                  |
| `groupId`                     | string  | No       | Filter by execution group id.                                                                                                                                                      |
| `triggeredByUserId`           | string  | No       | Filter by the user who triggered the execution.                                                                                                                                    |

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

Returns the full detail and input/output traceability for a single execution, including the underlying pipelines (with status, timing, and each pipeline's resolved configuration), input files, input metadata, input configurations, the execution's output target, and a listing of all outputs (files, metadata, and results). Input metadata arrives in two collections: asset and file metadata under `inputMetadata`, and database metadata under `inputDatabaseMetadata`, which belongs to no asset. Every input-metadata row carries the `pipelineId` of the pipeline that read the entity, and every output file/metadata entry the `pipelineId` of the pipeline that produced it. Pipeline names and descriptions are resolved from the pipeline definitions, and the workflow description from the workflow definition. Large collections are bounded and any partial section is named in `truncatedCollections`.

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
        "metadataSourceDatabaseId": "",
        "metadataSourceDatabases": ["my-database", "reference"],
        "metadataSourceAssets": [{ "databaseId": "reference", "assetId": "site-survey" }],
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
                "pipelineId": "convert-to-glb",
                "databaseId": "my-database",
                "assetId": "a1b2c3",
                "filePath": "/",
                "scope": "asset",
                "metadata": { "site": "north" },
                "attributes": {}
            },
            {
                "pipelineId": "convert-to-glb",
                "databaseId": "my-database",
                "assetId": "a1b2c3",
                "filePath": "/scans/pump.e57",
                "scope": "asset",
                "metadata": { "captured": "2026-05-01" },
                "attributes": { "sensor": "faro" }
            }
        ],
        "inputDatabaseMetadata": [
            {
                "pipelineId": "convert-to-glb",
                "databaseId": "my-database",
                "assetId": "",
                "filePath": "/",
                "scope": "database",
                "metadata": { "program": "apollo" },
                "attributes": {}
            },
            {
                "pipelineId": "generate-preview",
                "databaseId": "my-database",
                "assetId": "",
                "filePath": "/",
                "scope": "database",
                "metadata": { "program": "apollo" }
            },
            {
                "pipelineId": "convert-to-glb",
                "databaseId": "reference",
                "assetId": "",
                "filePath": "/",
                "scope": "database",
                "metadata": { "classification": "internal" }
            },
            {
                "pipelineId": "generate-preview",
                "databaseId": "reference",
                "assetId": "",
                "filePath": "/",
                "scope": "database",
                "metadata": { "classification": "internal" }
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

The `outputs` collections list what the execution wrote to its output **asset**. Files a pipeline writes to the **auxiliary** location are not recorded and are absent from the response, including special preview-file locations — they are working and viewer-support files rather than tracked asset outputs.

Each input file carries the concrete S3 `versionId` the run read, resolved when the execution launched — the exact version processed. It is empty for folder or whole-asset selections, which have no single version.

Input metadata is attributed **per pipeline**. A workflow's pipelines do not read the same entities — each receives the subset of input files passing its own `inputFileFilters`, and an `inputFileArity` `none` pipeline receives no files at all — so each row records the entity together with the `pipelineId` that read it. The same entity therefore appears once per pipeline that read it, and those rows are distinct facts rather than one row repeated: they answer which metadata went into which step.

Which pipeline a row belongs to follows from what that pipeline reads:

-   A **file's** metadata belongs only to the pipeline that received that file.
-   An **asset's** own metadata (the `/` row) belongs to a pipeline that received at least one file from that asset, or to every pipeline when the asset is a named metadata source. A file-less pipeline reads the first asset group of the run's metadata, so that group's asset row is recorded for it.
-   A **database's** metadata belongs to every pipeline of the run. Database metadata describes an entity rather than a file selection, and every step is handed the run's whole set of databases, so a three-pipeline run returns each database row three times — identical but for the `pipelineId`.

The metadata sources of the run are reported alongside its inputs. `metadataSourceDatabases` lists every database whose metadata the run captured, matching the rows in `inputDatabaseMetadata`, and `metadataSourceAssets` lists the assets named purely as metadata sources. `metadataSourceDatabaseId` is the single database a run with no input files named; it is empty for a run whose databases came from its input files. Viewing an execution requires `GET` permission on every database in `metadataSourceDatabases`, so the databases reported are exactly those access to this view required. See [Metadata inputs](#metadata-inputs).

For executions whose output target is an asset, each output file carries the target asset identity — `assetId` and `databaseId` — derived from the execution's output target. When a matching file version-history record exists, `assetFileVersionId` is also added, identifying the specific S3 file version the execution wrote. `assetFileVersionId` is absent for outputs with no history record (for example, executions that ran before file version history was recorded).

`results` lists structured result files a pipeline emits to the execution's `results/` output folder (as opposed to asset files). Each entry carries the file's path relative to that folder (`relativeFilePath`), the file content (`resultsContent`), and `resultsContentTruncated`, which is `true` when the stored content was truncated to fit the field limit.
:::

:::note[Collection limits and truncation]
Every collection in the response is bounded so a run with a very large number of inputs or outputs still fits the AWS Lambda synchronous-response limit. Each collection is read to at most 2,000 rows, and the input collections — `inputFiles`, `inputMetadata`, and `inputDatabaseMetadata` — are then trimmed to at most 1,000 returned rows.

A file counts once against those bounds whatever it carries. Its metadata and its attributes ride on one row rather than two, so granting a pipeline `fileAttributes` adds keys to existing rows instead of adding rows — the bounds are spent per entity, not per key. A file carrying attributes and no metadata still occupies one row, so a run whose pipelines read attributes alone reaches the same bound as one reading metadata alone.

`truncatedCollections` names every collection that came back partial, and is an empty array when the view is complete. The names it can contain are `inputFiles`, `inputMetadata`, `inputDatabaseMetadata`, `outputs.files`, `outputs.metadata`, and `outputs.results`. A collection named there holds fewer rows than the execution produced. The route has no continuation token, so the remaining rows are not retrievable through it — treat a named collection as a sample rather than a count.

A collection is bounded two ways, and either bound names it here: a row count, and a size budget measured over the serialized rows. The size budget is what keeps the response inside the AWS Lambda synchronous-response limit, because a metadata row carries a whole entity's captured map and a row count alone says nothing about how large each row is. An execution over many files that each carry hundreds of metadata entries therefore returns a bounded, flagged view rather than failing. When a collection reports the metadata each pipeline read, the budget is spent evenly across the pipelines, so every pipeline stays represented and no step's absence is mistaken for a step that read nothing.

Each row reports two content maps. `metadata` holds the entity's metadata, and `attributes` holds that file's attributes — kept apart because a pipeline's `metadataInputs` grants `fileMetadata` and `fileAttributes` independently, so a merged map would lose which grant delivered a value. A row whose file carries attributes but no metadata still appears, with an empty `metadata` map; asset-level and database-scope rows always report an empty `attributes` map. Comparing the two maps across a run's steps is how you confirm what each step received, since the grants are resolved per step.

`inputMetadata` and `inputDatabaseMetadata` are read together and separated afterwards by each row's `scope`. Because a row dropped at the read cap has no known scope, reaching that cap names both collections; a return trim, which runs after the split, names only the collection that was trimmed.

Both metadata collections are per pipeline, so their bounds are spent evenly across the run's pipelines rather than in collection order: each pipeline reads its own share of the 2,000-row read budget, and a return trim takes a share from each pipeline instead of a prefix. A trimmed collection therefore still holds rows for every pipeline, rather than the first pipelines' rows and none of the later ones' — which would read as those steps having taken no metadata.
:::

:::note[Truncated configuration bodies]
A pipeline entry's `renderedConfig` is the configuration body after the execution's own template-tag values were substituted, and before the system tags were. Template substitution runs in two stages: the values a caller supplies for a template's `tagSchema` are filled in when the execution is validated, while the system tags — `{{assetMetadataObject}}`, `{{jobName}}`, the output paths, and the rest of the reserved set — resolve per step at launch, once the step's manifest and execution context exist. `renderedConfig` therefore still shows the system tags as literal `{{tag}}` placeholders, which is expected rather than a sign that substitution failed.

The fully substituted body — the one the pipeline actually read — is written to Amazon S3 per step, and `renderedConfigLocation` points at it. The two fields describe different stages of the same body: `renderedConfig` is pre-system-tag, `renderedConfigLocation` is post. Read the object when you need the exact values a step ran with.

The inline copy is bounded by the record's field limit.

`renderedConfigTruncated` reports whether the inline copy was shortened. The entry carries `renderedConfigLocation` — a \{`bucket`, `key`\} pair identifying the Amazon S3 object that holds the fully substituted body — whenever that object exists, not only on truncation:

```json
{
    "pipelineId": "3d-conversion-pipeline",
    "renderedConfig": "{\"outputFormat\": \"gltf\", \"materials\": [",
    "renderedConfigTruncated": true,
    "renderedConfigLocation": {
        "bucket": "vams-execution-run-bucket",
        "key": "executions/a1b2c3d4e5f6/input/1/config.json"
    }
}
```

`renderedConfigLocation` is present whenever the object exists, including when the inline body is complete — that is the common case, and the pointer is the only route to the fully substituted body a step ran with. `renderedConfigTruncated` is the truncation signal on its own. Reading the object requires Amazon S3 access to that bucket — the response carries the location, not a presigned URL.

Because the location is emitted only on truncation, an execution whose inline body fits carries no pointer to its fully substituted body. The object is still written at the same per-step key, so it remains readable directly from the run bucket.
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
        "warnings": [],
        "nextToken": null
    }
}
```

For the whole execution (no `pipelineExecutionId`), a full-mode response also includes `sfnHistoryEvents` — the Step Functions execution history rendered as a state-transition timeline. When `pipelineExecutionId` is supplied, `subProcessEvents` carries three kinds of log, merged and sorted together:

| Source                    | What it is                                                                                                                                                                                                                                    |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Step invocation log       | The log of the resource the workflow's state machine invoked for this step — for a `Lambda` step, that function's own CloudWatch log group. Derived from the step's recorded execution type and resource, so a pipeline does not register it. |
| Registered logs           | Any log location the pipeline reported for itself while running (`registeredLogs`).                                                                                                                                                           |
| Registered sub-executions | For a step that runs its own Step Functions sub-execution: that sub-execution's history, plus the resolved log group of its state machine.                                                                                                    |

The step invocation log is what holds the reason a launch failed before the pipeline's own logging started. Only execution types with a log group that can be derived have one:

| Execution type  | Step invocation log                                                                             |
| --------------- | ----------------------------------------------------------------------------------------------- |
| `Lambda`        | Yes — the invoked function's log group                                                          |
| `SQS`           | No — a queue has no invocation log; the consumer's log is a separate resource VAMS does not own |
| `EventBridge`   | No — a bus does not log deliveries by default                                                   |
| `DeadlineCloud` | No — session logs are reachable through the job rather than a derivable CloudWatch group        |

`warnings` is present only when a log could not be read — a missing permission on one group, or a registration list longer than the per-request cap. Each entry names the log in question. A warning never fails the request: the logs that could be read are still returned.

:::note[Redaction]
Every log string in the response passes through credential redaction first, so an inline token, AWS key, or JWT in a log message is masked before it reaches the caller.
:::

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
