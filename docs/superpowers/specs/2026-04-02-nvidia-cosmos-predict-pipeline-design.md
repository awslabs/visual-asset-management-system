# NVIDIA Cosmos Predict Pipeline - Design Specification

**Date:** 2026-04-02
**Status:** Approved
**Author:** Kurt Scheuringer + Claude

## Overview

Add an NVIDIA Cosmos Predict pipeline to VAMS that supports multiple model instantiations for world generation from text prompts and video/image inputs. The pipeline runs containerized inference on AWS Batch with EC2 GPU instances (g5.12xlarge, 4x NVIDIA A10G), using shared EFS for model weight caching and S3 for model backup storage.

### Supported Model Types (Initial)

| Model | Input | Output | GPU Memory (full offload) |
|---|---|---|---|
| Cosmos-Predict1-7B-Text2World | Text prompt | 5s MP4 video (1280x704, 24fps) | 24.4 GB |
| Cosmos-Predict1-7B-Video2World | Image/video + optional text prompt | 5s MP4 video (1280x704, 24fps) | 27.3 GB |

### Future Model Types (Planned)

| Model | Notes |
|---|---|
| Cosmos-Predict1-14B-Text2World | ~39 GB VRAM, needs A100/H100 (p4d/p5 instances) |
| Cosmos-Predict1-14B-Video2World | ~39 GB VRAM, needs A100/H100 |
| Cosmos-Predict2.5 variants | Different architecture (flow-based), 2B and 14B sizes |

---

## CDK Configuration

### Config Schema (`infra/config/config.json`)

```json
{
  "app": {
    "pipelines": {
      "useCosmosPredict": {
        "enabled": false,
        "huggingFaceToken": "",
        "models": {
          "text2world7B": {
            "enabled": false,
            "autoRegisterWithVAMS": true,
            "instanceTypes": ["g5.12xlarge"],
            "maxVCpus": 48
          },
          "video2world7B": {
            "enabled": false,
            "autoRegisterWithVAMS": true,
            "autoTriggerOnFileExtensionsUpload": "",
            "instanceTypes": ["g5.12xlarge"],
            "maxVCpus": 48
          }
        }
      }
    }
  }
}
```

### Config TypeScript Interface (`infra/config/config.ts`)

```typescript
interface CosmosModelConfig {
    enabled: boolean;
    autoRegisterWithVAMS: boolean;
    instanceTypes: string[];
    maxVCpus: number;
}

interface CosmosVideo2WorldModelConfig extends CosmosModelConfig {
    autoTriggerOnFileExtensionsUpload: string;
}

interface CosmosPredict {
    enabled: boolean;
    huggingFaceToken: string; // SSM SecureString parameter path
    models: {
        text2world7B: CosmosModelConfig;
        video2world7B: CosmosVideo2WorldModelConfig;
    };
}
```

### Validation Rules (in `getConfig()`)

1. **No models enabled check**: If `useCosmosPredict.enabled === true` but every model sub-section has `enabled: false`, throw `"Configuration Error: useCosmosPredict is enabled but no model types are enabled. Enable at least one model in useCosmosPredict.models."`.
2. **VPC auto-enable**: If `useCosmosPredict.enabled === true`, set `useGlobalVpc.enabled = true`.
3. **HuggingFace token required**: If `useCosmosPredict.enabled === true` and `huggingFaceToken` is empty, throw `"Configuration Error: useCosmosPredict requires huggingFaceToken (SSM SecureString parameter path) for model downloads."`.
4. **Instance types required**: For each enabled model, validate `instanceTypes` is a non-empty array.
5. **Default population**: Set defaults for undefined sub-fields (enabled=false, autoRegisterWithVAMS=true, instanceTypes=["g5.12xlarge"], maxVCpus=48).

---

## Directory Structure

```
backendPipelines/
└── genAi/
    └── cosmosPredict/
        ├── lambda/
        │   ├── vamsExecuteCosmosText2WorldPipeline.py
        │   ├── vamsExecuteCosmosVideo2WorldPipeline.py
        │   ├── constructPipeline.py
        │   ├── openPipeline.py
        │   └── pipelineEnd.py
        └── container/
            ├── Dockerfile
            ├── entrypoint.sh
            ├── __main__.py
            ├── model_manager.py
            └── inference.py

infra/lib/nestedStacks/pipelines/genAi/cosmosPredict/
├── cosmosPredictBuilder-nestedStack.ts
├── constructs/
│   └── cosmosPredict-construct.ts
└── lambdaBuilder/
    └── cosmosPredictFunctions.ts
```

---

## Container Architecture

### Base Image & Dependencies

