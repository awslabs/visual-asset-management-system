/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: the externally owned pipeline bridge surface, asserted against the CloudFormation each shipped
 * config template emits and against a HYBRID of each template with one bridge configured.
 *
 *  - Orchestration bus resource policy (`app.pipelines.externalPipelineBridges`): no shipped template
 *    lists a bridge, so no `AWS::Events::EventBusPolicy` is emitted at all; the hybrid emits exactly one
 *    statement per entry, granting `events:PutEvents` on the bus ARN to the bridge account root under an
 *    `aws:PrincipalArn` condition on the operator's role ARN pattern. The account principal is the carrier
 *    of that condition, not a grant on its own, so a statement WITHOUT the condition is the defect.
 *  - Workflow role invoke targets (`invokeTargets`): exact ARNs are added verbatim per service beside the
 *    unchanged same-account name patterns, and no per-account wildcard appears.
 *  - Execution log read grant (`app.pipelines.executionLogReadPrefixes`): the executionService read scope
 *    is the two VAMS-own groups plus the configured list, and nothing else.
 *  - SSM integration surface: the bus ARN, event source prefix, workflow role ARN and import function
 *    name are published under `resourceNames/`.
 *
 * Partition portability rides along: every ARN the hybrid emits carries the template's partition.
 */

import {
    ALL_TEMPLATES,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";
import { DEFAULT_EXECUTION_LOG_READ_PREFIXES } from "../../config/config";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";

// A full-app synth is ~20 s, and this file needs six of them (three shipped templates + three hybrids).
jest.setTimeout(900_000);

/** A fixed 12-digit account distinct from the harness's own, standing in for the bridge account. */
const BRIDGE_ACCOUNT = "210987654321";
const BRIDGE_ROLE_PATTERN = (partition: string) =>
    `arn:${partition}:iam::${BRIDGE_ACCOUNT}:role/bridge-*`;
const BRIDGE_BUS_ARN = (partition: string, region: string) =>
    `arn:${partition}:events:${region}:${BRIDGE_ACCOUNT}:event-bus/mock-bridge`;
const BRIDGE_QUEUE_ARN = (partition: string, region: string) =>
    `arn:${partition}:sqs:${region}:${BRIDGE_ACCOUNT}:mock-bridge-queue`;
const BRIDGE_FUNCTION_ARN = (partition: string, region: string) =>
    `arn:${partition}:lambda:${region}:${BRIDGE_ACCOUNT}:function:mock-bridge-adapter`;
const EXTRA_LOG_PREFIX = "/aws/vendedlogs/t1-bridge/*";

const synth = (name: TemplateName): SynthResult => synthTemplate(name);

/** One bridge with one target of each service, plus one extra log-read prefix. */
const bridgeSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, {
        mutateKey: "ext-bridge",
        mutate: (c: any) => {
            const { partition, region } = c.env;
            c.app.pipelines.externalPipelineBridges = [
                {
                    accountId: BRIDGE_ACCOUNT,
                    roleArnPattern: BRIDGE_ROLE_PATTERN(partition),
                    invokeTargets: [
                        BRIDGE_BUS_ARN(partition, region),
                        BRIDGE_QUEUE_ARN(partition, region),
                        BRIDGE_FUNCTION_ARN(partition, region),
                    ],
                },
            ];
            c.app.pipelines.executionLogReadPrefixes = [
                ...DEFAULT_EXECUTION_LOG_READ_PREFIXES,
                EXTRA_LOG_PREFIX,
            ];
        },
    });

const asList = (value: unknown): any[] => (Array.isArray(value) ? value : [value]);

/** Every statement of the VAMSWorkflowIAMRole inline policies, across the whole assembly. */
const workflowRoleStatements = (s: SynthResult): any[] =>
    s
        .ofType("AWS::IAM::Role")
        .filter((r) => r.properties.Description === "VAMS Workflow IAM Role.")
        .flatMap((r) => (r.properties.Policies ?? []) as any[])
        .flatMap((p) => (p.PolicyDocument?.Statement ?? []) as any[]);

/**
 * Every statement attached to the role of the Lambda whose construct id is `lambdaId`. The role's
 * logical id starts with `${lambdaId}ServiceRole`; its grants may spill from the default
 * `AWS::IAM::Policy` into `AWS::IAM::ManagedPolicy` overflow policies, so both types are read.
 */
