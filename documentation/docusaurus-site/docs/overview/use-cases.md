# Use Cases

Visual Asset Management System (VAMS) supports a broad range of industries and workflows that involve the management, visualization, and processing of 2D and 3D digital assets. The spatial data plane concept applies to almost every industry dealing with visual media at scale — from terabyte-scale LiDAR scans to curated AI training datasets. This page describes common use cases where organizations use VAMS as a single source of truth for their spatial data.

---

## Manufacturing

### Problem

Manufacturing organizations generate large volumes of CAD models, 3D scans, and engineering documents across distributed teams. These assets are typically stored on local workstations or on-premises file servers, making collaboration difficult and limiting access to specialized desktop software.

### How VAMS Solves It

VAMS centralizes manufacturing visual assets in Amazon S3 with metadata-rich organization across databases. Engineers can view CAD files (STEP, IGES, BREP) and 3D meshes (GLTF, GLB, OBJ, FBX, STL) directly in the browser through the Three.js viewer without installing desktop CAD software. The CAD/Mesh Metadata Extraction pipeline automatically extracts geometric properties on upload.

**Key features used:**

-   Three.js viewer with CAD format support (STEP, IGES, BREP via WebAssembly)
-   CAD/Mesh Metadata Extraction pipeline for automated property extraction
-   Asset versioning with alias naming for engineering revision tracking
-   Metadata schemas for standardized part number and BOM (Bill of Materials) tracking
-   Fine-grained access control to separate product lines and restrict access by team

---

## Construction and AEC

### Problem

Architecture, Engineering, and Construction (AEC) projects produce massive point cloud scans, BIM models, and 3D tilesets that are difficult to share across stakeholders. Field teams, architects, and project managers need access to current site data without specialized software licenses.

### How VAMS Solves It

VAMS ingests point cloud data (E57, LAS, LAZ) and processes it through the Potree pipeline to generate octree representations for browser-based streaming. BIM files can be viewed through the Online 3D Viewer plugin, and 3D tilesets are rendered via the CesiumJS viewer with geospatial context. The database metadata system with Amazon Location Service integration provides mini-map views of project sites.

**Key features used:**

-   Potree viewer and pipeline for point cloud streaming and visualization
-   CesiumJS viewer for 3D tileset viewing with geospatial context
-   Online 3D Viewer for BIM format support (.bim)
-   Amazon Location Service integration for project site mapping on database pages
-   Cross-database asset linking for multi-site project relationships
-   Subscription and comment system for stakeholder review workflows

---

## Energy and Utilities

### Problem

Energy companies maintain large inventories of facility scans, infrastructure models, and inspection imagery collected across geographically dispersed sites. Sharing these assets securely across field teams, engineers, and regulatory reviewers requires careful access control and compliance with regional data residency requirements.

### How VAMS Solves It

VAMS deploys within the customer's AWS account, supporting AWS GovCloud (US) for data residency and compliance. Point cloud scans from facility inspections are processed through the Potree pipeline for web-based viewing. The two-tier ABAC/RBAC permission system enables precise access control by site, region, or organizational role.

**Key features used:**

-   AWS GovCloud (US) deployment with FIPS endpoint support
-   VPC-isolated deployment support for restricted environments
-   Potree pipeline for facility scan processing and visualization
-   Permission templates for role-based access by facility or region
-   AWS KMS CMK encryption for all data at rest
-   Tag-based asset classification for regulatory and inspection categorization

---

## Geospatial Intelligence

### Problem

Geospatial organizations work with large-scale 3D tilesets, satellite imagery, terrain models, and point cloud datasets. These assets require specialized viewers with geospatial awareness, and the volume of data makes local processing impractical.

### How VAMS Solves It

VAMS provides CesiumJS integration for viewing 3D tilesets with full geospatial context, including coordinate reference systems and terrain overlays. Point clouds are processed through the Potree pipeline for efficient browser-based streaming. Amazon OpenSearch Service enables spatial and attribute-based search across large asset catalogs.

