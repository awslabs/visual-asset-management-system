# Preview 3D Thumbnail Pipeline

The Preview 3D Thumbnail pipeline generates animated GIF or static image preview thumbnails from 3D files. It uses CPU-based headless rendering via PyVista/VTK with Xvfb inside an AWS Batch Fargate container.

## Contents

-   [Supported Formats](#supported-formats)
-   [Architecture](#architecture)
-   [Output](#output)
-   [Input Parameters](#input-parameters)
-   [Configuration](#configuration)
-   [Docker Notes](#docker-notes)
    -   [Prerequisites](#prerequisites)
    -   [Building the Container Image](#building-the-container-image)
    -   [Running the Container Image Locally](#running-the-container-image-locally)
-   [Rendering Details](#rendering-details)
-   [References](#references)

## Supported Formats

| Category    | Extensions                                | Handler              |
| :---------- | :---------------------------------------- | :------------------- |
| Mesh        | .ply, .stl, .obj, .glb, .gltf, .fbx, .drc | trimesh              |
| Point Cloud | .las, .laz, .e57, .ptx, .pcd, .fls, .fws  | laspy, pye57, open3d |
| CAD         | .stp, .step                               | cadquery             |
| USD         | .usd, .usda, .usdc, .usdz                 | usd-core (pxr)       |

## Architecture

```
S3 Event / API Trigger
    --> Open Pipeline Lambda (starts Step Functions state machine)
        --> Construct Pipeline Lambda (builds stage definitions)
            --> AWS Batch Fargate Job (runs Docker container)
                1. Download input file from S3
                2. Detect format, load with appropriate handler
                3. Normalize up-axis to Y-up
                4. Render 36-frame rotating preview (or single static frame fallback)
                5. Save as GIF with size optimization (JPEG fallback for single frame)
                6. Upload preview file to S3
            --> Pipeline End Lambda (finalizes execution, sends SFN callback)
```

## Output

The pipeline produces a single preview file alongside the original asset:

-   **Animated GIF** (`<filename>.previewFile.gif`): 36-frame rotation around the Y-axis at 800x600 resolution. If the GIF exceeds the size limit, frames are reduced by half iteratively. Maximum output size is approximately 5 MB.
-   **Static JPEG** (`<filename>.previewFile.jpg`): Single isometric-view frame. Used as a fallback when the rotating render fails or when only one frame is produced.

## Input Parameters

Input parameters are passed as a JSON string via the `inputParameters` field of the pipeline execution payload. All parameters are optional.

| Parameter                       | Type    | Default | Description                                                                                                                                                |
| :------------------------------ | :------ | :------ | :--------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `overwriteExistingPreviewFiles` | boolean | `false` | When `false`, the pipeline will fail with an error if a preview file already exists for the input file. Set to `true` to overwrite existing preview files. |

### Example

```json
{
    "inputParameters": "{\"overwriteExistingPreviewFiles\": true}"
}
```

When triggered via the VAMS workflow system, input parameters can be set on the workflow execution or on the pipeline definition.

## Configuration

Enable the pipeline in `infra/config/config.json`:

```json
{
    "app": {
        "pipelines": {
            "usePreview3dThumbnail": {
                "enabled": true,
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": true
            }
        }
    }
}
```

| Setting                               | Description                                                                                  |
| :------------------------------------ | :------------------------------------------------------------------------------------------- |
| `enabled`                             | Enables the pipeline infrastructure (Batch, Step Functions, Lambdas)                         |
| `autoRegisterWithVAMS`                | Automatically registers the pipeline and workflow in the VAMS database during CDK deployment |
| `autoRegisterAutoTriggerOnFileUpload` | When auto-registered, configures the workflow to trigger automatically on file upload        |

Enabling this pipeline requires the Global VPC to be enabled (`useGlobalVpc.enabled: true`). The CDK configuration validation enforces this automatically.

## Docker Notes

### Prerequisites

-   Python >=3.12
-   Docker (CLI and/or Desktop)

### Building the Container Image

The container image is built and uploaded automatically during CDK deployment. To build locally for testing:

```bash
cd backendPipelines/preview/3dThumbnail/container
docker build -t preview-3d-thumbnail:latest .
```

### Running the Container Image Locally

The container supports a `localTest` mode for testing without AWS dependencies. Mount input and output directories as Docker volumes.

**Basic usage:**

```bash
docker run --rm \
    -v /path/to/input:/data/input \
    -v /path/to/output:/data/output \
    preview-3d-thumbnail:latest \
    localTest PREVIEW_3D_THUMBNAIL "/data/input/model.glb"
```

**With input parameters (e.g., overwrite existing):**

```bash
docker run --rm \
    -v /path/to/input:/data/input \
    -v /path/to/output:/data/output \
    preview-3d-thumbnail:latest \
    localTest PREVIEW_3D_THUMBNAIL "/data/input/model.glb" \
    '{"overwriteExistingPreviewFiles":true}'
```

**Auto-detect first supported file in input directory:**

```bash
docker run --rm \
    -v /path/to/input:/data/input \
    -v /path/to/output:/data/output \
    preview-3d-thumbnail:latest \
    localTest PREVIEW_3D_THUMBNAIL
```

The output preview file will be written to the mounted output directory.

**Arguments:**

| Position | Value                  | Description                                                 |
| :------- | :--------------------- | :---------------------------------------------------------- |
| 1        | `localTest`            | Enables local testing mode (no S3/SFN)                      |
| 2        | `PREVIEW_3D_THUMBNAIL` | Pipeline stage type                                         |
| 3        | (optional) file path   | Path to input file inside the container                     |
| 4        | (optional) JSON string | Input parameters JSON (e.g., overwriteExistingPreviewFiles) |

## Rendering Details

### Camera Framing

The renderer uses percentile-based camera framing (2nd-98th percentile) instead of the full bounding box. This ensures that sparse scenes such as single-position LiDAR scans with distant outlier points are framed tightly around the dense content rather than zoomed out to include outliers.

### Up-Axis Normalization

All data is normalized to Y-up before rendering:

-   **Known Z-up formats** (LAS, LAZ, E57, PTX, PCD, FLS, FWS, STL): Rotated to Y-up automatically
-   **Known Y-up formats** (GLB, GLTF, STP, STEP, USD): No rotation
-   **Variable formats** (OBJ, FBX, PLY, DRC): Bounding-box heuristic determines up-axis
-   **USD files**: Up-axis read from stage metadata via `UsdGeom.GetStageUpAxis()`

### Point Cloud Handling

Point clouds are downsampled to a maximum of 20 million points for rendering performance. Downsampling uses random subsampling with a fixed seed for reproducibility. Points are rendered as colored spheres using per-point RGB colors when available, or colored by elevation (Y-axis) using the viridis colormap.

### USD Texture Support

For USD files with material bindings, the pipeline traverses the UsdShade material graph to extract diffuse textures:

1. Extracts UV coordinates from mesh primvars (supports common names: `st`, `UVMap`, `st0`, `texCoord_0`, `Texture_uv`, `uv`)
2. Traverses `MaterialBindingAPI` -> `UsdPreviewSurface` -> `diffuseColor` -> `UsdUVTexture` to find texture file paths
3. Loads textures from the filesystem or from inside USDZ archives (zip extraction)
4. Bakes texture colors to per-vertex RGB data for rendering

### File Size Limits

The maximum input file size is 100 GB. Files exceeding this limit are rejected before download with a descriptive error message. The Fargate container has 200 GiB of ephemeral storage to accommodate large input files plus working space.

## References

-   [PyVista](https://docs.pyvista.org/) - 3D plotting and mesh analysis
-   [VTK](https://vtk.org/) - Visualization Toolkit
-   [trimesh](https://trimesh.org/) - Mesh loading and processing
-   [laspy](https://laspy.readthedocs.io/) - LAS/LAZ point cloud format
-   [pye57](https://github.com/davidcaron/pye57) - E57 point cloud format
-   [OpenUSD](https://openusd.org/) - Universal Scene Description
-   [beersandrew/usd-thumbnail-generator](https://github.com/beersandrew/usd-thumbnail-generator) - Reference implementation for USD thumbnail generation
