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
| `genAi/nvidia/cosmos/predict,reason,transfer` | No — forces `inputMetadata=''` | Lambda extracts prompt   | Location-only; prompt extraction stays in the lambda                  |
| `genAi/nvidia/cosmos/3`       | No — extracts COSMOS3_* at boundary  | Yes — container reads config     | Thread locations; container reads `inputParameters` from S3          |
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

5.  **`genAi/nvidia/cosmos/3` (Cosmos 3 omni)** (done). Brought to the standard when it merged in:
    vendored `manifestHelper.py` + a container `manifest_io.py`; `vamsExecute` resolves inputs via
    the manifest, enforces single-file, extracts the COSMOS3_* generation fields (prompt, seed,
    guidance, control-signal fields) at the boundary from S3-read metadata (inline fallback), and
    threads the metadata + input-configuration S3 locations + `orchestrationEventPrefix`;
    `openPipeline` threads the locations into the nested SFN input and registers its sub-SFN;
    `constructPipeline` carries the locations (no inline content); the container reads
    `inputParameters` (INVALIDATE_COSMOS_MODELS / DISABLE_GUARDRAILS / GENERATE_PREVIEW_GIF /
    TASK_MODE / MODEL_VARIANT fallbacks) from S3 via the vendored `manifest_io.py` (Dockerfile ships
    it). CDK: the `openPipeline` builder now wires `ORCHESTRATION_BUS_NAME` /
    `STATE_MACHINE_LOG_GROUP_NAME` / `STATE_MACHINE_LOG_GROUP_ARN` and `grantPutEventsTo` the bus.

    All 13 use-case pipelines are now refactored. Next: remove the legacy SFN payload fields from
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
`auxPreviewPipelineSuffix`). Pipelines that hardcoded a viewer aux path (e.g. `pcPotreeViewer`'s
`/PotreeViewer`) now read `auxPreviewS3Path`; the viewer subfolder will come from the pipeline
configuration via `auxPreviewPipelineSuffix` (empty for now).

### Per-pipeline aux preview suffix (future workflow/pipeline overhaul)

`auxPreviewPipelineSuffix` is the manifest field that lets a pipeline write its preview/viewer data
into a viewer-specific subfolder of the per-input-file aux preview location. It is **appended** to
each input file's `auxPreviewPrefix`, so the resolved `auxPreviewS3Path` a pipeline receives is
`s3://{auxBucket}/{databaseId}/{assetFileKey}/preview/{auxPreviewPipelineSuffix}` (e.g. a suffix of
`/PotreeViewer` yields `.../preview/PotreeViewer`). It is a **per-pipeline-task** value: each
pipeline task in a workflow carries its own suffix drawn from that pipeline's configuration, so two
viewer pipelines writing into the same asset's aux preview area do not collide.

**Current state (execution side, implemented now).** The field exists end to end and defaults to
empty:

-   The manifest envelope carries `auxPreviewPipelineSuffix` (`build_manifest_envelope`), written
    empty by `executeWorkflow` for pipeline 1 and by the interim lambda for pipelines 2+.
-   `manifestHelper.resolve_inputs` reads it and builds `auxPreviewS3Path` = `auxBucket` +
    the input file's `auxPreviewPrefix` + the suffix.
-   `pcPotreeViewer`'s `vamsExecute` reads the resolved suffix and, **when it is empty, falls back
    to a hardcoded `/PotreeViewer`** so the viewer keeps working until the field is populated. Once
    the configuration supplies a non-empty suffix, the manifest value wins and the fallback is
    inert.

**What the workflow/pipeline overhaul must add (future).** The suffix is currently always empty
because the pipeline/workflow definitions do not yet carry it. When those are overhauled:

1.  Add a per-pipeline `auxPreviewPipelineSuffix` (name TBD) to the pipeline definition /
    configuration model (pipelines table + workflow/pipeline models + registration custom
    resources), so each pipeline declares its viewer subfolder (e.g. `pcPotreeViewer` →
    `/PotreeViewer`).
2.  **Back-integrate it into the execution side:** `executeWorkflow` populates the pipeline-1
    manifest's `auxPreviewPipelineSuffix` from that pipeline's configuration, and the interim
    lambda populates each pipeline N+1 manifest's suffix from pipeline N+1's configuration (the
    suffix updates per pipeline task, matching how the manifest is rebuilt per pipeline). Both
    currently pass an empty string at the single call site each, so this is a localized change.
3.  Remove the hardcoded `/PotreeViewer` fallback in `pcPotreeViewer`'s `vamsExecute` once every
    deployed pipeline definition supplies the suffix.

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

