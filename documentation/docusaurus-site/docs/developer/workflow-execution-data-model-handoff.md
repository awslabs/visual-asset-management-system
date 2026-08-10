---
title: Workflow Execution Data Model
description: The storage data model behind workflow executions, which records populate when, and the conventions a new read or write path must follow.
---

# Workflow Execution Data Model

A workflow execution is stored across a main record and ten supporting Amazon DynamoDB tables. This page
is the developer-level reference for that layout: the key and index of each table, which code path writes
each record, and the conventions a new read or write path has to honour. For the user-facing description
of what an execution contains, see [Data Model](../architecture/data-model.md#what-an-execution-stores);
for the request and response shapes, see the [Workflows API reference](../api/workflows.md).

## Tables

| Table                                             | PK                    | SK                                     | Indexes                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------- | --------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WorkflowExecutionsStorageTableV2`                | `workflowExecutionId` | `workflowDatabaseId:workflowId`        | `WorkflowExecutionsByWorkflowGSI` (PK `workflowDatabaseId:workflowId`, SK `executionStartDate`); `WorkflowExecutionsByGroupGSI` (PK `executionGroupId`, SK `executionStartDate`); `WorkflowExecutionsByDateGSI` (PK `allListPartition`, SK `executionStartDate` — global newest-first list) |
| `PipelineExecutionsStorageTable`                  | `pipelineExecutionId` | `workflowExecutionId`                  | `PipelineExecByWorkflowExecGSI` (PK `workflowExecutionId`, SK `pipelineDatabaseId:pipelineId`); `PipelineExecChainGSI` (PK `workflowExecutionId`, SK `from_pipeline_execution_id`); `PipelineExecEndStateGSI` (PK `workflowExecutionId`, SK `endStatePipeline`)                             |
| `PipelineExecutionInputFilesStorageTable`         | `pipelineExecutionId` | `databaseId:assetId:inputAssetFileKey` | `InputFilesByAssetGSI` (PK `databaseId:assetId`, SK `pipelineExecutionId`)                                                                                                                                                                                                                  |
| `PipelineExecutionInputMetadataStorageTable`      | `pipelineExecutionId` | `databaseId:assetId:filePath`          | —                                                                                                                                                                                                                                                                                           |
| `PipelineExecutionInputConfigurationStorageTable` | `pipelineExecutionId` | `recordType` (`configuration`)         | —                                                                                                                                                                                                                                                                                           |
| `PipelineExecutionOutputFilesStorageTable`        | `pipelineExecutionId` | `fileType:relativeFilePath`            | —                                                                                                                                                                                                                                                                                           |
| `PipelineExecutionOutputMetadataStorageTable`     | `pipelineExecutionId` | `targetFilePath:metadataKey`           | —                                                                                                                                                                                                                                                                                           |
| `PipelineExecutionOutputResultsStorageTable`      | `pipelineExecutionId` | `relativeFilePath`                     | —                                                                                                                                                                                                                                                                                           |
| `PipelineExecutionLogsStorageTable`               | `pipelineExecutionId` | `logType` (`summary`)                  | —                                                                                                                                                                                                                                                                                           |
| `WorkflowExecutionInputsStorageTable`             | `workflowExecutionId` | `databaseId:assetId:inputAssetFileKey` | `WorkflowExecInputsByAssetGSI` (PK `databaseId:assetId`, SK `executionStartDate`)                                                                                                                                                                                                           |
| `WorkflowExecutionConfigurationStorageTable`      | `workflowExecutionId` | `recordType` (`configuration`)         | `WorkflowExecConfigByOutputAssetGSI` (PK `outputDatabaseId:outputAssetId`, SK `executionStartDate`) — sparse: written only for an asset-targeted run with a resolved destination                                                                                                            |

Table names are never hardcoded. Resolve them through `common.resourceNames.get_table_name` with the
matching `ResourceKeys` constant, at module level in the handler.

The `WorkflowExecutionsStorageTable` (without the `V2` suffix) is a separate, earlier table kept intact as
the read source for the data migration. No handler reads it.

## Key conventions

-   **`workflowExecutionId` is a VAMS GUID** passed to AWS Step Functions as the execution name, so
    `$$.Execution.Name` equals the execution id and the state machine composes the execution's S3 output
    prefixes from it directly.
-   **Executions are workflow-keyed.** Asset and database linkage lives in the input and configuration
    rows, never on the main row. An asset's execution history is the union of two queries:
    `WorkflowExecInputsByAssetGSI` for executions that **read** the asset, and
    `WorkflowExecConfigByOutputAssetGSI` for executions that **wrote** to it, merged so an execution that
    did both appears once. The output direction needs its own index because a results-only run, or a
    pipeline whose `inputFileArity` is `none`, writes no input rows at all — its output target is the only
    association it has with an asset.
-   **Composite keys are plain colon-joined values** (`databaseId:assetId`,
    `workflowDatabaseId:workflowId`, `databaseId:assetId:inputAssetFileKey`), built by the helpers in
    `common/workflows/executionRecords.py`. All dates are ISO-8601 UTC.
-   **`triggeredByUserId` and `triggerType`** are recorded on the main row. `triggerType` is stored as
    `Manual` or `File-Upload`; the execute request accepts the lowercase `manual` / `fileUpload` forms and
    the handler maps them. A trigger-launched run is attributed to `SYSTEM_USER`, because a user may
    upload a file without holding permission to run the workflow the upload triggers.
-   **Every write path stamps the global-list partition.** `allListPartition` carries the constant value
    `execution` on every main row and is the partition key of `WorkflowExecutionsByDateGSI`, which backs the
    global executions list as one newest-first query rather than a scan. Amazon DynamoDB omits an item that
    is missing a GSI partition attribute, so a write path — including a migration or backfill — that
    forgets the attribute produces an execution absent from the global list with no error at write time.

### Sparse indexes and empty string keys

Three attributes are deliberately written only when they have a value, because Amazon DynamoDB rejects an
empty string for an indexed key attribute and omits an item missing one:

| Attribute                        | Index                                | Omitted when                                              |
| -------------------------------- | ------------------------------------ | --------------------------------------------------------- |
| `executionGroupId`               | `WorkflowExecutionsByGroupGSI`       | The execution was not launched as part of a group         |
| `from_pipeline_execution_id`     | `PipelineExecChainGSI`               | The pipeline is the first step, so it chains from nothing |
| `outputDatabaseId:outputAssetId` | `WorkflowExecConfigByOutputAssetGSI` | The run is results-only, with no asset destination        |

A path that walks the pipeline chain treats the absence of a predecessor as "this is the root" rather than
expecting a root entry in `PipelineExecChainGSI`.

## Status tracking

The main row carries `executionStopDate` and `lastSfnSyncCheckDate`, and the end-state
`processWorkflowExecutionOutput` Lambda function writes the stop date and terminal status directly when the
final pipeline completes — so a normal run reaches a terminal state in the table without any polling.
`executionService` calls the AWS Step Functions `DescribeExecution` API only when the row has **no** stop
date **and** its `lastSfnSyncCheckDate` is older than `SFN_SYNC_MIN_INTERVAL_SECONDS` (30 seconds); each
poll re-stamps `lastSfnSyncCheckDate`. This keeps direct Step Functions calls off the common path while
still detecting an execution cancelled or aborted directly in Step Functions, outside VAMS.

Pipeline rows start at `NEW` (queued), advance to `RUNNING` when the step begins, and end at a terminal
status. `TERMINAL_STATUSES` is `SUCCEEDED`, `FAILED`, `ABORTED`, `TIMED_OUT`; a reconcile or an abort leaves
a row that already reached one of those untouched.

### `executionLog` compared with `executionError`

The main row captures the full CloudWatch log for the run in `executionLog` on **every** terminal
completion, success or failure — the end-state Lambda function writes it on the normal path, and an
`executionService` poll writes it for any terminal status it reconciles out of band. `executionError` holds
only the specific failure message (the Step Functions error and cause) and is set only for a non-`SUCCEEDED`
terminal status.

`executionError` is the broadly visible message. Full log retrieval is scoped to a separate, narrower
route (`GET /workflows/executions/\{executionId\}/logs`), which the shipped Database User and read-only
permission templates deny; both fields are passed through log redaction before they leave the handler.

## Which record populates when

| Record                                            | Written by                                                                                                                                                                                                                                 |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Main execution row                                | `executeWorkflow` at launch; status, stop date, log and error reconciled by the end-state Lambda function or a poll                                                                                                                        |
| Workflow inputs and workflow configuration        | `executeWorkflow` at launch                                                                                                                                                                                                                |
| `PipelineExecutions` (one row per step)           | `executeWorkflow` at launch (paths, execution type, chain link, end-state flag); each step's start/stop date and status by the interim tracking Lambda function; registered sub-processes and log locations by `registerPipelineExecution` |
| Per-step `InputConfiguration` and `InputMetadata` | `executeWorkflow` at launch, one row set per step, narrowed to that step's own effective `metadataInputs` gate and to its own input entities                                                                                               |
| `OutputFiles`                                     | The interim tracking Lambda function for each intermediate step, by diffing the shared output folder against the versions already recorded; `processWorkflowExecutionOutput` for the end-state step                                        |
| `OutputMetadata`, `OutputResults`, `Logs`         | `processWorkflowExecutionOutput` at completion; `handleExecutionError` writes a log row on failure                                                                                                                                         |
| `PipelineExecutionInputFiles`                     | Not written at run time — see below                                                                                                                                                                                                        |

:::warning[`PipelineExecutionInputFiles` has no run-time writer]
`build_pipeline_input_file_record` exists and the data migration populates the table for migrated
executions, but no VAMS run-time path writes it: an execution launched through `executeWorkflow` records
its input files once, on `WorkflowExecutionInputsStorageTable`. The execution-details response reads that
table, and `executionService` touches the per-pipeline table only to delete its rows on a permanent delete.
A read path built against it returns zero rows for any execution launched by VAMS.
:::

### The two input tables are not interchangeable

Both tables key the same selected files, but only one pins a version.
`WorkflowExecutionInputsStorageTable` rows carry `s3Bucket` and `assetRootS3Key` — the bucket and
bucket-relative asset-root prefix of that file's own asset, stored per file because one run can read files
from several assets in different buckets — plus the concrete S3 `versionId` the run read, empty for a folder
or whole-asset selection that has no single version. `PipelineExecutionInputFilesStorageTable` rows carry
only the `databaseId` / `assetId` / `inputAssetFileKey` locator and the owning `workflowExecutionId`; there
is no `versionId` attribute on that table.

## Credential-vending fields

`PipelineExecutionsStorageTable` carries `vendedRoleArn`, `s3ReadOnlyScopes`, `s3ReadWriteScopes` and
`credentialVendingState`. `credentialVendingState` is `notVended` on every row: the fields reserve the
shape for scoped AWS STS credential delivery to pipeline containers, and no VAMS component populates them.
Treat them as reserved rather than as a data source.

## Sub-process registration

A pipeline step may report the lower-level resources it created — its Step Functions sub-execution and its
CloudWatch log locations — by putting an event on the orchestration event bus under the source prefix
`\{eventSourcePrefix\}.execution.\{executionId\}.pipeline.\{pipelineExecutionId\}` with the detail type
`pipeline.execution.register`. A standing Amazon EventBridge rule routes the event to
`registerPipelineExecution`, which appends the reported resources to the targeted pipeline row's
`registeredSubExecutions` and `registeredLogs` lists.

Registration is optional and additive — it does not replace the task-token callback a pipeline already
uses — but it is what makes two capabilities work:

-   **Abort reaches inside a pipeline.** `abort_execution` stops each still-running step's registered
    sub-processes before stopping the outer state machine. Each entry is typed by `resourceType`; Step
    Functions executions are stopped, and any other type is registered but returns a non-fatal warning so
    the caller knows the sub-process was left running.
-   **Full-mode log retrieval finds the right log group.** Each `registeredLogs` entry carries
    `logGroupArn`, `logGroupName`, `logStreamName` and `logStreamPrefix`, so a full-mode log read pulls from
    the pipeline's own CloudWatch location rather than only the workflow log group.

## Adding a read or write path

1. Resolve the table name with `get_table_name(ResourceKeys.*)` at module level. Never hardcode it.
2. Build the record with the matching `build_*_record` helper in
   `common/workflows/executionRecords.py`, so composite keys, sparse-key omission and the item byte
   budgets stay in one place. Items over the 400 KB Amazon DynamoDB limit are truncated by the helper,
   which sets the matching `*Truncated` flag and leaves the complete body in Amazon S3.
3. Enforce both authorization tiers. Execution reads and aborts authorize through
   `authorize_execution_access`, which requires `GET` on the workflow, the matching action on every asset
   the run read (or wrote to, for a run with no inputs), and `GET` on every database the run captured
   metadata from.
4. Never return a partially populated collection without flagging it. Every response that bounds a
   collection names what it dropped in `truncatedCollections` so the caller can page the remainder through
   `GET /workflows/executions/\{executionId\}/details/metadata`.
