# Prerequisites

Before deploying Visual Asset Management System (VAMS), ensure that your development environment, AWS account, and network meet the requirements described on this page.

## Required software

The following software must be installed on the machine used to build and deploy VAMS.

| Software                   | Minimum version                    | Purpose                                                                                                            |
| -------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Python                     | 3.12                               | AWS Lambda runtime, backend dependencies                                                                           |
| Docker                     | Latest stable                      | Container builds for AWS Lambda layers and pipelines                                                               |
| Git                        | Latest stable                      | Cloning the repository; the frontend install also clones the viewer repositories listed under Network requirements |
| Node.js                    | 22.0.0 (`web/.nvmrc` pins 22.22.3) | Frontend build tooling, AWS CDK CLI                                                                                |
| npm                        | 11.10.0                            | Package management for frontend and infrastructure. Node.js 22 bundles npm 10; install npm 11 separately           |
| Node Version Manager (nvm) | Latest stable                      | Ensures the correct Node.js version is active                                                                      |
| AWS CLI                    | v2 (latest)                        | AWS account authentication and resource management                                                                 |
| AWS CDK CLI                | Pinned in `infra/package.json`     | Infrastructure-as-code deployment; run it as `npx cdk` from `infra/` so the pinned version is used                 |

:::tip[Verify installed versions]
Run the following commands to confirm your tools are at the required versions:

```bash
python --version   # 3.12+
docker --version
git --version
node --version     # v22.0.0 or later; `nvm use` in the web/ directory selects v22.22.3
npm --version      # 11.10.0 or later
nvm --version
aws --version      # aws-cli/2.x
cd infra && npx cdk --version   # the version pinned in infra/package.json
```

Node.js 22 ships with npm 10, and the `package.json` files declare `engine-strict` with `npm >=11.10.0`, so `npm install` fails with `EBADENGINE` until npm 11 is installed. nvm keeps a separate npm per Node.js version, so install it after `nvm use` (the `.nvmrc` is in the `web/` directory):

