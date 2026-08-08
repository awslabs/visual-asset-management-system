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
-   **Per-run execution options**: which settings an operator should be able to choose at launch — a prompt, a seed, a quality preset, a target format, a mode. Each becomes a typed tag in a template's `tagSchema`, referenced as `{{tagName}}` in its config body (see Step 4, "Templates"). Ask specifically: does the pipeline generate **more than one output format or mode**? If so it wants several templates over one pipeline, not several pipelines.

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

-   `vamsExecute` resolves its inputs **from the manifest, not from the event body**. The Step Functions body carries only the workflow-execution identity, the I/O bucket, the executing-user context, `inputManifestS3Location`, `inputConfigurationS3Location`, and `TaskToken` (callback only). Input files, output paths, and asset identity come from `manifestHelper.resolve_pipeline_inputs(data, s3_client)`. Indexing the body for `inputS3AssetFilePath` or the output paths raises `KeyError` on the first invocation.
-   `vamsExecute` then forwards ALL resolved output paths (`outputS3AssetFilesPath`, `outputS3AssetPreviewPath`, `outputS3AssetMetadataPath`, `inputOutputS3AssetAuxiliaryFilesPath`) plus `assetId` / `databaseId` to `constructPipeline` — no hardcoded empty strings.
-   `constructPipeline` reads those forwarded keys from its own event, includes `assetId` in the pipeline definition dict, and selects the correct output path for the container's output target.
-   The container reads `assetId` from the PipelineDefinition and preserves relative subdirectories in output S3 keys.
-   Standard container utilities (S3 download/upload, Step Functions task token helpers, logging) are copied from a reference pipeline's container support package. **Do not name that package `utils`** if the container also vendors upstream third-party source — a top-level `utils` collides with an upstream `utils.py` on the same import name and one side's imports break. Use a distinct name (the Splat Toolbox container uses `vams_utils`).
-   `manifestHelper.py` is vendored per pipeline and must be byte-identical across pipelines; copy it, do not re-implement it.

