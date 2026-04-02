# NVIDIA Cosmos Predict Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add NVIDIA Cosmos Predict pipeline to VAMS supporting Text2World (7B) and Video2World (7B) model types with GPU-based Batch inference, EFS+S3 model caching, and metadata-driven prompt override.

**Architecture:** Single container image with shared EFS/S3/Batch infrastructure, separate vamsExecute Lambdas per model type (Text2World reads asset metadata, Video2World reads file metadata for COSMOS_PREDICT_PROMPT), separate Step Functions state machines and VAMS pipeline registrations per model type. Container downloads models from HuggingFace on first run, caches to EFS with S3 backup.

**Tech Stack:** Python 3.12 (Lambda), Python 3.10 (container/conda), CDK TypeScript, AWS Batch on EC2 GPU (g5.12xlarge), EFS, S3, Step Functions, NVIDIA Cosmos-Predict1 framework, PyTorch, CUDA 12.4.

**Spec:** `docs/superpowers/specs/2026-04-02-nvidia-cosmos-predict-pipeline-design.md`

---

## File Map

### New Files

| File | Responsibility |
|---|---|
| `backendPipelines/genAi/cosmosPredict/lambda/vamsExecuteCosmosText2WorldPipeline.py` | VAMS entry point for Text2World: extracts COSMOS_PREDICT_PROMPT from asset metadata |
| `backendPipelines/genAi/cosmosPredict/lambda/vamsExecuteCosmosVideo2WorldPipeline.py` | VAMS entry point for Video2World: extracts COSMOS_PREDICT_PROMPT from file metadata |
| `backendPipelines/genAi/cosmosPredict/lambda/constructPipeline.py` | Transforms workflow event into Batch job definition |
| `backendPipelines/genAi/cosmosPredict/lambda/openPipeline.py` | Validates input, starts Step Functions execution |
| `backendPipelines/genAi/cosmosPredict/lambda/pipelineEnd.py` | Cleanup and SFN task token callback |
| `backendPipelines/genAi/cosmosPredict/container/Dockerfile` | GPU container with Cosmos-Predict1 framework |
| `backendPipelines/genAi/cosmosPredict/container/entrypoint.sh` | Container entrypoint: JSON arg to __main__.py |
| `backendPipelines/genAi/cosmosPredict/container/__main__.py` | VAMS wrapper: S3 I/O, metadata parsing, model routing |
| `backendPipelines/genAi/cosmosPredict/container/model_manager.py` | EFS/S3/HuggingFace model caching cascade |
| `backendPipelines/genAi/cosmosPredict/container/inference.py` | Routes to text2world.py or video2world.py inference scripts |
| `infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/cosmosPredictBuilder-nestedStack.ts` | CDK nested stack entry point |
| `infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/constructs/cosmosPredict-construct.ts` | Main CDK construct: Batch, EFS, S3, SFN, registration |
| `infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/lambdaBuilder/cosmosPredictFunctions.ts` | Lambda builder functions (5 builders) |
| `documentation/docusaurus-site/docs/pipelines/nvidia-cosmos.md` | User-facing documentation |

### Modified Files

| File | Change |
|---|---|
| `infra/config/config.ts` | Add CosmosPredict interface, defaults, validation |
| `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts` | Add useCosmosPredict to VPC endpoint conditions + EFS endpoint |
| `infra/lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts` | Instantiate CosmosPredictBuilderNestedStack |
| `documentation/docusaurus-site/docs/deployment/configuration-reference.md` | Add config options |
| `CLAUDE.md` | Update pipeline list and directory structure |

---

## Phase 1: CDK Configuration

### Task 1: Add CosmosPredict config interfaces and defaults

**Files:**
- Modify: `infra/config/config.ts`

- [ ] **Step 1: Add CosmosPredict interfaces to ConfigPublic**

In `infra/config/config.ts`, add to the `pipelines` section of the `ConfigPublic` interface (after the `useSplatToolbox` block):

```typescript
useCosmosPredict: {
    enabled: boolean;
    huggingFaceToken: string;
    useWarmInstances: boolean;
    warmInstanceCount: number;
    models: {
        text2world7B: {
            enabled: boolean;
            autoRegisterWithVAMS: boolean;
            instanceTypes: string[];
            maxVCpus: number;
        };
        video2world7B: {
            enabled: boolean;
            autoRegisterWithVAMS: boolean;
            autoTriggerOnFileExtensionsUpload: string;
            instanceTypes: string[];
            maxVCpus: number;
        };
    };
};
```

- [ ] **Step 2: Add default population in getConfig()**

Add after the existing `useSplatToolbox` defaults block (around line 153):

```typescript
// Cosmos Predict defaults
if (config.app.pipelines.useCosmosPredict == undefined) {
    config.app.pipelines.useCosmosPredict = {
        enabled: false,
        huggingFaceToken: "",
        useWarmInstances: false,
        warmInstanceCount: 1,
        models: {
            text2world7B: {
                enabled: false,
                autoRegisterWithVAMS: true,
                instanceTypes: ["g5.12xlarge"],
                maxVCpus: 48,
            },
            video2world7B: {
                enabled: false,
                autoRegisterWithVAMS: true,
                autoTriggerOnFileExtensionsUpload: "",
                instanceTypes: ["g5.12xlarge"],
                maxVCpus: 48,
            },
        },
    };
}
if (config.app.pipelines.useCosmosPredict.enabled == undefined) {
    config.app.pipelines.useCosmosPredict.enabled = false;
}
if (config.app.pipelines.useCosmosPredict.useWarmInstances == undefined) {
    config.app.pipelines.useCosmosPredict.useWarmInstances = false;
}
if (config.app.pipelines.useCosmosPredict.warmInstanceCount == undefined) {
    config.app.pipelines.useCosmosPredict.warmInstanceCount = 1;
}
if (config.app.pipelines.useCosmosPredict.models == undefined) {
    config.app.pipelines.useCosmosPredict.models = {
        text2world7B: {
            enabled: false,
            autoRegisterWithVAMS: true,
            instanceTypes: ["g5.12xlarge"],
            maxVCpus: 48,
        },
        video2world7B: {
            enabled: false,
            autoRegisterWithVAMS: true,
            autoTriggerOnFileExtensionsUpload: "",
            instanceTypes: ["g5.12xlarge"],
            maxVCpus: 48,
        },
    };
}
if (config.app.pipelines.useCosmosPredict.models.text2world7B == undefined) {
    config.app.pipelines.useCosmosPredict.models.text2world7B = {
        enabled: false,
        autoRegisterWithVAMS: true,
        instanceTypes: ["g5.12xlarge"],
        maxVCpus: 48,
    };
}
if (config.app.pipelines.useCosmosPredict.models.video2world7B == undefined) {
    config.app.pipelines.useCosmosPredict.models.video2world7B = {
        enabled: false,
        autoRegisterWithVAMS: true,
        autoTriggerOnFileExtensionsUpload: "",
        instanceTypes: ["g5.12xlarge"],
        maxVCpus: 48,
    };
}
```

- [ ] **Step 3: Add validation rules in getConfig()**

Add after the VPC auto-enable block (around line 388). First, add `useCosmosPredict` to the VPC auto-enable condition:

```typescript
// In the existing VPC auto-enable condition, add:
config.app.pipelines.useCosmosPredict.enabled ||
```

Then add Cosmos-specific validation:

```typescript
// Cosmos Predict validation
if (config.app.pipelines.useCosmosPredict.enabled) {
    const cosmosModels = config.app.pipelines.useCosmosPredict.models;
    const anyModelEnabled =
        cosmosModels.text2world7B.enabled || cosmosModels.video2world7B.enabled;

    if (!anyModelEnabled) {
        throw new Error(
            "Configuration Error: useCosmosPredict is enabled but no model types are enabled. " +
                "Enable at least one model in useCosmosPredict.models."
        );
    }

    if (
        !config.app.pipelines.useCosmosPredict.huggingFaceToken ||
        config.app.pipelines.useCosmosPredict.huggingFaceToken.trim() === ""
    ) {
        throw new Error(
            "Configuration Error: useCosmosPredict requires huggingFaceToken " +
                "(SSM SecureString parameter path, e.g., '/vams/cosmos/hf-token') for model downloads."
        );
    }

    if (
        cosmosModels.text2world7B.enabled &&
        (!cosmosModels.text2world7B.instanceTypes ||
            cosmosModels.text2world7B.instanceTypes.length === 0)
    ) {
        throw new Error(
            "Configuration Error: useCosmosPredict.models.text2world7B.instanceTypes must be a non-empty array."
        );
    }

    if (
        cosmosModels.video2world7B.enabled &&
        (!cosmosModels.video2world7B.instanceTypes ||
            cosmosModels.video2world7B.instanceTypes.length === 0)
    ) {
        throw new Error(
            "Configuration Error: useCosmosPredict.models.video2world7B.instanceTypes must be a non-empty array."
        );
    }
}
```

- [ ] **Step 4: Verify CDK synth passes**

Run: `cd infra && npx cdk synth 2>&1 | head -20`
Expected: Synthesizes without errors (useCosmosPredict defaults to disabled)

- [ ] **Step 5: Commit**

```bash
git add infra/config/config.ts
git commit -m "feat: add CosmosPredict config interfaces, defaults, and validation"
```

---

## Phase 2: Backend Lambda Functions

### Task 2: Create vamsExecute Lambda for Text2World

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/lambda/vamsExecuteCosmosText2WorldPipeline.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p backendPipelines/genAi/cosmosPredict/lambda
mkdir -p backendPipelines/genAi/cosmosPredict/container
```

- [ ] **Step 2: Write vamsExecuteCosmosText2WorldPipeline.py**

```python
"""
VAMS entry point for Cosmos Predict Text2World pipeline.

Extracts COSMOS_PREDICT_PROMPT from asset-level metadata (not file metadata).
This pipeline runs on the global asset without a specific input file.
"""
import json
import os
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]
lambda_client = boto3.client("lambda")


def extract_cosmos_prompt_from_asset_metadata(input_metadata_str):
    """
    Scan assetMetadata for COSMOS_PREDICT_PROMPT key.
    Returns the prompt string if found, empty string otherwise.
    """
    if not input_metadata_str:
        return ""
    try:
        metadata = json.loads(input_metadata_str)
        asset_meta = metadata.get("assetMetadata", {}).get("metadata", [])
        for item in asset_meta:
            if item.get("metadataKey") == "COSMOS_PREDICT_PROMPT":
                return item.get("metadataValue", "")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse inputMetadata for COSMOS_PREDICT_PROMPT: {e}")
    return ""


def execute_pipeline(data):
    """Build payload and invoke openPipeline Lambda."""
    input_metadata = data.get("inputMetadata", "")
    input_parameters = data.get("inputParameters", "")
    external_task_token = data.get("TaskToken", "")

    if not external_task_token:
        raise ValueError("TaskToken is required for async pipeline execution")

    # Extract prompt from asset metadata (takes precedence over inputParameters)
    cosmos_prompt = extract_cosmos_prompt_from_asset_metadata(input_metadata)

    message_payload = {
        "modelType": "text2world",
        "cosmosPrompt": cosmos_prompt,
        "inputS3AssetFilePath": "",  # Text2World has no input file
        "outputS3AssetFilesPath": data.get("outputS3AssetFilesPath", ""),
        "outputS3AssetPreviewPath": data.get("outputS3AssetPreviewPath", ""),
        "outputS3AssetMetadataPath": data.get("outputS3AssetMetadataPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": data.get(
            "inputOutputS3AssetAuxiliaryFilesPath", ""
        ),
        "assetId": data.get("assetId", ""),
        "databaseId": data.get("databaseId", ""),
        "inputMetadata": input_metadata,
        "inputParameters": input_parameters,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": data.get("executingUserName", ""),
        "executingRequestContext": data.get("executingRequestContext", ""),
    }

    logger.info(
        f"Invoking openPipeline for Text2World, asset={data.get('assetId', '')}, "
        f"hasPrompt={'yes' if cosmos_prompt else 'no'}"
    )

    response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(message_payload).encode("utf-8"),
    )

    response_payload = json.loads(response["Payload"].read().decode("utf-8"))
    logger.info(f"openPipeline response: {json.dumps(response_payload)}")
    return response_payload


def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")
    try:
        data = json.loads(event.get("body", "{}"))
        result = execute_pipeline(data)
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Cosmos Text2World pipeline started", "result": result}),
        }
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"message": f"Pipeline execution failed: {str(e)}"}),
        }
```

- [ ] **Step 3: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/lambda/vamsExecuteCosmosText2WorldPipeline.py
git commit -m "feat: add Cosmos Text2World vamsExecute Lambda"
```

---

### Task 3: Create vamsExecute Lambda for Video2World

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/lambda/vamsExecuteCosmosVideo2WorldPipeline.py`

- [ ] **Step 1: Write vamsExecuteCosmosVideo2WorldPipeline.py**

```python
"""
VAMS entry point for Cosmos Predict Video2World pipeline.

Extracts COSMOS_PREDICT_PROMPT from file-level metadata (not asset metadata).
This pipeline runs on a specific video/image file within the asset.
"""
import json
import os
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]
lambda_client = boto3.client("lambda")


def extract_cosmos_prompt_from_file_metadata(input_metadata_str):
    """
    Scan fileMetadata for COSMOS_PREDICT_PROMPT key.
    Returns the prompt string if found, empty string otherwise.
    """
    if not input_metadata_str:
        return ""
    try:
        metadata = json.loads(input_metadata_str)
        file_meta = metadata.get("fileMetadata", {}).get("metadata", [])
        for item in file_meta:
            if item.get("metadataKey") == "COSMOS_PREDICT_PROMPT":
                return item.get("metadataValue", "")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse inputMetadata for COSMOS_PREDICT_PROMPT: {e}")
    return ""


def execute_pipeline(data):
    """Build payload and invoke openPipeline Lambda."""
    input_metadata = data.get("inputMetadata", "")
    input_parameters = data.get("inputParameters", "")
    external_task_token = data.get("TaskToken", "")

    if not external_task_token:
        raise ValueError("TaskToken is required for async pipeline execution")

    # Extract prompt from file metadata (takes precedence over inputParameters)
    cosmos_prompt = extract_cosmos_prompt_from_file_metadata(input_metadata)

    message_payload = {
        "modelType": "video2world",
        "cosmosPrompt": cosmos_prompt,
        "inputS3AssetFilePath": data.get("inputS3AssetFilePath", ""),
        "outputS3AssetFilesPath": data.get("outputS3AssetFilesPath", ""),
        "outputS3AssetPreviewPath": data.get("outputS3AssetPreviewPath", ""),
        "outputS3AssetMetadataPath": data.get("outputS3AssetMetadataPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": data.get(
            "inputOutputS3AssetAuxiliaryFilesPath", ""
        ),
        "assetId": data.get("assetId", ""),
        "databaseId": data.get("databaseId", ""),
        "inputMetadata": input_metadata,
        "inputParameters": input_parameters,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": data.get("executingUserName", ""),
        "executingRequestContext": data.get("executingRequestContext", ""),
    }

    logger.info(
        f"Invoking openPipeline for Video2World, asset={data.get('assetId', '')}, "
        f"file={data.get('inputS3AssetFilePath', '')}, hasPrompt={'yes' if cosmos_prompt else 'no'}"
    )

    response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(message_payload).encode("utf-8"),
    )

    response_payload = json.loads(response["Payload"].read().decode("utf-8"))
    logger.info(f"openPipeline response: {json.dumps(response_payload)}")
    return response_payload


def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")
    try:
        data = json.loads(event.get("body", "{}"))
        result = execute_pipeline(data)
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Cosmos Video2World pipeline started", "result": result}),
        }
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"message": f"Pipeline execution failed: {str(e)}"}),
        }
```

- [ ] **Step 2: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/lambda/vamsExecuteCosmosVideo2WorldPipeline.py
git commit -m "feat: add Cosmos Video2World vamsExecute Lambda"
```

---

