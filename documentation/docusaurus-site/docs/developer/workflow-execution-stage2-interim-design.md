---
title: Workflow Execution Stage 2 — Output Targeting + Interim Pipeline Tracking (Design)
description: Design for output-location recording, workflow-level input tracking, per-pipeline config/manifests, and the interim pipeline-tracking lambda + SFN flow.
---

# Workflow Execution Stage 2 — Design

> **Status: implemented (backend + CDK).** The use-case pipeline containers/lambdas are
> intentionally **not** yet updated to consume the new resolved-input manifest / per-pipeline
> config envelope — they continue to run on the legacy ASL path variables (kept alongside the
> new fields). All VAMS pipelines must be redeployed for the new createWorkflow ASL flow
> (interim tracking states + error-handler catch routing) to take effect, and are re-massaged
> to read the new envelope in a subsequent task.

Stage 2 records where an execution's outputs go, reshapes input tracking to the
workflow-execution level, gives each pipeline its own resolved inputs, and inserts a
reusable **interim pipeline-tracking lambda** between pipeline steps so every pipeline's
inputs and outputs are tracked — not just the end state. No use-case pipeline
containers/lambdas are modified in this stage (their SFN input contract changes, so they
are re-massaged in a later task); all VAMS pipelines will need redeployment.

## 0. Two distinct configuration scopes

Stage 2 keeps two clearly separated configuration concepts (separate tables, separate
purposes):

-   **Workflow execution input configuration** (`WorkflowExecutionConfigurationStorageTable`,
    one row per execution): configuration for how the **core workflow execution lambdas**
    operate — where final outputs are written (`outputLocationType` / `outputAssetId` /
    `outputDatabaseId`), where input metadata came from (`inputMetadataAssetId` /
    `inputMetadataDatabaseId`), and the input-metadata snapshot. It is **not** per-pipeline.
-   **Workflow pipeline input configuration**
    (`PipelineExecutionInputConfigurationStorageTable`, one row per pipeline execution):
    configuration for how an **individual pipeline** executes itself (its
    `inputParameters`). Materialized as that pipeline's `config.json` input file.

## 1. Output-location + input-metadata recording (executeWorkflow + config table)

New fields on `WorkflowExecutionConfigurationStorageTable` (one config row per execution,
`recordType="configuration"`) — these are the **workflow execution input configuration**
(core-lambda operational config, per §0):

| Field                     | Source today                     | Notes                                                            |
| ------------------------- | -------------------------------- | ---------------------------------------------------------------- |
| `outputLocationType`      | constant `"asset"`               | Only `"asset"` supported now; enum point for future targets.     |
| `outputAssetId`           | execute path `assetId`           | Where outputs land. Future generic-execute lets the user pick.   |
| `outputDatabaseId`        | execute path `databaseId`        | "                                                                |
| `inputMetadataAssetId`    | execute path `assetId`           | Recording only (no Casbin). Optional later when metadata is free.|
| `inputMetadataDatabaseId` | execute path `databaseId`        | Recording only.                                                  |

**Output-asset permission (proactive):** executeWorkflow enforces Casbin **POST** on the
output asset (= the path asset today) at launch. Denied → **403, no SFN started**. This is
in addition to the existing POST checks on each input-file asset. Forward-looking: when
generic execute lands, this is the gate on the user-chosen output asset.

## 2. Input-tracking reshape (workflow level, not per-pipeline)

- **Input asset files** are tracked on the **workflow execution** via
  `WorkflowExecutionInputsStorageTable` (already PK `workflowExecutionId`). executeWorkflow
  stops writing per-pipeline `PipelineExecutionInputFilesStorageTable` rows for inputs;
  that table is reserved for any future per-pipeline input-file needs.
- **Workflow-level input configuration** lives in
  `WorkflowExecutionConfigurationStorageTable` and does **not** include per-pipeline config.
- **Per-pipeline input configuration** lives in
  `PipelineExecutionInputConfigurationStorageTable` (PK `pipelineExecutionId`) **and** as a
  config file in that pipeline's input folder.
