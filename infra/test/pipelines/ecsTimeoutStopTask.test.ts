/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A RapidPipeline (Amazon ECS) Fargate task that reaches its task-level timeout is stopped.
 *
 * The `.sync` RapidPipelineRunFargate state carries a TimeoutSeconds (the chain in
 * ecsTimeoutHeadroom.test.ts). When it fires, the state fails with States.Timeout, but Step Functions
 * does not stop the task, and the error output carries no task ARN. So:
 *
 *  1. the RunTask call sets StartedBy to the execution name, which ecs:ListTasks can filter by;
 *  2. a Timeout catch, ahead of the general catch, runs ecs:ListTasks and then ecs:StopTask on what
 *     it found, and goes on to PipelineEndTask, which reports the failure to the parent workflow;
 *  3. every call is bounded and best-effort: a failure is caught into $.stopError and still reaches
 *     PipelineEndTask, so $.error, which PipelineEnd reports, stays States.Timeout;
 *  4. the state machine role gets ecs:ListTasks only under an ecs:cluster condition (its resource
 *     is a container instance, which a Fargate cluster has none of, so the statement is on "*"),
 *     and an ecs:StopTask statement scoped to this cluster's tasks under the same condition.
 *
 * The partition each ARN is built in is asserted across the shipped templates in
 * test/partition/t1RapidPipelineEcsStopTask.test.ts.
 */

import { Template } from "aws-cdk-lib/assertions";
import { RapidPipelineConstruct } from "../../lib/nestedStacks/pipelines/multi/rapidPipeline/constructs/rapidPipeline-construct";
import { ACCOUNT, REGION, makePipelineHarness } from "../support/pipelineConstructHarness";

const IMAGE_URI = `${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/rapidpipeline:latest`;

const FARGATE = "RapidPipelineRunFargate";
const GENERAL_ERROR = "HandleRapidPipelineError";
const LIST = "ListTimedOutTask";
const LISTED = "TimedOutTaskListed";
const STOP_MAP = "StopTimedOutTasks";
const STOP = "StopTimedOutTask";
const END = "PipelineEndTask";

/** The AWS SDK integration errors the stop calls retry; the ECS exception prefix is `Ecs`. */
const RETRIED_ERRORS = ["Ecs.ThrottlingException", "Ecs.ServerException"];

/** A CloudFormation value flattened to a string, tokens kept as `${LogicalId}` / `${LogicalId.Attr}`. */
const flatten = (value: any): string => {
    if (typeof value === "string") return value;
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(flatten).join("");
    if (typeof value === "object") {
        if (value["Fn::Join"]) {
            const [separator, parts] = value["Fn::Join"];
            return (parts as any[]).map(flatten).join(separator);
        }
        if (value.Ref) return `\${${value.Ref}}`;
        if (value["Fn::GetAtt"]) return `\${${(value["Fn::GetAtt"] as any[]).join(".")}}`;
        return Object.values(value).map(flatten).join("");
    }
    return String(value);
};

const asList = (value: unknown): any[] => (Array.isArray(value) ? value : [value]);