- **Base**: `nvcr.io/nvidia/pytorch:24.10-py3` (NVIDIA NGC PyTorch, CUDA 12.4)
- **Framework**: `cosmos-predict1` cloned from `https://github.com/nvidia-cosmos/cosmos-predict1.git`
- **Conda environment**: from `cosmos-predict1.yaml` (Python 3.10, PyTorch 2.6.0, CUDA 12.4)
- **Additional**: transformer-engine 1.12.0, NVIDIA APEX (compiled from source)
- **TORCH_CUDA_ARCH_LIST**: `"8.0 8.6 8.9"` (A100, A10G, L4/L40S)

Model weights are NOT baked into the image. They are managed at runtime via the model manager.

### Dockerfile

```dockerfile
FROM nvcr.io/nvidia/pytorch:24.10-py3

# System dependencies
RUN apt-get update && apt-get install -y ffmpeg git && rm -rf /var/lib/apt/lists/*

# Clone Cosmos-Predict1 framework
RUN git clone https://github.com/nvidia-cosmos/cosmos-predict1.git /opt/cosmos-predict1
WORKDIR /opt/cosmos-predict1

# Create conda environment
RUN conda env create -f cosmos-predict1.yaml && \
    conda clean -afy

# Install transformer-engine and APEX
SHELL ["conda", "run", "-n", "cosmos-predict1", "/bin/bash", "-c"]
RUN pip install transformer-engine[pytorch]==1.12.0
RUN git clone https://github.com/NVIDIA/apex.git /tmp/apex && \
    cd /tmp/apex && \
    pip install -v --disable-pip-version-check --no-cache-dir \
    --no-build-isolation --config-settings "--build-option=--cpp_ext" \
    --config-settings "--build-option=--cuda_ext" . && \
    rm -rf /tmp/apex

# Install boto3 for S3 operations + huggingface_hub for model download
RUN pip install boto3 huggingface_hub

# VAMS integration files
COPY __main__.py model_manager.py inference.py entrypoint.sh /opt/ml/code/
RUN chmod +x /opt/ml/code/entrypoint.sh

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
```

### Model Manager (`model_manager.py`)

Handles the EFS + S3 hybrid caching with lazy-load on first run.

**Flow:**
```
ensure_model_available(model_name, efs_base_path, s3_bucket, hf_token)
  │
  ├─ EFS path exists and has manifest?
  │   ├─ YES → Compare S3 manifest hash with EFS manifest hash
  │   │   ├─ Match → Return EFS path (fast path)
  │   │   └─ Mismatch → Sync from S3 to EFS, update EFS manifest
  │   │
  │   └─ NO → Check S3 for cached model
  │       ├─ S3 has model → Sync S3 → EFS, write EFS manifest
  │       └─ S3 empty → Download from HuggingFace → Write to EFS + sync to S3
  │
  └─ Return EFS model path
```

**Manifest format:** JSON file per model containing MD5 hashes of key checkpoint files (not full directory hash, which would be too slow for 45GB+).

**Required models per type:**

| Model Type | Required Checkpoints |
|---|---|
| Text2World 7B | Cosmos-Predict1-7B-Text2World, Cosmos-Tokenize1-CV8x8x8-720p, google-t5/t5-11b |
| Video2World 7B | Cosmos-Predict1-7B-Video2World, Cosmos-Tokenize1-CV8x8x8-720p, google-t5/t5-11b |

The tokenizer and T5 text encoder are shared between models -- downloaded once, used by both.

**EFS directory layout:**
```
/mnt/efs/cosmos-models/
├── Cosmos-Predict1-7B-Text2World/
├── Cosmos-Predict1-7B-Video2World/
├── Cosmos-Tokenize1-CV8x8x8-720p/      # Shared
├── google-t5-t5-11b/                     # Shared
└── manifests/
    ├── Cosmos-Predict1-7B-Text2World.json
    ├── Cosmos-Predict1-7B-Video2World.json
    ├── Cosmos-Tokenize1-CV8x8x8-720p.json
    └── google-t5-t5-11b.json
```

### Inference Routing (`inference.py`)

