/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The AWS Batch tier of the 3D Basic Conversion pipeline (#1732, app.pipelines.useConversion3dBasic.batchTier).
 *
 * Asserted on the synthesized construct: with the tier off nothing is added and the registration is the
 * converter Lambda's; with it on the job role has no Step Functions action and may write only the run's
 * output prefix, both images take the same STEP build argument, the state machine branches on the
 * Lambda attempt's answer, and the registration waits for the callback.
 */

import * as fs from "fs";
import * as path from "path";
import { Template, Match } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { Conversion3dBasicConstruct } from "../../lib/nestedStacks/pipelines/conversion/3dBasic/constructs/conversion3dBasic-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";
import { lambdaEnvironmentsByHandler, parseAsl, singleStateMachine } from "../support/asl";

const REPO = path.resolve(__dirname, "../../..");

function synth(id: string, tierEnabled: boolean, step = false, maxMemoryGiB = 120): Template {
    const h = makePipelineHarness(id, (c) => {
        const block = c.app.pipelines.useConversion3dBasic;
        block.enabled = true;
        block.autoRegisterWithVAMS = true;
        block.enableStepInput = step;
        block.batchTier = { enabled: tierEnabled, maxMemoryGiB };
    });
    const construct = new Conversion3dBasicConstruct(h.stack, "Conversion3dBasicPipeline", {
        config: h.config,
        storageResources: h.storage,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });
    return Template.fromStack(construct);
}

const roleStatements = (template: Template, roleLogicalIdPattern: RegExp): any[] => {
    const policies = template.findResources("AWS::IAM::Role");
    const out: any[] = [];
    for (const [id, role] of Object.entries<any>(policies)) {
        if (!roleLogicalIdPattern.test(id)) continue;
        for (const policy of role.Properties.Policies ?? []) {
            out.push(...policy.PolicyDocument.Statement);
        }
    }
    return out;
};

describe("batchTier off", () => {
    const template = synth("t1732off", false);

    test("no Batch, state machine or launcher resource is created", () => {
        template.resourceCountIs("AWS::Batch::ComputeEnvironment", 0);
        template.resourceCountIs("AWS::Batch::JobQueue", 0);
        template.resourceCountIs("AWS::Batch::JobDefinition", 0);
        template.resourceCountIs("AWS::StepFunctions::StateMachine", 0);
        expect(lambdaEnvironmentsByHandler(template, "openPipeline.lambda_handler")).toHaveLength(
            0
        );
    });

    test("the registration carries no execution config override", () => {
        const registrations = template.findResources("AWS::CloudFormation::CustomResource");
        const props = Object.values<any>(registrations).map((r) => r.Properties);
        expect(props.length).toBeGreaterThan(0);
        for (const p of props) {
            expect(p.executionConfigOverrides).toBeUndefined();
        }
    });
});

describe("batchTier on", () => {
    const template = synth("t1732on", true, true);

    test("one Fargate compute environment capped at 128 vCPUs, one queue, one job definition", () => {
        template.resourceCountIs("AWS::Batch::ComputeEnvironment", 1);
        template.hasResourceProperties("AWS::Batch::ComputeEnvironment", {
            ComputeResources: Match.objectLike({ MaxvCpus: 128, Type: "FARGATE" }),
        });
        template.resourceCountIs("AWS::Batch::JobQueue", 1);
        template.resourceCountIs("AWS::Batch::JobDefinition", 1);
    });

    test("the job definition has 200 GiB of storage, an init process and the Region", () => {
        template.hasResourceProperties("AWS::Batch::JobDefinition", {
            ContainerProperties: Match.objectLike({
                EphemeralStorage: { SizeInGiB: 200 },
                LinuxParameters: { InitProcessEnabled: true },
                Environment: Match.arrayWith([
                    Match.objectLike({ Name: "AWS_REGION" }),
                    Match.objectLike({ Name: "AWS_DEFAULT_REGION" }),
                ]),
            }),
        });
    });

    test("the job role holds no Step Functions action and writes only under the run's output prefix", () => {
        const statements = roleStatements(template, /Conversion3dBasicBatchJobRole/);
        expect(statements.length).toBeGreaterThan(0);
        const actions = statements.flatMap((s) =>
            Array.isArray(s.Action) ? s.Action : [s.Action]
        );
        expect(actions.filter((a: string) => a.startsWith("states:"))).toEqual([]);
        expect(
            actions.filter((a: string) => /^s3:(Delete|PutObjectAcl|PutBucket)/.test(a))
        ).toEqual([]);
        const putStatements = statements.filter((s) =>
            (Array.isArray(s.Action) ? s.Action : [s.Action]).includes("s3:PutObject")
        );
        expect(putStatements.length).toBeGreaterThan(0);
        for (const s of putStatements) {
            const resources = Array.isArray(s.Resource) ? s.Resource : [s.Resource];
            for (const r of resources) {
                expect(JSON.stringify(r)).toMatch(/pipelines\/\*\/output\/\*/);
            }
        }
    });

    test("the execution role carries only the managed execution policy", () => {
        expect(roleStatements(template, /Conversion3dBasicBatchExecutionRole/)).toEqual([]);
    });

    test("the registration waits for the callback for 6.5 hours", () => {
        const registrations = Object.values<any>(
            template.findResources("AWS::CloudFormation::CustomResource")
        ).map((r) => r.Properties);
        const withOverrides = registrations.filter((p) => p.executionConfigOverrides);
        expect(withOverrides).toHaveLength(1);
        expect(JSON.parse(withOverrides[0].executionConfigOverrides)).toEqual({
            waitForCallback: "Enabled",
            taskTimeout: "23400",
        });
    });

    test("openPipeline has the cap, the container log group and the job definition name", () => {
        const envs = lambdaEnvironmentsByHandler(template, "openPipeline.lambda_handler");
        expect(envs).toHaveLength(1);
        expect(envs[0].MAX_MEMORY_GIB).toBe("120");
        expect(envs[0].BATCH_JOB_DEFINITION_NAME).toBeDefined();
        expect(envs[0].BATCH_JOB_LOG_GROUP_NAME).toBeDefined();
        expect(envs[0].ALLOWED_INPUT_FILEEXTENSIONS).toContain(".stp");
    });

    test("the state machine branches on escalate and has one Batch state per job size", () => {
        const asl = parseAsl(singleStateMachine(template));
        const states = asl.States;
        expect(Object.keys(states)).toEqual(
            expect.arrayContaining([
                "ConversionLambdaAttempt",
                "EscalateChoice",
                "ConversionBatchJob",
                "ConversionBatchJob8Vcpu",
                "ConversionBatchJob16Vcpu",
                "PipelineEndTask",
            ])
        );
        expect(JSON.stringify(states.EscalateChoice)).toContain("$.attempt.Payload.escalate");
        const memories = [
            "ConversionBatchJob",
            "ConversionBatchJob8Vcpu",
            "ConversionBatchJob16Vcpu",
        ].map((name) =>
            Number(
                states[name].Parameters.ContainerOverrides.ResourceRequirements.find(
                    (r: any) => r.Type === "MEMORY"
                ).Value
            )
        );
        expect(memories).toEqual([30720, 61440, 122880]);
        for (const name of [
            "ConversionBatchJob",
            "ConversionBatchJob8Vcpu",
            "ConversionBatchJob16Vcpu",
        ]) {
            const env = JSON.stringify(states[name].Parameters.ContainerOverrides.Environment);
            expect(env).toContain("VAMS_CONVERSION_EVENT");
            expect(env).not.toContain("TASK_TOKEN");
            expect(states[name].TimeoutSeconds).toBe(5 * 3600 + 15 * 60);
            expect(states[name].Parameters.Timeout.AttemptDurationSeconds).toBe(4 * 3600 + 15 * 60);
        }
    });
});

describe("batchTier on with a lower cap", () => {
    test("maxMemoryGiB 30 builds only the 4 vCPU state", () => {
        const template = synth("t1732cap30", true, false, 30);
        const states = parseAsl(singleStateMachine(template)).States;
        expect(states.ConversionBatchJob).toBeDefined();
        expect(states.ConversionBatchJob8Vcpu).toBeUndefined();
        expect(states.ConversionBatchJob16Vcpu).toBeUndefined();
    });
});

describe("both images take the same STEP argument", () => {
    const source = fs.readFileSync(
        path.join(
            REPO,
            "infra/lib/nestedStacks/pipelines/conversion/3dBasic/constructs/conversion3dBasicBatchTier-construct.ts"
        ),
        "utf-8"
    );
    const functions = fs.readFileSync(
        path.join(
            REPO,
            "infra/lib/nestedStacks/pipelines/conversion/3dBasic/lambdaBuilder/conversion3dBasicFunctions.ts"
        ),
        "utf-8"
    );
    test("the build argument has the same name and value rule", () => {
        const rule = /ENABLE_STEP_INPUT: enableStepInput \? "true" : "false"/;
        expect(functions).toMatch(rule);
        expect(source).toMatch(rule);
        expect(source).toMatch(/enableStepInput = tierConfig\.enableStepInput === true/);
        expect(source).toContain('imageTarget: "batch"');
    });

    test("the job ladder equals the Python launcher's", () => {
        const py = fs.readFileSync(
            path.join(REPO, "backendPipelines/conversion/3dBasic/lambda/computeTier.py"),
            "utf-8"
        );
        expect(py).toContain("JOB_SIZES = ((4, 30 * GIB), (8, 60 * GIB), (16, 120 * GIB))");
        expect(
            Config.CONVERSION_3D_BASIC_BATCH_TIER_JOB_SIZES.map((s) => [s.vcpu, s.memoryGiB])
        ).toEqual([
            [4, 30],
            [8, 60],
            [16, 120],
        ]);
    });
});
