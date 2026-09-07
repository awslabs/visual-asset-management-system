/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as s3assets from "aws-cdk-lib/aws-s3-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { Stack, RemovalPolicy, Duration } from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../config/config";
import { contentImageTag } from "../../../../../helper/containerImageTag";

export interface SplatToolboxCodeBuildConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
}

export interface PipelineEcrRepo {
    repository: ecr.Repository;
    /** Content-addressed tag the build pushes and the Batch job definition consumes. */
    imageTag: string;
    codeBuildProjectName: string;
}

/**
 * SplatToolboxCodeBuildConstruct
 *
 * Builds the Splat Toolbox container image via CodeBuild and pushes it to ECR.
 * This avoids local Docker builds of the large CUDA/PyTorch image, which are extremely slow.
 *
 * This construct creates:
 * - An ECR repository for the container image
 * - An S3 asset upload of the container source directory
 * - A CodeBuild project configured with Docker layer caching
 * - A custom resource that triggers the build on Create/Update
 */
export class SplatToolboxCodeBuildConstruct extends Construct {
    public splatToolboxRepo: PipelineEcrRepo;

    constructor(parent: Construct, name: string, props: SplatToolboxCodeBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        // ECR Repository — no explicit repositoryName so CDK auto-generates a unique name
        // per deployment (avoids collisions across multiple VAMS stacks in the same region)
        const repository = new ecr.Repository(this, "EcrRepo-splat-toolbox", {
            removalPolicy: RemovalPolicy.DESTROY,
            emptyOnDelete: true,
            imageScanOnPush: true,
            lifecycleRules: [
                {
                    maxImageCount: 10,
                    description: "Keep last 10 images for vams-splat-toolbox",
                },
            ],
        });

        // S3 Asset: upload container source directory. The parent construct syncs the upstream
        // Splat Toolbox repository into this directory before this asset is created, so the upload
        // carries the pinned-commit sources plus the local __main__.py Dockerfile edit.
        const sourceAsset = new s3assets.Asset(this, "Source-splat-toolbox", {
            path: path.join(
                __dirname,
                "../../../../../../../backendPipelines/3dRecon/splatToolbox/container"
            ),
            exclude: [".git", "*.pyc", "__pycache__", ".venv", "node_modules", ".env"],
        });

        // Content-addressed image tag, supplied to the build and consumed by the Batch job definition
        // from this one literal so the two sides cannot name different images.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        // CodeBuild Project — runs in the same private VPC/subnets as pipeline Batch compute.
        // Private subnets have NAT Gateway egress for pulling Docker base images and cloning repos.
        const project = new codebuild.Project(this, "CodeBuild-splat-toolbox", {
            description: "Build Splat Toolbox container image and push to ECR",
            environment: {
                buildImage: Config.CODEBUILD_BUILD_IMAGE,
                // X_LARGE for its DISK, not its CPU: LARGE provides 128 GB and the upstream
                // Dockerfile's `apt-get upgrade -y` over an NVIDIA CUDA base image unpacks the whole
                // CUDA stack a second time, which exhausts it —
                // `unable to make backup link of '.../libcusolver_static.a' before installing new
                // version: No space left on device`, at layer 6 of 77. X_LARGE provides 824 GB and is
                // the cheaper of the two tiers that do.
                computeType: codebuild.ComputeType.X_LARGE,
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

        // Custom Resource: trigger CodeBuild on Create/Update
        const triggerFunction = new cdk.aws_lambda.Function(this, "BuildTrigger-splat-toolbox", {
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            handler: "index.handler",
            timeout: Duration.minutes(1),
            code: cdk.aws_lambda.Code.fromInline(`
import boto3
import cfnresponse

def handler(event, context):
    try:
        request_type = event.get("RequestType", "")
        if request_type in ("Create", "Update"):
            project_name = event["ResourceProperties"]["ProjectName"]
            client = boto3.client("codebuild")
            response = client.start_build(projectName=project_name)
            build_id = response["build"]["id"]
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {"BuildId": build_id})
        else:
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
    except Exception as e:
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": str(e)})
`),
        });

        // Grant the trigger Lambda permission to start builds
        triggerFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:StartBuild"],
                resources: [project.projectArn],
            })
        );

        const triggerProvider = new cr.Provider(this, "BuildProvider-splat-toolbox", {
            onEventHandler: triggerFunction,
        });

        new cdk.CustomResource(this, "BuildTriggerCR-splat-toolbox", {
            serviceToken: triggerProvider.serviceToken,
            properties: {
                ProjectName: project.projectName,
                SourceHash: sourceAsset.assetHash,
            },
        });

        /**
         * CDK Nag Suppressions
         */

        // CB4/CB3: CodeBuild project encryption + privileged mode
        NagSuppressions.addResourceSuppressions(
            project,
            [
                {
                    id: "AwsSolutions-CB4",
                    reason: "Splat Toolbox CodeBuild project uses default AWS-managed encryption. Build artifacts are transient container images pushed to ECR which has its own encryption.",
                },
                {
                    id: "AwsSolutions-CB3",
                    reason: "Privileged mode is required for Docker-in-Docker container image builds in CodeBuild.",
                },
            ],
            true
        );

        // IAM5 on the build project and its role. Applied with applyToChildren, so the reason has to
        // cover every wildcard shape in the subtree rather than only the ECR one: an ECR-only
        // justification silently blankets the S3 and CloudWatch Logs wildcards as well.
        NagSuppressions.addResourceSuppressions(
            project,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Three wildcard shapes, each required: ecr:GetAuthorizationToken is account-level and takes no repository ARN; the source-asset grant covers object keys under one bucket prefix; and CodeBuild writes to log streams and report groups whose names it generates per build.",
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

        this.splatToolboxRepo = {
            repository,
            imageTag,
            codeBuildProjectName: project.projectName,
        };

        new cdk.CfnOutput(this, "SplatToolboxCodeBuildProject", {
            value: project.projectName,
            description:
                "CodeBuild project name for the Splat Toolbox container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
        });
    }
}