```python
def run_inference(model_type: str, model_size: str, prompt: str,
                  input_file_path: str | None, output_dir: str,
                  efs_model_dir: str, num_gpus: int = 4):
    """Route to correct Cosmos inference script based on model type."""

    checkpoint_dir = efs_model_dir
    diffusion_dir = f"Cosmos-Predict1-{model_size}-{'Text2World' if model_type == 'text2world' else 'Video2World'}"

    base_args = [
        "--checkpoint_dir", checkpoint_dir,
        "--diffusion_transformer_dir", diffusion_dir,
        "--video_save_folder", output_dir,
        "--num_gpus", str(num_gpus),
        "--offload_diffusion_transformer",
        "--offload_tokenizer",
        "--offload_text_encoder_model",
        "--offload_prompt_upsampler",
    ]

    if model_type == "text2world":
        script = "cosmos_predict1/diffusion/inference/text2world.py"
        if prompt:
            base_args.extend(["--prompt", prompt])
    elif model_type == "video2world":
        script = "cosmos_predict1/diffusion/inference/video2world.py"
        base_args.extend(["--input_image_or_video_path", input_file_path])
        base_args.extend(["--num_input_frames", "1"])
        if prompt:
            base_args.extend(["--prompt", prompt])
            base_args.append("--disable_prompt_upsampler")

    cmd = ["torchrun", "--nproc_per_node", str(num_gpus), script] + base_args
    subprocess.run(cmd, check=True, env={**os.environ, "PYTHONPATH": "/opt/cosmos-predict1"})
```

### VAMS Integration Wrapper (`__main__.py`)

```python
"""VAMS pipeline wrapper for Cosmos Predict inference."""

def main():
    # 1. Parse pipeline definition from Batch command or env var
    pipeline_def = load_pipeline_definition()

    # 2. Extract parameters
    model_type = pipeline_def["modelType"]           # "text2world" or "video2world"
    model_size = pipeline_def.get("modelSize", "7B")
    cosmos_prompt = pipeline_def.get("cosmosPrompt", "")
    input_s3_path = pipeline_def.get("inputS3AssetFilePath", "")
    output_s3_files_path = pipeline_def.get("outputS3AssetFilesPath", "")
    asset_id = pipeline_def.get("assetId", "")
    hf_token = os.environ.get("HF_TOKEN", "")
    s3_model_bucket = os.environ.get("S3_MODEL_BUCKET", "")
    sfn_task_token = os.environ.get("EXTERNAL_SFN_TASK_TOKEN", "")

    # 3. Determine prompt (cosmosPrompt takes precedence, set by vamsExecute Lambda)
    prompt = cosmos_prompt or pipeline_def.get("inputParametersPrompt", "")

    # 4. Ensure models are available (EFS/S3/HuggingFace cascade)
    efs_model_dir = "/mnt/efs/cosmos-models"
    required_models = get_required_models(model_type, model_size)
    for model_name in required_models:
        ensure_model_available(model_name, efs_model_dir, s3_model_bucket, hf_token)

    # 5. Download input file for Video2World
    local_input = None
    if model_type == "video2world" and input_s3_path:
        local_input = download_from_s3(input_s3_path, "/tmp/input/")

    # 6. Run inference
    output_dir = "/tmp/output"
    run_inference(model_type, model_size, prompt, local_input, output_dir, efs_model_dir)

    # 7. Upload output video to VAMS S3 output path
    output_video = find_output_video(output_dir)
    if model_type == "video2world":
        # File-level output: preserve relative path from input
        relative_subdir = compute_relative_subdir(input_s3_path, asset_id)
        input_filename = os.path.basename(input_s3_path)
        output_key = f"{output_s3_files_path}{relative_subdir}/{input_filename}.cosmos-v2w.mp4"
    else:
        # Asset-level output: new file at asset root
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_key = f"{output_s3_files_path}cosmos-text2world-{timestamp}.mp4"

    upload_to_s3(output_video, output_key)

    # 8. Generate preview thumbnail (first frame as GIF)
    preview_path = generate_preview_gif(output_video)
    preview_key = f"{output_key}.previewFile.gif"
    upload_to_s3(preview_path, preview_key)

    # 9. Send Step Functions task success callback
    if sfn_task_token:
        send_task_success(sfn_task_token, {"status": "complete", "outputKey": output_key})
```

---

## Lambda Functions

### vamsExecuteCosmosText2WorldPipeline.py

```python
"""VAMS entry point for Cosmos Predict Text2World pipeline."""

OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]

def lambda_handler(event, context):
    data = json.loads(event['body'])

    input_metadata = data.get('inputMetadata', '')
    input_parameters = data.get('inputParameters', '')
    external_task_token = data.get('TaskToken', '')

    # Extract COSMOS_PREDICT_PROMPT from ASSET metadata (not file metadata)
    cosmos_prompt = ""
    if input_metadata:
        metadata = json.loads(input_metadata)
        asset_meta = metadata.get("assetMetadata", {}).get("metadata", [])
        for item in asset_meta:
            if item.get("metadataKey") == "COSMOS_PREDICT_PROMPT":
                cosmos_prompt = item.get("metadataValue", "")
                break

    # Build payload - cosmosPrompt overrides inputParameters prompt
    message_payload = {
        "modelType": "text2world",
        "cosmosPrompt": cosmos_prompt,  # Empty string if not found
        "inputS3AssetFilePath": "",     # No input file for Text2World
        "outputS3AssetFilesPath": data.get("outputS3AssetFilesPath", ""),
        "outputS3AssetPreviewPath": data.get("outputS3AssetPreviewPath", ""),
        "outputS3AssetMetadataPath": data.get("outputS3AssetMetadataPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": data.get("inputOutputS3AssetAuxiliaryFilesPath", ""),
        "assetId": data.get("assetId", ""),
        "databaseId": data.get("databaseId", ""),
        "inputParameters": input_parameters,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": data.get("executingUserName", ""),
        "executingRequestContext": data.get("executingRequestContext", ""),
    }

    lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(message_payload).encode('utf-8')
    )

    return {"statusCode": 200, "body": json.dumps({"message": "Pipeline started"})}
```

