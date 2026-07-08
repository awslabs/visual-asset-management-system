---
title: Workflow Execution Stage 3 — Unified Manifest, Sub-Process Registration, Schema Versioning (Design)
description: The v2.6 Stage 3 execution refactor — SFN handler grouping, the unified per-pipeline manifest envelope, EventBridge sub-ARN registration, and schema versioning.
---

# Workflow Execution Stage 3 — Design

Stage 3 continues the bottom-up execution refactor (execution first, then pipelines, then
workflows). It does not change any use-case pipeline container or `vamsExecute` lambda — those
are re-massaged in a later task (see the pipeline refactor plan). The API request/response
contracts for executing and saving workflows are unchanged.

Stage 3 delivers four things:

1. Group the three Step Functions-invoked execution lambdas under an `sfn/` subfolder.
2. Make the per-pipeline **manifest** the single envelope a pipeline reads — resolved input
   files (each self-locating), output locations, aux locations, and system config.
3. Let a pipeline step optionally **register its sub-process ARNs and log ARNs** with the
   execution via the existing EventBridge orchestration bus, for deeper tracking, sub-aborts,
   and sub-log retrieval.
4. Stamp a **schema version** on every VAMS-authored execution file and on the generated ASL,
   so stale workflow state-machine definitions can be detected and redeployed later.

## 1. SFN handler grouping

The three lambdas invoked by the workflow state machine move into
`backend/backend/handlers/workflows/sfn/`:

-   `sfn/processWorkflowExecutionOutput.py` — end-state output processing + finalization.
-   `sfn/interimPipelineTracking.py` — between-pipeline output diff + next-pipeline manifest.
-   `sfn/handleExecutionError.py` — error-catch reconciliation to FAILED.

A new `sfn/__init__.py` package marker is added. The CDK lambda builders set the handler to
`handlers.workflows.sfn.{name}.lambda_handler`. The API-facing workflow handlers
(`executeWorkflow`, `createWorkflow`, `executionService`, `workflowService`, etc.) stay at the
`workflows/` top level. This is purely organizational; no behavior changes.

## 2. The unified per-pipeline manifest envelope

The per-pipeline `manifest.json` (asset bucket, under
`pipelines/workflowExecutionInputs/{executionId}/pipeline{N}/manifest.json`) becomes the single
file a pipeline reads for everything static about its inputs and locations. It replaces the
flat resolved-files list with a grouped envelope:

```json
{
    "schemaVersion": 1,
    "inputFiles": [
        {
            "relativePath": "/test/pump.e57",
            "databaseId": "my-database",
            "assetId": "xabc123",
            "assetRootS3Key": "xabc123/",
            "auxPreviewPrefix": "my-database/xabc123/test/pump.e57/preview",
            "bucket": "asset-bucket",
            "key": "xabc123/test/pump.e57",
            "versionId": "v3"
        }
    ],
    "inputMetadataS3Location": "s3://asset-bucket/pipelines/workflowExecutionInputs/{execId}/metadata.json",
    "outputs": {
        "bucket": "asset-bucket",
        "files": "pipelines/{pipelineName}/{jobName}/output/{execId}/files/",
        "previews": "pipelines/{pipelineName}/{jobName}/output/{execId}/previews/",
        "metadata": "pipelines/{pipelineName}/{jobName}/output/{execId}/metadata/",
        "results": "pipelines/{pipelineName}/{jobName}/output/{execId}/results/"
    },
    "outputTarget": {
        "locationType": "asset",
        "assetId": "{outputAssetId}",
        "databaseId": "{outputDatabaseId}",
        "fileBaseExecutionPathExtension": "/"
    },
    "auxBucket": "aux-bucket",
    "auxTempPrefix": "pipelines/{pipelineName}/{execId}/",
    "auxPreviewPipelinePrefix": "",
    "systemConfig": {
        "orchestrationBusArn": "arn:...:event-bus/...-orchestration",
        "orchestrationEventPrefix": "vams.prod.execution.{execId}.pipeline.{pipelineExecutionId}"
    }
}
```

Design points:

-   **Locations are relative keys plus a bucket, never pre-built `s3://` URIs.** The `outputs`
    block pairs a single `bucket` with bucket-relative prefixes; `auxBucket` is the auxiliary
    bucket name; `auxTempPrefix` is a bucket-relative, execution-scoped working prefix
    (`pipelines/{pipelineName}/{execId}/`); and each input file's `assetRootS3Key` is the
    bucket-relative asset root. Downstream consumers (via `manifestHelper`) reconstruct `s3://`
    forms as needed for their integration layer. This keeps the contract composable — a consumer
    can address a bucket and a key independently — and multi-file/multi-bucket ready.
