# Coordinate Transform Pipeline

The Coordinate Transform pipeline reprojects point cloud files between coordinate reference systems (CRS). It supports E57, LAS, LAZ, and PLY input formats and can output to LAZ, LAS, E57, or PLY. The pipeline runs as an AWS Batch Fargate container with PDAL-based transformation and supports per-asset CRS configuration through VAMS metadata.

## Supported Formats

| Format | Extension | Notes                                     |
| :----- | :-------- | :---------------------------------------- |
| E57    | `.e57`    | ASTM standard for 3D imaging data         |
| LAS    | `.las`    | ASPRS LiDAR data exchange format          |
| LAZ    | `.laz`    | Compressed LAS format                     |
| PLY    | `.ply`    | Polygon File Format (point cloud variant) |

## Architecture

```mermaid
flowchart LR
    subgraph Workflow["AWS Step Functions Workflow"]
        VE[vamsExecute Lambda]
        OP[openPipeline Lambda]
        CP[constructPipeline Lambda]
        PE[pipelineEnd Lambda]
    end

    subgraph Batch["AWS Batch - Fargate"]
        CT[Coordinate Transform<br/>PDAL + pyproj]
    end

    S3In[(Asset Bucket<br/>Input Files)]
    S3Out[(Asset Bucket<br/>Output Files)]

    VE --> OP
    OP --> CP
    CP --> CT
    S3In --> CT
    CT --> S3Out
    CT --> PE
```

### Processing Flow

1. The `vamsExecute` Lambda receives the workflow event and invokes `openPipeline`.
2. `openPipeline` starts the AWS Step Functions state machine with input parameters and Amazon S3 paths.
3. `constructPipeline` merges pipeline default parameters with asset metadata overrides and builds the pipeline definition for the container.
4. AWS Batch submits an AWS Fargate job. The container downloads the input file, performs the coordinate transformation using PDAL and pyproj, and uploads output files to Amazon S3.
5. The container sends a task token callback to AWS Step Functions on completion.
6. `pipelineEnd` finalizes the workflow execution.

### Container Image Build

The container image is built via AWS CodeBuild during CDK deployment. When `useCodeBuild` is enabled, CDK uploads the container source to Amazon S3, and a custom resource triggers CodeBuild to build and push the image to Amazon ECR. The container includes PDAL, pyproj, and the coordinate transformation logic.

## Configuration

Enable this pipeline in `infra/config/config.json`:

```json
{
    "app": {
        "pipelines": {
            "useConversionCoordinateTransform": {
                "enabled": true,
                "useCodeBuild": true,
                "autoRegisterWithVAMS": true,
                "autoRegisterAutoTriggerOnFileUpload": false
            }
        }
    }
}
```

### Configuration Options

| Option                                | Default | Description                                                                                                                             |
| :------------------------------------ | :------ | :-------------------------------------------------------------------------------------------------------------------------------------- |
| `enabled`                             | `false` | Deploy the coordinate transform pipeline infrastructure. Enables the global VPC.                                                        |
| `useCodeBuild`                        | `false` | Build the container image via AWS CodeBuild during deployment. When enabled, CodeBuild runs outside the VPC to pull public base images. |
| `autoRegisterWithVAMS`                | `true`  | Automatically register the pipeline and workflow in the global VAMS database during CDK deployment.                                     |
| `autoRegisterAutoTriggerOnFileUpload` | `false` | Automatically trigger the pipeline when E57, LAS, LAZ, or PLY files are uploaded. Requires `autoRegisterWithVAMS` to be enabled.        |

## Input Parameters

The pipeline accepts transform parameters that control the coordinate reprojection. Parameters can be provided in two ways:

1. **Pipeline defaults** -- configured during pipeline registration (the `inputParameters` field in the pipeline definition).
2. **Asset metadata overrides** -- set as metadata key-value pairs on individual assets. Metadata values override pipeline defaults.

### Parameter Reference

| Parameter              | Type    | Required | Default | Description                                                           |
| :--------------------- | :------ | :------- | :------ | :-------------------------------------------------------------------- |
| `sourceCrs`            | string  | Yes      | --      | Source coordinate reference system (EPSG code, WKT, or PROJ string)   |
| `targetCrs`            | string  | Yes      | --      | Target coordinate reference system (EPSG code, WKT, or PROJ string)   |
| `outputFormats`        | array   | No       | `[laz]` | Output format(s): `laz`, `las`, `e57`, `ply`                          |
| `sourceScaleFactor`    | number  | No       | `1.0`   | Scale factor for source grid                                          |
| `targetScaleFactor`    | number  | No       | `1.0`   | Scale factor for target grid                                          |
| `applyScaleCorrection` | boolean | No       | `true`  | Whether to apply scale factor correction during transformation        |
| `combinedScaleFactor`  | number  | No       | --      | Override: apply a single combined scale factor directly               |
| `chunkSize`            | number  | No       | 1000000 | Number of points per processing chunk                                 |
| `enforceSourceCrs`     | boolean | No       | `true`  | Block processing if detected CRS does not match configured source CRS |
| `onMismatch`           | string  | No       | `warn`  | Action on CRS mismatch: `error`, `warn`, or `skip`                    |
| `compressLaz`          | boolean | No       | `true`  | Whether to compress LAZ output                                        |

