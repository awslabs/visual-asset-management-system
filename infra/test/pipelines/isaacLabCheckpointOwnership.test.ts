/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Isaac Lab checkpoint folder ownership after an upgrade (issue #1703).
 *
 * The training container runs as uid/gid 10000 and reaches the checkpoint file system through an EFS
 * access point at /checkpoints. EFS applies the access point's createAcl only when that folder does
 * not exist yet, and a deployment that trained before the container ran as uid 10000 already has it,
 * owned by root with mode 0755. Every later training run then fails creating its run folder. Each GPU
 * instance therefore mounts the file system root and hands /checkpoints, and everything under it, to
 * 10000:10000 before a job runs, which keeps the earlier runs' checkpoints usable.
 *
 * The script only reaches an instance on an upgrade if the compute environment is pinned to the
 * launch template version that carries it: AWS Batch merges the template into its own copy only when
 * the environment is created or updated.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { IsaacLabTrainingConstruct } from "../../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.app.pipelines.useIsaacLabTraining.enabled = true;
    config.app.pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
    config.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = false;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

let template: Template;

beforeAll(() => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, "IsaacLabCheckpointOwnershipTestStack", {
        env: { account: ACCOUNT, region: REGION },
    });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const kmsKey = new kms.Key(stack, "Key");

    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(new s3.Bucket(stack, "AssetBucket"), "/", "db");

    const storage = {
        encryption: { kmsKey },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    new IsaacLabTrainingConstruct(stack, "IsaacLabTrainingPipeline", {
        config,
        vpc,
        pipelineSubnets: vpc.privateSubnets,
        pipelineSubnetsIsolated: vpc.isolatedSubnets,
        pipelineSecurityGroups: [new ec2.SecurityGroup(stack, "Sg", { vpc })],
        storageResources: storage,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflowV2",
        // Sources the container image from ECR so synth does not run a local Docker build.
        codeBuildImage: {
            repository: ecr.Repository.fromRepositoryName(stack, "Repo", "isaaclab"),
            tag: "0123456789abcdef0123456789abcdef01234567", // pragma: allowlist secret
        },
    });

    template = Template.fromStack(stack);
});

const onlyResource = (type: string): { id: string; properties: any } => {
    const resources = template.findResources(type);
    const ids = Object.keys(resources);
    expect(ids).toHaveLength(1);
    return { id: ids[0], properties: resources[ids[0]].Properties };
};

/** The user data script, with each CloudFormation intrinsic in it written as <Ref:logicalId>. */
const userDataScript = (): { script: string; refs: string[] } => {
    const userData = onlyResource("AWS::EC2::LaunchTemplate").properties.LaunchTemplateData
        .UserData;
    expect(Object.keys(userData)).toEqual(["Fn::Base64"]);
    const body = userData["Fn::Base64"];
    const parts: any[] = typeof body === "string" ? [body] : body["Fn::Join"][1];
    const refs: string[] = [];
    const script = parts
        .map((part) => {
            if (typeof part === "string") return part;
            refs.push(part.Ref);
            return `<Ref:${part.Ref}>`;
        })
        .join("");
    return { script, refs };
};

describe("Isaac Lab checkpoint folder ownership", () => {
    test("the instance mounts the training file system's root, not the access point", () => {
        const fileSystemId = onlyResource("AWS::EFS::FileSystem").id;
        const { script, refs } = userDataScript();
        expect(refs).toContain(fileSystemId);
        expect(script).toContain(
            `mount -t efs -o tls <Ref:${fileSystemId}>:/ /mnt/vams-isaaclab-efs`
        );
        expect(script).not.toContain("accesspoint");
        // A failed mount is reported, never mistaken for the folder.
        expect(script).toContain("mountpoint -q /mnt/vams-isaaclab-efs");
    });

    test("it hands /checkpoints and everything under it to the container uid and gid", () => {
        const { script } = userDataScript();
        expect(script).toContain("mkdir -p /mnt/vams-isaaclab-efs/checkpoints");
        expect(script).toContain(
            "find /mnt/vams-isaaclab-efs/checkpoints \\( ! -uid 10000 -o ! -gid 10000 \\) -print -exec chown -h 10000:10000 {} +"
        );
        // The outcome reaches the serial console, which outlives the instance.
        expect(script).toContain("| tee /dev/console");
        expect(script).toContain("umount /mnt/vams-isaaclab-efs");
    });

    test("the script is MIME multipart, as AWS Batch requires for launch template user data", () => {
        const { script } = userDataScript();
        expect(script.startsWith("MIME-Version: 1.0\nContent-Type: multipart/mixed;")).toBe(true);
        expect(script).toContain('Content-Type: text/x-shellscript; charset="us-ascii"');
        expect(script.trimEnd().endsWith("--==MYBOUNDARY==--")).toBe(true);
    });

    test("the compute environment is pinned to the template version that carries the script", () => {
        const launchTemplateId = onlyResource("AWS::EC2::LaunchTemplate").id;
        const { properties } = onlyResource("AWS::Batch::ComputeEnvironment");
        expect(properties.ComputeResources.LaunchTemplate).toEqual({
            LaunchTemplateId: { Ref: launchTemplateId },
            Version: { "Fn::GetAtt": [launchTemplateId, "LatestVersionNumber"] },
        });
    });

    test("a version change updates the compute environment in place", () => {
        // The three conditions CloudFormation needs for an in-place infrastructure update: the Batch
        // service-linked role (no ServiceRole), a progressive strategy, no replacement.
        const { properties } = onlyResource("AWS::Batch::ComputeEnvironment");
        expect(properties.ServiceRole).toBeUndefined();
        expect(properties.ComputeResources.AllocationStrategy).toBe("BEST_FIT_PROGRESSIVE");
        expect(properties.ReplaceComputeEnvironment).toBe(false);
    });

    test("the access point is unchanged", () => {
        const { properties } = onlyResource("AWS::EFS::AccessPoint");
        expect(properties.RootDirectory).toEqual({
            Path: "/checkpoints",
            CreationInfo: { OwnerUid: "10000", OwnerGid: "10000", Permissions: "755" },
        });
        expect(properties.PosixUser).toEqual({ Uid: "10000", Gid: "10000" });
    });
});
