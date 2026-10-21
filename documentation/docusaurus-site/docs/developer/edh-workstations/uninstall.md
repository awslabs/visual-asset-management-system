---
sidebar_label: Uninstall
---

# Uninstall the EDH Workstations Add-on

This page describes how to remove what the EDH Workstations add-on (`app.addons.useEdhWorkstations`) leaves outside the VAMS stack when you uninstall VAMS. The steps follow the order of [Uninstall the solution](../../deployment/uninstall.md): complete the pre-uninstall steps while the deployment is still running, destroy the stack, then run the cleanup on this page together with the core cleanup steps. The pre-uninstall steps also apply before you remove the add-on by disabling it and redeploying, which deletes the add-on's nested stack; see [Disable or remove the add-on](enable.md#disable-or-remove-the-add-on).

## Pre-uninstall steps

When the EDH Workstations add-on (`app.addons.useEdhWorkstations.enabled`) is deployed, complete these steps while the deployment is still reachable. The desktops, software stacks and directory users the add-on creates live in EDH rather than in the VAMS stack, and once the stack is gone nothing enforces workstation leases, revokes session credentials or retires the images VAMS registered.

Complete the core [Pre-uninstall backup](../../deployment/uninstall.md#pre-uninstall-backup) as well. Its [Record stack resource identifiers](../../deployment/uninstall.md#record-stack-resource-identifiers) step records the AWS CodeBuild projects of the add-on's container image builds together with those of the pipelines, and [Delete the EDH log groups](#delete-the-edh-log-groups) relies on that record.

### Delete the EDH desktops

If the add-on creates EDH directory users (`autoProvisionUsers`), note each user's EDH uid for the EDH administrator before deleting any desktop — see [Objects left in EDH](#objects-left-in-edh). The **Sessions** tab of the **Workstation Admin** page shows the uid under the VAMS user id in the **Owner** column, and `vamscli edh admin instances list --json-output` returns it as `owner`. Both list only the users who still have a desktop in EDH. The uid of any other user derives from the VAMS user id (see [Identity mapping](overview.md#identity-mapping)), and the EDH administrator can recognize these users by their 16-character name beginning with `v`.

Each user then deletes their own workstations on the **My Workstations** page (**Delete**). An administrator deactivates any user's session on the **Sessions** tab (**Deactivate**), or with the CLI:

```bash
vamscli edh admin instances list
vamscli edh admin instances deactivate <SESSION_ID> --confirm
```

Deleting or deactivating a workstation deletes the desktop in EDH, revokes its session API key and deletes its session secret. A desktop left running keeps incurring charges in the EDH cluster after the stack is gone.

### Retire AMI builds and delete recipes

With the AMI builds feature (`amiBuilds.enabled`), retire each `AVAILABLE` build on the **AMI Recipes** tab (**Retire**) or with the CLI. Retiring a build deactivates its EDH software stack, then deregisters its AMI and snapshots and deletes its EC2 Image Builder image once no Amazon EC2 instance uses the AMI; with the desktops deleted first, the AMI build sweep removes a kept AMI within 30 minutes, and an AMI still kept when its recipe or the stack is deleted is removed in [Remove EDH AMIs](#remove-edh-amis). Then delete each recipe whose source is `admin` (created on the **AMI Recipes** tab, with `vamscli edh admin ami-recipe create` or `ami-recipe duplicate`, or through the API), which removes the Image Builder pipeline, the image recipe version, the components and both configurations of its latest build, together with its table rows:

```bash
vamscli edh admin ami-recipe list
vamscli edh admin ami-recipe builds <RECIPE_ID>
vamscli edh admin ami-recipe retire <RECIPE_ID> <VERSION> --confirm
vamscli edh admin ami-recipe delete <RECIPE_ID> --confirm
```

A recipe is not deleted while one of its builds is `BUILDING` or `REGISTERING`. Retiring works for every recipe, but a recipe whose source is `config` — one seeded from the configuration — cannot be deleted (the API answers `409`); removing it from the configuration only disables it, so its Image Builder pipeline, image recipes, components and configurations remain. Neither step removes the AMIs, snapshots and images of builds that a later build already superseded, or the earlier image recipe and component versions, or the AMIs of builds left `REGISTER_FAILED`, so [Remove EDH AMIs](#remove-edh-amis) is still needed after teardown. See [Registration and retirement](overview.md#registration-and-retirement) for how builds are retired and pruned.

### Delete the registered EDH images

The images registered from `predefinedImages`, and those registered on the **Images** tab, are EDH software stacks that stack deletion does not remove: the deploy-time registration does nothing on stack deletion. Delete them on the **Images** tab (**Delete**) or with the CLI, which deactivates each software stack in EDH. The AMIs behind the predefined images belong to the operator and are not changed.

```bash
vamscli edh admin images list
vamscli edh admin images delete-vdi <IMAGE_ID> --confirm
vamscli edh admin images delete-simulation <IMAGE_ID> --confirm
```

### Revoke the EDH ALB ingress rule

A rule added to the EDH ALB security group to admit the add-on's Lambda functions (see [EDH add-on Lambda security group](resources.md#edh-add-on-lambda-security-group)) names `EdhAddonLambdaSecurityGroup` as its source. That rule is not a VAMS resource, and Amazon EC2 refuses to delete a security group that a rule of another group in the same VPC references, so while the rule remains the add-on's nested stack ends in `DELETE_FAILED` with a `DependencyViolation`. Revoke it before running `cdk destroy`. In the commands on this page, `<CONFIG_NAME>` and `<BASE_STACK_NAME>` are the `name` and `app.baseStackName` values of `config.json` and `<REGION>` is the VAMS Region:

```bash
# The add-on Lambda security group of this deployment
SG_ID=$(aws ssm get-parameter \
    --name "/<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>/resourceNames/securityGroups/edhAddonLambda" \
    --query 'Parameter.Value' --output text)

# Every rule that references it: the group holding the rule, the rule id, and whether it is egress
aws ec2 describe-security-group-rules \
    --query "SecurityGroupRules[?ReferencedGroupInfo.GroupId=='${SG_ID}'].[GroupId,SecurityGroupRuleId,IsEgress]" \
    --output text

# Revoke each ingress rule listed
aws ec2 revoke-security-group-ingress \
    --group-id <EDH_ALB_SECURITY_GROUP_ID> \
    --security-group-rule-ids <SECURITY_GROUP_RULE_ID>
```

When the EDH cluster runs in another VPC, a rule that references the group across the peering connection does not block the deletion but is left stale; revoke it as well.

## Destroy the stack

Destroy the stack as described in [Step 1: Destroy the CDK stack](../../deployment/uninstall.md#step-1-destroy-the-cdk-stack). If the destroy fails with a `DependencyViolation` on an EDH security group: for `EdhAddonLambdaSecurityGroup`, revoke the EDH ALB ingress rule that references it (see [Revoke the EDH ALB ingress rule](#revoke-the-edh-alb-ingress-rule)). For the EDH AMI build security group, wait until no AMI build is running: a build instance holds the group until it terminates. Then retry.

## Clean up after teardown

Run the core cleanup steps of [Uninstall the solution](../../deployment/uninstall.md), Step 2 through Step 11, together with the steps below. The core steps also remove these add-on resources:

-   **Amazon DynamoDB tables** — the add-on's session and AMI recipe tables use a `RETAIN` removal policy and carry no custom name, like every VAMS table, so they survive `cdk destroy`, never block a redeploy, and are deleted with the other tables in [Step 3](../../deployment/uninstall.md#step-3-delete-dynamodb-tables).
-   **AMI recipe bundles** — the artefacts bucket holds the EDH AMI recipe bundles under `addon/edh/amiRecipes/`, which go with the bucket in [Step 2](../../deployment/uninstall.md#step-2-delete-s3-buckets).
-   **Resource-name parameters** — the add-on's [AWS Systems Manager parameters](resources.md#aws-systems-manager-parameters) (`addon/dynamoTables/edhSessionStorage`, `addon/dynamoTables/edhAmiRecipeStorage` and `securityGroups/edhAddonLambda`) sit under the deployment's `.../resourceNames/` prefix and are deleted with the stack; [Step 6](../../deployment/uninstall.md#step-6-delete-aws-systems-manager-parameters) removes them with the other parameters if a stack deletion fails partway.

The AMIs and snapshots VAMS built, the EC2 Image Builder objects, the AWS Batch job definitions, the session secrets, and the EDH software stacks, desktops and directory users the add-on created are not stack resources; the following sections cover them.

### Delete the EDH log groups

The add-on's image builds write to two families of log groups that the services create themselves rather than the stack, so their retention is **never expire** and `cdk destroy` leaves them in place:

-   `/aws/codebuild/<project name>` — AWS CodeBuild build logs, one per entry of `app.addons.useEdhWorkstations.containers.buildAndPushImages[]`. The project names are among those recorded in [Record stack resource identifiers](../../deployment/uninstall.md#record-stack-resource-identifiers), so the AWS CodeBuild loop in [Step 4](../../deployment/uninstall.md#step-4-delete-amazon-cloudwatch-log-groups) deletes them.
-   `/aws/imagebuilder/<prefix>-<recipeId>` — EC2 Image Builder build logs of the EDH AMI recipes, where the prefix is `<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh` lower-cased.

```bash
# Delete the EC2 Image Builder log groups of the EDH AMI recipes
EDH_PREFIX=$(echo "<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh" | tr '[:upper:]' '[:lower:]')
for LG in $(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/imagebuilder/${EDH_PREFIX}-" \
    --query 'logGroups[].logGroupName' --output text); do
    echo "Deleting log group ${LG}..."
    aws logs delete-log-group --log-group-name "${LG}"
done
```

### Delete orphaned EDH Amazon ECR repositories

Each EDH container image (`app.addons.useEdhWorkstations.containers.buildAndPushImages[]`) has an explicitly named repository, `<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh-<image name>` lower-cased, with `-kms` appended when `app.useKmsCmkEncryption.enabled` is `true`. The prefix listing below finds both forms. With the CMK enabled, deleting a repository retires the grant Amazon ECR holds on the VAMS key, so the caller needs `kms:RetireGrant` on the key. Like the pipeline repositories in [Step 5](../../deployment/uninstall.md#step-5-delete-orphaned-amazon-ecr-repositories), it uses the `DESTROY` removal policy with `emptyOnDelete`, so a clean teardown removes it with its images. The listing below matches names case-sensitively, so it lower-cases the prefix first. Delete any repository left behind before redeploying with the same configuration name and account:

```bash
EDH_PREFIX=$(echo "<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh" | tr '[:upper:]' '[:lower:]')

aws ecr describe-repositories \
    --query "repositories[?starts_with(repositoryName, '${EDH_PREFIX}-')].repositoryName" \
    --output text

# Delete each repository the listing prints
aws ecr delete-repository \
    --repository-name "<REPOSITORY_NAME>" \
    --force
```

### Deregister EDH job definitions

Job definitions the EDH Workstations add-on registered with AWS Batch (`app.addons.useEdhWorkstations.containers.jobDefinitions`) are not stack resources and survive `cdk destroy`. Each carries the resource tag `edh:visibility:<EDH_CLUSTER_ID>` with the value `true`, but that is the tag EDH itself lists job definitions by, and VAMS writes no deployment tag, so the tag alone also selects the definitions EDH users and other deployments registered for the same cluster. Deregister by the names in `containers.jobDefinitions` instead — including any entry removed from the configuration earlier, which stays registered — and only the revisions that carry the tag:

```bash
for NAME in <JOB_DEFINITION_NAME> <JOB_DEFINITION_NAME>; do
    for JD in $(aws batch describe-job-definitions --status ACTIVE \
        --job-definition-name "${NAME}" \
        --query "jobDefinitions[?tags.\"edh:visibility:<EDH_CLUSTER_ID>\"=='true'].jobDefinitionArn" \
        --output text); do
        aws batch deregister-job-definition --job-definition "${JD}"
    done
done
```

A deregistered job definition keeps its name and revision number, so a later deployment registers a new revision rather than colliding.

### Remove EDH session secrets

A deployment with the EDH Workstations add-on's session bootstrap enabled creates one AWS Secrets Manager secret per launched Linux desktop at runtime, named `<secretNamePrefix><bootstrapId>` — with the default prefix `vams/edh-session/`, that is `vams/edh-session/<bootstrapId>`. The add-on removes a secret when its desktop is deleted or found orphaned, but a desktop still running at teardown leaves its secret behind, and those secrets are encrypted under the add-on's `EdhSessionSecretsKey`, which `cdk destroy` schedules for deletion. Remove the secrets before that key's pending-deletion window ends.

The `EdhSessionSecretsKey` sits in the `EdhWorkstationsBuilderNestedStack` nested stack with a `DESTROY` removal policy, which is why `cdk destroy` schedules it for deletion; it is not the key that [Step 8: Delete the AWS KMS key](../../deployment/uninstall.md#step-8-delete-the-aws-kms-key) resolves. Deployments share the default prefix unless `sessionBootstrap.secretNamePrefix` is changed, so select this deployment's secrets by the key that encrypts them. The `EdhSessionSecretsKey` carries the `vams:stackname` tag and the description `VAMS EDH workstation session secrets`, so the tag loop in [Identify the key](../../deployment/uninstall.md#identify-the-key) prints its key id alongside the VAMS-generated key:

```bash
SESSION_KEY_ID="<EdhSessionSecretsKey key id>"

# List this deployment's session secrets (adjust the prefix if sessionBootstrap.secretNamePrefix was changed)
aws secretsmanager list-secrets \
    --filters Key=name,Values=vams/edh-session/ \
    --query "SecretList[?KmsKeyId && contains(KmsKeyId, '${SESSION_KEY_ID}')].Name" --output text

# Delete each one without a recovery window
for S in $(aws secretsmanager list-secrets --filters Key=name,Values=vams/edh-session/ \
    --query "SecretList[?KmsKeyId && contains(KmsKeyId, '${SESSION_KEY_ID}')].Name" --output text); do
    aws secretsmanager delete-secret --secret-id "${S}" --force-delete-without-recovery
done
```

The temporary session API keys those secrets carried, named `temporary-session-edh-<bootstrapId>`, or `temporary-session-edh-<bootstrapId>-r<YYYYmmddHHMMSS>` for a key a lease renewal re-issued, authorize nothing once the API is gone, and their rows go with the API key table in [Step 3](../../deployment/uninstall.md#step-3-delete-dynamodb-tables). While the deployment is still running they are listed on the API Keys page or with `vamscli api-key list`, and they expire on their own at their `expiresAt`.

### Remove EDH AMIs

The AMI recipes feature (`app.addons.useEdhWorkstations.amiBuilds`) leaves two kinds of object behind. Every AMI it built and its snapshots carry the tag `vams:deployment` with the deployment's EDH prefix, `<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh` lower-cased. Select them by that tag: the `vams:edhAmiRecipe` tag they also carry is common to every VAMS deployment in the account and Region, so a listing on it returns the AMIs of other deployments as well.

```bash
EDH_PREFIX=$(echo "<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh" | tr '[:upper:]' '[:lower:]')

aws ec2 describe-images --owners self --filters "Name=tag:vams:deployment,Values=${EDH_PREFIX}" \
    --query "Images[].[ImageId,Name,BlockDeviceMappings[].Ebs.SnapshotId|join(',',@)]" --output text
# deregister each AMI, then delete its snapshots
aws ec2 deregister-image --image-id <ami-id>
aws ec2 delete-snapshot --snapshot-id <snapshot-id>

# Snapshots that remain once the AMIs are deregistered
aws ec2 describe-snapshots --owner-ids self --filters "Name=tag:vams:deployment,Values=${EDH_PREFIX}" \
    --query "Snapshots[].[SnapshotId,StartTime]" --output text
```

The EC2 Image Builder objects are created at run time from recipe content and are not stack resources, so stack deletion leaves them in place. Each recipe has one image pipeline, image recipe, infrastructure configuration and distribution configuration named `<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh-<recipeId>`, plus one component per component the recipe defines itself, named `<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh-<recipeId>-cNN-<slug>`, and each build leaves an image named for its image recipe. VAMS lower-cases these names, and the listings below match them case-sensitively, so each block lower-cases the prefix first. Delete the objects in this order, so that none is deleted while another still references it:

```bash
EDH_PREFIX=$(echo "<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh" | tr '[:upper:]' '[:lower:]')

# Images (deleting an image does not deregister the AMI it produced)
aws imagebuilder list-images --owner Self \
    --query "imageVersionList[?starts_with(name, '${EDH_PREFIX}-')].[name,arn]" \
    --output text
aws imagebuilder list-image-build-versions --image-version-arn <IMAGE_VERSION_ARN> \
    --query "imageSummaryList[].arn" --output text
aws imagebuilder delete-image --image-build-version-arn <IMAGE_BUILD_VERSION_ARN>

# Image pipelines
aws imagebuilder list-image-pipelines \
    --query "imagePipelineList[?starts_with(name, '${EDH_PREFIX}-')].[name,arn]" \
    --output text
aws imagebuilder delete-image-pipeline --image-pipeline-arn <PIPELINE_ARN>

# Image recipes (one version per distinct recipe content and parent AMI)
aws imagebuilder list-image-recipes --owner Self \
    --query "imageRecipeSummaryList[?starts_with(name, '${EDH_PREFIX}-')].[name,arn]" \
    --output text
aws imagebuilder delete-image-recipe --image-recipe-arn <IMAGE_RECIPE_ARN>

# Components VAMS created; the managed AWS components a recipe references are not account-owned
aws imagebuilder list-components --owner Self \
    --query "componentVersionList[?starts_with(name, '${EDH_PREFIX}-')].[name,arn]" \
    --output text
aws imagebuilder list-component-build-versions --component-version-arn <COMPONENT_VERSION_ARN> \
    --query "componentSummaryList[].arn" --output text
aws imagebuilder delete-component --component-build-version-arn <COMPONENT_BUILD_VERSION_ARN>

# Infrastructure configurations
aws imagebuilder list-infrastructure-configurations \
    --query "infrastructureConfigurationSummaryList[?starts_with(name, '${EDH_PREFIX}-')].[name,arn]" \
    --output text
aws imagebuilder delete-infrastructure-configuration \
    --infrastructure-configuration-arn <INFRASTRUCTURE_CONFIGURATION_ARN>

# Distribution configurations
aws imagebuilder list-distribution-configurations \
    --query "distributionConfigurationSummaryList[?starts_with(name, '${EDH_PREFIX}-')].[name,arn]" \
    --output text
aws imagebuilder delete-distribution-configuration \
    --distribution-configuration-arn <DISTRIBUTION_CONFIGURATION_ARN>
```

[Retire AMI builds and delete recipes](#retire-ami-builds-and-delete-recipes) removes only part of these objects, so run the listings above after teardown as well.

:::note[The EC2 Image Builder service-linked role]
EC2 Image Builder creates the account-wide service-linked role `AWSServiceRoleForImageBuilder` the first time it is used in the account. The role is not a stack resource, so `cdk destroy` leaves it in place, and every other EC2 Image Builder user in the account shares it. Delete it only when nothing else in the account uses EC2 Image Builder:

```bash
aws iam delete-service-linked-role --role-name AWSServiceRoleForImageBuilder
```

:::

### Objects left in EDH

The following remain in the EDH cluster after teardown unless they were removed in [Pre-uninstall steps](#pre-uninstall-steps). They belong to the EDH administrator, and VAMS cannot reach them once the stack is gone:

-   **Software stacks** — the images registered from `predefinedImages` or on the **Images** tab, and the stacks AMI builds registered, named `<stackName> v<version>`. The deploy-time registration does nothing on stack deletion.
-   **Desktops** — a desktop not deleted before teardown keeps running and incurring charges in the EDH cluster, with no lease enforcement.
-   **Directory users** — the EDH directory users the launch worker created when `autoProvisionUsers` is `true`, each named by a 16-character uid beginning with `v` derived from the VAMS user id (see [Identity mapping](overview.md#identity-mapping)). VAMS never deletes them.
-   **AWS Batch job definitions** — registered in this account's AWS Batch rather than in EDH, which lists them by their `edh:visibility:<EDH_CLUSTER_ID>` tag; deregister them after teardown as described in [Deregister EDH job definitions](#deregister-edh-job-definitions).

## Verification

After the core [Verification](../../deployment/uninstall.md#verification), check for remaining EDH AMIs and EC2 Image Builder log groups:

```bash
# EDH Workstations add-on: check for remaining AMIs and EC2 Image Builder log groups
EDH_PREFIX=$(echo "<CONFIG_NAME>-<BASE_STACK_NAME>-<REGION>-edh" | tr '[:upper:]' '[:lower:]')
aws ec2 describe-images --owners self \
    --filters "Name=tag:vams:deployment,Values=${EDH_PREFIX}" \
    --query 'Images[].ImageId'
aws logs describe-log-groups \
    --log-group-name-prefix "/aws/imagebuilder/${EDH_PREFIX}-" \
    --query 'logGroups[].logGroupName'
```

Both commands should return empty results when the uninstall is complete.

## Cost impact after uninstall

The add-on leaves these charges in addition to those in [Cost impact after uninstall](../../deployment/uninstall.md#cost-impact-after-uninstall):

| Resource                                | Charges stop after `cdk destroy` |                                    Charges continue until manual cleanup                                     |
| --------------------------------------- | :------------------------------: | :----------------------------------------------------------------------------------------------------------: |
| Amazon EBS snapshots of EDH AMIs        |                --                |   Yes, until the AMIs are deregistered and their snapshots deleted ([Remove EDH AMIs](#remove-edh-amis)).    |
| EDH desktops left running               |                --                | Yes, in the EDH cluster, until the desktops are deleted there ([Objects left in EDH](#objects-left-in-edh)). |
| AWS Secrets Manager EDH session secrets |                --                | Yes, a monthly charge per secret until deleted ([Remove EDH session secrets](#remove-edh-session-secrets)).  |

## Related resources

-   [Uninstall the solution](../../deployment/uninstall.md)
-   [Enable the EDH Workstations Add-on](enable.md)
-   [EDH Workstations Add-on](overview.md)
-   [EDH Workstations Add-on Resources](resources.md)