Full field-by-field reference: [The pipeline input contract](../../documentation/docusaurus-site/docs/pipelines/custom-pipelines.md#the-pipeline-input-contract).

### Step 4: Author the vamsSchema Registration Bundle

**Without this the pipeline's AWS resources deploy but nothing appears in VAMS.** Registration is what
creates the pipeline, its templates, and a runnable workflow in the V2 tables. Create:

```
backendPipelines/{category}/{pipelineName}/vamsSchema/
    pipeline.json                  # required
    workflow.json                  # optional — one built-in workflow for the pipeline
    templates/{templateId}.json    # optional — one file per configuration template
```

`pipeline.json` carries **no ARNs or account ids** — the execution target is injected at deploy time
from `resourceOverrides` per `executionConfig.executionType`. Author the block for the type with its
resource fields empty (`"lambda": {}`, `"sqs": {}`, …). Copy the shape from
`backendPipelines/conversion/3dBasic/vamsSchema/pipeline.json`.

Verify these seven things, each of which silently produces an unusable pipeline when wrong:

1.  **`systemConfig.inputFileFilters.allow` matches the file types the container actually handles.**
    These globs are what the execute API and the file-upload trigger match against; a missing
    extension makes the pipeline unselectable for that type with no error.
2.  **A `requireTemplate: true` pipeline has a default template.** Execute auto-selects the default; with
    none, every caller must name a `templateId`. A bundle with exactly one template has it promoted
    automatically — with two or more, mark one `"isDefault": true`.
3.  **`inputFileArity: "none"`** means there are no input files, so `assetId` / `databaseId` resolve from
    the execution's output target (`outputAssetId` / `outputDatabaseId`), not from an input file.
4.  **`assetScope` accepts two vocabularies** — the shorthand `{"wholeAsset": true|false}` and the
    canonical four `*Allowed` keys. A malformed value can fail the import while the deploy still exits
    0, so always confirm the row landed.
5.  **Declare only the `systemConfig` fields that differ from the defaults.** The stored record
    replaces `systemConfig` wholesale rather than merging, so registration fills every field a bundle
    omits with its documented default before writing — including inside nested maps, so naming one
    `assetScope` rule does not drop its siblings. A partial block is therefore safe, and a future
    `systemConfig` field cannot change what an existing bundle means.
    **An `inputFileArity: "none"` workflow needs an explicit `outputTarget`:** with no input file there
    is no asset to lock output to, so it must be either results-only (`locationType: "none"`) or
    `{"locationType": "asset", "allowOverride": true}` so a destination can be chosen per run.
    Registration runs the same model validation as the API and FAILS the deploy on a bundle that
    cannot execute, rather than storing an unusable row.
6.  **Let the TEMPLATE decide whether a step needs an input file.** For a pipeline with several modes,
    set its `inputFileArity` to the LOWEST any template needs (usually `none`) and let each template
    raise it through `overrides` (`inputFileArity`, `assetScope`, `metadataInputs`,
    `inputFileFilters`). One pipeline per MODEL, not per mode — and the execute form asks for a file
    only when the chosen template consumes one. A workflow's arity is authored, so set it to the
    MAXIMUM any pipeline/template combination in that workflow can require.
7.  **A container must not create its own per-job output folder.** The workflow's
    `defaultOutputFileBaseExecutionPathExtension` (e.g. `/{{jobName}}/`) is what separates runs, and it
    is inserted just above each output file's own name so the container's folders are preserved. A
    container-side job folder shows up as a stray level inside every asset. Set
    `allowWorkflowTriggerChaining: true` only if this workflow should run on ANOTHER workflow's output
    (a preview or metadata built-in acting on a conversion's result); a workflow never fires on its own
    output regardless.

#### Templates: turning a pipeline into a form operators fill in

A template is the mechanism that gives a pipeline **per-run, operator-facing execution options** —
a generation prompt, a seed, an output format, a quality preset — without a code change and without a
separate pipeline per variation. It has two halves:

-   **`configBody`** — the configuration document delivered to the container, containing
    `{{tagName}}` placeholders.
-   **`tagSchema`** — the typed declaration of those placeholders. It is what the execute form renders
    as fields, and what the API validates a run's supplied values against.

At launch, VAMS substitutes the values into the body and writes the result to the run's
`config.json`; the container reads it via `manifestHelper.fetch_input_configuration()`. Nothing in the
container needs to know a template exists.

Two kinds of placeholder resolve in a config body, and the distinction decides who supplies the value:

| Placeholder                                    | Who supplies it                          | Resolved                                  |
| ---------------------------------------------- | ---------------------------------------- | ----------------------------------------- |
| **User tags** — the template's own `tagSchema` | The operator, per run (or the `default`) | At launch, from the execute request       |
| **System tags** — the fixed catalog            | VAMS, automatically                      | Per pipeline task, from execution context |

System tags are never declared in a `tagSchema` and never supplied by a caller — they are always
available. They cover execution/workflow identity, timestamps, the first input file's location and
name parts, input-file collections, every output prefix, auxiliary paths, and the metadata/config S3
locations. The catalog is `backend/backend/common/workflows/templateTags.py`, mirrored for authors in
`web/src/features/orchestration/components/SystemTagHelp.tsx`; a `tagKey` that collides with a reserved
system tag name is rejected at save.

**A `tagSchema` field** (`TemplateTagFieldModel`, `backend/backend/models/pipelines.py`):

| Field         | Required | Notes                                                                                     |
| ------------- | -------- | ----------------------------------------------------------------------------------------- |
| `tagKey`      | yes      | `[A-Za-z0-9_]+` only, so `{{tagKey}}` is substitutable. Max 128 chars.                    |
| `type`        | no       | One of `string`, `integer`, `number`, `boolean`, `string-list`, `enum`. Default `string`. |
| `required`    | no       | Default `false`. A required tag with no value fails the launch.                           |
| `default`     | no       | Used when the operator supplies nothing. Must itself be valid for `type`.                 |
| `enumValues`  | for enum | Non-empty list; `enum` without it is rejected at save.                                    |
| `label`       | no       | Field label in the execute form.                                                          |
| `description` | no       | Helper text in the execute form — where you explain units, ranges, and fallbacks.         |

**Quoting in a `json` config body is validated at save, and it is the one thing authors get wrong.**
A placeholder for a tag typed `integer`, `number`, `boolean`, or `string-list` renders a JSON _value_
and therefore takes **no quotes**; a `string` or `enum` tag renders text and must sit **inside** the
quotes of the string it fills:

```json
{ "steps": {{STEPS}}, "scale": {{SCALE}}, "debug": {{DEBUG}}, "prompt": "{{PROMPT}}" }
```

Quoting a typed tag would deliver `"150"` where the pipeline expects `150`, so the save is rejected
rather than the mistake surfacing as a malformed config at run time. The same rule applies to the two
"JSON value" system-tag groups (e.g. `"files": {{assetFileKeyArray}}`). This gate is **json-only** —
`yaml`, `xml`, `openjd`, and `raw` bodies are stored verbatim and are not shape-checked, and their tag
substitution is unaffected.

**One pipeline per MODEL, one template per MODE.** Prefer several templates over several
near-identical pipelines. A template may also narrow its pipeline's own settings through `overrides`,
limited to exactly four keys (`TEMPLATE_OVERRIDABLE_KEYS`): `inputFileArity`, `assetScope`,
`metadataInputs`, `inputFileFilters`. Any other key is rejected at save rather than ignored at execute
time. That is what lets one pipeline carry a text-to-video mode needing no input file and a
video-to-video mode needing one — see rule 6 above.

Give a template `inputInstructions` (max 4096 chars) when an operator needs guidance the field
descriptions cannot carry; it is displayed on the execute form.

A run may bypass the stored body entirely with a `customTemplateOverride`, but only when the pipeline
sets `allowCustomTemplateOverride` or the chosen template sets `allowCustomEdit`. A `json`-format
override is held to the same shape rules as a stored body — an unparseable one is refused at launch,
because every pipeline-side config reader treats an unreadable configuration as _absent_ and falls back
to its defaults, which would otherwise mean a SUCCESSFUL run with the caller's parameters silently
dropped.

Authoring limits (all reject at save; see `documentation/docusaurus-site/docs/additional/quotas.md`):
250 tag definitions per schema, 128-char `tagKey`, 1024-char `label`/`description`, 250 `enumValues` of
256 chars, 4096-char serialized `default`.

Register the bundle from the pipeline's nested stack with the `VamsSchemaRegistration` construct,
passing the deploy-time resolved resource values:

```typescript
new VamsSchemaRegistration(this, "MyPipelineSchema", {
    schemaPath: path.join(
        __dirname,
        "../../../../../backendPipelines/{category}/{pipelineName}/vamsSchema"
    ),
    resourceOverrides: { lambdaName: myPipelineFunction.functionName },
    importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
});
```

Registration is idempotent: a redeploy overwrites the definition and clears the archived flag, so it
never duplicates a pipeline or leaves one hidden.

### Step 5: Create CDK Infrastructure

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

### Step 6: Register Pipeline in Pipeline Builder

Update `infra/lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts`:

1. Add import for the new nested stack
2. Add config flag check: `if (props.config.app.pipelines.use{PipelineName}.enabled)`
3. Instantiate the nested stack with standard props
4. Add the `pipelineVamsLambdaFunctionName` to the `pipelineVamsLambdaFunctionNames` array

### Step 7: Update the VPC Builder (Batch/ECS/Fargate pipelines)

**CRITICAL:** Pipelines that use AWS Batch, ECS, or Fargate MUST be added to **all three** condition blocks in `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts`. Search for `useSplatToolbox` in the file to find all locations. Missing any one causes deployment failures:

1. **Subnet creation condition** (~line 341): the `if` block that pushes `subnetPublicConfig` and `subnetPrivateConfig` into `subnetConfigurations`. Without this, Batch compute environments fail with `"Resource subnets are required"`.
2. **VPC endpoint condition** (~line 610): the `if` block that creates Batch, ECR API, and ECR Docker interface VPC endpoints. Without this, Batch jobs cannot pull container images.
3. **ECS endpoint condition** (`needsEcsPrivate`, ~line 694): controls whether the ECS VPC endpoint includes private subnets. Without this, the ECS agent on Batch instances cannot register with the ECS service.

### Step 8: Add Config Flag

1. Add the pipeline block to the `ConfigPublic` interface in `infra/config/config.ts` under `pipelines`. Standard fields: `enabled`, `autoRegisterWithVAMS`, and where applicable `autoRegisterAutoTriggerOnFileUpload`, `useCodeBuild`.
2. Add a backward-compatibility `undefined` check with defaults in `getConfig()`.
3. Add validation in `getConfig()` if constraints exist. If the pipeline needs a VPC, add it to the `vpcRequiringFeatures` checks.
4. Update **ALL** config template files: `config.template.commercial.json`, `config.template.govcloud.json`, AND `config.template.eusovereign.json` — a missed template silently drops operator-set values.
5. Update `config.json` for the active deployment.

### Step 9: Update Documentation and Steering

1. **`documentation/docusaurus-site/docs/deployment/configuration-reference.md`**: add a section for the pipeline documenting every config option, following the existing pipeline-section format.
2. **`documentation/docusaurus-site/docs/pipelines/`**: create a new pipeline page, add it to `documentation/docusaurus-site/sidebars.ts`, and add the pipeline to the `pipelines/overview.md` table and `overview/features.md`.
3. **Root `CLAUDE.md`**: add the pipeline to the pipeline list (Rule 11).
4. If the pipeline added a VPC subnet/endpoint requirement, update the "VPC Resource Usage by Feature" tables in the configuration reference.

### Step 10: Validate

After creating all files, verify:

-   [ ] `lambda/` directory contains `__init__.py`, `customLogging/__init__.py`, and `customLogging/logger.py`
-   [ ] `vamsExecute` resolves inputs via `manifestHelper.resolve_pipeline_inputs()` — it does NOT index the event body for `inputS3AssetFilePath` or the output paths
-   [ ] `vamsExecute` forwards all resolved output paths (no hardcoded empty strings) and threads `assetId` / `databaseId`
-   [ ] Container preserves relative subdirectories in output keys using the threaded `assetId`
-   [ ] The container's support package is NOT named `utils` when the container also vendors upstream source (import-name collision)
-   [ ] `vamsSchema/pipeline.json` exists, carries no ARNs, and its `inputFileFilters.allow` matches the file types the container handles
-   [ ] A `requireTemplate: true` pipeline has a default template (auto-promoted only when the bundle ships exactly one)
-   [ ] Every per-run option the operator should control is a `tagSchema` tag with a `type`, a `label`, and a `description` — not a hardcoded value in the config body
-   [ ] Every `{{tag}}` in the config body is either declared in that template's `tagSchema` or a reserved system tag; no `tagKey` collides with a system tag name
-   [ ] In a `json` config body, typed tags (`integer`/`number`/`boolean`/`string-list`) are UNQUOTED and `string`/`enum` tags are quoted
-   [ ] A template's `overrides` uses only `inputFileArity`, `assetScope`, `metadataInputs`, `inputFileFilters`
-   [ ] The container reads its configuration via `manifestHelper.fetch_input_configuration()` and FAILS on a present-but-unparseable body rather than falling back to defaults (a silent fallback reports success while dropping every caller parameter)
-   [ ] `VamsSchemaRegistration` is wired in the nested stack with the correct `resourceOverrides`
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

**After deploying**, confirm registration actually landed — a malformed bundle can fail to import while
the deployment still reports success:

```bash
vamscli pipeline get -d GLOBAL -p {pipelineId} --json-output
vamscli pipeline template list -d GLOBAL -p {pipelineId}
```

## Workflow

1. Gather requirements from the user (or parse from $ARGUMENTS)
2. Determine pipeline category and processing type; pick a reference pipeline to copy from
3. Create all backend pipeline files (lambda + container)
4. Author the `vamsSchema/` registration bundle (pipeline.json, optional workflow.json + templates) — including a `tagSchema` per template for the per-run execution options the operator should control
5. Create CDK infrastructure (construct, nested stack, lambda builder) and wire `VamsSchemaRegistration`
6. Register in pipelineBuilder-nestedStack.ts and update the VPC builder
7. Add config flag (interface, getConfig defaults/validation, ALL templates, config.json)
8. Update documentation and steering docs
9. Summarize created files and next steps

## User Request

$ARGUMENTS
