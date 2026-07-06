# Add VAMS Processing Pipeline

Scaffold a new VAMS processing pipeline with all required files following established patterns. This creates the complete pipeline structure including Lambda functions, container code, CDK infrastructure, Step Functions integration, VPC builder updates, config, and documentation.

## Instructions

You are scaffolding a new VAMS processing pipeline. Follow root `CLAUDE.md` "Adding a New Processing Pipeline" and `infra/CLAUDE.md` "Pipeline Nested Stack Pattern" — the authoritative checklists. VAMS pipelines process assets through AWS Step Functions state machines with Lambda orchestration and either Lambda containers or Batch/Fargate for heavy processing.

### Step 1: Gather Requirements

Ask the user for:

-   **Pipeline name**: A descriptive name in camelCase (e.g., `meshOptimizer`, `imageClassifier`, `pointCloudProcessor`)
-   **Pipeline category**: One of `conversion`, `preview`, `genAi`, `multi`, `3dRecon`, `simulation` (determines folder location)
-   **Input file types**: Which file extensions the pipeline processes (e.g., `.obj, .fbx, .stl`)
-   **Processing type**: `lambdaContainer` (for short tasks < 15min) or `batchFargate`/`batchGpu` (for long-running tasks or GPU)
-   **Description**: What the pipeline does
-   **GPU required**: Whether the container needs GPU access (affects `batch-gpu-pipeline` vs `batch-fargate-pipeline` construct)
-   **Output type**: File-level outputs (new files, `.previewFile.X` thumbnails), asset-level preview, metadata, or auxiliary/viewer data — this determines which output S3 path the pipeline writes to

### Step 2: Understand the Pipeline Architecture

Every VAMS pipeline follows this Step Functions flow:

```
vamsExecute (Lambda) -> openPipeline (Lambda) -> constructPipeline (Lambda) -> [Container Task] -> pipelineEnd (Lambda)
```

-   **vamsExecute**: The VAMS-facing Lambda invoked by the workflow execution system. Captures the workflow event (including `assetId` and all output paths) and starts the pipeline.
-   **openPipeline**: Starts the pipeline Step Functions state machine with input parameters and S3 paths.
-   **constructPipeline**: Prepares the container task definition (S3 paths, merged parameters).
-   **Container Task**: The actual processing — Lambda container or Batch/Fargate task in `backendPipelines/{category}/{pipelineName}/container/`.
-   **pipelineEnd**: Cleanup + Step Functions task token callback.

All four Lambdas live in `backendPipelines/{category}/{pipelineName}/lambda/`.

#### Pipeline S3 Output Paths (critical)

The workflow ASL passes these paths to each pipeline step. Use the correct one (see root `CLAUDE.md` "Pipeline S3 Output Paths"):

| Path                                   | Bucket    | Use For                                                                     |
| -------------------------------------- | --------- | --------------------------------------------------------------------------- |
| `outputS3AssetFilesPath`               | Asset     | File-level outputs: new files, file previews (`.previewFile.X`). Versioned. |
| `outputS3AssetPreviewPath`             | Asset     | Asset-level previews only (whole-asset representative image). Versioned.    |
| `outputS3AssetMetadataPath`            | Asset     | Metadata output. Versioned.                                                 |
| `inputOutputS3AssetAuxiliaryFilesPath` | Auxiliary | Temporary working files or special non-versioned viewer data only.          |

**Rules:**

1. The `vamsExecute` lambda **must pass through all output paths** from the workflow payload — never hardcode empty strings. The workflow's process-output step depends on finding files at these locations.
2. The `constructPipeline` lambda uses the appropriate output path for the container's output target, falling back to the auxiliary path only for direct/local invocations where workflow context is unavailable.
3. **Containers must preserve the input file's relative path** when writing asset-adjacent outputs. Asset files are stored at `{assetId}/{relative_dirs}/{filename}`; outputs must keep the same relative subdirectory so process-output can locate them.
4. **`assetId` is a workflow state variable — thread it, never derive it from S3 path segments**: vamsExecute captures it from the event body → constructPipeline includes it in the definition dict → container reads it from the PipelineDefinition and uses it to compute the relative subdirectory:

```python
# assetId comes from the pipeline definition (threaded from workflow state)
input_parts = stage_input.objectKey.split("/")
asset_id_idx = input_parts.index(assetId)
relative_subdir = "/".join(input_parts[asset_id_idx + 1:-1])  # "" if file is at asset root
```

### Step 3: Create Backend Pipeline Files

