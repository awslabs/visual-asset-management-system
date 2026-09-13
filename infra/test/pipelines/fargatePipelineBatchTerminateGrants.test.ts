/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every state machine that submits a Batch job with `.sync` integration can also cancel it.
 *
 * `BatchSubmitJob` grants the state machine role `batch:SubmitJob` only. When an execution is stopped
 * — the parent workflow's StopExecution, a timeout, a Catch — Step Functions cancels the `.sync` task
 * by terminating the job it submitted, which needs `batch:DescribeJobs` (no resource type) and
 * `batch:TerminateJob` on the account's jobs. Without them the state machine stops and the Fargate
 * container keeps running, and billing, until its attempt duration ends.
 *
 * The three Fargate pipelines that submit through `.sync` are asserted alongside the Splat Toolbox
 * GPU pipeline, which is the positive control: it has carried both grants throughout, so the
 * assertion is known to find them where they exist.
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
import { Metadata3dLabelingConstruct } from "../../lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/metadata3dLabeling-construct";
import { Preview3dThumbnailConstruct } from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { PcPotreeViewerConstruct } from "../../lib/nestedStacks/pipelines/preview/pcPotreeViewer/constructs/pcPotreeViewer-construct";
import { SplatToolboxConstruct } from "../../lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/splatToolbox-construct";
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
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

interface Harness {
    stack: cdk.Stack;
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: lambda.LayerVersion;
    storage: storageResources;
    /** Imported image so the GPU construct skips a local Docker build. */
    codeBuildImage: { repository: ecr.IRepository; tag: string };
}

const makeHarness = (id: string, mutate: (c: Config.Config) => void): Harness => {
    const config = createMockConfig();
    mutate(config);
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, id, { env: { account: ACCOUNT, region: REGION } });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];

    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);

    const storage = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    return {
        stack,
        config,
        vpc,
        subnets: vpc.privateSubnets,
        securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        storage,
        codeBuildImage: {
            repository: ecr.Repository.fromRepositoryName(stack, "Repo", "vams-test-repo"),
            tag: "0123456789abcdef0123456789abcdef01234567",
        },
    };
};

const commonProps = (h: Harness) => ({
    config: h.config,
    storageResources: h.storage,
    vpc: h.vpc,
    pipelineSubnets: h.subnets,
    pipelineSecurityGroups: h.securityGroups,
    lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
    importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
});

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

const resourcesOf = (statement: any): string[] =>
    Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource];

/** The statements of the one state machine role default policy in the template. */
const stateMachineRoleStatements = (template: Template): any[] => {
    const policies = Object.entries(template.findResources("AWS::IAM::Policy")).filter(
        ([logicalId]) => logicalId.includes("StateMachineRoleDefaultPolicy")
    );
    expect(policies).toHaveLength(1);
    return (policies[0][1] as any).Properties.PolicyDocument.Statement;
};

describe.each([
    [
        "GenAI 3D metadata labeling",
        "Metadata3dLabelingTerminateStack",
        (c: Config.Config) => {
            c.app.pipelines.useGenAiMetadata3dLabeling.enabled = true;
            c.app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS = false;
        },
        // The construct IS a NestedStack, so its resources are in its own template.
        (h: Harness) =>
            Template.fromStack(
                new Metadata3dLabelingConstruct(
                    h.stack,
                    "Metadata3dLabelingPipeline",
                    commonProps(h)
                )
            ),
    ],
    [
        "3D preview thumbnail",
        "Preview3dThumbnailTerminateStack",
        (c: Config.Config) => {
            c.app.pipelines.usePreview3dThumbnail.enabled = true;
            c.app.pipelines.usePreview3dThumbnail.autoRegisterWithVAMS = false;
        },
        (h: Harness) =>
            Template.fromStack(
                new Preview3dThumbnailConstruct(
                    h.stack,
                    "Preview3dThumbnailPipeline",
                    commonProps(h)
                )
            ),
    ],
    [
        // PDAL and Potree are two .sync submissions on one state machine.
        "Potree point cloud viewer",
        "PcPotreeViewerTerminateStack",
        (c: Config.Config) => {
            c.app.pipelines.usePreviewPcPotreeViewer.enabled = true;
            c.app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS = false;
        },
        (h: Harness) =>
            Template.fromStack(
                new PcPotreeViewerConstruct(h.stack, "PcPotreeViewerPipeline", commonProps(h))
            ),
    ],
])("%s state machine role", (name, stackId, mutate, build) => {
    let statements: any[];

    beforeAll(() => {
        statements = stateMachineRoleStatements(build(makeHarness(stackId, mutate)));
    });

    test("[control] submits its Batch job through the .sync integration", () => {
        // A role that submits nothing has nothing to terminate, so the assertions below would hold
        // vacuously; the submit grant proves the state machine is the one under test.
        expect(statements.flatMap(actionsOf)).toContain("batch:SubmitJob");
    });

    test("can describe the job it submitted", () => {
        expect(statements.flatMap(actionsOf)).toContain("batch:DescribeJobs");
    });

    test("can terminate the job it submitted, scoped to this account's jobs", () => {
        const terminates = statements.filter((s) => actionsOf(s).includes("batch:TerminateJob"));
        expect(terminates).toHaveLength(1);
        expect(resourcesOf(terminates[0])).toEqual([`arn:aws:batch:${REGION}:${ACCOUNT}:job/*`]);
    });
});

describe("[control] Splat Toolbox state machine role", () => {
    // The GPU pipeline has carried both grants throughout, so this proves the statement search finds
    // them where they are known to exist.
    let statements: any[];

    beforeAll(() => {
        const h = makeHarness("SplatToolboxTerminateStack", (c) => {
            c.app.pipelines.useSplatToolbox.enabled = true;
            c.app.pipelines.useSplatToolbox.autoRegisterWithVAMS = false;
        });
        // The constructor syncs the pinned upstream container sources over the network. Mark the
        // pinned commit as already synced so only the CDK resources are exercised.
        (SplatToolboxConstruct as any).syncedCommit = SplatToolboxConstruct.GITHUB_REPO_COMMIT_HASH;
        new SplatToolboxConstruct(h.stack, "SplatToolboxPipeline", {
            ...commonProps(h),
            codeBuildImage: h.codeBuildImage,
        });
        statements = stateMachineRoleStatements(Template.fromStack(h.stack));
    });

    test("carries SubmitJob, DescribeJobs and TerminateJob", () => {
        const actions = statements.flatMap(actionsOf);
        expect(actions).toContain("batch:SubmitJob");
        expect(actions).toContain("batch:DescribeJobs");
        expect(actions).toContain("batch:TerminateJob");
    });
});