## Input-configuration template tags

A pipeline's input configuration (today the `inputParameters` JSON string; later the upgraded
pipeline input-configuration field — see [future changes](#future-changes-list)) may contain
`{{tagName}}` template tags that the execution layer substitutes, **per pipeline task**, with values
drawn from that task's resolved manifest + execution context. This lets a pipeline ship a ready-made
configuration file with placeholders instead of reconstructing it field-by-field in its
`vamsExecute` lambda — useful for pipelines that load a fixed config file (e.g. an OpenJD template
for a future Deadline Cloud integration).

### Mechanism

-   **Format-agnostic text substitution.** Rendering operates on the raw configuration TEXT, so it
    works regardless of format (JSON today; YAML / OpenJD later). The shared renderer lives in
    `common/workflows/templateRender.py` (`render_config`); the recognized tag NAMES are defined as
    constants in `common/workflows/templateTags.py` (the single source of truth — call sites import
    the constants rather than hard-coding tag strings).
-   **Where it runs (both wired now).** Pipeline 1's configuration + the templated
    `outputFileBaseExecutionPathExtension` are rendered in `executeWorkflow` at launch (against
    pipeline 1's manifest). Pipelines 2+ are rendered in the **interim tracking lambda**: the raw
    config written at launch is read, rendered against that pipeline task's own manifest (with
    shadowed inputs), and re-written in place before the pipeline runs. So each task's tags reflect
    _its_ manifest.
-   **Two substitution kinds.** A **scalar** tag substitutes a JSON-string-escaped bare value meant
    to sit inside existing quotes (`"databaseId": "{{firstAssetFileDatabaseId}}"`); an **array/object**
    tag substitutes a JSON literal meant to sit WITHOUT surrounding quotes
    (`"files": {{assetFileKeyArray}}`). Each tag's kind is fixed (documented below).
-   **Strict.** An unknown `{{tag}}` (one not in the catalog) raises an error rather than being left
    in place or blanked — this surfaces typos and reserves the namespace for the future dynamic tags
    (below). At the execution layer the interim render error is caught by the interim state's `Catch`
    and reconciled as a workflow failure.
-   **Absent source → empty, never error.** A _defined_ tag whose underlying value is absent (e.g.
    `{{firstAssetFileAssetId}}` on a no-input-files execution) resolves to an empty string / `[]` /
    `0`. This is what makes no-input-files executions render cleanly.
-   **Metadata content is read lazily.** Metadata-content tags trigger a single metadata-file read
    only when such a tag is actually present in the configuration text.

### Tag catalog

Scalar tags (substitute inside quotes):

| Tag | Value |
| --- | --- |
| `{{executionId}}` | Workflow execution id |
| `{{workflowId}}` / `{{workflowDatabaseId}}` | Workflow id / its database id |
| `{{triggerType}}` | `Manual` / `File-Upload` |
| `{{executingUserName}}` | Launching user (or `SYSTEM_USER`) |
| `{{pipelineExecutionId}}` | This pipeline task's execution id |
| `{{pipelineId}}` / `{{pipelineName}}` | Pipeline definition name (aliases) |
| `{{pipelineDatabaseId}}` | Pipeline's database id |
| `{{jobName}}` | ASL-generated per-pipeline job name |
| `{{jobStartTimestamp}}` / `{{jobStartTimestampUnix}}` / `{{jobStartDate}}` | Render-time UTC timestamp (ISO-8601 / epoch seconds / `YYYY-MM-DD`) |
| `{{executionStartTimestamp}}` | Workflow execution start (ISO-8601 UTC) |
| `{{firstAssetFileDatabaseId}}` | First input file's database id |
| `{{firstAssetFileAssetId}}` | First input file's asset id |
| `{{firstAssetFileAssetBucket}}` | First input file's bucket |
| `{{firstAssetFileAssetRootS3Key}}` | First input file's bucket-relative asset root key |
| `{{firstAssetFileRelativePath}}` | First input file's asset-relative path |
| `{{firstAssetFileKey}}` | First input file's full asset-bucket key |
| `{{firstAssetFileVersionId}}` | First input file's S3 version id |
| `{{firstAssetFileAuxPreviewPrefix}}` | First input file's bucket-relative aux preview prefix |
| `{{firstAssetFileS3Uri}}` | `s3://{bucket}/{key}` of the first input file |
| `{{firstAssetFileAuxPreviewS3Uri}}` | `s3://{auxBucket}/{auxPreviewPrefix}[/{suffix}]` of the first input file |
| `{{firstAssetFileFileName}}` / `{{firstAssetFileFileNameNoExt}}` / `{{firstAssetFileFileExtension}}` | First input file's basename / stem / extension |
| `{{outputBucket}}` | Output bucket name |
| `{{outputFilesPrefix}}` / `{{outputFilesS3Uri}}` | Output files relative prefix / full s3:// |
| `{{outputPreviewsPrefix}}` / `{{outputPreviewsS3Uri}}` | Output previews relative prefix / s3:// |
| `{{outputMetadataPrefix}}` / `{{outputMetadataS3Uri}}` | Output metadata relative prefix / s3:// |
| `{{outputResultsPrefix}}` / `{{outputResultsS3Uri}}` | Output results relative prefix / s3:// |
| `{{outputTargetAssetId}}` / `{{outputTargetDatabaseId}}` | Output-target asset id / database id (the identity basis when there are no input files) |
| `{{outputTargetLocationType}}` | Output-target location type (`asset`) |
| `{{outputTargetAssetRootS3Key}}` | Output-target asset root key |
| `{{outputFileBaseExecutionPathExtension}}` | Output base-execution path extension |
| `{{auxBucket}}` | Auxiliary bucket name |
| `{{auxTempPrefix}}` / `{{auxTempS3Uri}}` | Execution-scoped aux temp working prefix / s3:// |
| `{{auxPreviewPipelineSuffix}}` | Per-pipeline aux preview viewer suffix |
| `{{inputMetadataS3Location}}` | Shared input-metadata file s3:// |
| `{{inputConfigurationS3Location}}` | This task's input-configuration file s3:// |
| `{{orchestrationBusArn}}` / `{{orchestrationEventPrefix}}` | Orchestration bus ARN / per-execution+pipeline event prefix |

Array / object tags (substitute a JSON literal, unquoted). All array tags reflect **every** input
file in order; `Unique` variants de-duplicate:

| Tag | Value |
| --- | --- |
| `{{assetFileKeyArray}}` | Full asset-bucket keys |
| `{{assetFileRelativePathArray}}` | Asset-relative paths |
| `{{assetFileS3UriArray}}` | `s3://bucket/key` per file |
| `{{assetFileVersionIdArray}}` | Version ids per file |
| `{{assetFileObjectArray}}` | Full manifest entry objects |
| `{{assetFileAssetIdArray}}` / `{{assetFileUniqueAssetIdArray}}` | Asset ids per file / de-duplicated |
| `{{assetFileDatabaseIdArray}}` / `{{assetFileUniqueDatabaseIdArray}}` | Database ids per file / de-duplicated |
| `{{assetFileCount}}` | Integer count of input files |

Metadata-content tags (JSON object literals; trigger a lazy metadata read; empty object when
absent):

| Tag | Value |
| --- | --- |
| `{{inputMetadataObject}}` | Full metadata payload (envelope unwrapped) |
| `{{assetMetadataObject}}` | Asset-level metadata k/v map |
| `{{fileMetadataObject}}` | File-level metadata k/v map |
| `{{fileAttributesObject}}` | File-attributes k/v map |
| `{{assetDataObject}}` | Asset data block (assetName / description / tags) |

Deadline Cloud tags (scalar) — **defined now, empty until the pipeline configuration supplies
them.** These are reserved so a Deadline Cloud OpenJD template can be authored against them today
(they do not trip the strict unknown-tag check); a future pipeline system-configuration overhaul
populates the pipeline's farm / queue / storage profile and the renderer fills these from that
configuration:

| Tag | Value |
| --- | --- |
| `{{deadlineFarmId}}` | Deadline Cloud farm id (empty until configured) |
| `{{deadlineQueueId}}` | Deadline Cloud queue id (empty until configured) |
| `{{deadlineStorageProfileId}}` | Deadline Cloud storage profile id (empty until configured) |

### Fields rendered today

The renderer runs on the pipeline input configuration content **and** the output-path field
`outputFileBaseExecutionPathExtension` (so `/{{executionId}}/` or `/{{jobStartTimestamp}}/` sub-folder
layouts are possible). The rendered extension is reflected into the manifest `outputTarget` and the
SFN input so all consumers agree.

### No-input-files executions

The execution system supports an execution with **zero input files** — the input configuration
and/or metadata is the only input (there may still be output files). In that case the manifest
carries `inputFiles: []`; every `{{firstAssetFile*}}` tag resolves to an empty string, every array
tag to `[]`, and `{{assetFileCount}}` to `0`. Identity-based key lookups pivot to the **output
target** (`{{outputTargetAssetId}}` / `{{outputTargetDatabaseId}}`) rather than the inputs, since
those are always present. Use-case pipelines still enforce their own input-arity requirement via the
manifest-helper gate (today `enforce_single_input_file`); a no-input pipeline skips that gate.

### Future dynamic tags (documented, not yet implemented)

Two dynamic-tag families are reserved and **error today** (strict unknown-tag check), to be added
when the pipeline/workflow configuration system is overhauled:

-   **`{{metadata_<key>}}`** — a scalar lookup into the flattened metadata payload (e.g.
    `{{metadata_location}}` → the `location` metadata value), so a config can pull an individual
    metadata field without embedding the whole object.
-   **User-defined per-pipeline tags** — arbitrary `{{...}}` names declared on the pipeline
    definition and swapped at runtime, enabling per-pipeline dynamic configuration (e.g. an OpenJD
    template whose parameters are pipeline-declared).

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
subfolder appended from `auxPreviewPipelineSuffix`, e.g. `/PotreeViewer`), keyed on database +
asset + the file's asset-relative path. Existing deployments wrote preview data under the older
file-key-based layout (e.g. `{inputAssetFileKey}/preview/PotreeViewer`).

