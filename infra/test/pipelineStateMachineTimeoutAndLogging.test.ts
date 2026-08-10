/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Locks in two properties of the GPU pipeline state machines that fail silently at runtime.
 *
 *   1. Splat Toolbox: the state machine timeout must ENVELOPE the Batch attempt duration. The
 *      container — not the state machine — sends the VAMS task-token callback, so a state machine
 *      killed by States.Timeout before its Batch job ends leaves the parent workflow task RUNNING
 *      until its own taskTimeout. Its role must also be able to terminate the .sync Batch job.
 *   2. Isaac Lab: its vamsExecute lambda gates the `logs` entry of its sub-execution registration
 *      on STATE_MACHINE_LOG_GROUP_NAME/_ARN, so without a log group and those env vars the
 *      execution log viewer is empty with no error.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import * as s3AssetBuckets from "../lib/helper/s3AssetBuckets";
import { storageResources } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { SplatToolboxConstruct } from "../lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/splatToolbox-construct";
import { IsaacLabTrainingConstruct } from "../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import commercialTemplate from "../config/config.template.commercial.json";

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
    config.app.pipelines.useSplatToolbox.autoRegisterWithVAMS = false;
    config.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = false;
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
    /** Imported repository so the constructs skip a local Docker build of the GPU image. */
    codeBuildRepository: ecr.IRepository;
}

const makeHarness = (id: string): Harness => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = new cdk.App();
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
        codeBuildRepository: ecr.Repository.fromRepositoryName(stack, "Repo", "vams-test-repo"),
    };
};

/** The single state machine in the template, with its ASL parsed out of the Fn::Join parts. */
const getStateMachine = (template: Template): { properties: any; definition: string } => {
    const machines = Object.values(template.findResources("AWS::StepFunctions::StateMachine"));
    expect(machines).toHaveLength(1);
    const properties = (machines[0] as any).Properties;
    return { properties, definition: JSON.stringify(properties.DefinitionString) };
};

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

/** Every action granted to the named role across the policies in the template. */
const roleActions = (template: Template, roleIdFragment: string): string[] => {
    const policies = template.findResources("AWS::IAM::Policy");
    return Object.entries(policies)
        .filter(([logicalId]) => logicalId.includes(roleIdFragment))
        .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement)
        .flatMap(actionsOf);
};

describe("Splat Toolbox processing state machine", () => {
    let template: Template;

    beforeAll(() => {
        const h = makeHarness("SplatTestStack");
        // The constructor syncs the pinned upstream container sources over the network. Mark the
        // pinned commit as already synced so the test exercises only the CDK resources.
        (SplatToolboxConstruct as any).syncedCommit = SplatToolboxConstruct.GITHUB_REPO_COMMIT_HASH;

        new SplatToolboxConstruct(h.stack, "SplatToolboxPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
            codeBuildRepository: h.codeBuildRepository,
        });
        template = Template.fromStack(h.stack);
    });

    test("state machine timeout envelopes the Batch attempt duration", () => {
        const jobDefinitions = Object.values(
            template.findResources("AWS::Batch::JobDefinition")
        ) as any[];
        expect(jobDefinitions).toHaveLength(1);
        const attemptSeconds = jobDefinitions[0].Properties.Timeout.AttemptDurationSeconds;

        const { definition } = getStateMachine(template);
        const timeout = /TimeoutSeconds\\":(\d+)/.exec(definition);
        expect(timeout).not.toBeNull();

        // Strictly greater: the container sends the task-token callback, so the state machine must
        // outlive the job rather than being cut short by States.Timeout.
        expect(Number(timeout![1])).toBeGreaterThan(attemptSeconds);
    });

    test("state machine role can terminate the .sync Batch job it submitted", () => {
        const actions = roleActions(template, "StateMachineRoleDefaultPolicy");
        expect(actions).toContain("batch:SubmitJob");
        expect(actions).toContain("batch:TerminateJob");
        expect(actions).toContain("batch:DescribeJobs");
    });
});

describe("Isaac Lab training state machine", () => {
    let template: Template;

    beforeAll(() => {
        const h = makeHarness("IsaacLabTestStack");
        new IsaacLabTrainingConstruct(h.stack, "IsaacLabTrainingConstruct", {
            config: h.config,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSubnetsIsolated: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            storageResources: h.storage,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
            codeBuildRepository: h.codeBuildRepository,
        });
        template = Template.fromStack(h.stack);
    });

    test("logs ALL execution data to its own vended log group, with tracing on", () => {
        const { properties } = getStateMachine(template);
        expect(properties.LoggingConfiguration.Level).toEqual("ALL");
        expect(properties.LoggingConfiguration.IncludeExecutionData).toBe(true);
        expect(properties.TracingConfiguration).toEqual({ Enabled: true });

        const logGroupLogicalId =
            properties.LoggingConfiguration.Destinations[0].CloudWatchLogsLogGroup.LogGroupArn[
                "Fn::GetAtt"
            ][0];
        const logGroup = template.findResources("AWS::Logs::LogGroup")[logGroupLogicalId];
        expect(logGroup).toBeDefined();
        expect(logGroup.Properties.LogGroupName).toMatch(
            /^\/aws\/vendedlogs\/VAMSstateMachine-IsaacLab/
        );
    });

    test("vamsExecute lambda receives the log group name and ARN it registers", () => {
        const functions = Object.entries(template.findResources("AWS::Lambda::Function")).filter(
            ([logicalId]) => logicalId.includes("VamsExecuteFunction")
        );
        expect(functions).toHaveLength(1);
        const variables = (functions[0][1] as any).Properties.Environment.Variables;

        // The lambda gates detail["logs"] on both being non-empty.
        expect(variables.STATE_MACHINE_LOG_GROUP_NAME).toBeDefined();
        expect(variables.STATE_MACHINE_LOG_GROUP_ARN).toBeDefined();
        expect(variables.ORCHESTRATION_BUS_NAME).toBeDefined();
    });
});