### vamsExecuteCosmosVideo2WorldPipeline.py

Same structure as above, except:
- Reads `COSMOS_PREDICT_PROMPT` from **file metadata** (`fileMetadata.metadata`) instead of asset metadata
- Passes `inputS3AssetFilePath` from the event (specific file to process)
- Sets `modelType: "video2world"`

### constructPipeline.py (Shared)

```python
"""Transform workflow event into Batch job definition for Cosmos Predict."""

def construct_cosmos_definition(event):
    model_type = event.get("modelType", "video2world")
    asset_id = event.get("assetId", "")

    definition = json.dumps({
        "modelType": model_type,
        "modelSize": event.get("modelSize", "7B"),
        "cosmosPrompt": event.get("cosmosPrompt", ""),
        "inputParametersPrompt": extract_prompt_from_params(event.get("inputParameters", "")),
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath", ""),
        "outputS3AssetFilesPath": event.get("outputS3AssetFilesPath", ""),
        "outputS3AssetPreviewPath": event.get("outputS3AssetPreviewPath", ""),
        "outputS3AssetMetadataPath": event.get("outputS3AssetMetadataPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": event.get("inputOutputS3AssetAuxiliaryFilesPath", ""),
        "assetId": asset_id,
        "databaseId": event.get("databaseId", ""),
    })

    return {
        "jobName": f"cosmos-{model_type}-{uuid4().hex[:8]}",
        "definition": ["python", "__main__.py", definition],
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("sfnExternalTaskToken", ""),
        "status": "success",
    }
```

### openPipeline.py (Shared)

- Validates file extension for Video2World: `.mp4`, `.mov`, `.jpg`, `.jpeg`, `.png`, `.webp`
- For Text2World (no `inputS3AssetFilePath`): skips file validation
- Starts Step Functions state machine execution
- Environment variable: `ALLOWED_INPUT_FILEEXTENSIONS`
- **State machine ARN routing**: Each vamsExecute Lambda passes the correct `STATE_MACHINE_ARN` in its invocation payload to openPipeline (not as a Lambda env var). This allows the shared openPipeline to route to the correct state machine per model type. Alternatively, if the VAMS pattern requires `STATE_MACHINE_ARN` as an env var, we create two openPipeline instances (one per model type) with different env vars -- following whichever pattern is simpler.

**Note on Text2World asset-level execution**: VAMS workflow execution normally requires a `fileKey` (specific file). For Text2World, which operates on the global asset without a specific file, the workflow should be triggered with an empty or sentinel `fileKey`. The vamsExecute Lambda for Text2World ignores `inputAssetFileKey` and only uses `assetId` + `databaseId` for metadata lookup and output path construction. The `executeWorkflow.py` handler already handles the case where `filePath` is empty by skipping file metadata retrieval (only fetching asset metadata).

### pipelineEnd.py (Shared)

- Cleanup handler
- Sends `states:SendTaskSuccess` or `states:SendTaskFailure` via task token callback
- Same pattern as Gaussian Splat `pipelineEnd.py`

---

## CDK Infrastructure

### Nested Stack (`cosmosPredictBuilder-nestedStack.ts`)

```typescript
interface CosmosPredictBuilderNestedStackProps extends cdk.NestedStackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

// Exports:
// - pipelineText2WorldVamsLambdaFunctionName (if text2world7B enabled)
// - pipelineVideo2WorldVamsLambdaFunctionName (if video2world7B enabled)
```

### Main Construct (`cosmosPredict-construct.ts`)

#### Shared Resources (created once regardless of which models are enabled)

**S3 Model Cache Bucket:**
- Bucket name: `vams-cosmos-model-cache-{account}-{region}`
- KMS encryption (shared VAMS key)
- TLS enforced via bucket policy
- Lifecycle: transition to IA after 90 days

**EFS FileSystem:**
- Encrypted with shared KMS key
- Performance mode: generalPurpose
- Throughput mode: elastic (scales with usage, important for large model downloads)
- Lifecycle: transition to IA after 30 days
- Mount targets: one per pipeline subnet
- Security group: allows NFS (port 2049) from Batch compute security group
- Removal policy: RETAIN (model data persists across stack updates)

