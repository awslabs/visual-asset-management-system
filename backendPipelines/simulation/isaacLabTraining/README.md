# NVIDIA Isaac Lab Simulation Pipeline

GPU-accelerated reinforcement learning training and evaluation for robotic assets using NVIDIA IsaacLab on AWS Batch.

## Overview

This pipeline enables VAMS users to:
- **Train** RL policies for robots using GPU-accelerated simulation
- **Evaluate** trained policies and generate video recordings

It leverages AWS Batch for scalable GPU compute with automatic checkpoint management and S3 integration.

## Architecture

![Isaac Lab Pipeline Architecture](../../../documentation/diagrams/pipeline_usecase_isaacLab.png)

### Architecture Flow

1. **AWS VAMS (Existing)** - The existing AWS Visual Asset Management System (VAMS) solution facilitates storage, management, transformation, and viewing of visualization assets. The NVIDIA Isaac Lab pipeline modifications primarily affect the Amazon S3 asset and auxiliary asset file storage buckets for file uploads and AWS Lambda for VAMS pipeline execution functions.

2. **Job Configuration Upload** - Once a job configuration JSON file is uploaded to the VAMS Amazon S3 assets bucket through the VAMS Web UI, a VAMS pipeline call can be manually executed on the asset through a AWS Lambda function which notifies a downstream AWS Lambda function for kicking off the pipeline. Additional input parameters and input asset metadata can be provided to toggle on/off pipeline configuration.

3. **AWS Step Functions State Machine** - The AWS Step Functions State Machine orchestrates the Isaac Lab pipeline. AWS Batch handles scheduling and batch processing of pipeline computing jobs.

4. **Pipeline Construction** - The AWS Step State Machine first invokes a Lambda function to construct the pipeline input and check the Isaac Lab job configuration determining if a training or evaluation job has been triggered. Then, it builds the pipeline definition to be passed to AWS Batch for processing.

5. **Batch Compute Environment** - Amazon ECS EC2 manages provisioning Batch resources for the Isaac Lab job using GPU Docker Containers. Amazon ECR stores and provides the container images to AWS ECS EC2. The pipeline runs robotic training and evaluation jobs with NVIDIA Isaac Lab on GPU-enabled EC2 instances.

6. **Container Image Pull** - Amazon ECS pulls the latest Isaac Lab container image from NVIDIA NGC when building the container on AWS Batch. The private Batch compute environment connects to the internet through a NAT Gateway in a public subnet.

7. **Auxiliary Asset Bucket** - The Amazon S3 AuxiliaryAsset Bucket holds temporary data during container processing. Data in this bucket is unversioned in VAMS and is separate from VAMS versioned assets.

8. **Asset Bucket Output** - The Amazon S3 Asset Bucket receives the final metadata JSON file. Location of this file and how it is processed depends on how this pipeline was executed. If this was called from a VAMS workflow, the workflow provides a temporary pipeline output location for the particular job. The workflow then has a final AWS Lambda step to process metadata JSON objects back into VAMS Asset-viewable metadata.

9. **Elastic File System** - Amazon Elastic File System (EFS) provides durable storage between batch runs for multi-node parallel jobs, including checkpoints for the trained behavior models as well as logs.

10. **CloudWatch Logging** - Amazon CloudWatch stores log files for the VPC flow traffic, step functions states, and Fargate Container processes.

## Prerequisites

- VAMS deployed with VPC enabled (`useGlobalVpc.enabled: true`)
- AWS account with GPU instance quota (g5 or g6e instances)
- Docker installed locally (for container development)

---

## Job Configuration Files

Isaac Lab jobs are configured using JSON files that specify the training or evaluation parameters. These config files are uploaded to VAMS as assets and used to trigger pipeline executions.

### Training Config (`*-training-config.json`)

Training configs are used to train new RL policies from scratch.

#### Schema

```json
{
  "name": "string",           // Job display name
  "description": "string",    // Job description
  "trainingConfig": {
    "mode": "train",          // Must be "train" for training jobs
    "task": "string",         // Isaac Lab task name (e.g., "Isaac-Ant-Direct-v0")
    "numEnvs": number,        // Number of parallel environments (typically 1024-8192)
    "maxIterations": number,  // Training iterations (policy updates)
    "rlLibrary": "string"     // RL library: "rsl_rl" or "rl_games"
  },
  "computeConfig": {
    "numNodes": number        // Number of GPU nodes (1 for single-node training)
  }
}
```

#### Example: Cartpole Training

Simple balancing task - good for testing the pipeline:

