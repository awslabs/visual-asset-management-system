/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3assets from "aws-cdk-lib/aws-s3-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { Stack, RemovalPolicy, Duration } from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../../config/config";

export interface CosmosCodeBuildConstructProps extends cdk.StackProps {
    config: Config.Config;
    modelCacheBucket: s3.Bucket;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
}

export interface PipelineEcrRepo {
    repository: ecr.Repository;
    imageUri: string;
    codeBuildProjectName: string;
}

/**
 * CosmosCodeBuildConstruct
 *
 * Builds Cosmos container images via CodeBuild and pushes them to ECR.
 * This avoids local Docker builds of 35GB+ GPU images, which are extremely slow.
 *
 * For each enabled pipeline group (predictV2, reason, transfer), this construct creates:
 * - An ECR repository for the container image
 * - An S3 asset upload of the container source directory
 * - A CodeBuild project configured with Docker layer caching
 * - A custom resource that triggers the build on Create/Update
 */
export class CosmosCodeBuildConstruct extends Construct {
    public predictV2Repo?: PipelineEcrRepo;
    public reasonRepo?: PipelineEcrRepo;
    public transferRepo?: PipelineEcrRepo;

    constructor(parent: Construct, name: string, props: CosmosCodeBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;
        const cosmosConfig = props.config.app.pipelines.useNvidiaCosmos;

        /**
         * Helper: Create ECR repo, CodeBuild project, and trigger for a single pipeline.
         */
        const createPipelineBuild = (
            pipelineKey: string,
            containerDir: string
        ): PipelineEcrRepo => {
            // ECR Repository — no explicit repositoryName so CDK auto-generates a unique name
            // per deployment (avoids collisions across multiple VAMS stacks in the same region)
            const repository = new ecr.Repository(this, `EcrRepo-${pipelineKey}`, {
                removalPolicy: RemovalPolicy.DESTROY,
                emptyOnDelete: true,
                imageScanOnPush: true,
                lifecycleRules: [
                    {
                        maxImageCount: 10,
                        description: `Keep last 10 images for vams-cosmos-${pipelineKey}`,
                    },
                ],
            });

            // S3 Asset: upload container source directory
            const sourceAsset = new s3assets.Asset(this, `Source-${pipelineKey}`, {
                path: path.join(__dirname, containerDir),
                exclude: [".git", "*.pyc", "__pycache__", ".venv", "node_modules", ".env"],
            });

            // CodeBuild Project — runs in the same private VPC/subnets as pipeline Batch compute.
            // Private subnets have NAT Gateway egress for pulling Docker base images and cloning repos.
            const project = new codebuild.Project(this, `CodeBuild-${pipelineKey}`, {
                description: `Build Cosmos ${pipelineKey} container image and push to ECR`,
                environment: {
                    buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
                    computeType: codebuild.ComputeType.LARGE,
                    privileged: true,
                    environmentVariables: {
                        ECR_REPO_URI: {
                            value: repository.repositoryUri,
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
            const triggerFunction = new cdk.aws_lambda.Function(
                this,
                `BuildTrigger-${pipelineKey}`,
                {
                    runtime: cdk.aws_lambda.Runtime.PYTHON_3_12,
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
                }
            );

            // Grant the trigger Lambda permission to start builds
            triggerFunction.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: ["codebuild:StartBuild"],
                    resources: [project.projectArn],
                })
            );

            const triggerProvider = new cr.Provider(this, `BuildProvider-${pipelineKey}`, {
                onEventHandler: triggerFunction,
            });

            new cdk.CustomResource(this, `BuildTriggerCR-${pipelineKey}`, {
                serviceToken: triggerProvider.serviceToken,
                properties: {
                    ProjectName: project.projectName,
                    SourceHash: sourceAsset.assetHash,
                },
            });

            // Image URI: latest tag
            const imageUri = `${repository.repositoryUri}:latest`;

            /**
             * CDK Nag Suppressions
             */

            // CB4: CodeBuild project encryption (using default AWS-managed key is acceptable for build artifacts)
            NagSuppressions.addResourceSuppressions(
                project,
                [
                    {
                        id: "AwsSolutions-CB4",
                        reason: "Cosmos CodeBuild project uses default AWS-managed encryption. Build artifacts are transient container images pushed to ECR which has its own encryption.",
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
         * Conditional creation: predictV2
         * Enabled if any of the v2 predict models are enabled.
         */
        const anyPredictV2Enabled =
            cosmosConfig.modelsPredict.text2world2B_v2?.enabled ||
            cosmosConfig.modelsPredict.video2world2B_v2?.enabled ||
            cosmosConfig.modelsPredict.text2world14B_v2?.enabled ||
            cosmosConfig.modelsPredict.video2world14B_v2?.enabled;

        if (anyPredictV2Enabled) {
            this.predictV2Repo = createPipelineBuild(
                "predict-v2",
                "../../../../../../../../backendPipelines/genAi/nvidia/cosmos/predict/containerv2.5"
            );
            new cdk.CfnOutput(this, "CosmosPredictCodeBuildProject", {
                value: this.predictV2Repo.codeBuildProjectName,
                description:
                    "CodeBuild project name for Cosmos Predict v2.5 container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
            });
        }

        /**
         * Conditional creation: reason
         * Enabled if reason2B or reason8B is enabled.
         */
        const anyReasonEnabled =
            cosmosConfig.modelsReason?.reason2B?.enabled ||
            cosmosConfig.modelsReason?.reason8B?.enabled;

        if (anyReasonEnabled) {
            this.reasonRepo = createPipelineBuild(
                "reason",
                "../../../../../../../../backendPipelines/genAi/nvidia/cosmos/reason/container"
            );
            new cdk.CfnOutput(this, "CosmosReasonCodeBuildProject", {
                value: this.reasonRepo.codeBuildProjectName,
                description:
                    "CodeBuild project name for Cosmos Reason container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
            });
        }

        /**
         * Conditional creation: transfer
         * Enabled if transfer2B is enabled.
         */
        const anyTransferEnabled = cosmosConfig.modelsTransfer?.transfer2B?.enabled;

        if (anyTransferEnabled) {
            this.transferRepo = createPipelineBuild(
                "transfer",
                "../../../../../../../../backendPipelines/genAi/nvidia/cosmos/transfer/container"
            );
            new cdk.CfnOutput(this, "CosmosTransferCodeBuildProject", {
                value: this.transferRepo.codeBuildProjectName,
                description:
                    "CodeBuild project name for Cosmos Transfer container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
            });
        }
    }
}
