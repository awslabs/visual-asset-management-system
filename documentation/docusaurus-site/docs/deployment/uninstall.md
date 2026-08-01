# Uninstall the solution

This page describes how to completely remove VAMS from your AWS account, including the CDK stack destruction and manual cleanup of retained resources.

:::danger[Permanent data loss]
Uninstalling VAMS removes the AWS Lambda functions, Amazon API Gateway endpoints, and other managed resources for the deployment. The Amazon DynamoDB tables and the asset, auxiliary, artefacts, and access logs Amazon Simple Storage Service (Amazon S3) buckets are retained by design and are deleted only by the manual cleanup steps below, which permanently destroy their contents. This action cannot be undone. Ensure you have backed up all data you intend to keep before proceeding.
:::

## Pre-uninstall backup

Complete the following backup steps before beginning the uninstall process.

### Back up DynamoDB tables

Export critical DynamoDB tables to Amazon S3 for archival purposes. VAMS creates DynamoDB tables. At minimum, back up the tables containing your asset, database, and metadata records.

```bash
# List all VAMS DynamoDB tables
aws dynamodb list-tables \
    --query "TableNames[?contains(@, '<STACK_NAME>')]" \
    --output table

# Export a table to S3 using on-demand export
aws dynamodb export-table-to-point-in-time \
    --table-arn arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/<TABLE_NAME> \
    --s3-bucket <BACKUP_BUCKET> \
    --s3-prefix vams-backup/dynamodb/$(date +%Y%m%d)/<TABLE_NAME> \
    --export-format DYNAMODB_JSON
```

:::tip[Bulk table export]
To export all VAMS tables at once, use the following script:

```bash
STACK_NAME="<YOUR_STACK_NAME>"
BACKUP_BUCKET="<YOUR_BACKUP_BUCKET>"
REGION="<YOUR_REGION>"
ACCOUNT_ID="<YOUR_ACCOUNT_ID>"
DATE=$(date +%Y%m%d)

for TABLE in $(aws dynamodb list-tables \
    --query "TableNames[?contains(@, '${STACK_NAME}')]" \
    --output text); do
    echo "Exporting ${TABLE}..."
    aws dynamodb export-table-to-point-in-time \
        --table-arn "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE}" \
        --s3-bucket "${BACKUP_BUCKET}" \
        --s3-prefix "vams-backup/dynamodb/${DATE}/${TABLE}" \
        --export-format DYNAMODB_JSON
done
```

:::

### Back up S3 buckets

Sync the contents of VAMS-managed S3 buckets to a backup location. This preserves your uploaded assets, auxiliary files, and generated previews.

```bash
# Identify VAMS S3 buckets
aws s3 ls | grep <STACK_NAME>

# Sync each bucket to a backup location
aws s3 sync s3://<ASSET_BUCKET> s3://<BACKUP_BUCKET>/vams-backup/asset-bucket/
aws s3 sync s3://<AUXILIARY_BUCKET> s3://<BACKUP_BUCKET>/vams-backup/auxiliary-bucket/
```

:::note[External S3 buckets]
External S3 buckets configured via `externalAssetBuckets` in `config.json` are not deleted by VAMS uninstall. Only the VAMS-managed bucket policies and event notifications are removed. Your data in external buckets remains intact.
:::

### Record stack resource identifiers

Save the CloudFormation stack outputs and resource identifiers for reference during manual cleanup:

```bash
aws cloudformation describe-stacks \
    --stack-name <VAMS_STACK_NAME> \
    --query 'Stacks[0].Outputs' \
    --output json > vams-stack-outputs.json

aws cloudformation describe-stack-resources \
    --stack-name <VAMS_STACK_NAME> \
    --output json > vams-stack-resources.json
```

## Step 1: Destroy the CDK stack

Run the CDK destroy command from the `infra` directory. This removes all CloudFormation-managed resources.

```bash
cd infra
npx cdk destroy --all
```

:::info[Confirmation prompt]
The `cdk destroy` command prompts for confirmation before proceeding. Type `y` to confirm. To skip the prompt, append `--force` to the command.
:::

The destroy operation typically takes 15-30 minutes depending on the number of resources and enabled features. Monitor progress in the AWS CloudFormation console.

### Common destroy failures

If the stack destroy fails, check for the following common causes:

| Failure reason                     | Resolution                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------------ |
| S3 bucket not empty                | Empty the bucket first (see [Step 2](#step-2-delete-s3-buckets)).                                |
| DynamoDB table deletion protection | Disable deletion protection in the DynamoDB console, then retry.                                 |
| Resource in use by another stack   | Identify and remove the dependent stack first.                                                   |
| Nested stack deletion failed       | Delete the failed nested stack manually in CloudFormation, then retry the parent stack deletion. |

If the stack is stuck in `DELETE_FAILED` state, you can force-delete it with specific resources excluded:

```bash
aws cloudformation delete-stack \
    --stack-name <VAMS_STACK_NAME> \
    --retain-resources <RESOURCE_LOGICAL_ID_1> <RESOURCE_LOGICAL_ID_2>
```

Then manually delete the retained resources using the steps in Step 2 through Step 9.

## Step 2: Delete S3 buckets

Amazon S3 buckets with objects cannot be deleted by CloudFormation. Empty and delete each VAMS-managed bucket.

```bash
# List VAMS buckets
aws s3 ls | grep <STACK_NAME>

# Empty and delete each bucket (including versioned objects)
BUCKETS=$(aws s3 ls | grep <STACK_NAME> | awk '{print $3}')
for BUCKET in $BUCKETS; do
    echo "Emptying s3://${BUCKET}..."
    aws s3 rm "s3://${BUCKET}" --recursive

    # Delete versioned objects and delete markers
    echo "Removing version history for s3://${BUCKET}..."
    aws s3api list-object-versions \
        --bucket "${BUCKET}" \
        --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
        --output json | \
    aws s3api delete-objects --bucket "${BUCKET}" --delete file:///dev/stdin 2>/dev/null

    aws s3api list-object-versions \
        --bucket "${BUCKET}" \
        --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
        --output json | \
    aws s3api delete-objects --bucket "${BUCKET}" --delete file:///dev/stdin 2>/dev/null

    # Delete the bucket
    echo "Deleting s3://${BUCKET}..."
    aws s3 rb "s3://${BUCKET}"
done
```

VAMS creates the following S3 buckets. The asset, auxiliary, artefacts, and access logs buckets use a `RETAIN` removal policy and require manual deletion. The web app bucket and its access logs bucket are emptied and deleted automatically during stack teardown, so they normally require manual deletion only if the stack deletion fails partway.

| Bucket                     | Removal on teardown     | Blocks redeploy if left behind?     | Description                                                                             |
| -------------------------- | ----------------------- | ----------------------------------- | --------------------------------------------------------------------------------------- |
| Asset bucket(s)            | Retained (manual)       | No — auto-named                     | Stores uploaded asset files. One bucket per configuration (new bucket and/or external). |
| Auxiliary bucket           | Retained (manual)       | No — auto-named                     | Stores auto-generated previews, pipeline working files, and viewer data.                |
| Artefacts bucket           | Retained (manual)       | No — auto-named                     | Stores CDK deployment artefacts.                                                        |
| Access logs bucket         | Retained (manual)       | No — auto-named                     | Stores S3 server access logs.                                                           |
| Web app bucket             | Deleted (emptied first) | ALB only — fixed name (domain host) | Stores the built frontend static files (for both CloudFront and ALB deployments).       |
| Web app access logs bucket | Deleted (emptied first) | ALB only — fixed name (domain host) | Stores access logs for the web app bucket and ALB.                                      |

:::note[Retained does not mean it blocks a redeploy]
The retained asset, auxiliary, artefacts, and access logs buckets are **auto-named** by AWS CloudFormation, so they can be left in place when redeploying with the same configuration name — they will not cause a name collision. Delete them only when you intend to permanently remove the stored data. By contrast, under ALB deployments the web app bucket and its access logs bucket carry fixed names derived from the configured domain host; if a teardown fails and leaves either behind, delete it before redeploying with the same domain host to avoid a bucket-name collision.
:::

## Step 3: Delete DynamoDB tables

VAMS DynamoDB tables use a `RETAIN` removal policy, so they and their contents survive stack teardown and require manual deletion. This protects against accidental data loss. Every table is auto-named by AWS CloudFormation, so a retained table never blocks a redeploy with the same configuration name — delete the tables only when you intend to permanently remove the stored data.

```bash
# List remaining VAMS tables
aws dynamodb list-tables \
    --query "TableNames[?contains(@, '<STACK_NAME>')]" \
    --output text

# Delete each table
for TABLE in $(aws dynamodb list-tables \
    --query "TableNames[?contains(@, '<STACK_NAME>')]" \
    --output text); do
    echo "Deleting table ${TABLE}..."
    aws dynamodb delete-table --table-name "${TABLE}"
done
```

## Step 4: Delete Amazon CloudWatch log groups

VAMS creates Lambda function log groups and explicitly named log groups under `/aws/vendedlogs/` that may persist after stack deletion. The named log groups (audit, REST API access, workflow, orchestration bus, VPC flow logs, AWS CloudTrail, and per-pipeline state machine groups) use deterministic names derived from the stack name and account ID. If any are left behind, they will conflict with the same-named groups on a subsequent redeploy using the same configuration name and account, so delete them before redeploying.

The key named log groups are:

-   `/aws/vendedlogs/VAMSAudit*-{hash}` — Audit log groups (authentication, authorization, file upload/download, actions, errors)
-   `/aws/vendedlogs/vamsPipelineWorkflows-{hash}` — Step Functions workflow execution logs
-   `/aws/vendedlogs/VAMSOrchestrationBusAudit-{hash}` — EventBridge orchestration bus audit
-   `/aws/vendedlogs/VAMSCloudWatchVPCLogs-{hash}` — VPC flow logs (conditional on `useGlobalVpc`)
-   `/aws/vendedlogs/VAMSCloudTrailLogs-{hash}` — AWS CloudTrail logs (conditional on `addStackCloudTrailLogs`)
-   `/aws/vendedlogs/VAMSstateMachine-*-{hash}` — Per-pipeline state machine logs

```bash
# List VAMS-related log groups
aws logs describe-log-groups \
    --log-group-name-prefix "/aws/lambda/<STACK_NAME>" \
    --query 'logGroups[].logGroupName' --output text

# List all VAMS named log groups (audit, API access, workflow, orchestration,
# VPC, CloudTrail, and per-pipeline state machine groups)
aws logs describe-log-groups \
    --log-group-name-prefix "/aws/vendedlogs/VAMS" \
    --query 'logGroups[].logGroupName' --output text

aws logs describe-log-groups \
    --log-group-name-prefix "/aws/vendedlogs/vamsPipelineWorkflows" \
    --query 'logGroups[].logGroupName' --output text

# Delete Lambda log groups
for LG in $(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/lambda/<STACK_NAME>" \
    --query 'logGroups[].logGroupName' --output text); do
    echo "Deleting log group ${LG}..."
    aws logs delete-log-group --log-group-name "${LG}"
done

# Delete all VAMS named vendedlogs groups (audit, API access, orchestration bus,
# VPC flow logs, per-pipeline state machine logs). Includes the conditional
# CloudTrail (VAMSCloudTrailLogs) and VPC (VAMSCloudWatchVPCLogs) groups.
for LG in $(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/vendedlogs/VAMS" \
    --query 'logGroups[].logGroupName' --output text); do
    echo "Deleting log group ${LG}..."
    aws logs delete-log-group --log-group-name "${LG}"
done

# Delete the workflow execution log group
for LG in $(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/vendedlogs/vamsPipelineWorkflows" \
    --query 'logGroups[].logGroupName' --output text); do
    echo "Deleting log group ${LG}..."
    aws logs delete-log-group --log-group-name "${LG}"
done

# Delete container pipeline log groups (RapidPipeline, ModelOps), if present
for LG in $(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/vendedlogs/Pipelines/" \
    --query 'logGroups[].logGroupName' --output text); do
    echo "Deleting log group ${LG}..."
    aws logs delete-log-group --log-group-name "${LG}"
done

# Delete API Gateway access log groups (if present)
for LG in $(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/apigateway/<STACK_NAME>" \
    --query 'logGroups[].logGroupName' --output text); do
    echo "Deleting log group ${LG}..."
    aws logs delete-log-group --log-group-name "${LG}"
done
```

:::warning[Redeploying with the same configuration]
VAMS log group names are deterministic (a hash of the stack name plus account ID). If you intend to redeploy VAMS with the same configuration name into the same account, you **must** delete any orphaned `/aws/vendedlogs/...` groups first. A pre-existing log group with the same name causes the deployment's log group creation to fail. This most commonly affects the conditional `VAMSCloudTrailLogs` (when `addStackCloudTrailLogs` is enabled) and `VAMSCloudWatchVPCLogs` (when `useGlobalVpc` is enabled) groups.
:::

## Step 5: Delete AWS Systems Manager parameters

VAMS creates explicitly named SSM parameters under the deployment prefix `/<name>-<baseStackName>/` (resource-name parameters under `.../resourceNames/`, plus OpenSearch, web URL, and Location Service parameters). They are deleted with the stack, but if a stack deletion fails partway, orphaned parameters conflict with the same-named parameters on a subsequent redeploy using the same configuration name, so delete any remaining ones before redeploying.

```bash
# List remaining VAMS parameters for the deployment
aws ssm get-parameters-by-path \
    --path "/<CONFIG_NAME>-<BASE_STACK_NAME>" \
    --recursive \
    --query 'Parameters[].Name' --output text

# Delete them (deleteParameters accepts up to 10 names per call)
for P in $(aws ssm get-parameters-by-path \
    --path "/<CONFIG_NAME>-<BASE_STACK_NAME>" \
    --recursive \
    --query 'Parameters[].Name' --output text); do
    echo "Deleting parameter ${P}..."
    aws ssm delete-parameter --name "${P}"
done
```

## Step 6: Schedule AWS KMS key deletion

If VAMS was deployed with KMS CMK encryption (`app.useKmsCmkEncryption.enabled: true`) and the key was created by VAMS (not an imported external key), schedule the key for deletion.

```bash
# List VAMS KMS keys (search by alias or description)
aws kms list-aliases \
    --query "Aliases[?contains(AliasName, '<STACK_NAME>')]"

# Schedule key deletion (minimum 7-day waiting period)
aws kms schedule-key-deletion \
    --key-id <KEY_ID> \
    --pending-window-in-days 30
```

:::warning[KMS key waiting period]
AWS KMS enforces a minimum 7-day and maximum 30-day waiting period before a key is permanently deleted. During this period, the key is disabled but can be canceled. Use a 30-day window to allow time for discovering any remaining encrypted resources.
:::

:::danger[External KMS keys]
If you provided an external KMS key via `app.useKmsCmkEncryption.optionalExternalCmkArn`, do **not** delete that key. It may be in use by other applications. Only remove the VAMS-specific key policy statements.
:::

## Step 7: Delete the Amazon Cognito user pool

If VAMS was deployed with Amazon Cognito authentication, the user pool may be retained after stack deletion.

```bash
# List Cognito user pools
aws cognito-idp list-user-pools --max-results 20 \
    --query "UserPools[?contains(Name, '<STACK_NAME>')]"

# Delete the domain first (required before pool deletion)
aws cognito-idp delete-user-pool-domain \
    --domain <COGNITO_DOMAIN> \
    --user-pool-id <USER_POOL_ID>

# Delete the user pool
aws cognito-idp delete-user-pool \
    --user-pool-id <USER_POOL_ID>
```

## Step 8: Delete Amazon OpenSearch Service resources

If Amazon OpenSearch Service was enabled, delete the collection (Serverless) or domain (Provisioned).

### OpenSearch Serverless

```bash
# List collections
aws opensearchserverless list-collections \
    --query "collectionSummaries[?contains(name, '<STACK_NAME>')]"

# Delete the collection
aws opensearchserverless delete-collection \
    --id <COLLECTION_ID>

# Delete the collection group (created with a "cg" name prefix for both CLASSIC and NEXTGEN generations)
aws opensearchserverless list-collection-groups \
    --query "collectionGroupSummaries[?contains(name, 'cg')]"

aws opensearchserverless delete-collection-group \
    --name <COLLECTION_GROUP_NAME>

# Delete associated security policies and access policies
aws opensearchserverless list-security-policies --type encryption \
    --query "securityPolicySummaries[?contains(name, '<STACK_NAME>')]"

aws opensearchserverless delete-security-policy \
    --name <POLICY_NAME> --type encryption

aws opensearchserverless delete-security-policy \
    --name <POLICY_NAME> --type network
```

:::note
A collection group is created for every Serverless deployment (its generation is `CLASSIC` or `NEXTGEN`, set by `openSearch.useServerless.nextGen`). Delete the collection before the collection group. All of these resources use a `DESTROY` removal policy, so AWS CloudFormation deletes them automatically on stack teardown; the commands above are a fallback for resources orphaned by a failed delete.
:::

### OpenSearch Provisioned

```bash
# List domains
aws opensearch list-domain-names \
    --query "DomainNames[?contains(DomainName, '<STACK_NAME>')]"

# Delete the domain
aws opensearch delete-domain \
    --domain-name <DOMAIN_NAME>
```

## Step 9: Clean up VPC resources

If VAMS was deployed with a VPC (`app.useGlobalVpc.enabled: true`) and the VPC was created by VAMS (not imported), verify VPC endpoints and the VPC itself are deleted.

```bash
# List VPC endpoints associated with VAMS
aws ec2 describe-vpc-endpoints \
    --filters "Name=vpc-id,Values=<VPC_ID>" \
    --query 'VpcEndpoints[].VpcEndpointId' --output text

# Delete remaining VPC endpoints
for EP in $(aws ec2 describe-vpc-endpoints \
    --filters "Name=vpc-id,Values=<VPC_ID>" \
    --query 'VpcEndpoints[].VpcEndpointId' --output text); do
    echo "Deleting VPC endpoint ${EP}..."
    aws ec2 delete-vpc-endpoints --vpc-endpoint-ids "${EP}"
done
```

:::note[Imported VPCs]
If you imported an external VPC via `app.useGlobalVpc.optionalExternalVpcId`, do **not** delete the VPC or its subnets. Only remove VAMS-created VPC endpoints and security groups.
:::

## Verification

After completing all cleanup steps, verify that no VAMS resources remain.

```bash
# Check for remaining CloudFormation stacks
aws cloudformation list-stacks \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "StackSummaries[?contains(StackName, '<STACK_NAME>')]"

# Check for remaining S3 buckets
aws s3 ls | grep <STACK_NAME>

# Check for remaining DynamoDB tables
aws dynamodb list-tables \
    --query "TableNames[?contains(@, '<STACK_NAME>')]"

# Check for remaining Lambda functions
aws lambda list-functions \
    --query "Functions[?contains(FunctionName, '<STACK_NAME>')].[FunctionName]" \
    --output text

# Check for remaining log groups
aws logs describe-log-groups \
    --log-group-name-prefix "/aws/lambda/<STACK_NAME>" \
    --query 'logGroups[].logGroupName'
```

All of the above commands should return empty results when the uninstall is complete.

## Cost impact after uninstall

The following table describes what stops incurring charges immediately after stack deletion versus resources that continue to incur charges until manually cleaned up.

| Resource                               | Charges stop after `cdk destroy` |          Charges continue until manual cleanup           |
| -------------------------------------- | :------------------------------: | :------------------------------------------------------: |
| AWS Lambda functions                   |               Yes                |                            --                            |
| Amazon API Gateway                     |               Yes                |                            --                            |
| Amazon CloudFront distribution         |               Yes                |                            --                            |
| Application Load Balancer              |               Yes                |                            --                            |
| Amazon DynamoDB tables (data storage)  |                --                |              Yes, until tables are deleted.              |
| Amazon S3 buckets (data storage)       |                --                |       Yes, until buckets are emptied and deleted.        |
| Amazon CloudWatch log groups (storage) |                --                |            Yes, until log groups are deleted.            |
| AWS KMS keys                           |                --                | Yes, until keys are scheduled for and complete deletion. |
| Amazon OpenSearch Service              |                --                |      Yes, until collections or domains are deleted.      |
| Amazon Cognito user pool               |                --                |           Minimal, but remains until deleted.            |
| VPC endpoints                          |                --                |     Yes, hourly charges until endpoints are deleted.     |
| Elastic IP addresses (if ALB)          |                --                |           Yes, if allocated and not released.            |

:::tip[Cost verification]
After completing the uninstall, monitor your AWS billing dashboard for 24-48 hours to confirm that charges from VAMS resources have stopped. Use AWS Cost Explorer to filter costs by the VAMS CloudFormation stack tag if tags were applied during deployment.
:::

## Related resources

-   [Deploy the solution](deploy-the-solution.md)
-   [Update the solution](update-the-solution.md)
-   [External S3 setup](external-s3-setup.md)