**A data migration script is required** to move existing aux preview files from the old location to
the new `{databaseId}/{assetId}/{relativeAssetFileKey}/preview` location so viewers (e.g. the Potree
octree viewer, which reads octree files directly from the auxiliary bucket) keep resolving after the
refactor deploys. The migration belongs under `infra/deploymentDataMigration/` alongside the other
version-to-version migrations and must be paired with the frontend viewer's aux-path resolution
(the viewer read path is out of scope for the execution-side refactor and moves with the overhaul).

## Deadline Cloud creation enablement (with the pipeline/workflow table overhaul)

The execution layer already supports a fourth pipeline execution type, **DeadlineCloud**
(async-only): `DeadlineCloudTaskBuilder` in `stepfunctions_builder.py` emits an
`aws-sdk:deadline:createJob.waitForTaskToken` task state that flattens the shared SFN body
envelope into reserved string-typed OpenJD job parameters, and the `deadlineCloudJobCallback`
lambda (rule on the **default** bus, `source aws.deadline` / `Job Run Status Change`,
terminal `taskRunStatus` values) resolves the task token via `GetJob` →
`SendTaskSuccess`/`SendTaskFailure` and registers the job on the orchestration bus as the
pipeline execution's sub-process (`resourceType: deadlineCloudJob`, farmId/queueId/jobId).
Deployment is gated by `app.pipelines.deadlineCloudExecutionTypeEnabled`
(feature switch `DEADLINECLOUD_PIPELINES`; rejected in GovCloud). The reserved job-parameter
contract a registered template must declare (all `STRING` type):

