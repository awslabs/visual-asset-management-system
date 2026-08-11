# Common Issues

This page covers frequently encountered issues during deployment, web application usage, API interaction, and pipeline execution for Visual Asset Management System (VAMS).

---

## CDK Deployment Errors

### Amazon ECS VPC Endpoint Conflicts

When redeploying with pipeline configuration changes, you may encounter AWS CloudFormation errors related to Amazon ECS VPC interface endpoints. This occurs when Amazon ECS endpoint changes conflict with AWS CloudFormation stack change restrictions.

**Symptoms:**

-   CloudFormation stack rollback with VPC endpoint creation failures
-   Errors referencing duplicate interface endpoints for Amazon ECS

**Resolution:**

1. Temporarily disable the affected pipelines (Isaac Lab Training, Gaussian Splat Toolbox) in `infra/config/config.json`.
2. Deploy with pipelines disabled: `cdk deploy --all --require-approval never`.
3. Re-enable the pipelines in the configuration file.
4. Deploy again: `cdk deploy --all --require-approval never`.

:::tip
This issue typically occurs only when toggling multiple pipelines simultaneously. Deploying pipeline changes incrementally can help avoid it.
:::

### Subnet or VPC Resource Deletion Failures

When you disable a feature that required private or public subnets (for example, turning off a pipeline, the ALB public subnet, or reducing the Amazon OpenSearch Service availability zone count), AWS CloudFormation may fail to remove the now-unused subnets and their associated networking resources.

**Symptoms:**

-   `DELETE_FAILED` on subnet, route table, NAT gateway, or Elastic IP resources
-   Errors stating a subnet has a dependency or that a network interface is still in use
-   `UPDATE_ROLLBACK_FAILED` on the VPC nested stack after a configuration downgrade

**Cause:**

A subnet cannot be deleted while any elastic network interface (ENI) remains attached to it. VAMS attaches ENIs to subnets from several sources: interface VPC endpoints, NAT gateways, and—when `useGlobalVpc.useForAllLambdas` is enabled—every Lambda function placed in the VPC. When a feature is disabled, AWS CloudFormation attempts to delete the orphaned subnets before all dependent ENIs have detached, and the deletion fails. Changing the availability zone count or the set of subnet types also re-partitions the VPC CIDR, which compounds the issue because a subnet's CIDR cannot be changed in place.

**Resolution:**

Perform a staged "drain, redeploy, re-enable" procedure so the dependent ENIs are removed before the subnets are deleted:

1. In `infra/config/config.json`, disable everything that requires VPC networking so the dependent ENIs are torn down. This includes:
    - Amazon OpenSearch Service provisioned (`openSearch.useProvisioned.enabled = false`)
    - All pipelines that use AWS Batch, Amazon ECS, or Amazon EKS (Splat Toolbox, Isaac Lab Training, NVIDIA Cosmos, NVIDIA Gr00t, ModelOps, RapidPipeline, point cloud / 3D thumbnail / GenAI labeling previews)
    - `useGlobalVpc.useForAllLambdas = false` so Lambda functions are removed from the VPC (this releases the Lambda Hyperplane ENIs that most commonly block subnet deletion)
    - The ALB public subnet if it is being removed (`useAlb.usePublicSubnet = false`), or the ALB itself if the web tier is changing
2. Remove any Lambda functions and their VPC ENIs. Deploy with networking disabled so AWS CloudFormation detaches the Lambda functions from the VPC and deletes their ENIs:

    ```bash
    cd infra && cdk deploy --all --require-approval never
    ```

    If a Lambda ENI lingers (Lambda Hyperplane ENIs can take several minutes to detach after the function is removed from the VPC), wait for it to clear, or delete the orphaned ENI manually from the Amazon EC2 console (**Network Interfaces**, filter by the VPC, delete interfaces in the `available` state) before continuing.

3. Re-enable the features you need in `infra/config/config.json`, including any new subnet or availability-zone configuration (for example, set `openSearch.useProvisioned.availabilityZoneCount` to `2` for a two-AZ Region such as `eusc-de-east-1`).
4. Deploy again so the VPC is provisioned with the new subnet layout:

    ```bash
    cd infra && cdk deploy --all --require-approval never
    ```

5. If Amazon OpenSearch Service was disabled and re-enabled, re-index the search data. The domain is created empty, so run the re-index tool (or set `openSearch.reindexOnCdkDeploy = true` for one deployment). See [Reindexing OpenSearch](../developer/utilities/reindex.md).

:::note[No data loss]
Disabling Amazon OpenSearch Service and re-enabling it does not lose asset data. Search data is rebuilt from the authoritative Amazon DynamoDB tables and Amazon S3 buckets by the re-index process; only the search index itself is recreated.
:::

