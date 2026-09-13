/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The two ECS pipelines own their container log group (`/aws/vendedlogs/Pipelines/<container>`,
 * already in the executionService read scope) and register it as the run-task state's log source.
 * The env must reference THAT group, and the stage name must be a state of the machine.
 */

import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import { ModelOpsConstruct } from "../../lib/nestedStacks/pipelines/multi/modelOps/constructs/modelOps-construct";
import { RapidPipelineConstruct } from "../../lib/nestedStacks/pipelines/multi/rapidPipeline/constructs/rapidPipeline-construct";
import {
    ACCOUNT,
    REGION,
    makePipelineHarness,
    PipelineHarness,
} from "../support/pipelineConstructHarness";
import {
    declaredStageNames,
    lambdaEnvironmentsByHandler,
    parseAsl,
    singleStateMachine,
} from "../support/asl";
import * as Config from "../../config/config";
import * as cdk from "aws-cdk-lib";

const PRODUCERS = path.resolve(__dirname, "..", "..", "..", "backendPipelines");
const IMAGE_URI = `${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/multi:latest`;

describe.each([
    [
        "rapidPipeline",
        (h: PipelineHarness): cdk.NestedStack =>
            new RapidPipelineConstruct(h.stack, "RapidPipelineConstruct", {
                config: h.config,
                storageResources: h.storage,
                vpc: h.vpc,
                pipelineSubnetsPrivate: h.subnets,
                pipelineSubnetsIsolated: h.subnets,
                pipelineSecurityGroups: h.securityGroups,
                lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
            }),
        (c: Config.Config) => {
            c.app.pipelines.useRapidPipeline.useEcs.enabled = true;
            c.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI = IMAGE_URI;
            c.app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS = false;
        },
    ],
    [
        "modelOps",
        (h: PipelineHarness): cdk.NestedStack =>
            new ModelOpsConstruct(h.stack, "ModelOpsConstruct", {
                config: h.config,
                storageResources: h.storage,
                vpc: h.vpc,
                pipelineSubnetsPrivate: h.subnets,
                pipelineSubnetsIsolated: h.subnets,
                pipelineSecurityGroups: h.securityGroups,
                lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
            }),
        (c: Config.Config) => {
            c.app.pipelines.useModelOps.enabled = true;
            c.app.pipelines.useModelOps.ecrContainerImageURI = IMAGE_URI;
            c.app.pipelines.useModelOps.autoRegisterWithVAMS = false;
        },
    ],
])("multi/%s openPipeline registration environment", (name, build, mutate) => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness(`${name}EnvStack`, mutate);
        template = Template.fromStack(build(h));
    });

    test("references the pipeline's own /aws/vendedlogs/Pipelines container log group", () => {
        const envs = lambdaEnvironmentsByHandler(template, "openPipeline.lambda_handler");
        expect(envs).toHaveLength(1);
        const env = envs[0];
        const groupId = env.CONTAINER_LOG_GROUP_NAME?.Ref;
        expect(groupId).toBeDefined();
        const group = template.findResources("AWS::Logs::LogGroup")[groupId];
        expect(group).toBeDefined();
        expect(JSON.stringify(group.Properties.LogGroupName)).toContain(
            "/aws/vendedlogs/Pipelines/"
        );
        expect(env.CONTAINER_LOG_GROUP_ARN).toEqual({ "Fn::GetAtt": [groupId, "Arn"] });
    });

    test("the stage the producer declares is a state of the machine", () => {
        const declared = declaredStageNames(
            path.join(PRODUCERS, "multi", name, "lambda", "openPipeline.py")
        );
        expect(declared.length).toBeGreaterThan(0);
        const states = Object.keys(parseAsl(singleStateMachine(template)).States);
        for (const stage of declared) {
            expect(states).toContain(stage);
        }
    });
});