Create the following directory structure. **Every pipeline `lambda/` directory MUST include `__init__.py` and `customLogging/` package files** — without them, Lambda fails at import time with `No module named 'customLogging'`:

```
backendPipelines/
  {category}/
    {pipelineName}/
      lambda/
        __init__.py                       # Package marker (copy from existing pipeline)
        customLogging/
          __init__.py                     # Package marker
          logger.py                       # safeLogger (copy from e.g. backendPipelines/3dRecon/splatToolbox/lambda/customLogging/logger.py)
        vamsExecute{PipelineName}.py      # VAMS-facing entry (threads assetId + output paths)
        openPipeline.py                   # Step Functions starter
        constructPipeline.py              # Container/Batch job definition builder
        pipelineEnd.py                    # Cleanup + task token callback
      container/
        Dockerfile
        requirements.txt
        ...                               # Processing code + utils (copy utils/ from an existing pipeline)
```

**Copy the lambda files from a recent, similar pipeline** (e.g., `backendPipelines/conversion/coordinateTransform/` for Batch Fargate, `backendPipelines/3dRecon/splatToolbox/` for Batch GPU) and adapt them, rather than writing from scratch. When adapting, verify:

-   `vamsExecute` captures `assetId`, `databaseId`, and ALL output paths (`outputS3AssetFilesPath`, `outputS3AssetPreviewPath`, `outputS3AssetMetadataPath`, `inputOutputS3AssetAuxiliaryFilesPath`) from the workflow event body and includes them in the message payload — no hardcoded empty strings.
-   `constructPipeline` reads `assetId` from the event and includes it in the pipeline definition dict, and selects the correct output path for the container's output target.
-   The container reads `assetId` from the PipelineDefinition and preserves relative subdirectories in output S3 keys.
-   Standard container utilities (S3 download/upload, Step Functions task token helpers, logging) are copied from the reference pipeline's `container/utils/`.

### Step 4: Create CDK Infrastructure

Follow the pipeline nested stack pattern:

```
infra/lib/nestedStacks/pipelines/{category}/{pipelineName}/
    {pipelineName}Builder-nestedStack.ts    # Stack definition
    constructs/
        {pipelineName}-construct.ts         # Infrastructure construct
    lambdaBuilder/
        {pipelineName}Functions.ts          # Lambda builder functions
```

#### Pipeline Construct

Create `constructs/{pipelineName}-construct.ts` following an existing construct (e.g., `conversion/coordinateTransform/constructs/coordinateTransform-construct.ts` or `3dRecon/splatToolbox/`). The construct should:

1. Create the constructPipeline, openPipeline, and pipelineEnd Lambdas
2. Create the container task (Batch Fargate via `BatchFargatePipelineConstruct`, Batch GPU via `batch-gpu-pipeline`, or Lambda container)
3. Create the Step Functions state machine linking them
4. Create the vamsExecute Lambda that starts the state machine
5. Export `pipelineVamsLambdaFunctionName` for pipeline registration
6. Build container IAM policies: input bucket policy from the global asset bucket registry (`s3AssetBuckets.getS3AssetBucketRecords()`), output/auxiliary bucket policy, and Step Functions task-token policy
7. Support `autoRegisterWithVAMS` (custom resource registering the pipeline/workflow during deployment) and, if applicable, `autoRegisterAutoTriggerOnFileUpload`

#### Lambda Builder Functions

Create `lambdaBuilder/{pipelineName}Functions.ts` following existing pipeline lambda builders. Each function needs:

-   Standard signature with scope, layer, storageResources, config, vpc, subnets
-   Code path pointing to `backendPipelines/{category}/{pipelineName}/lambda`
-   The security helper calls, including `suppressCdkNagLambda(fun)` on every Lambda
-   Note: pipeline Lambdas use **legacy table-name environment variables** (they are excluded from SSM resource-name resolution)

#### Pipeline Nested Stack

Create `{pipelineName}Builder-nestedStack.ts`:

```typescript
import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { {PipelineName}Construct } from "./constructs/{pipelineName}-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../config/config";

export interface {PipelineName}NestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

export class {PipelineName}NestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;
    constructor(parent: Construct, name: string, props: {PipelineName}NestedStackProps) {
        super(parent, name);

        const pipeline = new {PipelineName}Construct(this, "{PipelineName}Pipeline", {
            ...props,
        });

        this.pipelineVamsLambdaFunctionName = pipeline.pipelineVamsLambdaFunctionName;
    }
}
```

### Step 5: Register Pipeline in Pipeline Builder

