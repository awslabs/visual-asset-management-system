# Prerequisites

Before deploying Visual Asset Management System (VAMS), ensure that your development environment, AWS account, and network meet the requirements described on this page.

## Required software

The following software must be installed on the machine used to build and deploy VAMS.

| Software                   | Minimum version       | Purpose                                              |
| -------------------------- | --------------------- | ---------------------------------------------------- |
| Python                     | 3.12                  | AWS Lambda runtime, backend dependencies             |
| Docker                     | Latest stable         | Container builds for AWS Lambda layers and pipelines |
| Node.js                    | 20.18.1               | Frontend build tooling, AWS CDK CLI                  |
| npm                        | Included with Node.js | Package management for frontend and infrastructure   |
| Node Version Manager (nvm) | Latest stable         | Ensures the correct Node.js version is active        |
| AWS CLI                    | v2 (latest)           | AWS account authentication and resource management   |
| AWS CDK CLI                | Latest stable         | Infrastructure-as-code deployment                    |

:::tip[Verify installed versions]
Run the following commands to confirm your tools are at the required versions:

```bash
python --version   # 3.12+
docker --version
node --version     # v20.18.1+
npm --version
nvm --version
aws --version      # aws-cli/2.x
cdk --version
```

:::

### Docker alternatives

Docker is the default and most widely tested container engine for building VAMS. However, AWS CDK also supports Docker-compatible alternatives. If your organization restricts Docker Desktop or prefers open-source tooling, you can use one of the following:

-   **[Finch](https://aws.github.io/finch/)** (AWS recommended) — An open-source container development tool from AWS that provides a native CLI experience on macOS and Windows without requiring Docker Desktop.
-   **[Podman](https://podman.io/)** — A daemonless, open-source container engine that can serve as a drop-in replacement for Docker.

To use an alternative container engine with AWS CDK, you must configure the `CDK_DOCKER` environment variable to point to your chosen tool's CLI. For detailed setup instructions, see:

-   [Building and deploying container assets with AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/build-containers.html)
-   [AWS CDK ECR Assets module reference](https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_ecr_assets/README.html)

```bash
# Example: Using Finch as the container engine for CDK
export CDK_DOCKER=finch

# Example: Using Podman as the container engine for CDK
export CDK_DOCKER=podman
```

:::warning[Docker alternatives compatibility]
While Finch and Podman are supported by AWS CDK, Docker remains the primary tested container engine for VAMS deployments. If you encounter container build issues when using an alternative engine, verify the issue reproduces with Docker before reporting it.
:::

### Optional software

| Software    | Purpose                                          |
| ----------- | ------------------------------------------------ |
| Poetry      | Managing Python dependencies in the VAMS backend |
| Conda-forge | Local development environment isolation          |

## AWS account requirements

### IAM permissions

The IAM principal used for deployment must have sufficient permissions to create and manage the following AWS services through AWS CloudFormation:

-   Amazon S3 buckets and policies
-   Amazon DynamoDB tables
-   AWS Lambda functions and layers
-   Amazon API Gateway HTTP APIs
-   Amazon Cognito user pools and identity pools
-   Amazon CloudFront distributions (commercial deployments)
-   Elastic Load Balancing Application Load Balancers (ALB deployments)
-   Amazon Virtual Private Cloud (Amazon VPC) resources
-   AWS Key Management Service (AWS KMS) keys
-   Amazon OpenSearch Service domains or collections
-   AWS Step Functions state machines
-   Amazon Simple Queue Service (Amazon SQS) queues
-   Amazon Simple Notification Service (Amazon SNS) topics
-   AWS Batch compute environments (when pipelines are enabled)
-   AWS Identity and Access Management (IAM) roles and policies
-   AWS CloudTrail trails
-   AWS WAF web ACLs

:::warning[Least privilege]
Use the least-permissive IAM role that can still generate the needed AWS components from AWS CloudFormation. Consult your organization's security team for appropriate permission boundaries.
:::

### CDK bootstrap

AWS CDK requires a one-time bootstrap operation per account and AWS Region combination. This creates the staging resources that CDK uses during deployment.

**Commercial AWS Regions:**

```bash
cdk bootstrap aws://ACCOUNT_ID/REGION
```

Replace `ACCOUNT_ID` with your 12-digit AWS account ID and `REGION` with the target Region (for example, `us-east-1`).

**AWS GovCloud (US) Regions:**

```bash
export AWS_REGION=us-gov-west-1
cdk bootstrap aws://ACCOUNT_ID/us-gov-west-1
```

:::info[GovCloud endpoint resolution]
When bootstrapping an AWS GovCloud account, you must set the `AWS_REGION` environment variable so the AWS SDK resolves to GovCloud endpoints. Without this variable, the SDK defaults to commercial endpoints and the bootstrap operation will fail.
:::

### FIPS endpoint configuration

If your organization requires Federal Information Processing Standards (FIPS) 140-2 validated cryptographic modules, enable the FIPS environment variable before any AWS CLI or CDK operations:

```bash
export AWS_USE_FIPS_ENDPOINT=true
```

You must also set `app.useFips` to `true` in the VAMS configuration file. See the [Configuration Reference](configuration-reference.md) for details.

## Network requirements

The build machine requires outbound internet access to download dependencies from the following sources:

| Source                              | Protocol | Purpose                                             |
| ----------------------------------- | -------- | --------------------------------------------------- |
| npm registry (`registry.npmjs.org`) | HTTPS    | Node.js packages for frontend and CDK               |
| PyPI (`pypi.org`)                   | HTTPS    | Python packages for Lambda layers                   |
| Docker Hub / Amazon ECR Public      | HTTPS    | Base container images for pipeline container builds |
| AWS service endpoints               | HTTPS    | CDK deployment, AWS CloudFormation operations       |

:::note[Network-restricted environments]
For deployments in VPC-isolated or network-restricted environments, you must pre-stage all npm packages, Python packages, and container images in internal registries. Consult the [Plan your deployment](plan-your-deployment.md) page for VPC-isolated deployment architecture.
:::

### SSL proxy considerations

If you are deploying behind an HTTPS SSL proxy that requires network nodes to have a custom SSL certificate, additional container engine and npm configuration is needed. Refer to the **CDK SSL Deploy with Custom SSL Cert Proxy** section in the [Developer Guide](../developer/setup.md) for detailed instructions on configuring custom certificates for CDK container builds.

## Next steps

After confirming all prerequisites are met, proceed to [Plan your deployment](plan-your-deployment.md) to choose your deployment mode and make key architectural decisions.
