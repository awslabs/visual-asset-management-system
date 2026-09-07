# Splat Toolbox Pipeline

The Splat Toolbox Pipeline enables 3D Gaussian Splatting reconstruction from images or videos within VAMS. This pipeline syncs its container code from a pinned commit of the [AWS Guidance for Open Source 3D Reconstruction Toolbox for Gaussian Splats](https://github.com/aws-solutions-library-samples/guidance-for-open-source-3d-reconstruction-toolbox-for-gaussian-splats-on-aws) repository.

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

Key pipeline parameters configurable via VAMS. A parameter takes effect only when it is a key of the
container's `src/config.json`, which the upstream sync provides; a key absent from that file is
reported on the container's log and otherwise ignored.

-   `MODEL` - Splatting model type (splatfacto, splatfacto-big, etc.)
-   `MAX_STEPS` - Training iterations
-   `RECON_SOFTWARE_NAME` - Reconstruction software (colmap, glomap, hloc, map_anything)
-   `RUN_RECON` - Recover camera poses from the input capture before training
-   `RUN_TRAIN` - Train the splat model
-   `SPHERICAL_CAMERA` - Treat the input as a 360/spherical capture
-   `REMOVE_BACKGROUND` - Background removal option
-   `CROP_OUTPUT_BOUNDS` - Trim the exported splat to the reconstructed scene bounds
-   `CROP_MODE` - How the crop bounds are computed (rigid_body, environment)
-   `ENABLE_SPZ` - Export the compressed SPZ splat format
-   `ENABLE_SOG` - Export the SOG splat format

`RUN_RECON`, `RUN_TRAIN`, `CROP_OUTPUT_BOUNDS` and `CROP_MODE` are declared as template tags on both
shipped templates, so each is a field on the execute form. The rest are set by editing a template's
configuration body.

## AWS Resources

-   **AWS Batch** - GPU compute environment for training
-   **Step Functions** - Pipeline orchestration
-   **Lambda** - Event handling and coordination
-   **S3** - Input/output storage
-   **ECR** - Container image storage

## Repository Sync

The pipeline syncs its container code from the upstream repository during CDK deployment, at the commit
recorded as `GITHUB_REPO_COMMIT_HASH` in `splatToolbox-construct.ts`. The sync verifies the checked-out
commit matches that value, so the sources are reproducible across rebuilds and moving the pipeline to a
newer upstream revision is a change to that constant. This:

-   Fixes which 3D reconstruction algorithms the image is built from
-   Preserves VAMS-specific integration (`__main__.py` plus the `vams_utils` package)

### Third-party source pinning

The pinned commit is upstream's own Dockerfile, and it fetches further third-party sources while the
image builds. Most are pinned — cloned and then reset to a recorded commit, or fetched at a version tag —
but some resolve to whatever the source serves at build time, so two builds of the same VAMS commit can
produce different images.

The Dockerfile is gitignored and rewritten on every synth, so that set is not visible in a diff. It is
recorded instead in `RECORDED_UNPINNED_SOURCES`
(`infra/lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/dockerfilePinAudit.ts`), and the sync
compares the synced Dockerfile against that record and fails the synth when it changes in either
direction — a new unrecorded unpinned source, or a recorded one upstream has since pinned. The audit runs
for any deployment that instantiates this pipeline; a deployment with the pipeline disabled never syncs
and never audits.

## Usage

1. Upload images (.zip) or video (.mp4/.mov) to VAMS
2. Pipeline triggers automatically via S3 events
3. Monitor progress in VAMS pipeline interface
4. Download generated 3D models from pipeline outputs

## Requirements

-   GPU-enabled AWS Batch compute environment
-   Sufficient storage for intermediate processing files
-   Network access for model downloads during container build
