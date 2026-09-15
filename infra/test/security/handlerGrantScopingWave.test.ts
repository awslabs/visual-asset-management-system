/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Five grant-scoping findings across the Lambda builders, asserted on the synthesized templates.
 *
 *  * **S1-INFRA-075** — all three comment Lambdas held `grantReadWriteData` on the asset storage
 *    table. Their only asset-table access is `get_asset_object_from_id()`, a `get_item`, so three
 *    low-privilege handlers carried mutate rights over the primary asset catalog.
 *
 *  * **S1-INFRA-073** — the same shape in the assetLinks builders, whose asset-table calls are all
 *    `get_item` used to confirm both ends of a link exist.
 *
 *  * **S1-INFRA-076** — `configService` was granted `ssm:GetParameter` on every parameter in the
 *    account whose path merely contained the deployment name, and `geo:DescribeKey` on all Location
 *    Service API keys even in a deployment with Location Service turned off. Both exact parameter
 *    paths are known at synthesis.
 *
 *  * **S1-INFRA-043** — workflow and pipeline state machines are created with tracing enabled, and
 *    the role they assume had no X-Ray permissions, so no trace was ever recorded. A missing signal
 *    reported as present, rather than a failure.
 *
 *  * **S1-INFRA-044** — the pipeline-execution registration rule had no dead-letter queue, unlike the
 *    Deadline Cloud rules beside it. Registration is what makes an execution abortable, so a discarded
 *    delivery leaves orphaned state machines and GPU Batch jobs running after an abort.
 *
 * Statements are collected from BOTH `AWS::IAM::Policy` and `AWS::IAM::ManagedPolicy`: CDK spills into
 * a managed policy once an inline policy nears the size limit, and scanning only the inline kind
 * reports a correctly-granted permission as missing.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** DynamoDB actions that `grantReadWriteData` adds over `grantReadData`. */
const DYNAMO_WRITE_ACTIONS = [
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
];