:::tip
To minimize the chance of these failures, keep the availability zone count and subnet types stable across deployments. VAMS provisions a fixed AZ count per subnet type so that toggling individual pipelines does not add or remove subnets. The largest source of subnet churn is changing `openSearch.useProvisioned.availabilityZoneCount` or switching `useAlb.usePublicSubnet`, both of which change the subnet set.
:::

### Docker Buildx Container Image Errors

When deploying with AWS CDK using Docker, you may encounter errors related to container image builds, particularly `failed commit on ref "manifest-sha256:..."` or `Lambda function XXX reached terminal FAILED state due to InvalidImage`.

**Symptoms:**

-   `unexpected status from PUT request to https://....dkr.ecr.REGION.amazonaws.com/v2/foo/manifests/bar: 400 Bad Request`
-   `InvalidImage(ImageLayerFailure: UnsupportedImageLayerDetected)` errors during Lambda function creation
-   Container images fail to push to Amazon ECR

**Resolution:**

This is a known issue with certain Docker buildx versions. Set the following environment variable before running `cdk deploy`:

```bash
export BUILDX_NO_DEFAULT_ATTESTATIONS=1
```

Additionally, if deploying from an ARM64 machine (such as Apple Silicon Mac), you may need to clear the Docker cache and configure cross-platform emulation:

```bash
# Clear Docker cache
docker system prune -a

# Set cross-platform emulation
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

:::tip
Set `BUILDX_NO_DEFAULT_ATTESTATIONS=1` permanently in your shell profile (`.bashrc`, `.zshrc`) to avoid repeating this step on every deployment.
:::

:::note[Alternative container engines]
If you are using [Finch](https://aws.github.io/finch/) or [Podman](https://podman.io/) instead of Docker, buildx-specific issues may not apply. However, you may encounter different container build errors specific to your engine. Verify the issue reproduces with Docker before reporting. See the [Prerequisites](../deployment/prerequisites.md#docker-alternatives) page for alternative container engine setup.
:::

### Web Build or Infrastructure CDK Errors

After upgrading VAMS or switching branches, stale dependencies can cause build failures.

**Symptoms:**

-   TypeScript compilation errors in `web/` or `infra/`
-   Module resolution failures during `cdk synth`

**Resolution:**

```bash
# Clear and reinstall web dependencies
cd web && rm -rf node_modules && npm install && npm run build

# Clear and reinstall infrastructure dependencies
cd infra && rm -rf node_modules && npm install
```

:::note
Always run `npm install` in both the `web/` and `infra/` directories after pulling new code or switching branches.
:::

### External VPC Import Failures

When importing an external Amazon VPC with subnets, the first deployment attempt may fail because AWS CDK cannot resolve VPC context before stack synthesis.

**Symptoms:**

-   VPC or subnet lookup errors during `cdk synth`
-   Stack deployment fails referencing missing VPC context

**Resolution:**

Perform a two-phase deployment:

```bash
# Phase 1: Import VPC context and deploy non-VPC stacks
cdk deploy --all --require-approval never --context loadContextIgnoreVPCStacks=true

