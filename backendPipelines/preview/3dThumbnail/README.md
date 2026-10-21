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

| Category       | Extensions                                                             | Handler                                          |
| :------------- | :--------------------------------------------------------------------- | :----------------------------------------------- |
| Mesh           | .ply, .stl, .obj, .glb, .gltf, .drc, .off                              | trimesh                                          |
| Mesh (FBX)     | .fbx                                                                   | open3d (Assimp)                                  |
| Mesh (XML)     | .3mf, .dae                                                             | built-in, hardened XML parsing                   |
| Point Cloud    | .las, .laz, .e57, .ptx, .pts, .pcd, vertex-only .ply                   | laspy, pye57, open3d                             |
| CAD            | .stp, .step                                                            | cadquery                                         |
| USD            | .usd, .usda, .usdc, .usdz                                              | usd-core (pxr)                                   |
| Gaussian splat | .ply (3DGS), .compressed.ply, .spz, .sog, .splat, .ksplat, .lcc, .lcc2 | numpy; PlayCanvas splat-transform for conversion |

FARO scans (`.fls`, `.fws`) are not supported: reading them needs the proprietary FARO SDK. Convert them to E57 or LAS first.

## Architecture

```
S3 Event / API Trigger
    --> Open Pipeline Lambda (starts Step Functions state machine)
        --> Construct Pipeline Lambda (builds stage definitions)
            --> AWS Batch Fargate Job (runs Docker container)
                1. Download the input and the files it references from the same asset
                2. Detect format, load with appropriate handler
                3. Normalize up-axis to Y-up
                4. Render 36-frame rotating preview (or the single still view)
                5. Save in the requested format (auto, gif, jpg, png), keeping a GIF under 5 MiB
                6. Upload preview file to S3
            --> Pipeline End Lambda (finalizes execution, sends SFN callback)
```

## Output

The pipeline produces a single preview file alongside the original asset:

-   **Animated GIF** (`<filename>.previewFile.gif`): 36-frame orbit around the Y-axis at 800x600 with a transparent background. If the GIF exceeds the 5 MB size limit, it is written again with 18, 12 and then 8 frames, then with those 8 frames at 75%, 50% and 37.5% resolution, and finally as a single PNG (then JPEG) of the still view.
-   **Still PNG or JPEG** (`<filename>.previewFile.png` / `.jpg`): one front three-quarter view. Written when `outputType` asks for it, when `auto` gets a single frame, or when the orbit render fails.

## Input Parameters

Input parameters are passed as a JSON string via the `inputParameters` field of the pipeline execution payload. All parameters are optional.

| Parameter                       | Type    | Default | Description                                                                                                                                                                          |
| :------------------------------ | :------ | :------ | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `overwriteExistingPreviewFiles` | boolean | `true`  | When `true`, a preview is generated even if one already exists for the input file. When `false`, a file that already has a preview is skipped and the run completes without writing. |
| `outputType`                    | enum    | `auto`  | `auto` writes an animated GIF when the renderer produces several frames and a JPEG otherwise; `gif`, `jpg` and `png` write that format for every input.                              |

### Example

