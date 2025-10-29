# Splat Toolbox Pipeline

The Splat Toolbox Pipeline enables 3D Gaussian Splatting reconstruction from images or videos within VAMS. This pipeline automatically pulls the latest container code from the [AWS Guidance for Open Source 3D Reconstruction Toolbox for Gaussian Splats](https://github.com/aws-solutions-library-samples/guidance-for-open-source-3d-reconstruction-toolbox-for-gaussian-splats-on-aws) repository.

## Pipeline Components

### Container (`container/`)

-   **Auto-synced from upstream repository** during CDK deployment
-   Contains the complete 3D Gaussian Splatting pipeline implementation
-   Uses unmodified upstream Dockerfile with VAMS-specific entrypoint

### Lambda Functions (`lambda/`)

-   **`constructPipeline.py`** - Transforms input data for AWS Batch execution
-   **`openPipeline.py`** - Initiates pipeline execution from S3 events
-   **`pipelineEnd.py`** - Handles pipeline completion and cleanup
-   **`vamsExecuteSplatToolboxPipeline.py`** - VAMS API integration for manual execution
-   **`sqsExecuteSplatToolboxPipeline.py`** - SQS-triggered pipeline execution

### CDK Infrastructure (`../../infra/lib/nestedStacks/pipelines/3dRecon/splatToolbox/`)

-   **`splatToolboxBuilder-nestedStack.ts`** - Main CDK stack definition
-   **`constructs/splatToolbox-construct.ts`** - Core pipeline infrastructure
-   **`lambdaBuilder/splatToolboxFunctions.ts`** - Lambda function definitions

## Pipeline Process

1. **Input Processing**

    - Accepts `.zip` (images), `.mp4`, `.mov` (videos)
    - Extracts images from videos if needed
    - Validates input format and quality

2. **Structure from Motion (SfM)**

    - Uses COLMAP or GLOMAP for camera pose estimation
    - Generates sparse 3D point cloud
    - Estimates camera intrinsics and extrinsics

3. **3D Gaussian Splatting**

    - Uses NerfStudio's splatfacto implementation
    - Trains 3D Gaussian representation
    - Supports GPU acceleration on AWS Batch

4. **Output Generation**
    - Generates `.ply` files for 3D viewing
    - Creates compressed `.spz` format for web viewing
    - Uploads results to S3

## Configuration Parameters

Key pipeline parameters configurable via VAMS:

-   `MODEL` - Splatting model type (splatfacto, splatfacto-big, etc.)
-   `MAX_STEPS` - Training iterations
-   `SFM_SOFTWARE_NAME` - COLMAP or GLOMAP
-   `REMOVE_BACKGROUND` - Background removal option
-   `GENERATE_SPLAT` - Enable splat file generation

## AWS Resources

-   **AWS Batch** - GPU compute environment for training
-   **Step Functions** - Pipeline orchestration
-   **Lambda** - Event handling and coordination
-   **S3** - Input/output storage
-   **ECR** - Container image storage

## Repository Sync

The pipeline automatically syncs the latest container code from the upstream repository during CDK deployment. This ensures:

-   Always uses the latest 3D reconstruction algorithms
-   Maintains compatibility with upstream improvements
-   Preserves VAMS-specific integration (entrypoint.sh)

## Usage

1. Upload images (.zip) or video (.mp4/.mov) to VAMS
2. Pipeline triggers automatically via S3 events
3. Monitor progress in VAMS pipeline interface
4. Download generated 3D models from pipeline outputs

## Requirements

-   GPU-enabled AWS Batch compute environment
-   Sufficient storage for intermediate processing files
-   Network access for model downloads during container build