Update `infra/lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts`:

1. Add import for the new nested stack
2. Add config flag check: `if (props.config.app.pipelines.use{PipelineName}.enabled)`
3. Instantiate the nested stack with standard props
4. Add the `pipelineVamsLambdaFunctionName` to the `pipelineVamsLambdaFunctionNames` array

### Step 6: Update the VPC Builder (Batch/ECS/Fargate pipelines)

**CRITICAL:** Pipelines that use AWS Batch, ECS, or Fargate MUST be added to **all three** condition blocks in `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts`. Search for `useSplatToolbox` in the file to find all locations. Missing any one causes deployment failures:

1. **Subnet creation condition** (~line 341): the `if` block that pushes `subnetPublicConfig` and `subnetPrivateConfig` into `subnetConfigurations`. Without this, Batch compute environments fail with `"Resource subnets are required"`.
2. **VPC endpoint condition** (~line 610): the `if` block that creates Batch, ECR API, and ECR Docker interface VPC endpoints. Without this, Batch jobs cannot pull container images.
3. **ECS endpoint condition** (`needsEcsPrivate`, ~line 694): controls whether the ECS VPC endpoint includes private subnets. Without this, the ECS agent on Batch instances cannot register with the ECS service.

### Step 7: Add Config Flag

1. Add the pipeline block to the `ConfigPublic` interface in `infra/config/config.ts` under `pipelines`. Standard fields: `enabled`, `autoRegisterWithVAMS`, and where applicable `autoRegisterAutoTriggerOnFileUpload`, `sqsAutoRunOnAssetModified`, `useCodeBuild`.
2. Add a backward-compatibility `undefined` check with defaults in `getConfig()`.
3. Add validation in `getConfig()` if constraints exist. If the pipeline needs a VPC, add it to the `vpcRequiringFeatures` checks.
4. Update **ALL** config template files: `config.template.commercial.json`, `config.template.govcloud.json`, AND `config.template.eusovereign.json` — a missed template silently drops operator-set values.
5. Update `config.json` for the active deployment.

### Step 8: Update Documentation and Steering

1. **`documentation/docusaurus-site/docs/deployment/configuration-reference.md`**: add a section for the pipeline documenting every config option, following the existing pipeline-section format.
2. **`documentation/docusaurus-site/docs/pipelines/`**: create a new pipeline page, add it to `documentation/docusaurus-site/sidebars.ts`, and add the pipeline to the `pipelines/overview.md` table and `overview/features.md`.
3. **Root `CLAUDE.md`**: add the pipeline to the pipeline list (Rule 11).
4. If the pipeline added a VPC subnet/endpoint requirement, update the "VPC Resource Usage by Feature" tables in the configuration reference.

### Step 9: Validate

After creating all files, verify:

-   [ ] `lambda/` directory contains `__init__.py`, `customLogging/__init__.py`, and `customLogging/logger.py`
-   [ ] `vamsExecute` passes through all output paths (no hardcoded empty strings) and threads `assetId`
-   [ ] Container preserves relative subdirectories in output keys using the threaded `assetId`
-   [ ] Lambda handler paths in CDK match actual file locations in `backendPipelines/`
-   [ ] Step Functions state machine references correct Lambda ARNs
-   [ ] Container Dockerfile builds successfully
-   [ ] Config flag name matches between config.json, config templates, config.ts interface, and the pipelineBuilder check
-   [ ] Backward-compatibility defaults + validation in `getConfig()`
-   [ ] Pipeline nested stack is imported and registered in pipelineBuilder-nestedStack.ts
-   [ ] `pipelineVamsLambdaFunctionName` is pushed to the array for pipeline registration
-   [ ] VPC builder updated in all three condition blocks (Batch/ECS/Fargate pipelines)
-   [ ] `suppressCdkNagLambda` and CDK Nag suppressions with justified reasons on all resources
-   [ ] Documentation updated: configuration-reference.md, pipelines page, overview table, features.md, sidebars.ts, root CLAUDE.md

## Workflow

1. Gather requirements from the user (or parse from $ARGUMENTS)
2. Determine pipeline category and processing type; pick a reference pipeline to copy from
3. Create all backend pipeline files (lambda + container)
4. Create CDK infrastructure (construct, nested stack, lambda builder)
5. Register in pipelineBuilder-nestedStack.ts and update the VPC builder
6. Add config flag (interface, getConfig defaults/validation, ALL templates, config.json)
7. Update documentation and steering docs
8. Summarize created files and next steps

## User Request

$ARGUMENTS
