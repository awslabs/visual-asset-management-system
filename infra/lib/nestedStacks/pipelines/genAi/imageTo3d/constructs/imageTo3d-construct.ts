/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as efs from "aws-cdk-lib/aws-efs";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as batch from "aws-cdk-lib/aws-batch";
import * as kms from "aws-cdk-lib/aws-kms";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { CfnOutput, Duration, RemovalPolicy, Stack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";
import { NagSuppressions } from "cdk-nag";
import {
    buildConstructPipelineFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildVamsExecuteImageTo3dPipelineFunction,
} from "../lambdaBuilder/imageTo3dFunctions";
import { Partition, Service } from "../../../../../helper/service-helper";
import { jobDefinitionNameFromRef } from "../../../../../helper/batchJobLogGroup";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../config/config";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
} from "../../../../../helper/security";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import { ImageTo3dModelImage } from "./imageTo3dCodeBuild-construct";
import { populateHuggingFaceTokenSecret } from "../../nvidia/customResources/populateHuggingFaceTokenSecret";

export interface ImageTo3dConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
    // From common construct:
    modelCacheBucket: s3.Bucket;
    efsFileSystem: efs.FileSystem;
    efsSecurityGroup: ec2.SecurityGroup;
    // Optional: CodeBuild-built images in ECR, keyed by model key (bypasses local Docker builds)
    codeBuildImages?: { [modelKey: string]: ImageTo3dModelImage };
}

/**
 * Default input properties
 */
const defaultProps: Partial<ImageTo3dConstructProps> = {};

/** Model keys in registration order. Each key is also the Dockerfile suffix and SFN discriminator. */
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
 * The Stability AI image-editing models the Blender agent's image-edit broker invokes, as the
 * inference profiles they run under. Mirrors `OPERATIONS` in
 * backendPipelines/genAi/imageTo3d/container/agent_broker.py; the job role is granted exactly these
 * when `useStabilityImageEditing` is on.
 */
export const BLENDERAGENT_STABILITY_INFERENCE_PROFILES = [
    "us.stability.stable-image-inpaint-v1:0",
    "us.stability.stable-image-control-structure-v1:0",
    "us.stability.stable-style-transfer-v1:0",
    "us.stability.stable-image-search-recolor-v1:0",
    "us.stability.stable-image-search-replace-v1:0",
    "us.stability.stable-image-erase-object-v1:0",
] as const;

/** The foundation-model id underneath a cross-Region inference-profile prefix. */
function foundationModelId(modelId: string): string {
    return modelId.replace(/^(global|us-gov|us|eu|apac)\./, "");
}

/**
 * GPU memory, in GiB, a model needs to complete its run, for the models that need more than the
 * general instance list provides. AWS Batch places a job on vCPU, container memory and GPU COUNT and
 * has no way to be asked for GPU memory, so a model listed here runs on a second compute environment
 * restricted to instance types whose card is large enough.
 *
 * Measured rather than assumed: Step1X-3D with its texture stage on was killed by
 * `torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 3.94 GiB. GPU 0 has a total capacity
 * of 22.03 GiB of which 1.66 GiB is free` after landing on a g6.8xlarge, whose L4 carries 22 GiB.
 * Upstream's own table puts geometry plus texture at 27 GB. Its geometry-only runs finish comfortably
 * on the same card, so only the textured configuration needs this -- and a run cannot be routed by the
 * settings it was given, so the whole model goes to the larger fleet.
 */
const MODEL_MIN_GPU_MEMORY_GIB: Record<string, number> = {
    // EMPTY DELIBERATELY, with the mechanism kept. Step1X-3D's textured configuration belongs here on its
    // memory needs -- upstream puts geometry plus texture at 27 GB and a run died with 22.03 GiB visible --
    // but routing it to a g6e-only fleet did not work in practice: every attempt returned
    // InsufficientInstanceCapacity for g6e.2xlarge in us-west-2a and us-west-2b, across two compute
    // environment generations, and the job sat RUNNABLE.
    //
    // Listing the general environment second on that queue does NOT rescue it either, which is worth
    // recording because it looks like it should: AWS Batch treats insufficient capacity as a transient
    // condition on the first environment rather than as "cannot place", so it keeps waiting there instead
    // of falling through to the second.
    //
    // So the model runs on the general fleet and its texture stage is asked to FIT that fleet instead --
    // STEP1X3D_TEXTURE_LOW_VRAM applies slicing and component offload when the visible card is small.
    // Whether that is enough is the measurement worth having; if it is not, the answer is to put an entry
    // back here in a region or account where g6e capacity exists.
    //
    // With this map empty no second environment or queue is created, so nothing idles.
    // step1x3d: 40,
};

/**
 * Instance types for a model with a GPU memory requirement, largest card first in capability terms.
 *
 * Deliberately NOT derived from the deployment's own `instanceTypes` list. That list is an
 * availability choice -- it is wide so a run survives a capacity shortfall in any one type (7.4) --
 * whereas this is a hardware floor: every entry carries one NVIDIA L40S with 48 GiB, which is the only
 * card in the g5/g6/g6e families above the 24 GiB the general list is mostly made of. g6e.xlarge is
 * excluded on host memory rather than GPU memory: it has the same card but only 32 GiB of RAM, under
 * the 48,000 MiB the textured Step1X-3D job asks for. Several sizes are listed for the same reason the
 * general list is wide.
 */