```bash
cd web && nvm use
npm install -g npm@11
npm --version      # 11.10.0 or later
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
-   Amazon API Gateway REST APIs
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
-   `vpce:AllowMultiRegion` (an EC2 permission-only action), when an external asset bucket is registered in another Region with `app.useGlobalVpc.enabled` and `app.useGlobalVpc.addCrossRegionS3Endpoints` on — it authorizes creating the cross-Region Amazon S3 interface endpoint, and a service control policy must not deny it ([External S3 setup](./external-s3-setup.md#cross-region-buckets))

:::warning[Least privilege]
Use the least-permissive IAM role that can still generate the needed AWS components from AWS CloudFormation. Consult your organization's security team for appropriate permission boundaries.
:::

### CDK bootstrap

AWS CDK requires a one-time bootstrap operation per account and AWS Region combination. This creates the staging resources that CDK uses during deployment. Bootstrap every Region a stack deploys into: the deployment Region, `us-east-1` when Amazon CloudFront with AWS WAF is enabled, and each Region that holds an external asset bucket outside the deployment Region (its per-Region notification stack deploys there). Run the commands from the `infra/` directory after installing its dependencies, so `npx cdk` uses the pinned AWS CDK CLI.

**Commercial AWS Regions:**

```bash
npx cdk bootstrap aws://ACCOUNT_ID/REGION
```

Replace `ACCOUNT_ID` with your 12-digit AWS account ID and `REGION` with the target Region (for example, `us-east-1`).

A CloudFront deployment with AWS WAF enabled (the commercial template default) also creates the CloudFront web ACL stack, `{name}-waf-{baseStackName}-{region}`, in `us-east-1`. When the target Region is not `us-east-1`, bootstrap that Region as well:

```bash
npx cdk bootstrap aws://ACCOUNT_ID/us-east-1
```

**AWS GovCloud (US) Regions:**

```bash
export AWS_REGION=us-gov-west-1
npx cdk bootstrap aws://ACCOUNT_ID/us-gov-west-1
```

:::info[GovCloud endpoint resolution]
When bootstrapping an AWS GovCloud account, you must set the `AWS_REGION` environment variable so the AWS SDK resolves to GovCloud endpoints. Without this variable, the SDK defaults to commercial endpoints and the bootstrap operation will fail.
:::

**AWS European Sovereign Cloud Regions:**

```bash
export AWS_REGION=eusc-de-east-1
npx cdk bootstrap aws://ACCOUNT_ID/eusc-de-east-1
```

As with GovCloud, set the `AWS_REGION` environment variable so the AWS SDK resolves to the European Sovereign Cloud partition endpoints.

### FIPS endpoint configuration

If your organization requires Federal Information Processing Standards (FIPS) 140-2 validated cryptographic modules, enable the FIPS environment variable before any AWS CLI or CDK operations:

```bash
export AWS_USE_FIPS_ENDPOINT=true
```

The same variable turns on `app.useFips` at synthesis. Setting `app.useFips` to `true` in the VAMS configuration file as well keeps the deployment reproducible when the variable is absent. See the [Configuration Reference](configuration-reference.md) for what the flag changes. In the AWS European Sovereign Cloud, leave the variable unset and `app.useFips` `false`: the partition offers FIPS endpoints for only four services, which do not include AWS STS, AWS CloudFormation or Amazon S3.

## Network requirements

The build machine requires outbound internet access to download dependencies from the following sources:

| Source                                            | Protocol | Purpose                                                                                                                                            |
| ------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| npm registry (`registry.npmjs.org`)               | HTTPS    | Node.js packages for frontend and CDK                                                                                                              |
| GitHub (`github.com`)                             | HTTPS    | The frontend install clones the `needle-tools/usd-viewer`, `potree/potree`, `playcanvas/supersplat` and `lichtblick-suite/lichtblick` repositories |
| Yarn (`repo.yarnpkg.com`, `registry.yarnpkg.com`) | HTTPS    | The robotics viewer install fetches Yarn through corepack and installs the Lichtblick packages with it                                             |
| PyPI (`pypi.org`)                                 | HTTPS    | Python packages for Lambda layers                                                                                                                  |
| Docker Hub / Amazon ECR Public                    | HTTPS    | Base container images for pipeline container builds                                                                                                |
| AWS service endpoints                             | HTTPS    | CDK deployment, AWS CloudFormation operations                                                                                                      |

:::note[Network-restricted environments]
For deployments in VPC-isolated or network-restricted environments, you must pre-stage all npm packages, Python packages, and container images in internal registries, and mirror the Git repositories the frontend install clones: `https://github.com/needle-tools/usd-viewer.git` (see `web/customInstalls/needletools-usd-viewer/usdViewerInstall.js`), `https://github.com/potree/potree.git` (see `web/customInstalls/potree/potreeInstall.js`) `https://github.com/playcanvas/supersplat.git` (see `web/customInstalls/supersplat/superSplatInstall.js`) and `https://github.com/lichtblick-suite/lichtblick.git` (see `web/customInstalls/robotics/roboticsViewerInstall.js`). Each install script exits non-zero when its clone fails, which fails `npm install`. The robotics viewer install also runs Yarn through corepack: corepack downloads Yarn from `repo.yarnpkg.com` unless `COREPACK_NPM_REGISTRY` names a registry mirror, and Yarn installs from `registry.yarnpkg.com` unless `YARN_NPM_REGISTRY_SERVER` names one. Setting `"enabled": false` on `robotics-data-viewer` in `web/src/visualizerPlugin/config/viewerConfig.json` skips that install. Consult the [Plan your deployment](plan-your-deployment.md) page for VPC-isolated deployment architecture.
:::

### SSL proxy considerations

If you are deploying behind an HTTPS SSL proxy that requires network nodes to have a custom SSL certificate, additional container engine and npm configuration is needed. Refer to the **CDK SSL Deploy with Custom SSL Cert Proxy** section in the [Developer Guide](../developer/setup.md) for detailed instructions on configuring custom certificates for CDK container builds.

## Next steps

After confirming all prerequisites are met, proceed to [Plan your deployment](plan-your-deployment.md) to choose your deployment mode and make key architectural decisions.