**Key features used:**

-   CesiumJS viewer for 3D tileset visualization with geospatial context
-   Potree viewer and pipeline for point cloud streaming
-   Amazon OpenSearch Service for full-text and attribute-based asset search
-   Database-level metadata with location coordinates for spatial organization
-   External Amazon S3 bucket support for integrating existing geospatial data stores
-   API access for integration with GIS platforms and downstream processing systems

---

## Robotics and Physical AI

### Problem

Robotics and Physical AI teams need to manage simulation environments, training datasets, sensor recordings, and 3D scene representations. These assets are critical inputs for reinforcement learning, perception model training, and simulation validation, but are often scattered across team members' workstations.

### How VAMS Solves It

VAMS provides centralized management of simulation assets with the NVIDIA Isaac Lab Training pipeline for running reinforcement learning workloads directly from managed assets. USD scene files are viewable through the Needle Engine viewer, and Gaussian splat reconstructions are rendered via BabylonJS or PlayCanvas viewers. The workflow system chains multiple processing steps for end-to-end asset preparation.

**Key features used:**

-   NVIDIA Isaac Lab Training pipeline for reinforcement learning training and evaluation
-   Needle USD Viewer for Universal Scene Description file visualization
-   Gaussian Splat pipelines (BabylonJS and PlayCanvas viewers, 3D Reconstruction Toolkit pipeline)
-   Workflow system for chaining asset processing steps
-   API and CLI access for CI/CD integration with robotics development pipelines
-   Metadata schemas for tracking simulation parameters and training configurations

---

## Digital Twins

### Problem

Digital twin initiatives require managing and synchronizing 3D representations of physical assets across multiple systems. As-built models, sensor overlays, and operational data must be linked and kept current as the physical environment changes over time.

### How VAMS Solves It

VAMS serves as the central asset repository for digital twin 3D models, with the Garnet Framework addon enabling synchronization to NGSI-LD knowledge graphs for linking spatial data with operational systems. Asset versioning tracks changes to digital twin representations over time, and cross-database linking connects related assets across organizational boundaries.

**Key features used:**

-   Garnet Framework addon for NGSI-LD digital twin knowledge graph synchronization
-   Asset versioning with alias naming for tracking as-built model revisions
-   Cross-database asset linking for connecting related twin representations
-   Multiple viewer plugins for visualizing different aspects of the digital twin
-   Automated pipeline processing for updating derived representations on asset change
-   REST API for integration with IoT platforms and operational data systems

---

## Media and Entertainment

### Problem

Media and entertainment studios manage large libraries of 3D models, textures, animations, video, and audio assets across production teams. Asset review requires specialized playback capabilities, and productions often involve external partners who need controlled access to specific asset subsets.

### How VAMS Solves It

VAMS provides a unified library for all visual and media assets with dedicated viewer plugins for 3D models (Three.js, Online 3D Viewer), video (Video Player), audio (Audio Player), images (Image Viewer), and documents (PDF Viewer, Text Viewer). The permission system enables granular access control for external collaborators, and the comment system supports asset review workflows.

**Key features used:**

-   17 viewer plugins covering 3D, video, audio, image, document, and data formats
-   3D Preview Thumbnail pipeline for generating animated GIF previews of 3D assets
-   Comment system with rich text for collaborative asset review
-   Permission templates for external collaborator access (database-readonly, database-user)
-   Asset versioning for tracking production iterations
-   Bulk upload and download with the VamsCLI for production asset pipelines

---

## Game Development

### Problem

Game development teams produce and iterate on thousands of 3D models, textures, level assets, and environment scenes. Asset pipelines require automated conversion between formats, quality validation, and optimization for target platforms. Existing game asset management tools often lack cloud-native scalability.

### How VAMS Solves It

VAMS provides automated 3D format conversion through the 3D Conversion Basic pipeline and the RapidPipeline integration for optimization. The Three.js viewer supports direct inspection of common game asset formats (GLTF, GLB, OBJ, FBX, STL), and the Gaussian Splat viewers enable review of photogrammetry-based environment captures. The workflow system chains conversion, optimization, and validation steps into repeatable pipelines.

