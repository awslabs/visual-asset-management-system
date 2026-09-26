/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as kms from "aws-cdk-lib/aws-kms";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as s3assets from "aws-cdk-lib/aws-s3-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { Stack, RemovalPolicy, Duration } from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../config/config";
import { contentImageTag } from "../../../../../helper/containerImageTag";

export interface VideoSopBomCodeBuildConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    /** Deployment key for the repository's image layers; absent when no CMK is configured. */
    kmsKey?: kms.IKey;
}

export class VideoSopBomCodeBuildConstruct extends Construct {
    public readonly repository: ecr.Repository;
    /** Content-addressed tag the build pushes and the Batch job definition consumes. */
    public readonly imageTag: string;
    public readonly codeBuildProjectName: string;

    constructor(parent: Construct, name: string, props: VideoSopBomCodeBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        // An EXPLICIT repositoryName. AWS Batch caps a container image reference at 255 characters over
        // the whole `<account>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>` string, and CDK derives
        // an auto-generated repository name from the nested-stack path — for a pipeline nested under
        // PipelineBuilder that measured 237 characters on coordinateTransform's identically shaped path,
        // which with the 32-character content tag is 270 and every job is rejected at submit with
        // `Container.image should be 255 characters or less`.
        //
        // Custom-named, therefore REDEPLOY-COLLISION relevant (infra/CLAUDE.md storage documentation
        // rule): the name embeds `config.name` and `app.baseStackName`, so two deployments differing in
        // either get different repositories, and only an orphan left by a failed teardown of the SAME
        // configuration can conflict. removalPolicy DESTROY + emptyOnDelete means an ordinary teardown
        // removes it.
        const repositoryName = [props.config.name, props.config.app.baseStackName, "videosopbom"]
            .join("-")
            .toLowerCase();

        // Image layers are encrypted with the deployment key when one is configured. Amazon ECR
        // encrypts and decrypts through a grant it creates on the key at repository creation, so the
        // push (CodeBuild) and pull (ECS agent) principals need no key permissions of their own.
        this.repository = new ecr.Repository(this, "EcrRepo-VideoSopBom", {
            repositoryName,
            removalPolicy: RemovalPolicy.DESTROY,
            emptyOnDelete: true,
            imageScanOnPush: true,
            encryption: props.kmsKey ? ecr.RepositoryEncryption.KMS : undefined,
            encryptionKey: props.kmsKey,
            lifecycleRules: [
                {
                    maxImageCount: 10,
                    description: "Keep last 10 images for video-sop-bom",
                },
            ],
        });

        // The Asset hash drives IMAGE_TAG, so what the hash covers decides what re-fires the build. The
        // list mirrors the container's .dockerignore: that file only keeps the entries out of the image,
        // and an edit to one of them would otherwise rebuild an identical image.
        const sourceAsset = new s3assets.Asset(this, "Source-VideoSopBom", {
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
                "videoSopBom",
                "container"
            ),
            exclude: [
                "__pycache__",
                "*.pyc",
                "*.egg-info",
                ".git",
                ".env",
                ".venv",
                "node_modules",
                "tests/",
                ".pytest_cache",
                ".ruff_cache",
            ],
        });

        // Content-addressed image tag, supplied to the build and consumed by the Batch job definition
        // from this one literal so the two sides cannot name different images.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        const project = new codebuild.Project(this, "CodeBuild-VideoSopBom", {
            description: "Build Video SOP/BOM Extraction container image and push to ECR",
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

        const triggerFunction = new cdk.aws_lambda.Function(this, "BuildTrigger-VideoSopBom", {
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
        });

        triggerFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:StartBuild"],
                resources: [project.projectArn],
            })
        );

        const triggerProvider = new cr.Provider(this, "BuildProvider-VideoSopBom", {
            onEventHandler: triggerFunction,
        });

        // EcrRepositoryUri is a trigger input, not decoration. CloudFormation invokes a custom resource's
        // Update only when one of its properties changes, and `RepositoryName` is a REPLACEMENT property —
        // so a repository rename destroys the old repository (with its images) and creates an empty one
        // while `ProjectName` and `SourceHash` both stay identical. No build fires, and the Batch job
        // definition is left pointing at a tag that exists nowhere. Including the URI makes the rename
        // itself the thing that re-fires the build.
        new cdk.CustomResource(this, "BuildTriggerCR-VideoSopBom", {
            serviceToken: triggerProvider.serviceToken,
            properties: {
                ProjectName: project.projectName,
                SourceHash: sourceAsset.assetHash,
                EcrRepositoryUri: this.repository.repositoryUri,
            },
        });

        this.imageTag = imageTag;
        this.codeBuildProjectName = project.projectName;

        // CDK Nag suppressions. Every IAM4/IAM5 entry names the managed policy or wildcard shape it covers.
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
                    appliesTo: [{ regex: "/^Resource::\\*$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "CodeBuild writes its build log streams under one log group and its test reports " +
                        "under one report-group prefix, both named after this project; the ARNs CDK grants " +
                        "carry the stream and report wildcards those services require.",
                    appliesTo: [
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:logs:.*:log-group:/aws/codebuild/<.*>:\\*$/g",
                        },
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:codebuild:.*:report-group/<.*>-\\*$/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "The build source is a CDK asset in the deployment's asset bucket; grantRead " +
                        "expresses object access as the bucket ARN plus a key wildcard and the S3 read " +
                        "action families.",
                    appliesTo: [
                        { regex: "/^Resource::arn:<AWS::Partition>:s3:::cdk-.*-assets-.*/\\*$/g" },
                        "Action::s3:GetObject*",
                        "Action::s3:GetBucket*",
                        "Action::s3:List*",
                    ],
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
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Custom resource provider framework invokes the trigger function through its version qualifiers, which reaches no other function. This is CDK-managed infrastructure.",
                    appliesTo: [{ regex: "/^Resource::<.*\\.Arn>:\\*$/g" }],
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
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Build trigger Lambda role grants are scoped to the project ARN and to this function's own version qualifiers.",
                    appliesTo: [{ regex: "/^Resource::<.*\\.Arn>:\\*$/g" }],
                },
            ],
            true
        );
    }
}
