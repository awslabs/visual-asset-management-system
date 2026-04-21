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

## Breaking changes checklist

Use this checklist to determine if additional actions are needed after updating.

| Change type                       | Versions affected          | Action required                                                                     |
| --------------------------------- | -------------------------- | ----------------------------------------------------------------------------------- |
| DynamoDB table schema change      | v2.3 to v2.4, v2.4 to v2.5 | Run version-specific migration scripts.                                             |
| Amazon OpenSearch Service reindex | v2.2 to v2.3, v2.3 to v2.4 | Run reindex script or set `reindexOnCdkDeploy: true`.                               |
| Permission constraint migration   | v2.3 to v2.4, v2.4 to v2.5 | Run constraint migration script if custom constraints exist.                        |
| API Gateway authorizer change     | v2.2 to v2.3               | Reset authorizer cache after deployment.                                            |
| Pipeline CDK construct rename     | v2.2 to v2.3               | Deploy without pipelines, then redeploy with pipelines enabled.                     |
| Website framework change          | v2.4 to v2.5               | Clear `node_modules` and reinstall: `cd web && rm -rf node_modules && npm install`. |

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
