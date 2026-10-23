# CLAUDE.md -- VAMS Pipeline Nested Stacks

Auto-loaded when Claude Code operates within `infra/lib/nestedStacks/pipelines/`. Covers pipeline stack layout, required Lambda package layout in `backendPipelines/`, VPC builder wiring, sub-process and log registration wiring, and S3 output path conventions. See `infra/CLAUDE.md` for cross-stack patterns (lambda builder, service helper, security helpers).

---

## Pipeline Nested Stack Pattern

**In a fork, or for an external solution.** Pipelines have no custom lane and are not add-ons. A fork adds a pipeline without editing these core files by deploying it as its own stack and registering it through the API (`documentation/docusaurus-site/docs/pipelines/custom-pipelines.md`), or as an externally owned pipeline bridge (`app.pipelines.externalPipelineBridges`); the custom nested stack (`infra/lib/nestedStacks/custom/customResources.ts`) is not a pipeline seam. An upstream pipeline that calls a vendor's service is still a core pipeline behind its own `app.pipelines.*` switch. The `/add-pipeline` skill's fork mode and `documentation/docusaurus-site/docs/developer/customizing-vams.md` (Known Limits) say the same; the add-on definition is in `documentation/docusaurus-site/docs/developer/addons-overview.md`.

Each pipeline follows a consistent structure:

```
lib/nestedStacks/pipelines/{category}/{pipelineName}/
    {pipelineName}Builder-nestedStack.ts    # Stack definition
    constructs/
        {pipelineName}-construct.ts         # Infrastructure construct
    lambdaBuilder/
        {pipelineName}Functions.ts          # Lambda builder functions
```

**CRITICAL — Pipeline Lambda Directory Structure:** Every pipeline's `lambda/` directory in `backendPipelines/` MUST include:

```
lambda/
  __init__.py                    # Package marker (copy from existing pipeline)
  customLogging/
    __init__.py                  # Package marker
    logger.py                    # safeLogger + mask_sensitive_data (copy from existing pipeline)
  vamsExecute*.py                # Pipeline handler(s)
  constructPipeline.py           # Batch job definition builder
  openPipeline.py                # Step Functions starter
  pipelineEnd.py                 # Cleanup + task token callback
```

Without `__init__.py` and `customLogging/logger.py`, Lambda will fail at import time with `No module named 'customLogging'`. Copy these files from any existing pipeline (e.g., `backendPipelines/3dRecon/splatToolbox/lambda/`).

Pipelines are conditionally created in `pipelineBuilder-nestedStack.ts` based on config flags.

**CRITICAL — VPC Builder Updates:** A new pipeline using AWS Batch, ECS, or Fargate must be added to condition blocks in `lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts` — **which ones depends on the subnets its compute runs in.** Decide that first, by looking at what `pipelineBuilder-nestedStack.ts` passes as the pipeline's `pipelineSubnets`: `pipelineNetwork.isolatedSubnets.pipeline` or `pipelineNetwork.privateSubnets.pipeline`. Search for `useSplatToolbox` (private) and `usePreview3dThumbnail` (isolated) to see both treatments.

| Block                                                                       | Isolated-subnet pipeline | Private-subnet pipeline |
| --------------------------------------------------------------------------- | ------------------------ | ----------------------- |
| 1. **Subnet creation** — pushes `subnetPublicConfig`/`subnetPrivateConfig`  | **No**                   | **Yes**                 |
| 2. **Pipeline-only endpoints** — Batch, ECR API, ECR Docker, optionally EFS | **Yes**                  | **Yes**                 |
| 3. **ECS endpoint** — the `needsEcsPrivate` variable                        | **No**                   | **Yes**                 |

-   **Block 2 is required either way.** Without it, Batch jobs cannot pull their container image, and the pipeline fails at task start with no obvious cause.
-   **Block 1 for a private-subnet pipeline only.** `subnetPrivateConfig` is `PRIVATE_WITH_EGRESS` and the `ec2.Vpc` sets no `natGateways`, so CDK creates **one NAT gateway per Availability Zone** (~$66/month at the default two AZs, plus data processing). Add an isolated-subnet pipeline here and that cost is incurred for subnets its ENIs never occupy. Omit it for a private-subnet pipeline and its compute environment fails with `"Resource subnets are required"`.
-   **Block 3 for a private-subnet pipeline only.** This is the ECS **control-plane** endpoint, which the ECS agent on an EC2-launch-type container instance needs. **Fargate tasks do not use it** — they need ECR, Amazon S3 and CloudWatch Logs, which block 2 supplies. Each endpoint adds one ENI per AZ (~$15/month).

