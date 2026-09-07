# CLAUDE.md -- VAMS Pipeline Nested Stacks

Auto-loaded when Claude Code operates within `infra/lib/nestedStacks/pipelines/`. Covers pipeline stack layout, required Lambda package layout in `backendPipelines/`, VPC builder wiring, and S3 output path conventions. See `infra/CLAUDE.md` for cross-stack patterns (lambda builder, service helper, security helpers).

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