describe("RapidPipeline ECS: a timed-out Fargate task is stopped", () => {
    let template: Template;
    let states: Record<string, any>;
    let roleStatements: any[];
    let clusterId: string;

    beforeAll(() => {
        const h = makePipelineHarness("RapidPipelineEcsStopTaskStack", (c) => {
            c.app.pipelines.useRapidPipeline.useEcs.enabled = true;
            c.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI = IMAGE_URI;
            c.app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS = false;
        });
        const construct = new RapidPipelineConstruct(h.stack, "RapidPipelineConstruct", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnetsPrivate: h.subnets,
            pipelineSubnetsIsolated: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(construct);

        const machines = Object.values<any>(
            template.findResources("AWS::StepFunctions::StateMachine")
        );
        expect(machines).toHaveLength(1);
        states = JSON.parse(flatten(machines[0].Properties.DefinitionString)).States;

        const roleId = machines[0].Properties.RoleArn["Fn::GetAtt"][0];
        roleStatements = Object.values<any>(template.findResources("AWS::IAM::Policy"))
            .filter((p) => (p.Properties.Roles ?? []).some((r: any) => r.Ref === roleId))
            .flatMap((p) => p.Properties.PolicyDocument.Statement);

        const clusters = Object.keys(template.findResources("AWS::ECS::Cluster"));
        expect(clusters).toHaveLength(1);
        clusterId = clusters[0];
    });

    /** Statements on the state machine role that grant `action`. */
    const grantsOf = (action: string): any[] =>
        roleStatements.filter((s) => s.Effect === "Allow" && asList(s.Action).includes(action));

    /** The ecs:cluster condition every timeout-route statement carries. */
    const clusterCondition = () => ({
        ArnEquals: { "ecs:cluster": { "Fn::GetAtt": [clusterId, "Arn"] } },
    });

    test("[control] the state machine runs the Fargate task with .sync on this cluster", () => {
        expect(states[FARGATE].Type).toBe("Task");
        expect(states[FARGATE].Resource).toMatch(/:states:::ecs:runTask\.sync$/);
        expect(states[FARGATE].Parameters.Cluster).toBe(`\${${clusterId}.Arn}`);
        expect(states[END].Type).toBe("Task");
        expect(roleStatements.length).toBeGreaterThan(0);
    });

    test("the RunTask call sets StartedBy to the execution name and keeps EcsRunTask's fields", () => {
        const parameters = states[FARGATE].Parameters;
        expect(parameters["StartedBy.$"]).toBe("$$.Execution.Name");
        // The subclass adds one field; everything EcsRunTask renders is still there.
        expect(Object.keys(parameters).sort()).toEqual([
            "Cluster",
            "LaunchType",
            "NetworkConfiguration",
            "Overrides",
            "PropagateTags",
            "StartedBy.$",
            "TaskDefinition",
        ]);
        expect(parameters.LaunchType).toBe("FARGATE");
        expect(parameters.Overrides.ContainerOverrides[0].Environment[0]["Value.$"]).toBe(
            "$.externalSfnTaskToken"
        );
    });

    test("the Timeout catch comes before the general catch and starts the stop route", () => {
        const catchers = states[FARGATE].Catch;
        expect(catchers).toHaveLength(2);
        // Catchers match in order, and States.ALL matches States.Timeout too.
        expect(catchers[0]).toEqual({
            ErrorEquals: ["States.Timeout"],
            ResultPath: "$.error",
            Next: LIST,
        });
        expect(catchers[1]).toEqual({
            ErrorEquals: ["States.ALL"],
            ResultPath: "$.error",
            Next: GENERAL_ERROR,
        });
    });

    test("ListTimedOutTask lists this cluster's running task that this execution started", () => {
        const list = states[LIST];
        expect(list.Type).toBe("Task");
        expect(list.Resource).toMatch(/^arn:aws:states:::aws-sdk:ecs:listTasks$/);
        expect(list.Parameters).toEqual({
            Cluster: `\${${clusterId}.Arn}`,
            "StartedBy.$": "$$.Execution.Name",
            DesiredStatus: "RUNNING",
        });
        // Kept beside the input; a ResultSelector path the response lacked would be a States.Runtime.
        expect(list.ResultPath).toBe("$.timedOutTasks");
        expect(list.ResultSelector).toBeUndefined();
        expect(list.Next).toBe(LISTED);
    });

    test("the Map runs only when the list is present, then PipelineEnd", () => {
        const listed = states[LISTED];
        expect(listed.Type).toBe("Choice");
        expect(listed.Choices).toEqual([
            { Variable: "$.timedOutTasks.TaskArns", IsPresent: true, Next: STOP_MAP },
        ]);
        expect(listed.Default).toBe(END);
        expect(states[STOP_MAP].Next).toBe(END);
    });

    test("StopTimedOutTasks stops each listed task in this cluster", () => {
        const map = states[STOP_MAP];
        expect(map.Type).toBe("Map");
        expect(map.ItemsPath).toBe("$.timedOutTasks.TaskArns");
        expect(map.ItemSelector).toEqual({ "taskArn.$": "$$.Map.Item.Value" });
        // The stop responses are discarded; the input reaches PipelineEnd as it was.
        expect(map.ResultPath).toBeNull();
        expect(map.ItemProcessor.StartAt).toBe(STOP);
        expect(Object.keys(map.ItemProcessor.States)).toEqual([STOP]);
        const stop = map.ItemProcessor.States[STOP];
        expect(stop.Type).toBe("Task");
        expect(stop.Resource).toMatch(/^arn:aws:states:::aws-sdk:ecs:stopTask$/);
        expect(stop.Parameters.Cluster).toBe(`\${${clusterId}.Arn}`);
        expect(stop.Parameters["Task.$"]).toBe("$.taskArn");
        expect(typeof stop.Parameters.Reason).toBe("string");
        expect(stop.Parameters.Reason.length).toBeGreaterThan(0);
        expect(stop.End).toBe(true);
    });

    test("every stop call is bounded and retried only on throttling and server errors", () => {
        for (const call of [states[LIST], states[STOP_MAP].ItemProcessor.States[STOP]]) {
            expect(typeof call.TimeoutSeconds).toBe("number");
            expect(call.TimeoutSeconds).toBeGreaterThan(0);
            expect(call.Retry).toHaveLength(1);
            expect([...call.Retry[0].ErrorEquals].sort()).toEqual([...RETRIED_ERRORS].sort());
            expect(call.Retry[0].MaxAttempts).toBeGreaterThan(0);
        }
    });

    test("every stop-route state's catch leads to PipelineEndTask with $.stopError", () => {
        // A Map iteration cannot leave the Map, so its failure is the Map's to catch.
        for (const name of [LIST, STOP_MAP]) {
            expect(states[name].Catch).toEqual([
                { ErrorEquals: ["States.ALL"], ResultPath: "$.stopError", Next: END },
            ]);
        }
        expect(states[STOP_MAP].ItemProcessor.States[STOP].Catch).toBeUndefined();
    });

    test("ecs:ListTasks is granted only on this cluster, through the ecs:cluster condition", () => {
        const grants = grantsOf("ecs:ListTasks");
        expect(grants).toHaveLength(1);
        for (const grant of grants) {
            expect(asList(grant.Action)).toEqual(["ecs:ListTasks"]);
            // The action's resource is a container instance; a Fargate cluster has none.
            expect(grant.Resource).toBe("*");
            expect(grant.Condition).toEqual(clusterCondition());
        }
    });

    test("ecs:StopTask is granted on this cluster's tasks under the same condition", () => {
        const scoped = grantsOf("ecs:StopTask").filter((s) => s.Resource !== "*");
        expect(scoped).toHaveLength(1);
        expect(asList(scoped[0].Action)).toEqual(["ecs:StopTask"]);
        expect(flatten(scoped[0].Resource)).toBe(
            `arn:aws:ecs:${REGION}:${ACCOUNT}:task/\${${clusterId}}/*`
        );
        expect(scoped[0].Condition).toEqual(clusterCondition());
        // EcsRunTask's own policy for the .sync integration is unchanged and already holds it on *.
        const generated = grantsOf("ecs:StopTask").filter((s) => s.Resource === "*");
        expect(generated).toHaveLength(1);
        expect([...asList(generated[0].Action)].sort()).toEqual([
            "ecs:DescribeTasks",
            "ecs:StopTask",
        ]);
    });

    test("the role gains no ECS or IAM action beyond ecs:ListTasks", () => {
        const actions = new Set(
            roleStatements
                .filter((s) => s.Effect === "Allow")
                .flatMap((s) => asList(s.Action))
                .filter((a: string) => /^(ecs|iam):/.test(a))
        );
        expect([...actions].sort()).toEqual([
            "ecs:DescribeTasks",
            "ecs:ListTasks",
            "ecs:RunTask",
            "ecs:StopTask",
            "iam:PassRole",
        ]);
    });
});
