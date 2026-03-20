# Partner Integrations

VAMS integrates with a range of open-source projects and licensed commercial solutions for 3D visualization, asset processing, data synchronization, and simulation. This page provides a reference of all significant integrations, their purpose, and how they connect to VAMS.

---

## Open-Source Integrations

The following open-source projects are integrated into VAMS as viewer plugins, processing pipelines, or utility components.

### 3D Viewers

| Integration                                       | Purpose                                                     | License               | VAMS Component                                        |
| ------------------------------------------------- | ----------------------------------------------------------- | --------------------- | ----------------------------------------------------- |
| [Online 3D Viewer](https://3dviewer.net/)         | Web viewer for Rhinoceros 3D, AMF, BIM, OFF, VRML formats   | MIT                   | Viewer plugin (`online3d-viewer`)                     |
| [CesiumJS](https://cesium.com/platform/cesiumjs/) | Geospatial 3D tileset viewer with streaming support         | Apache-2.0            | Viewer plugin (`cesium-viewer`)                       |
| [Potree](https://potree.github.io/)               | Point cloud viewer for E57, LAS, LAZ formats                | BSD-2-Clause          | Viewer plugin (`potree-viewer`) + processing pipeline |
| [BabylonJS](https://www.babylonjs.com/)           | Gaussian splat viewer for PLY and SPZ files                 | Apache-2.0            | Viewer plugin (`gaussian-splat-viewer-babylonjs`)     |
| [PlayCanvas](https://playcanvas.com/)             | Gaussian splat viewer for PLY and SOG files                 | MIT                   | Viewer plugin (`gaussian-splat-viewer-playcanvas`)    |
| [Needle Engine](https://needle.tools/)            | USD format WASM viewer for .usd, .usda, .usdc, .usdz        | Apache-2.0 (Modified) | Viewer plugin (`needletools-usd-viewer`)              |
| [Three.js](https://threejs.org/)                  | General-purpose 3D viewer for mesh and optional CAD formats | MIT                   | Viewer plugin (`threejs-viewer`)                      |

### Processing Pipelines

| Integration                                                                                                                                                 | Purpose                                              | License               | VAMS Component                                                      |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | --------------------- | ------------------------------------------------------------------- |
| [Trimesh](https://trimesh.org/)                                                                                                                             | 3D mesh conversion and metadata extraction           | MIT                   | 3D Basic Conversion pipeline, CAD/Mesh Metadata Extraction pipeline |
| [CadQuery](https://github.com/CadQuery/cadquery)                                                                                                            | Open-standard CAD conversion and metadata extraction | Apache-2.0 / LGPL-2.1 | CAD/Mesh Metadata Extraction pipeline, 3D Thumbnail pipeline        |
| [Blender](https://www.blender.org/)                                                                                                                         | Preview file generation and metadata generation      | GNU GPLv3             | GenAI Metadata 3D Labeling pipeline                                 |
| [3D Reconstruction Toolkit](https://github.com/aws-solutions-library-samples/guidance-for-open-source-3d-reconstruction-toolbox-for-gaussian-splats-on-aws) | Gaussian splat generation from media files           | MIT                   | Gaussian Splat Toolbox pipeline                                     |
| [NVIDIA Isaac Lab](https://github.com/isaac-sim/IsaacSim)                                                                                                   | Reinforcement learning training and evaluation       | Apache-2.0            | Isaac Lab Training pipeline                                         |

:::note
The Isaac Lab Training pipeline uses NVIDIA Isaac Sim container images, which are subject to the [NVIDIA Software License Agreement](https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license). You must set `acceptNvidiaEula: true` in the configuration to deploy this pipeline.
:::

### Data Synchronization

| Integration                                       | Purpose                                                       | License    | VAMS Component               |
| ------------------------------------------------- | ------------------------------------------------------------- | ---------- | ---------------------------- |
| [Garnet Framework](https://garnet-framework.dev/) | Push VAMS data changes to an external NGSI-LD knowledge graph | Apache-2.0 | Addon (`useGarnetFramework`) |

The Garnet Framework integration allows VAMS to synchronize database, asset, and file changes to a Garnet Framework deployment in the same AWS account. This enables building knowledge graph representations of your visual asset data. Configuration requires specifying the Garnet API endpoint, API token, and Amazon SQS ingestion queue URL.

#### What gets indexed

When Garnet Framework integration is enabled, VAMS automatically creates and maintains NGSI-LD entities for:

1. **Databases** -- Complete database records including bucket associations and custom metadata.
2. **Assets** -- Full asset information including relationships, versions, and custom metadata.
3. **Asset Links** -- Relationship entities connecting assets (parent-child, related) with metadata.
4. **Files** -- Individual file entities with Amazon S3 information, attributes, and custom metadata.

#### NGSI-LD entity types

VAMS creates the following NGSI-LD entity types in the Garnet Framework:

| Entity Type     | URN Format                                                     | Description                                |
| --------------- | -------------------------------------------------------------- | ------------------------------------------ |
| `VAMSDatabase`  | `urn:vams:database:\{databaseId\}`                             | Database entities with bucket associations |
| `VAMSAsset`     | `urn:vams:asset:\{databaseId\}:\{assetId\}`                    | Asset entities with full metadata          |
| `VAMSAssetLink` | `urn:vams:assetlink:\{assetLinkId\}`                           | Asset relationship entities                |
| `VAMSFile`      | `urn:vams:file:\{databaseId\}:\{assetId\}:\{encodedFilePath\}` | File entities with Amazon S3 details       |

#### Event flow

Data changes flow through the following architecture:

1. **Amazon DynamoDB Streams** to Amazon SNS topics to Amazon SQS queues to Garnet indexer Lambda functions.
2. **Amazon S3 event notifications** to Amazon SNS topics to Amazon SQS queues to the Garnet file indexer Lambda function.
3. **Garnet indexer Lambda functions** convert data to NGSI-LD format and send to the external Garnet ingestion Amazon SQS queue.

VAMS maintains bidirectional relationships between entities and includes all custom metadata fields as NGSI-LD properties. When data is created, updated, or deleted in VAMS, the corresponding NGSI-LD entity is automatically synchronized.

:::tip[Reindexing existing data]
If you enable Garnet Framework integration on an existing VAMS deployment and need all current data indexed, use the reindex utility in the migration scripts (without clearing Amazon OpenSearch indexes) to trigger a full data reindex through the global notification queues. This reindexes all relevant VAMS data with the Garnet Framework.
:::

---

## Licensed Commercial Integrations

The following integrations require separate commercial licenses from their respective vendors.

| Integration                                 | Purpose                                                  | License Type                 | VAMS Component                                                        |
| ------------------------------------------- | -------------------------------------------------------- | ---------------------------- | --------------------------------------------------------------------- |
| [RapidPipeline](https://rapidpipeline.com/) | Spatial data conversions and optimizations               | Commercial (AWS Marketplace) | Processing pipeline (`useRapidPipeline`)                              |
| [VNTANA](https://www.vntana.com/)           | 3D ModelOps: conversions, optimizations, and web viewing | Commercial (AWS Marketplace) | Viewer plugin (`vntana-viewer`) + Processing pipeline (`useModelOps`) |
| [Veerum](https://veerum.com/)               | Advanced 3D viewer for point clouds and 3D tilesets      | Commercial                   | Viewer plugin (`veerum-viewer`)                                       |

### Enabling Licensed Integrations

#### RapidPipeline

RapidPipeline provides two deployment options in VAMS: Amazon ECS and Amazon EKS.

1. Subscribe to the RapidPipeline 3D Processor on the [AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-zdg4blxeviyyi).
2. Set the container image URI in `app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI` or `app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI`.
3. Enable the pipeline with `enabled: true`.
4. Redeploy the stack. The pipeline requires a VPC with public/private subnets for internet access to the AWS Marketplace.

#### VNTANA

VNTANA provides both a viewer plugin and a processing pipeline (ModelOps).

1. Subscribe to the VNTANA 3D Optimization Engine on the [AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-ooio3bidshgy4).
2. For the processing pipeline, set the container image URI in `app.pipelines.useModelOps.ecrContainerImageURI` and enable with `enabled: true`.
3. For the viewer plugin, enable it in `web/src/visualizerPlugin/config/viewerConfig.json` by setting `"enabled": true` on the `vntana-viewer` entry.
4. Redeploy the stack.

#### Veerum

The Veerum 3D Viewer provides advanced visualization for point cloud and 3D tileset files.

1. Contact [Veerum](https://veerum.com/) to obtain a license.
2. Enable the viewer in `web/src/visualizerPlugin/config/viewerConfig.json` by setting `"enabled": true` on the `veerum-viewer` entry.
3. The Veerum Viewer requires the Potree Auto-Processing pipeline to be enabled for point cloud file loading.
4. Rebuild and redeploy the web application.

---

## Integration Architecture

The following diagram illustrates how integrations connect to VAMS components:

```
VAMS Web Application
  |-- Viewer Plugins (Online 3D Viewer, CesiumJS, Potree, BabylonJS,
  |                    PlayCanvas, Needle Engine, Three.js, VNTANA, Veerum)
  |
VAMS Backend
  |-- Processing Pipelines
  |     |-- AWS Batch (Fargate): Trimesh, CadQuery, Blender, 3D Recon Toolkit
  |     |-- AWS Batch (GPU): NVIDIA Isaac Lab
  |     |-- Amazon ECS: RapidPipeline, VNTANA ModelOps
  |     |-- Amazon EKS: RapidPipeline (EKS option)
  |
  |-- Addons
        |-- Amazon SQS --> Garnet Framework (Knowledge Graph)
```

:::info
All pipeline integrations are optional and disabled by default. Enable only the integrations you need to minimize deployment complexity and cost. See the [Configuration Guide](../deployment/configuration-reference.md) for pipeline configuration details.
:::
