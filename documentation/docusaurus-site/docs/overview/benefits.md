# Benefits

Visual Asset Management System (VAMS) delivers a comprehensive set of benefits for organizations managing specialized visual assets in the AWS Cloud. This page summarizes the key advantages of adopting VAMS for your spatial computing and visual asset workflows.

---

## Spatial Data Sovereignty and Access Democratization

VAMS serves as a **single pane of glass** for an organization's spatial data source of truth. By deploying entirely within your AWS account, VAMS provides full data sovereignty — your data never leaves your control. The web-based interface, CLI, and REST API **democratize access** to spatial data, enabling any authorized user to discover, visualize, and work with 3D assets without requiring specialized desktop software or direct access to storage systems.

---

## Open-Source Extensibility

As an open-source project, VAMS is designed to be adapted to your organization's specific requirements. The plugin-based architecture supports integration of new viewer plugins, upstream data sources (external S3 buckets, partner connectors), downstream consumers (knowledge graphs, processing services), and custom workflow pipelines — all without vendor lock-in.

---

## Centralized Asset Management

VAMS provides a single, cloud-native platform for storing, organizing, and distributing all visual asset types. Organizations no longer need to maintain fragmented local file servers or rely on general-purpose storage solutions that lack spatial data awareness.

- Organize assets across multiple databases with configurable metadata schemas
- Support for external Amazon S3 buckets, allowing integration with existing storage infrastructure
- Full asset versioning with alias naming, archive/unarchive, version comparison, and metadata restoration on revert
- Cross-database asset linking and file operations for unified asset management across organizational boundaries
- Tag-based categorization with custom tag types for flexible asset organization
- Built-in comment and subscription system for collaborative asset review

:::info[Storage Flexibility]
VAMS leverages Amazon S3 as its storage layer, providing 99.999999999% (11 nines) durability. You can use VAMS-managed buckets, import existing Amazon S3 buckets, or combine both approaches.
:::


---

## Interactive Visualization

VAMS includes 17 built-in viewer plugins that allow users to visualize a wide range of 3D, media, and document file formats directly in the browser, without requiring specialized desktop software or file downloads.

- View 3D meshes (GLTF, GLB, OBJ, FBX, STL, and more) with the Three.js viewer, including scene graph navigation, material editing, and transform controls
- Explore point clouds (E57, LAS, LAZ) through the Potree viewer with octree-based streaming
- Visualize Gaussian splats with BabylonJS or PlayCanvas viewers, including WebXR support
- Render CAD files (STEP, IGES, BREP) via Three.js with WebAssembly-based loaders
- Open USD scenes (USD, USDA, USDC, USDZ) through the Needle Engine WASM viewer
- Play video, audio, and view images, PDFs, columnar data, and text files with dedicated viewers
- Plugin-based architecture allows adding custom viewers for proprietary or emerging formats

---

## Automated Processing Pipelines

VAMS provides a configurable pipeline and workflow system that automates common asset processing tasks. Pipelines execute as AWS Batch Fargate containers orchestrated by AWS Step Functions, with support for AWS Lambda, Amazon SQS, and Amazon EventBridge execution types.

- **3D Conversion** -- Convert between 3D file formats using Trimesh and Blender
- **CAD/Mesh Metadata Extraction** -- Automatically extract geometric metadata from CAD and mesh files using CADQuery
- **Point Cloud Processing** -- Generate Potree octree representations for browser-based point cloud streaming
- **Gaussian Splat Generation** -- Create 3D Gaussian splats from media files using the 3D Reconstruction Toolkit
- **3D Preview Thumbnails** -- Generate animated GIF or static image previews from 3D, point cloud, CAD, and USD files via headless rendering
- **GenAI Metadata Labeling** -- Automatically generate metadata labels using Amazon Bedrock foundation models
- **NVIDIA Isaac Lab Training** -- Run reinforcement learning training and evaluation workloads
- **RapidPipeline and VNTANA ModelOps** -- Licensed pipeline integrations for advanced spatial data optimization

:::tip[Auto-Trigger]
Most pipelines support automatic triggering on file upload. Enable `autoRegisterAutoTriggerOnFileUpload` in the deployment configuration to process new assets as they arrive.
:::


---

## Intelligent Search

VAMS integrates with Amazon OpenSearch Service to provide full-text and attribute-based search across assets and files. The search system indexes asset metadata, file attributes, tags, and custom metadata fields for rapid discovery.

- Full-text search across asset names, descriptions, metadata, and file attributes
- Dual-index architecture with separate indexes for assets and files, enabling targeted search scopes
- Automatic index synchronization through Amazon SNS and Amazon SQS event-driven indexing
- Preview thumbnail display in search results for visual asset identification
- Support for both Amazon OpenSearch Serverless (zero operational overhead) and Amazon OpenSearch Provisioned (fine-grained configuration control)
- Optional search re-indexing on deployment for full index refresh

:::note[OpenSearch Is Optional]
VAMS can operate without Amazon OpenSearch. When disabled, the `NOOPENSEARCH` feature flag activates, and the web interface adjusts to hide search-dependent features. Basic asset browsing and management remain fully functional.
:::


---

## Fine-Grained Access Control

VAMS implements a two-tier authorization model using Casbin policy enforcement, providing both API-level and data-level access control. This enables precise control over who can access which resources and what actions they can perform.

- **Tier 1 (API-level)** -- Controls which API endpoints and web routes a role can access
- **Tier 2 (Object-level)** -- Controls which specific data entities (databases, assets, pipelines) a role can access
- Both tiers must independently authorize a request for access to succeed (defense-in-depth)
- Pre-built permission templates for common profiles: database admin, database user, database read-only, global read-only, and deny-tagged-assets
- Bulk import of permission constraints via JSON templates with server-side variable substitution
- Support for Amazon Cognito user pools, SAML federation, or external OAuth2 identity providers
- API key management for application-to-application integration with user ID impersonation
- IP range restrictions via the custom Lambda authorizer for network-level access control

---

## Multi-Region and Multi-Partition Deployment

VAMS is built from the ground up for deployment across AWS partitions and regions, including environments with strict compliance requirements. The infrastructure layer uses partition-aware resource generation, so the same codebase deploys correctly to any supported AWS environment.

- Deploy to any AWS commercial region with Amazon CloudFront distribution
- Deploy to AWS GovCloud (US) regions with Application Load Balancer distribution and FIPS endpoint support
- Deploy in air-gapped or VPC-isolated environments with VPC endpoints for all required AWS services

- Import existing VPCs and subnets for integration with organizational network architectures
- Multiple VAMS deployments in the same account using unique stack names and regions
- AWS WAF integration for web application firewall protection (optional)

:::warning[Compliance Responsibility]
VAMS is provided as near-production-grade at its default configuration. Consult with your organizational security team prior to production use, and review the [Security Considerations](../developer/setup.md) section for recommended hardening steps.
:::


---

## Extensible and Open Architecture

VAMS is designed as an open, extensible platform that development teams can customize and extend to meet specific organizational requirements. The modular architecture encourages building on top of VAMS rather than modifying core components.

- Open-source codebase with Apache-2.0 license
- Plugin-based viewer architecture for adding custom visualization capabilities
- Pipeline and workflow system for integrating custom processing logic
- REST API layer for building custom applications and integrations
- Addon framework (e.g., Garnet Framework) for connecting VAMS to external systems
- CDK-based infrastructure for customizing deployment topology and AWS service configuration
- Configurable metadata schemas for adapting data models to domain-specific requirements