**ECR Repository + Docker Image:**
- Single ECR repo for the Cosmos container image
- `DockerImageAsset` builds from `backendPipelines/genAi/cosmosPredict/container/`
- Image tag based on build hash

**Batch Compute Environment:**
- Uses `BatchGpuPipelineConstruct` (reusable construct from SplatToolbox pattern)
- Instance types: from config (default: `["g5.12xlarge"]`)
- Min vCPUs: 0, Max vCPUs: from config (default: 48)
- Allocation strategy: `BEST_FIT_PROGRESSIVE`
- Launch template: 200GB gp3 encrypted EBS + EFS mount via user data
- AMI: ECS AL2 (Amazon Linux 2)
- Security group: outbound internet for HuggingFace downloads

**Launch Template User Data** (EFS mount):
```bash
#!/bin/bash
yum install -y amazon-efs-utils
mkdir -p /mnt/efs/cosmos-models
mount -t efs -o tls ${EFS_ID}:/ /mnt/efs/cosmos-models
echo "${EFS_ID}:/ /mnt/efs/cosmos-models efs _netdev,tls 0 0" >> /etc/fstab
mkdir -p /mnt/workspace
```

**Batch Job Queue:**
- Single queue shared by all Cosmos model types
- Priority: 1
- State: ENABLED

**Shared Lambda Functions:**
- `constructPipelineFunction` (shared across model types)
- `openPipelineFunction` (shared, different env vars per state machine)
- `pipelineEndFunction` (shared)

#### Per-Model Resources (conditional on each model's `enabled` flag)

**For each enabled model (text2world7B, video2world7B):**

1. **Batch Job Definition:**
   - Container image: shared ECR image
   - vCPUs: 48, Memory: 120,000 MB, GPUs: 4
   - Privileged mode, shared memory 8192 MB
   - Device mappings: `/dev/nvidia0-3`, `/dev/nvidiactl`, `/dev/nvidia-uvm`
   - Environment: `AWS_REGION`, `MODEL_TYPE`, `MODEL_SIZE`
   - Secrets: `HF_TOKEN` from SSM SecureString
   - Mount points: EFS at `/mnt/efs/cosmos-models`, `/tmp` workspace volume
   - Timeout: 28,800 seconds (8 hours)
   - Retry: 1 attempt

2. **vamsExecute Lambda:**
   - Text2World: `vamsExecuteCosmosText2WorldPipeline.lambda_handler`
   - Video2World: `vamsExecuteCosmosVideo2WorldPipeline.lambda_handler`
   - Env: `OPEN_PIPELINE_FUNCTION_NAME`
   - Grants: invoke openPipeline, read asset buckets

3. **Step Functions State Machine:**
   ```
   ConstructPipelineTask (Lambda)
     → CosmosBatchJob (BatchSubmitJob, RUN_JOB integration)
       → [catch] HandleBatchError → PipelineEndTask
     → PipelineEndTask (Lambda)
       → EndStatesChoice
         → SuccessState / FailState
   ```
   - Container overrides pass: `EXTERNAL_SFN_TASK_TOKEN`, `INPUT_PARAMETERS`, `INPUT_METADATA`, `S3_MODEL_BUCKET`
   - Timeout: 5 hours
   - Logging: ALL, 10-year retention
   - Tracing: ENABLED

4. **Custom Resource (Pipeline + Workflow Registration):**

   Text2World:
   ```
   pipelineId: "cosmos-predict-text2world-7b"
   pipelineType: "standardFile"
   pipelineExecutionType: "Lambda"
   assetType: ".all"
   outputType: ".mp4"
   waitForCallback: "Enabled"
   taskTimeout: "28800"
   inputParameters: '{"MODEL_TYPE": "text2world", "MODEL_SIZE": "7B"}'
   workflowId: "cosmos-predict-text2world-7b"
   ```

   Video2World:
   ```
   pipelineId: "cosmos-predict-video2world-7b"
   pipelineType: "standardFile"
   pipelineExecutionType: "Lambda"
   assetType: ".all"
   outputType: ".mp4"
   waitForCallback: "Enabled"
   taskTimeout: "28800"
   autoTriggerOnFileExtensionsUpload: (from config)
   inputParameters: '{"MODEL_TYPE": "video2world", "MODEL_SIZE": "7B"}'
   workflowId: "cosmos-predict-video2world-7b"
   ```

### Lambda Builders (`cosmosPredictFunctions.ts`)

5 Lambda builder functions following standard VAMS pattern:

| Function | Handler | Key Env Vars | Key Grants |
|---|---|---|---|
| `buildVamsExecuteCosmosText2WorldFunction` | `vamsExecuteCosmosText2WorldPipeline.lambda_handler` | `OPEN_PIPELINE_FUNCTION_NAME` | invoke openPipeline, read asset buckets |
| `buildVamsExecuteCosmosVideo2WorldFunction` | `vamsExecuteCosmosVideo2WorldPipeline.lambda_handler` | `OPEN_PIPELINE_FUNCTION_NAME` | invoke openPipeline, read asset buckets |
| `buildConstructPipelineFunction` | `constructPipeline.lambda_handler` | (none) | read asset buckets |
| `buildOpenPipelineFunction` | `openPipeline.lambda_handler` | `STATE_MACHINE_ARN`, `ALLOWED_INPUT_FILEEXTENSIONS` | start state machine, read asset buckets |
| `buildPipelineEndFunction` | `pipelineEnd.lambda_handler` | (none) | read asset+auxiliary buckets, states:SendTask* |

All builders:
- Runtime: PYTHON_3_12
- Timeout: 5 minutes
- Memory: 5,308 MB (LAMBDA_MEMORY_SIZE)
- Code path: `backendPipelines/genAi/cosmosPredict/lambda`
- VPC: conditional on `useGlobalVpc.enabled && useForAllLambdas`
- Security: `kmsKeyLambdaPermissionAddToResourcePolicy()`, `globalLambdaEnvironmentsAndPermissions()`, `suppressCdkNagErrorsByGrantReadWrite()`

### IAM Roles

**Container Execution Role:**
- Principal: `ecs-tasks.amazonaws.com`
- Managed policies: `AmazonECSTaskExecutionRolePolicy`, `AWSXrayWriteOnlyAccess`
- Inline: S3 read/write on asset buckets + model cache bucket, EFS client mount, states:SendTask*

**Container Job Role:**
- Principal: `ecs-tasks.amazonaws.com`
- Same inline policies as execution role
- Additional: SSM GetParameter for HF_TOKEN

### CDK Nag Suppressions

Required suppressions (with justification):
- `AwsSolutions-IAM5`: Wildcard S3 permissions on asset buckets (dynamic bucket names from global registry)
- `AwsSolutions-IAM4`: AWS Managed Policies for Batch/ECS roles (required by service)
- `AwsSolutions-EFS1`: EFS encryption handled by KMS key
- `AwsSolutions-SQS3`: No DLQ needed for pipeline event queues

---

## Metadata & Prompt Flow

### COSMOS_PREDICT_PROMPT Metadata Key

Users can set a text prompt on their asset or file metadata using the key `COSMOS_PREDICT_PROMPT`. This metadata key is checked by the vamsExecute Lambda before pipeline execution.

**Priority order for prompt resolution:**

1. `COSMOS_PREDICT_PROMPT` metadata key (highest priority, overrides everything)
2. Prompt in `inputParameters` (fallback if metadata not set)
3. No prompt (lowest priority -- Text2World will fail without prompt, Video2World will auto-generate from input)

**Text2World:** Reads from **asset-level metadata** (`assetMetadata.metadata`)
**Video2World:** Reads from **file-level metadata** (`fileMetadata.metadata`)

### Flow Diagram

```
User sets COSMOS_PREDICT_PROMPT metadata on asset/file in VAMS UI
  │
  ▼
User triggers workflow execution (manual or auto-trigger on upload)
  │
  ▼
executeWorkflow.py (existing VAMS code)
  ├─ get_asset_metadata(databaseId, assetId) → includes COSMOS_PREDICT_PROMPT
  ├─ get_file_metadata(databaseId, assetId, filePath) → includes COSMOS_PREDICT_PROMPT
  └─ inputMetadata = JSON({ assetMetadata, fileMetadata, fileAttributes })
  │
  ▼
Step Functions passes inputMetadata + inputParameters to vamsExecute Lambda
  │
  ▼
vamsExecute Lambda (model-specific)
  ├─ Text2World: scans assetMetadata for COSMOS_PREDICT_PROMPT
  └─ Video2World: scans fileMetadata for COSMOS_PREDICT_PROMPT
  │
  ├─ Found → set cosmosPrompt = metadata value (overrides inputParameters)
  └─ Not found → cosmosPrompt = "" (inputParameters prompt used in container)
  │
  ▼
constructPipeline Lambda → Batch Job Definition
  │
  ▼
Container __main__.py
  ├─ prompt = cosmosPrompt || inputParametersPrompt || ""
  └─ inference.py → torchrun → cosmos output video
```

---

## Output Handling

### Video2World (File-Level Output)

The generated video is a file-level output tied to the specific input file.

**Output path:** `{outputS3AssetFilesPath}{relative_subdir}/{input_filename}.cosmos-v2w.mp4`