**Key features used:**

-   Three.js viewer for game asset inspection (GLTF, GLB, OBJ, FBX, STL, and more)
-   3D Conversion Basic pipeline for automated format conversion
-   RapidPipeline integration for asset optimization (licensed)
-   VNTANA ModelOps for advanced spatial data optimization (licensed)
-   Gaussian Splat viewers and pipeline for photogrammetry-based environment capture
-   Workflow system for chaining conversion, optimization, and quality validation steps
-   VamsCLI for automated asset ingestion from build pipelines

---

## Defense and Public Sector

### Problem

Defense and aerospace organizations work with sensitive 3D data — facility scans, equipment models, terrain data, and simulation environments — that must remain within controlled environments. Data is often siloed across classification domains, and non-engineering teams (operations, maintenance, training) need access but lack the specialized tools or clearances for traditional PLM systems.

### How VAMS Solves It

VAMS deploys entirely within your AWS account, including AWS GovCloud (US) regions, ensuring full data sovereignty. The ABAC/RBAC permission system provides fine-grained access control at both the API and data entity level, allowing precise control over who can access which assets. The solution supports VPC-isolated deployments with FIPS endpoints for restricted environments.

**Key features used:**

-   AWS GovCloud (US) deployment with FIPS endpoint support
-   Two-tier ABAC/RBAC authorization for fine-grained access control
-   VPC isolation with VPC endpoints for restricted environments
-   NVIDIA Isaac Lab pipeline for simulation training and evaluation
-   Potree pipeline for point cloud web visualization of facility scans
-   Asset versioning with lineage tracking
-   Custom pipeline extensibility for specialized processing requirements

---

## AI and Machine Learning

### Problem

AI and ML teams working with spatial data need to manage, curate, and label large training datasets of 3D models, point clouds, and associated imagery. Dataset versioning, metadata tagging, and quality tracking are essential but often handled through ad-hoc file systems or spreadsheets.

### How VAMS Solves It

VAMS provides structured dataset management with metadata schemas, versioning, and automated labeling pipelines. The GenAI Metadata Labeling pipeline uses Amazon Bedrock and Amazon Rekognition to automatically generate descriptive metadata for 3D assets. The search system enables ML engineers to discover and curate training datasets by querying across metadata fields.

**Key features used:**

-   GenAI Metadata Labeling pipeline for automated asset tagging via Amazon Bedrock
-   Metadata schemas with typed fields for structured dataset annotation
-   Asset versioning for tracking dataset iterations
-   OpenSearch-powered search for dataset discovery and curation
-   VamsCLI for bulk dataset ingestion and metadata export
-   REST API for integration with training pipelines

---

## Data Center Construction

### Problem

Data center construction requires precise verification that builds match specifications — every missing server rack, misaligned cable tray, or incorrect component represents lost revenue and project delays. Teams need to compare as-built LiDAR scans against design models at scale.

### How VAMS Solves It

VAMS centralizes both design models and as-built scan data in a versioned, searchable repository. The Potree pipeline generates web-viewable point clouds from LiDAR scans, enabling browser-based comparison without desktop software. Metadata schemas track build status, compliance, and inspection data at the asset and file level.

**Key features used:**

-   Potree pipeline for automated point cloud web viewing
-   Asset versioning for tracking design iterations and as-built captures
-   Metadata schemas for build compliance tracking
-   3D Preview Thumbnail pipeline for visual overview of scans
-   Cross-database asset linking for relating design models to as-built scans
-   Tag-based organization for tracking construction phases and zones

:::tip[Pipeline Extensibility]
If your use case requires processing capabilities beyond the built-in pipelines, VAMS supports custom pipelines using AWS Lambda, Amazon SQS, or Amazon EventBridge execution types. See the [Developer Guide](../developer/setup.md) for instructions on writing custom pipelines.
:::