# Phase 2: Deploy all stacks including VPC-dependent ones
cdk deploy --all --require-approval never
```

Alternatively, set `loadContextIgnoreVPCStacks: true` in `infra/config/config.json` for the first deployment, then set it back to `false` for subsequent deployments.

### AWS KMS Key Permission Errors

AWS KMS key policy errors can occur when AWS CloudFormation custom resources attempt to modify Amazon S3 or Amazon DynamoDB tables encrypted with a customer-managed KMS key.

**Symptoms:**

-   Custom resource Lambda functions fail with `AccessDeniedException` for AWS KMS operations
-   Stack deployment rolls back during default data population steps

**Resolution:**

Verify that the KMS key policy includes the required principals. If using an external CMK via `app.useKmsCmkEncryption.optionalExternalCmkArn`, ensure the key policy grants the following actions to the AWS CloudFormation service principal and the deployment role:

-   `kms:Decrypt`
-   `kms:Encrypt`
-   `kms:GenerateDataKey`
-   `kms:ReEncrypt*`

### Amazon OpenSearch Provisioned Domain Update Failures

Updating a provisioned Amazon OpenSearch Service domain can fail when the requested domain configuration is incompatible with the existing indexes.

**Symptoms:**

-   Stack enters `UPDATE_ROLLBACK_FAILED` referencing the OpenSearch domain and the indexer / search / reindexer resources
-   Domain update rejected with `Cluster.Index.ReplicaCount: The following indices do not have multiples of 3 copies ... Index should have 3 or multiples of 3 copies including primary and replica for 3-AZ with Standby`

**Cause:**

Amazon OpenSearch Service Multi-AZ with Standby requires every index to have copies (primary plus replicas) in a multiple of three. VAMS enables Multi-AZ with Standby only when `availabilityZoneCount` is `3`, and in that case creates the asset and file indexes with two replicas (three copies). The error occurs when an **existing** domain that already holds single-copy indexes (a prior 2-AZ deployment) is switched to `availabilityZoneCount: 3` in place: AWS CloudFormation validates the new Standby domain configuration against the existing indexes before their replica count can be raised, and rejects it.

**Resolution:**

1. Recover the stuck stack: in the AWS CloudFormation console, choose **Continue update rollback** on the `UPDATE_ROLLBACK_FAILED` stack, skipping the OpenSearch domain and dependent indexer/search resources if prompted, so the stack returns to `UPDATE_ROLLBACK_COMPLETE`.
2. To stay on the existing domain unchanged, set `app.openSearch.useProvisioned.availabilityZoneCount` back to `2` (the default) and redeploy: `cd infra && cdk deploy --all --require-approval never`.
3. To move to a 3-AZ Multi-AZ-with-Standby domain, create it fresh: deploy once with OpenSearch disabled (`useProvisioned.enabled = false`) to remove the existing domain, then re-enable it with `availabilityZoneCount: 3` and deploy again. Run the reindex tool (or set `reindexOnCdkDeploy = true` for one deployment) to repopulate the new domain from source data — no asset data is lost.

:::note
A fresh 3-AZ domain is created as Multi-AZ with Standby with three data nodes and three copies of each index. A 2-AZ domain runs without Standby with two data nodes and a single copy. Leave `availabilityZoneCount` at its default of `2` to keep an existing domain unchanged.
:::

---

## Web Application Issues

### Content Security Policy Errors in Local Development

During local development, Content Security Policy (CSP) errors may block certain viewer functionality or API calls.

**Symptoms:**

-   Browser console errors mentioning `Content-Security-Policy`
-   Viewers fail to load external resources
-   WebAssembly modules blocked

**Resolution:**

VAMS includes a service worker that sets the required cross-origin isolation headers. Ensure the service worker is registered by verifying:

1. The development server is running on `https://` or `localhost`.
2. The browser has not blocked the service worker registration.
3. For WASM-based viewers, verify `allowUnsafeEvalFeatures` is enabled in the deployment configuration if testing against a deployed backend.

:::info
The Vite development server proxy handles most CSP issues automatically. If problems persist, clear your browser cache and service worker registrations.
:::

### WASM-Based Viewers Not Loading

Viewers that use WebAssembly (Needle USD Viewer, Three.js CAD Viewer, Cesium 3D Tileset Viewer, ThatOpen IFC BIM Viewer, SuperSplat Editor) require specific HTTP headers to function.

**Symptoms:**

-   Viewer shows a loading spinner indefinitely
-   Browser console errors mentioning `SharedArrayBuffer` or `Cross-Origin-Opener-Policy`

**Resolution:**

WASM-based viewers require Cross-Origin Isolation headers (`Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`). These are provided in two ways:

-   **Amazon CloudFront deployment:** Headers are set automatically by the CloudFront distribution.
-   **Application Load Balancer (ALB) deployment:** A front-end service worker attempts to set the headers. If your organization's security policy blocks service workers, WASM viewers will not function.

For the Needle USD Viewer, the SuperSplat Editor, the ThatOpen IFC BIM Viewer, and Three.js CAD formats, you must also enable `allowUnsafeEvalFeatures` in `infra/config/config.json` because their WASM loaders require runtime code generation.

### Safari Limitations for WASM Viewers

Safari does not fully support the cross-origin isolation requirements needed by certain WASM-based viewers.

**Symptoms:**

-   Needle USD Viewer, Three.js CAD formats (.stp, .step, .iges, .brep), and Cesium Viewer fail to load in Safari
-   Standard mesh formats (.gltf, .glb, .obj, .stl) in the Three.js Viewer work correctly

**Resolution:**

Use a Chromium-based browser (Google Chrome, Microsoft Edge) or Mozilla Firefox for WASM-dependent viewers. Non-WASM viewers and standard mesh formats work in all supported browsers.

### Login Loop or Configuration Fetch Failures

Users may experience a login loop where the application repeatedly redirects to the sign-in page.

**Symptoms:**

-   Page refreshes continuously after successful authentication
-   Browser console shows errors fetching `/api/amplify-config` or `/api/secure-config`

**Resolution:**

1. Clear your browser cache and local storage for the VAMS domain.
2. Verify the API Gateway endpoint is accessible from your network.
3. If using IP range restrictions (`authorizerOptions.allowedIpRanges`), confirm your IP address is within an allowed range.
4. For external OAuth identity provider configurations, verify all endpoint URLs in the configuration are correct and reachable.

---

## API Issues

### 429 Rate Limiting

VAMS applies API rate limiting through Amazon API Gateway throttling.