| OpenJD job parameter                                                    | Source                                               |
| ----------------------------------------------------------------------- | ---------------------------------------------------- |
| `VamsWorkflowDatabaseId` / `VamsWorkflowId` / `VamsWorkflowExecutionId` | workflow-execution identity                          |
| `VamsWorkflowExecutionS3InputOutputBucket`                              | execution I/O bucket                                 |
| `VamsExecutingUserName` / `VamsExecutingRequestContext`                 | executing-user context (context is serialized JSON)  |
| `VamsInputManifestS3Location` / `VamsInputConfigurationS3Location`      | per-pipeline manifest + config                       |
| `VamsTaskToken`                                                         | Step Functions task token (job must NOT alter it)    |
| `VamsPipelineExecutionId`                                               | pipeline-execution row id (sub-process registration) |

What remains — **creation-side enablement**, to land with the pipeline/workflow table
overhaul (there is intentionally no way to create a `DeadlineCloud` pipeline until then):

-   Extend `PipelineExecutionType` (`models/pipelines.py`) with `"DeadlineCloud"` and the
    create-request fields: `deadlineFarmId`, `deadlineQueueId`, template reference,
    `deadlineTemplateType` (`JSON`|`YAML`), optional `deadlinePriority`,
    `deadlineMaxRetriesPerTask`, `deadlineMaxFailedTasksCount`, `deadlineStorageProfileId`.
    The root validator must force `waitForCallback = "Enabled"` for this type (the builder
    also rejects non-callback Deadline pipelines). Add `DEADLINE_FARM_ID` / `DEADLINE_QUEUE_ID`
    validators (`farm-[0-9a-f]{32}` / `queue-[0-9a-f]{32}`) next to the SQS/EventBridge ones.
