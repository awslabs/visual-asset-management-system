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
    upload a file without holding permission to run the workflow the upload triggers. A system pipeline
    run stores a system trigger value such as `System-MyFeature`, which the execute request never accepts
    (see [System pipeline runs](#system-pipeline-runs)).
-   **Every write path stamps the global-list partition.** `allListPartition` carries the constant value
    `execution` on every main row and is the partition key of `WorkflowExecutionsByDateGSI`, which backs the
    global executions list as one newest-first query rather than a scan. Amazon DynamoDB omits an item that
    is missing a GSI partition attribute, so a write path — including a migration or backfill — that
    forgets the attribute produces an execution absent from the global list with no error at write time.
-   **An output-metadata row's sort-key value names its type.** The attribute is still called
    `targetFilePath:metadataKey`, but its value is `{targetFilePath}:{metadataType}:{metadataKey}` with
    `metadataType` `metadata` or `attribute`, and the row also stores `metadataType`. A step that writes one
    key to a file as both file metadata and a file attribute therefore records two rows. Rows from earlier
    releases keep the `{targetFilePath}:{metadataKey}` value and have no `metadataType`; readers take the
    type from `executionRecords.output_metadata_type`, which falls back to the source file name, and never
    parse the sort-key value.

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

### Terminal writers

Five paths write a terminal status on the main row, and all of them use the same terminal guard
(`executionOutputs.not_terminal_condition`), so the first to write wins and the others leave the row alone:

| Writer              | Code                                                                            | Status written                                                   |
| ------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| End state           | `processWorkflowExecutionOutput` (`_write_execution_outputs`)                   | `SUCCEEDED`, or `FAILED` on an output failure                    |
| Error handler       | `sfn/handleExecutionError.py` (`executionOutputs.finalize_main_row`)            | `FAILED`                                                         |
| Abort               | `executionService.abort_execution`                                              | `ABORTED`                                                        |
| Group abort         | `executionService.abort_group`, through `abort_execution` per member            | `ABORTED`                                                        |
| Read-path reconcile | `executionService._reconcile_main_status` (details) and `get_executions` (list) | the status Step Functions reports for a run stopped outside VAMS |
| Status change event | `executionService.handle_sfn_status_change`, through `_reconcile_main_status`   | the status Step Functions reports for a run stopped outside VAMS |

The status change event writer is the push form of the details reconcile. An Amazon EventBridge rule on
the default bus matches the Step Functions "Execution Status Change" events with a terminal status for
this account and Region's `vams-` workflow state machines and invokes the execution service. The handler
acts only when the event names an execution with a main row in this deployment whose recorded
`workflow_execution_arn` is the event's (several deployments share the prefix), takes the status from its
own `DescribeExecution` call rather than from the event, and does not log the event, whose detail
carries the execution's input and output. A run that finished through the state machine is already
terminal when the event arrives, so the handler only reads the row. It handles every run, not only
system runs, because what it writes is what the next read would write, and it releases the locks of a
run stopped outside VAMS right away.

### `executionLog` compared with `executionError`

The main row captures the full CloudWatch log for the run in `executionLog` on **every** terminal
completion, success or failure — the end-state Lambda function writes it on the normal path, and an
`executionService` poll writes it for any terminal status it reconciles out of band, and the error-handler
Lambda function writes it for a run that fails. Each reads the run's
events from its start date onward, following the log's continuation token, and stores at most 256 KB of
redacted text: the newest events are kept, with a first line giving the number left out and a last line
when the read's page or time budget ended before the log did. CloudWatch Logs makes events searchable
some seconds after they happen, and not in timestamp order, so after its status write the end-state
and error-handler Lambda functions re-read for up to 30 seconds until two full re-reads in a row,
after a minimum settle time, find the same number of events with the same total size, and replace the
stored log with that read. When the reads do not agree within the 30 seconds, the largest read is
stored with a last line saying the log may be incomplete. The execution details view and the
executions list each capture the log when they are the first to record a run stopped outside VAMS.
The truncated logs route also reads the live log and returns whichever copy holds more lines, and the
live log when it holds as many lines as a stored copy marked as possibly incomplete. `executionError` holds
only the specific failure message (the Step Functions error and cause) and is set only for a non-`SUCCEEDED`
terminal status.

`executionError` is the broadly visible message. Full log retrieval is scoped to a separate, narrower
route (`GET /workflows/executions/{executionId}/logs`), which the shipped Database User and read-only
permission templates deny; both fields are passed through log redaction before they leave the handler.
The per-asset executions list returns a shortened copy of `executionLog` (the newest 16 KB,
`LISTED_EXECUTION_LOG_MAX_BYTES`) so a 200-row page stays under the Lambda response limit.

## System pipeline runs

A system pipeline run is an execution VAMS itself launches for a system feature. The workflow core
treats it as an ordinary run, with two differences, both implemented in
`common/workflows/systemPipelines.py` so the core never imports, names or calls the feature:

-   **Server-only trigger.** The run's `triggerType` is a system trigger value the feature chooses. Core
    keeps no list of them: it accepts any value of the form `System-` followed by ASCII letters and
    digits in words joined by single hyphens, at most 64 characters (for example `System-MyFeature`),
    and stores it as given. The execute handler sets it only for a `lambdaCrossCall` carrying
    `SYSTEM_USER` **and** the `vamsSystemPipeline` source marker, plus a top-level `systemExecution`
    object (`{"triggerType": "System-MyFeature", "context": {...}}`). An API Gateway request cannot carry
    either top-level key, and the public request model accepts only `manual` and `fileUpload`, so a
    public request naming a system value is answered `400`. A malformed marked call is answered `400`
    with a fixed message before any record is read.
-   **Server-only context.** The marked call may carry `context`, a JSON object of at most 4096 bytes once
    serialized. It is written to the first pipeline step's manifest under `systemExecutionContext` and to
    the main row as serialized text, and nowhere else: not in `systemConfig` or `effectiveSystemConfig`,
    not in the Step Functions input (which the workflow Lambda functions log), not in the list or details
    responses, and never replayed. Later steps of a multi-step system workflow do not receive it.

There is no terminal callback. The feature stores the `workflowExecutionId` the launch returns on its
own record and reads the run's status from the ordinary execution tracking when its own record is read:
the main execution row, which the terminal writers above keep current, the read-time reconcile, and the
status change rule, which records a run stopped outside VAMS as soon as Step Functions reports it. Every
terminal writer handles a system run exactly as it handles an ordinary run. A feature cleans up its own
leftover artefacts with Amazon S3 lifecycle rules (for example, aborting incomplete multipart uploads),
not with a hook in the workflow core.

The launch path's trust rests on who can invoke the execute function directly: any principal with
`lambda:InvokeFunction` on `executeWorkflowV2` can send the marked call and start a system run under any
well-formed system trigger value. Today only the execution service (re-run) and the workflow trigger
dispatcher hold that grant, and neither sends the marker, so a new invoke grant on that function is a
grant to launch system runs and is reviewed as one.

A re-run (`POST /workflows/executions/{executionId}/rerun`) of a system run is refused with `400`.

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
table, and the per-pipeline table is touched only by the permanent-delete function, to delete its rows.
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
`{eventSourcePrefix}.execution.{executionId}.pipeline.{pipelineExecutionId}` with the detail type
`pipeline.execution.register`. A standing Amazon EventBridge rule routes the event to
`registerPipelineExecution`, which appends the reported resources to the targeted pipeline row's
`registeredSubExecutions` and `registeredLogs` lists. The handler accepts an event only when its `Source`
ends in `.pipeline.{pipelineExecutionId}` for the `pipelineExecutionId` the detail names, so a pipeline
can attach resources to its own execution alone. Log entries are deduplicated by location — the log group
(ARN without a trailing `:*`, or name), stream name, and stream prefix — and a redelivery that carries a
`stageName`, `label`, or `sourceType` the stored entry lacks merges them in; at most 50 log entries and 50
sub-executions are stored per row.

Registration is optional and additive — it does not replace the task-token callback a pipeline already
uses — but it is what makes two capabilities work:

-   **Abort reaches inside a pipeline.** `abort_execution` stops each still-running step's registered
    sub-processes before stopping the outer state machine, and the error handler does the same for the
    in-flight steps of a run that fails. Each entry is typed by `resourceType`; Step Functions executions,
    AWS Batch jobs and AWS Deadline Cloud farm jobs are stopped, and any other type is registered but
    returns a non-fatal warning so the caller knows the sub-process was left running.
-   **Full-mode log retrieval finds the right log group.** Each `registeredLogs` entry carries
    `logGroupArn`, `logGroupName`, `logStreamName`, `logStreamPrefix`, `stageName`, `label`, and
    `sourceType` (the last three are `""` when the producer did not send them; `sourceType` is one of
    `stateMachine`, `lambda`, `batch`, `ecs`, `container`, `custom`), so a full-mode log read pulls from the
    pipeline's own CloudWatch location rather than only the workflow log group. The details route lists every
    entry — together with the step's derived invocation log and a registered sub-state-machine's logging
    destination — as `availableLogs`, each with a `logId` (the first 16 hex characters of the SHA-256 of
    the UTF-8 JSON array `[kind, logGroupArn without a trailing ":*", logStreamName, logStreamPrefix]`,
    with `""` for an absent stream or prefix — `log_id()` in `common/workflows/availableLogs.py`, the only
    place the encoding lives), and the logs route reports the same list with a read status as `logSources` and
    reads one entry alone when given `logId`. An entry with an exact stream is read without
    the execution-scope terms only when the stream starts with a prefix registered on the same pipeline
    execution or was itself registered with that stream.
-   **Stage status is derived, never stored.** `registeredSubExecutions` holds locators only. When a details
    request carries `includeSubExecutions=true`, `executionService` describes the registered state machine
    (memoised per invocation), walks its definition into an ordered stage frame, and attributes the execution
    history's events to those stages to produce `subExecutions[].stages` with a status per stage. The reads
    are bounded (definition ≤ 256 KiB, ≤ 50 stages, depth ≤ 3, ≤ 5 history pages per sub-execution and 20
    per request) and best-effort — a Step Functions or AWS Batch error yields `UNKNOWN` plus a
    `subExecutionWarnings` entry, never a failed request — and the CloudWatch `nextToken` of a logs request is
    never passed to `get_execution_history`.

## Workflow completion event

Once the main execution row holds a terminal status, the lambda that wrote it puts one event on the
orchestration event bus (`ORCHESTRATION_BUS_ARN`, with the source prefix from
`ORCHESTRATION_EVENT_SOURCE_PREFIX`): `processWorkflowExecutionOutput` for a run that succeeds,
`handleExecutionError` for a run that fails or times out, and `executionService` when an abort request
stops the run or when one of its Step Functions status reconciles records a completion no other writer
did. All of them publish through `emit_workflow_execution_completed` in
`common/workflows/executionOutputs.py`, and only the writer whose terminal-status write landed
publishes; the event is skipped when the bus ARN or the event source prefix is unset. The entry is
built by `workflow_execution_completed_event` in
`common/workflows/executionRecords.py`, the only place its shape lives:

| Field          | Value                                                                                                                                                                                                                                            |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `EventBusName` | The orchestration bus ARN                                                                                                                                                                                                                        |
| `Source`       | `{eventSourcePrefix}.execution.{executionId}`                                                                                                                                                                                                    |
| `DetailType`   | `workflow.execution.completed`                                                                                                                                                                                                                   |
| `Detail`       | `executionId`, `workflowDatabaseId`, `workflowId`, `status` (the stored terminal status: `SUCCEEDED`, `FAILED`, `ABORTED`, `TIMED_OUT`), `startedAt`, `completedAt` (ISO-8601 UTC), and `executionGroupId` when the execution belongs to a group |

The detail carries identifiers and timestamps only — no template bodies, tag values or file locations; a
consumer that needs those reads the V2 execution records by `executionId`. Publishing is best-effort:
a `put_events` failure is logged and does not change the stored status.

The compliance workflow callback is the standing consumer. Its Amazon EventBridge rule on the
orchestration bus matches `source` by the deployment's event source prefix and `detail-type`
`workflow.execution.completed`; the handler looks the `executionId` up in the compliance evaluation
table's `ExecutionIdIndex` and, for an evaluation awaiting that execution, reads the pipeline's
`compliance-output.json` from the `PipelineExecutionOutputResults` rows of the run and completes the
pipeline rule (see [Compliance](../concepts/compliance.md#workflow-completion)). On every completion,
compliance's or not, it then launches the pipeline rules waiting for that workflow over the assets
the ended execution read (see
[Rules that wait for a running execution](../concepts/compliance.md#rules-that-wait-for-a-running-execution)). Any other consumer
subscribes the same way; the `pipeline.execution.register` source form above and this one share the
`{eventSourcePrefix}.execution.{executionId}` stem, so a rule using `source` prefix matching on the
deployment prefix sees both.

## Adding a read or write path

1. Resolve the table name with `get_table_name(ResourceKeys.*)` at module level. Never hardcode it.
2. Build the record with the matching `build_*_record` helper in
   `common/workflows/executionRecords.py`, so composite keys, sparse-key omission and the item byte
   budgets stay in one place. Items over the 400 KB Amazon DynamoDB limit are truncated by the helper,
   which sets the matching `*Truncated` flag and leaves the complete body in Amazon S3.
3. Enforce both authorization tiers. Execution reads and aborts authorize through
   `authorize_execution_access`, which requires `GET` on the workflow, the matching action on every asset
   the run read and on the asset it wrote to, and `GET` on every database the run captured metadata
   from.
4. Never return a partially populated collection without flagging it. Every response that bounds a
   collection names what it dropped in `truncatedCollections` so the caller can page the remainder through
   `GET /workflows/executions/{executionId}/details/metadata`.