-   **Each input file is self-locating.** A file can be a different asset / version (especially
    once outputs from a prior pipeline shadow an original input), so every entry carries its own
    `assetRootS3Key`, `bucket`, `key`, `versionId`, and its own unique `auxPreviewPrefix` rather
    than a single shared root.
-   **Per-input-file aux preview location.** Every input file carries a unique
    `auxPreviewPrefix` (`{databaseId}/{assetFileKey}/preview`, where `assetFileKey` is the full
    asset-bucket key — the asset location key plus the relative file path, so a custom asset base
    prefix is preserved), regardless of pipeline type — a pipeline that writes preview/viewer data
    resolves it against `auxBucket`.
    The top-level `auxPreviewPipelinePrefix` is a per-pipeline viewer subfolder (e.g.
    `/PotreeViewer`) appended to that per-file prefix; it is empty until sourced from the pipeline
    configuration, replacing hardcoded viewer paths in pipeline code.
-   **No `previewMode` field.** A redundant boolean is not carried; a pipeline resolves its aux
    preview location itself from `auxBucket` + the input file's `auxPreviewPrefix` (+ the
    per-pipeline `auxPreviewPipelinePrefix`).
-   **`outputTarget`** identifies where the execution's outputs are written: `locationType`
    (`asset` today), and the `assetId` / `databaseId` of the output asset. The target equals the
    input asset today but is carried explicitly so the end-state process-output step writes to the
    declared target rather than assuming the input asset. `fileBaseExecutionPathExtension` is a
    path segment inserted between the output asset's location key and each output file's relative
    path (final key = `assetLocationKey + extension + relativePath`); it defaults to `/` (no extra
    segment) and is reserved for writing an execution's outputs under a sub-folder of the asset.
-   **`systemConfig`** is the home for all VAMS-controlled (non-user) pipeline configuration.
    Today it carries the orchestration bus ARN + the per-execution+pipeline event prefix for
    optional sub-process registration (§3). It is distinct from the user-defined
    `inputParameters` (delivered separately as `config.json`, a future user feature).
-   **The external task token is NOT in the manifest.** The manifest is written to S3 _before_
    the pipeline state runs (by `executeWorkflow` for pipeline 1, by the interim lambda for
    pipelines 2+), but Step Functions generates the task token at state entry
    (`$$.Task.Token`). The token therefore stays in the SFN payload body, alongside the
    `manifestS3Location` and identity fields. The pipeline reads static data from the manifest
    and takes the token from the payload.
-   **Output paths match the ASL exactly.** The shared output S3 folder is keyed by the first
    pipeline's name + a per-pipeline job name + the execution id. The job names are generated
    once when the state machine is created/updated (`generate_workflow_asl`) and persisted on the
    workflow record (`jobNames`). `executeWorkflow` reads `jobNames[0]` at launch to build the
    manifest's `outputs`, so the manifest advertises the same S3 folder the ASL hands the first
    pipeline's container (rather than an independently drawn job name pointing at a different
    folder). Pipelines 2+ get their `outputs` threaded from the ASL's global output URIs by the
    interim lambda, so the whole chain is consistent.

### SFN payload after Stage 3

`stepfunctions_builder.build_payload` continues to emit the legacy path variables and inline
`inputMetadata` (kept so existing pipelines keep working until re-massaged), and adds the
`manifestS3Location` envelope pointer. The intent is that re-massaged pipelines read
`manifestS3Location` + `taskToken` + identity and ignore the legacy fields; the legacy fields
are removed only when every pipeline has migrated (a later task).

### Multi-partition service-integration ARNs

The generated ASL references Step Functions optimized integrations by ARN
(`arn:{partition}:states:::lambda:invoke`, `…:sqs:sendMessage`, `…:events:putEvents`, and their
`.waitForTaskToken` variants). The partition segment must match the deployment — Step Functions
rejects an `arn:aws:` integration ARN in GovCloud, China, or ISO partitions. `stepfunctions_builder`
builds every integration ARN through a single `states_integration_arn(integration, partition)`
helper and threads the partition through the state builders (default `aws`). The `createWorkflow`
and `createPipeline` lambdas read the deployment partition from an `AWS_PARTITION` environment
variable (injected by the CDK lambda builders from `config.env.partition`) and pass it to the
builders, so the same code emits valid ASL in every partition.

### End-state output recording

The single `process-outputs-*` state runs once after all pipelines complete and invokes
`processWorkflowExecutionOutput`. Its payload carries the four shared output-folder prefixes the
end-state lambda lists for produced artifacts — `filesPathKey`, `previewPathKey`,
`metadataPathKey`, and `resultsPathKey` — each resolving to the matching subfolder of the
execution's output location (`.../output/{execId}/files|previews|metadata|results/`).