```json
{
    "inputParameters": "{\"overwriteExistingPreviewFiles\": false, \"outputType\": \"png\"}"
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

A mesh (glTF, OBJ, FBX, STL, PLY mesh, STEP, USD) is framed on its full bounding box. A point cloud uses percentile-based camera framing (2nd-98th percentile) instead of the full bounding box, so sparse scenes such as single-position LiDAR scans with distant outlier points are framed tightly around the dense content rather than zoomed out to include outliers. A scene that holds both meshes and points renders all of them and is framed as a mesh.

### Still View

A still preview (`outputType` `jpg` or `png`, the static fallback, and the size-limit fallback of an animated preview) shows the front three-quarter view: 45° around the orbit and 25° above it.

### Referenced Files

Before loading, `preview_pipeline/dependencies.py` fetches the files the input references from the input's own asset (glTF buffers and images; USD sublayers, references, payloads and asset-valued attributes, followed through each fetched layer; OBJ `mtllib` files and their `map_*`/`bump`/`disp` textures; FBX texture file names; an `.lcc` scene's `data.bin`, `index.bin`, `shcoef.bin` and `environment.bin`; the chunk files of the one level of detail an `.lcc2` scene is read at). The input and every fetched file land at their asset-relative paths under one local root, so relative references resolve locally as they do in the asset.

Every reference is untrusted input. It is accepted only as a relative path that, normalised against the referring file's directory, stays inside the asset prefix (the key up to the asset id, the run's output files folder for a file an earlier step wrote, else the input's own directory); absolute paths, `..` escapes, URI schemes, control characters and over-long values are rejected. The fetch is bounded: 2,000 files, 50 GiB (HeadObject before each download, checked against free disk with 1 GiB headroom) and a recursion depth of 32. A rejected, missing or over-budget reference is logged and skipped.

### Up-Axis Normalization

All data is normalized to Y-up before rendering, from what the file or its format declares:

-   **Z-up formats** (LAS, LAZ, E57, PTX, PTS, PCD): rotated to Y-up
-   **Y-up formats** (GLB, GLTF; STEP, rendered as authored since it carries no axis): no rotation
-   **Formats with axis metadata**: USD (`UsdGeom.GetStageUpAxis()`), COLLADA (`<up_axis>`), 3MF (Z-up by specification) and FBX (`GlobalSettings` `UpAxis`) are converted by what they declare
-   **Formats without a convention** (OBJ, PLY, DRC, STL, OFF, and FBX without `UpAxis`): rendered as authored. No bounding-box guess is made: it turned Y-up models that are long in depth (a car, a ship) onto their side.

### XML and Archive Inputs

3MF and COLLADA are XML. They are parsed with the standard library's expat parser with document type declarations, entity declarations and external entity references refused (ARCC BSC12), so a crafted file can neither expand entities nor read local files or URLs. A 3MF reads its build items, components (with their transforms) and `basematerials`/`colorgroup` colours; COLLADA reads the visual scene's node transforms, `triangles`, `polylist` and `polygons`, and each bound material's diffuse colour. Components and node instances are expanded until the placed geometry passes the run's triangle budget (or three vertices per triangle of it) or the walk has visited 1,000,000 nodes; the remaining placements are skipped with one warning, and each object or geometry is parsed once however often it is placed. A zip-based input (`.3mf`, `.sog`, and a texture read out of a `.usdz`) is checked from its central directory before anything is decompressed: the uncompressed total must fit the free disk and no member may be compressed more than 200:1 above 64 MiB.

`.drc` files keep their vertex colours. `.pts` (Leica text) is streamed from Amazon S3 like PTX.

### FBX Handling

An FBX file (ASCII or binary, FBX 2011 / 7.1 and later) is read with Open3D's `read_triangle_model`, which uses Assimp: quad and n-gon faces are triangulated, every node's transform is applied, and the meshes are merged into one. A mesh keeps its vertex colours, or else takes its material's base colour; a scene in which no mesh has either (every mesh on Assimp's default material) is drawn in the default mesh colour, and a mesh without a colour beside coloured ones gets the default mesh colour. Textures are not sampled. Assimp places a model once under every parent it is connected to and copies a geometry for every model that uses it, all before it returns, so the placements are first counted from the file's `Objects` and `Connections` (`format_handlers/fbx_scene.py`, one pass): a file that places more than 1,000,000 models (a cycle counts as more), or whose placements add up to more triangles than the larger of the run's triangle budget and the triangles the file stores (or more vertices than three per triangle of the budget), fails before it is read. The count reads the file the way Assimp 5.4.2, the version Open3D 0.19 bundles, reads it (every connection type but `"PP"` links a model, comments and strings hold no structure, a zero word inside a binary record list is skipped), so an Open3D upgrade needs `fbx_scene.py` checked against the new Assimp's FBX reader. The same pass measures how deeply the file nests: the reader descends into each scope (ASCII `{ }` block) or node-record list (binary) as it opens, so a file whose scopes or records nest deeper than 256 levels — far more than any model an exporter produces — is refused before it is read.

### Point Cloud Handling

Every point cloud reader keeps at most the run's point budget (see [Compute and Limits](#compute-and-limits)), sampled uniformly at random with a fixed seed while it reads, so peak memory is one read chunk plus the sample whatever the file's size:

-   **LAS/LAZ**: read in 2-million-point chunks and sampled with a uniform stride.
-   **E57**: every scan is read through the libe57 reader in 1-million-point buffers, its pose (rotation and translation) is applied so all scans share the file's frame, points flagged invalid are dropped, and colours are scaled from the scan's declared colour limits to 8 bits.
-   **PTX**: every scan's ten header lines are parsed and its 4x4 transform is applied to its points; `0 0 0` (no return) is dropped. In S3 mode a PTX file is streamed from Amazon S3 and parsed in blocks of one million lines, with no local copy.
-   **PCD**: read with Open3D, then sampled.

Points are rendered as colored spheres using per-point RGB colors when available, or colored by elevation (Y-axis) using the viridis colormap.

### CAD (STEP) Handling

A STEP file is read through XCAF (`STEPCAFControl_Reader`), so assemblies keep their structure, every component is placed by the transforms of all assembly levels above it, and a colour set on a part, a component or a face colours that face's triangles (the nearest colour wins; uncoloured faces in a coloured file get the default mesh colour). Every face is tessellated, including free surfaces beside solids. The linear deflection is 0.2% of the model's bounding-box diagonal and the angular deflection 0.35 rad, so detail does not depend on the model's units. STEP carries no up-axis metadata, so the model is rendered as authored. A file XCAF cannot read falls back to `STEPControl_Reader` (geometry only). Before either reader transfers the file, its component placements are counted from its `NEXT_ASSEMBLY_USAGE_OCCURRENCE` entities, and a file that places more than 1,000,000 components fails, since OCCT's transfer visits every placement. Within that bound, parts stop being placed once the tessellated triangles pass the run's triangle budget.

### Gaussian Splat Handling

A `.ply` is routed by its header: a vertex element with `f_dc_0`, `opacity`, `scale_*` and `rot_*` is a 3D Gaussian Splatting scene, a PLY with faces is a mesh, and a vertex-only PLY is a point cloud (sampled to the point budget). A 3DGS PLY is read through a memory map; `.splat` (32-byte records) is decoded in numpy; `.spz`, `.sog`, `.ksplat`, `.lcc`, `.lcc2` and `.compressed.ply` are converted to a 3DGS PLY on the CPU with the PlayCanvas [`splat-transform`](https://github.com/playcanvas/splat-transform) CLI (MIT), pinned by `container/splat-transform/package-lock.json` and installed with `npm ci` on a pinned, SHA-256-verified Node.js 22 release. An XGRIDS scene is read at one level of detail: the finest whose splat count is at most twice the splat budget, else the coarsest. The counts come from the scene description (`format_handlers/lcc_meta.py`) and the level is passed to the converter as `--select-lod`. An `.lcc` scene keeps every level in one `data.bin`, so its `data.bin`, `index.bin`, `shcoef.bin` and `environment.bin` are fetched beside it as referenced files. An `.lcc2` scene is a JSON octree whose `root.splatFiles` names the chunk files (usually under `data/3dgs/`); only the chosen level's chunk files are fetched, not the other levels or the environment chunk. Chunk files are `.spz`, `.sog` or `.ply`, all accepted on their own, so the file-upload trigger excludes `*/data/3dgs/*`: uploading a scene starts one run for the `.lcc2` rather than one per chunk. The workflow's own filter does not exclude them, so a chunk can still be run by hand.

Each splat is drawn as a flat, unlit disc in its degree-0 spherical-harmonic colour (`0.5 + 0.28209479 × f_dc`). The disc has the coverage of the splat's Gaussian: radius `σ × √(2α)`, where `σ = √(s_max × s_mid)` (the two larger of `exp(scale_*)`) and `α = sigmoid(opacity)`, so a faint or elongated splat does not draw as a large opaque blob. Splats with opacity below 0.2 are dropped and the rest are sampled to the splat budget (1 million per vCPU, at most 5 million) with probability proportional to opacity. Scenes are turned 180° about Z, the original 3DGS (COLMAP) Y-down convention to Y-up, as SuperSplat does. View-dependent colour and anisotropic splat shapes are not rendered.

A captured scene surrounds its subject with background (room walls, sky, distant trees) built from large splats, and framing the whole scene put the camera among them. The handler renders the scene's core: the splats within 1.75 times the 60th-percentile distance from the scene's centre, with each splat weighted by `α / σ²` in both the centre and the percentile, so small opaque splats (the detail the capture looked at closely) decide where the core is. A compact subject (an object, a room, a slab of terrain) loses nothing, since all of it lies within 1.75 times its own 60th-percentile distance. Splats in the core whose disc is larger than 3% of the core radius and ten times the median disc are dropped as fog. When the core is a plane (its smallest spread under 35% of the middle one) tilted less than 60° from the up axis, as the ground of an outdoor capture is, it is levelled so the orbit circles the ground's normal. The log records the core radius, the splats kept and dropped, and any levelling. A scene of fewer than 1,000 splats is drawn whole.

`splat-transform` runs `.mjs` generator scripts and downloads `http(s)://` inputs, so the container only ever passes it a local regular file inside the job's work directory whose name ends in an allowlisted splat extension, starts it with an argument list (no shell), `--gpu cpu` and a minimal environment, and stops it after 30 minutes. The converter opens an `.lcc2` scene's chunk files by the names its description lists, so before it starts, every name must be a relative path that stays inside the job's work directory (no absolute path, URL or scheme, `..` escape, backslash or control character), and every chunk of the chosen level must be present. Otherwise the run fails with a message naming the problem. The description is read up to 64 MiB and 1,000,000 octree nodes.

### USD Stage Handling

The USD handler walks the composed stage with `Usd.PrimRange(..., Usd.TraverseInstanceProxies())`, so instanced geometry is drawn at every instance. A `PointInstancer` is expanded with `ComputeInstanceTransformsAtTime` and its prototypes are drawn only through it. A prim whose computed visibility is `invisible`, or whose computed purpose is `guide` or `proxy`, is skipped with its subtree. `Mesh` prims and the implicit `Cube`, `Sphere`, `Cylinder`, `Cone` and `Capsule` prims are tessellated. Values are read at the stage's `startTimeCode` when it declares a time range, else at each attribute's earliest time sample.

### USD Colour and Texture Support

Each prim's colour comes from, in order: the diffuse texture of the `UsdUVTexture` connected to the bound `UsdPreviewSurface`'s `diffuseColor`; that input's constant value; the `displayColor` primvar (indices applied; constant, uniform, vertex/varying and faceVarying interpolation averaged onto vertices). A prim with no colour in a stage where others are coloured gets a neutral grey. Other textures of the material (normal, roughness, occlusion) are never used as colour.

1. Extracts UV coordinates from the primvar the texture's `UsdPrimvarReader` names, else a common name (`st`, `UVMap`, `st0`, `texCoord_0`, `Texture_uv`, `uv`)
2. Traverses `MaterialBindingAPI` -> `UsdPreviewSurface` -> `diffuseColor` -> `UsdUVTexture` to find the texture file
3. Loads the texture from the path USD resolved against the layer that authored it, only from inside the input's local asset directory, or from inside a USDZ archive (zip extraction)
4. Bakes texture colors to per-vertex RGB data for rendering

MaterialX materials are not read (the `usd-core` build has no MaterialX plugin); their prims render in the default colour.

### Compute and Limits

The container sizes its work to the task it runs in, read from its cgroup (memory limit; CPU quota and affinity), so it stays within whichever job size started it. The shipped state machine does not set `VAMS_COMPUTE_TIER`; when an orchestration sets it, the value is logged, and budgets never depend on it.

| Budget    | Per vCPU  | Memory cap (35% of the limit) | Floor     | Ceiling    |
| :-------- | :-------- | :---------------------------- | :-------- | :--------- |
| Points    | 5 million | 120 bytes per point           | 1 million | 20 million |
| Triangles | 5 million | 200 bytes per triangle        | 1 million | 30 million |
| Splats    | 1 million | 200 bytes per splat           | 250,000   | 5 million  |

On 4 vCPU and 16 GiB that is 20 million points or triangles. A point cloud above its budget is sampled; a mesh above its budget is decimated with VTK quadric clustering (a texture is baked to colours first). VTK allocates the clustering grid densely, so the grid is also bounded to an eighth of the memory limit at 88 bytes per bin (at most 2^26 bins): a closed mesh far above the budget comes out with fewer triangles than the budget allows (a 20 million triangle sphere on 16 GiB becomes about 730,000). The rasteriser (Mesa llvmpipe) runs `LP_NUM_THREADS` = the task's vCPU count; on 4 vCPU a 36-frame 800x600 orbit of 20 million triangles renders in about 100 seconds.

-   **Download**: parallel ranged GETs (64 MiB parts, four per vCPU, at most 32).
-   **Disk**: before the download, the input's size plus 1 GiB of output headroom is checked against the work volume's free space; an input that does not fit fails at once with a message naming both. Referenced files are checked the same way as they are fetched. A streamed PTX needs no disk.
-   **Memory**: a `MemoryError` while loading fails the run with a message naming the task's memory limit.
-   **Timings**: the log records the input size, the budgets and the seconds spent in each phase (`download`, `fetch`, `load`, `render`, `encode`, `upload`).

## References

-   [PyVista](https://docs.pyvista.org/) - 3D plotting and mesh analysis
-   [VTK](https://vtk.org/) - Visualization Toolkit
-   [trimesh](https://trimesh.org/) - Mesh loading and processing
-   [laspy](https://laspy.readthedocs.io/) - LAS/LAZ point cloud format
-   [pye57](https://github.com/davidcaron/pye57) - E57 point cloud format
-   [OpenUSD](https://openusd.org/) - Universal Scene Description
-   [beersandrew/usd-thumbnail-generator](https://github.com/beersandrew/usd-thumbnail-generator) - Reference implementation for USD thumbnail generation
