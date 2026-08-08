# Update the solution

This page describes how to apply updates to an existing VAMS deployment, including standard in-place updates, A/B deployment strategies for major changes, and version-specific migration steps.

## Update methods

VAMS supports two update methods depending on the scope of changes being applied.

| Method              | Use case                                                                                                          | Downtime                                                 |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| **In-place update** | Minor updates, bug fixes, feature additions within the same major version.                                        | Minimal (during AWS CloudFormation changeset execution). |
| **A/B deployment**  | Major version upgrades, KMS key changes, distribution type changes (Amazon CloudFront to ALB), region migrations. | Moderate (during data migration and DNS switchover).     |

## Pre-update checklist

Complete the following steps before applying any update.

:::danger[Always back up your data before updating]
VAMS uses `RemovalPolicy.DESTROY` on Amazon DynamoDB tables by default. An update that replaces a table will permanently delete its data.
:::

1. **Review the changelog.** Read the [CHANGELOG.md](https://github.com/awslabs/visual-asset-management-system/blob/main/CHANGELOG.md) for breaking changes, required migration scripts, and known issues for the target version.

2. **Back up DynamoDB tables.** Export critical tables using AWS Backup or on-demand exports:

    ```bash
    # Export a table to Amazon S3 using point-in-time export
    aws dynamodb export-table-to-point-in-time \
        --table-arn arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/<TABLE_NAME> \
        --s3-bucket <BACKUP_BUCKET> \
        --s3-prefix vams-backup/$(date +%Y%m%d) \
        --export-format DYNAMODB_JSON
    ```

3. **Back up S3 buckets.** Sync asset buckets to a backup location:

    ```bash
    aws s3 sync s3://<ASSET_BUCKET> s3://<BACKUP_BUCKET>/asset-backup/ \
        --profile <PROFILE>
    ```

4. **Record current stack outputs.** Save existing CloudFormation outputs for reference and potential rollback:

    ```bash
    aws cloudformation describe-stacks \
        --stack-name <VAMS_STACK_NAME> \
        --query 'Stacks[0].Outputs' \
        --output json > stack-outputs-backup.json
    ```

5. **Test in a non-production environment.** Deploy the update to a test stack first and validate functionality before applying to production.

## In-place update

Use this method for minor updates and configuration changes within the same major version.

### Step 1: Pull the latest code

```bash
git fetch --all --tags
git checkout tags/<TARGET_VERSION>
```

### Step 2: Install dependencies

```bash
cd web && npm install && npm run build && cd ..
cd infra && npm install && cd ..
```

### Step 3: Update configuration

Review and update `infra/config/config.json` with any new configuration fields introduced in the target version. New configuration fields are typically backward-compatible and receive defaults, but review the changelog for required changes.

### Step 4: Deploy the update

```bash
cd infra
npx cdk deploy --all --require-approval never
```

AWS CDK creates a CloudFormation changeset and applies only the modified resources. Resources that have not changed are left untouched.

:::info[Changeset behavior]
CloudFormation changesets update, replace, or delete resources based on the type of change. Property updates that require replacement (such as changing a DynamoDB table's partition key) result in the old resource being deleted and a new one created. Review the `cdk diff` output before deploying to understand which resources will be affected.

```bash
npx cdk diff
```

:::

### Step 5: Post-update verification

1. Confirm the stack deployed successfully in the AWS CloudFormation console.
2. Navigate to the VAMS web interface and verify login and basic operations.
3. Check Amazon CloudWatch Logs for Lambda function errors.
4. If Amazon OpenSearch Service is enabled and the update requires reindexing, set `app.openSearch.reindexOnCdkDeploy` to `true` in `config.json` and redeploy, or run the reindex utility manually.

## A/B deployment

Use A/B deployment when the update involves changes that cannot be safely applied through a CloudFormation changeset. This method deploys a parallel VAMS stack, migrates data, and then decommissions the original stack.

### When to use A/B deployment

-   Major version upgrades with breaking DynamoDB schema changes.
-   Changing the KMS CMK encryption key.
-   Switching distribution type between Amazon CloudFront and Application Load Balancer (ALB).
-   Migrating the deployment to a different AWS Region within the same account.

### A/B deployment steps

1. **Deploy Stack B.** Use a different `baseStackName` in your `infra/config/config.json` for the new deployment.

2. **Redirect traffic.** Update DNS records to point to a temporary maintenance page.

3. **Deploy the new stack.**

    ```bash
    cd infra
    npx cdk deploy --all --require-approval never
    ```

4. **Migrate DynamoDB data.** Use the A/B migration scripts provided in `infra/deploymentDataMigration/`:

    ```bash
    cd infra/deploymentDataMigration
    pip install boto3
    python tools/VAMSDataMigration.py config/<YOUR_MIGRATION_CONFIG>.json
    ```

5. **Migrate S3 data.** Sync asset buckets from Stack A to Stack B:

    ```bash
    aws s3 sync s3://<STACK_A_ASSET_BUCKET> s3://<STACK_B_ASSET_BUCKET>
    aws s3 sync s3://<STACK_A_AUXILIARY_BUCKET> s3://<STACK_B_AUXILIARY_BUCKET>
    ```

6. **Migrate users.** If using Amazon Cognito, manually recreate users in the new user pool. Password resets may be required.

7. **Validate Stack B.** Test all VAMS functionality with the migrated data.

8. **Switch DNS.** Update DNS records to point to Stack B endpoints.

9. **Decommission Stack A.** After confirming Stack B is stable, destroy Stack A following the [uninstall procedure](uninstall.md).

:::warning[ALB deployment consideration]
When using the ALB configuration, the web application S3 bucket is named after the domain. This creates a naming conflict during A/B deployment. You must delete the web app bucket from Stack A before deploying Stack B with the same domain, then restore the bucket contents after deployment.
:::

## Version-specific migration instructions

Each major version upgrade may require data migration scripts to transform DynamoDB schemas or reindex Amazon OpenSearch Service. The following sections document required migrations for each version path.

### v2.2 to v2.3

**Breaking changes:**

-   API Gateway authorizers replaced with custom Lambda authorizers.
-   AWS Batch Fargate CDK construct naming changed for pipeline stacks.
-   Amazon OpenSearch Service indexes replaced with new dual-index schema (assets and files).

**Required migration steps:**

1. Deploy the v2.3 CDK stack.
2. Run the OpenSearch reindex script to populate the new indexes:

    ```bash
    cd infra/deploymentDataMigration/v2.2_to_v2.3/upgrade
    ```

3. Optionally disable and re-enable batch pipelines if experiencing CDK deployment errors with Amazon Elastic Container Service (Amazon ECS) Fargate constructs.

:::note
If Lambda functions behind a VPC were broken in v2.2, this version restores VPC support. However, MFA for roles is not supported when all Lambda functions are behind a VPC with Amazon Cognito enabled.
:::

### v2.3 to v2.4

**Breaking changes:**

-   Permission constraints migrated to a dedicated DynamoDB table (no longer shared with auth entities).
-   Metadata and metadata schema DynamoDB tables replaced with new tables supporting multi-entity types.
-   Amazon OpenSearch Service index schemas changed for `MD_` and `AB_` fields (now flat objects).

**Required migration steps:**

1.  Deploy the v2.4 CDK stack. Default admin and read-only constraints are re-created automatically.

2.  Navigate to the migration scripts directory:

    ```bash
    cd infra/deploymentDataMigration/v2.3_to_v2.4/upgrade
    ```

3.  Copy and configure the migration configuration file:

    ```bash
    cp v2.3_to_v2.4_migration_config.json my_migration_config.json
    ```

4.  Update the configuration file with your DynamoDB table names. Retrieve table names from CloudFormation outputs:

    ```bash
    aws cloudformation describe-stacks --stack-name <VAMS_STACK_NAME> \
        --query 'Stacks[0].Outputs[?contains(OutputKey, `Table`)].{Key:OutputKey,Value:OutputValue}' \
        --output table
    ```

5.  Run the migration:

    === "Linux / macOS"

        ```bash
        chmod +x run_migration.sh
        ./run_migration.sh my_migration_config.json
        ```

    === "Windows"

        ```powershell
        .\run_migration.ps1 my_migration_config.json
        ```

6.  The migration performs the following operations:
    -   Migrates metadata from the old table to the new multi-entity metadata tables.
    -   Migrates metadata schemas to the new schema table with support for multiple entity types.
    -   Migrates permission constraints from the auth entities table to the dedicated constraints table.
    -   Reindexes Amazon OpenSearch Service with the new field schemas.

### v2.4 to v2.5

**Breaking changes:**

-   Asset version DynamoDB tables restructured with `databaseId`-prefixed composite keys to prevent cross-database collisions.
-   Website overhauled with Vite build framework, AWS Amplify v6, and dark/light theme support (may cause merge conflicts for forked repositories).

**Required migration steps:**

1.  Deploy the v2.5 CDK stack. The new V2 tables are created alongside the existing V1 tables.

2.  Navigate to the migration scripts directory:

    ```bash
    cd infra/deploymentDataMigration/v2.4_to_v2.5/upgrade
    ```

3.  Copy and configure the migration configuration file:

    ```bash
    cp v2.4_to_v2.5_migration_config.json my_migration_config.json
    ```

4.  Update the configuration file with your DynamoDB table names. The migration requires these tables:

    | Table                                   | Purpose                                               |
    | --------------------------------------- | ----------------------------------------------------- |
    | `AssetStorageTable`                     | Lookup source for `assetId` to `databaseId` mapping.  |
    | `AssetVersionsStorageTable`             | V1 source for asset versions.                         |
    | `AssetVersionsStorageTableV2`           | V2 destination for asset versions.                    |
    | `AssetFileVersionsStorageTable`         | V1 source for asset file versions.                    |
    | `AssetFileVersionsStorageTableV2`       | V2 destination for asset file versions.               |
    | `AssetFileMetadataVersionsStorageTable` | In-place backfill for new `databaseId:assetId` field. |

5.  Run the migration:

    === "Linux / macOS"

        ```bash
        chmod +x run_migration.sh
        ./run_migration.sh my_migration_config.json
        ```

    === "Windows"

        ```powershell
        .\run_migration.ps1 my_migration_config.json
        ```

6.  The migration performs five phases:
    -   **Phase 1:** Builds a lookup cache by scanning the asset storage table for `assetId` to `databaseId` mappings.
    -   **Phase 2:** Migrates asset versions from V1 to V2 with transformed key schema (`assetId` becomes `databaseId:assetId`).
    -   **Phase 3:** Migrates asset file versions from V1 to V2 with transformed key schema.
    -   **Phase 4:** Backfills the `databaseId:assetId` field on existing asset file metadata version records for the new Global Secondary Index (GSI).
    -   **Phase 5:** Verifies record counts and key structure integrity between V1 and V2 tables.

:::tip[IAM permissions for migration]
The migration requires `dynamodb:Scan` on source tables, `dynamodb:BatchWriteItem` on V2 destination tables, and `dynamodb:UpdateItem` on the metadata versions table. See the [v2.4 to v2.5 migration README](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/deploymentDataMigration/v2.4_to_v2.5/upgrade/v2.4_to_v2.5_migration_README.md) for the full IAM policy.
:::

### v2.5 to v2.6

:::info[Custom pipelines need code changes as well]
The migration steps below move stored pipeline and workflow **definitions** onto the new data model. They
cannot update the **code** of a pipeline you wrote yourself: v2.6 delivers inputs through a manifest
rather than on the payload, expects asynchronous pipelines to return a task token, and expects a pipeline
to register its sub-processes and logs so aborts and log retrieval work. See
[Migrating custom pipelines from v2.5 to v2.6](../pipelines/migrating-pipelines-v25-to-v26.md) for the
porting order and checklist. Deployments that run only VAMS built-in pipelines need nothing beyond the
steps here.
:::

**Breaking changes:**

-   The backend API moves from API Gateway HTTP API (v2) to REST API (v1), served under a stage path (default `/api`). The API Gateway identifier and invoke URL change on deployment. **Any client registered directly against the old API Gateway endpoint URL must be re-setup against the new endpoint** — re-run `vamscli setup` for the CLI, and update any external integrations or scripts that stored the API base URL. Clients that reach the API through the CloudFront or ALB front (the web application, and CLIs configured with the front's `/api` URL) continue to work without change. See [API Gateway REST API endpoint change](#api-gateway-rest-api-endpoint-change).
-   **Externally registered pipelines do not run unchanged.** The workflow, pipeline, and execution overhaul changes three things a pipeline depends on: it reads its inputs from a resolved manifest rather than from the invocation payload, an asynchronous pipeline returns a Step Functions task token for the workflow to advance past it, and it registers its sub-processes and log locations so abort and log retrieval reach them. Registration itself also moves — a definition is declared in a file-based `vamsSchema` bundle imported through the schema importer, and a pipeline is referenced by composite `pipelineDatabaseId:pipelineId`. The migration steps below reshape stored **definitions**; they cannot change a pipeline's **code**. Port every externally maintained pipeline with [Migrating custom pipelines from v2.5 to v2.6](../pipelines/migrating-pipelines-v25-to-v26.md). Deployments running only VAMS built-in pipelines need nothing beyond the steps here.
-   New OpenSearch index names: `vams-assets-v3` and `vams-files-v3`. The new mapping adds a `geo_MD_location` field of type `geo_shape` that powers the new geospatial search filter and map view. The previous v2 indexes are abandoned and remain in OpenSearch until you delete them manually.
-   Provisioned OpenSearch domains are upgraded from engine version 2.7 to 3.5. Serverless collections are reworked separately (see below).
-   OpenSearch Serverless collections are reshaped onto a next-generation collection group with new `app.openSearch.useServerless` settings (`nextGen`, `allowPublic`, `enableStandbyReplicas`, and configurable OCU capacity). The collection cannot be updated in place — it must be removed and re-created, then reindexed. See [OpenSearch Serverless next-gen upgrade](#opensearch-serverless-next-gen-upgrade).
-   The VPC is no longer enabled automatically. If a feature that requires a VPC (ALB, OpenSearch Provisioned, or any container-based pipeline) is enabled while `app.useGlobalVpc.enabled` is `false`, the deployment now fails configuration validation with an error that lists the offending features, rather than silently turning the VPC on. See [VPC is now required for certain features](#vpc-is-now-required-for-certain-features).
-   Provisioned OpenSearch `availabilityZoneCount` now defaults to `2`, and the VPC is built with exactly that many Availability Zones. Earlier releases always built the VPC across 3 Availability Zones for provisioned OpenSearch even though the domain only used 2, so on upgrade the previously-unused third AZ subnet is removed (a VPC downgrade). See [OpenSearch Provisioned Availability Zone count downgrade](#opensearch-provisioned-availability-zone-count-downgrade).
-   GPU pipeline AWS Batch compute environments move to the Amazon Linux 2023 NVIDIA-accelerated AMI (`ECS_AL2023_NVIDIA`). AWS Batch blocks creation of new Amazon ECS compute environments that use Batch-provided Amazon Linux 2 AMIs, so earlier image types fail on a new deployment. This affects the Gaussian Splat Toolbox, NVIDIA Cosmos (Predict, Reason, Transfer), Cosmos 3, GR00T, and Isaac Lab pipelines. **Each affected GPU compute environment is replaced on upgrade**, so drain or wait for in-flight GPU pipeline jobs before deploying. All supported GPU instance families (G5, G6, G6E, P4DE, P5, P5E) work with this AMI; the `P3` and `G3` families are not supported by it.

**Required migration steps:**

1.  Deploy the v2.6 CDK stack. The schema-deploy custom resource creates the empty v3 indexes; the v2 indexes are left in place but unreferenced.

2.  Navigate to the migration scripts directory:

    ```bash
    cd infra/deploymentDataMigration/v2.5_to_v2.6/upgrade
    ```

3.  Copy and configure the migration configuration file:

    ```bash
    cp v2.5_to_v2.6_migration_config.json my_migration_config.json
    ```

4.  Set `resource_names_ssm_param_prefix` in the config to the value of the CloudFormation output `ResourceNamesSSMParamPrefixOutput` from your stack, and set `aws_region` (and `aws_profile` if needed):

    ```bash
    aws cloudformation describe-stacks --stack-name your-vams-stack \
      --query 'Stacks[0].Outputs[?OutputKey==`ResourceNamesSSMParamPrefixOutput`].OutputValue' \
      --output text
    ```

    The reindexer Lambda function name is then resolved automatically from the deployment's SSM Parameter Store resource-name parameters (requires `ssm:GetParametersByPath` on the prefix). To skip or override the lookup, set `reindexer_function_name` explicitly to the value of the CloudFormation output `ReindexerFunctionNameOutput` instead.

5.  Run the migration:

    === "Linux / macOS"

        ```bash
        chmod +x run_migration.sh
        ./run_migration.sh my_migration_config.json
        ```

    === "Windows"

        ```powershell
        .\run_migration.ps1 -ConfigFile my_migration_config.json
        ```

6.  The migration invokes the deployed reindexer Lambda, which:
    -   Re-publishes every asset record from the asset storage DynamoDB table so the asset indexer writes the asset document (including the new `geo_MD_location` field) into `vams-assets-v3`.
    -   Lists every asset bucket and re-publishes file events so the file indexer writes file documents into `vams-files-v3`.
    -   Returns aggregate success/failure counts.

:::note[`--clear-indexes` defaults to false]
The v3 indexes are empty after the v2.6 CDK deploy, so the first migration run never needs to clear them. Pass `--clear-indexes` only if a previous run partially populated v3 and you want to start clean.
:::

:::warning[Provisioned OpenSearch 3.5 upgrade]
The v2.6 CDK switches `OPENSEARCH_VERSION` to `OPENSEARCH_3_5`. This applies only to provisioned deployments (`app.openSearch.useProvisioned.enabled = true`); serverless collections are unaffected. Amazon OpenSearch Service supports in-place version upgrades, but a major-version jump on a long-running domain can occasionally fail or exceed the CloudFormation custom-resource timeout.

If `cdk deploy` fails on the OpenSearch domain version upgrade, recover by deploying first with OpenSearch disabled and then re-enabling it:

1. Set `app.openSearch.useProvisioned.enabled = false` (and `useServerless.enabled = false`) in `infra/config/config.json`.
2. Run `cdk deploy --all --require-approval never` to delete the existing 2.7 domain.
3. Restore the original `useProvisioned` configuration in `config.json`.
4. Run `cdk deploy --all --require-approval never` to create a fresh 3.5 domain with the empty v3 indexes.
5. Run this migration to repopulate the v3 indexes from source data.

This recovery path discards only the OpenSearch indexes; all source data lives in DynamoDB and S3, and the reindex restores the full search corpus. For details and other troubleshooting steps, see the [v2.5 to v2.6 migration README](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/deploymentDataMigration/v2.5_to_v2.6/upgrade/v2.5_to_v2.6_migration_README.md).
:::

#### OpenSearch Serverless next-gen upgrade

v2.6 reworks the OpenSearch Serverless collection and adds new `app.openSearch.useServerless` settings:

| Setting                                                               | Default                                        | Behavior                                                                                                                                                                                                                                                                                              |
| --------------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nextGen`                                                             | `true` (commercial), `false` (GovCloud and EU) | Sets the collection group generation to `NEXTGEN` (`true`) or `CLASSIC` (`false`). The collection is placed in a collection group either way; `NEXTGEN` adds scale-to-zero support.                                                                                                                   |
| `allowPublic`                                                         | `true`                                         | When `false`, the collection is reachable only through a VPC endpoint and requires `app.useGlobalVpc.enabled` to be `true` (built across 2 Availability Zones). Only the OpenSearch-facing Lambda functions are placed in the VPC, so `app.useGlobalVpc.useForAllLambdas` does not need to be `true`. |
| `enableStandbyReplicas`                                               | tracks `nextGen`                               | Enables Serverless standby replicas. Required for `NEXTGEN` (must be `true` when `nextGen` is `true`); optional for `CLASSIC`. Defaults to the value of `nextGen`.                                                                                                                                    |
| `minIndexingOcu` / `maxIndexingOcu` / `minSearchOcu` / `maxSearchOcu` | `2` / `16` / `2` / `16`                        | OpenSearch Compute Unit (OCU) capacity bounds. Each must be one of `0`, `2`, `4`, `8`, `16`, or any multiple of `16`. A minimum of `0` (scale-to-zero) requires `nextGen = true`.                                                                                                                     |

:::warning[Serverless collections cannot be updated in place]
Previously, a Serverless collection was updated by the CDK changeset like any other resource. In v2.6 the collection is placed in a collection group, the `allowPublic` network-policy change is applied, and the group generation is set, all of which reshape the collection, so an existing Serverless deployment **cannot** be updated in place. Remove the collection and re-create it, then reindex:

1. Set `app.openSearch.useServerless.enabled = false` in `infra/config/config.json` and deploy. This removes the existing collection.

    ```bash
    npx cdk deploy --all --require-approval never
    ```

2. Set `app.openSearch.useServerless.enabled = true` again with the desired settings (`nextGen`, `allowPublic`, and OCU values) and deploy. This creates the new collection group and collection.

    ```bash
    npx cdk deploy --all --require-approval never
    ```

3. Reindex to repopulate the new collection — run the reindex utility (`infra/deploymentDataMigration/v2.5_to_v2.6/upgrade` or `tools/reindex_utility.py`), or set `app.openSearch.reindexOnCdkDeploy = true` for one deployment and then set it back to `false`.

Tearing down the collection deletes the indexes, but all source data lives in Amazon DynamoDB and Amazon S3, so the reindex restores the full search corpus. **GovCloud and EU Sovereign Cloud deployments must keep `nextGen = false`.**
:::

:::note[Reindexing a private collection]
A private collection (`allowPublic = false`) is reachable only from inside the VPC and not from a local machine, so the reindex utility's `direct` mode cannot reach it. For a private collection, reindex through the deployed Lambda (`lambda` mode) or set `app.openSearch.reindexOnCdkDeploy = true`.
:::

#### VPC is now required for certain features

In earlier releases, enabling a feature that needs a VPC while `app.useGlobalVpc.enabled` was `false` silently turned the VPC on. In v2.6 this is a configuration error instead: the deployment fails and lists the features that require a VPC. The VPC-requiring features are ALB (`useAlb`), OpenSearch Provisioned (`openSearch.useProvisioned`), and the container-based pipelines (Potree viewer, 3D preview thumbnail, GenAI labeling, Gaussian splatting, RapidPipeline ECS/EKS, ModelOps, Isaac Lab, NVIDIA Cosmos, NVIDIA Gr00t).

If your existing `config.json` relied on the old implicit behavior, update it before deploying v2.6: set `app.useGlobalVpc.enabled` to `true` (the value the deployment was effectively using all along), or disable the listed features. No infrastructure changes result from setting the flag to the value that was already in effect — this is a configuration-file correction only.

#### OpenSearch Provisioned Availability Zone count downgrade

In v2.6, provisioned OpenSearch adds `app.openSearch.useProvisioned.availabilityZoneCount` (default `2`), and the VPC is built with exactly that many Availability Zones. Earlier releases always built the VPC across **3** Availability Zones for provisioned OpenSearch, even though the OpenSearch domain itself only ever used **2** of them — the third AZ's subnet was created but unused. v2.6 now provisions 2 Availability Zones by default (or 3 only when you set `availabilityZoneCount` to `3`) and uses them consistently.

On upgrade, this is a **VPC downgrade**: the previously-unused third Availability Zone's subnet is removed. Removing a subnet can fail in AWS CloudFormation when the subnet still holds elastic network interfaces — in this case the shared interface VPC endpoints placed across the isolated subnets, and the VPC-attached Lambda (Hyperplane) ENIs created when `app.useGlobalVpc.useForAllLambdas` is `true`. The OpenSearch domain itself does not change AZ count (it was already on 2), so the failure is specifically about deleting the orphaned third-AZ subnet.

You have two options:

-   **Keep the existing 3-AZ VPC layout (no change):** set `app.openSearch.useProvisioned.availabilityZoneCount` to `3` in `config.json` before deploying v2.6. The VPC is unchanged and the upgrade proceeds normally.
-   **Move to the 2-AZ layout (default):** because the third AZ's subnet must be deleted, follow the staged drain-and-redeploy procedure so the elastic network interfaces release first. Turn off the VPC and all VPC-associated components, deploy to release the ENIs, manually clear any orphaned ENIs / subnets / VPC resources that still fail to delete, then redeploy with the 2-AZ settings and reindex. The full step-by-step is documented in [Subnet or VPC Resource Deletion Failures](../troubleshooting/common-issues.md) — set `availabilityZoneCount` to `2` when you re-enable OpenSearch in that procedure. No asset data is lost: search data is rebuilt from the authoritative DynamoDB tables and S3 buckets by the reindex.

:::warning[Plan the Availability Zone choice before first v2.6 deploy]
Decide on `availabilityZoneCount` (`2` or `3`) before upgrading. Setting it to `3` preserves the existing VPC with no teardown. Accepting the default of `2` removes a subnet and may require the manual VPC teardown above if elastic network interfaces have not finished detaching.
:::

#### API Gateway REST API endpoint change

In v2.6 the backend API is an API Gateway REST API (v1) served under the fixed stage path `/api`, replacing the previous HTTP API (v2). On deployment the API Gateway identifier and invoke URL change.

The `app.api` configuration block is also restructured in v2.6: the per-implementation settings move under a new `app.api.apiGatewayRest` sub-block, and a new `app.api.apiType` field (fixed to `"APIGATEWAY_REST"`) selects the API implementation. Update an existing `config.json` so that `globalRateLimit`, `globalBurstLimit`, and `endpointType` live under `app.api.apiGatewayRest` (see the [API configuration reference](configuration-reference.md#api-configuration-appapi)). The execute-api VPC endpoint id field is now `app.api.apiGatewayRest.optionalExternalPrivateApigVPCEId` and applies only to a `PRIVATE` endpoint. The REST API stage name is not a configuration option — it is the fixed value `api`.

The web application is unaffected: it reads the API base URL at runtime from `/api/amplify-config`, and the CloudFront `/api/*` behavior (or ALB redirect) absorbs the stage path so browser URLs remain `/api/*`.

The change affects any client that was configured directly against the **old** API Gateway endpoint URL:

-   **VAMS CLI:** re-run `vamscli setup` and provide the new API URL. When pointing the CLI at the deployment's front (CloudFront/ALB) the base URL is unchanged; when pointing it directly at the execute-api endpoint, pass the bare endpoint URL (`https://{rest-api-id}.execute-api.{region}.amazonaws.com`) — the CLI appends the `/api` stage path automatically.
-   **External integrations and scripts:** update any stored API base URL to the new endpoint.

The deployment exposes the new endpoint as the CloudFormation output `APIGatewayEndpointOutput`. IP allow-list enforcement continues to work for both fronted and direct callers — the authorizer resolves the true client IP from the front's forwarded headers when present, and from the direct connection otherwise — so existing direct integrations keep working once re-pointed at the new URL.

#### Execution visibility

In v2.6 every execution read path applies one permission rule: `GET` on the execution's workflow, plus the operation's action on **every** asset the run read — each input file's asset and each asset named as a metadata source. Earlier releases accepted access to any one of a run's assets for the listing while requiring all of them elsewhere, so a listing could offer a row whose details then returned `403`. One rule across the list, the details, the logs, the abort, and the re-run removes that inconsistency.

The narrower side of this is worth checking against existing roles before upgrading. A role scoped to a subset of databases loses list visibility of runs that span databases outside its scope, and loses the ability to re-run them, even when it can read some of the assets involved. Runs whose assets all sit inside the role's scope behave as before. To restore the earlier breadth for a role, widen its `asset` and `database` GET constraints to cover the databases those runs span.

An execution has assets only when it read or wrote one. A run with no inputs of either kind is authorized on the asset it wrote to; a results-only run writes no files and has no asset at all, leaving workflow `GET` as its whole gate.

Deleting an asset does not delete the executions that ran against it. An asset that has been **permanently deleted** is authorized on the database it lived in, under the same action — a database is never removed, since deleting one archives the record — so the history of runs against a deleted asset stays reachable by whoever can read that database.

**Archiving** an asset is not a deletion and does not change how its executions are authorized. An archived asset's record is retained, so it is still authorized on its own attributes (name, type, tags) exactly as it was before archiving, and any asset-level constraint that applied to it continues to apply.

#### Switching `endpointType` between `PRIVATE` and `REGIONAL`

Changing `app.api.apiGatewayRest.endpointType` on an existing deployment is supported and requires no manual steps. A `PRIVATE` endpoint carries an API Gateway resource policy that only permits invocation through the execute-api VPC interface endpoint (an `aws:SourceVpce` condition); a `REGIONAL` endpoint uses a public allow-all resource policy. VAMS writes the correct resource policy for the configured endpoint type on every deployment, so a `PRIVATE` → `REGIONAL` switch overwrites the VPC-restricted policy with the public one, and a `REGIONAL` → `PRIVATE` switch re-applies the restriction.

This explicit-policy behavior exists because Amazon API Gateway does not clear a previously-set resource policy when an update stops supplying one. If a stale `PRIVATE` resource policy is ever left on a now-`REGIONAL` API (for example, after an out-of-band change to the API), every public request — including the browser CORS preflight — is denied at the resource-policy layer with `403 AccessDeniedException` ("no resource-based policy allows the execute-api:Invoke action"). Because that denial happens before any CORS headers are applied, the browser surfaces it as a missing `Access-Control-Allow-Origin` / failed-preflight error rather than an authorization failure. Re-running the VAMS deployment re-asserts the correct policy for the configured `endpointType` and resolves it.

## Breaking changes checklist

Use this checklist to determine if additional actions are needed after updating.

| Change type                            | Versions affected                          | Action required                                                                                                                         |
| -------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| DynamoDB table schema change           | v2.3 to v2.4, v2.4 to v2.5                 | Run version-specific migration scripts.                                                                                                 |
| Amazon OpenSearch Service reindex      | v2.2 to v2.3, v2.3 to v2.4, v2.5 to v2.6   | Run reindex script or set `reindexOnCdkDeploy: true`.                                                                                   |
| OpenSearch engine version upgrade      | v2.5 to v2.6 (provisioned only, 2.7 → 3.5) | Redeploy with OpenSearch disabled then re-enabled if the in-place upgrade fails.                                                        |
| OpenSearch Serverless next-gen reshape | v2.5 to v2.6 (serverless only)             | Disable Serverless, deploy, re-enable with new settings, deploy, then reindex. Keep `nextGen: false` on GovCloud/EU.                    |
| VPC no longer auto-enabled             | v2.5 to v2.6                               | Set `app.useGlobalVpc.enabled: true` (or disable VPC-requiring features) if validation fails.                                           |
| OpenSearch AZ count VPC downgrade      | v2.5 to v2.6 (provisioned only)            | Set `availabilityZoneCount: 3` to keep the existing VPC, or follow the drain-and-redeploy teardown to move to 2 AZs.                    |
| Externally registered pipelines        | v2.5 to v2.6                               | Port each pipeline's input reads, task-token return, and sub-process registration, and declare its definition in a `vamsSchema` bundle. |
| API Gateway REST API endpoint change   | v2.5 to v2.6                               | Re-run `vamscli setup`; update any client or script that stored the API Gateway invoke URL.                                             |
| Permission constraint migration        | v2.3 to v2.4, v2.4 to v2.5                 | Run constraint migration script if custom constraints exist.                                                                            |
| API Gateway authorizer change          | v2.2 to v2.3                               | Reset authorizer cache after deployment.                                                                                                |
| Pipeline CDK construct rename          | v2.2 to v2.3                               | Deploy without pipelines, then redeploy with pipelines enabled.                                                                         |
| Website framework change               | v2.4 to v2.5                               | Clear `node_modules` and reinstall: `cd web && rm -rf node_modules && npm install`.                                                     |

## Rollback guidance

If an update causes issues, the rollback approach depends on the update method used.

### In-place update rollback

1. Check out the previous version tag:

    ```bash
    git checkout tags/<PREVIOUS_VERSION>
    cd web && npm install && npm run build && cd ..
    cd infra && npm install
    npx cdk deploy --all --require-approval never
    ```

2. If DynamoDB tables were replaced during the update, restore from the backups taken in the pre-update checklist.

3. If Amazon OpenSearch Service indexes were modified, trigger a reindex from DynamoDB data.

:::warning[Irreversible changes]
Some changes (such as DynamoDB table replacements) cannot be rolled back through redeployment alone. Always maintain backups before updating.
:::

### A/B deployment rollback

1. Switch DNS records back to Stack A endpoints.
2. Destroy Stack B using `cdk destroy --all`.
3. Verify Stack A is functioning correctly.

## Related resources

-   [Deploy the solution](deploy-the-solution.md)
-   [Uninstall the solution](uninstall.md)
-   [Configuration reference](configuration-reference.md)
