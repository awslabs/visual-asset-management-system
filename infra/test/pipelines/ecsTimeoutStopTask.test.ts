/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A RapidPipeline (Amazon ECS) Fargate task that reaches its task-level timeout is stopped.
 *
 * The `.sync` RapidPipelineRunFargate state carries a TimeoutSeconds (the chain in
 * ecsTimeoutHeadroom.test.ts). When it fires, the state fails with States.Timeout and its error
 * output carries no task ARN. Step Functions stops the task as it ends the state, but its
 * documentation does not list that case, so the state machine also stops a task still running:
 *
 *  1. the RunTask call sets the task's group to the execution name;
 *  2. a Timeout catch, ahead of the general catch, lists the cluster's running tasks with
 *     ecs:ListTasks (which cannot filter by group); only when it listed one, ecs:DescribeTasks keeps
 *     the ARNs of the tasks whose group is the execution name; a Map calls ecs:StopTask on those;
 *     the route goes on to PipelineEndTask, which reports the failure to the parent workflow;
 *  3. every call is bounded and best-effort: a failure is caught into $.stopError and still reaches
 *     PipelineEndTask, so $.error, which PipelineEnd reports, stays States.Timeout;
 *  4. the state machine role gets ecs:ListTasks only under an ecs:cluster condition (its resource
 *     is a container instance, which a Fargate cluster has none of, so the statement is on "*"),
 *     and ecs:DescribeTasks and ecs:StopTask statements scoped to this cluster's tasks under the
 *     same condition.
 *
 * The partition each ARN is built in is asserted across the shipped templates in
 * test/partition/t1RapidPipelineEcsStopTask.test.ts.
 */

import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { RapidPipelineConstruct } from "../../lib/nestedStacks/pipelines/multi/rapidPipeline/constructs/rapidPipeline-construct";
import { ACCOUNT, REGION, makePipelineHarness } from "../support/pipelineConstructHarness";

const IMAGE_URI = `${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/rapidpipeline:latest`;

const FARGATE = "RapidPipelineRunFargate";
const GENERAL_ERROR = "HandleRapidPipelineError";
const LIST = "ListTimedOutTask";
const LISTED = "TimedOutTaskListed";
const DESCRIBE = "DescribeTimedOutTasks";
const STOP_MAP = "StopTimedOutTasks";
const STOP = "StopTimedOutTask";
const END = "PipelineEndTask";

/** The AWS SDK integration errors the stop calls retry; the ECS exception prefix is `Ecs`. */
const RETRIED_ERRORS = ["Ecs.ThrottlingException", "Ecs.ServerException"];

/** What DescribeTimedOutTasks keeps: its input, and the ARNs of the tasks this execution started. */
const DESCRIBE_OUTPUT =
    "{% $merge([$states.input, {'timedOutTaskArns': " +
    "[$states.result.Tasks[Group = $states.context.Execution.Name].TaskArn]}]) %}";