```json
{
  "name": "Cartpole Training Job",
  "description": "Train a PPO policy for the Isaac-Cartpole-Direct-v0 environment",
  "trainingConfig": {
    "mode": "train",
    "task": "Isaac-Cartpole-Direct-v0",
    "numEnvs": 4096,
    "maxIterations": 500,
    "rlLibrary": "rsl_rl"
  },
  "computeConfig": {
    "numNodes": 1
  }
}
```

#### Example: Ant Locomotion Training

Quadruped locomotion - more complex task:

```json
{
  "name": "Ant Training Job",
  "description": "Train a PPO policy for the Isaac-Ant-Direct-v0 environment",
  "trainingConfig": {
    "mode": "train",
    "task": "Isaac-Ant-Direct-v0",
    "numEnvs": 4096,
    "maxIterations": 1000,
    "rlLibrary": "rsl_rl"
  },
  "computeConfig": {
    "numNodes": 1
  }
}
```

#### Training Parameters Guide

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Display name for the job shown in VAMS UI and logs |
| `description` | string | Yes | Human-readable description of the training job purpose |
| `trainingConfig.mode` | string | Yes | Must be `"train"` for training jobs |
| `trainingConfig.task` | string | Yes | Isaac Lab task/environment name (e.g., `"Isaac-Ant-Direct-v0"`, `"Isaac-Cartpole-Direct-v0"`) |
| `trainingConfig.numEnvs` | number | Yes | Number of parallel simulation environments. Higher values = faster training but more GPU memory. Recommended: 1024-8192 |
| `trainingConfig.maxIterations` | number | Yes | Number of policy update iterations. More iterations = longer training, potentially better policy. Recommended: 500-5000 depending on task complexity |
| `trainingConfig.rlLibrary` | string | Yes | Reinforcement learning library to use. Options: `"rsl_rl"` (recommended for locomotion), `"rl_games"`, `"skrl"` |
| `computeConfig.numNodes` | number | Yes | Number of GPU nodes for distributed training. Use `1` for single-node training |

---

### Evaluation Config (`*-evaluation-config.json`)

Evaluation configs are used to evaluate trained policies and generate video recordings.

#### Schema

```json
{
  "name": "string",           // Job display name
  "description": "string",    // Job description
  "trainingConfig": {
    "mode": "evaluate",       // Must be "evaluate" for evaluation jobs
    "task": "string",         // Isaac Lab task name (must match training task)
    "checkpointPath": "string", // Relative path to checkpoint (e.g., "checkpoints/model_300.pt")
    "numEnvs": number,        // Number of parallel environments (1-16 recommended for video)
    "numEpisodes": number,    // Number of episodes to run
    "stepsPerEpisode": number,// Steps per episode (task-dependent)
    "rlLibrary": "string"     // RL library (must match training)
  },
  "computeConfig": {
    "numNodes": number        // Always 1 for evaluation
  }
}
```

#### Checkpoint Discovery

The pipeline supports three methods to specify the checkpoint file (in priority order):

1. **`checkpointPath`** (recommended): Relative path within the asset directory
   ```json
   "checkpointPath": "checkpoints/model_300.pt"
   ```

2. **`policyS3Uri`**: Full S3 URI to the checkpoint
   ```json
   "policyS3Uri": "s3://bucket/path/to/model.pt"
   ```

3. **Auto-discovery** (legacy): Place a `.pt` file in the same directory as the evaluation config

#### Example: Cartpole Evaluation with checkpointPath

```json
{
  "name": "Cartpole Evaluation Job",
  "description": "Evaluate a trained PPO policy for the Isaac-Cartpole-Direct-v0 environment",
  "trainingConfig": {
    "mode": "evaluate",
    "task": "Isaac-Cartpole-Direct-v0",
    "checkpointPath": "checkpoints/model_499.pt",
    "numEnvs": 4,
    "numEpisodes": 10,
    "stepsPerEpisode": 500,
    "rlLibrary": "rsl_rl"
  },
  "computeConfig": {
    "numNodes": 1
  }
}
```

#### Example: Ant Evaluation

```json
{
  "name": "Ant Evaluation Job",
  "description": "Evaluate a trained PPO policy for the Isaac-Ant-Direct-v0 environment. Videos are automatically generated and uploaded.",
  "trainingConfig": {
    "mode": "evaluate",
    "task": "Isaac-Ant-Direct-v0",
    "checkpointPath": "checkpoints/model_1000.pt",
    "numEnvs": 4,
    "numEpisodes": 5,
    "stepsPerEpisode": 900,
    "rlLibrary": "rsl_rl"
  },
  "computeConfig": {
    "numNodes": 1
  }
}
```

#### Example: Cartpole Evaluation (legacy auto-discovery)