Four Batch pipelines run in isolated subnets (Potree viewer, 3D thumbnail, GenAI metadata labeling, coordinate transform) and appear in block 2 only. Eight run in private subnets (Splat Toolbox, ModelOps, RapidPipeline ECS and EKS, NVIDIA Cosmos, NVIDIA Cosmos 3, NVIDIA GR00T, and the GenAI CAD STEP agent on its `fargate` runtime) and appear in all three. Isaac Lab training runs its compute in private subnets and appears in blocks 1 and 2; its ECS endpoint is gated separately by `needsEcsIsolated`. The Lambda-container pipelines (3dBasic, CAD/mesh metadata extraction) use no Batch and appear in none; on its `agentcore` runtime the CAD STEP agent runs on Amazon Bedrock AgentCore outside the VPC and is likewise gated out of all three blocks. Regression coverage: `infra/test/pipelines/coordinateTransformVpcPlacement.test.ts`, which asserts both directions — no NAT for an isolated-subnet pipeline, NAT present for a private-subnet one.

### Sub-Process and Log Registration Wiring

The lambda that starts the pipeline's state machine (or submits a Batch job itself) registers its sub-process and log sources on the orchestration bus (`backendPipelines/CLAUDE.md` "Registering Sub-Processes and Logs"). Its builder supplies:

-   `ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName` and `orchestrationBus.grantPutEventsTo(fun)`.
-   The container log group env, from `lib/helper/batchJobLogGroup.ts`. A **Fargate** pipeline spreads `...vendedBatchJobLogGroupEnvironment(containerLogGroup)` — the VAMS-owned `/aws/vendedlogs/Pipelines/<Name><hash>` group it passed to `BatchFargatePipelineConstruct` as `logGroup`, whose `awslogs-stream-prefix` the construct sets to the physical job definition name (the Potree builder sets `PDAL_` / `POTREE_JOB_LOG_GROUP_NAME` / `_ARN` inline, one group per job). A **GPU** pipeline whose `CfnJobDefinition` sets no log configuration spreads `...batchJobLogGroupEnvironment()` — `BATCH_JOB_LOG_GROUP_NAME = "/aws/batch/job"` (AWS Batch's default) and `BATCH_JOB_LOG_GROUP_ARN` in the colon-separated `log-group:` form the backend's `CLOUDWATCH_LOG_GROUP_ARN` validator accepts. Never `formatArn(..., ArnFormat.SLASH_RESOURCE_NAME)`, which renders `log-group//aws/batch/job`. Registering a group the job definition does not write to is not caught at synth: `infra/test/pipelines/batchLogRegistrationEnvFargate.test.ts` asserts the registered group IS the job definition's `awslogs-group` and that its `awslogs-stream-prefix` equals `JobDefinitionName`.
-   `BATCH_JOB_DEFINITION_NAME` — the job definition **name**, passed from the construct as `{ jobDefinitionName }` (`OpenPipelineBatchLogProps`): Fargate `EcsJobDefinition` → `.jobDefinitionName`; a GPU `CfnJobDefinition` with a `jobDefinitionName` prop → the same string the prop was given; an unnamed `CfnJobDefinition` → `jobDefinitionNameFromRef(jobDef.ref)` (the Ref is the ARN with revision; the helper keeps `<name>` from `job-definition/<name>:<rev>`). A `:` in the value fails `LOG_STREAM_NAME` and leaves the container log source permanently `unscoped`.
-   A `lambda` log entry uses `` `/aws/lambda/${fn.functionName}` `` with `IAMArn(name).loggroup`; never `fn.logGroup` (synthesizes `Custom::LogRetention`).

The producer's `stageName` must equal the ASL state name, which is the CDK construct id of the Batch task (no construct sets `stateName`). Add the pipeline to `infra/test/pipelines/batchLogRegistrationEnvFargate.test.ts`, `batchLogRegistrationEnvGpu.test.ts` or `containerLogRegistrationEnvEcs.test.ts`: they synthesize one construct through `infra/test/support/pipelineConstructHarness.ts`, parse its ASL with `infra/test/support/asl.ts`, and assert the env is present and every module-level `*_STATE_NAME = "…"` literal in the producer (`declaredStageNames`; for cosmos the `COSMOS_BATCH_STATE_NAME` env value) is a key of `States`. Renaming a Batch construct without the producer fails those tests instead of silently breaking the stage ⇄ history join. The executionService role's read on `/aws/vendedlogs/Pipelines/*` (the Fargate container groups), on `/aws/batch/job` (the GPU containers) and `batch:DescribeJobs` is granted once in `lib/lambdaBuilder/workflowFunctions.ts`, with the log-group list taken from `app.pipelines.executionLogReadPrefixes` (default `DEFAULT_EXECUTION_LOG_READ_PREFIXES` in `config.ts`); a pipeline logging to another group needs a name an existing entry covers, or its group added to the templates and the default list together (see `infra/CLAUDE.md` Partition Portability item 8).

### CodeBuild Build Trigger: One Shared Handler

A pipeline built with `useCodeBuild: true` starts its image build from a custom resource behind `cr.Provider`. Every `*CodeBuild-construct.ts` gives its trigger function `code: codeBuildTriggerCode()` (`constructs/codeBuildTrigger.ts`), which inlines the one handler file `constructs/codeBuildTrigger/index.py`; never paste a handler of its own. The handler returns `{"Data": {"BuildId": ...}}`, retries `start_build` on throttling and service error codes and on connection and timeout faults only (any other `BotoCoreError` fails on the first attempt), and raises otherwise, which is what makes the provider report the resource `FAILED`. The client's `Config` (standard retry mode, 2 attempts, connect and read timeouts) and the handler's attempts and backoff together stay inside the trigger function's 1-minute timeout; the arithmetic is in the handler's comment, and the Python test holds it. A `cfnresponse.send(...)` call behind the provider framework reports to nothing, so a handler that uses it and catches its exceptions reports every failure as a success. A Delete starts nothing and never raises. The handler stays within Lambda's 4096-byte inline code limit (the helper throws at synth past it). Coverage: `infra/test/pipelines/codeBuildTriggerHandler.test.ts` (every construct uses the helper) and `backendPipelines/tests/test_codebuild_build_trigger_handler.py` (the handler's behaviour).

