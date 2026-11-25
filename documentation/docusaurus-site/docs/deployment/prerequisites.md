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
| npm                        | 11.10.0 (npm 11 only, not npm 12)  | Package management for frontend and infrastructure. Node.js 22 bundles npm 10; install npm 11 separately           |
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
npm --version      # 11.10.0 or later, below 12
nvm --version
aws --version      # aws-cli/2.x
cd infra && npx cdk --version   # the version pinned in infra/package.json
```

The supported npm range is `>=11.10.0 <12`: npm 11, from 11.10.0 on. The `package.json` files declare it in `engines.npm` and the `.npmrc` files set `engine-strict`, so `npm install` and `npm ci` stop with `EBADENGINE` under any npm outside that range:

-   **npm 10**, which Node.js 22 ships with, does not honor the `min-release-age` setting in the `.npmrc` files.
-   **npm 12** blocks dependency install scripts, among them those of `esbuild` and `@parcel/watcher`, unless an `allowScripts` entry approves them. VAMS has not approved or tested any, so builds are supported on npm 11 only until that is done.

nvm keeps a separate npm per Node.js version, so install npm 11 after `nvm use` (the `.nvmrc` is in the `web/` directory). Install `npm@11`, not `npm@latest`:

```bash
cd web && nvm use
npm install -g npm@11
npm --version      # 11.10.0 or later, below 12
```

npm 11.21 and later print an `install scripts not yet covered by allowScripts` warning for `esbuild` and `@parcel/watcher`; the warning is expected, and npm 11 still runs those scripts.

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

| Software    | Purpose                                                                                                                                                                                                              |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| uv          | Changing or auditing VAMS Python dependencies (development only; deploys install the committed requirements files with pip). See [Managing Python Dependencies](../developer/setup.md#managing-python-dependencies). |
| Conda-forge | Local development environment isolation                                                                                                                                                                              |

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

### Amazon Bedrock model access

The SYSTEM GenAI metadata pipeline (`app.pipelines.useSystemGenAiMetadata`) and natural-language search (`app.vectorSearch`) call Amazon Bedrock models with the deployment's own Lambda roles. Deployment grants those roles `bedrock:InvokeModel` on the configured models; whether a model is available to the account is account state that the stack does not manage.

-   **Commercial AWS Regions** — Amazon Bedrock enables a third-party model on its first invocation when a principal in the account holds `aws-marketplace:Subscribe`, `aws-marketplace:ViewSubscriptions`, and `aws-marketplace:Unsubscribe`. The VAMS Lambda roles hold none of these, so enable the analysis model, and the text model (`bedrockModels.textModelId`) when one is set, before the first upload: invoke it once from a principal with those permissions, or enable it on the Amazon Bedrock console's **Model access** page. Amazon-provider models, including the default embeddings model Amazon Titan Text Embeddings V2, need no Marketplace step.
-   **Anthropic models** (the default analysis model in the commercial and GovCloud templates) additionally require a one-time use-case form, submitted once per account or once at the organization's management account. Until it is submitted, invocations fail with `FTUFormNotFilled` and the pipeline records each execution as failed with `BedrockAccessDenied`; configuration validation warns about this at synthesis.
-   **AWS GovCloud (US)** — model access is a manual step. Enable a third-party model, such as the default Anthropic analysis model, in **both** the linked commercial account and the GovCloud account (console **Model access** page in `us-gov-west-1`); enable an Amazon model, such as Amazon Titan Text Embeddings V2, in the GovCloud account only (see [Access Amazon Bedrock foundation models in AWS GovCloud (US)](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html#model-access-govcloud)). Use a `us-gov.` inference profile id for the analysis model.
-   **AWS European Sovereign Cloud** — `app.vectorSearch.enabled` must be `false` (Amazon DynamoDB vector search is not available there); the pipeline can run with a model available in that partition.

An account-wide service control policy that denies `bedrock:InvokeModel` makes every analysis fail with `AccessDeniedException`; the pipeline still writes the extracted file attributes. Prompt content sent to the analysis model is untrusted (file text, user-entered metadata); the deployment creates an Amazon Bedrock guardrail with a prompt-attack filter and PII filters by default (`app.pipelines.useSystemGenAiMetadata.bedrockGuardrail.create`), and an operator-owned guardrail can replace it through `guardrailIdentifier` and `guardrailVersion` — see the [pipeline page](../pipelines/system-genai-metadata.md#amazon-bedrock-guardrail). The role that deploys the stack needs `bedrock:CreateGuardrail`, `bedrock:CreateGuardrailVersion`, `bedrock:UpdateGuardrail`, `bedrock:DeleteGuardrail`, and `bedrock:GetGuardrail` for the created guardrail; the default AWS CDK bootstrap role has them.

## Network requirements

The build machine requires outbound internet access to download dependencies from the following sources:

| Source                                                                         | Protocol     | Purpose                                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------------ | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| npm registry (`registry.npmjs.org`)                                            | HTTPS        | Node.js packages for frontend and CDK                                                                                                                                                                                                                                                |
| GitHub (`github.com`)                                                          | HTTPS        | The frontend install clones the `needle-tools/usd-viewer`, `potree/potree`, `playcanvas/supersplat` and `lichtblick-suite/lichtblick` repositories; pipeline container builds download release files and clone model repositories (NVIDIA Cosmos, NVIDIA GR00T, Image to 3D, Potree) |
| Yarn (`repo.yarnpkg.com`, `registry.yarnpkg.com`)                              | HTTPS        | The robotics viewer install fetches Yarn through corepack and installs the Lichtblick packages with it                                                                                                                                                                               |
| PyPI (`pypi.org`)                                                              | HTTPS        | Python packages for Lambda layers and pipeline container builds                                                                                                                                                                                                                      |
| Docker Hub / Amazon ECR Public                                                 | HTTPS        | Base container images for pipeline container builds (for example `nvidia/cuda`, `mambaorg/micromamba`)                                                                                                                                                                               |
| NVIDIA NGC (`nvcr.io`)                                                         | HTTPS        | Base image of the Isaac Lab training container                                                                                                                                                                                                                                       |
| Linux package mirrors (the base image's Ubuntu or Debian archives)             | HTTPS / HTTP | `apt-get` packages in every pipeline container build                                                                                                                                                                                                                                 |
| AWS CLI (`awscli.amazonaws.com`)                                               | HTTPS        | The AWS CLI installer in the NVIDIA and Image to 3D containers                                                                                                                                                                                                                       |
| Hugging Face (`huggingface.co`)                                                | HTTPS        | Model code and weights the Image to 3D containers download at build time                                                                                                                                                                                                             |
| PyTorch (`download.pytorch.org`, `pytorch.org`)                                | HTTPS        | PyTorch wheels for the Image to 3D containers                                                                                                                                                                                                                                        |
| Blender (`download.blender.org`, mirrors `mirrors.dotsrc.org`, `ftp.nluug.nl`) | HTTPS        | Blender for the Image to 3D Blender agent container                                                                                                                                                                                                                                  |
| Meta (`dl.fbaipublicfiles.com`)                                                | HTTPS        | The DINOv2 checkpoint in the Image to 3D Stable3DGen container                                                                                                                                                                                                                       |
| NVIDIA Kaolin (`nvidia-kaolin.s3.us-east-2.amazonaws.com`)                     | HTTPS        | Kaolin wheels for the Image to 3D containers                                                                                                                                                                                                                                         |
| conda-forge (`conda.anaconda.org`)                                             | HTTPS        | Conda packages for the Image to 3D SAM 3D container                                                                                                                                                                                                                                  |
| Node.js (`nodejs.org`)                                                         | HTTPS        | Node.js for the 3D Preview Thumbnail container                                                                                                                                                                                                                                       |
| AWS service endpoints                                                          | HTTPS        | CDK deployment, AWS CloudFormation operations                                                                                                                                                                                                                                        |

A pipeline whose `useCodeBuild` is `true` builds its container image in AWS CodeBuild, which then needs that pipeline's hosts instead of the build machine. The AWS European Sovereign Cloud template turns `useCodeBuild` on for no pipeline, so there every pipeline image builds on the build machine.

:::note[Network-restricted environments]
For deployments in VPC-isolated or network-restricted environments, you must pre-stage all npm packages, Python packages, and container images in internal registries, mirror the hosts each enabled pipeline's container build fetches from (listed above; each pipeline's Dockerfiles under `backendPipelines/` name them), and mirror the Git repositories the frontend install clones: `https://github.com/needle-tools/usd-viewer.git` (see `web/customInstalls/needletools-usd-viewer/usdViewerInstall.js`), `https://github.com/potree/potree.git` (see `web/customInstalls/potree/potreeInstall.js`), `https://github.com/playcanvas/supersplat.git` (see `web/customInstalls/supersplat/superSplatInstall.js`) and `https://github.com/lichtblick-suite/lichtblick.git` (see `web/customInstalls/robotics/roboticsViewerInstall.js`). Each install script exits non-zero when its clone fails, which fails `npm install`. The robotics viewer install also runs Yarn through corepack: corepack downloads Yarn from `repo.yarnpkg.com` unless `COREPACK_NPM_REGISTRY` names a registry mirror, and Yarn installs from `registry.yarnpkg.com` unless `YARN_NPM_REGISTRY_SERVER` names one. Setting `"enabled": false` on `robotics-data-viewer` in `web/src/visualizerPlugin/config/viewerConfig.json` skips that install. Consult the [Plan your deployment](plan-your-deployment.md) page for VPC-isolated deployment architecture.
:::

### SSL proxy considerations

If you are deploying behind an HTTPS SSL proxy that requires network nodes to have a custom SSL certificate, additional container engine and npm configuration is needed. Refer to the **CDK SSL Deploy with Custom SSL Cert Proxy** section in the [Developer Guide](../developer/setup.md) for detailed instructions on configuring custom certificates for CDK container builds.

## Next steps

After confirming all prerequisites are met, proceed to [Plan your deployment](plan-your-deployment.md) to choose your deployment mode and make key architectural decisions.