This example uses auto-discovery - place a `.pt` file in the same directory as the config:

```json
{
  "name": "Cartpole Evaluation Job",
  "description": "Evaluate a trained PPO policy for the Isaac-Cartpole-Direct-v0 environment. Videos are automatically generated and uploaded.",
  "trainingConfig": {
    "mode": "evaluate",
    "task": "Isaac-Cartpole-Direct-v0",
    "numEnvs": 4,
    "numEpisodes": 10,
    "stepsPerEpisode": 500,
    "rlLibrary": "rsl_rl"
  },
  "computeConfig": {
    "numNodes": 1
  }
}
```

#### Evaluation Parameters Guide

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Display name for the job shown in VAMS UI and logs |
| `description` | string | Yes | Human-readable description of the evaluation job purpose |
| `trainingConfig.mode` | string | Yes | Must be `"evaluate"` for evaluation jobs |
| `trainingConfig.task` | string | Yes | Isaac Lab task/environment name. Must match the task used during training |
| `trainingConfig.checkpointPath` | string | Recommended | Relative path to the trained model checkpoint within the asset (e.g., `"checkpoints/model_499.pt"`) |
| `trainingConfig.policyS3Uri` | string | Optional | Full S3 URI to checkpoint file. Use for cross-asset or external checkpoints |
| `trainingConfig.numEnvs` | number | Yes | Number of parallel environments. **Keep low (1-16) for video recording to avoid out-of-memory errors** |
| `trainingConfig.numEpisodes` | number | Yes | Number of evaluation episodes to run. Each episode generates video frames |
| `trainingConfig.stepsPerEpisode` | number | Yes | Simulation steps per episode. Task-dependent: Cartpole ~500, Ant ~900, Humanoid ~1000 |
| `trainingConfig.rlLibrary` | string | Yes | RL library used during training. Must match the training configuration |
| `computeConfig.numNodes` | number | Yes | Always `1` for evaluation jobs |

#### Steps Per Episode by Task

| Task | Typical Steps/Episode | Notes |
|------|----------------------|-------|
| Isaac-Cartpole-Direct-v0 | 500 | Short episodes |
| Isaac-Ant-Direct-v0 | 900 | Medium episodes |
| Isaac-Humanoid-Direct-v0 | 1000 | Longer episodes |

#### Video Recording

- Videos are **automatically generated** during evaluation
- Video length = `numEpisodes × stepsPerEpisode` frames
- Videos are uploaded to S3 alongside evaluation results
- **Important**: Keep `numEnvs` low (1-16) to avoid out-of-memory errors during video recording

---

## Usage Workflow

### 1. Training a New Policy

1. Create a training config JSON file
2. Upload the config file to VAMS as an asset
3. Run the `isaaclab-training` pipeline on the config asset
4. Wait for training to complete (check job status in VAMS)
5. Download the trained policy (`.pt` file) from the output asset

### 2. Evaluating a Trained Policy

1. Create an evaluation config JSON file with `checkpointPath` pointing to the trained model
2. Upload the config file to VAMS as an asset (e.g., in an `evaluation/` subdirectory)
3. Run the `isaaclab-evaluation` pipeline on the config asset
4. View the generated evaluation video in the output asset

### Checkpoint Discovery

The pipeline supports three methods to locate the checkpoint file:

| Method | Config Field | Example | Use Case |
|--------|--------------|---------|----------|
| Relative path | `checkpointPath` | `"checkpoints/model_300.pt"` | **Recommended** - reference checkpoints within the same asset |
| Full S3 URI | `policyS3Uri` | `"s3://bucket/path/model.pt"` | Cross-asset or external checkpoints |
| Auto-discovery | (none) | Place `.pt` in config directory | Legacy - backward compatibility |

**Asset Directory Structure Example:**
```
my-training-asset/
├── training/
│   └── cartpole-training-config.json
├── evaluation/
│   └── cartpole-evaluation-config.json  ← references checkpoints/model_499.pt
└── checkpoints/
    ├── model_0.pt
    ├── model_100.pt
    └── model_499.pt
```

---

## Output Files

Pipeline outputs are organized under the job UUID for easy identification when running multiple jobs on the same asset.

### Training Output Structure

```
{asset}/
└── {job-uuid}/
    ├── checkpoints/
    │   ├── model_100.pt
    │   ├── model_200.pt
    │   └── model_*.pt
    ├── metrics.csv           # Training metrics from TensorBoard
    ├── training-config.json  # Copy of input configuration
    └── *.txt                 # Log files
```

### Evaluation Output Structure

```
{asset}/
└── {job-uuid}/
    ├── videos/
    │   └── *.mp4             # Recorded evaluation videos
    ├── metrics.csv           # Evaluation metrics
    ├── evaluation-config.json
    └── *.txt                 # Log files
```

