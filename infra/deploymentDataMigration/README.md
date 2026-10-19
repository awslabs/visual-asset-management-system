# Visual Asset Management System (VAMS) - Deployment Methods and Data Migration

This document provides instructions for deploying VAMS updates and migrating data between deployments.

## Deployment Methods

### Minor Updates

When performing minor updates to VAMS or VAMS configuration, it is possible to just re-deploy the stack which will use the CloudFormation/CDK changeset feature to deploy the updates. Follow the [Deploy VAMS Update](../../README.md#deploy-vams-updates) instructions to conduct this type of deployment.

### Major Updates (A/B Deployment)

When upgrading an existing VAMS stack due to major changes in either code or configuration that would normally break a CloudFormation changeset, it may be necessary to perform an A/B deployment methodology.

A/B deployments are when you would deploy a secondary VAMS CloudFormation/CDK stack (with a different name) [Stack B], copy the data from the initial stack deployed [Stack A] into it, point domains to the secondary [Stack B] after migration, and then delete/destroy the initial stack [Stack A]. This allows for a smooth transition and/or rollback with only minimal downtime during data migration and domain switch-over.

**Use-case Note:** This process can also be used for cases of migration of VAMS stack to another region deployment within the same AWS account.

**CRITICAL NOTE:** Certain configurations of VAMS, such as currently the ALB configuration feature, causes a modified A/B deployment due to using non-unique resource names such as the S3 WebApp bucket named after the domain. In such cases, you will need to have longer down-times as it will require you to take down the VAMS deployment (DNS update), backup WebApp S3 bucket contents, delete the bucket from Stack A, and then perform the rest of the A/B Deployment instructions. In case of roll-back, the Stack A WebApp bucket will need to be restored with the backed up contents.

**CRITICAL NOTE:** Currently if using Cognito, registered users in the Cognito user pool group will need to be moved by hand with possibly reset passwords. Future TBD: create script to move users automatically and/or re-use existing user pools.

## A/B Deployment Instructions

Perform the following steps to conduct an A/B deployment and data migration between stacks.

0. (Pre-step) Have an existing VAMS stack deployed (Stack A)
1. Use a different `baseStackName` in your VAMS infrastructure configuration file for deploying to your Stack B (new) deployment
2. Change all domains / DNS records pointing to Stack A to temporarily offline notice site
3. Deploy VAMS stack B in CDK/CloudFormation
4. Migrate data from Stack A to Stack B:
    - For DynamoDB tables: Follow the [A/B Deployment Data Copy](#ab-deployment-data-copy) section
    - For S3 buckets: Use AWS CLI or SDK to copy data between buckets (see [S3 Bucket Migration](#s3-bucket-migration))
5. If using Cognito, cognito user pool will need to be moved by hand
6. Verify VAMS stack B endpoints are working and test VAMS stack B functionality with Stack A data
7. Change all domains / DNS records to point to stack B endpoints
8. Delete / Destroy Stack A in CDK/CloudFormation - Verify that Stack A data is no longer needed and verify access logs bucket needs

### DynamoDB Table Migration

When migrating DynamoDB tables between stacks:

1. Confirm which Stack B tables its own deployment has already written (see [A/B Deployment Data Copy](#ab-deployment-data-copy)); every other target table is empty, so no key collides there
2. Copy each table with AWS tooling as described in [A/B Deployment Data Copy](#ab-deployment-data-copy); if Stack B is a newer VAMS version, run the [Version Upgrade Scripts](#version-upgrade-scripts) for each version step afterwards (see the [Version Upgrade Script Index](#version-upgrade-script-index))
3. Verify that all data has been successfully migrated before proceeding
4. If the migration fails, clear the target tables and retry

### S3 Bucket Migration

When migrating S3 buckets between stacks:

1. For asset buckets, use the AWS CLI to sync the contents:

    ```bash
    aws s3 sync s3://source-bucket s3://destination-bucket --profile your-profile
    ```

2. For the WebApp bucket (if using ALB configuration):

    - Back up the contents of the WebApp bucket from Stack A
    - Delete the bucket from Stack A (may require DNS changes first)
    - Deploy Stack B
    - Restore the WebApp bucket contents to Stack B

3. Verify that all files have been successfully copied before proceeding

## Version-Specific Migration Instructions

This section contains instructions for migrating data between specific VAMS versions. Each version upgrade may require specific data transformations or schema changes.

### VAMS v2.1 to v2.2 Migration

The v2.1 to v2.2 migration involves creating a new asset versions table, updating the structure of asset records, and adding bucket information to database records in DynamoDB.

#### Migration Overview

The migration script performs the following operations:

1. **Asset Versions Migration**:

    - For each record in the assets table, create a record in the new asset versions table
    - Map fields from the `currentVersion` field in the assets table to the new structure in the asset versions table

2. **Asset Records Update**:

    - Update the `assetLocation` field structure to use `baseAssetsPrefix`: `{'assetLocation': { 'Key': "{baseAssetsPrefix}{assetId}/" }}`
    - Add `bucketId` to each asset record based on lookup from S3_Asset_Buckets table
    - Move the version number from `currentVersion.Version` to a new field `currentVersionId`
    - Remove specified fields: `isMultiFile`, `pipelineId`, `executionId`, `versions`, `currentVersion`, `specifiedPipelines`, `Parent`, `objectFamily`

3. **Database Records Update**:

    - Add `defaultBucketId` to all records in the databases table

4. **Asset Links Migration** (Optional):
    - Migrate asset links from the old table schema to the new table schema
    - Look up database IDs for each asset in the link
    - Create composite keys for GSI indexes (`fromAssetDatabaseId:fromAssetId` and `toAssetDatabaseId:toAssetId`)
    - Transform relationship types: "related" stays as is, "parent"/"child" becomes "parentChild"
    - Add empty tags array to each link

#### Running the Migration

The migration scripts are located in the `infra/deploymentDataMigration/v2.1_to_v2.2/upgrade/` directory.

**Quick Start:**

1. Navigate to the upgrade directory:

    ```bash
    cd infra/deploymentDataMigration/v2.1_to_v2.2/upgrade/
    ```

2. Configure the migration:

    - Copy and modify one of the provided configuration templates:
        - `v2.1_to_v2.2_migration_config.json`: Basic configuration template
        - `v2.1_to_v2.2_migration_prod_config.json`: Template for production migration
    - Make sure to set the following required parameters:
        - `assets_table_name`: Name of the assets table
        - `asset_versions_table_name`: Name of the asset versions table
        - `s3_asset_buckets_table_name`: Name of the S3 asset buckets table
        - `databases_table_name`: Name of the databases table
        - `base_assets_prefix`: Base prefix for asset locations (should end with a slash)
        - `asset_bucket_name`: Name of the S3 bucket used for asset storage
        - `asset_links_table_name` (optional): Name of the old asset links table to migrate from
        - `asset_links_table_v2_name` (optional): Name of the new asset links v2 table to migrate to

3. Run the migration using the helper scripts:

    For Linux/macOS:

    ```bash
    chmod +x run_migration.sh
    ./run_migration.sh your_config_file.json
    ```

    For Windows:

    ```powershell
    .\run_migration.ps1 your_config_file.json
    ```

4. Verify the migration:
    - Use the verification script to check that all data was migrated correctly
    - Manually verify that the application works with the migrated data

For detailed instructions, refer to the [v2.1 to v2.2 Migration README](./v2.1_to_v2.2/upgrade/v2.1_to_v2.2_migration_README.md).

## Migration Script Instructions

VAMS provides two kinds of migration tooling:

1. **A/B Deployment Data Copy**: Copying data from one VAMS stack to another, using AWS tooling (VAMS ships no cross-stack copy script)
2. **Version Upgrade Scripts**: Upgrading the data schema within the same stack, one directory per version step under this folder

### A/B Deployment Data Copy

VAMS does not ship a Stack A to Stack B table copy script. Copy each DynamoDB table from Stack A into the matching table in Stack B with the tooling of your choice -- an AWS Glue job, an AWS Database Migration Service task, or a scan-and-batch-write script built on the AWS SDK -- and sync the S3 buckets as described in [S3 Bucket Migration](#s3-bucket-migration).

1. Deploy Stack B and leave its data tier idle (no uploads, no pipeline runs) until the copy is verified.

2. Read the table names of both stacks from SSM Parameter Store. Each stack publishes them under the prefix in its `ResourceNamesSSMParamPrefixOutput` CloudFormation output:

    ```bash
    aws ssm get-parameters-by-path --recursive \
      --path "<ResourceNamesSSMParamPrefixOutput>/dynamoTables" \
      --query 'Parameters[].[Name,Value]' --output table
    ```

    Parameters under `dynamoTables/legacy/` name deprecated tables kept only as migration sources; copy them when the version upgrade step below still reads from them.

3. Ensure the credentials the copy runs under have read access to the Stack A tables and buckets and write access to the Stack B tables and buckets, including both stacks' KMS keys when `useKmsCmkEncryption` is enabled.

4. Copy each table into the Stack B table with the same `dynamoTables/<key>` parameter name, with two exceptions that Stack B's own deployment writes before any copy and rewrites on every deploy. Skip `dynamoTables/appFeatureEnabledStorage` entirely: it holds Stack B's feature switches, derived from Stack B's configuration, and a copy would replace them with Stack A's until the next deploy. Copy `rolesStorage` (the `admin` and `basicReadOnly` roles), `constraintsStorage` (the `initial_admin_*` and `initial_basicro_*` constraints), `userStorage` and `userRolesStorage` (the `SYSTEM_USER` and `app.adminUserId` records and their `admin` role assignments) and `metadataSchemaStorageV2` (the default schemas, when an `app.metadataSchema.autoLoad*` flag is on) with a writer that overwrites or skips an existing key. Every other destination table is empty until the copy writes it; if a copy of one of those fails, clear that destination table and retry.

5. Sync the S3 buckets ([S3 Bucket Migration](#s3-bucket-migration)).

6. If Stack B runs a newer VAMS version than Stack A, run the [Version Upgrade Scripts](#version-upgrade-scripts) for each version step against Stack B after the copy.

7. Verify in the Stack B tables and buckets that the data moved successfully, then test the Stack B endpoints.

### Version Upgrade Scripts

These scripts are used to upgrade data schema within the same stack. Each version upgrade has its own set of scripts located in a version-specific directory under `infra/deploymentDataMigration/`.

For example, the v2.1 to v2.2 upgrade scripts are located in `infra/deploymentDataMigration/v2.1_to_v2.2/upgrade/`.

Refer to the version-specific migration instructions for details on how to run these scripts.

### SSM Resource-Name Lookup (v2.6+ migrations)

Deployments publish their DynamoDB table names, non-asset S3 bucket names, deprecated migration-only table names, and selected Lambda function names as SSM parameters under a deployment-unique base prefix, exposed by the core stack output `ResourceNamesSSMParamPrefixOutput`. Migration scripts targeting v2.6+ deployments resolve resource names through the shared utility `infra/deploymentDataMigration/tools/ssm_resource_lookup.py` (`SsmResourceLookup` + `ResourceParamKeys`) instead of requiring operators to copy each physical name into the migration config. The operator fills in only the base prefix (and region/profile); explicit per-resource config values remain supported as optional overrides via `resolve_with_override()`.

Migration scripts for upgrades **to versions before v2.6** (v2.4 to v2.5 and earlier) keep their explicit-name configs, because the source deployments do not publish the SSM parameters. The reindex utility (`tools/reindex_utility.py`) also keeps its explicit inputs since it serves those earlier upgrade paths; v2.6+ migration scripts resolve names via `SsmResourceLookup` and pass them to it.

When a new DynamoDB table, audit log group, or migration-consumed Lambda function is added to the deployment, add the matching constant to `ResourceParamKeys` in `ssm_resource_lookup.py` alongside the `infra/common/resourceParamKeys.ts` and `backend/backend/common/resourceNames.py` entries.

### Version Upgrade Script Index

Each version step has its own directory under `infra/deploymentDataMigration/` with the script, a configuration template and a README describing the transformation and its IAM needs. Run the steps in order when upgrading across more than one version.

-   VAMS 2.1 -> 2.2 - See the [v2.1 to v2.2 Migration README](./v2.1_to_v2.2/upgrade/v2.1_to_v2.2_migration_README.md)
-   VAMS 2.2 -> 2.3 - See the [v2.2 to v2.3 Migration README](./v2.2_to_v2.3/upgrade/v2.2_to_v2.3_migration_README.md)
-   VAMS 2.3 -> 2.4 - See the [v2.3 to v2.4 Migration README](./v2.3_to_v2.4/upgrade/v2.3_to_v2.4_migration_README.md)
-   VAMS 2.4 -> 2.5 - See the [v2.4 to v2.5 Migration README](./v2.4_to_v2.5/upgrade/v2.4_to_v2.5_migration_README.md)
-   VAMS 2.5 -> 2.6 - See the [v2.5 to v2.6 Migration README](./v2.5_to_v2.6/upgrade/v2.5_to_v2.6_migration_README.md).

Deployments older than v2.1 have no in-repository upgrade path; deploy a new stack and copy the data as described in [A/B Deployment Data Copy](#ab-deployment-data-copy).

### Script Notes

-   Destination tables for an A/B copy are empty except the ones Stack B's deployment seeds (listed in [A/B Deployment Data Copy](#ab-deployment-data-copy)); on those, overwrite or skip existing keys and never copy `appFeatureEnabledStorage`. If a copy or an upgrade script fails on an unseeded table, clear that table's data first before re-running.
-   Always test migrations on a small subset of data before running on production data.
-   Always back up your data before running any migration scripts.
