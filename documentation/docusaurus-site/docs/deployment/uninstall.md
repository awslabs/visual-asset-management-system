# Uninstall the solution

This page describes how to completely remove VAMS from your AWS account, including the CDK stack destruction and manual cleanup of retained resources.

:::danger[Permanent data loss]
Uninstalling VAMS removes the AWS Lambda functions, Amazon API Gateway endpoints, and other managed resources for the deployment. The Amazon DynamoDB tables and the asset, auxiliary, artefacts, and access logs Amazon Simple Storage Service (Amazon S3) buckets are retained by design and are deleted only by the manual cleanup steps below, which permanently destroy their contents. This action cannot be undone. Ensure you have backed up all data you intend to keep before proceeding.

When the deployment uses AWS Key Management Service (AWS KMS) customer-managed key encryption (`app.useKmsCmkEncryption.enabled: true`), the VAMS-generated key is retained with the same design, which is what keeps the retained tables and buckets readable after the stack is gone. Scheduling that key for deletion makes their contents permanently undecryptable, so delete the key last — see [Step 7](#step-7-delete-the-aws-kms-key).
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

Save the CloudFormation stack outputs and resource identifiers for reference during manual cleanup. Most VAMS resources live in nested stacks, so record those as well — this is where the physical identifier of the retained AWS KMS key is captured for [Step 7](#step-7-delete-the-aws-kms-key):

```bash
aws cloudformation describe-stacks \
    --stack-name <VAMS_STACK_NAME> \
    --query 'Stacks[0].Outputs' \
    --output json > vams-stack-outputs.json

aws cloudformation describe-stack-resources \
    --stack-name <VAMS_STACK_NAME> \
    --output json > vams-stack-resources.json

# Record the resources of each nested stack, including the retained KMS key
for STACK in $(aws cloudformation list-stack-resources \
    --stack-name <VAMS_STACK_NAME> \
    --query "StackResourceSummaries[?ResourceType=='AWS::CloudFormation::Stack'].PhysicalResourceId" \
    --output text); do
    aws cloudformation list-stack-resources \
        --stack-name "${STACK}" \
        --query 'StackResourceSummaries[].[ResourceType,LogicalResourceId,PhysicalResourceId]' \
        --output text >> vams-nested-stack-resources.txt
done
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

Then manually delete the retained resources using the steps in Step 2 through Step 10.

:::note[Amazon SQS queues are deleted with the stack]
Every VAMS Amazon SQS queue uses a `DESTROY` removal policy, so none of the steps below covers one. That includes the file and asset indexer queues, the two Physna sync queues when Physna sync is enabled, and the dead-letter queue each of them redrives to. The dead-letter queues are auto-named by AWS CloudFormation and never conflict with a redeploy; the bucket sync, indexer, Physna sync, and Garnet queues carry explicit names of the form `<configuration name>-<app.baseStackName>-<purpose>`, and the large file processing queue one of the form `<configuration name>-<env.coreStackName>-sqsUploadLargeFile-queue`. If a teardown fails partway, delete any queue left behind before redeploying with the same configuration name and the same `app.baseStackName` into the same account and Region.

Check the indexer dead-letter queues before deleting them. They hold the asset and file records the indexer could not add to the search index, and a redeploy does not replay them — run a reindex to rebuild the index for the affected assets and files.

Check the Physna sync dead-letter queues the same way when Physna sync is enabled. They hold the file and asset sync events that never reached Physna, and a redeploy does not replay them — anything left in them is a file or asset the Physna tenant does not have.
:::

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

VAMS creates the following S3 buckets. The asset, auxiliary, artefacts, access logs, and GPU model cache buckets use a `RETAIN` removal policy and require manual deletion. The web app bucket and its access logs bucket are emptied and deleted automatically during stack teardown, so they normally require manual deletion only if the stack deletion fails partway.

| Bucket                     | Removal on teardown     | Blocks redeploy if left behind?     | Description                                                                                                                                                                    |
| -------------------------- | ----------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Asset bucket(s)            | Retained (manual)       | No — auto-named                     | Stores uploaded asset files. One bucket per configuration (new bucket and/or external).                                                                                        |
| Auxiliary bucket           | Retained (manual)       | No — auto-named                     | Stores auto-generated previews, pipeline working files, and viewer data.                                                                                                       |
| Artefacts bucket           | Retained (manual)       | No — auto-named                     | Stores CDK deployment artefacts.                                                                                                                                               |
| Access logs bucket         | Retained (manual)       | No — auto-named                     | Stores S3 server access logs.                                                                                                                                                  |
| Model cache bucket(s)      | Retained (manual)       | No — auto-named                     | Caches downloaded model weights for the NVIDIA Cosmos and NVIDIA GR00T pipelines. Present only when `useNvidiaCosmos`, `useNvidiaCosmos3`, or `useNvidiaGr00t` was enabled. |
| Web app bucket             | Deleted (emptied first) | ALB only — fixed name (domain host) | Stores the built frontend static files (for both CloudFront and ALB deployments).                                                                                              |
| Web app access logs bucket | Deleted (emptied first) | ALB only — fixed name (domain host) | Stores access logs for the web app bucket and ALB.                                                                                                                             |

:::note[Retained does not mean it blocks a redeploy]
The retained asset, auxiliary, artefacts, access logs, and model cache buckets are **auto-named** by AWS CloudFormation, so they can be left in place when redeploying with the same configuration name — they will not cause a name collision. Delete them only when you intend to permanently remove the stored data. By contrast, under ALB deployments the web app bucket and its access logs bucket carry fixed names derived from the configured domain host; if a teardown fails and leaves either behind, delete it before redeploying with the same domain host to avoid a bucket-name collision.
:::

:::warning[Check for orphaned ALB web buckets before redeploying into ALB mode]
The two domain-host-named buckets, `{domainHost}` and `{domainHost}-webappaccesslogs`, are the only VAMS buckets whose names can collide, and a switch of the web distribution mode renames them as surely as a teardown deletes them. Whether the stack was torn down or reconfigured from ALB to Amazon CloudFront, list both names before deploying into ALB mode again and delete any that remain:

```bash
DOMAIN_HOST="vams.example.com"   # app.useAlb.domainHost

for BUCKET in "${DOMAIN_HOST}" "${DOMAIN_HOST}-webappaccesslogs"; do
    aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null && echo "${BUCKET} still exists"
done
```

Both hold only the built frontend files and its access logs, so removing them destroys no asset data. A deployment that hits the collision fails with `AlreadyExists` on the bucket name and rolls the nested stack back — see [Switching between CloudFront and ALB](deploy-the-solution.md#switching-between-cloudfront-and-alb).
:::

:::warning[Model cache buckets can hold hundreds of gigabytes]
A model cache bucket holds the model weights the GPU pipelines download on first use — a single Cosmos 3 Super checkpoint is roughly 133 GB — so it is often the largest retained bucket in a deployment and the one whose ongoing Amazon S3 charges are most noticeable. The `aws s3 ls | grep <STACK_NAME>` listing above includes it; it is an expected bucket, not a stray. The Amazon EFS filesystem that the same pipelines use as their working model cache is deleted with the stack and needs no manual step.
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
-   `aws-waf-logs-vams-{hash}` — AWS WAF request logs, one per web ACL (conditional on `useWaf`). Outside the `/aws/vendedlogs/` namespace because AWS WAF requires the `aws-waf-logs-` prefix, and the CloudFront ACL's group is in us-east-1 rather than the deployment Region
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

:::note[An Amazon Location Service API key orphaned by an earlier release]
When Amazon Location Service is enabled, VAMS creates an API key named
`vams-location-api-key-<name>-<baseStackName>` and deletes it with the stack. Earlier releases retained
it, so a deployment that has ever had a failed-and-rolled-back deploy may still have a key left behind
from that release. It costs nothing while unused, but its name is deterministic, so it conflicts with a
redeploy that uses the same configuration name, stack name, and Region. List and remove any that remain:

```bash
aws location list-keys --query "Entries[].KeyName" --output text
aws location delete-key --key-name "vams-location-api-key-<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>"
```

A key created with no expiry deletes immediately. `--force-delete` is available if a key was given an
expiry in the past and is therefore subject to the deprecation waiting period.
:::

## Step 6: Delete the API Gateway account CloudWatch role

VAMS provisions the account-level API Gateway CloudWatch role that stage execution and access logging
requires. Both the `AWS::ApiGateway::Account` resource and the IAM role it points at use a `RETAIN`
removal policy, so they survive stack teardown.

That retention is deliberate. `AWS::ApiGateway::Account` holds **one** CloudWatch role ARN per AWS
account and Region, and every REST API in that account and Region delivers its stage logs through it.
Deleting the role while the account setting still references it leaves API Gateway configured with a
role ARN that no longer exists, which stops log delivery for **every** REST API there — including other
VAMS deployments and unrelated workloads that share the account and Region.

The role is explicitly named. A CDK aspect names every IAM role in the stack, producing a fixed name of
the form `<unique>CloudWatchRole<suffix>-<region>`, so the name embeds the configuration and base stack
name and two co-resident VAMS deployments do not collide on it. A retained orphan does, however,
conflict with the role that a redeploy of **the same** configuration into the same account and Region
creates.

```bash
# Find the retained role for this deployment
aws iam list-roles \
    --query "Roles[?contains(RoleName, 'CloudWatchRole')].RoleName" --output text

# Confirm what the account-level setting currently points at, in this Region
aws apigateway get-account --query 'cloudwatchRoleArn' --output text

# Delete only when the ARN above does NOT name this role, or no other REST API
# in this account and Region needs log delivery
aws iam delete-role --role-name "<ROLE_NAME>"
```

:::warning[Check the account setting before deleting]
Delete this role only when redeploying the same configuration into the same account and Region, and
only after confirming that `aws apigateway get-account` does not point at it — or that no other REST
API in that account and Region relies on log delivery. If the setting still names the role you delete,
re-point it by redeploying VAMS or by setting `cloudwatchRoleArn` to a valid role.
:::

## Step 7: Delete the AWS KMS key

When VAMS is deployed with KMS CMK encryption (`app.useKmsCmkEncryption.enabled: true`) and the key is created by VAMS rather than imported, the key uses a `RETAIN` removal policy and survives stack teardown. The retained DynamoDB tables and S3 buckets are encrypted under it, so the key must outlive them: a key in the pending-deletion state is disabled, and every read of a table or object encrypted under it fails, including point-in-time recovery restores.

Delete the key only after the other cleanup steps have removed the data encrypted under it: the retained buckets and tables in [Step 2](#step-2-delete-s3-buckets) and [Step 3](#step-3-delete-dynamodb-tables), and any Amazon OpenSearch Service collection or domain left behind by a failed teardown ([Step 9](#step-9-delete-amazon-opensearch-service-resources)), which is encrypted under the same key. Deleting the key is a deliberate final action, not part of the teardown.

### Identify the key

Every VAMS-generated key carries the same description, `VAMS Generated KMS Encryption key`, and VAMS creates no KMS alias, so the description does not distinguish the key of one deployment from the key of another. Resolve the key from the AWS CloudFormation record of the stack that created it: it is the single `AWS::KMS::Key` resource in the deployment's storage nested stack, and its logical ID begins with `VAMSEncryptionKMSKey`. If you completed [Record stack resource identifiers](#record-stack-resource-identifiers) before teardown, the key id is already in `vams-nested-stack-resources.txt`.

AWS CloudFormation keeps the resource records of a deleted stack for 90 days, but a deleted stack is addressed by its unique stack ID rather than its name, so resolve the stack ID first. Successive deployments of the same stack name each have their own stack ID and creation time, which is what tells their keys apart:

```bash
# Resolve the stack ID (list-stacks includes stacks deleted within the last 90 days)
aws cloudformation list-stacks \
    --query "StackSummaries[?StackName=='<VAMS_STACK_NAME>'].[StackId,StackStatus,CreationTime]" \
    --output text

STACK_ID="<VAMS_STACK_ID>"

# Read the key id from the root stack and its nested stacks
for STACK in "${STACK_ID}" $(aws cloudformation list-stack-resources \
    --stack-name "${STACK_ID}" \
    --query "StackResourceSummaries[?ResourceType=='AWS::CloudFormation::Stack'].PhysicalResourceId" \
    --output text); do
    aws cloudformation list-stack-resources \
        --stack-name "${STACK}" \
        --query "StackResourceSummaries[?ResourceType=='AWS::KMS::Key'].[LogicalResourceId,PhysicalResourceId]" \
        --output text
done
```

If the stack was deleted more than 90 days ago, match the key on its tags instead. VAMS tags the key with `vams:stackname` set to the deployment's stack name, and `describe-key` reports the creation date, which distinguishes keys left behind by successive deployments of that same stack name:

```bash
for KEY in $(aws kms list-keys --query 'Keys[].KeyId' --output text); do
    STACK_TAG=$(aws kms list-resource-tags --key-id "${KEY}" \
        --query "Tags[?TagKey=='vams:stackname'].TagValue" --output text 2>/dev/null)
    if [ "${STACK_TAG}" = "<VAMS_STACK_NAME>" ]; then
        aws kms describe-key --key-id "${KEY}" \
            --query 'KeyMetadata.[KeyId,KeyState,CreationDate,Description]' --output text
    fi
done
```

### Schedule the key for deletion

```bash
# Schedule key deletion (minimum 7-day waiting period)
aws kms schedule-key-deletion \
    --key-id <KEY_ID> \
    --pending-window-in-days 30
```

:::danger[Confirm the key belongs to the deployment you are removing]
Never select the key by description alone. All VAMS-generated keys share the same description, so in an account that holds more than one VAMS deployment — or that has redeployed the same stack name — a key chosen that way can belong to a live deployment, and scheduling it for deletion makes that deployment's retained tables and buckets unreadable. Confirm the key id against the CloudFormation record or the `vams:stackname` tag of the deployment you are removing before running `schedule-key-deletion`.
:::

:::warning[KMS key waiting period]
AWS KMS enforces a minimum 7-day and maximum 30-day waiting period before a key is permanently deleted. During this period, the key is disabled but can be canceled. Use a 30-day window to allow time for discovering any remaining encrypted resources.
:::

:::note[A retained key does not block a redeploy]
The VAMS-generated key has no KMS alias and is addressed only by its generated key id and ARN, so leaving it in place does not cause a name collision when redeploying with the same configuration name and account. A redeploy creates its own key; the retained key remains available for decrypting data left behind by the previous deployment. Keys that remain enabled continue to incur a monthly charge.
:::

:::danger[External KMS keys]
If you provided an external KMS key via `app.useKmsCmkEncryption.optionalExternalCmkArn`, do **not** delete that key. It may be in use by other applications. Only remove the VAMS-specific key policy statements.
:::

## Step 8: Delete the Amazon Cognito user pool

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

## Step 9: Delete Amazon OpenSearch Service resources

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

## Step 10: Clean up VPC resources

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
| Amazon S3 GPU model cache buckets      |                --                | Yes, until emptied and deleted. Often the largest retained storage cost — a single cached model checkpoint can exceed 100 GB. |
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