The end-state lambda lists each prefix and records what it finds against the end-state pipeline
execution:

-   **Files** move to the output asset and are recorded in `PipelineExecutionOutputFilesStorageTable`.
-   **Metadata** files are applied to the asset and recorded in `PipelineExecutionOutputMetadataStorageTable`.
-   **Results** are structured artifacts a pipeline emits to the `results/` folder for the
    execution itself (rather than as asset files). The lambda reads each result file's content and
    records a `PipelineExecutionOutputResultsStorageTable` row (`relativeFilePath` relative to the
    results folder, the `resultsContent`, a truncation flag when the content exceeds the field
    limit, and the source `s3Key`). The execution-details API returns these under `results`.

## 3. Optional sub-process ARN + log registration (EventBridge)

A pipeline step may optionally report the lower-level resources it created (its own Step
Functions sub-execution, AWS Batch job, ECS/Fargate task, CloudWatch logs) so VAMS can track
them, attempt sub-aborts, and retrieve sub-process logs. Reporting is optional: a pipeline that
does not report still works exactly as before (it reports success/failure via the existing
task-token callback). Registration is _in addition to_ the task-token mechanism, not a
replacement.

Flow:

```
pipeline step --PutEvents--> orchestration bus
   source        = <orchestrationEventPrefix>  (from manifest.systemConfig)
   detail-type   = "pipeline.execution.register"
   detail        = { pipelineExecutionId,
                     subExecution: { resourceType, ...locator ARNs },   # optional
                     logs: [ { logGroupArn, logGroupName, logStreamName, logStreamPrefix } ] }  # optional
        |
   one standing EventBridge rule (source prefix = deployment eventSourcePrefix,
        detail-type = "pipeline.execution.register")
        |
   registerPipelineExecution lambda  ->  append to the PipelineExecutions row
```

-   **One standing rule** matches every execution/pipeline in the deployment by the source
    prefix; the lambda routes to the exact pipeline row by `detail.pipelineExecutionId`. The
    `orchestrationEventPrefix` =
    `<eventSourcePrefix>.execution.<executionId>.pipeline.<pipelineExecutionId>` is recorded on
    each pipeline-execution record and handed to the pipeline in `manifest.systemConfig`.
-   **Use-case pipelines need only `events:PutEvents`** on the bus (no DynamoDB access). The
    registration lambda owns the table write.
-   **Reported values are validated before they are stored.** Because the registration event
    originates from a pipeline (untrusted input written to a DynamoDB key and later used to call
    `StopExecution`/CloudWatch), the lambda validates each field with the shared, partition-aware
    `common/validators.py` dispatcher: `pipelineExecutionId` must be a valid id (else the event is
    ignored), each sub-process locator must match its expected format (`ARN` for the `*Arn`
    locators, an id for `jobId`), and each log field must be a valid CloudWatch log-group ARN /
    log-group name / log-stream name. Validation is **best-effort and field-level**: a malformed
    field is dropped (and logged) rather than failing the whole registration, and a sub-process or
    log entry with no remaining valid locator is discarded. Registration never raises.

### Registered-resource storage on the PipelineExecutions record

The record carries typed, list-valued fields so the _kind_ of each reported resource is
unambiguous and multiple sub-processes / log files are supported (the table is new, so there are
no legacy single-ARN fields):

| Field                         | Shape                                                                  | Meaning                                             |
| ----------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------- |
| `orchestrationBusEventPrefix` | string                                                                 | The event source prefix this pipeline reports under |
| `registeredSubExecutions`     | list of `{ resourceType, ...locator ARNs }`                            | Reported sub-process resources (any type)           |
| `registeredLogs`              | list of `{ logGroupArn, logGroupName, logStreamName, logStreamPrefix }`| Reported CloudWatch log locations                   |

Each `registeredSubExecutions` entry is **typed by `resourceType`** so it can hold any kind of
sub-process, with whichever locator keys apply to that type:

| `resourceType`            | Locator keys                                   | Abortable today |
| ------------------------- | ---------------------------------------------- | --------------- |
| `stepFunctionsExecution`  | `stateMachineArn`, `executionArn`              | Yes             |
| `batchJob`                | `jobArn`, `jobId`                              | Not yet         |
| `ecsTask`                 | `taskArn`, `clusterArn`                        | Not yet         |
| _(other)_                 | `arn` (generic fallback)                       | Not yet         |