### Task 4: Create constructPipeline Lambda (shared)

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/lambda/constructPipeline.py`

- [ ] **Step 1: Write constructPipeline.py**

```python
"""
Transform Cosmos Predict workflow event into AWS Batch job definition.

Shared by both Text2World and Video2World model types.
The modelType field in the event determines container behavior.
"""
import json
import os
import logging
from uuid import uuid4

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def extract_prompt_from_input_parameters(input_parameters_str):
    """Extract prompt from inputParameters JSON string if present."""
    if not input_parameters_str:
        return ""
    try:
        params = json.loads(input_parameters_str)
        return params.get("PROMPT", params.get("prompt", ""))
    except (json.JSONDecodeError, TypeError):
        return ""


def construct_cosmos_definition(event):
    """Build pipeline definition for Batch container."""
    model_type = event.get("modelType", "video2world")
    asset_id = event.get("assetId", "")

    definition = {
        "modelType": model_type,
        "modelSize": "7B",
        "cosmosPrompt": event.get("cosmosPrompt", ""),
        "inputParametersPrompt": extract_prompt_from_input_parameters(
            event.get("inputParameters", "")
        ),
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath", ""),
        "outputS3AssetFilesPath": event.get("outputS3AssetFilesPath", ""),
        "outputS3AssetPreviewPath": event.get("outputS3AssetPreviewPath", ""),
        "outputS3AssetMetadataPath": event.get("outputS3AssetMetadataPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": event.get(
            "inputOutputS3AssetAuxiliaryFilesPath", ""
        ),
        "assetId": asset_id,
        "databaseId": event.get("databaseId", ""),
    }

    job_name = f"cosmos-{model_type}-{uuid4().hex[:8]}"

    return {
        "jobName": job_name,
        "definition": ["python", "__main__.py", json.dumps(definition)],
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("sfnExternalTaskToken", ""),
        "status": "STARTING",
    }


def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")
    try:
        result = construct_cosmos_definition(event)
        logger.info(f"Constructed job: {result['jobName']} for model type: {event.get('modelType')}")
        return result
    except Exception as e:
        logger.error(f"Failed to construct pipeline definition: {e}", exc_info=True)
        return {
            "jobName": "",
            "definition": [],
            "error": str(e),
            "status": "FAILED",
            "externalSfnTaskToken": event.get("sfnExternalTaskToken", ""),
        }
```

- [ ] **Step 2: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/lambda/constructPipeline.py
git commit -m "feat: add Cosmos Predict constructPipeline Lambda"
```

---

### Task 5: Create openPipeline Lambda (shared)

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/lambda/openPipeline.py`

- [ ] **Step 1: Write openPipeline.py**

```python
"""
Validates input and starts Step Functions state machine execution for Cosmos Predict.

Shared by Text2World and Video2World. For Text2World (no input file),
file validation is skipped. For Video2World, validates file extension.
"""
import json
import os
import boto3
import logging
from uuid import uuid4

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get(
    "ALLOWED_INPUT_FILEEXTENSIONS", ".mp4,.mov,.jpg,.jpeg,.png,.webp"
)

sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)


def abort_external_workflow(task_token, error_message):
    """Send task failure callback if external task token is provided."""
    if task_token:
        try:
            sfn_client.send_task_failure(
                taskToken=task_token,
                error="PipelineValidationError",
                cause=error_message,
            )
        except Exception as e:
            logger.error(f"Failed to send task failure: {e}")


def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")

    model_type = event.get("modelType", "video2world")
    input_s3_path = event.get("inputS3AssetFilePath", "")
    task_token = event.get("sfnExternalTaskToken", "")

    # For Video2World, validate input file extension
    if model_type == "video2world" and input_s3_path:
        file_ext = os.path.splitext(input_s3_path)[1].lower()
        allowed_extensions = [
            ext.strip().lower()
            for ext in ALLOWED_INPUT_FILEEXTENSIONS.replace(" ", ",").split(",")
            if ext.strip()
        ]

        if file_ext not in allowed_extensions:
            error_msg = (
                f"Invalid file extension '{file_ext}'. "
                f"Allowed extensions: {', '.join(allowed_extensions)}"
            )
            logger.error(error_msg)
            abort_external_workflow(task_token, error_msg)
            return {"statusCode": 400, "body": json.dumps({"message": error_msg})}

        # Validate it's a file, not a folder
        if input_s3_path.endswith("/"):
            error_msg = "Input path is a folder, not a file."
            logger.error(error_msg)
            abort_external_workflow(task_token, error_msg)
            return {"statusCode": 400, "body": json.dumps({"message": error_msg})}

    # For Text2World, no input file validation needed
    if model_type == "text2world":
        logger.info("Text2World pipeline - no input file validation required")

    # Generate unique execution name
    execution_name = f"cosmos-{model_type}-{uuid4().hex[:12]}"

    # Build Step Functions input
    sfn_input = {
        "modelType": model_type,
        "cosmosPrompt": event.get("cosmosPrompt", ""),
        "inputS3AssetFilePath": input_s3_path,
        "outputS3AssetFilesPath": event.get("outputS3AssetFilesPath", ""),
        "outputS3AssetPreviewPath": event.get("outputS3AssetPreviewPath", ""),
        "outputS3AssetMetadataPath": event.get("outputS3AssetMetadataPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": event.get(
            "inputOutputS3AssetAuxiliaryFilesPath", ""
        ),
        "assetId": event.get("assetId", ""),
        "databaseId": event.get("databaseId", ""),
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "sfnExternalTaskToken": task_token,
        "executingUserName": event.get("executingUserName", ""),
        "executingRequestContext": event.get("executingRequestContext", ""),
    }

    try:
        response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps(sfn_input),
        )
        logger.info(f"Started execution: {response['executionArn']}")
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Pipeline execution started",
                    "executionArn": response["executionArn"],
                    "executionName": execution_name,
                }
            ),
        }
    except Exception as e:
        error_msg = f"Failed to start state machine execution: {str(e)}"
        logger.error(error_msg, exc_info=True)
        abort_external_workflow(task_token, error_msg)
        return {"statusCode": 500, "body": json.dumps({"message": error_msg})}
```

- [ ] **Step 2: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/lambda/openPipeline.py
git commit -m "feat: add Cosmos Predict openPipeline Lambda"
```

---

### Task 6: Create pipelineEnd Lambda (shared)

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/lambda/pipelineEnd.py`

- [ ] **Step 1: Write pipelineEnd.py**

```python
"""
Pipeline completion handler for Cosmos Predict.

Sends Step Functions task success/failure callback based on pipeline result.
"""
import json
import os
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)


def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")
    logger.info(f"Context: {context}")

    has_error = "error" in event or event.get("status") == "FAILED"
    task_token = event.get("externalSfnTaskToken", "")

    if task_token:
        try:
            if has_error:
                error_msg = event.get("error", "Pipeline failed with unknown error")
                logger.error(f"Pipeline failed, sending task failure: {error_msg}")
                sfn_client.send_task_failure(
                    taskToken=task_token,
                    error="CosmosPipelineError",
                    cause=str(error_msg),
                )
            else:
                logger.info("Pipeline succeeded, sending task success")
                sfn_client.send_task_success(
                    taskToken=task_token,
                    output=json.dumps({"status": "Pipeline Success"}),
                )
        except Exception as e:
            logger.error(f"Failed to send task callback: {e}", exc_info=True)

    return event
```

- [ ] **Step 2: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/lambda/pipelineEnd.py
git commit -m "feat: add Cosmos Predict pipelineEnd Lambda"
```

---

## Phase 3: Container Code

### Task 7: Create model_manager.py

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/container/model_manager.py`

- [ ] **Step 1: Write model_manager.py**

```python
"""
EFS/S3/HuggingFace model caching cascade for Cosmos Predict.

Downloads all required models on first run:
- Cosmos-Predict1-7B-Text2World or Video2World (diffusion transformer, ~45GB)
- Cosmos-Tokenize1-CV8x8x8-720p (video tokenizer, ~5GB)
- google-t5/t5-11b (text encoder, ~85GB)

Caches models on EFS for fast subsequent runs, with S3 as backup.
"""
import os
import json
import hashlib
import logging
import subprocess
import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# HuggingFace model IDs for each required component
REQUIRED_MODELS = {
    "text2world": {
        "diffusion": "nvidia/Cosmos-Predict1-7B-Text2World",
        "tokenizer": "nvidia/Cosmos-Tokenize1-CV8x8x8-720p",
        "text_encoder": "google-t5/t5-11b",
    },
    "video2world": {
        "diffusion": "nvidia/Cosmos-Predict1-7B-Video2World",
        "tokenizer": "nvidia/Cosmos-Tokenize1-CV8x8x8-720p",
        "text_encoder": "google-t5/t5-11b",
    },
}