/** A JSONata catcher's equivalent of ResultPath "$.stopError". */
const KEEP_INPUT_AND_ADD_STOP_ERROR =
    "{% $merge([$states.input, {'stopError': $states.errorOutput}]) %}";

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

    // In-development defect D3 guard (PR #775). Step Functions rejects a StartedBy field in an
    // ecs:runTask.sync state's Parameters ("The field 'StartedBy' is not supported by Step
    // Functions"), so a definition carrying it fails to deploy, and CDK synthesis does not catch it.
    // The task is labelled with Group instead; this keeps a later edit from bringing StartedBy back.
    test("the RunTask call labels the task with Group, never StartedBy, and keeps EcsRunTask's fields", () => {
        const parameters = states[FARGATE].Parameters;
        expect(parameters["Group.$"]).toBe("$$.Execution.Name");
        expect(parameters).not.toHaveProperty("StartedBy");
        expect(parameters).not.toHaveProperty(["StartedBy.$"]);
        // The subclass adds one field; everything EcsRunTask renders is still there.
        expect(Object.keys(parameters).sort()).toEqual([
            "Cluster",
            "Group.$",
            "LaunchType",
            "NetworkConfiguration",
            "Overrides",
            "PropagateTags",
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

    test("ListTimedOutTask lists one page of this cluster's running tasks, unfiltered by label", () => {
        const list = states[LIST];
        expect(list.Type).toBe("Task");
        expect(list.Resource).toMatch(/^arn:aws:states:::aws-sdk:ecs:listTasks$/);
        // ListTasks cannot filter by group; the match is DescribeTimedOutTasks'.
        expect(list.Parameters).toEqual({
            Cluster: `\${${clusterId}.Arn}`,
            DesiredStatus: "RUNNING",
            MaxResults: 100,
        });
        // One page: the most ListTasks returns, and the most DescribeTasks accepts.
        expect(list.Parameters.MaxResults).toBe(Config.RAPID_PIPELINE_ECS_STOP_ROUTE_PAGE_SIZE);
        // Kept beside the input; a ResultSelector path the response lacked would be a States.Runtime.
        expect(list.ResultPath).toBe("$.timedOutTasks");
        expect(list.ResultSelector).toBeUndefined();
        expect(list.Next).toBe(LISTED);
    });

    test("DescribeTimedOutTasks runs only when a task was listed, else the run goes to PipelineEnd", () => {
        const listed = states[LISTED];
        expect(listed.Type).toBe("Choice");
        // DescribeTasks rejects an empty task list; an empty or absent list has no first element.
        expect(listed.Choices).toEqual([
            { Variable: "$.timedOutTasks.TaskArns[0]", IsPresent: true, Next: DESCRIBE },
        ]);
        expect(listed.Default).toBe(END);
    });

    test("DescribeTimedOutTasks describes the listed tasks and keeps only this execution's", () => {
        const describe = states[DESCRIBE];
        expect(describe.Type).toBe("Task");
        expect(describe.QueryLanguage).toBe("JSONata");
        expect(describe.Resource).toMatch(/^arn:aws:states:::aws-sdk:ecs:describeTasks$/);
        // The ARNs ListTimedOutTask listed, on this cluster.
        expect(describe.Arguments).toEqual({
            Cluster: `\${${clusterId}.Arn}`,
            Tasks: "{% $states.input.timedOutTasks.TaskArns %}",
        });
        // The input is kept and the ARNs of the tasks whose group is this execution's name are added,
        // as a list that is empty when no task matches. The descriptions themselves are not kept.
        expect(describe.Output).toBe(DESCRIBE_OUTPUT);
        expect(describe.Parameters).toBeUndefined();
        expect(describe.ResultPath).toBeUndefined();
        expect(describe.ResultSelector).toBeUndefined();
        expect(describe.Next).toBe(STOP_MAP);
    });

    test("only the tasks DescribeTimedOutTasks matched reach StopTimedOutTask", () => {
        const map = states[STOP_MAP];
        expect(map.Type).toBe("Map");
        // The list DescribeTimedOutTasks' Output adds, which it always sets.
        expect(DESCRIBE_OUTPUT).toContain("'timedOutTaskArns':");
        expect(map.ItemsPath).toBe("$.timedOutTaskArns");
        // The Map is reached only through DescribeTimedOutTasks.
        const into = Object.entries(states)
            .filter(([, s]: [string, any]) =>
                [
                    s.Next,
                    s.Default,
                    ...(s.Choices ?? []).map((c: any) => c.Next),
                    ...(s.Catch ?? []).map((c: any) => c.Next),
                ].includes(STOP_MAP)
            )
            .map(([name]) => name);
        expect(into).toEqual([DESCRIBE]);
        expect(map.ItemSelector).toEqual({ "taskArn.$": "$$.Map.Item.Value" });
        // The stop responses are discarded; the input reaches PipelineEnd as it was.
        expect(map.ResultPath).toBeNull();
        expect(map.Next).toBe(END);
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

    test("every stop-route call is bounded and retried only on throttling and server errors", () => {
        for (const call of [
            states[LIST],
            states[DESCRIBE],
            states[STOP_MAP].ItemProcessor.States[STOP],
        ]) {
            expect(call.TimeoutSeconds).toBe(Config.RAPID_PIPELINE_ECS_STOP_CALL_TIMEOUT_SECONDS);
            expect(call.Retry).toHaveLength(1);
            expect([...call.Retry[0].ErrorEquals].sort()).toEqual([...RETRIED_ERRORS].sort());
            expect(call.Retry[0].MaxAttempts).toBe(Config.RAPID_PIPELINE_ECS_STOP_CALL_RETRIES);
            expect(call.Retry[0].IntervalSeconds).toBe(1);
            expect(call.Retry[0].BackoffRate).toBe(2);
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
        // A JSONata catcher has no ResultPath; its Output keeps the input and adds the error at
        // stopError. States.ALL does not match States.DataLimitExceeded (a DescribeTasks response
        // over the payload quota), so that error is caught by name.
        expect(states[DESCRIBE].Catch).toEqual([
            {
                ErrorEquals: ["States.DataLimitExceeded"],
                Output: KEEP_INPUT_AND_ADD_STOP_ERROR,
                Next: END,
            },
            { ErrorEquals: ["States.ALL"], Output: KEEP_INPUT_AND_ADD_STOP_ERROR, Next: END },
        ]);
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

    test.each(["ecs:DescribeTasks", "ecs:StopTask"])(
        "%s is granted on this cluster's tasks under the same condition",
        (action) => {
            const scoped = grantsOf(action).filter((s) => s.Resource !== "*");
            expect(scoped).toHaveLength(1);
            expect(asList(scoped[0].Action)).toEqual([action]);
            expect(flatten(scoped[0].Resource)).toBe(
                `arn:aws:ecs:${REGION}:${ACCOUNT}:task/\${${clusterId}}/*`
            );
            expect(scoped[0].Condition).toEqual(clusterCondition());
            // EcsRunTask's own policy for the .sync integration is unchanged and already holds both
            // on *.
            const generated = grantsOf(action).filter((s) => s.Resource === "*");
            expect(generated).toHaveLength(1);
            expect([...asList(generated[0].Action)].sort()).toEqual([
                "ecs:DescribeTasks",
                "ecs:StopTask",
            ]);
        }
    );

    test("the role's ECS and IAM actions are exactly the .sync integration's and ecs:ListTasks", () => {
        const statements = roleStatements.filter((s) => s.Effect === "Allow");
        const actions = new Set(
            statements.flatMap((s) => asList(s.Action)).filter((a: string) => /^(ecs|iam):/.test(a))
        );
        expect([...actions].sort()).toEqual([
            "ecs:DescribeTasks",
            "ecs:ListTasks",
            "ecs:RunTask",
            "ecs:StopTask",
            "iam:PassRole",
        ]);
        // The task is labelled with Group, which needs no tagging permission and no Tags.
        expect(states[FARGATE].Parameters).not.toHaveProperty("Tags");
    });
});
