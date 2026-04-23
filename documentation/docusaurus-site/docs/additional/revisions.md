# Document Revision History

This page tracks the version history of the Visual Asset Management System (VAMS). Each release includes a summary of key changes, new features, and important upgrade notes.

---

## Revision History

| Version       | Date       | Key Changes                                                                                                                                                                                                                                                                                      |
| ------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [2.5.1](#251) | 2026-04-29 | Bug fixes: upload subfolder paths, file version history cleanup on delete, S3 version pagination, authorization error handling, image viewer version switching, CLI download pagination, CLI upload progress display                                                                             |
| [2.5.0](#250) | 2026-04-21 | Website overhaul (Vite, Amplify V6, dark/light theme), Needle USD viewer, Three.js CAD viewer, SQS/EventBridge pipeline support, 3D preview thumbnail pipeline, database metadata with location maps, enhanced asset versions, Cognito user management, API key management, permission templates |
| [2.4.1](#241) | 2026-01-30 | GovCloud deployment fixes, CloudFront KMS fix, metadata schema navigation fix, file manager UX improvements                                                                                                                                                                                      |
| [2.4.0](#240) | 2026-01-16 | Veerum viewer, NVIDIA Isaac Lab pipeline, Garnet Framework addon, metadata schema overhaul, metadata system overhaul, asset unarchiving, CloudFront custom domains, audit logging, EKS pipeline option                                                                                           |
| [2.3.2](#232) | 2026-01-12 | CLI documentation fixes, NPM dependency updates                                                                                                                                                                                                                                                  |
| [2.3.1](#231) | 2025-11-21 | CLI bug fixes, viewer install optimizations                                                                                                                                                                                                                                                      |
| [2.3.0](#230) | 2025-11-13 | VamsCLI tool, overhauled search system, plugin-based viewer architecture, CesiumJS/BabylonJS/PlayCanvas viewers, CAD metadata pipeline, Gaussian Splat Toolbox, IP restrictions, asset link enhancements                                                                                         |
| [2.2.0](#220) | 2025-09-31 | Asset/file separation, multi-file assets, S3 presigned uploads, external OAuth IDP, asset versioning, new pipelines, global workflows, VPC improvements                                                                                                                                          |

---

## Version Details

### 2.5.1

**Release date:** 2026-04-29

**Key fixes:**

-   Fixed file upload path construction when uploading to a subfolder — files now correctly include the full folder path (e.g., `/textures/USD/texture.png` instead of `/texture.png`).
-   Fixed permanent file deletion not cleaning up Amazon DynamoDB version snapshot records — re-uploading a file at the same path no longer shows stale version history from previously deleted files.
-   Fixed Amazon S3 version deletion not paginating — permanent delete now removes all S3 object versions even when a file has more than 1000 versions.
-   Fixed permanent asset deletion not paginating Amazon DynamoDB queries for version files and metadata version cleanup — assets with large numbers of versions or files now fully delete all related records, using batch writes for efficiency.
-   Fixed `authorization_error()` being raised as an exception instead of returned as a response across multiple backend handlers (assetService, metadataSchemaService, userRolesService, createRole, tagService, createTag), which caused "exceptions must derive from BaseException" errors.
-   Fixed version switching across many viewer plugins — when switching to a different file version — the viewer now correctly fetches and displays the selected version instead of showing the latest only
-   Fixed CLI asset download command capping at approximately 100 files — now paginates through all API results when downloading whole assets or folders.
    -   Improved CLI asset download performance — presigned URL generation and file downloads now run concurrently via a streaming pipeline instead of sequentially.
    -   The `--recursive` flag on `assets download` now defaults `--file-key` to `/` when not specified, enabling whole-asset recursive downloads without explicitly providing `--file-key /`.
-   Fixed CLI file upload progress display erasing terminal scrollback history — progress now tracks and clears only the lines it actually printed.
-   Fixed NVIDIA pipeline CodeBuild Amazon ECR repositories failing to delete when disabling pipelines — added `emptyOnDelete` to Cosmos and Gr00t CodeBuild ECR repositories so container images are automatically cleared before AWS CloudFormation deletion.

### 2.5.0

**Release date:** 2026-04-21

**Major changes:**

-   Migrated the web application to Vite build framework with AWS Amplify V6 Gen2 SDK and dark/light theme support (dark default).
-   Added Needle USD 3D WASM viewer for `.usd`, `.usda`, `.usdc`, `.usdz` files.
-   Added Three.js 3D and CAD viewer for `.gltf`, `.glb`, `.obj`, `.fbx`, `.stl`, `.ply`, `.dae`, `.3ds`, `.3mf`, `.stp`, `.step`, `.iges`, `.brep` files with optional LGPL-licensed CAD support.
-   Added Amazon SQS and Amazon EventBridge pipeline execution types alongside AWS Lambda.
-   Added 3D Preview Thumbnail pipeline for CPU-based headless rendering of animated GIF or static image previews.
-   Added database metadata management with Amazon Location Service mini-map display.
-   Enhanced asset versioning with aliasing, archive/unarchive, version editing, and metadata versioning.
-   Added Amazon Cognito user management through web UI, API, and CLI.
-   Added API Key management system with user ID impersonation.
-   Added permission constraint template bulk import system with pre-built templates.

**Breaking changes:**

-   Asset version DynamoDB table changes require migration scripts.
-   Web overhaul causes significant merge conflicts for forked repositories.

:::warning[Upgrade Path]
Run the upgrade script at `infra/deploymentDataMigration/v2.4_to_v2.5/upgrade` to migrate permission constraints and asset version data.
:::

---

### 2.4.1

**Release date:** 2026-01-30

**Key fixes:**

-   Fixed CDK deployment errors for GovCloud environments (storage queue names, CloudFront KMS principals, metadata schema KMS permissions, Isaac Lab pipeline IAM).
-   Fixed metadata schema page blank state when re-navigating.
-   Improved Asset FileManager to remember expanded folders during lazy loading.
-   Added service worker for local WASM debugging.

---

### 2.4.0

**Release date:** 2026-01-16

**Major changes:**

-   Added Veerum 3D Viewer (licensed) for point clouds and 3D tilesets.
-   Added NVIDIA Isaac Lab reinforcement learning training pipeline with GPU acceleration.
-   Added Garnet Framework addon for knowledge graph data synchronization.
-   Overhauled metadata schema system: database-specific and global schemas, multi-schema overlay, new field types, CDK-deployable defaults.
-   Overhauled metadata system: multi-entity support (databases, assets, files, asset links), bulk editing with CSV, file attributes, metadata versioning.
-   Added Amazon EKS deployment option for RapidPipeline.
-   Added asset unarchiving, file renaming, CloudFront custom domain support.
-   Added Amazon CloudWatch audit logging for authorization, actions, and errors.
-   Refactored data indexing flow for Amazon OpenSearch and partner integrations.

**Breaking changes:**

-   Permission constraints migrated to a dedicated DynamoDB table.
-   Metadata and schema DynamoDB tables replaced with new tables.
-   Amazon OpenSearch indexes changed schema for metadata fields.

:::warning[Upgrade Path]
Run the upgrade script at `infra/deploymentDataMigration/v2.3_to_v2.4/upgrade` to migrate constraints, metadata, and re-index Amazon OpenSearch.
:::

---

### 2.3.2

**Release date:** 2026-01-12

**Key fixes:**

-   CLI documentation corrections.
-   NPM dependency security updates (`npm audit fix`).

---

### 2.3.1

**Release date:** 2025-11-21

**Key fixes:**

-   CLI sentinel object check, file upload exception handling, and pattern updates.
-   Optimized web viewer custom installers to skip disabled viewers.
-   Licensed viewers disabled by default in configuration.

---

### 2.3.0

**Release date:** 2025-11-13

**Major changes:**

-   Introduced VamsCLI command-line tool for automation, bulk operations, and CI/CD integration.
-   Overhauled asset and file search with new Amazon OpenSearch dual-index architecture.
-   Rewrote the viewer system as a plugin-based, dynamically-loaded architecture with 17 viewer plugins.
-   Added CesiumJS 3D tileset viewer, BabylonJS and PlayCanvas Gaussian Splat viewers, VNTANA licensed viewer, PDF viewer, and Text viewer.
-   Added CAD/Mesh Metadata Extraction pipeline using Trimesh and CadQuery.
-   Added Gaussian Splat Toolbox pipeline.
-   Replaced built-in API Gateway authorizers with custom Lambda authorizers supporting IP range restrictions.
-   Added support for additional asset link metadata types (WXYZ, Boolean, Date, Matrix4x4, GeoJSON, GeoPoint, LLA, JSON).
-   Refactored workflow creation to remove heavyweight step functions library dependency.

**Breaking changes:**

-   Custom Lambda authorizers replace built-in authorizers (may require cache reset).
-   AWS Batch/Fargate CDK construct naming changes require two-phase deployment for existing pipelines.
-   Amazon OpenSearch re-indexing required for new dual-index schema.

:::warning[Upgrade Path]
Run the upgrade script at `infra/deploymentDataMigration/v2.2_to_v2.3/upgrade` to re-index Amazon OpenSearch.
:::

---

### 2.2.0

**Release date:** 2025-09-31

**Major changes:**

-   Complete overhaul of asset management APIs separating assets from files.
-   Added multi-file asset support with folder structures.
-   Implemented Amazon S3 presigned URL uploads replacing scoped S3 access.
-   Added external OAuth 2.0 identity provider support as alternative to Amazon Cognito.
-   Added asset versioning with Amazon S3 version tracking.
-   Added global pipelines and workflows across databases.
-   Introduced new pipelines: GenAI Metadata 3D Labeling, Potree point cloud viewer, 3D basic conversion.
-   Relaxed naming conventions for databases, pipelines, workflows, and asset IDs.
-   Enhanced VPC support with additional endpoints.
-   Added multiple Amazon S3 bucket support with external bucket import.

**Breaking changes:**

-   CDK configuration file format changes required.
-   Asset and database DynamoDB table schema changes require migration scripts.
-   VPC subnet changes may break existing deployments (A/B deployment recommended).
-   New Amazon Cognito user pool may be generated (user migration may be needed).

:::warning[Upgrade Path]
Use A/B stack deployment with data migration scripts at `infra/deploymentDataMigration/v2.1_to_v2.2/upgrade`.
:::