s3_client = boto3.client("s3")


def get_required_models(model_type, model_size="7B"):
    """Return list of HuggingFace model IDs required for the given model type."""
    models = REQUIRED_MODELS.get(model_type, {})
    return list(models.values())


def _safe_model_dirname(hf_model_id):
    """Convert HuggingFace model ID to safe directory name (e.g., 'nvidia/Cosmos...' -> 'Cosmos...')."""
    return hf_model_id.split("/")[-1]


def _compute_manifest(model_dir):
    """
    Compute a lightweight manifest of key checkpoint files.
    Uses file sizes + names as a fast proxy for content hashing
    (full MD5 of 85GB files would be too slow).
    """
    manifest = {}
    if not os.path.exists(model_dir):
        return manifest

    for fname in sorted(os.listdir(model_dir)):
        fpath = os.path.join(model_dir, fname)
        if os.path.isfile(fpath) and (
            fname.endswith(".safetensors")
            or fname.endswith(".bin")
            or fname.endswith(".json")
            or fname.endswith(".model")
        ):
            manifest[fname] = os.path.getsize(fpath)

    return manifest


def _read_manifest_file(manifest_path):
    """Read manifest JSON from file."""
    try:
        with open(manifest_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_manifest_file(manifest_path, manifest):
    """Write manifest JSON to file."""
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def _read_s3_manifest(s3_bucket, model_dirname):
    """Read manifest from S3."""
    key = f"manifests/{model_dirname}.json"
    try:
        response = s3_client.get_object(Bucket=s3_bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        return {}


def _write_s3_manifest(s3_bucket, model_dirname, manifest):
    """Write manifest to S3."""
    key = f"manifests/{model_dirname}.json"
    try:
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
        )
    except Exception as e:
        logger.warning(f"Failed to write S3 manifest for {model_dirname}: {e}")


def _download_from_huggingface(hf_model_id, target_dir, hf_token):
    """Download model from HuggingFace using huggingface_hub."""
    logger.info(f"Downloading {hf_model_id} from HuggingFace to {target_dir}...")
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=hf_model_id,
        local_dir=target_dir,
        token=hf_token,
        local_dir_use_symlinks=False,
    )
    logger.info(f"Download complete: {hf_model_id}")


def _sync_s3_to_efs(s3_bucket, model_dirname, efs_model_dir):
    """Sync model from S3 to EFS using aws s3 sync."""
    s3_path = f"s3://{s3_bucket}/models/{model_dirname}/"
    logger.info(f"Syncing {s3_path} -> {efs_model_dir}")
    subprocess.run(
        ["aws", "s3", "sync", s3_path, efs_model_dir],
        check=True,
    )


def _sync_efs_to_s3(efs_model_dir, s3_bucket, model_dirname):
    """Sync model from EFS to S3 as backup."""
    s3_path = f"s3://{s3_bucket}/models/{model_dirname}/"
    logger.info(f"Backing up {efs_model_dir} -> {s3_path}")
    subprocess.run(
        ["aws", "s3", "sync", efs_model_dir, s3_path],
        check=True,
    )


def _s3_model_exists(s3_bucket, model_dirname):
    """Check if model exists in S3 (has at least one object under models/ prefix)."""
    try:
        response = s3_client.list_objects_v2(
            Bucket=s3_bucket,
            Prefix=f"models/{model_dirname}/",
            MaxKeys=1,
        )
        return response.get("KeyCount", 0) > 0
    except Exception:
        return False


def ensure_model_available(hf_model_id, efs_base_path, s3_bucket, hf_token):
    """
    Ensure a model is available on EFS, using S3 cache or HuggingFace download.

    Flow:
    1. Check EFS for model + compare manifest with S3
    2. If EFS has model and manifest matches -> use EFS (fast path)
    3. If EFS model missing/stale and S3 has model -> sync S3 -> EFS
    4. If neither has model -> download from HuggingFace -> save to EFS + backup to S3
    """
    model_dirname = _safe_model_dirname(hf_model_id)
    efs_model_dir = os.path.join(efs_base_path, model_dirname)
    efs_manifest_path = os.path.join(efs_base_path, "manifests", f"{model_dirname}.json")

    logger.info(f"Ensuring model available: {hf_model_id} at {efs_model_dir}")

    # Check if model exists on EFS
    efs_manifest = _read_manifest_file(efs_manifest_path)
    if efs_manifest:
        # EFS has a manifest - compare with S3
        if s3_bucket:
            s3_manifest = _read_s3_manifest(s3_bucket, model_dirname)
            if s3_manifest and s3_manifest == efs_manifest:
                logger.info(f"EFS model up-to-date: {model_dirname} (manifest match)")
                return efs_model_dir
            elif s3_manifest and s3_manifest != efs_manifest:
                logger.info(f"EFS model stale for {model_dirname}, syncing from S3...")
                _sync_s3_to_efs(s3_bucket, model_dirname, efs_model_dir)
                new_manifest = _compute_manifest(efs_model_dir)
                _write_manifest_file(efs_manifest_path, new_manifest)
                return efs_model_dir
        else:
            # No S3 bucket configured, trust EFS
            logger.info(f"Using EFS model (no S3 configured): {model_dirname}")
            return efs_model_dir

    # EFS doesn't have the model - check S3
    if s3_bucket and _s3_model_exists(s3_bucket, model_dirname):
        logger.info(f"Model not on EFS, syncing from S3: {model_dirname}")
        os.makedirs(efs_model_dir, exist_ok=True)
        _sync_s3_to_efs(s3_bucket, model_dirname, efs_model_dir)
        manifest = _compute_manifest(efs_model_dir)
        _write_manifest_file(efs_manifest_path, manifest)
        if s3_bucket:
            _write_s3_manifest(s3_bucket, model_dirname, manifest)
        return efs_model_dir

    # Neither EFS nor S3 has the model - download from HuggingFace
    logger.info(f"First run: downloading {hf_model_id} from HuggingFace...")
    os.makedirs(efs_model_dir, exist_ok=True)
    _download_from_huggingface(hf_model_id, efs_model_dir, hf_token)

    # Compute and save manifest
    manifest = _compute_manifest(efs_model_dir)
    _write_manifest_file(efs_manifest_path, manifest)

    # Backup to S3
    if s3_bucket:
        logger.info(f"Backing up {model_dirname} to S3...")
        _sync_efs_to_s3(efs_model_dir, s3_bucket, model_dirname)
        _write_s3_manifest(s3_bucket, model_dirname, manifest)

    return efs_model_dir
```

- [ ] **Step 2: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/container/model_manager.py
git commit -m "feat: add Cosmos model manager with EFS/S3/HuggingFace cascade"
```

---

### Task 8: Create inference.py

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/container/inference.py`

- [ ] **Step 1: Write inference.py**

```python
"""
Routes inference to the correct Cosmos-Predict1 script based on model type.

Offloading flags (--offload_*) are always enabled regardless of GPU instance size.
They are required on g5.12xlarge (A10G 24GB per GPU) and safe on larger GPUs
with only minor performance overhead.
"""
import os
import subprocess
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

COSMOS_PREDICT1_DIR = "/opt/cosmos-predict1"