-   The overhauled pipeline record's typed per-type execution configuration must adopt the
    field shape the builder already parses from the user resource: `resourceType:
"DeadlineCloud"`, `deadlineFarmId`, `deadlineQueueId`, `deadlineTemplate` (template
    **text** — the create path resolves an S3-stored template to text before ASL generation),
    `deadlineTemplateType`, plus the optional job settings above.
-   Store OpenJD templates **by reference** (S3 location + content hash on the pipeline
    record; small inline templates allowed with size validation — CreateJob caps the template
    at 1,000,000 characters) and validate at pipeline-create time that the template declares
    every reserved `Vams*` parameter.
-   Formalize generic external-job fields on pipeline-execution records
    (`externalJobType/Id/Arn/consoleDeepLink`) — the callback lambda's registration event
    already carries farmId/queueId/jobId through `registeredSubExecutions`.
-   Deep abort: `deadline:UpdateJob` (`targetTaskRunStatus=CANCELED`) using the registered
    farmId/queueId/jobId when an execution with a Deadline step is aborted.
-   `PipelineResponseModel`/`pipelineService` field extraction, web UI
    (`pipelineExecutionTypeOptions` + `appearsWhen` fields), `VAMS_API.yaml` +
    `api/pipelines.md`, configuration reference entry for
    `app.pipelines.deadlineCloudExecutionTypeEnabled`.
-   Operator documentation for the queue-role policy: the customer-owned Deadline queue role
    needs read on the execution input locations (manifest/config/metadata + asset files) and
    write on the execution output prefixes in the KMS-encrypted asset bucket. The default-bus
    events only arrive in the farm's own account/region, so the farm must live in the VAMS
    deployment account/region.

## Future changes list

-   Drop the legacy `inputMetadata` / `inputParameters` / `outputType` fallbacks once all pipelines
    are redeploy-confirmed, and bump `ASL_SCHEMA_VERSION` / `MANIFEST_SCHEMA_VERSION` (see above).
-   Source the per-pipeline `auxPreviewPipelineSuffix` from the pipeline configuration (see
    [Per-pipeline aux preview suffix](#per-pipeline-aux-preview-suffix-future-workflowpipeline-overhaul)
    below) so viewer pipelines like `pcPotreeViewer` get their `/PotreeViewer` subfolder without
    hardcoding it in pipeline code.
-   Introduce a per-pipeline / per-workflow **input-arity + asset-scope setting** that drives which
    manifest-helper gate each `vamsExecute` applies. Input arity: `none` (input configuration and/or
    metadata only — no input files) / `one` (today's single-file pipelines) / `multi`. Asset scope
    (for `multi`): `cross-asset` / `single-asset` (all files from one asset — the first file's
    databaseId + bucket then apply to all, and configs use `{{assetFileKeyArray}}`) /
    `whole-asset` (every file of an asset) / `folder` (a folder within an asset). The execution +
    manifest layer already supports zero/one/many input files and the output-target identity pivot;
    this setting formalizes per-pipeline validation (relaxing / replacing the current
    `enforce_single_input_file` gate) once the pipeline/workflow tables are overhauled.
-   Move template-tag rendering onto the upgraded pipeline **input-configuration** field (below) —
    the renderer (`common/workflows/templateRender.py`) already runs on the per-pipeline config
    text and the templated `outputFileBaseExecutionPathExtension`; it only needs to point at the
    renamed field. Then add the two reserved dynamic-tag families the renderer errors on today:
    `{{metadata_<key>}}` scalar lookups and user-defined per-pipeline tags (see
    [Input-configuration template tags](#input-configuration-template-tags)). User-defined tags pair
    with the OpenJD/Deadline Cloud template use case (pipeline-declared parameters swapped at runtime).
-   Rename the pipeline definition's `inputParameters` field to `inputConfiguration` (pipelines
    table + workflow/pipeline models, registration custom resources, and the execute-time override
    `pipelineInputParameters`). The execution layer already treats this value as the per-pipeline
    "input configuration" (written to `config.json`, delivered via `inputConfigurationS3Location`,
    template-tag-rendered per task); the field name on the pipeline/workflow level is the remaining
    inconsistency to reconcile.
-   Support a divergent output target (an output asset different from the input asset). The plumbing
    is in place (`outputTarget` in the manifest, `outputAssetId`/`outputDatabaseId`/
    `outputLocationType` through the SFN, honored by the end-state lambda); what remains is to let
    the execute request specify a different output asset and to write/validate those values in the
    workflow execution configuration row.
-   `3dRecon/splatToolbox`'s `sqsExecute` auto-trigger lambda was removed (old, unused). If
    auto-trigger-on-upload is needed again, re-add it through the standard auto-trigger path.
