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

import * as fs from "fs";
import * as path from "path";
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
import { SplatToolboxConstruct } from "../../lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/splatToolbox-construct";
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

/**
 * The ASL document as an object, with CloudFormation tokens replaced so it parses as JSON.
 *
 * Needed because the execution timeout and a task timeout BOTH render as `TimeoutSeconds` — the
 * execution's at the ASL top level, the task's inside its state. A regex taking the first or the largest
 * match cannot tell them apart, and gets it exactly wrong when the two are equal, which is the case
 * worth asserting. (Measured: a first draft of the Isaac Lab assertion below passed with the defect
 * reintroduced, because filtering the matches by value removed both equal timeouts and left the
 * comparison loop with nothing to iterate.)
 */
const parseAsl = (properties: any): any => {
    const definition = properties.DefinitionString;
    if (typeof definition === "string") {
        return JSON.parse(definition);
    }
    const [separator, parts] = definition["Fn::Join"] as [string, any[]];
    return JSON.parse(parts.map((p) => (typeof p === "string" ? p : "CFN_TOKEN")).join(separator));
};

/** Every `TimeoutSeconds` declared by an individual state, keyed by state name. */
const stateTimeouts = (asl: any): Record<string, number> => {
    const out: Record<string, number> = {};
    for (const [name, state] of Object.entries(asl.States ?? {})) {
        const seconds = (state as any).TimeoutSeconds;
        if (typeof seconds === "number") {
            out[name] = seconds;
        }
    }
    return out;
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

    test("state machine timeout envelopes both the task timeout and the Batch attempt duration", () => {
        // The assertion the Splat Toolbox block already had and this one did not. Isaac Lab's task is
        // not the first state — openPipelineState runs before it — so an execution timeout EQUAL to the
        // task timeout makes the task-level States.Timeout unreachable: the execution-level one fires
        // first and bypasses every Catch, so the error handler writes no record and closePipelineState
        // never runs.
        const { properties } = getStateMachine(template);
        const asl = parseAsl(properties);

        // The ASL top-level TimeoutSeconds is the EXECUTION timeout, read by key rather than by
        // position or magnitude so it cannot be confused with a state's own.
        const executionSeconds = asl.TimeoutSeconds;
        expect(typeof executionSeconds).toBe("number");

        const perState = stateTimeouts(asl);
        // The control: at least one state declares its own timeout, or "envelopes the task timeout"
        // holds for a definition that has no task timeout to envelope.
        expect(Object.keys(perState).length).toBeGreaterThan(0);

        const jobDefinitions = Object.values(
            template.findResources("AWS::Batch::JobDefinition")
        ) as any[];
        expect(jobDefinitions).toHaveLength(1);
        const attemptSeconds = jobDefinitions[0].Properties.Timeout.AttemptDurationSeconds;

        // Strictly greater at every level: Batch attempt < task < execution. Reported as a list so a
        // failure names the offending state rather than just two numbers.
        expect(executionSeconds).toBeGreaterThan(attemptSeconds);
        const notEnveloped = Object.entries(perState)
            .filter(([, seconds]) => seconds >= executionSeconds)
            .map(([name, seconds]) => `${name}=${seconds}s vs execution=${executionSeconds}s`);
        expect(notEnveloped).toEqual([]);
    });

    test("the task heartbeat tolerance stays below the parent pipeline's, in both bundles", () => {
        // Both clocks are starved by the same silence — nothing heartbeats while the Batch job waits
        // for GPU capacity or pulls its image. Whichever expires FIRST decides the outcome, and it has
        // to be this one: only the internal task's Catch terminates the Batch job and reports a
        // diagnosable failure. If the parent won the race the job would keep running and billing.
        const { properties } = getStateMachine(template);
        const asl = parseAsl(properties);
        const heartbeats = Object.values(asl.States ?? {})
            .map((state: any) => state.HeartbeatSeconds)
            .filter((seconds): seconds is number => typeof seconds === "number");
        // The control: no heartbeat at all would make the comparison below vacuous.
        expect(heartbeats).toHaveLength(1);
        const internalSeconds = heartbeats[0];

        const schemaDir = path.resolve(
            __dirname,
            "..",
            "../../backendPipelines/simulation/isaacLabTraining/vamsSchema"
        );
        const bundles = fs
            .readdirSync(schemaDir, { withFileTypes: true })
            .filter((entry) => entry.isDirectory())
            .map((entry) => path.join(schemaDir, entry.name, "pipeline.json"))
            .filter((p) => fs.existsSync(p));
        // The control: an empty listing would make the loop below assert nothing. Isaac Lab ships two
        // bundles (training, evaluation) with DIFFERENT parent tolerances, which is the whole reason a
        // single internal value has to sit below the smaller of them.
        expect(bundles.length).toBeGreaterThan(1);

        for (const bundle of bundles) {
            const executionConfig = JSON.parse(fs.readFileSync(bundle, "utf-8")).executionConfig;
            const parentSeconds = Number(executionConfig.taskHeartbeatTimeout);
            expect(parentSeconds).toBeGreaterThan(0);
            expect(internalSeconds).toBeLessThan(parentSeconds);
        }
    });
});