The `{job-uuid}` prefix matches the workflow execution ID, making it easy to identify outputs from different pipeline runs.

---

## Infrastructure Configuration

### config.json Settings

Add the IsaacLab training pipeline configuration to `infra/config/config.json`:

```json
{
  "app": {
    "useGlobalVpc": {
      "enabled": true,
      "useForAllLambdas": false,
      "addVpcEndpoints": true
    },
    "pipelines": {
      "useIsaacLabTraining": {
        "enabled": true,
        "acceptNvidiaEula": true,
        "autoRegisterWithVAMS": true,
        "keepWarmInstance": false
      }
    }
  }
}
```

| Setting | Description |
|---------|-------------|
| `enabled` | Enable/disable the pipeline |
| `acceptNvidiaEula` | Accept NVIDIA Software License Agreement (required) |
| `autoRegisterWithVAMS` | Auto-register pipelines with VAMS on deployment |
| `keepWarmInstance` | Keep 1 GPU instance warm to avoid cold starts |

### Optimizing Container Pull Times

The Isaac Lab container image is ~10GB. The first AWS Batch job may take 5-10 minutes to pull the image. For faster subsequent job startup:

1. **Keep warm instances**: Set `keepWarmInstance: true` to keep instances running
2. **Pre-bake AMI**: Create a custom AMI with the container image pre-cached
3. **Larger EBS volumes**: The pipeline uses 100GB EBS volumes with Docker layer caching

Note: ECR pull-through cache is **not supported** for NVIDIA NGC (nvcr.io).

---

## Supported Tasks

| Task | Description | Complexity | Training Time (1 GPU) |
|------|-------------|------------|----------------------|
| Isaac-Cartpole-Direct-v0 | Cartpole balancing | Low | 2-5 min |
| Isaac-Ant-Direct-v0 | Quadruped locomotion | Medium | 15-30 min |
| Isaac-Humanoid-Direct-v0 | Humanoid locomotion | High | 1-2 hrs |
| Isaac-Velocity-Flat-Anymal-D-v0 | Anymal quadruped (flat) | Medium | 10-15 min |
| Isaac-Velocity-Rough-Anymal-D-v0 | Anymal quadruped (rough) | High | 1-2 hrs |

## Instance Types

| Instance | GPUs | VRAM | Cost/hr | Use Case |
|----------|------|------|---------|----------|
| g5.2xlarge | 1x A10G | 24GB | ~$1.20 | Development, evaluation |
| g5.4xlarge | 1x A10G | 24GB | ~$1.60 | Single-node training |
| g6e.2xlarge | 1x L40S | 48GB | ~$1.50 | Large-scale training |
| g6e.12xlarge | 4x L40S | 192GB | ~$6.00 | Multi-GPU training |

---

## Troubleshooting

### Out of Memory (OOM) during Evaluation

**Symptom**: Job fails with "OutOfMemoryError: Container killed due to memory usage"

**Solution**: Reduce `numEnvs` in evaluation config to 1-16. Video recording requires significant memory.

### Container fails to start

- Verify NVIDIA drivers are available on Batch compute instances
- Check CloudWatch logs: `/aws/batch/job`

### Training job times out

- Default timeout is 6 hours
- Consider using more GPUs or reducing `maxIterations`

---

## Directory Structure

```
isaacLabTraining/
├── README.md                    # This file (quick reference)
├── USER_GUIDE.md               # Detailed user guide
├── ISAACLAB_CLI_REFERENCE.md   # Isaac Lab CLI reference
├── container/                   # Docker container
│   ├── Dockerfile
│   ├── __main__.py             # Main entry point
│   ├── entrypoint.sh
│   └── utils/
│       ├── aws/s3.py           # S3 utilities
│       └── training/config.py  # Config parsing
└── lambda/                      # AWS Lambda functions
    ├── openPipeline.py         # Parse config, prepare job
    ├── executeBatchJob.py      # Submit Batch job
    ├── closePipeline.py        # Handle completion
    ├── handleError.py          # Handle failures
    └── vamsExecuteIsaacLabPipeline.py  # VAMS entry point
```

## References

- [User Guide](./USER_GUIDE.md)
- [Isaac Lab Documentation](https://isaac-sim.github.io/IsaacLab/)
- [Isaac Lab Docker Guide](https://isaac-sim.github.io/IsaacLab/main/source/deployment/docker.html)
- [AWS Batch Multi-Node Parallel Jobs](https://docs.aws.amazon.com/batch/latest/userguide/multi-node-parallel-jobs.html)