def run_inference(
    model_type,
    model_size,
    prompt,
    input_file_path,
    output_dir,
    efs_model_dir,
    num_gpus=4,
):
    """
    Run Cosmos Predict inference using torchrun for multi-GPU execution.

    Args:
        model_type: "text2world" or "video2world"
        model_size: "7B" or "14B"
        prompt: Text prompt (optional for video2world)
        input_file_path: Local path to input video/image (video2world only)
        output_dir: Local directory for output video
        efs_model_dir: EFS base path where models are stored
        num_gpus: Number of GPUs to use (default 4 for g5.12xlarge)
    """
    os.makedirs(output_dir, exist_ok=True)

    if model_type == "text2world":
        diffusion_dir = f"Cosmos-Predict1-{model_size}-Text2World"
    else:
        diffusion_dir = f"Cosmos-Predict1-{model_size}-Video2World"

    # Base arguments common to both model types
    base_args = [
        "--checkpoint_dir", efs_model_dir,
        "--diffusion_transformer_dir", diffusion_dir,
        "--video_save_folder", output_dir,
        "--num_gpus", str(num_gpus),
        # Offloading flags: always enabled for compatibility across GPU instance sizes
        "--offload_diffusion_transformer",
        "--offload_tokenizer",
        "--offload_text_encoder_model",
        "--offload_prompt_upsampler",
        "--offload_guardrail_models",
    ]

    if model_type == "text2world":
        script = os.path.join(
            COSMOS_PREDICT1_DIR,
            "cosmos_predict1/diffusion/inference/text2world.py",
        )
        if prompt:
            base_args.extend(["--prompt", prompt])
        else:
            raise ValueError("Text2World requires a text prompt")

    elif model_type == "video2world":
        script = os.path.join(
            COSMOS_PREDICT1_DIR,
            "cosmos_predict1/diffusion/inference/video2world.py",
        )
        if not input_file_path or not os.path.exists(input_file_path):
            raise ValueError(f"Video2World requires a valid input file: {input_file_path}")
        base_args.extend(["--input_image_or_video_path", input_file_path])
        base_args.extend(["--num_input_frames", "1"])
        if prompt:
            base_args.extend(["--prompt", prompt])
            # When explicit prompt provided, disable auto-generation from visual input
            base_args.append("--disable_prompt_upsampler")
    else:
        raise ValueError(f"Unknown model type: {model_type}")

    cmd = [
        "torchrun",
        "--nproc_per_node", str(num_gpus),
        script,
    ] + base_args

    env = {
        **os.environ,
        "PYTHONPATH": COSMOS_PREDICT1_DIR,
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
    }

    logger.info(f"Running inference: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=env, check=True, capture_output=False)
    logger.info(f"Inference completed with return code: {result.returncode}")


def generate_preview_gif(video_path, output_path):
    """Generate a GIF preview from the first ~2 seconds of the output video."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-t", "2",
        "-vf", "fps=10,scale=320:-1",
        "-loop", "0",
        output_path,
    ]
    logger.info(f"Generating preview GIF: {output_path}")
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path
```

- [ ] **Step 2: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/container/inference.py
git commit -m "feat: add Cosmos inference routing with multi-GPU torchrun"
```

---

### Task 9: Create __main__.py (VAMS container wrapper)

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/container/__main__.py`

- [ ] **Step 1: Write __main__.py**

```python
"""
VAMS pipeline wrapper for Cosmos Predict inference.

Handles:
1. Pipeline definition parsing (from Batch command args or env vars)
2. Model availability check (EFS/S3/HuggingFace cascade)
3. Input file download from S3 (Video2World)
4. Inference execution via torchrun
5. Output upload to VAMS S3 paths (with preview generation)
6. Step Functions task token callback (success/failure)
"""
import json
import os
import sys
import glob
import logging
import boto3
from datetime import datetime

from model_manager import ensure_model_available, get_required_models
from inference import run_inference, generate_preview_gif

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("cosmos-predict-main")

s3_client = boto3.client("s3")
sfn_client = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def load_pipeline_definition():
    """Load pipeline definition from command line arg, file path, or env var."""
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        # Check if it's a file path or inline JSON
        if os.path.isfile(arg):
            with open(arg, "r") as f:
                return json.load(f)
        else:
            return json.loads(arg)

    env_def = os.environ.get("PIPELINE_DEFINITION", "")
    if env_def:
        return json.loads(env_def)

    raise ValueError("No pipeline definition provided via args or PIPELINE_DEFINITION env var")


def download_from_s3(s3_uri, local_dir):
    """Download a file from S3 URI to local directory. Returns local file path."""
    os.makedirs(local_dir, exist_ok=True)
    # Parse s3://bucket/key
    parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    filename = os.path.basename(key)
    local_path = os.path.join(local_dir, filename)

    logger.info(f"Downloading s3://{bucket}/{key} -> {local_path}")
    s3_client.download_file(bucket, key, local_path)
    return local_path


def upload_to_s3(local_path, s3_uri):
    """Upload a local file to S3 URI."""
    parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""

    logger.info(f"Uploading {local_path} -> s3://{bucket}/{key}")
    s3_client.upload_file(local_path, bucket, key)


def find_output_video(output_dir):
    """Find the generated output video in the output directory."""
    patterns = ["*.mp4", "*.MP4"]
    for pattern in patterns:
        matches = glob.glob(os.path.join(output_dir, pattern))
        if matches:
            return matches[0]
    # Check subdirectories
    for pattern in patterns:
        matches = glob.glob(os.path.join(output_dir, "**", pattern), recursive=True)
        if matches:
            return matches[0]
    raise FileNotFoundError(f"No output video found in {output_dir}")


def compute_relative_subdir(input_s3_path, asset_id):
    """
    Compute the relative subdirectory between assetId and filename.
    Example: input key 'xd130a6d6/videos/test/drone.mp4' with assetId 'xd130a6d6'
    -> relative_subdir = 'videos/test'
    """
    # Extract the key part (after bucket)
    key = input_s3_path.replace("s3://", "").split("/", 1)[1] if "s3://" in input_s3_path else input_s3_path
    parts = key.split("/")

    try:
        asset_idx = parts.index(asset_id)
        # Relative path is between assetId and filename
        relative_parts = parts[asset_idx + 1 : -1]
        return "/".join(relative_parts)
    except ValueError:
        return ""


def send_task_success(task_token, result):
    """Send SFN task success callback."""
    try:
        sfn_client.send_task_success(
            taskToken=task_token,
            output=json.dumps(result),
        )
        logger.info("Sent task success callback")
    except Exception as e:
        logger.error(f"Failed to send task success: {e}")


def send_task_failure(task_token, error_msg):
    """Send SFN task failure callback."""
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error="CosmosPipelineError",
            cause=str(error_msg)[:256],
        )
        logger.info("Sent task failure callback")
    except Exception as e:
        logger.error(f"Failed to send task failure: {e}")


def main():
    pipeline_def = load_pipeline_definition()
    logger.info(f"Pipeline definition: {json.dumps(pipeline_def, default=str)}")

    model_type = pipeline_def["modelType"]
    model_size = pipeline_def.get("modelSize", "7B")
    cosmos_prompt = pipeline_def.get("cosmosPrompt", "")
    input_parameters_prompt = pipeline_def.get("inputParametersPrompt", "")
    input_s3_path = pipeline_def.get("inputS3AssetFilePath", "")
    output_s3_files_path = pipeline_def.get("outputS3AssetFilesPath", "")
    asset_id = pipeline_def.get("assetId", "")

    hf_token = os.environ.get("HF_TOKEN", "")
    s3_model_bucket = os.environ.get("S3_MODEL_BUCKET", "")
    sfn_task_token = os.environ.get("EXTERNAL_SFN_TASK_TOKEN", "")

    # Determine prompt: cosmosPrompt (from metadata) takes precedence
    prompt = cosmos_prompt if cosmos_prompt else input_parameters_prompt

    try:
        # 1. Ensure all required models are available on EFS
        efs_model_dir = "/mnt/efs/cosmos-models"
        required_models = get_required_models(model_type, model_size)
        logger.info(f"Required models for {model_type}/{model_size}: {required_models}")

        for hf_model_id in required_models:
            ensure_model_available(hf_model_id, efs_model_dir, s3_model_bucket, hf_token)

        # 2. Download input file for Video2World
        local_input = None
        if model_type == "video2world" and input_s3_path:
            local_input = download_from_s3(input_s3_path, "/tmp/input")

        # 3. Run inference
        output_dir = "/tmp/output"
        run_inference(model_type, model_size, prompt, local_input, output_dir, efs_model_dir)

        # 4. Find output video
        output_video = find_output_video(output_dir)
        logger.info(f"Output video: {output_video}")

        # 5. Determine output S3 key
        if model_type == "video2world" and input_s3_path:
            relative_subdir = compute_relative_subdir(input_s3_path, asset_id)
            input_filename = os.path.basename(input_s3_path)
            if relative_subdir:
                output_key = f"{output_s3_files_path}{relative_subdir}/{input_filename}.cosmos-v2w.mp4"
            else:
                output_key = f"{output_s3_files_path}{input_filename}.cosmos-v2w.mp4"
        else:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            output_key = f"{output_s3_files_path}cosmos-text2world-{timestamp}.mp4"

        # 6. Upload output video
        upload_to_s3(output_video, output_key)

        # 7. Generate and upload preview GIF
        preview_local = os.path.join(output_dir, "preview.gif")
        generate_preview_gif(output_video, preview_local)
        preview_key = f"{output_key}.previewFile.gif"
        upload_to_s3(preview_local, preview_key)

        # 8. Success callback
        if sfn_task_token:
            send_task_success(sfn_task_token, {
                "status": "complete",
                "outputKey": output_key,
                "previewKey": preview_key,
            })

        logger.info(f"Pipeline complete: {output_key}")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        if sfn_task_token:
            send_task_failure(sfn_task_token, str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/container/__main__.py
git commit -m "feat: add Cosmos VAMS container wrapper with S3 I/O and callbacks"
```

---

### Task 10: Create Dockerfile and entrypoint.sh

**Files:**
- Create: `backendPipelines/genAi/cosmosPredict/container/Dockerfile`
- Create: `backendPipelines/genAi/cosmosPredict/container/entrypoint.sh`

- [ ] **Step 1: Write entrypoint.sh**

```bash
#!/bin/bash
set -e

# Activate conda environment
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate cosmos-predict1

# Run VAMS wrapper with pipeline definition as argument
cd /opt/ml/code
exec python __main__.py "$@"
```

- [ ] **Step 2: Write Dockerfile**

```dockerfile
FROM nvcr.io/nvidia/pytorch:24.10-py3

# Build args
ARG DEBIAN_FRONTEND=noninteractive
ENV TORCH_CUDA_ARCH_LIST="8.0 8.6 8.9"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    awscli \
    && rm -rf /var/lib/apt/lists/*

# Install Miniconda (Cosmos-Predict1 uses conda for environment management)
RUN curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh && \
    bash /tmp/miniconda.sh -b -p /opt/miniconda3 && \
    rm /tmp/miniconda.sh
ENV PATH="/opt/miniconda3/bin:$PATH"

# Clone Cosmos-Predict1 framework (code only, no model weights)
RUN git clone https://github.com/nvidia-cosmos/cosmos-predict1.git /opt/cosmos-predict1
WORKDIR /opt/cosmos-predict1

# Create conda environment from Cosmos-Predict1 spec
RUN conda env create -f cosmos-predict1.yaml && \
    conda clean -afy

# Install additional dependencies in the conda env
SHELL ["conda", "run", "-n", "cosmos-predict1", "/bin/bash", "-c"]

# Transformer Engine for optimized transformer computation
RUN pip install --no-cache-dir transformer-engine[pytorch]==1.12.0

# NVIDIA APEX for mixed precision training utilities
RUN git clone https://github.com/NVIDIA/apex.git /tmp/apex && \
    cd /tmp/apex && \
    pip install -v --disable-pip-version-check --no-cache-dir \
    --no-build-isolation \
    --config-settings "--build-option=--cpp_ext" \
    --config-settings "--build-option=--cuda_ext" . && \
    rm -rf /tmp/apex

# boto3 for S3 operations, huggingface_hub for model download
RUN pip install --no-cache-dir boto3 huggingface_hub

# Reset shell
SHELL ["/bin/bash", "-c"]

# VAMS integration files
RUN mkdir -p /opt/ml/code
COPY __main__.py model_manager.py inference.py entrypoint.sh /opt/ml/code/
RUN chmod +x /opt/ml/code/entrypoint.sh

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
```

- [ ] **Step 3: Commit**

```bash
git add backendPipelines/genAi/cosmosPredict/container/Dockerfile \
        backendPipelines/genAi/cosmosPredict/container/entrypoint.sh
git commit -m "feat: add Cosmos Predict container Dockerfile and entrypoint"
```

---

## Phase 4: CDK Infrastructure

### Task 11: Create Lambda builders

**Files:**
- Create: `infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/lambdaBuilder/cosmosPredictFunctions.ts`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/lambdaBuilder
mkdir -p infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/constructs
```

- [ ] **Step 2: Write cosmosPredictFunctions.ts**

Follow the exact pattern from `splatToolboxFunctions.ts` (5 builder functions). The file should export 5 functions:

- `buildVamsExecuteCosmosText2WorldPipelineFunction` — handler: `vamsExecuteCosmosText2WorldPipeline.lambda_handler`, env: `OPEN_PIPELINE_FUNCTION_NAME`
- `buildVamsExecuteCosmosVideo2WorldPipelineFunction` — handler: `vamsExecuteCosmosVideo2WorldPipeline.lambda_handler`, env: `OPEN_PIPELINE_FUNCTION_NAME`
- `buildConstructPipelineFunction` — handler: `constructPipeline.lambda_handler`, no extra env vars
- `buildOpenPipelineFunction` — handler: `openPipeline.lambda_handler`, env: `STATE_MACHINE_ARN`, `ALLOWED_INPUT_FILEEXTENSIONS`
- `buildPipelineEndFunction` — handler: `pipelineEnd.lambda_handler`, IAM for `states:SendTaskSuccess`/`states:SendTaskFailure`

All functions:
- Runtime: `Config.LAMBDA_PYTHON_RUNTIME`
- Code path: `backendPipelines/genAi/cosmosPredict/lambda`
- Layers: `lambdaCommonBaseLayer`
- Memory: `Config.LAMBDA_MEMORY_SIZE`
- Timeout: 5 minutes
- VPC: conditional on `config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas`
- Security: all 4 required calls (`kmsKeyLambdaPermissionAddToResourcePolicy`, `lambdaFunctionSetup`, `globalLambdaEnvironmentsAndPermissions`, `suppressCdkNagErrorsByGrantReadWrite`)
- Asset bucket permissions: `grantReadPermissionsToAllAssetBuckets`

Refer to `infra/lib/nestedStacks/pipelines/3dRecon/splatToolbox/lambdaBuilder/splatToolboxFunctions.ts` for the exact pattern and import paths.

- [ ] **Step 3: Commit**

```bash
git add infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/lambdaBuilder/cosmosPredictFunctions.ts
git commit -m "feat: add Cosmos Predict Lambda builder functions"
```

---

### Task 12: Create Cosmos Predict construct

**Files:**
- Create: `infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/constructs/cosmosPredict-construct.ts`

This is the largest CDK file. It creates all shared and per-model resources.

- [ ] **Step 1: Write cosmosPredict-construct.ts**

The construct should create:

**Shared resources (always created when useCosmosPredict.enabled):**

1. **S3 Model Cache Bucket** — `vams-cosmos-model-cache-{account}-{region}`, KMS encrypted, TLS enforced, IA lifecycle at 90 days
2. **EFS FileSystem** — encrypted with shared KMS key, elastic throughput, IA lifecycle at 30 days, mount targets in pipeline subnets, security group allowing NFS (2049) from Batch compute SG, removal policy RETAIN
3. **ECR Repository + DockerImageAsset** — single image from `backendPipelines/genAi/cosmosPredict/container/`
4. **IAM Roles** — container execution role and container job role (same pattern as SplatToolbox: S3 read/write on asset buckets + model cache bucket, EFS client mount, states:SendTask*, SSM GetParameter for HF_TOKEN)
5. **Batch Compute Environment** — via `BatchGpuPipelineConstruct`, instance types from config, launch template with 200GB gp3 EBS + EFS mount user data. **Warm instances**: if `config.app.pipelines.useCosmosPredict.useWarmInstances === true`, set `minVCpus = config.app.pipelines.useCosmosPredict.warmInstanceCount * 48` (48 vCPUs per g5.12xlarge). If `false` (default), set `minVCpus = 0` (scale to zero, cold start)
6. **Batch Job Queue** — shared by all model types
7. **Shared Lambda Functions** — constructPipeline, pipelineEnd (openPipeline may need to be per-model if STATE_MACHINE_ARN is an env var, or shared if passed in payload)

**Per-model resources (conditional on each model's enabled flag):**

For each of text2world7B and video2world7B:
1. **Batch Job Definition** — container image from shared ECR, vCPUs 48, memory 120000, GPUs 4, privileged, shared memory 8192, device mappings, env vars (MODEL_TYPE, AWS_REGION), secrets (HF_TOKEN from SSM), EFS mount at `/mnt/efs/cosmos-models`, S3_MODEL_BUCKET env var, timeout 28800
2. **vamsExecute Lambda** — model-specific handler
3. **openPipeline Lambda** — with model-specific STATE_MACHINE_ARN
4. **Step Functions State Machine** — ConstructPipeline -> BatchSubmitJob (RUN_JOB) -> PipelineEnd, with error handling, 5hr timeout, logging, tracing
5. **Custom Resource** — pipeline + workflow auto-registration via importGlobalPipelineWorkflow

Key CDK patterns to follow:
- Use `BatchGpuPipelineConstruct` (from `../constructs/batch-gpu-pipeline.ts`) for compute environment, but extend it with EFS mount in the launch template user data
- Reference `splatToolbox-construct.ts` for IAM roles, state machine definition, custom resource registration, and CDK Nag suppressions
- Pass `S3_MODEL_BUCKET` bucket name as env var to job definition
- For the EFS mount in launch template, add user data script:
  ```bash
  yum install -y amazon-efs-utils
  mkdir -p /mnt/efs/cosmos-models
  mount -t efs -o tls ${EFS_ID}:/ /mnt/efs/cosmos-models
  echo "${EFS_ID}:/ /mnt/efs/cosmos-models efs _netdev,tls 0 0" >> /etc/fstab
  ```

**Props interface:**
```typescript
interface CosmosPredictConstructProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: lambda.LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}
```

**Public properties:**
```typescript
public readonly pipelineText2WorldVamsLambdaFunctionName?: string;
public readonly pipelineVideo2WorldVamsLambdaFunctionName?: string;
```

- [ ] **Step 2: Commit**

```bash
git add infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/constructs/cosmosPredict-construct.ts
git commit -m "feat: add Cosmos Predict CDK construct with Batch, EFS, S3, SFN"
```

---

### Task 13: Create nested stack wrapper

**Files:**
- Create: `infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/cosmosPredictBuilder-nestedStack.ts`

- [ ] **Step 1: Write cosmosPredictBuilder-nestedStack.ts**

```typescript
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import * as Config from "../../../../config/config";
import { storageResources } from "../../../nestedStacks/storage/storageBuilder-nestedStack";
import { CosmosPredictConstruct } from "./constructs/cosmosPredict-construct";

export interface CosmosPredictBuilderNestedStackProps extends cdk.NestedStackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

export class CosmosPredictBuilderNestedStack extends cdk.NestedStack {
    public readonly pipelineText2WorldVamsLambdaFunctionName: string;
    public readonly pipelineVideo2WorldVamsLambdaFunctionName: string;

    constructor(scope: Construct, id: string, props: CosmosPredictBuilderNestedStackProps) {
        super(scope, id, props);

        const cosmosPredictConstruct = new CosmosPredictConstruct(
            this,
            "CosmosPredictConstruct",
            {
                config: props.config,
                vpc: props.vpc,
                subnets: props.pipelineSubnets,
                securityGroups: props.pipelineSecurityGroups,
                storageResources: props.storageResources,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowFunctionName:
                    props.importGlobalPipelineWorkflowFunctionName,
            }
        );

        this.pipelineText2WorldVamsLambdaFunctionName =
            cosmosPredictConstruct.pipelineText2WorldVamsLambdaFunctionName ?? "";
        this.pipelineVideo2WorldVamsLambdaFunctionName =
            cosmosPredictConstruct.pipelineVideo2WorldVamsLambdaFunctionName ?? "";
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/cosmosPredictBuilder-nestedStack.ts
git commit -m "feat: add Cosmos Predict nested stack wrapper"
```

---

## Phase 5: Integration

### Task 14: Update VPC endpoint conditions

**Files:**
- Modify: `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts`

- [ ] **Step 1: Add useCosmosPredict to VPC endpoint conditions**

In `vpcBuilder-nestedStack.ts`, find the pipeline-conditional VPC endpoint block (around line 540) and add `props.config.app.pipelines.useCosmosPredict.enabled ||` to the condition:

```typescript
if (
    props.config.app.pipelines.usePreviewPcPotreeViewer.enabled ||
    props.config.app.pipelines.usePreview3dThumbnail.enabled ||
    props.config.app.pipelines.useGenAiMetadata3dLabeling.enabled ||
    props.config.app.pipelines.useRapidPipeline.useEcs.enabled ||
    props.config.app.pipelines.useRapidPipeline.useEks.enabled ||
    props.config.app.pipelines.useModelOps.enabled ||
    props.config.app.pipelines.useSplatToolbox.enabled ||
    props.config.app.pipelines.useIsaacLabTraining?.enabled ||
    props.config.app.pipelines.useCosmosPredict.enabled  // ADD THIS LINE
) {
```

- [ ] **Step 2: Add EFS VPC endpoint**

Inside the same condition block, after the existing ECR Docker endpoint, add an EFS endpoint:

```typescript
// EFS endpoint for Cosmos Predict model storage
if (props.config.app.pipelines.useCosmosPredict.enabled) {
    new ec2.InterfaceVpcEndpoint(this, "EFSEndpoint", {
        vpc: this.vpc,
        privateDnsEnabled: true,
        service: ec2.InterfaceVpcEndpointAwsService.ELASTIC_FILESYSTEM,
        subnets: { subnets: this.isolatedSubnets },
        securityGroups: [vpceSecurityGroup],
    });
}
```

- [ ] **Step 3: Commit**

```bash
git add infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts
git commit -m "feat: add Cosmos Predict to VPC endpoint conditions + EFS endpoint"
```

---

### Task 15: Update pipeline builder

**Files:**
- Modify: `infra/lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts`

- [ ] **Step 1: Add import**

```typescript
import { CosmosPredictBuilderNestedStack } from "./genAi/cosmosPredict/cosmosPredictBuilder-nestedStack";
```

- [ ] **Step 2: Add conditional instantiation**

After the SplatToolbox block (around line 108), add:

```typescript
if (props.config.app.pipelines.useCosmosPredict.enabled) {
    const cosmosPredictPipelineNestedStack = new CosmosPredictBuilderNestedStack(
        this,
        "CosmosPredictBuilderNestedStack",
        {
            ...props,
            config: props.config,
            storageResources: props.storageResources,
            vpc: props.vpc,
            pipelineSubnets: pipelineNetwork.privateSubnets.pipeline,
            pipelineSecurityGroups: [pipelineNetwork.securityGroups.pipeline],
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowFunctionName:
                props.importGlobalPipelineWorkflowFunctionName,
        }
    );

    if (props.config.app.pipelines.useCosmosPredict.models.text2world7B.enabled) {
        this.pipelineVamsLambdaFunctionNames.push(
            cosmosPredictPipelineNestedStack.pipelineText2WorldVamsLambdaFunctionName
        );
    }
    if (props.config.app.pipelines.useCosmosPredict.models.video2world7B.enabled) {
        this.pipelineVamsLambdaFunctionNames.push(
            cosmosPredictPipelineNestedStack.pipelineVideo2WorldVamsLambdaFunctionName
        );
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add infra/lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts
git commit -m "feat: integrate Cosmos Predict pipeline into pipeline builder"
```

---

### Task 16: Verify CDK synth

- [ ] **Step 1: Run CDK synth with Cosmos disabled (default)**

Run: `cd infra && npx cdk synth 2>&1 | tail -5`
Expected: Successful synthesis, no errors

- [ ] **Step 2: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: complete Cosmos Predict CDK integration"
```

---

## Phase 6: Documentation

### Task 17: Create NVIDIA Cosmos documentation page

**Files:**
- Create: `documentation/docusaurus-site/docs/pipelines/nvidia-cosmos.md`

- [ ] **Step 1: Write nvidia-cosmos.md**

Create a comprehensive documentation page covering:
1. Overview of NVIDIA Cosmos integration
2. Prerequisites (HuggingFace account, license acceptance, GPU instance availability, SSM parameter for HF token)
3. Configuration reference (all `useCosmosPredict.*` options with descriptions and defaults)
4. Cosmos Predict sub-section:
   - Text2World usage (setting COSMOS_PREDICT_PROMPT on asset metadata, triggering pipeline)
   - Video2World usage (uploading video/image, setting COSMOS_PREDICT_PROMPT on file metadata)
   - Prompt priority (metadata > inputParameters > none)
   - Output format (5s MP4 video, 1280x704, 24fps, with GIF preview)
5. GPU instance recommendations table (7B: g5.12xlarge, 14B: p4d/p5)
6. Model caching (how EFS+S3 works, first-run download behavior for all 3 model components including T5-11B)
7. Troubleshooting (OOM, HF token errors, EFS mount issues)
8. Future models placeholder (14B, Cosmos Predict2.5)
9. "Built on NVIDIA Cosmos" attribution per licensing requirements

- [ ] **Step 2: Commit**

```bash
git add documentation/docusaurus-site/docs/pipelines/nvidia-cosmos.md
git commit -m "docs: add NVIDIA Cosmos pipeline documentation"
```

---

### Task 18: Generate architecture diagram

**Files:**
- Create: architecture diagram PNG in `documentation/` or `generated-diagrams/`

- [ ] **Step 1: Generate diagram using AWS diagrams MCP tool**

Use the `generate_diagram` MCP tool to create a professional architecture diagram showing the full pipeline flow:
- VAMS UI -> API Gateway -> vamsExecute Lambda (T2W/V2W)
- Step Functions state machine
- constructPipeline Lambda -> Batch Job (g5.12xlarge, 4x A10G)
- Batch container connections to: EFS (model weights), S3 Model Cache, S3 Asset Bucket (output), HuggingFace (first download)
- pipelineEnd Lambda with SFN callback

Use AWS service icons for all components.

- [ ] **Step 2: Add diagram reference to nvidia-cosmos.md**

- [ ] **Step 3: Commit**

```bash
git add documentation/ generated-diagrams/
git commit -m "docs: add Cosmos Predict architecture diagram"
```

---

### Task 19: Update configuration reference

**Files:**
- Modify: `documentation/docusaurus-site/docs/deployment/configuration-reference.md`

- [ ] **Step 1: Add Cosmos Predict config options**

Add after the existing pipeline configuration entries:

```markdown
-   `app.pipelines.useCosmosPredict.enabled` | default: false | # Enable NVIDIA Cosmos Predict pipeline
-   `app.pipelines.useCosmosPredict.huggingFaceToken` | default: "" | # SSM SecureString parameter path for HuggingFace token (e.g., /vams/cosmos/hf-token)
-   `app.pipelines.useCosmosPredict.useWarmInstances` | default: false | # Keep GPU instances running when idle for instant job starts (~$5.67/hr per g5.12xlarge)
-   `app.pipelines.useCosmosPredict.warmInstanceCount` | default: 1 | # Number of warm GPU instances to maintain when useWarmInstances is true
-   `app.pipelines.useCosmosPredict.models.text2world7B.enabled` | default: false | # Enable Cosmos-Predict1-7B-Text2World model
-   `app.pipelines.useCosmosPredict.models.text2world7B.autoRegisterWithVAMS` | default: true | # Auto-register pipeline with VAMS on deploy
-   `app.pipelines.useCosmosPredict.models.text2world7B.instanceTypes` | default: ["g5.12xlarge"] | # EC2 GPU instance types for Batch compute environment
-   `app.pipelines.useCosmosPredict.models.text2world7B.maxVCpus` | default: 48 | # Maximum vCPUs for Batch compute environment
-   `app.pipelines.useCosmosPredict.models.video2world7B.enabled` | default: false | # Enable Cosmos-Predict1-7B-Video2World model
-   `app.pipelines.useCosmosPredict.models.video2world7B.autoRegisterWithVAMS` | default: true | # Auto-register pipeline with VAMS on deploy
-   `app.pipelines.useCosmosPredict.models.video2world7B.autoTriggerOnFileExtensionsUpload` | default: "" | # File extensions to auto-trigger pipeline on upload (e.g., ".mp4,.mov,.jpg")
-   `app.pipelines.useCosmosPredict.models.video2world7B.instanceTypes` | default: ["g5.12xlarge"] | # EC2 GPU instance types for Batch compute environment
-   `app.pipelines.useCosmosPredict.models.video2world7B.maxVCpus` | default: 48 | # Maximum vCPUs for Batch compute environment
```

- [ ] **Step 2: Commit**

```bash
git add documentation/docusaurus-site/docs/deployment/configuration-reference.md
git commit -m "docs: add Cosmos Predict config options to configuration reference"
```

---

### Task 20: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add Cosmos Predict to directory structure tree**

In the directory structure section, add under `backendPipelines/`:
```
├── backendPipelines/
│   ├── genAi/
│   │   ├── cosmosPredict/         # NVIDIA Cosmos Predict pipeline (Text2World, Video2World)
│   │   └── metadata3dLabeling/    # GenAI metadata labeling
│   └── 3dRecon/
│       └── splatToolbox/          # Gaussian Splat pipeline
```

- [ ] **Step 2: Add to pipeline list in Architecture Summary**

Add to the Pipeline Architecture section or create a new entry in the processing pipelines list.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add Cosmos Predict pipeline to CLAUDE.md"
```

---

## Self-Review Checklist

### Spec Coverage
- [x] CDK config with huggingFaceToken, models sub-sections, per-model enabled flags — Task 1
- [x] Config validation (no models enabled error, HF token required, VPC auto-enable) — Task 1
- [x] Text2World vamsExecute reads asset metadata for COSMOS_PREDICT_PROMPT — Task 2
- [x] Video2World vamsExecute reads file metadata for COSMOS_PREDICT_PROMPT — Task 3
- [x] Shared constructPipeline, openPipeline, pipelineEnd — Tasks 4-6
- [x] Model manager with EFS/S3/HuggingFace cascade — Task 7
- [x] All 3 model downloads: diffusion, tokenizer, T5-11B text encoder — Task 7 (get_required_models)
- [x] Inference routing with always-on offloading flags — Task 8
- [x] VAMS wrapper with S3 I/O, preview generation, task callbacks — Task 9
- [x] Dockerfile with NGC base, conda env, framework install — Task 10
- [x] Lambda builders (5 functions) — Task 11
- [x] CDK construct with EFS, S3, Batch, SFN, registration — Task 12
- [x] Nested stack wrapper — Task 13
- [x] VPC endpoint update + EFS endpoint — Task 14
- [x] Pipeline builder integration — Task 15
- [x] Output stored back to VAMS via proper S3 paths — Task 9 (__main__.py)
- [x] Preview GIF generation — Task 9 (__main__.py)
- [x] Documentation page — Task 17
- [x] Architecture diagram — Task 18
- [x] Config reference update — Task 19
- [x] CLAUDE.md update — Task 20

### Type/Name Consistency
- `modelType`: "text2world" / "video2world" — consistent across vamsExecute, constructPipeline, __main__.py, inference.py
- `cosmosPrompt`: set in vamsExecute, passed through constructPipeline, used in __main__.py
- `COSMOS_PREDICT_PROMPT`: metadata key checked in both vamsExecute Lambdas
- `OPEN_PIPELINE_FUNCTION_NAME`: env var in both vamsExecute builders
- `STATE_MACHINE_ARN`: env var in openPipeline builder
- `EXTERNAL_SFN_TASK_TOKEN`: env var passed to container in Batch job override
- `S3_MODEL_BUCKET`: env var passed to container
- `HF_TOKEN`: secret from SSM in Batch job definition