**Symptoms:**

-   API responses return HTTP 429 (Too Many Requests)
-   Bulk operations fail intermittently

**Resolution:**

Increase the rate limits in `infra/config/config.json`:

```json
{
    "app": {
        "api": {
            "globalRateLimit": 100,
            "globalBurstLimit": 200
        }
    }
}
```

The default values are `globalRateLimit: 50` requests per second and `globalBurstLimit: 100`. Redeploy after changing these values.

:::warning
Increasing rate limits raises the potential cost of Amazon API Gateway usage and may affect downstream service limits. Monitor your Amazon CloudWatch metrics after adjustments.
:::

### Timeout on Large Operations

Amazon API Gateway returns a timeout once the configured integration timeout elapses (`app.api.apiGatewayRest.apiGatewayTimeoutTime`, 29 seconds by default), while the underlying AWS Lambda function continues processing for up to 15 minutes.

**Symptoms:**

-   API returns a 504 Gateway Timeout
-   The operation actually completes successfully in the background

**Affected operations:**

-   Listing or exporting assets with thousands of files
-   Amazon OpenSearch re-indexing for large datasets
-   Bulk metadata operations

**Resolution:**

For operations that may exceed the integration timeout, check your AWS Lambda function logs in Amazon CloudWatch to confirm whether the operation completed. The VamsCLI provides automatic pagination and retry logic that handles timeout scenarios for bulk operations.

Deployments that regularly run these operations can raise `app.api.apiGatewayRest.apiGatewayTimeoutTime` (up to 300 seconds) so the request completes synchronously instead of returning a 504. Values above 29 seconds require an approved account-level **Integration timeout** quota increase (`L-E5AE38E3`) in the deployment Region before deploying — see the [configuration reference](../deployment/configuration-reference.md).

### Amazon OpenSearch Indexing Delays After Bulk Operations

After uploading many files or performing bulk metadata changes, search results may not immediately reflect the updates.

**Symptoms:**

-   Newly uploaded assets do not appear in search results
-   Metadata changes are not reflected in search filters

**Resolution:**

Amazon OpenSearch indexing is asynchronous. After bulk operations, allow 30-60 seconds for indexing to complete. If indexing appears stuck:

1. Check the Amazon CloudWatch logs for the indexing Lambda functions.
2. Verify the Amazon OpenSearch cluster health in the AWS Management Console.
3. If necessary, trigger a re-index by setting `reindexOnCdkDeploy: true` in the configuration and redeploying, or by using the manual re-index tool in `infra/deploymentDataMigration/`.

---

## Pipeline Issues

### Container Pull Failures

Pipeline containers running on AWS Batch with AWS Fargate may fail to pull container images from Amazon Elastic Container Registry (Amazon ECR).

**Symptoms:**

-   AWS Batch job fails with `CannotPullContainerError`
-   Timeout errors during image pull

**Resolution:**

Pipeline containers require network access to Amazon ECR endpoints. Verify:

1. The VPC has the required VPC endpoints for Amazon ECR (`com.amazonaws.region.ecr.api` and `com.amazonaws.region.ecr.dkr`) and Amazon S3.
2. If using pipelines that require internet access (such as RapidPipeline or ModelOps), ensure the VPC has NAT Gateway or public subnet access configured.
3. Check that the security groups attached to AWS Batch compute environments allow outbound HTTPS traffic.

### GPU Instance Unavailability for AWS Batch Jobs

Some pipelines (Isaac Lab Training, Gaussian Splat Toolbox) require GPU instances that may not be available in all AWS Regions or Availability Zones.

**Symptoms:**

-   AWS Batch jobs remain in `RUNNABLE` state indefinitely
-   No compute environment instances are launched

**Resolution:**

1. Verify GPU instance type availability in your AWS Region (e.g., `g6e.2xlarge`, `g5.xlarge`).
2. Request a service quota increase for the required instance types through the AWS Service Quotas console.
3. For Isaac Lab Training, consider enabling the `keepWarmInstance` option to reduce cold start times at the cost of continuous compute charges.

### Pipeline Timeout vs. Workflow Timeout

Pipeline step timeouts and overall workflow timeouts are configured separately. A pipeline step that exceeds its timeout will cause the workflow to fail.

**Symptoms:**

-   Workflow execution fails but logs show the container was still processing
-   AWS Step Functions execution history shows a timeout error on a specific state

**Resolution:**

Adjust the timeout values in the pipeline or workflow configuration. Large files (approaching the 100 GB limit for the 3D Thumbnail pipeline) may require extended processing time. Monitor the AWS Batch job logs and AWS Step Functions execution history to determine appropriate timeout values.

:::tip
For long-running pipeline operations, check the AWS Batch job logs in Amazon CloudWatch rather than relying solely on the API response or web UI status.
:::
