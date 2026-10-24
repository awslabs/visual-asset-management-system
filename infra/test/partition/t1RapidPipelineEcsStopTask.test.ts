/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: the RapidPipeline (Amazon ECS) timeout stop route, in every shipped partition.
 *
 * A Fargate task still running at its task-level timeout is found with ecs:ListTasks and stopped
 * with ecs:StopTask (test/pipelines/ecsTimeoutStopTask.test.ts covers the route itself). Both calls
 * are AWS SDK integrations whose `Resource` is a Step Functions ARN, and the StopTask grant is an ECS
 * task ARN; each is built from the deployment's partition. A commercial `arn:aws:` in GovCloud or
 * the EU Sovereign Cloud is accepted by CloudFormation and only fails when the route runs — at the
 * timeout, the one moment it is needed — so it is asserted here.
 *
 * No shipped template enables the ECS RapidPipeline, so every assertion runs against a HYBRID of each
 * template with it switched on (and the global VPC it requires). The absence assertion on the
 * restricted partitions carries its positive control: the number of stop-route ARNs found.
 */

import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

// A full-app synth is ~20 s, and this file needs three of them (one hybrid per shipped template).
jest.setTimeout(600_000);

const ACCOUNT = "123456789012";
const IMAGE_URI = `${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1`;

const ecsSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, {
        mutateKey: "rapidpipeline-ecs-stop-task",
        mutate: (c: any) => {
            c.app.useGlobalVpc.enabled = true;
            c.app.pipelines.useRapidPipeline.enabled = true;
            c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
            c.app.pipelines.useRapidPipeline.useEcs.enabled = true;
            c.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI = IMAGE_URI;
            c.app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS = false;
        },
    });

const asList = (value: unknown): any[] => (Array.isArray(value) ? value : [value]);

interface StopRoute {
    machine: Resource;
    states: Record<string, any>;
    clusterId: string;
    listTasks: any;
    stopTask: any;
}

/** The RapidPipeline ECS state machine, its stop-route statements and the cluster beside it. */
const stopRoute = (s: SynthResult): StopRoute => {
    const machines = s
        .ofType("AWS::StepFunctions::StateMachine")
        .map((machine) => ({
            machine,
            states: JSON.parse(SynthResult.flatten(machine.properties.DefinitionString)).States,
        }))
        .filter((m) => m.states.RapidPipelineRunFargate !== undefined);
    expect(machines).toHaveLength(1);
    const { machine, states } = machines[0];

    const clusters = s
        .ofType("AWS::ECS::Cluster")
        .filter(
            (c) => c.stack === machine.stack && c.logicalId.startsWith("RapidPipelineEcsCluster")
        );
    expect(clusters).toHaveLength(1);

    const statements = s
        .ofType("AWS::IAM::Policy")
        .filter((p) => p.stack === machine.stack)
        .flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]);
    const bySid = (sid: string): any => {
        const found = statements.filter((st) => st.Sid === sid);
        expect(found).toHaveLength(1);
        return found[0];
    };

    return {
        machine,
        states,
        clusterId: clusters[0].logicalId,
        listTasks: bySid("ListTimedOutRapidPipelineTask"),
        stopTask: bySid("StopTimedOutRapidPipelineTask"),
    };
};

/** Every ARN the stop route builds: the two SDK integration resources and the StopTask grant. */
const stopRouteArns = (s: SynthResult): string[] => {
    const r = stopRoute(s);
    return [
        r.states.ListTimedOutTask.Resource,
        r.states.StopTimedOutTasks.ItemProcessor.States.StopTimedOutTask.Resource,
        ...asList(r.stopTask.Resource).map((v) => SynthResult.flatten(v)),
    ];
};

describe("RapidPipeline ECS stop route across partitions", () => {
    test.each(ALL_TEMPLATES)("[control] %s with ECS enabled emits the stop route", (name) => {
        const r = stopRoute(ecsSynth(name));
        expect(r.states.RapidPipelineRunFargate.Catch[0].Next).toBe("ListTimedOutTask");
        expect(r.states.StopTimedOutTasks.Type).toBe("Map");
    });

    test.each(ALL_TEMPLATES)(
        "%s: the SDK calls are this partition's Step Functions ARNs",
        (name) => {
            const s = ecsSynth(name);
            const r = stopRoute(s);
            expect(r.states.ListTimedOutTask.Resource).toBe(
                `arn:${s.partition}:states:::aws-sdk:ecs:listTasks`
            );
            expect(r.states.StopTimedOutTasks.ItemProcessor.States.StopTimedOutTask.Resource).toBe(
                `arn:${s.partition}:states:::aws-sdk:ecs:stopTask`
            );
            // The calls target this pipeline's cluster.
            expect(r.states.ListTimedOutTask.Parameters.Cluster).toBe(`\${${r.clusterId}.Arn}`);
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s: ListTasks and StopTask are granted on this cluster in this partition",
        (name) => {
            const s = ecsSynth(name);
            const r = stopRoute(s);
            const condition = {
                ArnEquals: { "ecs:cluster": { "Fn::GetAtt": [r.clusterId, "Arn"] } },
            };
            expect(asList(r.listTasks.Action)).toEqual(["ecs:ListTasks"]);
            expect(r.listTasks.Resource).toBe("*");
            expect(r.listTasks.Condition).toEqual(condition);
            expect(asList(r.stopTask.Action)).toEqual(["ecs:StopTask"]);
            expect(asList(r.stopTask.Resource).map((v) => SynthResult.flatten(v))).toEqual([
                `arn:${s.partition}:ecs:${s.region}:${ACCOUNT}:task/\${${r.clusterId}}/*`,
            ]);
            expect(r.stopTask.Condition).toEqual(condition);
        }
    );

    test.each(RESTRICTED_TEMPLATES)("%s: no stop-route ARN is a commercial one", (name) => {
        const arns = stopRouteArns(ecsSynth(name));
        expectAbsent(
            "a stop-route ARN in the aws partition",
            arns.filter((arn) => arn.startsWith("arn:aws:")),
            { description: "stop-route ARNs found", count: arns.length }
        );
    });
});
