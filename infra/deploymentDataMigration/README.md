# Visual Asset Management System(VAMS) - VAMS Deployment Method and Data Migration

When performing minor updates to VAMS or VAMS configuration, it is possible to just re-deploy the stack which will use the Cloudformation/CDK changeset feature to deploy the updates. Follow the [Deploy VAMS Update](../../README.md#deploy-vams-updates) instructions to conduct this type of deployment.

When upgrading an existing VAMS stack due to major changes in either code or configuration that would normally break a Cloudformation changeset, it may be nesseary to perform an A/B deployment methadology.

A/B deployments are when you would deploy a secondary VAMS Cloudformation/CDK stack (with a different name) [Stack B], migrate data using the migration scripts from the initial stack deployed [Stack A], point domains to the secondary [Stack B] after migration, and then delete/destroy the initial stack [Stack A]. This allows for a smooth transition and/or rollback with only minimal downtime during data migration and domain switch-over.

Use-case Note: This process can also be used for cases of migration of VAMS stack to another region deployment within the same AWS account.

**CRITICAL NOTE** Certain configurations of VAMS, such as currently the ALB configuration feature, causes a modified A/B deployment due to using non-unique resource names such as the S3 WebApp bucket named after the domain. In such cases, you will need to have longer down-times as it will require you to take down the VAMS deployment (DNS update), backup WebApp S3 bucket contents, delete the bucket from Stack A, and then perform the rest of the [A/B Deployment instructions](./README.md#ab-deployment-instructions). In case of roll-back, the Stack A WebApp bucket will need to be restored with the backed up contents.

**CRITICAL NOTE** Currently if using Cognito, registered users in the Cognito user pool group will need to be moved by hand with possibly reset passwords. Future TBD: create script to move users automatically and/or re-use existing user pools.

## A/B Deployment Instructions

Perform the following steps to conduct a A/B deployment and data migration between stacks.

0. (Pre-step) Have an existing VAMS stack deployed
1. Use a different `baseStackName` in your VAMS infrastructure configuration file for deploying to your Stack B (new) deployment
2. Change all domains / DNS records pointing to Stack A to temporarily offline notice site
3. Deploy VAMS stack B in CDK/CloudFormation
4. Follow all the instructions in [Migration Script Instructions](./README.md#migration-script-instructions)
5. If using cognito, cognito user pool will need to be moved by hand
6. Verify VAMS stack B endpoints are working and test VAMS stack B functionality with Stack A data
7. Change all domains / DNS records to point to stack B endpoints
8. Delete / Destroy Stack A in CDK/CloudFormation - Verify that Stack A data no longer needed and verify access logs bucket needs

### Migration Script Template Outline

Migration script templates can be found at `./infra/deploymentDataMigration/config`.

VAMS Upgrade Templates are described below. Copy the template to a new configuration file that you modify with your from/to resource types that the data migration script will use.

-   VAMS 1.X -> 1.X - `MigrationSchema_v1.X_to_v2.0.template.json`
-   VAMS 1.X -> 2.0 - `MigrationSchema_v1.X_to_v2.0.template.json`
-   VAMS 2.0 -> 2.0 - `MigrationSchema_v2.1_to_v2.1.template.json`

### Migration Script Instructions

-   Script to run locally to migrate data from one VAMS deployment stack to another.
-   Intended to be run as part of an A/B deployment, before the new stack version is fully switched over to and before the old stack is tore down.

1. Install python 3.12 and boto3 (pip install boto3)

2. Copy the appropriate migration script in [Migration Script Template Outline](./README.md#migration-script-template-outline) to a new JSON schema file in `./infra/deploymentDataMigration/config`.
3. Update the regionFrom [Stack A] and regionTo [Stack B] fields in your `./infra/deploymentDataMigration/config/` JSON MigrationSchema file to reflect the region where the old and new VAMS stacks are deployed.
4. Add S3 bucket names in your `./infra/deploymentDataMigration/config/` JSON MigrationSchema file for S3Bucket_OldStack_Name [Stack A] and S3Bucket_NewStack_Name [Stack B] based on bucket descriptor provided in each JSON block
5. Add DynamoDB table names in your `./infra/deploymentDataMigration/config/` JSON MigrationSchema file for DynamoDBTable_OldStack_Name [Stack A] and DynamoDBTable_NewStack_Name [Stack B] based on table descriptor provided in each JSON block

6. Ensure permissions on AWS account this will be running against has read/write permissions on both sets of S3 buckets and tables (old stack + new stack)

7. Run the script from the command line as follows, filling in the schema file to use in the `./infra/deploymentDataMigration/config/` folder as the argument: `python VAMSDataMigration.py MigrationSchema_XXX.json`

8. Verify in NewStack S3 Buckets and Tables that data moved successfully

#### Script Notes

-   Do not use this script in conjunction with stagingBucket configurations as staging is only used for new VAMS stack deployments that require existing asset migration
-   New tables should be empty to prevent possible key collisions. If script fails, clear table data first before re-running.