**Preview:** `{outputS3AssetFilesPath}{relative_subdir}/{input_filename}.cosmos-v2w.mp4.previewFile.gif`

**Example:**
```
Input:  s3://asset-bucket/xd130a6d6/videos/drone-footage.mp4
Output: s3://asset-bucket/xd130a6d6/videos/drone-footage.mp4.cosmos-v2w.mp4
Preview: s3://asset-bucket/xd130a6d6/videos/drone-footage.mp4.cosmos-v2w.mp4.previewFile.gif
```

The relative path (`videos/`) is preserved so the process-output step can move files to the correct final location.

### Text2World (Asset-Level Output)

The generated video is a new file added to the asset at the root level.

**Output path:** `{outputS3AssetFilesPath}cosmos-text2world-{timestamp}.mp4`

**Preview:** `{outputS3AssetFilesPath}cosmos-text2world-{timestamp}.mp4.previewFile.gif`

**Example:**
```
Output: s3://asset-bucket/xd130a6d6/cosmos-text2world-20260402-143022.mp4
Preview: s3://asset-bucket/xd130a6d6/cosmos-text2world-20260402-143022.mp4.previewFile.gif
```

### Preview Generation

The container generates a GIF preview from the first ~2 seconds of the output video using ffmpeg:
```bash
ffmpeg -i output.mp4 -t 2 -vf "fps=10,scale=320:-1" -loop 0 output.previewFile.gif
```

---

## VPC Integration

### Endpoint Conditions (`vpcBuilder-nestedStack.ts`)

Add `config.app.pipelines.useCosmosPredict.enabled` to the existing condition block that creates:
- Batch interface endpoint
- ECR API interface endpoint
- ECR DKR (Docker) interface endpoint
- ECS interface endpoint
- CloudWatch Logs interface endpoint

**New endpoint required:** EFS interface endpoint (`com.amazonaws.{region}.elasticfilesystem`)
- Required for EFS mount from Batch compute instances in VPC
- Add to the same condition block

### Subnet Configuration

Uses existing pipeline private subnets (same as SplatToolbox). The Batch compute environment security group needs:
- Outbound: HTTPS (443) to internet (for HuggingFace model downloads)
- Outbound: NFS (2049) to EFS security group
- Inbound: NFS (2049) from Batch compute security group (on EFS SG)

---

## Pipeline Builder Integration

### `pipelineBuilder-nestedStack.ts`

