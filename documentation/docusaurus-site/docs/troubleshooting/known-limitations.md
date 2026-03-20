# Known Limitations

This page documents the known limitations and constraints of the Visual Asset Management System (VAMS). These limitations are tracked and may be addressed in future releases.

---

## Data and Storage Limitations

### Asset ID Conflicts Across Databases with Multiple Amazon S3 Buckets

When using multiple Amazon S3 buckets across different databases, identical asset IDs can exist if assets were created through direct Amazon S3 manipulation. This causes lookup conflicts in **comments** and **subscriptions** functionality.

:::note
This conflict only occurs with manual Amazon S3 changes. Assets created through the VAMS upload API use unique GUIDs for asset IDs, which prevents this issue.
:::


### Pipeline ID Conflicts Between GLOBAL and Non-GLOBAL Databases

Using the same pipeline ID in both a GLOBAL database and a non-GLOBAL database causes overlap conflicts. Pipeline IDs must be unique across the GLOBAL scope and any individual database scope.

### Metadata and Attribute Record Limit

Each metadata entity type (database, asset, asset file, asset link) supports a maximum of **500 metadata and attribute records**. Operations that would exceed this limit are rejected by the API.

### Schema Validation Enforcement

Metadata schema validation is enforced only when metadata is created or updated through the VAMS API. Metadata written by pipeline outputs or direct Amazon DynamoDB manipulation is not validated against schemas. This means:

- Newly created assets may not have required schema fields until metadata is explicitly set via the API.
- Pipeline-generated metadata may not conform to schema restrictions.
- CSV bulk imports are validated, but the validation occurs at the API layer.

---

## API and Performance Limitations

### API Gateway Timeout for Large Operations

Amazon API Gateway enforces a **29-second timeout** on all HTTP responses. The underlying AWS Lambda function continues executing for up to **15 minutes**. This affects:

| Operation | Impact |
|---|---|
| Listing assets with thousands of files | Response may time out while Lambda completes |
| Asset export with deep link trees | Large response payloads take time to assemble |
| Bulk metadata operations | Individual batch writes continue after timeout |
| Amazon OpenSearch re-indexing | Lambda may complete indexing after API returns 504 |

:::info
When a 504 timeout occurs, check Amazon CloudWatch Logs for the relevant Lambda function to verify whether the operation completed successfully.
:::


### Amazon OpenSearch Re-Indexing Timeout

Re-indexing hundreds of thousands to millions of files may not complete within the **15-minute AWS Lambda timeout**. For very large datasets, a local or containerized re-indexing approach may be required. The re-index utility logs a warning when the Lambda function times out, noting that indexing may still be running.

### File Upload Rate Limiting

File upload initialization (stage 1) is limited to **10 upload initializations per user per minute**. This is a security measure to minimize abuse potential. Both the web interface and the VamsCLI have built-in mechanisms for bulk uploading with automatic chunking, retry logic, and throttle recovery.

---

## Pipeline Limitations

### Amazon ECS Pipeline Metadata Input Size Limit

Pipeline metadata inputs sent to Amazon ECS containers have an **8,000 character JSON input limit**. Assets or files with extensive metadata may exceed this limit, causing pipeline execution failures.

:::warning
This limitation applies to metadata passed as JSON input to the container environment. A future pipeline overhaul will convert metadata input to a file-based approach to remove this constraint.
:::


### 3D Preview Thumbnail Pipeline File Size Limit

The 3D Preview Thumbnail pipeline supports a maximum input file size of **100 GB**. The pipeline performs a pre-download Amazon S3 size validation and rejects files exceeding this limit. Supporting larger files may require an Amazon Elastic File System (Amazon EFS) and AWS Fargate implementation.

### Pipeline Output Path Requirements

Pipeline containers must preserve the input file's relative subdirectory path when writing output files. The workflow process-output step expects outputs at the same relative location as the input file within the asset. Failure to maintain this structure results in files being written to incorrect locations.

---

## Web Application Limitations

### Safari Browser Support for WASM Viewers

Safari does not support the cross-origin isolation requirements needed by WebAssembly-based viewers. The following viewers do not function in Safari:

- Needle USD Viewer (.usd, .usda, .usdc, .usdz)
- Three.js Viewer CAD formats (.stp, .step, .iges, .brep)
- Cesium 3D Tileset Viewer (.json tilesets)

Standard mesh formats in the Three.js Viewer (.gltf, .glb, .obj, .fbx, .stl) work correctly in Safari because they do not require WASM.

### Needle USD Viewer Compressed File Limitations

The Needle USD WASM Viewer has difficulty loading dependencies from compressed USD files (USDC format). Compressed files cannot be reliably parsed ahead of time for dependency resolution. Uncompressed USD or USDA files are recommended for the best viewing experience.

### File Extension Upload Restrictions

File extension and MIME type restrictions are enforced only on uploads through the VAMS API. Files added directly to the Amazon S3 asset bucket bypass these checks. The following file extensions are blocked on upload:

| Extension | Description |
|---|---|
| `.exe`, `.dll`, `.com` | Executable files |
| `.bat`, `.cmd` | Batch and command scripts |
| `.jar`, `.java` | Java archives and source |
| `.php` | PHP scripts |
| `.vbs` | VBScript files |
| `.reg` | Registry files |
| `.pif`, `.lnk` | Shortcut files |
| `.bak` | Backup files |
| `.nat` | NAT files |
| `.docm` | Macro-enabled Word documents |

### Folder Selection in Firefox

The web application file selector for asset uploads supports folder selection in Chromium-based browsers but does not support folder selection in Mozilla Firefox. Individual file selection works in all supported browsers.

---

## Deployment Limitations

### AWS GovCloud Restrictions

When deploying to AWS GovCloud (US) regions, the following services are not available:

| Feature | Restriction |
|---|---|
| Amazon CloudFront | Not available; use ALB deployment mode |
| Amazon Location Service | Not available; map features are disabled |
| Amazon Cognito Advanced Security | Not available; security check is suppressed |


### Simultaneous CloudFront and ALB Deployment

VAMS supports either Amazon CloudFront or Application Load Balancer for web hosting, but not both simultaneously. Attempting to enable both results in a configuration validation error. You may also deploy with neither enabled for an API-only deployment (no web interface).

### VPC Subnet IP Requirements

When using a VPC, each subnet must have sufficient IP addresses for all deployed Lambda functions and VPC endpoints. The number of required IPs scales with the number of enabled pipelines and services. Refer to the [Configuration Guide](../deployment/configuration-reference.md) for subnet sizing guidance.
