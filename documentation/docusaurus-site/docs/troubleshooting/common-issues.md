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

The 3D Basic Conversion and CAD/Mesh Metadata Extraction pipelines deploy as AWS Lambda container images, which AWS CDK builds with Docker and pushes to Amazon ECR during `npx cdk deploy`. Pushing one of these images or creating its function can fail with `failed commit on ref "manifest-sha256:..."` or `Lambda function XXX reached terminal FAILED state due to InvalidImage`.

**Symptoms:**

-   `unexpected status from PUT request to https://....dkr.ecr.REGION.amazonaws.com/v2/foo/manifests/bar: 400 Bad Request`
-   `InvalidImage(ImageLayerFailure: UnsupportedImageLayerDetected)` or `The image manifest, config or layer media type for the source image ... is not supported` while AWS CloudFormation creates the pipeline's Lambda function
-   The conversion pipeline nested stack fails and the core stack rolls back

**Cause:**

AWS Lambda accepts a container image only as a single-platform image manifest. When Docker stores images in the containerd image store, the default in Docker Desktop 4.34 and later and in new installations of Docker Engine 29 and later, a build adds a provenance attestation by default and the image is pushed as an OCI image index, which Lambda rejects. The AWS CDK CLI version pinned in `infra/package.json` turns that default off for every image it builds: it runs the build with `BUILDX_NO_DEFAULT_ATTESTATIONS=1` regardless of the value in your shell. A deployment run with `npx cdk` from the `infra/` directory therefore pushes a single manifest.

When the error appears anyway, the image index is already cached. AWS CDK tags each image with a hash of its source, and it skips the build and the push when an image with that tag exists, first in the bootstrap container assets repository in Amazon ECR and then as the local Docker image `cdkasset-<hash>`. An image index pushed by an earlier attempt, for example one built by another AWS CDK CLI version or outside the AWS CDK CLI, is reused by every later deployment until its tag is deleted.

**Resolution:**

1. Run the deployment from the `infra/` directory with `npx cdk` so that the pinned AWS CDK CLI builds the images. `npx cdk --version` shows the version in use.
2. List the asset hashes of the two conversion images. In `cdk.out/*.assets.json`, written by the failed deployment or by `npx cdk synth`, each image under `dockerImages` is keyed by its hash, which this command (run from the `infra/` directory) prints on the line before the image's `displayName`:

    ```bash
    grep -B1 -E 'vamsExecute(3dBasic|MeshCadMetadataExtraction)Conversion/AssetImage' cdk.out/*.assets.json
    ```

3. Delete those image tags from the container assets repository. With the default bootstrap qualifier, the repository is named `cdk-hnb659fds-container-assets-<account>-<region>`:

    ```bash
    aws ecr batch-delete-image \
        --repository-name cdk-hnb659fds-container-assets-<account>-<region> \
        --image-ids imageTag=<hash>
    ```

4. Remove the local copies, so AWS CDK builds the images again instead of pushing the cached ones:

    ```bash
    docker image rm cdkasset-<hash>
    ```

5. Deploy again with `npx cdk deploy --all --require-approval never`.

Removing these two images is sufficient; clearing the whole Docker cache is not required.

