/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

export interface CoordinateTransformCodeBuildConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
}

export class CoordinateTransformCodeBuildConstruct extends Construct {
    public readonly repository: ecr.Repository;
    /** Content-addressed tag the build pushes and the Batch job definition consumes. */
    public readonly imageTag: string;
    public readonly codeBuildProjectName: string;

    constructor(
        parent: Construct,
        name: string,
        props: CoordinateTransformCodeBuildConstructProps
    ) {
        super(parent, name);

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        // An EXPLICIT repositoryName, unlike the other CodeBuild pipelines, because the auto-generated
        // one is too long for AWS Batch to accept.
        //
        // Batch and Amazon ECS cap a container image reference at 255 characters over the whole
        // `<account>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>` string. CDK derives the
        // repository name from the nested-stack path, and this pipeline's path is the deepest of the
        // CodeBuild set — `pipelinebuilderne-coordinatetransformbuildernestedstackneste-<hash>-
        // coordinatetransformpipelinecoordtransformcodebuildecrrepocoordtransform<hash>-<suffix>`.
        //
        // MEASURED on the reference deployment: that URI is 237 characters, so with the 32-character
        // content tag the reference is 270 and every job is rejected at submit with
        // `Container.image should be 255 characters or less` — the job never starts, no container log
        // exists, and the workflow simply waits out its task timeout. The comparable auto-named
        // repositories measure 194-207, which is why the existing tag truncation was calibrated against
        // Splat Toolbox's 207 and why nothing caught this: `useCodeBuild` defaults to false here, so
        // the path was never exercised.
        //
        // Truncating the tag further is not the fix — it would cost content-addressing for 15
        // characters. A short deterministic name is: host (45) + name (~35) + tag (33) leaves well over
        // 100 characters of headroom, and the same name is reproducible across deployments.
        //
        // Custom-named, therefore REDEPLOY-COLLISION relevant (infra/CLAUDE.md storage documentation
        // rule): the name embeds `config.name` and `app.baseStackName`, so two deployments differing in
        // either get different repositories, and only an orphan left by a failed teardown of the SAME
        // configuration can conflict. removalPolicy DESTROY + emptyOnDelete means an ordinary teardown
        // removes it.
        const repositoryName = [props.config.name, props.config.app.baseStackName, "coordtransform"]
            .join("-")
            .toLowerCase();

        this.repository = new ecr.Repository(this, "EcrRepo-CoordTransform", {
            repositoryName,
            removalPolicy: RemovalPolicy.DESTROY,
            emptyOnDelete: true,
            imageScanOnPush: true,
            lifecycleRules: [
                {
                    maxImageCount: 10,
                    description: "Keep last 10 images for coordinate-transform",
                },
            ],
        });

        const sourceAsset = new s3assets.Asset(this, "Source-CoordTransform", {
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
                "conversion",
                "coordinateTransform",
                "container"
            ),
            exclude: [".git", "*.pyc", "__pycache__", ".venv", "node_modules", ".env"],
        });

        // Content-addressed image tag, supplied to the build and consumed by the Batch job definition
        // from this one literal so the two sides cannot name different images.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        const project = new codebuild.Project(this, "CodeBuild-CoordTransform", {
            description: "Build Coordinate Transform container image and push to ECR",
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

        const triggerFunction = new cdk.aws_lambda.Function(this, "BuildTrigger-CoordTransform", {
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

        const triggerProvider = new cr.Provider(this, "BuildProvider-CoordTransform", {
            onEventHandler: triggerFunction,
        });

        // EcrRepositoryUri is a trigger input, not decoration. CloudFormation invokes a custom resource's
        // Update only when one of its properties changes, and `RepositoryName` is a REPLACEMENT property —
        // so a repository rename destroys the old repository (with its images) and creates an empty one
        // while `ProjectName` and `SourceHash` both stay identical. No build fires, and the Batch job
        // definition is left pointing at a tag that exists nowhere.
        //
        // MEASURED on the reference deployment: after the rename, the new repository held 0 images while
        // the job definition referenced tag `f0462...`, and the build had to be started by hand. Including
        // the URI makes the rename itself the thing that re-fires the build.
        new cdk.CustomResource(this, "BuildTriggerCR-CoordTransform", {
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