### Supported CRS Formats

The `sourceCrs` and `targetCrs` parameters accept the following coordinate reference system formats. The pipeline resolves them in priority order:

| Format                | Syntax                | Example                                               | Notes                                                               |
| :-------------------- | :-------------------- | :---------------------------------------------------- | :------------------------------------------------------------------ |
| Custom named grid     | Grid name string      | `local+sizewell`                                      | Matched against `custom_grids` in pipeline config. Source CRS only. |
| EPSG code             | `EPSG:<numeric_code>` | `EPSG:27700`, `EPSG:4326`                             | Standard EPSG registry codes                                        |
| PROJ string           | Starts with `+proj`   | `+proj=lcc +lat_1=33 +lat_2=45 +datum=NAD83 +units=m` | Full PROJ.4 projection definition                                   |
| Well-Known Text (WKT) | OGC WKT string        | `GEOGCS["WGS 84",DATUM["WGS_1984",...]]`              | Fallback — any string not matching the above is parsed as WKT       |

:::note[Resolution Order]
The pipeline checks CRS strings in this order: custom named grid → EPSG code → PROJ string → WKT. The first successful match is used.
:::

#### Custom Named Grids

Custom grids allow you to define local or site-specific coordinate systems using a friendly name. Each grid maps a name to a PROJ string definition. Custom grids are configured in the pipeline's `custom_grids` parameter:

```json
{
    "custom_grids": [
        {
            "name": "local+sizewell",
            "definition": "+proj=tmerc +lat_0=52.2 +lon_0=1.6 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
        }
    ]
}
```

Custom grid names are only supported for `sourceCrs`. For `targetCrs`, use EPSG codes, PROJ strings, or WKT.

### Example Input Parameters

```json
{
    "sourceCrs": "EPSG:27700",
    "targetCrs": "EPSG:4326",
    "outputFormats": ["laz", "e57"],
    "applyScaleCorrection": true
}
```

### Projection Scale Factors and Local Scale Factor (LSF)

Many national mapping projections — such as OSGB36 (`EPSG:27700`) which uses a Secant Transverse Mercator — have a local scale factor (LSF) that varies by position. In the case of OSGB36, the central meridian has a scale factor of 0.9996012717 and the two secant lines (approximately 180 km apart) have a scale factor of exactly 1.0. Between and beyond these lines the LSF transitions from less than 1.0 to greater than 1.0 depending on distance from the central meridian.

**The pipeline handles this automatically.** When you specify a well-defined CRS such as `EPSG:27700`, the underlying pyproj transformation applies the full rigorous inverse projection mathematics. This correctly accounts for the position-dependent LSF at every point in the cloud regardless of its grid location. You do **not** need to look up the local scale factor (for example, from NRG LSF or Grid InQuest) and supply it manually.

```json
{
    "sourceCrs": "EPSG:27700",
    "targetCrs": "EPSG:4326"
}
```

This is sufficient to correctly transform point clouds captured anywhere in the UK — the varying scale factor across the OSGB36 grid is handled by the CRS definition itself.

:::tip[When to use sourceScaleFactor / targetScaleFactor]
The `sourceScaleFactor` and `targetScaleFactor` parameters apply a **uniform** post-transformation multiplier to X and Y coordinates. They are intended for compensating additional scale offsets in local site grids or scan data that are not part of the standard CRS definition — not for handling the inherent projection scale variation of well-defined coordinate systems like OSGB36 or UTM zones.
:::

## Asset Metadata Overrides

The coordinate transform pipeline supports per-asset configuration via VAMS metadata fields. When a workflow executes, the asset's metadata is passed to the pipeline and any recognized keys override the pipeline's default parameters.

This allows you to set different source and target CRS values for individual assets without modifying the pipeline registration.

### How It Works

1. Set metadata on an asset in VAMS (via the web UI, API, or CLI) using recognized key names.
2. When the pipeline runs for that asset, the `constructPipeline` Lambda reads the asset metadata from the workflow event.
3. Recognized metadata keys are merged into the transform parameters, with metadata values taking priority over pipeline defaults.

### Recognized Metadata Keys

The following metadata key names are recognized (case-insensitive):

