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
import { suppressCdkNagLambda } from "../../../../../helper/security";

const PIPELINE_DIR = path.join(
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
    "3dBasic"
);

// The image is small: MEASURED locally with the buildspec's own commands, 628 MB, its layers build in
// 5 s from a cached base image and push in 19 s. A CodeBuild run adds provisioning and an uncached pull
// of the ~520 MB base image, so it is expected to take minutes; 20 minutes is several times that. The
// build and queue timeouts together stay below the waiter's total timeout, so a stuck build ends in its
// own TIMED_OUT status, which the deployment reports, before the waiter gives up. The provider framework
// caps the waiter at one hour, which is also AWS CloudFormation's custom resource timeout.
const BUILD_TIMEOUT = Duration.minutes(20);
const BUILD_QUEUED_TIMEOUT = Duration.minutes(10);
const WAIT_QUERY_INTERVAL = Duration.seconds(30);
const WAIT_TOTAL_TIMEOUT = Duration.minutes(35);

export interface Conversion3dBasicCodeBuildConstructProps {
    config: Config.Config;
}

/**
 * Builds the 3D Basic Conversion Lambda container image with AWS CodeBuild and pushes it to Amazon ECR.
 *
 * Unlike the AWS Batch pipelines' CodeBuild constructs, the build trigger WAITS for the build: AWS
 * Lambda resolves the image when it creates or updates the function, so the function depends on
 * `buildTrigger` and the deployment fails, with the build's status, when the build fails.
 */
export class Conversion3dBasicCodeBuildConstruct extends Construct {
    public readonly repository: ecr.Repository;
    /** Content-addressed tag the build pushes and the Lambda function consumes. */
    public readonly imageTag: string;
    public readonly codeBuildProjectName: string;
    /** Completes only once the image is in the repository; the function depends on it. */
    public readonly buildTrigger: cdk.CustomResource;

