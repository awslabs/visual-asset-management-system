/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as codeartifact from "aws-cdk-lib/aws-codeartifact";
import * as s3assets from "aws-cdk-lib/aws-s3-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { Stack, RemovalPolicy, Duration } from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../config/config";
import { contentImageTag } from "../../../../../helper/containerImageTag";
import { codeBuildTriggerCode } from "../../../constructs/codeBuildTrigger";

export interface ImageTo3dCodeBuildConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    /** `storageResources.encryption.kmsKey`: the shared VAMS key, undefined when the CMK is off. */
    kmsKey?: kms.IKey;
}

export interface ImageTo3dModelImage {
    repository: ecr.IRepository;
    imageUri: string;
    codeBuildProjectName: string;
}

/**
 * Model keys, in registration order. Each key names the per-model Dockerfile
 * (Dockerfile.<modelKey>) inside the shared container source directory.
 */
const MODEL_KEYS = [
    "sam3d",
    "stable3dgen",
    "trellis2",
    "triposr",
    "step1x3d",
    "hunyuan3d",
    "blenderagent",
] as const;

/**
 * ImageTo3dCodeBuildConstruct
 *
 * Builds the Image to 3D container images via CodeBuild and pushes them to ECR.
 * This avoids local Docker builds of the large CUDA/PyTorch images, which are extremely slow.
 *
 * All models share one container source directory and one buildspec; a build differs only by the
 * DOCKERFILE_NAME it selects. For each enabled model this construct creates:
 * - An ECR repository for the container image
 * - A CodeBuild project configured with Docker layer caching
 * - A custom resource that triggers the build on Create/Update
 *
 * The container source directory is uploaded once as a single S3 asset shared by every project, and
 * every model's image is pushed under the one content-addressed tag derived from it.
 */
export class ImageTo3dCodeBuildConstruct extends Construct {
    public readonly modelImages: { [modelKey: string]: ImageTo3dModelImage } = {};