```typescript
if (props.config.app.pipelines.useCosmosPredict.enabled) {
    const cosmosPredictPipelineNestedStack = new CosmosPredictBuilderNestedStack(
        this,
        "CosmosPredictBuilderNestedStack",
        {
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

    // Add vamsExecute Lambda names for API Gateway routing
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

---

## Documentation

### New Page: `documentation/docusaurus-site/docs/pipelines/nvidia-cosmos.md`

Structure:
1. **Overview** - What is NVIDIA Cosmos, what models are supported
2. **Architecture Diagram** - Visual diagram showing the full pipeline flow
3. **Prerequisites** - HuggingFace account, license acceptance, GPU instance availability
4. **Configuration** - CDK config options with descriptions and defaults
5. **Cosmos Predict** (sub-section)
   - Text2World usage (setting asset metadata, triggering pipeline)
   - Video2World usage (uploading video/image, setting file metadata, triggering pipeline)
   - Prompt configuration (metadata vs inputParameters)
   - Output format and location
6. **GPU Instance Recommendations** - Table of instance types per model size
7. **Model Caching** - How EFS + S3 caching works, first-run behavior
8. **Troubleshooting** - Common issues (OOM, HF token, EFS mount)
9. **Future Models** (placeholder) - 14B models, Cosmos Predict2.5

### Architecture Diagram

Generated using the AWS diagrams package, showing:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VAMS Cosmos Predict Pipeline                   │
│                                                                       │
│  ┌──────────┐    ┌─────────────┐    ┌──────────────────────┐        │
│  │ VAMS UI  │───▶│ API Gateway │───▶│ vamsExecute Lambda   │        │
│  │          │    │  + Auth     │    │ (T2W or V2W)         │        │
│  └──────────┘    └─────────────┘    │ - Extract prompt from│        │
│                                      │   metadata           │        │
│                                      └──────────┬───────────┘        │
│                                                  │                    │
│                                                  ▼                    │
│                                      ┌──────────────────────┐        │
│                                      │ Step Functions        │        │
│                                      │ State Machine         │        │
│                                      └──────────┬───────────┘        │
│                                                  │                    │
│                          ┌───────────────────────┼────────────┐      │
│                          ▼                       ▼            ▼      │
│               ┌─────────────────┐   ┌──────────────┐  ┌──────────┐  │
│               │ constructPipeline│   │ Batch Job    │  │ pipeline │  │
│               │ Lambda          │──▶│ (g5.12xlarge) │──▶│ End     │  │
│               └─────────────────┘   │ 4x A10G GPU  │  │ Lambda  │  │
│                                      │              │  └──────────┘  │
│                                      └──────┬───────┘                │
│                                             │                        │
│                          ┌──────────────────┼──────────────────┐     │
│                          ▼                  ▼                  ▼     │
│                   ┌───────────┐     ┌───────────┐      ┌──────────┐ │
│                   │ EFS       │     │ S3 Model  │      │ S3 Asset │ │
│                   │ (models)  │◀───▶│ Cache     │      │ Bucket   │ │
│                   └─────┬─────┘     └───────────┘      │ (output) │ │
│                         │                               └──────────┘ │
│                         ▼                                            │
│                   ┌───────────┐                                      │
│                   │ HuggingFace│ (first download only)               │
│                   └───────────┘                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Configuration Reference Update

Add to `documentation/docusaurus-site/docs/deployment/configuration-reference.md`:

```
- `app.pipelines.useCosmosPredict.enabled` | default: false | # Enable NVIDIA Cosmos Predict pipeline
- `app.pipelines.useCosmosPredict.huggingFaceToken` | default: "" | # SSM SecureString parameter path for HuggingFace token
- `app.pipelines.useCosmosPredict.models.text2world7B.enabled` | default: false | # Enable Cosmos-Predict1-7B-Text2World model
- `app.pipelines.useCosmosPredict.models.text2world7B.autoRegisterWithVAMS` | default: true | # Auto-register pipeline on deploy
- `app.pipelines.useCosmosPredict.models.text2world7B.instanceTypes` | default: ["g5.12xlarge"] | # EC2 GPU instance types for Batch
- `app.pipelines.useCosmosPredict.models.text2world7B.maxVCpus` | default: 48 | # Max vCPUs for Batch compute environment
- `app.pipelines.useCosmosPredict.models.video2world7B.enabled` | default: false | # Enable Cosmos-Predict1-7B-Video2World model
- `app.pipelines.useCosmosPredict.models.video2world7B.autoRegisterWithVAMS` | default: true | # Auto-register pipeline on deploy
- `app.pipelines.useCosmosPredict.models.video2world7B.autoTriggerOnFileExtensionsUpload` | default: "" | # File extensions to auto-trigger (e.g., ".mp4,.mov,.jpg")
- `app.pipelines.useCosmosPredict.models.video2world7B.instanceTypes` | default: ["g5.12xlarge"] | # EC2 GPU instance types for Batch
- `app.pipelines.useCosmosPredict.models.video2world7B.maxVCpus` | default: 48 | # Max vCPUs for Batch compute environment
```

### CLAUDE.md Update

Add to root CLAUDE.md pipeline list and directory structure.

---

## Future Extensibility (14B Models)

When adding 14B model support:

1. Add `text2world14B` and `video2world14B` sub-sections to config (same schema as 7B)
2. Adjust default `instanceTypes` to `["p4d.24xlarge"]` or `["p5.48xlarge"]` for 14B
3. Create new Batch Job Definitions with higher memory/GPU requirements
4. Container code already supports `modelSize` parameter routing -- no container changes needed
5. Register new VAMS pipelines: `cosmos-predict-text2world-14b`, `cosmos-predict-video2world-14b`
6. Shared EFS/S3/compute resources can be reused (if instance types are compatible)
7. May need separate compute environments if 14B requires different instance types than 7B

---

## Design Decisions

### Offloading Flags Always Enabled

The `--offload_diffusion_transformer`, `--offload_tokenizer`, `--offload_text_encoder_model`, and `--offload_prompt_upsampler` flags are always passed to the inference script regardless of GPU instance size. These flags move model components to CPU RAM, reducing VRAM usage at the cost of slightly increased inference time.

**Rationale:**
- On g5.12xlarge (A10G 24GB per GPU): offloading is required -- the model does not fit without it.
- On larger instances (A100 40GB, H100 80GB): offloading is unnecessary but safe, adding ~10-20% overhead.
- Always-on avoids runtime GPU detection complexity and works on any instance type.
- If performance optimization for large-GPU customers becomes important, add an optional `offloadStrategy` config field per model sub-section that the container reads to skip offloading flags.

---

## Licensing Requirements

Per NVIDIA Open Model License:
- Include "Built on NVIDIA Cosmos" attribution in VAMS documentation
- Do not bypass/disable safety guardrails in production (license termination risk)
- Note: reference implementation uses `--disable_guardrail` flag -- we should use `--offload_guardrail_models` instead (offloads to CPU rather than disabling)
