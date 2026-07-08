---
title: Use-Case Pipeline Refactor Plan (post-Stage 3)
description: How VAMS use-case pipelines will be re-massaged to consume the Stage 3 manifest envelope, read large metadata from S3, and optionally register sub-process ARNs.
---

# Use-Case Pipeline Refactor Plan

This plan describes how the VAMS use-case pipelines (`backendPipelines/`) will be updated to
consume the Stage 3 manifest envelope. **No pipeline code is changed yet** — Stage 3 keeps the
legacy SFN payload fields alongside the new `manifestS3Location` so existing pipelines keep
working. This is the plan for the subsequent pipeline-refactor stage. All VAMS pipelines must
be redeployed once the refactor lands.

## Current pipeline entry contract (today)

Every pipeline's `vamsExecute*` lambda reads from the SFN payload `event['body']`:

-   S3 path strings: `inputS3AssetFilePath`, `outputS3AssetFilesPath`,
    `outputS3AssetPreviewPath`, `outputS3AssetMetadataPath`,
    `inputOutputS3AssetAuxiliaryFilesPath`;
-   `inputMetadata` — the asset/file metadata passed **inline** in the SFN definition;
-   `inputParameters` — the user-defined parameters (kept as-is, user feature);
-   `outputType`, `executingUserName`, `executingRequestContext`;
-   `TaskToken` — the Step Functions callback token (when `waitForCallback=Enabled`).

It forwards these to the pipeline's `constructPipeline` lambda, which builds the AWS Batch job
(or other compute) definition.

### The large-metadata problem

`inputMetadata` is passed inline through the SFN definition and, for several pipelines, into
the Batch job definition (an AWS Batch container override / ECS task definition). Both have
size limits. When an asset's metadata is large, the job definition exceeds the limit and the
pipeline fails. Stage 3 provides `inputMetadataS3Location` in the manifest precisely so the
pipeline can read the metadata file from S3 instead of receiving it inline.

## The metadata-content boundary rule

**The `vamsExecute*` lambda is the metadata-content boundary.** Metadata content must never
travel past it. Past the lambda, only the metadata S3 location (`inputMetadataS3Location`)
travels through the pipeline payload — through `openPipeline`, the nested Step Functions
`start_execution` input, `constructPipeline`, the AWS Batch / ECS container override, and into
the container. Any component that actually needs the metadata reads the file from S3
(`manifestHelper.fetch_metadata`).

This is a single rule, not two depths: inline metadata content forwarded through any of those
boundaries hits a payload size limit (Step Functions input and AWS Batch / ECS command overrides
are both hard-capped), so a large-metadata asset fails. Forwarding only the location removes the
limit entirely for any metadata size.

A pipeline's refactor therefore has two independent questions:

1.  **Does metadata content travel past `vamsExecute` today?** If yes, switch every downstream
    hop to thread `inputMetadataS3Location` instead of the inline `inputMetadata` content (the
    `vamsExecute` lambda, `openPipeline`, `constructPipeline`, and the container definition).
    This is required even when the eventual consumer ignores the metadata — the content must not
    occupy the size-limited payload at all.
2.  **Does a downstream component consume the metadata?** If yes, that consumer (a container or a
    later lambda) calls `manifestHelper.fetch_metadata(s3_client, inputMetadataS3Location)` to
    read the file from S3, replacing the inline read.

### Pipeline classification (verified)

Reading each pipeline confirms which questions apply. "Metadata flows past `vamsExecute`" means
the pipeline forwards inline `inputMetadata` content into a downstream hop today.

