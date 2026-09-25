# CLAUDE.md -- VAMS Pipeline Nested Stacks

Auto-loaded when Claude Code operates within `infra/lib/nestedStacks/pipelines/`. Covers pipeline stack layout, required Lambda package layout in `backendPipelines/`, VPC builder wiring, sub-process and log registration wiring, and S3 output path conventions. See `infra/CLAUDE.md` for cross-stack patterns (lambda builder, service helper, security helpers).

---

## Pipeline Nested Stack Pattern

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

Six pipelines run in isolated subnets today (3dBasic, CAD/mesh metadata extraction, Potree viewer, 3D thumbnail, GenAI metadata labeling, coordinate transform) and appear in block 2 only. Four run in private subnets (Splat Toolbox, NVIDIA Cosmos, NVIDIA GR00T, Isaac Lab training) and appear in all three. Regression coverage: `infra/test/pipelines/coordinateTransformVpcPlacement.test.ts`, which asserts both directions — no NAT for an isolated-subnet pipeline, NAT present for a private-subnet one.

### Sub-Process and Log Registration Wiring

The lambda that starts the pipeline's state machine (or submits a Batch job itself) registers its sub-process and log sources on the orchestration bus (`backendPipelines/CLAUDE.md` "Registering Sub-Processes and Logs"). Its builder supplies:

