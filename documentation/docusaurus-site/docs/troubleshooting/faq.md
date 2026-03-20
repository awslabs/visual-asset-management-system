# Frequently Asked Questions

This page answers common questions about using, configuring, and extending the Visual Asset Management System (VAMS).

---

## User Management

### How do I reset my password?

The password reset process depends on your authentication provider:

-   **Amazon Cognito (default):** An administrator can reset your password through the VAMS web interface under **Admin - Auth > User Management**, or by using the VamsCLI command `vamscli user cognito reset-password`. You will receive an email with a temporary password.
-   **External OAuth Identity Provider:** Password management is handled by your external identity provider. Contact your organization's identity administrator.

:::note
The User Management page is only visible when Amazon Cognito authentication is active.
:::

### How do I add a new user?

**Using the web interface:**

1. Navigate to **Admin - Auth > User Management**.
2. Select **Create User**.
3. Enter the user's email address and any required attributes.
4. The user receives an email with a temporary password.

**Using the VamsCLI:**

```bash
vamscli user cognito create --email user@example.com
```

**Using the API:**

```
POST /user/cognito
```

After creating the user, assign them to a role under **Admin - Auth > User Roles** to grant appropriate permissions.

### Why can I not see certain navigation pages?

VAMS uses a two-tier permission system. Navigation pages are filtered based on your assigned role constraints. If a page is missing from your navigation menu, your role does not include a `web` object type constraint that grants access to that route. Contact your VAMS administrator to review your role assignments and constraints.

:::tip
If no navigation items appear at all, a message will display indicating that your user does not have permissions to view any web navigation pages.
:::

---

## Asset Management

### How do I make an asset downloadable?

When creating or editing an asset, set the **isDistributable** flag to `true`. This flag controls whether download operations are permitted for the asset. Assets with `isDistributable` set to `false` will not generate presigned download URLs.

### How do I import existing Amazon S3 data?

VAMS can register assets from existing Amazon S3 buckets in two ways:

1. **External asset buckets:** Configure `app.assetBuckets.externalAssetBuckets` in `infra/config/config.json` with the bucket ARN, base assets prefix, and default sync database ID. VAMS will index existing objects as assets during deployment.

2. **Direct Amazon S3 placement:** Place files into a VAMS-managed asset bucket under the correct prefix structure (`{assetId}/{files}`). The Amazon S3 bucket sync process will detect new objects and create corresponding asset records.

:::warning
When importing from external buckets, ensure asset IDs do not conflict with existing assets in other databases. See [Known Limitations](known-limitations.md#asset-id-conflicts-across-databases-with-multiple-amazon-s3-buckets) for details.
:::

### How do I back up VAMS data?

VAMS data is stored across Amazon DynamoDB tables and Amazon S3 buckets. To back up your data:

-   **Amazon DynamoDB:** Enable point-in-time recovery (PITR) on VAMS DynamoDB tables, or use AWS Backup to create scheduled backups.
-   **Amazon S3:** Enable versioning on asset buckets (enabled by default) and configure Amazon S3 Lifecycle rules or AWS Backup for cross-region replication if needed.
-   **Amazon OpenSearch:** Use OpenSearch snapshot and restore for search index backups, though indexes can be rebuilt from DynamoDB data using the re-index tool.

For full environment migration, use the data migration scripts provided in `infra/deploymentDataMigration/`.

---

## Search and Filtering

### Can I use VAMS without Amazon OpenSearch?

Yes. Amazon OpenSearch is optional. When neither OpenSearch Serverless nor OpenSearch Provisioned is enabled:

-   The `NOOPENSEARCH` feature flag is set automatically.
-   The web application hides search-specific UI elements.
-   Asset and file listing uses Amazon DynamoDB queries with pagination instead of full-text search.
-   Advanced search features (full-text search, metadata field filtering, relevance ranking) are not available.

---

## Deployment and Configuration

### Can I deploy to AWS GovCloud?

Yes. VAMS supports AWS GovCloud (US) regions with specific configuration requirements:

1. Set `app.govCloud.enabled: true` in `infra/config/config.json`.
2. Set `app.useGlobalVpc.enabled: true` (required for GovCloud).
3. Set `app.useCloudFront.enabled: false` (CloudFront is not available in GovCloud).
4. Set `app.useLocationService.enabled: false` (Location Service is not available in GovCloud).
5. Use the ALB deployment mode for the web interface.

A GovCloud-specific configuration template is provided at `infra/config/config.template.govcloud.json`.

### What file types are blocked from upload?

The following file extensions are blocked by the VAMS upload API for security reasons:

`.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`

Additionally, the following MIME types are blocked:

`application/java-archive`, `application/x-msdownload`, `application/x-sh`, `application/x-php`, `application/javascript`, `application/x-powershell`, `application/vbscript`

:::info
These restrictions apply only to the upload API. Files placed directly into Amazon S3 buckets bypass these checks.
:::

### How do I connect VAMS to my existing authentication system?

VAMS supports external OAuth 2.0 identity providers as an alternative to Amazon Cognito. To configure external authentication:

1. Set `app.authProvider.useCognito.enabled: false` in your configuration.
2. Set `app.authProvider.useExternalOAuthIdp.enabled: true`.
3. Provide the required OAuth 2.0 endpoints:
    - `idpAuthProviderUrl` -- Identity provider base URL
    - `idpAuthClientId` -- OAuth client ID
    - `idpAuthProviderTokenEndpoint` -- Token endpoint
    - `idpAuthProviderAuthorizationEndpoint` -- Authorization endpoint
    - `idpAuthProviderDiscoveryEndpoint` -- Discovery endpoint
    - `lambdaAuthorizorJWTIssuerUrl` -- JWT issuer URL for token validation
    - `lambdaAuthorizorJWTAudience` -- Expected JWT audience
4. Deploy the stack with the updated configuration.

Refer to the [Configuration Guide](../deployment/configuration-reference.md) for the complete list of required external OAuth fields.

---

## Extensibility

### How do I add a custom 3D viewer?

VAMS uses a plugin-based viewer architecture. To add a custom viewer:

1. Create a viewer plugin directory under `web/src/visualizerPlugin/viewers/YourViewerPlugin/`.
2. Implement the React component following the `ViewerPluginProps` interface.
3. Register the viewer in `web/src/visualizerPlugin/config/viewerConfig.json` with its supported extensions, priority, and category.
4. Add the component path to `web/src/visualizerPlugin/viewers/manifest.ts`.
5. If the viewer has external dependencies, add a custom install script under `web/customInstalls/`.

Refer to [Viewer Plugins](../additional/viewer-plugins.md) for the complete viewer reference and the plugin README at `web/src/visualizerPlugin/README.md` for development details.

### How do I create a custom pipeline?

To create a custom processing pipeline:

1. Create a directory under `backendPipelines/{useCase}/` with `lambda/` and optionally `container/` subdirectories.
2. Implement the `vamsExecute` Lambda handler to receive workflow payloads.
3. Implement the `constructPipeline` Lambda to build the container job definition.
4. Create a CDK nested stack under `infra/lib/nestedStacks/pipelines/`.
5. Add the pipeline configuration to `infra/config/config.ts` under the `pipelines` section.
6. Register the pipeline in the pipeline builder nested stack.
7. Add the pipeline feature flag to the VPC builder if it requires AWS Batch or Amazon ECR endpoints.

VAMS pipelines support three execution types: **Lambda** (synchronous or asynchronous), **Amazon SQS** (asynchronous), and **Amazon EventBridge** (asynchronous). Refer to the [Developer Guide](../developer/setup.md) for detailed pipeline development instructions.