| Pipeline                      | Metadata flows past `vamsExecute`?   | Downstream consumer reads it?    | Work                                                                  |
| ----------------------------- | ------------------------------------ | -------------------------------- | --------------------------------------------------------------------- |
| `3dRecon/splatToolbox`        | Yes (Batch command + env)            | Yes — container                  | Thread location through; container reads from S3 in `__main__.py`     |
| `genAi/metadata3dLabeling`    | Yes (Batch command)                  | Yes — downstream Lambda          | Thread location through; the metadata Lambda reads from S3            |
| `multi/modelOps`              | Yes (into the lambda chain)          | No                               | Thread location through; no consumer change                           |
| `multi/rapidPipeline`         | Yes (into the lambda chain)          | No                               | Thread location through; no consumer change                           |
| `preview/3dThumbnail`         | Yes (openPipeline → nested SFN)      | No                               | Thread location through (done); no consumer change                    |
| `preview/pcPotreeViewer`      | Yes (Batch command)                  | No                               | Thread location through; no consumer change                           |
| `genAi/nvidia/gr00t`          | No — forces `inputMetadata=''`       | Lambda merges, pre-`vamsExecute` | Location-only; lambda already reads what it needs before the boundary |
| `genAi/nvidia/cosmos/*`       | No — forces `inputMetadata=''`       | Lambda extracts prompt           | Location-only; prompt extraction stays in the lambda                  |
| `multi/rapidPipelineEKS`      | No                                   | No                               | Location-only                                                         |
| `simulation/isaacLabTraining` | Yes (Batch command via openPipeline) | No                               | Thread location through; no consumer change                           |

The table above is about **metadata**. `genAi/metadata3dLabeling` needs its downstream **Lambda**
to read metadata from S3; `3dRecon/splatToolbox`'s container reads metadata to build its config.
For the pipelines that extract what they need in the `vamsExecute` lambda itself (Cosmos prompt,
gr00t config merge), that extraction stays before the boundary and only the location travels
onward.

### The same rule applies to `inputParameters` (input configuration)

`inputParameters` is delivered by the **same Stage-2/3 mechanism** as metadata: the workflow
writes it to a per-pipeline `config.json` and the SFN body carries `inputConfigurationS3Location`
alongside `inputMetadataS3Location`. So the content boundary applies identically — `inputParameters`
content must not travel past `vamsExecute` either; thread `inputConfigurationS3Location` and have
the consumer read+parse it from S3 (`manifestHelper.fetch_input_configuration`). This is why a
pipeline can need a container change even when it ignores metadata: `preview/3dThumbnail`'s
container consumes `inputParameters` (`overwriteExistingPreviewFiles`), so its container reads the
configuration from S3 even though it reads no metadata. When auditing each pipeline, apply the two
questions to **both** metadata and input configuration.

## Optional sub-process ARN registration

Each pipeline may optionally register the lower-level resources it creates so VAMS can track
them, attempt sub-aborts, and retrieve sub-process logs (Stage 3 §3). It reads
`systemConfig.orchestrationBusArn` and `systemConfig.orchestrationEventPrefix` from the manifest
(threaded through from the `vamsExecute*` lambda) and `PutEvents` on the orchestration bus:

```
PutEvents(
  EventBusName = systemConfig.orchestrationBusArn,
  Source       = systemConfig.orchestrationEventPrefix,
  DetailType   = "pipeline.execution.register",
  Detail       = {
    "pipelineExecutionId": <this pipeline execution id>,
    "subExecution": { "stateMachineArn": ..., "executionArn": ... },   # if a sub-SFN
    "logs": [ { "logGroupArn": ..., "logGroupName": ..., "logStreamName": ... } ]  # job logs
  })
```

**Where registration happens (not `constructPipeline`).** `constructPipeline` runs _before_ the
sub-resource exists — it only builds a definition dict, so it never holds the Batch job id or
ECS task ARN. Registration is therefore split by what each component actually knows:

-   **Sub-SFN execution ARN** → register from `openPipeline` (it calls `start_execution` and
    holds the `executionArn`).
-   **AWS Batch job / ECS task ARN + its CloudWatch logs** → register from the Step Functions
    layer or `pipelineEnd` (only the running state machine knows the submitted job id). For ECS
    pipelines (`multi/modelOps`, `multi/rapidPipeline`) the container log group is known at synth
    time in the CDK construct, so a concrete log ARN can be registered without runtime discovery.

The pipeline execution's role needs `events:PutEvents` on the orchestration bus (added per
pipeline construct during this refactor — not granted in Stage 3, since nothing reports yet).

Registration is optional: a pipeline that does not register still works (it reports
success/failure via the existing task-token callback). Registering it gives VAMS better
tracking, sub-aborts, and sub-log retrieval (subject to permissions — abort/log retrieval are
best-effort and warn rather than fail).