    constructor(parent: Construct, name: string, props: Conversion3dBasicCodeBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;

        // An explicit repositoryName, as for coordinate transform: the auto-generated name is derived
        // from the nested-stack path, and this construct sits one nested stack deeper than coordinate
        // transform's, whose auto-generated URI measured 237 characters. A short deterministic name
        // keeps the function's image URI readable and reproducible across deployments.
        //
        // Custom-named, therefore REDEPLOY-COLLISION relevant (infra/CLAUDE.md storage documentation
        // rule): the name embeds `config.name` and `app.baseStackName`, so only an orphan left by a
        // failed teardown of the SAME configuration can conflict. removalPolicy DESTROY + emptyOnDelete
        // means an ordinary teardown removes it.
        const repositoryName = [props.config.name, props.config.app.baseStackName, "3dbasic"]
            .join("-")
            .toLowerCase();

        this.repository = new ecr.Repository(this, "EcrRepo-3dBasic", {
            repositoryName,
            removalPolicy: RemovalPolicy.DESTROY,
            emptyOnDelete: true,
            imageScanOnPush: true,
            lifecycleRules: [
                {
                    maxImageCount: 10,
                    description: "Keep last 10 images for 3D basic conversion",
                },
            ],
        });

        const sourceAsset = new s3assets.Asset(this, "Source-3dBasic", {
            path: path.join(PIPELINE_DIR, "lambdaContainer"),
            exclude: [
                ".git",
                "*.pyc",
                "__pycache__",
                ".pytest_cache",
                ".venv",
                "node_modules",
                ".env",
            ],
        });

        // Content-addressed image tag, supplied to the build and consumed by the Lambda function from
        // this one literal so the two sides cannot name different images.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        const project = new codebuild.Project(this, "CodeBuild-3dBasic", {
            description: "Build the 3D Basic Conversion Lambda container image and push it to ECR",
            environment: {
                buildImage: Config.CODEBUILD_BUILD_IMAGE,
                computeType: codebuild.ComputeType.SMALL,
                privileged: true,
                environmentVariables: {
                    ECR_REPO_URI: {
                        value: this.repository.repositoryUri,
                    },
                    IMAGE_TAG: {
                        value: imageTag,
                    },
                    AWS_DEFAULT_REGION: {
                        value: region,
                    },
                },
            },
            // CodeBuild runs outside the VPC to pull the public base image.
            // It accesses ECR and S3 via IAM (no VPC endpoints needed).
            source: codebuild.Source.s3({
                bucket: sourceAsset.bucket,
                path: sourceAsset.s3ObjectKey,
            }),
            buildSpec: codebuild.BuildSpec.fromSourceFilename("buildspec.yml"),
            timeout: BUILD_TIMEOUT,
            queuedTimeout: BUILD_QUEUED_TIMEOUT,
            cache: codebuild.Cache.local(codebuild.LocalCacheMode.DOCKER_LAYER),
        });

        // Push and pull on this repository; also adds ecr:GetAuthorizationToken, which has no
        // resource-level form. Source.s3 already grants read on the source object.
        this.repository.grantPullPush(project);

        const triggerCode = lambda.Code.fromAsset(path.join(PIPELINE_DIR, "codeBuildTrigger"), {
            exclude: ["tests", "__pycache__", "*.pyc", ".pytest_cache"],
        });

        const startBuildFunction = new lambda.Function(this, "BuildStart-3dBasic", {
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            handler: "codeBuildTrigger.on_event",
            code: triggerCode,
            timeout: Duration.minutes(1),
        });
        startBuildFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:StartBuild"],
                resources: [project.projectArn],
            })
        );
        startBuildFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["ecr:DescribeImages"],
                resources: [this.repository.repositoryArn],
            })
        );

        const buildStatusFunction = new lambda.Function(this, "BuildStatus-3dBasic", {
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            handler: "codeBuildTrigger.is_complete",
            code: triggerCode,
            timeout: Duration.minutes(1),
        });
        buildStatusFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:BatchGetBuilds"],
                resources: [project.projectArn],
            })
        );

        const buildProvider = new cr.Provider(this, "BuildProvider-3dBasic", {
            onEventHandler: startBuildFunction,
            isCompleteHandler: buildStatusFunction,
            queryInterval: WAIT_QUERY_INTERVAL,
            totalTimeout: WAIT_TOTAL_TIMEOUT,
        });

        // ProjectName, SourceHash and EcrRepositoryUri are the trigger inputs: AWS CloudFormation
        // invokes the Update handler only when a property changes, so a source change or a repository
        // replacement (RepositoryName is a REPLACEMENT property, which leaves an empty repository behind
        // an unchanged project and source) each re-fire the build. ImageTag is what the handler builds
        // and looks for.
        this.buildTrigger = new cdk.CustomResource(this, "BuildTriggerCR-3dBasic", {
            serviceToken: buildProvider.serviceToken,
            properties: {
                ProjectName: project.projectName,
                SourceHash: sourceAsset.assetHash,
                EcrRepositoryUri: this.repository.repositoryUri,
                ImageTag: imageTag,
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
                    reason: "The project's only output is the 3D Basic Lambda container image, which it pushes to an ECR repository with its own encryption at rest.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "The build role's wildcards are ecr:GetAuthorizationToken, which has no resource-level form, and the CDK-generated log-stream and report-group grants under this project's own name.",
                },
            ],
            true
        );

        suppressCdkNagLambda(startBuildFunction);
        suppressCdkNagLambda(buildStatusFunction);

        NagSuppressions.addResourceSuppressions(
            buildProvider,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The CDK provider framework functions for the 3D Basic image build trigger use AWSLambdaBasicExecutionRole for their CloudWatch logging.",
                },
                {
                    id: "AwsSolutions-SF1",
                    reason: "The CDK provider framework's waiter state machine only polls the 3D Basic image build; the build's own status and log link are reported in the deployment event.",
                },
                {
                    id: "AwsSolutions-SF2",
                    reason: "The CDK provider framework's waiter state machine only polls the 3D Basic image build and has no downstream calls to trace.",
                },
            ],
            true
        );
    }
}