-   `ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName` and `orchestrationBus.grantPutEventsTo(fun)`.
-   The container log group env, from `lib/helper/batchJobLogGroup.ts`. A **Fargate** pipeline spreads `...vendedBatchJobLogGroupEnvironment(containerLogGroup)` — the VAMS-owned `/aws/vendedlogs/Pipelines/<Name><hash>` group it passed to `BatchFargatePipelineConstruct` as `logGroup`, whose `awslogs-stream-prefix` the construct sets to the physical job definition name (the Potree builder sets `PDAL_` / `POTREE_JOB_LOG_GROUP_NAME` / `_ARN` inline, one group per job). A **GPU** pipeline whose `CfnJobDefinition` sets no log configuration spreads `...batchJobLogGroupEnvironment()` — `BATCH_JOB_LOG_GROUP_NAME = "/aws/batch/job"` (AWS Batch's default) and `BATCH_JOB_LOG_GROUP_ARN` in the colon-separated `log-group:` form the backend's `CLOUDWATCH_LOG_GROUP_ARN` validator accepts. Never `formatArn(..., ArnFormat.SLASH_RESOURCE_NAME)`, which renders `log-group//aws/batch/job`. Registering a group the job definition does not write to is not caught at synth: `infra/test/pipelines/batchLogRegistrationEnvFargate.test.ts` asserts the registered group IS the job definition's `awslogs-group` and that its `awslogs-stream-prefix` equals `JobDefinitionName`.
-   `BATCH_JOB_DEFINITION_NAME` — the job definition **name**, passed from the construct as `{ jobDefinitionName }` (`OpenPipelineBatchLogProps`): Fargate `EcsJobDefinition` → `.jobDefinitionName`; a GPU `CfnJobDefinition` with a `jobDefinitionName` prop → the same string the prop was given; an unnamed `CfnJobDefinition` → `jobDefinitionNameFromRef(jobDef.ref)` (the Ref is the ARN with revision; the helper keeps `<name>` from `job-definition/<name>:<rev>`). A `:` in the value fails `LOG_STREAM_NAME` and leaves the container log source permanently `unscoped`.
-   A `lambda` log entry uses `` `/aws/lambda/${fn.functionName}` `` with `IAMArn(name).loggroup`; never `fn.logGroup` (synthesizes `Custom::LogRetention`).

The producer's `stageName` must equal the ASL state name, which is the CDK construct id of the Batch task (no construct sets `stateName`). Add the pipeline to `infra/test/pipelines/batchLogRegistrationEnvFargate.test.ts`, `batchLogRegistrationEnvGpu.test.ts` or `containerLogRegistrationEnvEcs.test.ts`: they synthesize one construct through `infra/test/support/pipelineConstructHarness.ts`, parse its ASL with `infra/test/support/asl.ts`, and assert the env is present and every module-level `*_STATE_NAME = "…"` literal in the producer (`declaredStageNames`; for cosmos the `COSMOS_BATCH_STATE_NAME` env value) is a key of `States`. Renaming a Batch construct without the producer fails those tests instead of silently breaking the stage ⇄ history join. The executionService role's read on `/aws/vendedlogs/Pipelines/*` (the Fargate container groups), on `/aws/batch/job` (the GPU containers) and `batch:DescribeJobs` is granted once in `lib/lambdaBuilder/workflowFunctions.ts`; a pipeline logging to another group needs a `/aws/vendedlogs/*` name that the existing allow-list covers (see `infra/CLAUDE.md` rule 8).

### Container Dockerfiles: a Non-Root `USER` Reads What It COPYs

Neither `BatchFargatePipelineConstruct` nor `batch-gpu-pipeline.ts` sets a user on the job definition, so the image's `USER` is what runs. A container Dockerfile that drops to a non-root `USER` therefore runs `RUN chmod -R a+rX <path>` on the line immediately after every `COPY` of source it reads: `docker COPY` preserves the build host's umask and copies files root-owned, so a hardened host (umask `077` STIG default / `027` locked-down CI) produces root-owned `600`/`640` files the non-root user cannot read — Python raises `PermissionError: [Errno 13]` at import (not `ModuleNotFoundError`; the directory is recreated `0755`). The build is green; only the job fails. `a+rX` adds no execute bit to plain files, and `COPY --chown=<user>` is NOT sufficient (owner-only read, mode still restrictive). Rule and worked examples: `backendPipelines/CLAUDE.md` "A Non-Root Container Normalizes Read Bits on the Source It COPYs".

### Pipeline S3 Output Path Conventions

The workflow ASL (built by `createWorkflow.py`) generates S3 paths for each pipeline step. The `vamsExecute` lambda and `constructPipeline` lambda must handle these correctly:

| Path                                   | Bucket    | Use For                                                                     |
| -------------------------------------- | --------- | --------------------------------------------------------------------------- |
| `outputS3AssetFilesPath`               | Asset     | File-level outputs: new files, file previews (`.previewFile.X`). Versioned. |
| `outputS3AssetPreviewPath`             | Asset     | Asset-level previews only (whole-asset representative image). Versioned.    |
| `outputS3AssetMetadataPath`            | Asset     | Metadata output. Versioned.                                                 |
| `inputOutputS3AssetAuxiliaryFilesPath` | Auxiliary | Temporary working files or special non-versioned viewer data only.          |

**Key distinction:** `outputS3AssetFilesPath` is for file-level outputs, including `.previewFile.gif/.jpg/.png` thumbnails tied to specific files. `outputS3AssetPreviewPath` is only for asset-level preview images representing the asset as a whole. Most pipelines producing file previews should write to `outputS3AssetFilesPath`.

**Rules:**

1. The `vamsExecute` lambda **must pass through** all output paths from the workflow payload to the `constructPipeline` lambda. Never hardcode empty strings — the workflow's process-output step depends on finding files at these locations.
2. The `constructPipeline` lambda should use the appropriate output path for the container's `outputFiles` stage definition: `outputS3AssetFilesPath` for file-level outputs (including `.previewFile.X` thumbnails), `outputS3AssetPreviewPath` for asset-level previews only. Fall back to `inputOutputS3AssetAuxiliaryFilesPath` only for direct/local invocations.
3. The **auxiliary path** (`inputOutputS3AssetAuxiliaryFilesPath`) is for temporary files during container processing or special non-versioned viewer data (e.g., Potree octree files that the frontend reads directly). It should **not** be used for standard pipeline outputs that flow through the workflow's process-output step.
4. Container IAM roles must have write access to the target buckets. The `inputBucketPolicy` in pipeline constructs typically grants read/write to all asset buckets; the `outputBucketPolicy` covers the auxiliary bucket.
5. **Containers must preserve the input file's relative path** when writing asset-adjacent outputs (e.g., `.previewFile.X` thumbnails). Asset files are stored at `{assetId}/{relative_dirs}/{filename}` — the relative subdirectory structure between the asset ID and filename must be maintained in the output S3 key. The process-output step expects outputs at the same relative location as the input. The `assetId` is a workflow state variable that must be **threaded through the entire chain** (vamsExecute → constructPipeline → pipeline definition → container) — never derive it from path segments. In the container, use the explicit `assetId` to find the split point in the input object key: `"/".join(input_parts[input_parts.index(assetId) + 1:-1])`.
