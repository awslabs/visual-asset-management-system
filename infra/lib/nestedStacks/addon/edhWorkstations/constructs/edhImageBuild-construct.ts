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
import * as Config from "../../../../../config/config";
import { contentImageTag } from "../../../../helper/containerImageTag";
import { suppressCdkNagEcrAuthTokenWildcard } from "../../../../helper/security";

/** Repository root: the operator's `dockerfileDir` is relative to it and the buildspec lives under it. */
const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..", "..", "..");
const BUILDSPEC_PATH = path.join(REPO_ROOT, "backendAddons", "edh", "buildspec.yml");
/** What the build source leaves out of `dockerfileDir`, at any depth, credential files among it. */
export const CONTAINER_SOURCE_EXCLUDE = [
    ".git",
    "*.pyc",
    "__pycache__",
    ".venv",
    "node_modules",
    ".env",
    ".env.*",
];

export interface EdhImageBuildConstructProps {
    config: Config.Config;
    /** `buildAndPushImages[].name`: repository suffix and the `imageRef` a job definition names. */
    imageName: string;
    /** `buildAndPushImages[].dockerfileDir`, relative to the repository root. */
    dockerfileDir: string;
    /** `buildAndPushImages[].platform`, forwarded to `docker build --platform` when set. */
    platform?: string;
    /** The repository is tagged `edh:visibility:<edhClusterId>=true`, which is how EDH lists it. */
    edhClusterId: string;
    /** `containers.imagePullRoleArns`: EDH compute roles granted pull on the repository; empty for none. */
    imagePullRoleArns: string[];
    /** `storageResources.encryption.kmsKey`: the shared VAMS key, undefined when the CMK is off. */
    kmsKey?: kms.IKey;
}

export class EdhImageBuildConstruct extends Construct {
    public readonly imageName: string;
    public readonly repository: ecr.Repository;
    /** Content-addressed tag the build pushes and every registered job definition names. */
    public readonly imageTag: string;
    public readonly imageUri: string;
    public readonly codeBuildProjectName: string;
    /** Starts the build on Create/Update; the catalog registration depends on it. */
    public readonly buildTrigger: cdk.CustomResource;

