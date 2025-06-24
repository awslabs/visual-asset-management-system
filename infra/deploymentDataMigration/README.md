# Visual Asset Management System (VAMS) - Deployment Methods and Data Migration

This document provides instructions for deploying VAMS updates and migrating data between deployments.

## Deployment Methods

### Minor Updates

When performing minor updates to VAMS or VAMS configuration, it is possible to just re-deploy the stack which will use the CloudFormation/CDK changeset feature to deploy the updates. Follow the [Deploy VAMS Update](../../README.md#deploy-vams-updates) instructions to conduct this type of deployment.

### Major Updates (A/B Deployment)

When upgrading an existing VAMS stack due to major changes in either code or configuration that would normally break a CloudFormation changeset, it may be necessary to perform an A/B deployment methodology.

A/B deployments are when you would deploy a secondary VAMS CloudFormation/CDK stack (with a different name) [Stack B], migrate data using the migration scripts from the initial stack deployed [Stack A], point domains to the secondary [Stack B] after migration, and then delete/destroy the initial stack [Stack A]. This allows for a smooth transition and/or rollback with only minimal downtime during data migration and domain switch-over.

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
    - For DynamoDB tables: Follow the [Migration Script Instructions](#migration-script-instructions) section
    - For S3 buckets: Use AWS CLI or SDK to copy data between buckets (see [S3 Bucket Migration](#s3-bucket-migration))
5. If using Cognito, cognito user pool will need to be moved by hand
6. Verify VAMS stack B endpoints are working and test VAMS stack B functionality with Stack A data
7. Change all domains / DNS records to point to stack B endpoints
8. Delete / Destroy Stack A in CDK/CloudFormation - Verify that Stack A data is no longer needed and verify access logs bucket needs

### DynamoDB Table Migration

When migrating DynamoDB tables between stacks:

1. Ensure the target tables in Stack B are empty to prevent key collisions
2. Use the appropriate migration script based on your VAMS version (see [Migration Script Template Outline](#migration-script-template-outline))
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

### VAMS v2.2 to v2.3 Migration

The v2.2 to v2.3 migration involves creating a new asset versions table, updating the structure of asset records, and adding bucket information to database records in DynamoDB.

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

#### Running the Migration

The migration scripts are located in the `infra/deploymentDataMigration/v2.2_to_v2.3/upgrade/` directory.

**Quick Start:**

1. Navigate to the upgrade directory:

    ```bash
    cd infra/deploymentDataMigration/v2.2_to_v2.3/upgrade/
    ```

2. Configure the migration:

    - Copy and modify one of the provided configuration templates:
        - `v2.2_to_v2.3_migration_config.json`: Basic configuration template
        - `v2.2_to_v2.3_migration_prod_config.json`: Template for production migration
    - Make sure to set the following required parameters:
        - `assets_table_name`: Name of the assets table
        - `asset_versions_table_name`: Name of the asset versions table
        - `s3_asset_buckets_table_name`: Name of the S3 asset buckets table
        - `databases_table_name`: Name of the databases table
        - `base_assets_prefix`: Base prefix for asset locations (should end with a slash)
        - `asset_bucket_name`: Name of the S3 bucket used for asset storage

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

For detailed instructions, refer to the [v2.2 to v2.3 Migration README](./v2.2_to_v2.3/upgrade/v2.2_to_v2.3_migration_README.md).

## Migration Script Instructions

VAMS provides two types of migration scripts:

1. **A/B Deployment Migration Scripts**: For migrating data between two separate VAMS stacks
2. **Version Upgrade Scripts**: For upgrading data schema within the same stack

### A/B Deployment Migration Scripts

These scripts are used to migrate data from one VAMS deployment stack to another.

1. Install python 3.12 and boto3:

    ```bash
    pip install boto3
    ```

2. Copy the appropriate migration script template from [Migration Script Template Outline](#migration-script-template-outline) to a new JSON schema file in `./infra/deploymentDataMigration/config/`.

3. Update the regionFrom [Stack A] and regionTo [Stack B] fields in your JSON MigrationSchema file to reflect the region where the old and new VAMS stacks are deployed.

4. Add S3 bucket names in your JSON MigrationSchema file for S3Bucket_OldStack_Name [Stack A] and S3Bucket_NewStack_Name [Stack B] based on bucket descriptor provided in each JSON block.

5. Add DynamoDB table names in your JSON MigrationSchema file for DynamoDBTable_OldStack_Name [Stack A] and DynamoDBTable_NewStack_Name [Stack B] based on table descriptor provided in each JSON block.

6. Ensure permissions on AWS account this will be running against has read/write permissions on both sets of S3 buckets and tables (old stack + new stack).

7. Run the script from the command line as follows, filling in the schema file to use in the `./infra/deploymentDataMigration/config/` folder as the argument:

    ```bash
    python VAMSDataMigration.py MigrationSchema_XXX.json
    ```

8. Verify in NewStack S3 Buckets and Tables that data moved successfully.

### Version Upgrade Scripts

These scripts are used to upgrade data schema within the same stack. Each version upgrade has its own set of scripts located in a version-specific directory under `infra/deploymentDataMigration/`.

For example, the v2.2 to v2.3 upgrade scripts are located in `infra/deploymentDataMigration/v2.2_to_v2.3/upgrade/`.

Refer to the version-specific migration instructions for details on how to run these scripts.

### Migration Script Template Outline

Migration script templates for A/B deployments can be found at `./infra/deploymentDataMigration/config/`.

VAMS Upgrade Templates are described below. Copy the template to a new configuration file that you modify with your from/to resource types that the data migration script will use.

-   VAMS 1.X -> 1.X - `MigrationSchema_v1.X_to_v2.0.template.json`
-   VAMS 1.X -> 2.0 - `MigrationSchema_v1.X_to_v2.0.template.json`
-   VAMS 2.0 -> 2.0 - `MigrationSchema_v2.1_to_v2.1.template.json`
-   VAMS 2.2 -> 2.3 - See the [v2.2 to v2.3 Migration README](./v2.2_to_v2.3/upgrade/v2.2_to_v2.3_migration_README.md)

### Script Notes

-   Do not use A/B deployment migration scripts in conjunction with stagingBucket configurations as staging is only used for new VAMS stack deployments that require existing asset migration.
-   New tables should be empty to prevent possible key collisions. If script fails, clear table data first before re-running.
-   Always test migrations on a small subset of data before running on production data.
-   Always back up your data before running any migration scripts.