const lambdaRoleStatements = (s: SynthResult, lambdaId: string): any[] => {
    const roleIds = s
        .ofType("AWS::IAM::Role")
        .filter((r) => r.logicalId.startsWith(`${lambdaId}ServiceRole`))
        .map((r) => r.logicalId);
    expect(roleIds.length).toBeGreaterThan(0);
    return [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")]
        .filter((p) => roleIds.some((id) => JSON.stringify(p.properties.Roles ?? []).includes(id)))
        .flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]);
};

/** Flattened resource entries of every statement carrying `action`. */
const resourcesFor = (statements: any[], action: string): string[] =>
    statements
        .filter((st) => asList(st.Action).includes(action))
        .flatMap((st) => asList(st.Resource))
        .map((r) => SynthResult.flatten(r));

/** The names of every SSM String parameter under the deployment's resourceNames prefix. */
const resourceNameParameters = (s: SynthResult): string[] =>
    s
        .ofType("AWS::SSM::Parameter")
        .map((r) => SynthResult.flatten(r.properties.Name))
        .filter((name) => name.includes("/resourceNames/"));

/* ------------------------------------------------------------------------------------------------ *
 * Enabler 2 — orchestration bus resource policy
 * ------------------------------------------------------------------------------------------------ */

describe("orchestration bus resource policy", () => {
    test.each(ALL_TEMPLATES)("%s with a bridge emits exactly one bus policy", (name) => {
        // Positive control for the absence assertion on the shipped template below.
        expect(bridgeSynth(name).countOfType("AWS::Events::EventBusPolicy")).toBe(1);
    });

    test.each(ALL_TEMPLATES)("%s as shipped emits no bus policy at all", (name) => {
        expectAbsent(
            "AWS::Events::EventBusPolicy",
            synth(name).ofType("AWS::Events::EventBusPolicy"),
            {
                description: "the hybrid emits a bus policy",
                count: bridgeSynth(name).countOfType("AWS::Events::EventBusPolicy"),
            }
        );
    });

    test.each(ALL_TEMPLATES)(
        "%s grants PutEvents to the bridge account root under an aws:PrincipalArn condition",
        (name) => {
            const s = bridgeSynth(name);
            const [policy] = s.ofType("AWS::Events::EventBusPolicy");
            const statement = policy.properties.Statement;
            expect(statement).toBeDefined();
            expect(asList(statement.Action)).toEqual(["events:PutEvents"]);
            expect(statement.Effect).toBe("Allow");
            expect(SynthResult.flatten(statement.Principal?.AWS)).toBe(
                `arn:${s.partition}:iam::${BRIDGE_ACCOUNT}:root`
            );
            // The bus ARN is a GetAtt in the template; flattened it names the bus resource.
            expect(SynthResult.flatten(statement.Resource)).toMatch(/OrchestrationBus.*\.Arn/);
            expect(statement.Condition).toEqual({
                StringLike: { "aws:PrincipalArn": [BRIDGE_ROLE_PATTERN(s.partition)] },
            });
        }
    );

    test.each(ALL_TEMPLATES)("%s emits no bus statement without the condition", (name) => {
        const s = bridgeSynth(name);
        const statements = s
            .ofType("AWS::Events::EventBusPolicy")
            .map((p) => p.properties.Statement)
            .filter((st) => st !== undefined);
        const unconditioned = statements.filter(
            (st) => !st.Condition?.StringLike?.["aws:PrincipalArn"]
        );
        expectAbsent("bus policy statement without aws:PrincipalArn", unconditioned, {
            description: "bus policy statements exist",
            count: statements.length,
        });
    });
});

/* ------------------------------------------------------------------------------------------------ *
 * Enabler 3 — workflow role exact cross-account targets
 * ------------------------------------------------------------------------------------------------ */