## Shared manifest helper (vendored)

The `vamsExecute*` lambdas perform the same manifest read in every pipeline. Because pipelines
are standalone Lambda code assets that cannot import the backend package, the shared logic lives
in a `manifestHelper.py` module that is **vendored into each pipeline's `lambda/` directory**
(the same pattern as `customLogging/logger.py`). Keep it dependency-light (boto3 only) so the
same file can also be copied into a container that needs to read metadata from S3.

`manifestHelper` exposes:

-   `resolve_pipeline_inputs(data, s3_client)` — fetch the manifest referenced by
    `inputManifestS3Location` (best-effort) and return normalized fields using the same legacy
    names the pipelines already forward (`inputS3AssetFilePath`, `outputS3AssetFilesPath`,
    `outputS3AssetPreviewPath`, `outputS3AssetMetadataPath`,
    `inputOutputS3AssetAuxiliaryFilesPath`, `inputMetadataS3Location`, `assetId`, `databaseId`),
    plus the resolved `inputFiles` list and `orchestrationBusArn` / `orchestrationEventPrefix`.
-   `fetch_metadata(s3_client, input_metadata_s3_location)` — read the shared metadata file from
    S3 and unwrap the Stage-3 metadata envelope (`{schemaVersion, metadata}`), returning the inner
    payload dict. This is what any consumer **downstream** of the `vamsExecute` lambda calls to
    obtain metadata, since metadata content is never forwarded past the boundary.
-   `fetch_manifest` / `resolve_inputs` / `parse_s3_uri` — lower-level pieces.

Resolution is **manifest-preferred, legacy-fallback**: a payload without a manifest (or a failed
S3 read) resolves exactly to today's legacy fields, so the change is non-breaking. The manifest's
first resolved input file supplies the input S3 path and the self-locating asset identity.

## Validated pilot pattern

`preview/3dThumbnail` is the validated reference. It exercises **all three** concerns in one
pipeline: input resolution, the content boundary for both metadata and input configuration, a
container that reads its configuration from S3, and sub-process registration. (Its container does
consume `inputParameters` — `overwriteExistingPreviewFiles` — so config is a genuine
consumer-reads-from-S3 case even though metadata is not.)