const HIGH_GPU_MEMORY_INSTANCE_TYPES = [
    // Single-L40S sizes first, smallest to largest: these carry one 48 GiB card and enough host memory,
    // and are what the job actually needs.
    "g6e.2xlarge",
    "g6e.4xlarge",
    "g6e.8xlarge",
    "g6e.16xlarge",
    // Multi-card sizes, listed to widen PLACEMENT rather than to use more than one GPU. The job requests
    // a single GPU and gets one; the rest of the instance is idle, so these cost several times more per
    // run. They are here because the single-card sizes returned InsufficientInstanceCapacity in two
    // availability zones, and a fleet that cannot place a job is worth less than one that costs more.
    "g6e.12xlarge",
    "g6e.24xlarge",
];

/** Container memory, in MiB, for a model whose run needs more than the shared default. */
const DEFAULT_MEMORY_MIB = 26000;
const MODEL_MEMORY_MIB: Record<string, number> = {
    // Step1X-3D with its texture stage on runs two models in one job -- a 1.3B geometry model and an
    // SD-XL texture module -- and was killed by the container memory limit, not by the GPU: AWS Batch
    // reported "OutOfMemoryError: Container killed due to memory usage" at the shared 26,000 MiB while
    // its geometry-only runs finish comfortably inside it. Raising this narrows where the job can be
    // placed: the xlarge instance types in the configured list carry 32 GiB, so a request above that
    // schedules only onto the 2xlarge and larger ones.
    step1x3d: 48000,
};

/** EFS mount path on both the Batch host and inside the container. */
const EFS_MOUNT_PATH = "/mnt/efs/imageto3d-models";

/** Host/container volume name for the shared model cache. */
const EFS_VOLUME_NAME = "imageto3d-models";

export class ImageTo3dConstruct extends Construct {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: ImageTo3dConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;
        const imageTo3dConfig = props.config.app.pipelines.useImageTo3d;

        // Models the deployment opted into. Only these get a job definition and a Choice branch.
        const enabledModelKeys = MODEL_KEYS.filter(
            (modelKey) => imageTo3dConfig.models?.[modelKey]?.enabled === true
        );

        /**
         * HuggingFace Token stored in Secrets Manager
         * Batch injects the secret into the container so the token is never an env var value.
         * The secret is created EMPTY and populated at deploy time from config by a custom
         * resource that carries the token in its code asset, so the token never lands in the
         * synthesized CloudFormation template.
         */
        const hfTokenSecret = new secretsmanager.Secret(this, "ImageTo3dHfTokenSecret", {
            description: "HuggingFace API token for downloading image-to-3D model weights",
            // Imported by ARN, not passed as the key object: the grants CDK derives from
            // grantRead/grantWrite then land only on each grantee's own policy. Passing the object
            // writes those grantees into the key's resource policy, which makes the storage stack
            // that owns the key reference this pipeline stack and forms a circular dependency.
            encryptionKey: props.storageResources.encryption.kmsKey
                ? kms.Key.fromKeyArn(
                      this,
                      "HfTokenSecretKmsKeyRef",
                      props.storageResources.encryption.kmsKey.keyArn
                  )
                : undefined,
        });

        populateHuggingFaceTokenSecret(
            this,
            "ImageTo3dHfTokenSecretPopulate",
            hfTokenSecret,
            imageTo3dConfig.huggingFaceToken
        );

        NagSuppressions.addResourceSuppressions(
            hfTokenSecret,
            [
                {
                    id: "AwsSolutions-SMG4",
                    reason: "HuggingFace API token is externally managed by the user. Automatic rotation is not applicable as the token lifecycle is controlled by the HuggingFace account holder, not AWS.",
                },
            ],
            true
        );

        /**
         * Shared Image to 3D resources (provided by ImageTo3dCommonConstruct)
         */
        const modelCacheBucket = props.modelCacheBucket;
        const modelEfs = props.efsFileSystem;
        const nfsSecurityGroup = props.efsSecurityGroup;

