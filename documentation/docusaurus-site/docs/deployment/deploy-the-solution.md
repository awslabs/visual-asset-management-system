# Deploy the solution

This page provides step-by-step instructions for deploying Visual Asset Management System (VAMS) to your AWS account. Before proceeding, ensure you have completed all items on the [Prerequisites](prerequisites.md) page and reviewed the [Plan your deployment](plan-your-deployment.md) page.

:::info[Expected deployment time]
A first-time deployment typically takes **30 to 45 minutes**, depending on the features enabled. Deployments that include Amazon OpenSearch Provisioned or container-based pipelines may take longer.
:::

## Step 1: Clone the repository

Clone the VAMS repository and check out a stable release tag.

```bash
git clone https://github.com/awslabs/visual-asset-management-system.git
cd visual-asset-management-system
```

To use a specific release version, fetch all tags and check out the desired tag:

```bash
git fetch --all --tags
git tag --list
git checkout tags/TAG_NAME
```

Replace `TAG_NAME` with the version you want to deploy (for example, the latest release tag). Stable releases are listed on the [releases page](https://github.com/awslabs/visual-asset-management-system/releases).

:::tip[Production deployments]
Always deploy from a tagged release rather than a development branch. The `main` branch may contain in-progress changes.
:::

## Step 2: Build the frontend

Navigate to the `web/` directory, activate the correct Node.js version, install dependencies, and build the production bundle.

```bash
cd web
nvm use
npm install
npm run build
```

The build output is written to `web/dist/`, which the CDK infrastructure references during deployment.

## Step 3: Install CDK dependencies

Navigate to the `infra/` directory and install the CDK project dependencies.

```bash
cd ../infra
npm install
```

## Step 4: Bootstrap AWS CDK

If you have not already bootstrapped your AWS account and Region for CDK, run the bootstrap command.

**Commercial AWS:**

```bash
cdk bootstrap aws://ACCOUNT_ID/REGION
```

**AWS GovCloud (US):**

```bash
export AWS_REGION=us-gov-west-1
cdk bootstrap aws://ACCOUNT_ID/us-gov-west-1
```

:::warning[GovCloud endpoint resolution]
You must set the `AWS_REGION` environment variable when bootstrapping AWS GovCloud accounts. The AWS SDK requires this to resolve GovCloud service endpoints correctly.
:::

Replace `ACCOUNT_ID` with your 12-digit AWS account ID and `REGION` with your target deployment Region.

## Step 5: Configure the deployment

Edit the configuration file at `infra/config/config.json` to set your deployment parameters. Template files are provided as starting points:

| Template       | File                                           |
| -------------- | ---------------------------------------------- |
| Commercial AWS | `infra/config/config.template.commercial.json` |
| AWS GovCloud   | `infra/config/config.template.govcloud.json`   |

Copy the appropriate template to `config.json` and customize it:

```bash
cp config/config.template.commercial.json config/config.json
```

**Minimum required fields to update:**

| Field                   | Description                                                       | Example             |
| ----------------------- | ----------------------------------------------------------------- | ------------------- |
| `env.region`            | Target AWS Region                                                 | `us-east-1`         |
| `app.adminEmailAddress` | Email for the initial admin account (receives temporary password) | `admin@example.com` |
| `app.adminUserId`       | Username for the initial admin account                            | `administrator`     |
| `app.baseStackName`     | Stack environment name (appended to resource names)               | `prod`              |

For a complete list of configuration options, see the [Configuration Reference](configuration-reference.md).

:::note[Configuration templates]
The GovCloud template pre-configures settings required for AWS GovCloud: VPC enabled, CloudFront disabled, ALB enabled, Location Service disabled, FIPS enabled, and KMS CMK encryption enabled.
:::

## Step 6: Set environment variables (optional)

You can override the Region and stack name at deployment time using environment variables. These take effect only if the corresponding fields in `config.json` are null.

```bash
export AWS_REGION=us-east-1
export STACK_NAME=dev
```

### FIPS endpoints (optional)

If deploying with FIPS-compliant endpoints, set the following environment variable in addition to enabling `app.useFips` in `config.json`:

```bash
export AWS_USE_FIPS_ENDPOINT=true
```

## Step 7: Import an external VPC (conditional)

:::info[Skip this step]
This step is only required if you are importing an existing VPC by setting `app.useGlobalVpc.optionalExternalVpcId` in `config.json`. If you are creating a new VPC or not using a VPC, proceed to [Step 8](#step-8-deploy).
:::

When importing an external VPC, a two-phase deployment is required. The first phase imports VPC context and deploys non-VPC-dependent stacks:

```bash
cdk deploy --all --require-approval never --context loadContextIgnoreVPCStacks=true
```

After this command completes successfully, proceed to Step 8 to deploy all remaining stacks.

:::warning[Two-phase deployment]
Skipping this step when importing an external VPC will cause the deployment to fail with VPC or subnet lookup errors. The `loadContextIgnoreVPCStacks` context flag instructs CDK to skip VPC-dependent nested stacks during the initial synthesis.
:::

## Step 8: Deploy

:::danger[Container engine must be running]
Ensure Docker (or your configured container engine) is running before deploying. CDK builds container images for AWS Lambda layers and pipeline containers during synthesis. The deployment will fail if a container engine is not available.

If you are using an alternative container engine such as [Finch](https://aws.github.io/finch/) or [Podman](https://podman.io/), set the `CDK_DOCKER` environment variable before deploying (for example, `export CDK_DOCKER=finch`). See the [Prerequisites](prerequisites.md#docker-alternatives) page for setup details.
:::

Run the CDK deploy command from the `infra/` directory:

```bash
cdk deploy --all --require-approval never
```

This command synthesizes all AWS CloudFormation templates and deploys every nested stack. The `--require-approval never` flag auto-approves IAM and security-related changes.

### What happens during deployment

1. CDK synthesizes the core stack and all nested stacks (VPC, storage, auth, API, static web, search, pipelines, addons).
2. AWS CloudFormation creates resources in dependency order.
3. An Amazon Cognito user account is created using the `adminEmailAddress` from your configuration.
4. A temporary password is sent to the admin email address from `no-reply@verificationemail.com`.
5. If WAF is enabled, a separate WAF stack is deployed first (in `us-east-1` for CloudFront deployments, or in-Region for ALB deployments).

## Step 9: Verify the deployment

### Find your application URL

The web application URL is displayed in the CDK deployment output.

**CloudFront deployments:**

Look for the output key `WebAppCloudFrontDistributionDomainName` in the deployment output. The URL format is `https://dXXXXXXXXXXXXX.cloudfront.net`.

If you configured a custom domain, also look for `CloudFrontCustomDomainUrl`.

**ALB deployments:**

Look for the output key `webDistributionUrl` in the deployment output. This is the ALB domain you configured in `app.useAlb.domainHost`.

### Sign in

1. Navigate to the application URL in a web browser.
2. Check the admin email address for a message from `no-reply@verificationemail.com` containing a temporary password.
3. Sign in with the admin user ID and temporary password.
4. You will be prompted to set a new password on first login.

### Create additional users (optional)

After signing in as the administrator, you can create additional users through the web interface under **Admin - Auth > User Management** (when Amazon Cognito authentication is active). For detailed instructions, see the [Permissions Guide](../user-guide/permissions.md).

## Multiple deployments in the same account

You can deploy multiple independent instances of VAMS to the same AWS account by changing the `baseStackName` and optionally the Region in `config.json`:

```bash
# First deployment
# config.json: "baseStackName": "prod", "region": "us-east-1"
cdk deploy --all --require-approval never

# Second deployment
# config.json: "baseStackName": "dev", "region": "us-west-2"
cdk deploy --all --require-approval never
```

Alternatively, use environment variables to override the stack name and Region:

```bash
export AWS_REGION=us-west-2
export STACK_NAME=staging
cdk deploy --all --require-approval never
```

Each deployment creates a fully isolated set of resources with unique stack names derived from the `baseStackName` and Region combination.

## Deploying updates

To deploy customizations or updates to an existing VAMS deployment, rebuild the frontend and redeploy:

```bash
cd web && npm install && npm run build
cd ../infra && npm install
cdk deploy --all --require-approval never
```

A CloudFormation changeset is created and applied to your existing stack. Only modified resources are updated.

:::warning[Availability during updates]
Depending on the scope of changes, VAMS may experience partial or full downtime during the deployment. Review the [CHANGELOG](https://github.com/awslabs/visual-asset-management-system/blob/main/CHANGELOG.md) carefully and test changes in a non-production environment first.
:::

:::tip[Major version upgrades]
For major version changes, significant configuration changes (such as switching from CloudFront to ALB, or changing KMS CMK settings), or redeployments to a different Region, use the A/B deployment path with the data migration utility. See `infra/deploymentDataMigration/README.md` for instructions.
:::

## Common deployment errors

| Error                                                                                          | Cause                                                          | Resolution                                                                                                                                                                                                                                           |
| ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Docker daemon not running`                                                                    | Docker (or your configured container engine) is not started.   | Start Docker Desktop (or your alternative container engine such as Finch or Podman) and retry. If using an alternative, ensure `CDK_DOCKER` is set correctly.                                                                                        |
| `Must define a app.assetBuckets.defaultNewBucketSyncDatabaseId`                                | Missing required configuration field.                          | Set `app.assetBuckets.defaultNewBucketSyncDatabaseId` in `config.json` (default: `"default"`).                                                                                                                                                       |
| `Cannot use ALB deployment without specifying a valid domain hostname and ACM Certificate ARN` | ALB enabled without domain or certificate.                     | Provide `app.useAlb.domainHost` and `app.useAlb.certificateArn`.                                                                                                                                                                                     |
| `Must specify an initial admin email address`                                                  | Admin email not configured.                                    | Set `app.adminEmailAddress` to a valid email.                                                                                                                                                                                                        |
| `Must specify either none or one openSearch method`                                            | Both OpenSearch Serverless and Provisioned enabled.            | Enable only one OpenSearch option or disable both.                                                                                                                                                                                                   |
| `Must specify only one authentication method`                                                  | Both Cognito and External OAuth IdP enabled.                   | Enable only one authentication provider.                                                                                                                                                                                                             |
| `GovCloud must have useGlobalVpc.enabled set to true`                                          | GovCloud enabled without VPC.                                  | Set `app.useGlobalVpc.enabled: true` for GovCloud deployments.                                                                                                                                                                                       |
| `Must define either a global VPC Cidr Range or an External VPC ID`                             | VPC enabled without network configuration.                     | Provide either `vpcCidrRange` or `optionalExternalVpcId`.                                                                                                                                                                                            |
| `route table already has a route with destination-prefix-list-id`                              | Imported VPC already has VPC endpoints.                        | Set `app.useGlobalVpc.addVpcEndpoints: false` and manually add missing endpoints.                                                                                                                                                                    |
| `Invalid request provided: Before you can proceed, you must enable a service-linked role`      | OpenSearch Provisioned service-linked role not yet propagated. | Wait 5 minutes and redeploy. If the issue persists, manually create the roles: `aws iam create-service-linked-role --aws-service-name es.amazonaws.com` and `aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com`. |
| `Properties validation failed ... array items are not unique` (ALB target group)               | Rare CloudFormation issue with ALB VPC endpoint IP resolution. | No configuration change needed. Redeploy to resolve.                                                                                                                                                                                                 |

## Uninstalling

To remove VAMS and all associated resources:

```bash
cd infra
cdk destroy --all
```

Some resources (Amazon S3 buckets, Amazon DynamoDB tables, pipeline stacks) may not be automatically deleted due to retention policies. Remove these manually through the AWS Management Console or AWS CLI.

## Next steps

-   [Configuration Reference](configuration-reference.md) -- Comprehensive list of all configuration options.
-   [Permissions Guide](../user-guide/permissions.md) -- Set up roles and access control.
-   [Developer Guide](../developer/setup.md) -- Architecture details and custom development.
