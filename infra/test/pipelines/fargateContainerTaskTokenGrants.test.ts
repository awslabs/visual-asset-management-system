/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Fargate pipeline containers hold the parent workflow's task token for the whole PDAL / Potree
 * run and heartbeat it from `pipelines/core.py` so the parent task's heartbeat timeout is not hit
 * while a large point cloud converts. `send_external_task_heartbeat` swallows errors, so a missing
 * `states:SendTaskHeartbeat` grant never fails a job — it only leaves an AccessDeniedException in
 * every job log and lets the heartbeat go unsent. This locks the three task-token actions onto the
 * container job role's inline policy, scoped to the deployment account and region like the state
 * machine grants next to it.
 *
 * The 3D preview thumbnail construct has carried all three actions throughout and is the positive
 * control that the inline-policy search finds them where they are known to exist.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { Preview3dThumbnailConstruct } from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { PcPotreeViewerConstruct } from "../../lib/nestedStacks/pipelines/preview/pcPotreeViewer/constructs/pcPotreeViewer-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const TASK_TOKEN_ACTIONS = [
    "states:SendTaskSuccess",
    "states:SendTaskFailure",
    "states:SendTaskHeartbeat",
];

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

/**
 * The statements of the one IAM role whose logical id contains `roleIdFragment`, flattened
 * across its inline policies. The container roles carry their grants inline (not as a separate
 * AWS::IAM::Policy), so the role resource itself is where they live.
 */
const inlineStatementsOfRole = (template: Template, roleIdFragment: string): any[] => {
    const roles = Object.entries(template.findResources("AWS::IAM::Role")).filter(([logicalId]) =>
        logicalId.includes(roleIdFragment)
    );
    expect(roles).toHaveLength(1);
    const policies: any[] = (roles[0][1] as any).Properties.Policies ?? [];
    return policies.flatMap((p) => p.PolicyDocument.Statement);
};

describe.each([
    [
        "Potree point cloud viewer",
        "PcPotreeViewerHeartbeatStack",
        "PcPotreeViewerContainerJobRole",
        (c: Config.Config) => {
            c.app.pipelines.usePreviewPcPotreeViewer.enabled = true;
            c.app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS = false;
        },
        (h: Harness) =>
            Template.fromStack(
                new PcPotreeViewerConstruct(h.stack, "PcPotreeViewerPipeline", commonProps(h))
            ),
    ],
    [
        // Positive control: has carried all three actions throughout.
        "3D preview thumbnail",
        "Preview3dThumbnailHeartbeatStack",
        "Preview3dThumbnailContainerJobRole",
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
])("%s container job role", (name, stackId, roleIdFragment, mutate, build) => {
    let statements: any[];

    beforeAll(() => {
        statements = inlineStatementsOfRole(build(makeHarness(stackId, mutate)), roleIdFragment);
    });

    test("can report success, failure and a heartbeat on the workflow task token", () => {
        const actions = statements.flatMap(actionsOf);
        for (const action of TASK_TOKEN_ACTIONS) {
            expect(actions).toContain(action);
        }
    });

    test("the task-token grant is one statement scoped to the deployment account and region", () => {
        const grants = statements.filter((s) => actionsOf(s).includes("states:SendTaskHeartbeat"));
        expect(grants).toHaveLength(1);
        expect(actionsOf(grants[0]).sort()).toEqual([...TASK_TOKEN_ACTIONS].sort());
        expect(resourcesOf(grants[0])).toEqual([`arn:aws:states:${REGION}:${ACCOUNT}:*`]);
    });
});
