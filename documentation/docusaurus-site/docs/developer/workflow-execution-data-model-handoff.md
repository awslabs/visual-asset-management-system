---
title: Workflow Execution Data Model (Stage 1 Handoff)
description: The v2.6 workflow-execution storage data model and what the next stage builds on it.
---

# Workflow Execution Data Model — Stage 1 Handoff

Stage 1 of the Workflow & Execution System revamp establishes the storage data model
for executions and wires the existing handlers to populate the fields known today
(workflow/first-pipeline inputs at launch; end-state pipeline outputs and logs at
completion). No API routes, CLI commands, web UI, STS vending, or pipeline behavior
changed in Stage 1. This document is the reference the next stage builds on.

## Tables

| Table                                           | PK                  | SK                                   | Indexes                                                                                                                                                                                                                                                      |
| ----------------------------------------------- | ------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| WorkflowExecutionsStorageTableV2                | executionId (GUID)  | workflowDatabaseId:workflowId        | GSI WorkflowExecutionsByWorkflowGSI (PK workflowDatabaseId:workflowId, SK executionStartDate)                                                                                                                                                                |
| PipelineExecutionsStorageTable                  | pipelineExecutionId | workflowExecutionId                  | GSI1 PipelineExecByWorkflowExecGSI (PK workflowExecutionId, SK pipelineDatabaseId:pipelineId); GSI2 PipelineExecChainGSI (PK workflowExecutionId, SK from_pipeline_execution_id); GSI3 PipelineExecEndStateGSI (PK workflowExecutionId, SK endStatePipeline) |
| PipelineExecutionInputFilesStorageTable         | pipelineExecutionId | databaseId:assetId:inputAssetFileKey | GSI InputFilesByAssetGSI (PK databaseId:assetId, SK pipelineExecutionId)                                                                                                                                                                                     |
| PipelineExecutionInputMetadataStorageTable      | pipelineExecutionId | databaseId:assetId:filePath          | —                                                                                                                                                                                                                                                            |
| PipelineExecutionInputConfigurationStorageTable | pipelineExecutionId | recordType ("configuration")         | —                                                                                                                                                                                                                                                            |
| PipelineExecutionOutputFilesStorageTable        | pipelineExecutionId | fileType:relativeFilePath            | —                                                                                                                                                                                                                                                            |
| PipelineExecutionOutputMetadataStorageTable     | pipelineExecutionId | targetFilePath:metadataKey           | —                                                                                                                                                                                                                                                            |
| PipelineExecutionOutputResultsStorageTable      | pipelineExecutionId | relativeFilePath                     | —                                                                                                                                                                                                                                                            |
| PipelineExecutionLogsStorageTable               | pipelineExecutionId | logType ("summary")                  | —                                                                                                                                                                                                                                                            |
| WorkflowExecutionInputsStorageTable             | workflowExecutionId | databaseId:assetId:inputAssetFileKey | GSI WorkflowExecInputsByAssetGSI (PK databaseId:assetId, SK executionStartDate)                                                                                                                                                                              |
| WorkflowExecutionConfigurationStorageTable      | workflowExecutionId | recordType ("configuration")         | —                                                                                                                                                                                                                                                            |

The legacy `WorkflowExecutionsStorageTable` is retained intact as the migration read source.

## Key decisions

-   **executionId is a VAMS GUID** passed as the Step Functions execution name, so
    `$$.Execution.Name == executionId` and all existing ASL S3 paths keep working.
-   **Executions are workflow-keyed.** Asset/database linkage lives in the input tables.
    The asset-scoped GET resolves through `WorkflowExecInputsByAssetGSI`.
-   **Clean composite keys** (no legacy `$` prefix); ISO-8601 UTC dates.
-   **`triggeredByUserId`** (or `system`) and **`triggerType`** (`Manual` | `File-Upload`)
    are recorded on the main row.
-   **Throttled Step Functions status sync.** The main row carries `executionStopDate`
    plus `lastSfnSyncCheckDate`. The end-state `processWorkflowExecutionOutput` lambda
    writes the stop date + status directly when the final pipeline completes, so a
    normal run is terminal in the table without any poll. `executionService` only calls
    Step Functions `describe_execution` when the row has **no** stop date **and** its
    `lastSfnSyncCheckDate` is older than the sync window (`SFN_SYNC_MIN_INTERVAL_SECONDS`,
    30s); each poll re-stamps `lastSfnSyncCheckDate`. This cuts direct SFN calls while
    still polling periodically so executions cancelled/aborted directly in Step Functions
    (outside VAMS) are still detected.
-   **`executionLog` vs `executionError`.** The main row captures the full CloudWatch
    execution log in `executionLog` on **every** terminal completion (success or failure)
    for debugging — the end-state `processWorkflowExecutionOutput` lambda writes it on the
    normal success path, and an `executionService` poll writes it for any terminal status it
    reconciles out-of-band. `executionError` holds only the specific failure message (SFN
    error/cause) and is set only for a non-`SUCCEEDED` terminal status. `executionError` is
    the broadly-visible message; `executionLog` is the fuller data intended for more
    limited roles (role-gated surfacing is a later stage).

## Populated in Stage 1 vs deferred

| Record                                                         | Stage 1                                              | Deferred                                      |
| -------------------------------------------------------------- | ---------------------------------------------------- | --------------------------------------------- |
| Main execution                                                 | Full                                                 | —                                             |
| Workflow inputs / configuration                                | At launch                                            | Per-stage updates                             |
| PipelineExecutions                                             | One row per pipeline (paths, type, chain, end-state) | Per-stage dates/status, STS vending, sub-ARNs |
| First-pipeline InputFiles / InputMetadata / InputConfiguration | Yes                                                  | All-stage inputs, input-port mappings         |
| End-state OutputFiles / OutputMetadata / Logs                  | Via process-output                                   | Per-stage outputs                             |
| OutputResults                                                  | Schema only                                          | Population when a pipeline emits results      |

## STS data-model fields (schema only in Stage 1)

`PipelineExecutionsStorageTable` carries `vendedRoleArn`, `s3ReadOnlyScopes`,
`s3ReadWriteScopes`, and `credentialVendingState` (`notVended`). The next stage builds
the STS vending lambda + container credential channel that populates them.

## What the next stage builds on this

-   API routes + CLI commands + web UI for detailed execution querying (history, current
    executions, basic/detailed logs), surfacing the renamed fields directly.
-   Per-stage start/stop registration so intermediate PipelineExecutions rows and their
    I/O populate as each step runs (requires modifying createWorkflow + pipeline steps).
-   STS credential vending + container delivery channel.
-   Pipeline input-port mappings and output-results population.
-   Deep aborts.

## Implementation notes for the next stage

-   **`PipelineExecChainGSI` and the empty root key.** The first pipeline in a workflow has
    `from_pipeline_execution_id = ""`. DynamoDB omits items whose GSI key attribute is an empty
    string, so the first (root) pipeline-execution row does not appear in `PipelineExecChainGSI`.
    Stage 1 never queries this GSI, so this is inert today; a later stage that walks the execution
    chain should either use a non-empty root sentinel for `from_pipeline_execution_id` or treat the
    absence of a predecessor as "this is the root."