- **executionService details handler** repoints its `inputFiles` assembly at
  `WorkflowExecutionInputsStorageTable` (was per-pipeline). (Reconciliation of the
  Stage‑1.5 details API.)

## 3. Working-file layout (asset bucket execution folder + aux for scratch)

Input-definition files (asset bucket, under the execution folder):

```
assetBucket/{outputAssetBasePrefix}/{execId}/
  input/metadata.json                 # shared input metadata file (all pipelines)
  pipeline{N}/input/config.json        # pipeline N's input configuration file
  pipeline{N}/input/manifest.json      # pipeline N's resolved input manifest (see §5)
```

Shared execution output folder (asset bucket — single folder, all pipelines write here):

```
assetBucket/.../{execId}/output/   files/   previews/   metadata/
```

Pipeline **scratch/temp** working files use the **aux** bucket execution temp prefix
(unchanged from today's `inputOutputS3AssetAuxiliaryFilesPath`).

## 4. Per-pipeline SFN input envelope (exec-type-agnostic)

`stepfunctions_builder.build_payload` is the single shared envelope for all task types
(Lambda/SQS/EventBridge/DeadlineCloud — DeadlineCloud flattens the same fields into
reserved `Vams*` OpenJD job parameters with no per-type divergence). Each pipeline state
receives:

```
# identity / context (top level)
executionId / workflowExecutionId, workflowId, workflowDatabaseId,
executingUserName, executingRequestContext
# resolved inputs for THIS pipeline
inputManifestS3Location            # resolved {relPath -> bucket,key,versionId}  (§5)
inputConfigurationS3Location       # this pipeline's config.json
inputMetadataS3Location            # shared metadata.json
inputAssetFilesS3Root              # direct S3 root of the asset's files
# shared outputs + scratch
outputS3AssetFilesPath / outputS3AssetPreviewPath / outputS3AssetMetadataPath
auxTempPrefix
```

Existing ASL path variables are **kept** alongside the new fields this stage (not removed),
so nothing breaks before the pipelines are re-massaged.

## 5. Input shadowing — resolved manifest per pipeline

Problem: once a pipeline writes a file to the output **files** folder at the same
asset-relative path as an execution input, later pipelines must use **that** file for that
path, while still seeing originals for untouched paths.

Solution: a **resolved input manifest** per pipeline. For pipeline N+1, the interim lambda
writes `pipeline{N+1}/input/manifest.json` =

```
[ { relativePath, bucket, key, versionId }, ... ]
```

For each relative path: if `output/files/{relPath}` exists, point at the output file's
bucket/key/**latest versionId**; else point at the original asset file + its versionId.
Only the output **files** folder shadows (previews/metadata never shadow). Pins S3 versions
(no large copies). Pipeline 1's manifest = all original asset files, written by
executeWorkflow at launch.

## 6. Interim pipeline-tracking lambda + SFN flow

```
P1 -> interim(1->2) -> P2 -> interim(2->3) -> ... -> Pn -> processWorkflowExecutionOutput
  \________________ every state's Catch ________________/
                            v
                     handleExecutionError (records failure, §6a)
                            v
                  WorkflowProcessingJobFailed (Fail)
```

- **One reusable interim Lambda** (built once in CDK). createWorkflow inserts a distinct
  Task state per adjacent pipeline pair, each carrying that gap's `from`/`to`
  pipelineExecutionIds + the next pipeline's config/manifest targets in its payload.
- After pipeline N, the interim lambda:
  1. **Logs N's outputs**: diff the shared output **files** folder using a
     **versionId snapshot** taken before N (snapshot stored per execution; P1's snapshot is
     written by executeWorkflow). N's outputs = keys new since the snapshot OR whose latest
     `versionId` changed. Record `PipelineExecutionOutputFiles` rows with `s3VersionId`, plus
     N's stop date + status on its `PipelineExecutions` row.
  2. **Prepares N+1**: write `pipeline{N+1}/input/manifest.json` (§5), refresh the versionId
     snapshot, and set the SFN result so N+1's state reads its (already-written, §7)
     `inputConfigurationS3Location` + new `inputManifestS3Location`.
- **End state** stays `processWorkflowExecutionOutput`, which logs the **last** pipeline's
  outputs (same diff/version logic, shared module) and finalizes the execution
  (stop date, status, execution log) — its current behavior, extended with the diff +
  S3-version capture.
- The shared diff/record/version logic lives in a common module imported by both the interim
  lambda and processWorkflowExecutionOutput (no duplication).

## 6a. Error-catch state + error-handler lambda

Today every pipeline task's `Catch` routes directly to a single `WorkflowProcessingJobFailed`
Fail state, so a failure leaves the V2 main row and all in-flight pipeline rows with no stop
time, no `ABORTED`/`FAILED` status, and no captured error message. Stage 2 adds a reusable
**error-handler lambda** and routes every state's `Catch` through it before the Fail state:

```
any pipeline/interim state --Catch(States.ALL, ResultPath=$.errorInfo)--> handleExecutionError
handleExecutionError --> WorkflowProcessingJobFailed (Fail)   # SFN still ends FAILED
```

- **One reusable error-handler Lambda** (built once in CDK). createWorkflow points every task
  state's `Catch` at it, capturing the caught error object via `ResultPath` (e.g.
  `$.errorInfo`) so the handler receives the Step Functions `Error`/`Cause`.
- On invocation the handler reconciles all tables for the execution:
  - sets the V2 main row to `FAILED` with a stop date (unless already terminal) and stores
    the specific `executionError` (from the caught `Error: Cause`) + the full CloudWatch
    `executionLog` (same fetch the end-state lambda uses);
  - marks every non-terminal `PipelineExecutions` row `FAILED` with a stop date;
  - writes a per-pipeline logs row for the failing pipeline when identifiable.
- After the handler returns, the state machine transitions to `WorkflowProcessingJobFailed`
  so the execution still terminates in a `FAILED` SFN status (the handler does not swallow the
  failure — it only records it). The handler is best-effort/idempotent: any error inside it is
  logged and still falls through to the Fail state so a bookkeeping problem never masks the
  original failure.
- This reuses the shared status/stop/log module (§6) so the failure path and the success path
  write consistent fields. It also complements the abort API (which writes `ABORTED`); the
  error handler writes `FAILED`.

## 7. Config-file authorship

executeWorkflow writes **every** pipeline's `config.json` (content is static, from each
pipeline def's `inputParameters`) to its `pipeline{N}/input/` folder at launch, and records
each in `PipelineExecutionInputConfigurationStorageTable`. The interim lambda only points
N+1 at its already-written config file and writes the dynamic manifest. Config errors surface
at execute time.

## 8. S3 versioning capture

`_collect_output_descriptors` (and the interim diff) capture `s3VersionId` via
`list_object_versions` / head, replacing today's empty `s3VersionId: ""`. Requires S3
versioning on the asset bucket (already enabled for versioned outputs).

## What is NOT in this stage

- No use-case pipeline container/lambda changes (their input contract changes; re-massaged
  in a later task). All VAMS pipelines will need redeployment after this stage.
- No generic-output execute API yet (output asset = path asset today); the recorded
  `output*`/`inputMetadata*` fields + POST check lay the groundwork.
- The DeadlineCloud task builder exists at the execution layer (`DeadlineCloudTaskBuilder`
  emits `aws-sdk:deadline:createJob.waitForTaskToken` task states; the job-callback lambda
  resolves task tokens from the default-bus `aws.deadline` job status events and registers
  the job as the pipeline execution's sub-process). Pipeline **creation** with
  `pipelineExecutionType: DeadlineCloud` is not yet possible — the request model, storage
  shape, and UI land with the pipeline/workflow table overhaul (see the pipeline refactor
  plan's "Deadline Cloud creation enablement" section).