    constructor(parent: Construct, name: string, props: ImageTo3dCodeBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;
        const imageTo3dConfig = props.config.app.pipelines.useImageTo3d;

        // The shared VAMS key, imported by ARN so no derived grant reaches the storage stack's key
        // policy (infra/CLAUDE.md, encryption trap 2). Undefined when the CMK is off.
        const encryptionKey = props.kmsKey
            ? kms.Key.fromKeyArn(this, "ImageTo3dEcrKmsKeyRef", props.kmsKey.keyArn)
            : undefined;

        // Every mesh repair rung ships in the image so the whole ladder is available to a run. Which
        // rungs a run actually walks is a per-execution choice, carried by the template's
        // REPAIR_BACKENDS setting, and whether repair happens at all is its MESH_CLEANUP setting.
        const enableGplMeshRepair = "true";

        // S3 Asset: upload the shared container source directory once. Every model build reads the
        // same sources and selects its own Dockerfile, so a single upload serves all projects and
        // gives one asset hash that re-triggers every build when container code changes.
        const sourceAsset = new s3assets.Asset(this, "Source-image-to-3d", {
            path: path.join(
                __dirname,
                "../../../../../../../backendPipelines/genAi/imageTo3d/container"
            ),
            exclude: [".git", "*.pyc", "__pycache__", ".venv", "node_modules", ".env"],
        });

        // Content-addressed image tag, supplied to every build and consumed at the pull site from this
        // one literal so the two sides cannot name different images.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        // AWS CodeArtifact as a pull-through cache for PyPI. Every model image installs its Python
        // dependencies from source during the build, so a build is only as reliable as the public
        // package CDN is reachable from the build subnet -- and a spell of files.pythonhosted.org
        // connection failures blocked every model here, on both base images, for hours. Fetching
        // through CodeArtifact instead means the first build populates the cache and later builds are
        // served from it, which makes a rebuild reproducible rather than dependent on the internet.
        //
        // One domain and one repository serve every model build.
        // The domain name includes baseStackName because a CodeArtifact domain name is unique per
        // account and Region: keyed on config.name and the Region alone, two VAMS deployments in one
        // account would try to create the same domain and the second would fail. baseStackName already
        // carries the Region in practice, so the name is truncated to CodeArtifact's 50-character limit.
        const packageDomain = new codeartifact.CfnDomain(this, "PackageDomain", {
            domainName: `${props.config.name}-${props.config.app.baseStackName}-imageto3d`
                .toLowerCase()
                .replace(/[^a-z0-9-]/g, "-")
                .slice(0, 50),
        });

        const pypiCache = new codeartifact.CfnRepository(this, "PypiCache", {
            domainName: packageDomain.domainName,
            repositoryName: "pypi-store",
            description:
                "Pull-through cache for PyPI, used by the Image to 3D container builds so a rebuild " +
                "does not depend on reaching the public package CDN",
            externalConnections: ["public:pypi"],
        });
        pypiCache.addDependency(packageDomain);

        /**
         * Helper: Create ECR repo, CodeBuild project, and build trigger for a single model.
         */
        const createModelBuild = (modelKey: string): ImageTo3dModelImage => {
            // ECR Repository — no explicit repositoryName so CDK auto-generates a unique name
            // per deployment (avoids collisions across multiple VAMS stacks in the same region).
            // KMS-encrypted with the shared key when the CMK is on, AES-256 without.
            const repository = new ecr.Repository(this, `EcrRepo-${modelKey}`, {
                encryption: encryptionKey
                    ? ecr.RepositoryEncryption.KMS
                    : ecr.RepositoryEncryption.AES_256,
                encryptionKey,
                removalPolicy: RemovalPolicy.DESTROY,
                emptyOnDelete: true,
                imageScanOnPush: true,
                lifecycleRules: [
                    {
                        maxImageCount: 10,
                        description: `Keep last 10 images for vams-image-to-3d-${modelKey}`,
                    },
                ],
            });

            // CodeBuild Project — runs in the same private VPC/subnets as pipeline Batch compute.
            // Private subnets have NAT Gateway egress for pulling Docker base images and cloning repos.
            const project = new codebuild.Project(this, `CodeBuild-${modelKey}`, {
                description: `Build Image to 3D ${modelKey} container image and push to ECR`,
                environment: {
                    buildImage: Config.CODEBUILD_BUILD_IMAGE,
                    computeType: codebuild.ComputeType.LARGE,
                    privileged: true,
                    environmentVariables: {
                        ECR_REPO_URI: {
                            value: repository.repositoryUri,
                        },
                        IMAGE_TAG: {
                            value: imageTag,
                        },
                        AWS_ACCOUNT_ID: {
                            value: account,
                        },
                        AWS_DEFAULT_REGION: {
                            value: region,
                        },
                        DOCKERFILE_NAME: {
                            value: `Dockerfile.${modelKey}`,
                        },
                        ENABLE_GPL_MESH_REPAIR: {
                            value: enableGplMeshRepair,
                        },
                        // The buildspec resolves an endpoint and a short-lived token from these and
                        // points pip at the cache instead of the public index.
                        CODEARTIFACT_DOMAIN: {
                            value: packageDomain.domainName,
                        },
                        CODEARTIFACT_DOMAIN_OWNER: {
                            value: account,
                        },
                        CODEARTIFACT_REPOSITORY: {
                            value: pypiCache.repositoryName,
                        },
                    },
                },
                vpc: props.vpc,
                subnetSelection: { subnets: props.pipelineSubnets },
                securityGroups: props.pipelineSecurityGroups,
                source: codebuild.Source.s3({
                    bucket: sourceAsset.bucket,
                    path: sourceAsset.s3ObjectKey,
                }),
                buildSpec: codebuild.BuildSpec.fromSourceFilename("buildspec.yml"),
                timeout: Duration.hours(3),
                cache: codebuild.Cache.local(
                    codebuild.LocalCacheMode.DOCKER_LAYER,
                    codebuild.LocalCacheMode.CUSTOM
                ),
            });

            // Permissions: ECR push/pull
            repository.grantPullPush(project);

            // Permissions: read source from S3
            sourceAsset.grantRead(project);

            // Permissions: ecr:GetAuthorizationToken (required for docker login)
            project.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: ["ecr:GetAuthorizationToken"],
                    resources: ["*"],
                })
            );

            // Permissions: read Python packages through the CodeArtifact cache. The token is minted per
            // build and expires, so nothing long-lived is stored anywhere.
            const stack = Stack.of(this);
            project.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: ["codeartifact:GetAuthorizationToken"],
                    resources: [
                        stack.formatArn({
                            service: "codeartifact",
                            resource: "domain",
                            resourceName: packageDomain.domainName,
                        }),
                    ],
                })
            );
            project.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: [
                        "codeartifact:GetRepositoryEndpoint",
                        "codeartifact:ReadFromRepository",
                    ],
                    resources: [
                        stack.formatArn({
                            service: "codeartifact",
                            resource: "repository",
                            resourceName: `${packageDomain.domainName}/${pypiCache.repositoryName}`,
                        }),
                        // ReadFromRepository is evaluated against the package as well as the repository
                        stack.formatArn({
                            service: "codeartifact",
                            resource: "package",
                            resourceName: `${packageDomain.domainName}/${pypiCache.repositoryName}/*`,
                        }),
                    ],
                })
            );
            // GetAuthorizationToken is implemented by STS on CodeArtifact's behalf, so the build role
            // needs the bearer-token action as well, restricted to that service.
            project.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: ["sts:GetServiceBearerToken"],
                    resources: ["*"],
                    conditions: {
                        StringEquals: {
                            "sts:AWSServiceName": "codeartifact.amazonaws.com",
                        },
                    },
                })
            );

            // Custom Resource: trigger CodeBuild on Create/Update
            const triggerFunction = new cdk.aws_lambda.Function(this, `BuildTrigger-${modelKey}`, {
                runtime: Config.LAMBDA_PYTHON_RUNTIME,
                handler: "index.handler",
                timeout: Duration.minutes(1),
                code: codeBuildTriggerCode(),
            });

            // Grant the trigger Lambda permission to start builds
            triggerFunction.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: ["codebuild:StartBuild"],
                    resources: [project.projectArn],
                })
            );

            const triggerProvider = new cr.Provider(this, `BuildProvider-${modelKey}`, {
                onEventHandler: triggerFunction,
            });

            // SourceHash is the shared container asset hash, so any container code edit re-runs the
            // build for every enabled model on the next deployment. EcrRepositoryUri is a trigger input
            // as well: a CMK flip replaces the repository (its encryption is immutable) while the other
            // properties stay the same, and the new URI re-fires the build into the new repository.
            new cdk.CustomResource(this, `BuildTriggerCR-${modelKey}`, {
                serviceToken: triggerProvider.serviceToken,
                properties: {
                    ProjectName: project.projectName,
                    SourceHash: sourceAsset.assetHash,
                    DockerfileName: `Dockerfile.${modelKey}`,
                    EnableGplMeshRepair: enableGplMeshRepair,
                    EcrRepositoryUri: repository.repositoryUri,
                },
            });

            // Image URI at the content-addressed tag the build pushes.
            const imageUri = `${repository.repositoryUri}:${imageTag}`;

            /**
             * CDK Nag Suppressions
             */

            // CB4/CB3: CodeBuild project encryption + privileged mode
            NagSuppressions.addResourceSuppressions(
                project,
                [
                    {
                        id: "AwsSolutions-CB4",
                        reason: "Image to 3D CodeBuild project uses default AWS-managed encryption. Build artifacts are transient container images pushed to ECR which has its own encryption.",
                    },
                    {
                        id: "AwsSolutions-CB3",
                        reason: "Privileged mode is required for Docker-in-Docker container image builds in CodeBuild.",
                    },
                ],
                true
            );

            // IAM5: ecr:GetAuthorizationToken requires wildcard resource
            NagSuppressions.addResourceSuppressions(
                project,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "ecr:GetAuthorizationToken requires resource '*' as it is an account-level operation, not scoped to a specific repository.",
                    },
                ],
                true
            );

            // Suppressions for the trigger provider's auto-generated resources
            NagSuppressions.addResourceSuppressions(
                triggerProvider,
                [
                    {
                        id: "AwsSolutions-IAM4",
                        reason: "Custom resource provider framework uses AWS managed policies for basic Lambda execution. This is CDK-managed infrastructure.",
                    },
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Custom resource provider framework requires wildcard permissions for log group creation. This is CDK-managed infrastructure.",
                    },
                    {
                        id: "AwsSolutions-L1",
                        reason: "Custom resource provider framework Lambda runtime is managed by CDK and may not use the latest runtime version.",
                    },
                ],
                true
            );

            // Suppressions for the trigger function itself
            NagSuppressions.addResourceSuppressions(
                triggerFunction,
                [
                    {
                        id: "AwsSolutions-IAM4",
                        reason: "Build trigger Lambda uses AWSLambdaBasicExecutionRole managed policy for CloudWatch logging.",
                    },
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Build trigger Lambda role requires wildcard for log stream creation under its log group.",
                    },
                ],
                true
            );

            return { repository, imageUri, codeBuildProjectName: project.projectName };
        };

        /**
         * Conditional creation: one image build per enabled model
         */
        for (const modelKey of MODEL_KEYS) {
            if (imageTo3dConfig?.models?.[modelKey]?.enabled !== true) {
                continue;
            }

            const modelRepo = createModelBuild(modelKey);
            this.modelImages[modelKey] = modelRepo;

            new cdk.CfnOutput(this, `ImageTo3dCodeBuildProject-${modelKey}`, {
                value: modelRepo.codeBuildProjectName,
                description: `CodeBuild project name for the Image to 3D ${modelKey} container. Check build status: aws codebuild list-builds-for-project --project-name <value>`,
            });
        }
    }
}