describe("workflow role invoke targets", () => {
    test.each(ALL_TEMPLATES)("%s adds each exact target beside the name patterns", (name) => {
        const s = bridgeSynth(name);
        const statements = workflowRoleStatements(s);
        expect(statements.length).toBeGreaterThan(0);

        const putEvents = resourcesFor(statements, "events:PutEvents");
        expect(putEvents).toContain(BRIDGE_BUS_ARN(s.partition, s.region));
        expect(putEvents.some((r) => r.endsWith(":event-bus/*vams*"))).toBe(true);
        expect(putEvents.some((r) => r.endsWith(":event-bus/default"))).toBe(true);

        expect(resourcesFor(statements, "sqs:SendMessage")).toContain(
            BRIDGE_QUEUE_ARN(s.partition, s.region)
        );
        expect(resourcesFor(statements, "lambda:InvokeFunction")).toContain(
            BRIDGE_FUNCTION_ARN(s.partition, s.region)
        );
    });

    test.each(ALL_TEMPLATES)("%s grants no wildcard in the bridge account", (name) => {
        const s = bridgeSynth(name);
        const statements = workflowRoleStatements(s);
        const all = ["events:PutEvents", "sqs:SendMessage", "lambda:InvokeFunction"].flatMap(
            (action) => resourcesFor(statements, action)
        );
        const bridgeWildcards = all.filter(
            (r) => r.includes(`:${BRIDGE_ACCOUNT}:`) && r.includes("*")
        );
        expectAbsent("wildcard resource in the bridge account", bridgeWildcards, {
            description: "same-account name-pattern wildcards are still granted",
            count: all.filter((r) => r.includes("*vams*")).length,
        });
    });

    test.each(ALL_TEMPLATES)("%s as shipped carries no bridge-account resource", (name) => {
        const statements = workflowRoleStatements(synth(name));
        const bridgeRefs = ["events:PutEvents", "sqs:SendMessage", "lambda:InvokeFunction"]
            .flatMap((action) => resourcesFor(statements, action))
            .filter((r) => r.includes(BRIDGE_ACCOUNT));
        expectAbsent("bridge-account resource on the shipped workflow role", bridgeRefs, {
            description: "the hybrid grants bridge-account resources",
            count: resourcesFor(
                workflowRoleStatements(bridgeSynth(name)),
                "events:PutEvents"
            ).filter((r) => r.includes(BRIDGE_ACCOUNT)).length,
        });
    });
});

/* ------------------------------------------------------------------------------------------------ *
 * Enabler 8 — executionService log read allow-list
 * ------------------------------------------------------------------------------------------------ */

describe("executionService log read grant", () => {
    const LOGS_ACTIONS = ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"];

    /** Log-group resource entries with their `:*` stream suffix stripped and de-duplicated. */
    const logGroups = (s: SynthResult): string[] => {
        const entries = resourcesFor(
            lambdaRoleStatements(s, "executionService"),
            LOGS_ACTIONS[0]
        ).map((r) => r.replace(/:\*$/, ""));
        return Array.from(new Set(entries));
    };

    test.each(ALL_TEMPLATES)("%s as shipped reads exactly the default prefixes", (name) => {
        const s = synth(name);
        const groups = logGroups(s);
        // The shared workflow SFN log group is a GetAtt (flattened as `${<id>.Arn}`), the config-name
        // pattern is the `*vams*` group; everything else must be the configured list and nothing more.
        const workflowGroup = groups.filter((g) => /vamsPipelineWorkflows.*\.Arn/.test(g));
        const namePattern = groups.filter((g) => g.endsWith(":log-group:*vams*"));
        expect(workflowGroup).toHaveLength(1);
        expect(namePattern).toHaveLength(1);
        const configured = groups.filter(
            (g) => !workflowGroup.includes(g) && !namePattern.includes(g)
        );
        expect(configured.sort()).toEqual(
            [...DEFAULT_EXECUTION_LOG_READ_PREFIXES]
                .map((p) => `arn:${s.partition}:logs:${s.region}:123456789012:log-group:${p}`)
                .sort()
        );
    });

    test.each(ALL_TEMPLATES)("%s grants every log action the same group set", (name) => {
        const statements = lambdaRoleStatements(synth(name), "executionService");
        const sets = LOGS_ACTIONS.map((action) =>
            resourcesFor(statements, action)
                .filter((r) => r.includes(":log-group:"))
                .sort()
                .join("|")
        );
        expect(new Set(sets).size).toBe(1);
        expect(sets[0]).toContain("/aws/sagemaker/TrainingJobs");
    });

    test.each(ALL_TEMPLATES)("%s with an extra prefix reads it, with and without :*", (name) => {
        const s = bridgeSynth(name);
        const entries = resourcesFor(lambdaRoleStatements(s, "executionService"), LOGS_ACTIONS[0]);
        const extra = `arn:${s.partition}:logs:${s.region}:123456789012:log-group:${EXTRA_LOG_PREFIX}`;
        expect(entries).toContain(extra);
        expect(entries).toContain(`${extra}:*`);
        // The shipped template does not read it, so the grant really is config-driven.
        expect(
            resourcesFor(lambdaRoleStatements(synth(name), "executionService"), LOGS_ACTIONS[0])
        ).not.toContain(extra);
    });
});

