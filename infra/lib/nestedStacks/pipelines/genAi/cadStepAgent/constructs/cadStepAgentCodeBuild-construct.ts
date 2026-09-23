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
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { Stack, RemovalPolicy, Duration } from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../config/config";
import { contentImageTag } from "../../../../../helper/containerImageTag";

/** The Docker platform the image is built for; decided by the runtime that will run it. */
export type CadStepAgentImagePlatform = "linux/arm64" | "linux/amd64";

/** The pipeline's Lambda sources; the image-build custom resource handlers live beside the pipeline Lambdas. */
const LAMBDA_CODE_PATH = path.join(
    __dirname,
    "../../../../../../../backendPipelines/genAi/cadStepAgent/lambda"
);

export interface CadStepAgentCodeBuildConstructProps extends cdk.StackProps {
    config: Config.Config;
    /** linux/arm64 for the AgentCore Runtime (its only supported architecture), linux/amd64 for Fargate. */
    platform: CadStepAgentImagePlatform;
}

export class CadStepAgentCodeBuildConstruct extends Construct {
    public readonly repository: ecr.Repository;
    /** Content-addressed tag the build pushes and the runtime consumes. */
    public readonly imageTag: string;
    public readonly codeBuildProjectName: string;
    /**
     * The custom resource that completes only once the image at `imageTag` has been pushed. Anything
     * that names the image (the AgentCore Runtime, the Batch job definition) depends on it.
     */
    public readonly imageBuild: cdk.CustomResource;

    constructor(parent: Construct, name: string, props: CadStepAgentCodeBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        // A short explicit repositoryName: AWS Batch caps a container image reference at 255
        // characters, and a nested-stack-derived name at this depth exceeds it once the 32-character
        // content tag is appended. Custom-named, therefore redeploy-collision relevant: the name embeds
        // config.name and app.baseStackName, and removalPolicy DESTROY + emptyOnDelete removes it on
        // an ordinary teardown.
        const repositoryName = [props.config.name, props.config.app.baseStackName, "cadstepagent"]
            .join("-")
            .toLowerCase();

        this.repository = new ecr.Repository(this, "EcrRepo-CadStepAgent", {
            repositoryName,
            removalPolicy: RemovalPolicy.DESTROY,
            emptyOnDelete: true,
            imageScanOnPush: true,
            lifecycleRules: [
                {
                    maxImageCount: 10,
                    description: "Keep last 10 images for the CAD STEP agent",
                },
            ],
        });

        const sourceAsset = new s3assets.Asset(this, "Source-CadStepAgent", {
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
                "genAi",
                "cadStepAgent",
                "container"
            ),
            exclude: [".git", "*.pyc", "__pycache__", ".venv", "node_modules", ".env", "tests"],
        });

        // Content-addressed image tag, supplied to the build and consumed by the runtime from this
        // one literal so the two sides cannot name different images. A deployment builds one platform;
        // the build trigger below carries the platform as a property, so switching runtimes re-fires
        // the build and the tag is re-pushed for the new architecture.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        const project = new codebuild.Project(this, "CodeBuild-CadStepAgent", {
            description: `Build the CAD STEP agent container image (${props.platform}) and push to ECR`,
            environment: {
                // An arm64 image is built on an arm64 build host so the Python wheels resolve natively.
                buildImage:
                    props.platform === "linux/arm64"
                        ? codebuild.LinuxArmBuildImage.AMAZON_LINUX_2023_STANDARD_3_0
                        : Config.CODEBUILD_BUILD_IMAGE,
                computeType: codebuild.ComputeType.LARGE,
                privileged: true,
                environmentVariables: {
                    ECR_REPO_URI: {
                        value: this.repository.repositoryUri,
                    },
                    IMAGE_TAG: {
                        value: imageTag,
                    },
                    TARGET_PLATFORM: {
                        value: props.platform,
                    },
                    AWS_ACCOUNT_ID: {
                        value: account,
                    },
                    AWS_DEFAULT_REGION: {
                        value: region,
                    },
                },
            },
            // CodeBuild runs outside the VPC to pull public base images and PyPI wheels.
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

        // The build is synchronous from CloudFormation's point of view: the onEvent handler starts it
        // and names the build id as the physical id, and the isComplete handler is polled until that
        // build is terminal. Amazon Bedrock AgentCore validates the image at CreateAgentRuntime time,
        // so the Runtime (and the Batch job definition) depend on this resource and are created only
        // once the content-addressed tag exists in the repository.
        const startBuildFunction = new lambda.Function(this, "BuildTrigger-CadStepAgent", {
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            handler: "imageBuildCustomResource.on_event",
            timeout: Duration.minutes(1),
            code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        });
        startBuildFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:StartBuild"],
                resources: [project.projectArn],
            })
        );

        const waitForBuildFunction = new lambda.Function(this, "BuildWait-CadStepAgent", {
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            handler: "imageBuildCustomResource.is_complete",
            timeout: Duration.minutes(1),
            code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        });
        waitForBuildFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:BatchGetBuilds"],
                resources: [project.projectArn],
            })
        );

        // The wait outlasts the project's own build timeout so a build that CodeBuild times out is
        // reported by status (with its build id) rather than by the framework giving up first.
        const buildProvider = new cr.Provider(this, "BuildProvider-CadStepAgent", {
            onEventHandler: startBuildFunction,
            isCompleteHandler: waitForBuildFunction,
            queryInterval: Duration.seconds(30),
            totalTimeout: Duration.hours(2),
        });

        // The repository URI is a trigger input: a repository rename is a replacement that leaves an
        // empty repository while ProjectName and SourceHash stay identical, so including it re-fires
        // the build on exactly that change.
        this.imageBuild = new cdk.CustomResource(this, "BuildTriggerCR-CadStepAgent", {
            serviceToken: buildProvider.serviceToken,
            properties: {
                ProjectName: project.projectName,
                SourceHash: sourceAsset.assetHash,
                TargetPlatform: props.platform,
                EcrRepositoryUri: this.repository.repositoryUri,
            },
        });

        this.imageTag = imageTag;
        this.codeBuildProjectName = project.projectName;

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
            buildProvider,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Custom resource provider framework uses AWS managed policies for basic Lambda execution. This is CDK-managed infrastructure.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Custom resource provider framework requires wildcard permissions for log group creation and for invoking its own onEvent/isComplete handlers and waiter state machine. This is CDK-managed infrastructure.",
                },
                {
                    id: "AwsSolutions-L1",
                    reason: "Custom resource provider framework Lambda runtime is managed by CDK and may not use the latest runtime version.",
                },
                {
                    id: "AwsSolutions-SF1",
                    reason: "The provider framework's isComplete waiter state machine is CDK-managed infrastructure; it carries no data worth logging beyond the build id.",
                },
                {
                    id: "AwsSolutions-SF2",
                    reason: "The provider framework's isComplete waiter state machine is CDK-managed infrastructure; X-Ray tracing is not exposed on it.",
                },
            ],
            true
        );

        for (const handler of [startBuildFunction, waitForBuildFunction]) {
            NagSuppressions.addResourceSuppressions(
                handler,
                [
                    {
                        id: "AwsSolutions-IAM4",
                        reason: "Image build custom resource Lambda uses AWSLambdaBasicExecutionRole managed policy for CloudWatch logging.",
                    },
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Image build custom resource Lambda role requires wildcard for log stream creation under its log group.",
                    },
                ],
                true
            );
        }
    }
}