### Size-Tiered Fargate Compute (one job definition, one Batch state per tier)

The 3D thumbnail pipeline is the worked example (`preview/3dThumbnail/constructs/preview3dThumbnail-construct.ts`). One `BatchFargatePipelineConstruct` job definition is registered at the standard tier's size with `attemptDuration` = the longest tier's bound; each tier is its own `tasks.BatchSubmitJob` with fixed `containerOverrides.vcpus` / `memory` (aws-cdk-lib renders them as `ResourceRequirements` `VCPU` / `MEMORY` in MiB, the form AWS Batch accepts for Fargate — never the deprecated top-level `Vcpus`/`Memory`), a `VAMS_COMPUTE_TIER` env value and its own `taskTimeout`, behind a `sfn.Choice` on the tier the lambdas carry in the state. Ephemeral storage is a job-definition property and cannot be overridden per job, so size it for the largest tier. AWS Batch checks a vCPU/memory override against the Fargate task sizes only at submit, so every configurable tier size is validated in `getConfig()` with `isFargateCpuMemory` (`config/config.ts`, mirrored in the ConfigBuilder `validation.ts`). The state machine timeout covers the longest tier and the `vamsSchema` `taskTimeout` covers the state machine. Each tier's Batch construct id is a module-level `*_STATE_NAME` literal in the registering lambda, which registers the container log source of the state its tier routes to. Tests: `infra/test/pipelines/preview3dThumbnailComputeTiers.test.ts`, `infra/test/config/preview3dThumbnailComputeTierValidation.test.ts`.

### Container Dockerfiles: a Non-Root `USER` Reads What It COPYs

Neither `BatchFargatePipelineConstruct` nor `batch-gpu-pipeline.ts` sets a user on the job definition, so the image's `USER` is what runs. A container Dockerfile that drops to a non-root `USER` therefore runs `RUN chmod -R a+rX <path>` on the line immediately after every `COPY` of source it reads: `docker COPY` preserves the build host's umask and copies files root-owned, so a hardened host (umask `077` STIG default / `027` locked-down CI) produces root-owned `600`/`640` files the non-root user cannot read — Python raises `PermissionError: [Errno 13]` at import (not `ModuleNotFoundError`; the directory is recreated `0755`). The build is green; only the job fails. `a+rX` adds no execute bit to plain files, and `COPY --chown=<user>` is NOT sufficient (owner-only read, mode still restrictive). Rule and worked examples: `backendPipelines/CLAUDE.md` "A Non-Root Container Normalizes Read Bits on the Source It COPYs".

### Lambda Container Image Functions