| Metadata Key           | Maps To                | Notes                                           |
| :--------------------- | :--------------------- | :---------------------------------------------- |
| `sourceCrs`            | `sourceCrs`            | EPSG code, WKT, or PROJ string                  |
| `targetCrs`            | `targetCrs`            | EPSG code, WKT, or PROJ string                  |
| `outputFormats`        | `outputFormats`        | Comma-separated string (for example, `laz,e57`) |
| `sourceScaleFactor`    | `sourceScaleFactor`    | Numeric value                                   |
| `targetScaleFactor`    | `targetScaleFactor`    | Numeric value                                   |
| `applyScaleCorrection` | `applyScaleCorrection` | `true` or `false`                               |
| `combinedScaleFactor`  | `combinedScaleFactor`  | Numeric value                                   |
| `chunkSize`            | `chunkSize`            | Numeric value                                   |
| `enforceSourceCrs`     | `enforceSourceCrs`     | `true` or `false`                               |
| `onMismatch`           | `onMismatch`           | `error`, `warn`, or `skip`                      |
| `compressLaz`          | `compressLaz`          | `true` or `false`                               |

:::tip[Per-Asset CRS Configuration]
Set `sourceCrs` and `targetCrs` as metadata on each asset to define the correct coordinate systems for that specific scan. This is particularly useful when a database contains point clouds from multiple survey sites with different native coordinate systems.
:::

### Example Workflow

1. Upload a LAS file captured in British National Grid:
    - Set asset metadata: `sourceCrs` = `EPSG:27700`
2. Configure the pipeline default `targetCrs` as `EPSG:4326` (WGS84).
3. When the workflow triggers, the pipeline reads `sourceCrs` from asset metadata and `targetCrs` from pipeline defaults.
4. The output LAZ file is reprojected to WGS84.

## Prerequisites

### VPC with Private Subnets

This pipeline runs on AWS Batch with AWS Fargate compute. Enabling it automatically sets `app.useGlobalVpc.enabled` to `true`. The VPC builder creates the required VPC endpoints for AWS Batch, Amazon ECR, and Amazon ECR Docker.

### CodeBuild Internet Access

When `useCodeBuild` is enabled, the CodeBuild project runs **outside the VPC** to pull public base images from Amazon ECR Public during container builds. It accesses Amazon ECR and Amazon S3 via IAM credentials.

## Infrastructure Components

The following AWS resources are created when this pipeline is enabled:

| Resource                     | Service            | Purpose                                                                           |
| :--------------------------- | :----------------- | :-------------------------------------------------------------------------------- |
| Fargate Compute Environment  | AWS Batch          | Serverless container execution                                                    |
| Job Queue                    | AWS Batch          | Job scheduling and prioritization                                                 |
| Job Definition               | AWS Batch          | Container configuration (60 GiB ephemeral storage)                                |
| Container Repository         | Amazon ECR         | Stores the coordinate transform container image                                   |
| CodeBuild Project            | AWS CodeBuild      | Builds and pushes the container image to Amazon ECR                               |
| Step Functions State Machine | AWS Step Functions | Workflow orchestration with 4-hour timeout                                        |
| Lambda Functions (4)         | AWS Lambda         | Pipeline coordination (vamsExecute, openPipeline, constructPipeline, pipelineEnd) |

## Troubleshooting

### Container Fails to Start

If the AWS Batch job fails immediately, check that the CodeBuild project completed successfully:

```bash
aws codebuild list-builds-for-project \
    --project-name <CodeBuild-project-name> \
    --sort-order DESCENDING \
    --region <region>
```

Verify the build status is `SUCCEEDED` and the Amazon ECR repository contains the `latest` tag.

### Missing sourceCrs or targetCrs

The container requires both `sourceCrs` and `targetCrs` to be present in the final merged parameters. If neither the pipeline defaults nor the asset metadata provide these values, the pipeline fails with:

```
inputParameters must include 'sourceCrs' and 'targetCrs'
```

Ensure at least one source provides both CRS values.

### CRS Mismatch Errors

If `enforceSourceCrs` is `true` (default) and the file's embedded CRS does not match the configured `sourceCrs`, the pipeline behavior depends on the `onMismatch` setting:

-   `error` -- Pipeline fails immediately
-   `warn` -- Pipeline continues with a warning in the output report
-   `skip` -- File is skipped without processing

## Third-Party Library Licenses

The container performs LAS/LAZ reading and writing with the open-source [laspy](https://github.com/laspy/laspy) library, which is distributed under a 3-clause BSD license (BSD-3-Clause). This is a standard permissive license — its terms are equivalent to the MIT and Apache-2.0 licenses used elsewhere in VAMS (use, modification, and redistribution with attribution and no warranty), plus the standard third clause prohibiting use of the copyright holders' names to endorse derived products. It carries no copyleft obligations. The pipeline's other core libraries — PDAL (BSD-3-Clause), pyproj (MIT), and NumPy (BSD-3-Clause) — are likewise permissively licensed. See [Notices](../additional/notices.md) for the full third-party software notice.

## Related Resources

-   [Pipeline System Overview](overview.md)
-   [Potree Point Cloud Viewer Pipeline](potree-viewer.md) -- converts point clouds to Potree octree format for web viewing
-   [Configuration Reference](../deployment/configuration-reference.md) -- all pipeline configuration options