1.  **Vendor `manifestHelper.py`** into the pipeline's `lambda/` directory; for a container that
    reads metadata/config, also vendor a small container-side reader (here
    `container/preview_pipeline/utils/manifest_io.py`, which reuses the container's own S3 client).
2.  **`vamsExecute*`**: create an S3 client, call `manifestHelper.resolve_pipeline_inputs(data,
s3_client)`, and forward the resolved `inputS3AssetFilePath` / output / aux / `assetId`, the
    `inputMetadataS3Location` and `inputConfigurationS3Location` (the **locations**, never the
    inline `inputMetadata` / `inputParameters` content), and the `orchestrationEventPrefix`.
3.  **`openPipeline`**: thread both locations into the nested SFN input (replacing the inline
    content) and, after `start_execution`, best-effort `PutEvents` the sub-SFN `executionArn` +
    the state-machine log group to the orchestration bus. Registration never fails the pipeline.
4.  **`constructPipeline`**: put both locations into the job definition dict (matching the
    container's `PipelineDefinition` field names — verify the dataclass accepts the exact keys).
5.  **Container**: read configuration (and metadata, if consumed) from S3 via the vendored
    reader; the `PipelineDefinition` dataclass carries the **locations**, not the content. Keep
    the `to_json()` multi-stage round-trip coherent. Preserve the localTest path (inline-JSON
    affordance for values that are not `s3://`).
6.  **CDK**: pass the orchestration bus + state-machine log group to the `openPipeline` builder,
    set `ORCHESTRATION_BUS_NAME` / `STATE_MACHINE_LOG_GROUP_NAME` / `STATE_MACHINE_LOG_GROUP_ARN`
    env, and `grantPutEventsTo` the bus.
7.  **Tests** under the pipeline's `lambda/tests/`: manifest-preferred resolution + legacy
    fallback, handler forwards the **locations** (and not the content), `fetch_metadata` /
    `fetch_input_configuration` envelope-unwrap + best-effort, registration `PutEvents` shape +
    best-effort, and a **container-contract test** that the `constructPipeline` definition dict
    instantiates the container `PipelineDefinition` dataclass (the producer→consumer contract).

The task token still comes from the payload, not the manifest.

## Suggested ordering

1.  **Reference pilot — `preview/3dThumbnail`** (done): vendored helper + container reader,
    metadata + input-configuration content boundary (locations only past `vamsExecute`), container
    reads config from S3, and sub-process registration (sub-SFN ARN + log group) with the CDK
    role/env wiring. Validates the boundary rule, the shared helper, the container contract, and
    registration in one pipeline.
2.  **Large-metadata consumer — `3dRecon/splatToolbox`** (done; the only pipeline whose container
    consumes metadata): threaded both locations through `vamsExecute` + `sqsExecute` (auto-trigger
    keeps working with empty locations) + `openPipeline` + `constructPipeline`; the preserved
    `__main__.py` wrapper now reads metadata + config from S3 via a vendored
    `container/utils/manifest_io.py` and maps them to the per-key env vars `src/main.py` reads;
    dropped the dead inline `INPUT_METADATA` / `INPUT_PARAMETERS` Batch env; added registration.
    **Downloaded-source gotcha:** `Dockerfile`, `/src/*`, and `LOCAL_DEBUG_README.md` are
    downloaded at deploy (see `container/.gitignore`) — never modify them. The B-deep change lives
    entirely in the tracked `__main__.py` wrapper, which preserves the env-var contract
    (`MODEL`, `MAX_NUM_IMAGES`, …) that the downloaded `src/main.py` reads via `config.json`.
3.  **Batch 2 — `multi/modelOps`, `multi/rapidPipeline`, `preview/pcPotreeViewer`,
    `simulation/isaacLabTraining`** (done). Each threads the metadata + input-configuration S3
    locations end to end and registers its sub-SFN. Notable per-pipeline shapes:
    -   `multi/modelOps` and `multi/rapidPipeline` are **lambda-side config consumers**: their
        `constructPipeline` lambda reads the input configuration from S3
        (`manifestHelper.fetch_input_configuration`) to build the ECS command / write
        `rp_config.json`, instead of consuming inline `inputParameters`. The CDK grants the
        `constructPipeline` lambda asset-bucket read for that.
    -   `preview/pcPotreeViewer` is **threading-only** (2 entry points: `vamsExecute` +
        `sqsExecute`); its container does not consume metadata/config, but the container
        `PipelineDefinition` dataclass carries the location fields (producer→consumer contract).
        The bespoke aux-only Potree output override (empty workflow output paths) is preserved.
    -   `simulation/isaacLabTraining` has **no `constructPipeline`** — `vamsExecute` starts the
        internal SFN, reads the config from S3 at the boundary to extract
        `trainingConfig`/`computeConfig`, threads the locations through the SFN states, and
        registers the internal SFN execution. The dual task-token model (external VAMS callback +
        internal Batch token) is preserved.
4.  **Batch 3 — Cosmos family (`predict` text2world+video2world, `reason`, `transfer`),
    `genAi/nvidia/gr00t`, `multi/rapidPipelineEKS`, `genAi/metadata3dLabeling`** (done). All thread
    the metadata + input-configuration S3 locations end to end and register their sub-SFN. Notable
    per-pipeline shapes:

    -   **Cosmos** (predict/reason/transfer): the prompt (and transfer's control type/path) is
        extracted at the `vamsExecute` boundary — now sourced from the S3 metadata envelope
        (legacy inline fallback) — and only the locations travel onward. Each container reads its
        config flags (e.g. `INVALIDATE_COSMOS_MODELS`, `DISABLE_GUARDRAILS`, `CONTROL_WEIGHT`) from
        S3 via a vendored container `manifest_io.py` (AWS-CLI-based S3 read, no boto3 in image);
        the Dockerfile `COPY` ships it. predict has two entry points refactored identically.
    -   **gr00t**: the `gr00tConfig` merge stays at the `vamsExecute` boundary (sourced from S3);
        raw inline metadata/config no longer forwarded.
    -   **rapidPipelineEKS**: no container — the `consolidated_handler` CONSTRUCT_PIPELINE op reads
        config from S3 via the vendored lambda `manifestHelper` and writes `rp_config.json`; the
        per-operation container self-callback task-token model is preserved.
    -   **metadata3dLabeling**: the downstream **lambda** `metadataGenerationPipeline` is the
        consumer — it reads metadata + config from S3 (preserving the
        `seedMetadataGenerationWithInputMetadata` gate); the container `PipelineDefinition`
        dataclass carries the location fields for the multi-stage round-trip.

    **Pre-existing bug fixed** in cosmos/predict: both `vamsExecuteCosmosText2WorldPipeline.py` and
    `vamsExecuteCosmosVideo2WorldPipeline.py` referenced `external_task_token` in their `except`
    block before it was bound (raised `UnboundLocalError` instead of a clean 500 + failure callback
    when `TaskToken` is missing). Initialized `external_task_token = None` at the top of each
    handler, matching the `cosmos/reason`/`splatToolbox` pattern.

    All 12 use-case pipelines are now refactored. Next: remove the legacy SFN payload fields from
    `build_payload` and bump `ASL_SCHEMA_VERSION` / `MANIFEST_SCHEMA_VERSION` once a redeploy of all
    pipelines is confirmed.

## Task-token callback model (preview pipelines) — verified working, not changed

`preview/3dThumbnail` and `preview/pcPotreeViewer` pass `TASK_TOKEN: sfn.JsonPath.taskToken` into
their AWS Batch container overrides. Supplying a task token in `containerOverrides` makes the
`BatchSubmitJob` use the `.sync.waitForTaskToken` integration, so `$$.Task.Token` is a real
per-task callback token: the container completes its own task token on success (and on failure the
state's `.addCatch` routes to `pipelineEnd`, which reports failure on the threaded
`externalSfnTaskToken`). Both pipelines report success and failure correctly when run end to end,
including `pcPotreeViewer`'s two-stage `.e57`/`.ply` path (each Batch stage carries its own
per-task token, so the per-stage callback completes that stage's task and the chain advances).

This was briefly mis-analyzed as a `RUN_JOB` token bug and a one-line change was applied, then
reverted after confirming the live pipelines work as-is. Leave the `$$.Task.Token` wiring as it
is. Do **not** swap it for `$.externalSfnTaskToken`: the container calls its terminal callback
once per stage (unconditionally), so the real external token must stay on the heartbeat-only path
and `pipelineEnd` owns the external-token completion — using the external token in `TASK_TOKEN`
would complete the parent task after the first stage.

## What changes outside the pipelines (later)

Removing the legacy `build_payload` fields and bumping `ASL_SCHEMA_VERSION` /
`MANIFEST_SCHEMA_VERSION` happens only after all pipelines migrate. Until then, the schema
versions let a later migration detect workflows/pipelines whose deployed state machine predates
the manifest contract and flag them for redeploy.

## `outputType` removed from the pipeline-task body

`outputType` is no longer emitted into the Step Functions pipeline-task body. The shared
`build_payload` (and the workflow-update path) only carry the execution-level fields and the
input-location references; `outputType` remains a pipeline/workflow definition field (still set in
the pipeline registration and read by the VAMS-internal `process-outputs` end-state), but is no
longer passed to the pipeline at execution time.

Pipelines that internally need an output format now read it from their input configuration: the
registration custom resource carries `outputType` inside the `inputParameters` JSON, which the
workflow writes to the per-pipeline `config.json` and delivers via `inputConfigurationS3Location`.
The consuming pipeline reads `outputType` from the fetched configuration
(`manifestHelper.fetch_input_configuration`), falling back to the legacy threaded value for
executions whose state machine predates this change. `outputType` is a VAMS-reserved key within the
input configuration; pipelines that write the remainder of the configuration to a tool config file
(e.g. RapidPipeline's `rp_config.json`) pop `outputType` out first so it does not reach the tool.

Pipelines migrated to read `outputType` from the input configuration: `conversion/3dBasic`,
`multi/rapidPipeline`, `multi/rapidPipelineEKS`. Pipelines that only extracted `outputType` and
never used it had the dead read removed: `conversion/meshCadMetadataExtraction`,
`preview/3dThumbnail`, `preview/pcPotreeViewer`, `multi/modelOps`, `genAi/metadata3dLabeling`.

## Lean SFN pipeline-task body

The per-pipeline Step Functions task body carries only what is unavailable from the manifest:
the manifest and per-pipeline input-configuration S3 locations (`inputManifestS3Location`,
`inputConfigurationS3Location`), the workflow-execution I/O bucket
(`workflowExecutionS3InputOutputBucket` — where the ASL pulls the manifest/config files from and
where the shared output folder lives), the workflow/execution identifiers (`workflowDatabaseId`,
`workflowId`, `workflowExecutionId`), the executing-user context, and the callback `TaskToken`.
The auxiliary bucket is NOT threaded in the body — it lives in the manifest (`manifest.auxBucket`),
resolved by the interim lambda. No single triggering input-file key travels in the body —
it is input-file-agnostic and multi-file-ready. Everything describing the pipeline's inputs and
outputs — the resolved input files (per-file `bucket`/`key`/`versionId`/`databaseId`/`assetId`/
`assetRootS3Key`/`auxPreviewPrefix`, so inputs may span buckets), the output locations (a single
output bucket + bucket-relative prefixes), `auxBucket` + `auxTempPrefix`, the asset identity, and
the orchestration config — is read from the manifest at `inputManifestS3Location` via
`manifestHelper.resolve_pipeline_inputs`, which reconstructs the `s3://` forms the pipeline
forwards. The manifest is rebuilt per pipeline (pipeline 1 at launch, pipeline N+1 by the interim
lambda), so each step's manifest is specific to that step.

The orchestration bus ARN + event source prefix are NOT threaded through the SFN input either:
they live in the manifest's `systemConfig` (for pipelines) and in the interim tracking lambda's
environment (`ORCHESTRATION_BUS_ARN`, `ORCHESTRATION_EVENT_SOURCE_PREFIX`, for building each
next-pipeline manifest) — each per its intended purpose.

### Relative locations, reconstructed downstream

The manifest never carries pre-built `s3://` URIs. The `outputs` block carries a single `bucket`
plus bucket-relative prefixes; `auxBucket` is the auxiliary bucket name; `auxTempPrefix` is a
bucket-relative, execution-scoped working prefix (`pipelines/{pipelineName}/{execId}/`); and each
input file carries a bucket-relative `assetRootS3Key` and its own unique `auxPreviewPrefix`
(`{databaseId}/{assetFileKey}/preview`, where `assetFileKey` is the full asset-bucket key so a
custom asset base prefix is preserved). `manifestHelper.resolve_inputs`
reconstructs the flat `s3://` fields the pipelines forward (`outputS3AssetFilesPath`,
`inputOutputS3AssetAuxiliaryFilesPath`, and — for preview/viewer pipelines — `auxPreviewS3Path`,
which combines `auxBucket` + the input file's `auxPreviewPrefix` + the per-pipeline
`auxPreviewPipelinePrefix`). Pipelines that hardcoded a viewer aux path (e.g. `pcPotreeViewer`'s
`/PotreeViewer`) now read `auxPreviewS3Path`; the viewer subfolder will come from the pipeline
configuration via `auxPreviewPipelinePrefix` (empty for now).

### Single-file guard (multi-file-ready SFN, single-file pipelines)

The SFN + manifest layer is engineered for multi-file inputs (a manifest may carry many
`inputFiles`), but the use-case pipelines still process one file per execution. Each `vamsExecute`
lambda calls `manifestHelper.enforce_single_input_file(resolved)` right after resolving, which
raises a clear error if the manifest supplies more than one input file. When per-pipeline
multi-file support becomes a workflow/pipeline configuration flag, that guard is relaxed per
pipeline; until then it fails fast rather than silently processing only the first file.

Use-case pipelines read their inputs from the manifest and translate to whatever their downstream
layer needs. `conversion/meshCadMetadataExtraction` (which had no manifest helper) was migrated to
read the input file + output-metadata locations from the manifest, with the legacy body fields as
the fallback for direct/local invocations.

## Output-target identity in the manifest + SFN

The manifest envelope carries an `outputTarget` block (`locationType`, `assetId`, `databaseId`,
`fileBaseExecutionPathExtension`) identifying where the execution's outputs are written.
`locationType` is `asset` today and the target equals the input asset, but it is threaded
explicitly: `executeWorkflow` writes it into the pipeline-1 manifest and into the top-level SFN
input (the top-level copy is read only by the end-state `processWorkflowExecutionOutput` lambda,
which has no manifest of its own); the interim lambda carries it into each subsequent manifest; the
process-outputs payload carries
`outputLocationType`/`outputAssetId`/`outputDatabaseId`/`outputFileBaseExecutionPathExtension`; and
the end-state lambda resolves its output asset from those fields (falling back to the input asset
for older state machines). This removes the prior implicit assumption that the output target is
always the input asset, and keeps the output-target identity in exactly one channel per consumer
(the manifest for pipelines, the SFN top-level for the end-state lambda) rather than duplicated.

`outputFileBaseExecutionPathExtension` is a path segment inserted between the output asset's
location key and each output file's relative path when outputs are written back to the asset
(final key = `assetLocationKey + extension + relativePath`). It defaults to `/` (no extra segment)
and is stored on the workflow execution configuration row alongside the output-target identity. It
applies to asset FILE outputs (path-structured); preview outputs are basename-only and unaffected.
The recorded output-file `relativeFilePath` includes the extension so output provenance and the
asset file version-history join stay aligned with the actual write location. It is reserved for a
future feature that writes an execution's outputs under a per-execution sub-folder of the asset.

## Data migration — existing aux preview files

The auxiliary-bucket preview layout changed. Preview/viewer data is now written per input file at
`{databaseId}/{assetId}/{relativeAssetFileKey}/preview` (with an optional per-pipeline viewer
subfolder appended from `auxPreviewPipelinePrefix`, e.g. `/PotreeViewer`), keyed on database +
asset + the file's asset-relative path. Existing deployments wrote preview data under the older
file-key-based layout (e.g. `{inputAssetFileKey}/preview/PotreeViewer`).

**A data migration script is required** to move existing aux preview files from the old location to
the new `{databaseId}/{assetId}/{relativeAssetFileKey}/preview` location so viewers (e.g. the Potree
octree viewer, which reads octree files directly from the auxiliary bucket) keep resolving after the
refactor deploys. The migration belongs under `infra/deploymentDataMigration/` alongside the other
version-to-version migrations and must be paired with the frontend viewer's aux-path resolution
(the viewer read path is out of scope for the execution-side refactor and moves with the overhaul).

## Future changes list

-   Drop the legacy `inputMetadata` / `inputParameters` / `outputType` fallbacks once all pipelines
    are redeploy-confirmed, and bump `ASL_SCHEMA_VERSION` / `MANIFEST_SCHEMA_VERSION` (see above).
-   Source `auxPreviewPipelinePrefix` from the pipeline table/configuration (currently always empty)
    so viewer pipelines like `pcPotreeViewer` get their `/PotreeViewer` subfolder without hardcoding
    it in pipeline code.
-   Relax the `enforce_single_input_file` guard per pipeline once multi-file input becomes a
    workflow/pipeline configuration flag (the SFN + manifest layer is already multi-file-ready).
-   Rename the pipeline definition's `inputParameters` field to `inputConfiguration` (pipelines
    table + workflow/pipeline models, registration custom resources, and the execute-time override
    `pipelineInputParameters`). The execution layer already treats this value as the per-pipeline
    "input configuration" (written to `config.json`, delivered via `inputConfigurationS3Location`);
    the field name on the pipeline/workflow level is the remaining inconsistency to reconcile.
-   Support a divergent output target (an output asset different from the input asset). The plumbing
    is in place (`outputTarget` in the manifest, `outputAssetId`/`outputDatabaseId`/
    `outputLocationType` through the SFN, honored by the end-state lambda); what remains is to let
    the execute request specify a different output asset and to write/validate those values in the
    workflow execution configuration row.
-   `3dRecon/splatToolbox`'s `sqsExecute` auto-trigger lambda was removed (old, unused). If
    auto-trigger-on-upload is needed again, re-add it through the standard auto-trigger path.