    constructor(parent: Construct, name: string, props: EdhImageBuildConstructProps) {
        super(parent, name);

        const region = Stack.of(this).region;
        this.imageName = props.imageName;

        // An EXPLICIT repositoryName: Batch and ECS cap an image reference at 255 characters, and
        // this construct sits a nested stack deeper than coordinateTransform, whose auto-generated
        // name already measured over the cap. DESTROY + emptyOnDelete removes the repository on an
        // ordinary teardown; a failed teardown can leave it behind under this fixed name.
        //
        // Amazon ECR cannot change a repository's encryption after it is created, so turning
        // useKmsCmkEncryption on or off replaces the repository, and AWS CloudFormation cannot replace
        // a custom-named resource under the same name. The name therefore carries `-kms` when the key
        // is used; the build trigger carries the repository URI, so the build re-fires into the new one.
        const repositoryName = [
            props.config.name,
            props.config.app.baseStackName,
            "edh",
            props.imageName,
            ...(props.kmsKey ? ["kms"] : []),
        ]
            .join("-")
            .toLowerCase();

        // Imported by ARN, not passed as the key object, so no grant CDK derives can land in the key
        // policy of the storage nested stack (infra/CLAUDE.md, encryption trap 2). Amazon ECR creates
        // its own grant on the key when the repository is created; pushing and pulling need no
        // permission on the key.
        const encryptionKey = props.kmsKey
            ? kms.Key.fromKeyArn(this, `EdhEcrKmsKeyRef-${props.imageName}`, props.kmsKey.keyArn)
            : undefined;

        this.repository = new ecr.Repository(this, `EcrRepo-Edh-${props.imageName}`, {
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
                    description: `Keep last 10 images for edh-${props.imageName}`,
                },
            ],
        });
        // The resource tag EDH's ECR listing selects on. The build pushes the content-addressed tag the
        // job definitions name, plus a mutable `latest` alias that seeds the next build's layer cache.
        cdk.Tags.of(this.repository).add(`edh:visibility:${props.edhClusterId}`, "true");

        const sourceAsset = new s3assets.Asset(this, `Source-Edh-${props.imageName}`, {
            path: path.join(REPO_ROOT, props.dockerfileDir),
            exclude: CONTAINER_SOURCE_EXCLUDE,
        });

        // Content-addressed image tag, supplied to the build and written into the job-definition
        // registration from this one literal so the two sides cannot name different images.
        const imageTag = contentImageTag(sourceAsset.assetHash);

        const environmentVariables: Record<string, codebuild.BuildEnvironmentVariable> = {
            ECR_REPO_URI: {
                value: this.repository.repositoryUri,
            },
            IMAGE_TAG: {
                value: imageTag,
            },
            AWS_DEFAULT_REGION: {
                value: region,
            },
        };
        if (props.platform) {
            environmentVariables.IMAGE_PLATFORM = { value: props.platform };
        }

        // Runs outside the VPC to pull public base images; reaches ECR and S3 through IAM.
        const project = new codebuild.Project(this, `CodeBuild-Edh-${props.imageName}`, {
            description: `Build the EDH container image '${props.imageName}' and push to ECR`,
            environment: {
                buildImage: Config.CODEBUILD_BUILD_IMAGE,
                computeType: codebuild.ComputeType.LARGE,
                privileged: true,
                environmentVariables,
            },
            source: codebuild.Source.s3({
                bucket: sourceAsset.bucket,
                path: sourceAsset.s3ObjectKey,
            }),
            // The buildspec ships with VAMS as its own asset rather than inside the operator's
            // Dockerfile directory, so the immutable-tag rule cannot be edited away per image.
            buildSpec: codebuild.BuildSpec.fromAsset(BUILDSPEC_PATH),
            timeout: Duration.hours(2),
            cache: codebuild.Cache.local(
                codebuild.LocalCacheMode.DOCKER_LAYER,
                codebuild.LocalCacheMode.CUSTOM
            ),
        });

        // grantPullPush includes ecr:GetAuthorizationToken on '*' (an account-level action).
        this.repository.grantPullPush(project);
        sourceAsset.grantRead(project);

        // Pull access for the EDH compute roles named in containers.imagePullRoleArns. A same-account
        // role whose identity policy already carries the pull actions needs no entry, and an empty
        // list emits no repository policy. ecr:GetAuthorizationToken is identity-only and cannot be
        // granted here.
        if (props.imagePullRoleArns.length > 0) {
            this.repository.addToResourcePolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    principals: props.imagePullRoleArns.map(
                        (roleArn) => new iam.ArnPrincipal(roleArn)
                    ),
                    actions: [
                        "ecr:BatchGetImage",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchCheckLayerAvailability",
                    ],
                })
            );
        }

        // Start-build trigger. Built directly: it wires no VAMS table, key, VPC or environment, and
        // holds one codebuild:StartBuild grant. The handler is the onEvent function of the Provider
        // below and speaks its protocol: the build id is returned as Data on Create and Update, a
        // failure propagates as an exception, and the framework alone answers CloudFormation (keeping
        // the incoming PhysicalResourceId on Update and Delete).
        const triggerFunction = new cdk.aws_lambda.Function(
            this,
            `BuildTrigger-Edh-${props.imageName}`,
            {
                runtime: Config.LAMBDA_PYTHON_RUNTIME,
                handler: "index.handler",
                timeout: Duration.minutes(1),
                code: cdk.aws_lambda.Code.fromInline(`
import boto3
from botocore.config import Config

def handler(event, context):
    request_type = event.get("RequestType", "")
    if request_type in ("Create", "Update"):
        project_name = event["ResourceProperties"]["ProjectName"]
        client = boto3.client(
            "codebuild", config=Config(retries={"max_attempts": 5, "mode": "adaptive"})
        )
        response = client.start_build(projectName=project_name)
        return {"Data": {"BuildId": response["build"]["id"]}}
    return {}
`),
            }
        );

        triggerFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["codebuild:StartBuild"],
                resources: [project.projectArn],
            })
        );

        const triggerProvider = new cr.Provider(this, `BuildProvider-Edh-${props.imageName}`, {
            onEventHandler: triggerFunction,
        });

        // EcrRepositoryUri is a trigger input: RepositoryName is a REPLACEMENT property, so a rename
        // recreates the repository empty while ProjectName and SourceHash both stay identical, and
        // without the URI no build would re-fire to repopulate it.
        this.buildTrigger = new cdk.CustomResource(this, `BuildTriggerCR-Edh-${props.imageName}`, {
            serviceToken: triggerProvider.serviceToken,
            properties: {
                ProjectName: project.projectName,
                SourceHash: sourceAsset.assetHash,
                EcrRepositoryUri: this.repository.repositoryUri,
            },
        });

        this.imageTag = imageTag;
        this.imageUri = `${this.repository.repositoryUri}:${imageTag}`;
        this.codeBuildProjectName = project.projectName;

        // ecr:GetAuthorizationToken on '*' (grantPullPush): the shared, appliesTo-scoped helper.
        suppressCdkNagEcrAuthTokenWildcard(project);
        NagSuppressions.addResourceSuppressions(
            project,
            [
                {
                    id: "AwsSolutions-CB4",
                    reason: "The EDH image build project uses default AWS-managed encryption. Build artifacts are transient container images pushed to ECR, which has its own encryption.",
                },
                {
                    id: "AwsSolutions-CB3",
                    reason: "Privileged mode is required for Docker-in-Docker container image builds in CodeBuild.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Read of the build source and the buildspec staged under the CDK asset bucket, as the asset grant renders it; the image push actions are scoped to the EDH image repository.",
                    appliesTo: [
                        { regex: "/^Action::s3:(GetObject|GetBucket|List)\\*$/g" },
                        { regex: "/^Resource::arn:.*:s3:::cdk-.*-assets-.*/\\*$/g" },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "CodeBuild's default role writes the project's own log group; streams are created per build, so the log-stream suffix is the wildcard CodeBuild emits.",
                    appliesTo: [
                        {
                            regex: "/^Resource::arn:.*:logs:.*:log-group:/aws/codebuild/<.*>:\\*$/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "CodeBuild's default role creates report groups named after the project; the suffix is the wildcard CodeBuild emits for the project's own report groups.",
                    appliesTo: [
                        { regex: "/^Resource::arn:.*:codebuild:.*:report-group/<.*>-\\*$/g" },
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

        // The trigger role's one inline statement is codebuild:StartBuild on the project ARN; only the
        // managed logging policy is left to suppress.
        NagSuppressions.addResourceSuppressions(
            triggerFunction,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The EDH image build trigger Lambda uses AWSLambdaBasicExecutionRole for CloudWatch logging.",
                },
            ],
            true
        );
    }
}