        /**
         * IAM Policies for Batch container roles
         */
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                    const prefix = record.prefix || "/";
                    // Build the object-level resource as {bucketArn}/{prefix}*. Strip any
                    // leading slash from the prefix so the '/' separator after the bucket
                    // ARN is always present (root prefix yields {bucketArn}/*).
                    const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
                    const objectPrefix = normalizedPrefix.replace(/^\/+/, "");
                    return new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: [
                            "s3:PutObject",
                            "s3:GetObject",
                            "s3:ListBucket",
                            "s3:DeleteObject",
                            "s3:GetObjectVersion",
                        ],
                        resources: [
                            record.bucket.bucketArn,
                            `${record.bucket.bucketArn}/${objectPrefix}*`,
                        ],
                    });
                }),
                // Model weight cache bucket access
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"],
                    resources: [modelCacheBucket.bucketArn, `${modelCacheBucket.bucketArn}/*`],
                }),
            ],
        });

        const outputBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources: [
                        props.storageResources.s3.assetAuxiliaryBucket.bucketArn,
                        `${props.storageResources.s3.assetAuxiliaryBucket.bucketArn}/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    actions: ["s3:ListBucket"],
                    resources: [props.storageResources.s3.assetAuxiliaryBucket.bucketArn],
                }),
            ],
        });

        // Add KMS key permissions if provided
        if (props.storageResources.encryption.kmsKey) {
            inputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
            outputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const containerExecutionRole = new iam.Role(this, "ImageTo3dContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // The ECS agent, not the container, fetches the secret, so the read grant belongs on the
        // execution role.
        hfTokenSecret.grantRead(containerExecutionRole);

        const efsClientPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: [
                        "elasticfilesystem:ClientMount",
                        "elasticfilesystem:ClientWrite",
                        "elasticfilesystem:ClientRootAccess",
                    ],
                    resources: [modelEfs.fileSystemArn],
                }),
            ],
        });

        const containerJobRole = new iam.Role(this, "ImageTo3dContainerJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
                EfsClientPolicy: efsClientPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Grant access to any external asset bucket customer managed KMS keys so the
        // container can read/write objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(containerJobRole);

        // The Blender agent reasons with a foundation model on Amazon Bedrock, invoked from inside the
        // container on the job role through a local bridge -- no API key is minted or stored. Granted only
        // when that model is enabled, and only for the configured model plus, when image editing is on,
        // the Stability AI profiles the image-edit broker runs. A cross-Region inference profile routes to
        // the foundation model in several Regions, so the foundation-model resource names the model in
        // any Region; the profile resource is pinned to this Region and account.
        const agentModel = imageTo3dConfig.models?.blenderagent;
        if (agentModel?.enabled) {
            const invokedIds: string[] = [agentModel.bedrockModelId];
            if (agentModel.useStabilityImageEditing) {
                invokedIds.push(...BLENDERAGENT_STABILITY_INFERENCE_PROFILES);
            }
            const resources: string[] = [];
            for (const modelId of invokedIds) {
                const foundation = foundationModelId(modelId);
                resources.push(
                    `arn:${Partition()}:bedrock:*::foundation-model/${foundation}`,
                    `arn:${Partition()}:bedrock:::foundation-model/${foundation}`
                );
                if (foundation !== modelId) {
                    resources.push(
                        `arn:${Partition()}:bedrock:${region}:${account}:inference-profile/${modelId}`
                    );
                }
            }
            containerJobRole.addToPolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources: resources,
                })
            );
            NagSuppressions.addResourceSuppressions(
                containerJobRole,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "A cross-Region inference profile routes each request to the named foundation model in one of several Regions, so the foundation-model ARN names the configured model in any Region. The model ids are deployment configuration; no other model is granted.",
                        appliesTo: [
                            { regex: "/^Resource::arn:[^:]+:bedrock:\\*::foundation-model/.*$/g" },
                        ],
                    },
                ],
                true
            );
        }

        /**
         * Container images per model
         * A CodeBuild-built ECR image is used when supplied; otherwise the per-model Dockerfile is
         * built locally as a Docker image asset.
         */
        const modelImageUris: { [modelKey: string]: string } = {};
        for (const modelKey of enabledModelKeys) {
            const codeBuildImage = props.codeBuildImages?.[modelKey];
            if (codeBuildImage) {
                modelImageUris[modelKey] = codeBuildImage.imageUri;
                codeBuildImage.repository.grantPull(containerExecutionRole);
                continue;
            }

            const localImage = new DockerImageAsset(this, `ImageTo3dContainerImage-${modelKey}`, {
                directory: path.join(
                    __dirname,
                    "../../../../../../../backendPipelines/genAi/imageTo3d/container"
                ),
                file: `Dockerfile.${modelKey}`,
                platform: Platform.LINUX_AMD64,
                buildArgs: {
                    // The full repair ladder ships in the image; a run selects its rungs through the
                    // template's MESH_CLEANUP and REPAIR_BACKENDS settings.
                    ENABLE_GPL_MESH_REPAIR: "true",
                },
            });
            modelImageUris[modelKey] = localImage.imageUri;
            localImage.repository.grantPull(containerExecutionRole);
        }

        /**
         * Batch Compute Environment
         * One GPU compute environment and queue shared by every enabled model.
         */

        const instanceRole = new iam.Role(this, "BatchInstanceRole", {
            assumedBy: Service("EC2").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonEC2ContainerServiceforEC2Role"
                ),
            ],
        });

        // The instance writes its mount log to the model cache bucket. Without this grant the copy in the
        // user data below fails silently: an instance role otherwise carries no S3 access at all -- the
        // container's own bucket access belongs to the JOB role, which is a different role -- so the
        // diagnostic that exists to explain a failed mount could never be read. Narrowed to the one
        // prefix, and grantPut covers the bucket's KMS key as well as the object write.
        modelCacheBucket.grantPut(instanceRole, "mount-logs/*");

        const instanceProfile = new iam.CfnInstanceProfile(this, "BatchInstanceProfile", {
            roles: [instanceRole.roleName],
        });

        // Batch compute security group - allow outbound and NFS access to EFS
        const batchSecurityGroup = new ec2.SecurityGroup(this, "BatchSecurityGroup", {
            vpc: props.vpc,
            description:
                "Security group for Image to 3D Batch compute environment with internet access",
            allowAllOutbound: true,
        });

        // Allow NFS traffic from Batch compute SG to EFS SG
        nfsSecurityGroup.addIngressRule(
            batchSecurityGroup,
            ec2.Port.tcp(2049),
            "Allow NFS from Image to 3D Batch compute to EFS"
        );

        // Mount the shared model cache on every Batch instance, and persist the mount in fstab so a
        // reboot keeps it.
        const userData = `MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==MYBOUNDARY=="

--==MYBOUNDARY==
Content-Type: text/x-shellscript; charset="us-ascii"

#!/bin/bash
# Mount the shared model cache, and LEAVE EVIDENCE either way.
#
# This ran silently before and did not work: the mount command was handed a CDK token's debug text
# instead of a file system id, so it failed on every instance since the first deploy. Nothing reported
# it -- the filesystem recorded no client connection at all across a day of jobs -- while every run
# restored its whole cache from Amazon S3 instead, 15 s for the smallest model's weights and 477 s for
# the largest, on billed GPU time. The pipeline looked merely slow.
#
# Two mount methods are tried. The first needs amazon-efs-utils, which carries TLS in transit; the
# second is plain NFS 4.1, which needs only nfs-utils. Trying both means a package problem does not
# silently cost every run its cache.
#
# Every command here is on ONE line. A backslash-newline continuation does not survive this file: the
# script is a JavaScript template literal, where backslash-newline is a line continuation JavaScript
# removes before the string is ever written, so the shell never sees it.
exec > >(tee -a /var/log/imageto3d-efs-mount.log) 2>&1
set -x
mkdir -p ${EFS_MOUNT_PATH}

dnf install -y amazon-efs-utils || yum install -y amazon-efs-utils || echo "amazon-efs-utils unavailable; falling back to nfs4"

if mount -t efs -o tls ${modelEfs.fileSystemId}:/ ${EFS_MOUNT_PATH}; then
    echo "${modelEfs.fileSystemId}:/ ${EFS_MOUNT_PATH} efs _netdev,tls 0 0" >> /etc/fstab
else
    # AWS::URLSuffix rather than a literal amazonaws.com: the mount-target DNS
    # suffix is amazonaws.eu in EU Sovereign Cloud and amazonaws.com.cn in China,
    # and a wrong one fails this fallback silently -- which is the failure it exists
    # to prevent.
    dnf install -y nfs-utils || yum install -y nfs-utils || true
    mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport ${modelEfs.fileSystemId}.efs.${region}.${cdk.Aws.URL_SUFFIX}:/ ${EFS_MOUNT_PATH} && echo "${modelEfs.fileSystemId}.efs.${region}.${cdk.Aws.URL_SUFFIX}:/ ${EFS_MOUNT_PATH} nfs4 nfsvers=4.1,_netdev 0 0" >> /etc/fstab
fi

# Verified rather than assumed, and recorded where a container can see it: a marker inside the mount is
# only visible to a job if the mount is real and shared.
if mountpoint -q ${EFS_MOUNT_PATH}; then
    echo "MODEL CACHE MOUNTED at ${EFS_MOUNT_PATH}"
    date -u +"mounted %Y-%m-%dT%H:%M:%SZ by $(hostname)" >> ${EFS_MOUNT_PATH}/.mount-history
else
    echo "MODEL CACHE NOT MOUNTED: ${EFS_MOUNT_PATH} is local disk, so every job on this instance will restore its weights from S3 instead of reading them from the shared cache"
fi

# The log goes to the model cache bucket, and stays on the serial console besides. An instance is gone
# by the time a slow run is noticed, so a channel that outlives it is the difference between reading a
# failed mount and guessing at it. The console copy is the one that depends on nothing further -- no
# AWS CLI on the image, no egress, no agent -- and it is what finally carried the reason this mount was
# failing.
aws s3 cp /var/log/imageto3d-efs-mount.log s3://${modelCacheBucket.bucketName}/mount-logs/$(date -u +%Y%m%dT%H%M%SZ)-$(hostname).log --region ${region} || echo "could not upload the mount log"
--==MYBOUNDARY==--
`;

        const launchTemplate = new ec2.CfnLaunchTemplate(this, "BatchLaunchTemplate", {
            launchTemplateData: {
                blockDeviceMappings: [
                    {
                        deviceName: "/dev/xvda",
                        ebs: {
                            volumeSize: 200,
                            volumeType: "gp3",
                            encrypted: true,
                            deleteOnTermination: true,
                        },
                    },
                ],
                // Encoded by CloudFormation rather than here, because this string carries CDK
                // tokens -- the EFS file system id among them -- and Buffer.from() freezes a token as
                // its debug text. The deployed launch template read
                // "mount -t efs -o tls ${Token[TOKEN.6935]}:/ /mnt/efs/...", so the mount never had a
                // file system id and the shared model cache was never mounted at all: the filesystem
                // recorded no client connection across a day of jobs while every run restored its
                // weights from S3 instead, at up to 461 seconds a run. Fn.base64 emits Fn::Base64, so
                // the encoding happens after token resolution.
                userData: cdk.Fn.base64(userData),
                tagSpecifications: [
                    {
                        resourceType: "instance",
                        tags: [
                            {
                                key: "Name",
                                value: "VAMS-ImageTo3d-Batch",
                            },
                        ],
                    },
                ],
            },
        });

        const batchEnvironment = new batch.CfnComputeEnvironment(this, "ImageTo3dGpuComputeEnv", {
            // No explicit name - let CDK auto-generate to allow CloudFormation replacements
            // when instance types change (custom-named resources can't be replaced in-place)
            type: "MANAGED",
            state: "ENABLED",
            // No serviceRole, so this environment uses the Batch service-linked role
            // (AWSServiceRoleForBatch), which Batch creates on demand for a managed environment. Naming a
            // role instead is what forbids an in-place update of the launch template, instance types,
            // subnets or security groups -- Batch allows those fields to be updated "only for ... Compute
            // Environment having a Batch Service Linked Role" -- leaving replacement as the only way to
            // deliver a change to the instance start-up script. VAMS already runs the Isaac Lab, Coordinate
            // Transform and Fargate environments on the service-linked role, and AWS maintains its
            // permissions as Batch gains features, which a hand-rolled role does not track.
            // Updated in PLACE for the rest of this environment's life: Batch re-merges the launch template
            // and rolls the instances, with no new environment, no job-queue re-point and no orphan left
            // behind. That needs three things together, and all three are here -- no serviceRole above (so
            // the Batch service-linked role is used), BEST_FIT_PROGRESSIVE below, and this property.
            //
            // It is safe to ship to an existing deployment only because the environment's construct id
            // changed in the same release. That makes the upgrade a CREATE of this resource and a DELETE of
            // the old one rather than an update, so this property does not have to permit the one
            // replacement the upgrade needs -- an environment still naming a service role can be neither
            // updated in place nor left alone. Setting this without the rename would fail a v2.5 upgrade.
            replaceComputeEnvironment: false,
            computeResources: {
                type: "EC2",
                allocationStrategy: "BEST_FIT_PROGRESSIVE",
                // Each infrastructure update takes the current ECS-optimised AMI rather than staying on the one
                // that was current when this environment was first created, which matters on a GPU image
                // carrying drivers. This is also the fourth condition CloudFormation names for updating a
                // compute environment in place, alongside no serviceRole, a progressive allocation strategy and
                // replaceComputeEnvironment.
                updateToLatestImageVersion: true,
                minvCpus: 0,
                maxvCpus: imageTo3dConfig.maxVCpus,
                desiredvCpus: 0,
                instanceTypes: imageTo3dConfig.instanceTypes,
                ec2Configuration: [
                    {
                        imageType: "ECS_AL2023_NVIDIA",
                    },
                ],
                subnets: props.pipelineSubnets.map((subnet) => subnet.subnetId),
                securityGroupIds: [batchSecurityGroup.securityGroupId],
                instanceRole: instanceProfile.attrArn,
                launchTemplate: {
                    launchTemplateId: launchTemplate.ref,
                    // Pinned to the template's own latest version rather than "$Latest".
                    // Batch does not read this template when an instance launches: it MERGES it
                    // with its own bootstrap into a Batch-managed copy when the compute
                    // environment is created or updated. "$Latest" never changes, so a new
                    // template version is not a change to the compute environment, and Batch goes
                    // on handing instances a stale merge: a later template version with a correct
                    // mount command reaches no instance until the environment itself is updated.
                    version: launchTemplate.attrLatestVersionNumber,
                },
            },
        });

        const batchJobQueue = new batch.CfnJobQueue(this, "ImageTo3dBatchJobQueue", {
            // No explicit name - let CDK auto-generate to allow CloudFormation replacements
            state: "ENABLED",
            priority: 1,
            computeEnvironmentOrder: [
                {
                    order: 1,
                    computeEnvironment: batchEnvironment.ref,
                },
            ],
        });

        /**
         * A second environment and queue for models that need a larger GPU than the general instance
         * list guarantees, created only when such a model is enabled. Idle cost is nothing: minvCpus is
         * 0, so the environment holds no instances until a job is queued against it.
         */
        const modelsNeedingLargeGpu = enabledModelKeys.filter(
            (modelKey) => MODEL_MIN_GPU_MEMORY_GIB[modelKey] !== undefined
        );

        let highGpuMemoryJobQueue: batch.CfnJobQueue | undefined;
        if (modelsNeedingLargeGpu.length > 0) {
            const highGpuMemoryEnvironment = new batch.CfnComputeEnvironment(
                this,
                "ImageTo3dHighGpuMemoryEnv",
                {
                    type: "MANAGED",
                    state: "ENABLED",
                    // No serviceRole, so this environment uses the Batch service-linked role
                    // (AWSServiceRoleForBatch), which Batch creates on demand for a managed environment. Naming a
                    // role instead is what forbids an in-place update of the launch template, instance types,
                    // subnets or security groups -- Batch allows those fields to be updated "only for ... Compute
                    // Environment having a Batch Service Linked Role" -- leaving replacement as the only way to
                    // deliver a change to the instance start-up script. VAMS already runs the Isaac Lab, Coordinate
                    // Transform and Fargate environments on the service-linked role, and AWS maintains its
                    // permissions as Batch gains features, which a hand-rolled role does not track.
                    // Updated in PLACE for the rest of this environment's life: Batch re-merges the launch template
                    // and rolls the instances, with no new environment, no job-queue re-point and no orphan left
                    // behind. That needs three things together, and all three are here -- no serviceRole above (so
                    // the Batch service-linked role is used), BEST_FIT_PROGRESSIVE below, and this property.
                    //
                    // It is safe to ship to an existing deployment only because the environment's construct id
                    // changed in the same release. That makes the upgrade a CREATE of this resource and a DELETE of
                    // the old one rather than an update, so this property does not have to permit the one
                    // replacement the upgrade needs -- an environment still naming a service role can be neither
                    // updated in place nor left alone. Setting this without the rename would fail a v2.5 upgrade.
                    replaceComputeEnvironment: false,
                    computeResources: {
                        type: "EC2",
                        allocationStrategy: "BEST_FIT_PROGRESSIVE",
                        // Each infrastructure update takes the current ECS-optimised AMI rather than staying on the one
                        // that was current when this environment was first created, which matters on a GPU image
                        // carrying drivers. This is also the fourth condition CloudFormation names for updating a
                        // compute environment in place, alongside no serviceRole, a progressive allocation strategy and
                        // replaceComputeEnvironment.
                        updateToLatestImageVersion: true,
                        minvCpus: 0,
                        maxvCpus: imageTo3dConfig.maxVCpus,
                        desiredvCpus: 0,
                        instanceTypes: HIGH_GPU_MEMORY_INSTANCE_TYPES,
                        ec2Configuration: [
                            {
                                imageType: "ECS_AL2023_NVIDIA",
                            },
                        ],
                        subnets: props.pipelineSubnets.map((subnet) => subnet.subnetId),
                        securityGroupIds: [batchSecurityGroup.securityGroupId],
                        instanceRole: instanceProfile.attrArn,
                        launchTemplate: {
                            launchTemplateId: launchTemplate.ref,
                            // Pinned to the template's own latest version rather than "$Latest".
                            // Batch does not read this template when an instance launches: it MERGES it
                            // with its own bootstrap into a Batch-managed copy when the compute
                            // environment is created or updated. "$Latest" never changes, so a new
                            // template version is not a change to the compute environment, and Batch goes
                            // on handing instances a stale merge: a later template version with a correct
                            // mount command reaches no instance until the environment itself is updated.
                            version: launchTemplate.attrLatestVersionNumber,
                        },
                    },
                }
            );

            highGpuMemoryJobQueue = new batch.CfnJobQueue(
                this,
                "ImageTo3dHighGpuMemoryBatchJobQueue",
                {
                    state: "ENABLED",
                    priority: 1,
                    // The large-card environment first, then the general one as a FALLBACK. The g6e
                    // types are scarce: a first attempt to place a job here returned
                    // "InsufficientInstanceCapacity ... g6e.2xlarge" in two availability zones in a row
                    // and the job sat RUNNABLE indefinitely. Without the fallback, restricting a model
                    // to the card its heaviest configuration needs blocks every OTHER configuration of
                    // that model too -- geometry-only Step1X-3D runs perfectly well on a 22 GiB card and
                    // has no reason to wait for a 48 GiB one.
                    //
                    // The trade is explicit: a job that falls back may land on a card too small for the
                    // configuration it was given. That is reported rather than hidden -- the container
                    // catches its texture stage, keeps the geometry it already produced, and records
                    // step1x3dTextureStage as "failed" rather than "run".
                    computeEnvironmentOrder: [
                        {
                            order: 1,
                            computeEnvironment: highGpuMemoryEnvironment.ref,
                        },
                        {
                            order: 2,
                            computeEnvironment: batchEnvironment.ref,
                        },
                    ],
                }
            );
        }

        /** The queue whose fleet can run this model, by its GPU memory requirement. */
        const queueForModel = (modelKey: string): string => {
            if (MODEL_MIN_GPU_MEMORY_GIB[modelKey] !== undefined && highGpuMemoryJobQueue) {
                return highGpuMemoryJobQueue.ref;
            }
            return batchJobQueue.ref;
        };

        /**
         * Shared Lambda Functions
         */
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        /**
         * Per-model Batch job definition. They differ only by model key, image, container memory, and
         * the model key's weight-cache prefix.
         */
        const createModelJobDefinition = (modelKey: string): batch.CfnJobDefinition => {
            const containerProperties: batch.CfnJobDefinition.ContainerPropertiesProperty = {
                image: modelImageUris[modelKey],
                vcpus: 4,
                memory: MODEL_MEMORY_MIB[modelKey] ?? DEFAULT_MEMORY_MIB,
                jobRoleArn: containerJobRole.roleArn,
                executionRoleArn: containerExecutionRole.roleArn,
                // Overridden at runtime with the pipeline definition built by constructPipeline
                command: ["python", "__main__.py"],
                privileged: true,
                resourceRequirements: [
                    {
                        type: "GPU",
                        value: "1",
                    },
                ],
                linuxParameters: {
                    sharedMemorySize: 16384,
                    devices: [
                        {
                            hostPath: "/dev/nvidia0",
                            containerPath: "/dev/nvidia0",
                            permissions: ["READ", "WRITE", "MKNOD"],
                        },
                        {
                            hostPath: "/dev/nvidiactl",
                            containerPath: "/dev/nvidiactl",
                            permissions: ["READ", "WRITE", "MKNOD"],
                        },
                        {
                            hostPath: "/dev/nvidia-uvm",
                            containerPath: "/dev/nvidia-uvm",
                            permissions: ["READ", "WRITE", "MKNOD"],
                        },
                    ],
                },
                environment: [
                    { name: "MODEL", value: modelKey },
                    { name: "AWS_REGION", value: region },
                    { name: "S3_MODEL_BUCKET", value: modelCacheBucket.bucketName },
                    { name: "S3_MODEL_PREFIX", value: `imageTo3d/hf_cache/${modelKey}` },
                    // The Bedrock model the Blender agent reasons with. Present on every job definition so
                    // the container has one source for it; only the agent reads it.
                    {
                        name: "BLENDERAGENT_BEDROCK_MODEL_ID",
                        value: imageTo3dConfig.models?.blenderagent?.bedrockModelId ?? "",
                    },
                    // Whether the agent's image-edit broker may invoke the Stability AI models; the job
                    // role carries their grant only when it may.
                    {
                        name: "BLENDERAGENT_IMAGE_EDITING",
                        value: imageTo3dConfig.models?.blenderagent?.useStabilityImageEditing
                            ? "true"
                            : "false",
                    },
                    {
                        name: "LD_LIBRARY_PATH",
                        value: "/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
                    },
                    {
                        name: "NVIDIA_DRIVER_CAPABILITIES",
                        value: "compute,utility,graphics",
                    },
                ],
                secrets: [
                    {
                        name: "HF_TOKEN",
                        valueFrom: hfTokenSecret.secretArn,
                    },
                ],
                mountPoints: [
                    {
                        sourceVolume: EFS_VOLUME_NAME,
                        containerPath: EFS_MOUNT_PATH,
                        readOnly: false,
                    },
                    {
                        sourceVolume: "shm",
                        containerPath: "/dev/shm",
                        readOnly: false,
                    },
                ],
                volumes: [
                    {
                        name: EFS_VOLUME_NAME,
                        host: {
                            sourcePath: EFS_MOUNT_PATH,
                        },
                    },
                    {
                        name: "shm",
                        host: {
                            sourcePath: "/dev/shm",
                        },
                    },
                ],
                ulimits: [
                    {
                        name: "memlock",
                        softLimit: -1,
                        hardLimit: -1,
                    },
                    {
                        name: "stack",
                        softLimit: 67108864,
                        hardLimit: 67108864,
                    },
                ],
            };

            return new batch.CfnJobDefinition(this, `BatchJobDef-${modelKey}`, {
                // No explicit name - let CDK auto-generate to allow CloudFormation replacements
                type: "container",
                containerProperties,
                retryStrategy: {
                    attempts: 1,
                },
                timeout: {
                    attemptDurationSeconds: 7200,
                },
            });
        };

        /**
         * SFN States
         */
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            outputPath: "$.Payload",
        });

        const successState = new sfn.Succeed(this, "SuccessState", {
            comment: "Image to 3D pipeline returned SUCCESS",
        });

        const failState = new sfn.Fail(this, "FailState", {
            causePath: sfn.JsonPath.stringAt("$.error.Cause"),
            errorPath: sfn.JsonPath.stringAt("$.error.Error"),
        });

        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(sfn.Condition.isPresent("$.error"), failState)
            .otherwise(successState);

        // pipelineEnd is the single owner of the outer workflow callback, so both the success and
        // the failure path route through it.
        const pipeLineEndTask = new tasks.LambdaInvoke(this, "PipelineEndTask", {
            lambdaFunction: pipelineEndFunction,
            inputPath: "$",
            outputPath: "$.Payload",
        }).next(endStatesChoice);

        const handleBatchError = new sfn.Pass(this, "HandleBatchError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        // error handler passthrough - Construct Pipeline Lambda.
        //
        // Without it a failure in the FIRST state ends the execution before PipelineEndTask runs,
        // and PipelineEndTask is the only state that resolves the parent workflow's task token.
        const handleConstructPipelineError = new sfn.Pass(this, "HandleConstructPipelineError", {
            resultPath: "$",
        }).next(pipeLineEndTask);
        constructPipelineTask.addCatch(handleConstructPipelineError, {
            resultPath: "$.error",
        });

        // A model the deployment did not enable (licence-blocked or simply off) fails fast rather
        // than falling through to another model's job definition, and still reaches PipelineEndTask
        // so the parent workflow's task token is released.
        const modelNotEnabled = new sfn.Pass(this, "ModelNotEnabled", {
            result: sfn.Result.fromObject({
                Error: "ModelNotEnabled",
                Cause: "The selected Image to 3D model is not enabled for this deployment. Enable it under app.pipelines.useImageTo3d.models.",
            }),
            resultPath: "$.error",
        }).next(pipeLineEndTask);

        // The job definition name of each model's Batch state, registered by openPipeline as that
        // state's container log source (streams are `<jobDefinitionName>/default/<task-id>`).
        const batchJobDefinitionNames: { [modelKey: string]: string } = {};

        let modelSelection = new sfn.Choice(this, "ModelSelection");
        for (const modelKey of enabledModelKeys) {
            const jobDefinition = createModelJobDefinition(modelKey);
            batchJobDefinitionNames[modelKey] = jobDefinitionNameFromRef(jobDefinition.ref);

            const batchJob = new tasks.BatchSubmitJob(this, `ImageTo3dBatchJob-${modelKey}`, {
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                jobDefinitionArn: jobDefinition.attrJobDefinitionArn,
                jobQueueArn: queueForModel(modelKey),
                containerOverrides: {
                    command: [...sfn.JsonPath.listAt("$.definition")],
                    environment: {
                        AWS_REGION: region,
                        S3_MODEL_BUCKET: modelCacheBucket.bucketName,
                    },
                },
                integrationPattern: sfn.IntegrationPattern.RUN_JOB,
                resultPath: "$.batchResult",
            })
                .addCatch(handleBatchError, {
                    resultPath: "$.error",
                })
                .next(pipeLineEndTask);

            modelSelection = modelSelection.when(
                sfn.Condition.stringEquals("$.modelKey", modelKey),
                batchJob
            );
        }
        modelSelection = modelSelection.otherwise(modelNotEnabled);

        const sfnPipelineDefinition = sfn.Chain.start(constructPipelineTask.next(modelSelection));

        /**
         * CloudWatch Log Group
         */
        const stateMachineLogGroup = new logs.LogGroup(this, "ImageTo3d-StateMachineLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSstateMachine-ImageTo3dPipeline" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "ImageTo3d-StateMachineLogGroup",
                    10
                ),
            // Encrypted with the shared VAMS CMK when the deployment enables one; undefined leaves the
            // CloudWatch Logs AWS-managed key. The key policy already admits the Logs service principal.
            encryptionKey: props.storageResources.encryption.kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: RemovalPolicy.DESTROY,
        });

        /**
         * SFN State Machine
         */
        const pipelineStateMachine = new sfn.StateMachine(this, "ImageTo3d-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(sfnPipelineDefinition),
            // Envelopes the 2h Batch attempt so pipelineEnd always runs and closes the token; the
            // pipeline's own 4h task timeout in turn envelopes this.
            timeout: Duration.hours(3),
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        // Step Functions cancels the submitted Batch job when the execution stops, which needs
        // TerminateJob. Without it an abort leaves the GPU job running to its attempt limit and the
        // execution reports stopped while the compute is still billing. DescribeJobs is granted
        // alongside it because the `.sync` integration reads the job's terminal state. Batch job ids
        // are runtime-generated with no name pattern to scope on, so both actions take a wildcard
        // resource. Matches the NVIDIA GPU pipelines.
        pipelineStateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["batch:DescribeJobs", "batch:TerminateJob"],
                resources: ["*"],
            })
        );

        /**
         * Lambda Resources
         */
        const allowedInputFileExtensions = ".png,.jpg,.jpeg";
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            pipelineStateMachine,
            allowedInputFileExtensions,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup,
            batchJobDefinitionNames,
            props.storageResources.encryption.kmsKey
        );

        const vamsExecuteFunction = buildVamsExecuteImageTo3dPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            openPipelineFunction,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.storageResources.encryption.kmsKey
        );

        /**
         * Auto-Registration with VAMS (vamsSchema bundle -> V2 pipeline/workflow/template tables).
         * One bundle carries all four model templates; the template pins the model.
         */
        if (imageTo3dConfig.autoRegisterWithVAMS === true) {
            const imageTo3dRegistration = new VamsSchemaRegistration(
                this,
                "ImageTo3dRegistration",
                {
                    importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                    artefactsBucket: props.storageResources.s3.artefactsBucket,
                    vamsSchemaDir: path.join(
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
                        "imageTo3d",
                        "vamsSchema"
                    ),
                    resourceOverrides: {
                        lambdaName: vamsExecuteFunction.functionName,
                    },
                    idOverrides: {
                        pipelineId: "genai-image-to-3d",
                        workflowId: "genai-image-to-3d",
                    },
                    triggerEnabled:
                        (imageTo3dConfig.autoTriggerOnFileExtensionsUpload || "") !== "",
                }
            );

            /**
             * The Blender agent's refine mode as a second pipeline on the same Lambda and images, so
             * a workflow can run a reconstruction model and then the agent: a workflow may not list one
             * pipeline twice, and a later step receives only the prior step's outputs.
             */
            if (imageTo3dConfig.models.blenderagent.enabled === true) {
                const imageTo3dRefineRegistration = new VamsSchemaRegistration(
                    this,
                    "ImageTo3dRefineRegistration",
                    {
                        importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                        artefactsBucket: props.storageResources.s3.artefactsBucket,
                        vamsSchemaDir: path.join(
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
                            "imageTo3d",
                            "vamsSchema-refine"
                        ),
                        resourceOverrides: {
                            lambdaName: vamsExecuteFunction.functionName,
                        },
                        idOverrides: {
                            pipelineId: "genai-image-to-3d-refine",
                            workflowId: "genai-image-to-3d-refine",
                        },
                        triggerEnabled: false,
                    }
                );

                // The refine workflow lists the base pipeline, so its import must run after that
                // pipeline is written to the table; on a cold table the two registrations otherwise race.
                imageTo3dRefineRegistration.node.addDependency(imageTo3dRegistration);
            }
        }

        new CfnOutput(this, "ImageTo3dLambdaExecutionFunctionName", {
            value: vamsExecuteFunction.functionName,
            description: "The Image to 3D Pipeline Lambda Function Name to use in a VAMS Pipeline",
        });
        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        /**
         * CDK Nag Suppressions
         */
        const reason =
            "Intended Solution. The Image to 3D pipeline lambda functions need appropriate access to S3 for reading asset files and model data.";

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*openPipeline/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*ImageTo3d.*StateMachine/Role/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*pipelineEnd/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*vamsExecuteImageTo3dPipeline/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerExecutionRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in asset buckets and the model weight cache bucket for image-to-3D generation",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in asset buckets, the model weight cache, and EFS for shared model weights",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            instanceRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The ECS Instance Role for EC2 Batch Compute Environment requires AmazonEC2ContainerServiceforEC2Role managed policy",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Writing the instance's mount log to the model cache bucket requires the bucket's KMS key, and AWS KMS names these two operations only in their wildcard form: kms:GenerateDataKey* covers GenerateDataKey and GenerateDataKeyWithoutPlaintext, and kms:ReEncrypt* covers ReEncryptFrom and ReEncryptTo. Both are added by the CDK grant helper and neither has a non-wildcard equivalent to name instead. The instance writes one timestamped, per-host log object per boot under the fixed mount-logs/ prefix, so the object-key wildcard is confined to those log objects, and the grant reaches only that one bucket and its own key.",
                    appliesTo: [
                        "Action::kms:GenerateDataKey*",
                        "Action::kms:ReEncrypt*",
                        { regex: "/^Resource::<.*ModelCacheBucket.*mount-logs.*$/g" },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/ImageTo3d-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "AWS Batch generates a job id at submit time, so the job this state machine describes and terminates cannot be named at synthesis.",
                    appliesTo: [
                        "Resource::*",
                        "Action::kms:GenerateDataKey*",
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:batch:(?:[a-z0-9-]+|<AWS::Region>):(?:\\d{12}|<AWS::AccountId>):job-definition\\/\\*$/g",
                        },
                        {
                            regex: "/^Resource::<.*Function.*.Arn>:.*$/g",
                        },
                        {
                            regex: "/^Action::s3:.*$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/constructPipeline/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "constructPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "constructPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/pipelineEnd/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "pipelineEnd requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "pipelineEnd uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );
    }
}