/* ------------------------------------------------------------------------------------------------ *
 * Enabler 7 — SageMaker sub-process grants
 * ------------------------------------------------------------------------------------------------ */

describe("SageMaker sub-process grants", () => {
    const STOPS = ["sagemaker:StopTrainingJob", "sagemaker:StopPipelineExecution"];
    const DESCRIBES = ["sagemaker:DescribeTrainingJob", "sagemaker:DescribePipelineExecution"];
    const expected = (s: SynthResult) =>
        [
            `arn:${s.partition}:sagemaker:${s.region}:123456789012:training-job/*`,
            `arn:${s.partition}:sagemaker:${s.region}:123456789012:pipeline/*/execution/*`,
        ].sort();

    test.each(ALL_TEMPLATES)(
        "%s executionService stops and describes in its own partition",
        (name) => {
            const s = synth(name);
            const statements = lambdaRoleStatements(s, "executionService");
            for (const action of [...STOPS, ...DESCRIBES]) {
                expect(resourcesFor(statements, action).sort()).toEqual(expected(s));
            }
        }
    );

    test.each(ALL_TEMPLATES)("%s handleExecutionError stops only", (name) => {
        const s = synth(name);
        const statements = lambdaRoleStatements(s, "handleExecutionError");
        for (const action of STOPS) {
            expect(resourcesFor(statements, action).sort()).toEqual(expected(s));
        }
        for (const action of DESCRIBES) {
            expect(resourcesFor(statements, action)).toEqual([]);
        }
    });

    test.each(ALL_TEMPLATES)("%s grants no bare wildcard on any SageMaker action", (name) => {
        const s = synth(name);
        const all = ["executionService", "handleExecutionError"].flatMap((id) =>
            [...STOPS, ...DESCRIBES].flatMap((action) =>
                resourcesFor(lambdaRoleStatements(s, id), action)
            )
        );
        expectAbsent(
            "bare * on a SageMaker action",
            all.filter((r) => r === "*"),
            { description: "SageMaker grants exist", count: all.length }
        );
    });
});

/* ------------------------------------------------------------------------------------------------ *
 * Enabler 1 — SSM integration surface
 * ------------------------------------------------------------------------------------------------ */

describe("SSM integration surface", () => {
    const INTEGRATION_KEYS = [
        RESOURCE_PARAM_KEYS.integration.orchestrationBusArn,
        RESOURCE_PARAM_KEYS.integration.orchestrationEventSourcePrefix,
        RESOURCE_PARAM_KEYS.integration.workflowExecutionRoleArn,
        RESOURCE_PARAM_KEYS.lambdaFunctions.importGlobalPipelineWorkflow,
    ];

    test.each(ALL_TEMPLATES)("%s publishes the four integration parameters", (name) => {
        const names = resourceNameParameters(synth(name));
        for (const key of INTEGRATION_KEYS) {
            expect(names.filter((n) => n.endsWith(`/resourceNames/${key}`))).toHaveLength(1);
        }
    });

    test.each(ALL_TEMPLATES)("%s publishes every canonical key exactly once", (name) => {
        const s = synth(name);
        const names = resourceNameParameters(s);
        const canonical = Object.values(RESOURCE_PARAM_KEYS).flatMap((category) =>
            Object.values(category as Record<string, string>)
        );
        // crOsReindexer is published only when the reindexer is built (search enabled), so it is the
        // one canonical key allowed to be absent.
        const required = canonical.filter(
            (key) => key !== RESOURCE_PARAM_KEYS.lambdaFunctions.crOsReindexer
        );
        for (const key of required) {
            expect(names.filter((n) => n.endsWith(`/resourceNames/${key}`))).toHaveLength(1);
        }
        // 66 registry parameters (ResourceNamesBuilder) + the two late-bound ones here, plus the
        // reindexer when search is enabled.
        expect(names.length).toBeGreaterThanOrEqual(68);
        expect(names.length).toBeLessThanOrEqual(69);
    });

    test.each(ALL_TEMPLATES)("%s binds the workflow role ARN parameter to the role", (name) => {
        const s = synth(name);
        const param = s
            .ofType("AWS::SSM::Parameter")
            .find((r) =>
                SynthResult.flatten(r.properties.Name).endsWith(
                    `/${RESOURCE_PARAM_KEYS.integration.workflowExecutionRoleArn}`
                )
            );
        expect(param).toBeDefined();
        expect(SynthResult.flatten(param!.properties.Value)).toMatch(/VAMSWorkflowIAMRole.*\.Arn/);
    });
});
