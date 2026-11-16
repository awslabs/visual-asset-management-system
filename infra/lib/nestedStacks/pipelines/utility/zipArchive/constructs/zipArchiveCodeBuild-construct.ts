/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
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

export interface ZipArchiveCodeBuildConstructProps extends cdk.StackProps {
    config: Config.Config;
    /** `storageResources.encryption.kmsKey`: the shared VAMS key, undefined when the CMK is off. */
    kmsKey?: kms.IKey;
}

/**
 * Builds the zipArchive container image with AWS CodeBuild (app.pipelines.useZipDownloads.useCodeBuild) into a
 * VAMS-owned Amazon ECR repository, from backendPipelines/utility/zipArchive/container and its
 * buildspec.yml. Mirrors CoordinateTransformCodeBuildConstruct.
 */
export class ZipArchiveCodeBuildConstruct extends Construct {
    public readonly repository: ecr.Repository;
    /** Content-addressed tag the build pushes and the Batch job definition consumes. */
    public readonly imageTag: string;
    public readonly codeBuildProjectName: string;

    constructor(parent: Construct, name: string, props: ZipArchiveCodeBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        // An explicit repositoryName for the same reason as the coordinate transform repository: AWS
        // Batch caps the image reference at 255 characters, and the auto-generated name from this
        // nested-stack path plus the 32-character content tag would come close to it. The name embeds
        // `config.name` and `app.baseStackName`, so two deployments get different repositories, and
        // carries `-kms` with the CMK because Amazon ECR cannot change a repository's encryption and AWS
        // CloudFormation cannot replace a custom-named resource under the same name.
        const repositoryName = [
            props.config.name,
            props.config.app.baseStackName,
            "ziparchive",
            ...(props.kmsKey ? ["kms"] : []),
        ]
            .join("-")
            .toLowerCase();

        // Imported by ARN so no derived grant lands in the storage nested stack's key policy
        // (infra/CLAUDE.md, encryption trap 2). Amazon ECR creates its own grants on the key.
        const encryptionKey = props.kmsKey
            ? kms.Key.fromKeyArn(this, "ZipArchiveEcrKmsKeyRef", props.kmsKey.keyArn)
            : undefined;

        this.repository = new ecr.Repository(this, "EcrRepo-ZipArchive", {
            repositoryName,
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
                    description: "Keep last 10 images for zip-archive",
                },
            ],
        });

        const sourceAsset = new s3assets.Asset(this, "Source-ZipArchive", {
            path: path.join(
                __dirname,
                "..",
                "..",
                "..",
                "..",
                "..",
                "..",
                "..",
                "backendPipelines",
                "utility",
                "zipArchive",
                "container"
            ),
            // The container's tests are not part of the image (the Dockerfile copies only the package).
            exclude: [".git", "*.pyc", "__pycache__", ".venv", "node_modules", ".env", "tests"],
        });

        // Content-addressed image tag, supplied to the build and consumed by the Batch job definition
        // from this one literal so the two sides cannot name different images.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        const project = new codebuild.Project(this, "CodeBuild-ZipArchive", {
            description: "Build Zip Archive container image and push to ECR",
            environment: {
                buildImage: Config.CODEBUILD_BUILD_IMAGE,
                computeType: codebuild.ComputeType.LARGE,
                privileged: true,
                environmentVariables: {
                    ECR_REPO_URI: {
                        value: this.repository.repositoryUri,
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
                },
            },
            // CodeBuild runs outside the VPC to pull public base images.
            // It accesses ECR and S3 via IAM (no VPC endpoints needed).
            source: codebuild.Source.s3({
                bucket: sourceAsset.bucket,
                path: sourceAsset.s3ObjectKey,
            }),
            buildSpec: codebuild.BuildSpec.fromSourceFilename("buildspec.yml"),
            timeout: Duration.hours(1),
            cache: codebuild.Cache.local(
                codebuild.LocalCacheMode.DOCKER_LAYER,
                codebuild.LocalCacheMode.CUSTOM
            ),
        });

        this.repository.grantPullPush(project);
        sourceAsset.grantRead(project);

        project.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["ecr:GetAuthorizationToken"],
                resources: ["*"],
            })
        );

        const triggerFunction = new cdk.aws_lambda.Function(this, "BuildTrigger-ZipArchive", {
            runtime: cdk.aws_lambda.Runtime.PYTHON_3_12,
            handler: "index.handler",
            timeout: Duration.minutes(1),
            code: codeBuildTriggerCode(),
        });

        triggerFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:StartBuild"],
                resources: [project.projectArn],
            })
        );

        const triggerProvider = new cr.Provider(this, "BuildProvider-ZipArchive", {
            onEventHandler: triggerFunction,
        });

        // EcrRepositoryUri re-fires the build when the repository is replaced (a rename or the `-kms`
        // flip), as in CoordinateTransformCodeBuildConstruct: CloudFormation invokes the custom
        // resource's Update only when one of its properties changes.
        new cdk.CustomResource(this, "BuildTriggerCR-ZipArchive", {
            serviceToken: triggerProvider.serviceToken,
            properties: {
                ProjectName: project.projectName,
                SourceHash: sourceAsset.assetHash,
                EcrRepositoryUri: this.repository.repositoryUri,
            },
        });

        this.imageTag = imageTag;
        this.codeBuildProjectName = project.projectName;

        // CDK Nag Suppressions
        NagSuppressions.addResourceSuppressions(
            project,
            [
                {
                    id: "AwsSolutions-CB4",
                    reason: "CodeBuild project uses default AWS-managed encryption. Build artifacts are transient container images pushed to ECR which has its own encryption.",
                },
                {
                    id: "AwsSolutions-CB3",
                    reason: "Privileged mode is required for Docker-in-Docker container image builds in CodeBuild.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ecr:GetAuthorizationToken requires resource '*' as it is an account-level operation, not scoped to a specific repository.",
                },
            ],
            true
        );

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
    }
}