A bare `{ stateMachineArn, executionArn }` report (what the current use-case pipelines send)
normalizes to `resourceType: "stepFunctionsExecution"` for back-compat. All reported types are
**stored** now; the abort path **acts** only on Step Functions executions today (see below) and
gains the others in a later stage. Naming is explicit about each ARN's type — `stateMachineArn`
(the SFN definition) vs `executionArn` (a running execution) vs `logGroupArn`/`logGroupName`/
`logStreamName`/`logStreamPrefix` — so readers never have to guess what an ARN points at.

The current use-case pipelines register only their Step Functions sub-execution and its log
group; the broader resource types exist so future pipelines (or future stages of existing ones)
can register Batch jobs, ECS tasks, etc. without a schema change.

The lambda appends with an atomic DynamoDB `list_append` (seeded by `if_not_exists`) rather
than a read-modify-write, so concurrent registration events for the same pipeline execution
accumulate their entries without clobbering each other.

### Abort and full-mode logs use the registered resources (best-effort)

-   **Abort** (`DELETE /workflows/executions/{executionId}`): in addition to stopping the main
    SFN execution, it stops each `registeredSubExecutions[]` entry whose `resourceType` is
    `stepFunctionsExecution` (via `StopExecution` on its `executionArn`). Other resource types
    are not yet abortable: each is left running and surfaces a non-fatal warning naming the type
    and locator.
-   **Full-mode logs** (`GET /workflows/executions/{executionId}/logs?mode=full`): in addition to
    the shared workflow log group, it pulls from each `registeredLogs[]` location — scoped to an
    exact `logStreamName` when given, else narrowed by `logStreamPrefix`, else the whole group.

Both are **best-effort**: a permission or other failure on any one resource never fails the
request. The failure (or not-yet-supported resource type) is caught per-entry, logged, and
surfaced as a non-fatal warning in the response body (e.g.
`{"message": "...", "warnings": ["Sub-process abort failed for <arn>: <reason>"]}`), so a caller
learns that sub-process abort/log retrieval was incomplete and why (permissions, missing
resource, unsupported type, etc.).

This is distinct from the `PipelineExecutionLogsStorageTable` (built by `build_log_record`),
which stores the **captured log text** (`resultLog`/`errorLog`) the truncated-mode logs API
returns. `registeredLogs` holds **pointers** to CloudWatch locations that full-mode logs
live-fetches on demand — where to pull from, versus what was already pulled and stored.

## 4. Schema versioning

To detect when a file or ASL schema has changed (so stale pipelines/workflows can be
identified for redeploy):

-   **VAMS-authored files** carry an inline `schemaVersion` integer: the per-pipeline
    `manifest.json` (`MANIFEST_SCHEMA_VERSION`) and the shared input `metadata.json`
    (`METADATA_SCHEMA_VERSION`). The user-defined `config.json` (`inputParameters`) does **not**
    carry a version — it is user content, not a VAMS schema. The metadata file wraps the
    metadata payload in a stamped envelope so the original snapshot is preserved verbatim:

    ```json
    {
      "schemaVersion": 1,
      "metadata": { "...the asset/file input metadata snapshot..." }
    }
    ```

-   **The ASL** carries `aslSchemaVersion=N` in the state machine `Comment`
    (`"VAMS Pipeline Workflow for {wf} | aslSchemaVersion=N"`) **and** `aslSchemaVersion=N` is
    persisted on the workflow DynamoDB record at create/update. A later stale-check compares a
    workflow record's stored `aslSchemaVersion` (or the live state machine's Comment) against the
    current `ASL_SCHEMA_VERSION` code constant to flag workflows whose state machine should be
    redeployed.

Versions are simple incrementing integers defined as code constants
(`MANIFEST_SCHEMA_VERSION`, `METADATA_SCHEMA_VERSION` in `common/workflows/executionRecords.py`;
`ASL_SCHEMA_VERSION` in `createWorkflow.py`). Bump the relevant constant whenever its schema
changes.

## What is NOT in this stage

-   No use-case pipeline container/`vamsExecute` changes (see the pipeline refactor plan). All
    VAMS pipelines must be redeployed for the new ASL flow + manifest to take effect, and are
    re-massaged to read the manifest envelope (and, for metadata-passing pipelines, to read the
    metadata file from S3) in a later task.
-   No automatic stale-workflow migration/redeploy yet; Stage 3 only records the versions that a
    later migration will read.
-   No generic-output execute API; no STS credential vending.

## Component boundaries

Execution, pipelines, and workflows remain stand-alone with hard boundaries defined only by the
inputs passed from one to the next (as in the current handler structure). Stage 3 keeps that
boundary: the manifest + SFN payload are the entire contract a pipeline depends on, and the
EventBridge registration is a one-way, optional report. This isolation is what lets the
execution layer be refactored now without touching Web/CLI, and pipelines/workflows to be
refactored in later stages.