describe("handler grant scoping", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    /** Every statement attached to any role whose logical id matches, inline and managed alike. */
    function statementsForRole(rolePattern: RegExp): any[] {
        const roleIds = synth
            .ofType("AWS::IAM::Role")
            .filter((r) => rolePattern.test(r.logicalId))
            .map((r) => r.logicalId);
        expect(roleIds.length).toBeGreaterThan(0);

        const attached = synth.resources
            .filter((r) => /IAM::(Policy|ManagedPolicy)$/.test(r.type))
            .filter((p) =>
                (((p.properties as any).Roles ?? []) as unknown[]).some((ref) =>
                    roleIds.some((id) => JSON.stringify(ref).includes(id))
                )
            )
            .flatMap((p) => ((p.properties as any).PolicyDocument?.Statement ?? []) as any[]);

        // A role's own INLINE policies, a third place a statement can live and not an AWS::IAM::Policy
        // resource at all. The workflow role's document is built that way, so a scanner that skipped it
        // reported a granted permission as missing.
        const inline = synth
            .ofType("AWS::IAM::Role")
            .filter((r) => roleIds.includes(r.logicalId))
            .flatMap((r) => ((r.properties as any).Policies ?? []) as any[])
            .flatMap((doc) => (doc.PolicyDocument?.Statement ?? []) as any[]);

        return [...attached, ...inline];
    }

    const actionsOf = (statements: any[]): string[] =>
        statements.flatMap((s) =>
            (Array.isArray(s.Action) ? s.Action : [s.Action]).filter(Boolean)
        );

    /**
     * The statements that reach the asset storage table.
     *
     * Matched on the table's own logical id inside the statement's Resource, because the table is
     * auto-named — there is no literal name to look for, and matching on the action alone would pick up
     * every other table the handler legitimately writes.
     */
    function assetTableStatements(rolePattern: RegExp): any[] {
        // Matched on the cross-stack reference NAME rather than the table's logical id: the table lives
        // in the storage nested stack while these handlers live in an API stack, so what appears in the
        // statement is a parameter reference carrying the table identity in its name. Matching the
        // logical id finds nothing, which is how the first version of this suite reported zero
        // statements and failed its own control.
        return statementsForRole(rolePattern).filter((st) =>
            /AssetStorageTable/i.test(JSON.stringify(st.Resource ?? ""))
        );
    }

    describe.each([
        ["comment handlers", /(addComment|editComment|commentService)ServiceRole/],
        ["assetLinks handlers", /(assetLinksService|createAssetLink)ServiceRole/],
    ])("%s hold read-only access to the asset table", (_name, rolePattern) => {
        test("the handler roles and the asset table ARE in this synth", () => {
            // The control. "No write action on the asset table" is satisfied by a synth in which
            // neither the roles nor the table exist.
            expect(assetTableStatements(rolePattern).length).toBeGreaterThan(0);
        });

        test("they can read the asset table", () => {
            // The positive half: the handlers resolve an asset to validate it exists, so removing read
            // would break them. A test that only checked for absent writes would pass if both were
            // stripped.
            const actions = actionsOf(assetTableStatements(rolePattern));
            expect(actions).toContain("dynamodb:GetItem");
        });

        test("they hold no write action on it", () => {
            const actions = actionsOf(assetTableStatements(rolePattern));
            for (const write of DYNAMO_WRITE_ACTIONS) {
                expect(actions).not.toContain(write);
            }
        });
    });

    describe("configService parameter access is scoped (S1-INFRA-076)", () => {
        /**
         * The parameter statements this builder adds.
         *
         * Excludes the resource-name prefix grant `globalLambdaEnvironmentsAndPermissions` adds to every
         * handler: that one legitimately ends in a wildcard over the deployment's own `resourceNames/`
         * path, and including it would fail the narrowing assertions on a correct statement.
         */
        const ssmStatements = () =>
            statementsForRole(/configService.*ServiceRole/)
                .filter((s) => actionsOf([s]).some((a) => a.startsWith("ssm:Get")))
                .filter((s) => !/resourceNames/.test(JSON.stringify(s.Resource ?? "")));

        test("it still reads parameters", () => {
            // The control: the handler resolves the Location Service key ARN and the deployed web URL
            // from SSM, so a synth with no statement at all would satisfy the narrowing assertions.
            expect(ssmStatements().length).toBeGreaterThan(0);
        });

        test("no parameter resource is a name wildcard over the deployment", () => {
            // The old resource was `*<config.name>*`, which reaches unrelated parameters in the same
            // account. Asserted as "the resource names a concrete path", since the exact ARN is a
            // partition-aware join and comparing the whole string would pin the join's shape.
            for (const statement of ssmStatements()) {
                const resources = (
                    Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource]
                ).map((r: unknown) => SynthResult.flatten(r));
                expect(resources.length).toBeGreaterThan(0);
                for (const resource of resources) {
                    expect(resource).toContain("parameter/");
                    // A trailing or leading wildcard segment is what the finding is about.
                    expect(resource).not.toMatch(/parameter\/\*/);
                    expect(resource).not.toMatch(/\*$/);
                }
            }
        });

        test("both parameters the handler reads are covered", () => {
            // Narrowing to one of the two would break the other lookup at runtime with an
            // AccessDenied that reads as a missing parameter.
            const resources = ssmStatements()
                .flatMap((s) => (Array.isArray(s.Resource) ? s.Resource : [s.Resource]))
                .map((r: unknown) => SynthResult.flatten(r))
                .join(" ");
            expect(resources).toMatch(/location\/apiKeyArn/i);
            expect(resources).toMatch(/web\/deployedUrl/i);
        });

        test("geo:DescribeKey IS granted when Location Service is on", () => {
            // The commercial template ships Location Service ENABLED, so this is the shipped state and
            // the paired positive: removing the statement outright would satisfy the absence test below
            // while breaking the map URL the web front requests.
            expect(actionsOf(statementsForRole(/configService.*ServiceRole/))).toContain(
                "geo:DescribeKey"
            );
        });

        test("geo:DescribeKey is absent when Location Service is off", () => {
            // Which is every restricted-partition deployment - Location Service is not offered there,
            // so the grant was permanently dead in exactly the deployments most likely to be audited.
            const govcloud = synthTemplate("govcloud");
            const roleIds = govcloud
                .ofType("AWS::IAM::Role")
                .filter((r) => /configService.*ServiceRole/.test(r.logicalId))
                .map((r) => r.logicalId);
            // Control: the handler must exist there, or the absence proves nothing.
            expect(roleIds.length).toBeGreaterThan(0);

            const actions = govcloud.resources
                .filter((r) => /IAM::(Policy|ManagedPolicy)$/.test(r.type))
                .filter((p) =>
                    (((p.properties as any).Roles ?? []) as unknown[]).some((ref) =>
                        roleIds.some((id) => JSON.stringify(ref).includes(id))
                    )
                )
                .flatMap((p) => ((p.properties as any).PolicyDocument?.Statement ?? []) as any[])
                .flatMap((st) =>
                    (Array.isArray(st.Action) ? st.Action : [st.Action]).filter(Boolean)
                );
            expect(actions).not.toContain("geo:DescribeKey");
        });
    });

    describe("state machine tracing is actually recorded (S1-INFRA-043)", () => {
        /** The role the workflow and pipeline state machines assume. */
        const workflowRolePattern = /^VAMSWorkflowIAMRole/;

        test("the state machines really do enable tracing", () => {
            // The premise. If tracing were off, granting X-Ray would be the wrong fix and this suite
            // would be enforcing an unnecessary permission.
            const source = fs.readFileSync(
                path.resolve(
                    __dirname,
                    "../../../backend/backend/common/workflows/stepfunctions_builder.py"
                ),
                "utf-8"
            );
            expect(source).toMatch(/tracingConfiguration=\{\s*['"]enabled['"]:\s*True/);
        });

        test("the workflow role can write trace segments", () => {
            const actions = actionsOf(statementsForRole(workflowRolePattern));
            expect(actions).toContain("xray:PutTraceSegments");
            expect(actions).toContain("xray:PutTelemetryRecords");
        });

        test("it can also read the sampling rules, without which the SDK cannot sample", () => {
            const actions = actionsOf(statementsForRole(workflowRolePattern));
            expect(actions).toContain("xray:GetSamplingRules");
            expect(actions).toContain("xray:GetSamplingTargets");
        });
    });

    describe("the registration rule has a dead-letter queue (S1-INFRA-044)", () => {
        /** The EventBridge rule that routes pipeline.execution.register events. */
        function registerRule() {
            const rules = synth
                .ofType("AWS::Events::Rule")
                .filter((r) =>
                    JSON.stringify(r.properties).includes("pipeline.execution.register")
                );
            expect(rules.length).toBe(1);
            return rules[0];
        }

        test("the rule IS in this synth", () => {
            // The control for the two assertions below, both of which inspect its targets.
            expect(registerRule()).toBeDefined();
        });

        test("its target retries and then dead-letters", () => {
            const targets = ((registerRule().properties as any).Targets ?? []) as any[];
            expect(targets.length).toBe(1);
            expect(targets[0].RetryPolicy?.MaximumRetryAttempts).toBe(3);
            expect(targets[0].DeadLetterConfig?.Arn).toBeDefined();
        });

        test("the dead-letter queue is encrypted and TLS-only", () => {
            // It holds the registration payload for executions that cannot be aborted until an
            // operator redrives it, so it is not a throwaway queue.
            const dlqArn = JSON.stringify(
                (((registerRule().properties as any).Targets ?? []) as any[])[0].DeadLetterConfig
                    .Arn
            );
            const queue = synth.ofType("AWS::SQS::Queue").find((q) => dlqArn.includes(q.logicalId));
            expect(queue).toBeDefined();
            const props = queue!.properties as any;
            expect(props.KmsMasterKeyId ?? props.SqsManagedSseEnabled).toBeDefined();

            const policies = synth
                .ofType("AWS::SQS::QueuePolicy")
                .filter((p) => JSON.stringify(p.properties).includes(queue!.logicalId));
            expect(JSON.stringify(policies.map((p) => p.properties))).toContain("SecureTransport");
        });
    });
});