The 3dBasic and CAD/mesh metadata extraction pipelines deploy as `lambda.DockerImageFunction`. Three settings name the instruction set and must agree: `FROM --platform=linux/amd64` in the Dockerfile, `platform: Platform.LINUX_AMD64` on the image asset, and `architecture: lambda.Architecture.X86_64` on the function. `infra/test/pipelines/t1LambdaContainerImageArchitecture.test.ts` fails any `PackageType: Image` function without `Architectures: ["x86_64"]` in all three templates. Lambda also rejects an image pushed as an OCI image index (`InvalidImage` / unsupported media type). The pinned AWS CDK CLI (2.179.0 and later) runs `docker build` for image assets with `BUILDX_NO_DEFAULT_ATTESTATIONS=1`, so Docker's default provenance attestation is not attached on any image store; anything that builds or pushes these images outside the CDK CLI must pass `--provenance=false`.

### Pipeline S3 Output Path Conventions

Each pipeline step reads the S3 paths below from its manifest, which `vamsExecute` resolves with `manifestHelper.resolve_pipeline_inputs`. `backend/backend/handlers/workflows/executeWorkflow.py` writes step 1's manifest, and `backend/backend/handlers/workflows/sfn/interimPipelineTracking.py` writes each later step's from the prefixes the workflow ASL (`backend/backend/common/workflows/workflowAslBuilder.py`) passes it. The pipeline task body carries none of these paths, only the manifest and input-configuration locations. The `vamsExecute` lambda and `constructPipeline` lambda must handle these correctly:

| Path                                   | Bucket    | Use For                                                                     |
| -------------------------------------- | --------- | --------------------------------------------------------------------------- |
| `outputS3AssetFilesPath`               | Asset     | File-level outputs: new files, file previews (`.previewFile.X`). Versioned. |
| `outputS3AssetPreviewPath`             | Asset     | Asset-level previews only (whole-asset representative image). Versioned.    |
| `outputS3AssetMetadataPath`            | Asset     | Metadata output. Versioned.                                                 |
| `outputS3AssetResultsPath`             | Asset     | Result files recorded as the execution's output results (not on the asset). |
| `inputOutputS3AssetAuxiliaryFilesPath` | Auxiliary | Temporary working files or special non-versioned viewer data only.          |

**Key distinction:** `outputS3AssetFilesPath` is for file-level outputs, including `.previewFile.gif/.jpg/.png` thumbnails tied to specific files. `outputS3AssetPreviewPath` is only for asset-level preview images representing the asset as a whole. Most pipelines producing file previews should write to `outputS3AssetFilesPath`.

**Rules:**

1. The `vamsExecute` lambda **must pass through** all output paths resolved from its manifest to the `constructPipeline` lambda. Never hardcode empty strings — the workflow's process-output step depends on finding files at these locations.
2. The `constructPipeline` lambda should use the appropriate output path for the container's `outputFiles` stage definition: `outputS3AssetFilesPath` for file-level outputs (including `.previewFile.X` thumbnails), `outputS3AssetPreviewPath` for asset-level previews only. Fall back to `inputOutputS3AssetAuxiliaryFilesPath` only for direct/local invocations.
3. The **auxiliary path** (`inputOutputS3AssetAuxiliaryFilesPath`) is for temporary files during container processing or special non-versioned viewer data (e.g., Potree octree files that the frontend reads directly). It should **not** be used for standard pipeline outputs that flow through the workflow's process-output step.
4. Container IAM roles must have write access to the target buckets. The `inputBucketPolicy` in pipeline constructs typically grants read/write to all asset buckets; the `outputBucketPolicy` covers the auxiliary bucket.
5. **Containers must preserve the input file's relative path** when writing asset-adjacent outputs (e.g., `.previewFile.X` thumbnails). An asset file's key is the asset's root key followed by its relative path; the root is usually `{baseAssetsPrefix}{assetId}/`, but an asset created on an existing bucket folder (`bucketExistingKey`) keeps that folder as its root, so its keys need not contain the asset ID. The process-output step expects outputs at the same relative location as the input. The `assetId` is a workflow state variable that must be **threaded through the entire chain** (vamsExecute, which resolves it from the manifest → constructPipeline → pipeline definition → container) — never derive it from path segments. In the container, use the explicit `assetId` to find the split point in the input object key, guarded for a key that does not contain it (an existing-folder asset, or a file an earlier workflow step wrote under the run's output folder): `"/".join(input_parts[input_parts.index(assetId) + 1:-1]) if assetId and assetId in input_parts else ""`. For such a key the search returns `""` and the output is written at the top of the output folder; to keep the subfolder, take it from the input file's manifest `relativePath` (`resolved['inputFiles'][0]['relativePath']` in vamsExecute, which `manifestHelper.resolved_file_key(resolved)` returns normalized). See `backendPipelines/CLAUDE.md` "`assetId` Threading".