:::note[ARM64 hosts]
Every VAMS container image is pinned to `linux/amd64`. Docker Desktop on macOS and Windows includes the emulation that builds these images on an ARM64 host such as a Mac with Apple silicon, so no emulation setup is required. On an ARM64 Linux host running Docker Engine, install amd64 emulation as described in the Docker [multi-platform builds](https://docs.docker.com/build/building/multi-platform/) guide.
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
npx cdk deploy --all --require-approval never --context loadContextIgnoreVPCStacks=true

# Phase 2: Deploy all stacks including VPC-dependent ones
npx cdk deploy --all --require-approval never
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

### SuperSplat Editor Reports That WebGPU Is Required

The SuperSplat Editor renders through WebGPU and has no WebGL fallback.

**Symptoms:**

-   Opening a `.ply`, `.sog`, `.splat`, or `.lcc` file in the SuperSplat Editor shows the message "SuperSplat requires WebGPU, which this browser does not support" instead of the editor
-   The BabylonJS and PlayCanvas Gaussian Splat viewers render the same `.ply` and `.sog` files

**Resolution:**

Use a browser with WebGPU enabled: current Google Chrome or Microsoft Edge, Safari 26 or later, or Mozilla Firefox where WebGPU is enabled. Where a browser policy disables WebGPU or the machine has no supported GPU driver, open `.ply` files with the BabylonJS or PlayCanvas Gaussian Splat viewer and `.sog` files with the PlayCanvas Gaussian Splat viewer instead; `.lcc` and `.splat` files have no alternative viewer.

### Point Cloud or 3D Tileset Viewer Reports a Load Failure

The Potree Viewer and the Cesium 3D Tileset Viewer stream their files from the API, and each names the cause when a file fails to load.

**Symptoms:**

-   The Potree Viewer shows "Potree Viewer Auxiliary Preview files not currently available" with a prompt to run the Point Cloud Potree Viewer Pipeline
-   Either viewer shows a message beginning "Unable to load" or "Unable to reach" instead of the model, or the Potree Viewer shows one beginning "Unable to display"
-   The Cesium 3D Tileset Viewer shows **Error Loading 3D Tileset** with a message beginning "Tileset loading failed for", or with "N of N tilesets failed to load" when every selected tileset failed
-   The Cesium 3D Tileset Viewer renders the scene under an **Incomplete 3D Tileset** banner

**Resolution:**

| Message                                                                          | Cause and resolution                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Preview files not currently available, with the pipeline prompt                  | The point cloud has no Potree preview files. Run the Point Cloud Potree Viewer Pipeline on the file.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `Request blocked by the deployment's AWS WAF web ACL (request ID ...)`           | A web ACL rule blocked the request. Find the request by its ID in the web ACL's log group to see the rule that matched (see [AWS WAF and Rate Limiting](../architecture/security.md#aws-waf-and-rate-limiting)).                                                                                                                                                                                                                                                                                                                                                                      |
| `Unable to reach ...`                                                            | The request failed before VAMS answered it, so the browser reports no status: a network failure, Amazon API Gateway throttling of the browser's CORS preflight (`OPTIONS`) request, or an AWS WAF block of the preflight by a rule on the request's URI path or by the rate-based rule. Each file and tile request has its own preflight. Look for an `OPTIONS` request to the file's path at that time in the REST API access log group, where a `429` status is throttling (see [429 Rate Limiting](#429-rate-limiting)), and, when AWS WAF is enabled, in the web ACL's log group. |
| `Unable to display ...`                                                          | The file downloads, but the Potree Viewer could not read it. Check the file, or run the Point Cloud Potree Viewer Pipeline on it again to regenerate the preview files.                                                                                                                                                                                                                                                                                                                                                                                                               |
| `Tileset loading failed for ...`                                                 | Cesium could not use the tileset for a reason other than a failed request, such as tileset JSON it cannot read. The text after the colon is Cesium's message. Check the tileset file.                                                                                                                                                                                                                                                                                                                                                                                                 |
| `N of N tilesets failed to load`                                                 | Every selected tileset failed to load. The message goes on to name the cause of the first failure, which has its own row in this table.                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `you don't have permission to view it`                                           | The role cannot read the file. An administrator grants the role access to the file's database or asset.                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `Asset is not currently allowing file distribution` or `Asset not distributable` | The asset's **Is Distributable** setting is off, so the API streams none of its files. Set **Is Distributable?** to **Yes** under **Edit** on the asset's page to view the file.                                                                                                                                                                                                                                                                                                                                                                                                      |
| `your session is no longer valid`                                                | The sign-in expired. Sign in again.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `too many requests`                                                              | Amazon API Gateway or the web ACL's rate-based rule throttled the request. See [429 Rate Limiting](#429-rate-limiting).                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Incomplete 3D Tileset** banner                                                 | Some tiles or tilesets failed to load while the rest of the scene loaded. The banner counts the tilesets that failed and names the cause of the first, and counts the failed tile requests and shows Cesium's message for the last one, such as `Request has failed. Status Code: 403`.                                                                                                                                                                                                                                                                                               |

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

### Deadline Cloud Jobs Never Start and Workers Are Replaced Repeatedly

A `DeadlineCloud` pipeline execution hangs, and the Deadline Cloud fleet keeps launching and discarding workers. The usual cause is the fleet role missing Amazon CloudWatch Logs write permission — the AWS managed `AWSDeadlineCloud-FleetWorker` policy does not include it.

**Symptoms:**

-   The VAMS execution stays in a running state until it times out, with no error reported by VAMS
-   The farm, fleet, and queue all report `ACTIVE`, and the Deadline Cloud job was created successfully
-   The job's task never leaves its pending state; jobs that are cancelled show `CANCELED` having never run
-   The fleet's worker-not-responding alarm fires, and the fleet replaces its instance every few minutes
-   The worker agent log records `AccessDeniedException` on `UpdateWorker` with `does not have sufficient access to perform CreateLogStream`

**Resolution:**

Add the two log actions to the fleet role, alongside the worker permissions it already holds:

```json
{
    "Effect": "Allow",
    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
    "Resource": [
        "arn:aws:logs:*:<account-id>:log-group:/aws/deadline/*",
        "arn:aws:logs:*:<account-id>:log-group:/aws/deadline/*:log-stream:*"
    ]
}
```

Submit a job again and confirm its task reaches `SUCCEEDED`. A worker on a scale-to-zero fleet is created, runs, and is released quickly, so it may not be observable in `ListWorkers` between polls — the job's terminal status is the reliable signal, not catching the worker in a particular state.

:::warning
Because the alarm names worker responsiveness rather than an IAM permission, this reads as a capacity or instance-availability problem. Check the fleet role's log permissions before requesting quota increases or changing instance types.
:::

See [Configuration reference](../deployment/configuration-reference.md) for the full list of operator-supplied Deadline Cloud prerequisites.

### 3D Preview Thumbnail Run Fails on a Large or Complex File

**Symptoms:**

-   The execution fails with a message such as `does not fit on the work volume`, `Out of memory loading the 3D file`, `FBX scene places more than`, `FBX file nests deeper than 256 levels`, `STEP assembly places more than 1,000,000 components`, `splat-transform did not finish within 1800 s`, `has an implausible compression ratio`, or `LCC2 chunk file`

**Resolution:**

These are fixed limits of the pipeline (see [Quotas](../additional/quotas.md)). Reduce or split the input; for an FBX file, export it as FBX 2011 (7.1) or later. For an `.lcc2` scene, upload its `data/3dgs/` folder beside the `.lcc2` file in the same asset. The AWS Batch job log in Amazon CloudWatch records the input size, the render budgets and each phase's duration.

### Pipeline Timeout vs. Workflow Timeout

Pipeline step timeouts and overall workflow timeouts are configured separately. A pipeline step that exceeds its timeout will cause the workflow to fail.

**Symptoms:**

-   Workflow execution fails but logs show the container was still processing
-   AWS Step Functions execution history shows a timeout error on a specific state

**Resolution:**

Adjust the timeout values in the pipeline or workflow configuration. Large files may need more than the 1-hour job limit of the 3D Preview Thumbnail pipeline. Monitor the AWS Batch job logs and AWS Step Functions execution history to determine appropriate timeout values.

:::tip
For long-running pipeline operations, check the AWS Batch job logs in Amazon CloudWatch rather than relying solely on the API response or web UI status.
:::
