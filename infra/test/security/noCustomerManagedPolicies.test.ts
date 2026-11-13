/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * VAMS creates no customer managed IAM policy, in any template it synthesizes.
 *
 * A stack-created managed policy complicates a deployment in a customer account: some organizations
 * restrict who may create one, pre-create roles, or apply permission boundaries and policy-count limits
 * that it runs into. VAMS uses role inline policies, resource-based policies, and AWS managed policies
 * (infra/CLAUDE.md, "No customer managed policies"). A role that outgrows IAM's inline quota is split by
 * route instead (the execution service functions, lambdaBuilder/workflowFunctions.ts).
 *
 * The check is on the resource type, so it also catches the AWS::IAM::ManagedPolicy "OverflowPolicy"
 * CDK creates on its own when a role's default policy outgrows CDK's size estimate. The cells are the
 * shipped templates, the commercial worst case for policy size (a customer managed key, the Deadline
 * Cloud execution type and every Lambda in the VPC), and the commercial template with every optional
 * pipeline enabled, so a pipeline construct cannot add one unseen.
 *
 * A policy VAMS code creates explicitly (iam.ManagedPolicy, or addManagedPolicy() / managedPolicies:
 * with a stack-created policy) is never allowed and cannot be waived. A CDK-generated OverflowPolicy is
 * the only kind that may appear, and only as a tracked, owner-waived exception named in
 * WAIVED_OVERFLOW_POLICIES; it is not a pattern to copy.
 */

import { SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";

jest.setTimeout(900_000);

interface Cell {
    name: string;
    template: TemplateName;
    mutate?: (c: any) => void;
}

/** Every optional pipeline switch in the config's pipelines block (`use*` objects with `enabled`). */
function enableEveryPipeline(c: any) {
    for (const value of Object.values(c.app.pipelines ?? {}) as any[]) {
        if (value && typeof value === "object" && "enabled" in value) {
            value.enabled = true;
        }
    }
    // The splat toolbox Dockerfile is not in the repository; its image comes from CodeBuild instead
    // (templateSynth.ts, UNTRACKED_DOCKER_ASSET_PIPELINES).
    c.app.pipelines.useSplatToolbox.useCodeBuild = true;
}

const CELLS: Cell[] = [
    { name: "commercial", template: "commercial" },
    { name: "govcloud", template: "govcloud" },
    { name: "eusovereign", template: "eusovereign" },
    {
        name: "commercial-worst",
        template: "commercial",
        mutate: (c) => {
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
            c.app.useGlobalVpc.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
        },
    },
    {
        name: "commercial-every-pipeline",
        template: "commercial",
        mutate: (c) => {
            c.app.useGlobalVpc.enabled = true;
            enableEveryPipeline(c);
        },
    },
];

interface WaivedOverflowPolicy {
    /** Logical id of the CDK-generated overflow policy (CDK names it <role>OverflowPolicy<n><hash>). */
    logicalId: RegExp;
    /** Why it is waived, with the tracking issue and the owner's waiver. */
    reason: string;
}

/**
 * CDK-generated overflow policies that are tracked, owner-waived exceptions. Only an OverflowPolicy may
 * be listed (an explicitly created managed policy is never waived), and the list may only shrink
 * (MAX_WAIVED). Remove an entry once its role no longer overflows.
 */
const WAIVED_OVERFLOW_POLICIES: WaivedOverflowPolicy[] = [
    {
        logicalId: /^assetServiceServiceRoleOverflowPolicy[0-9]+[0-9A-F]{8}$/,
        reason:
            "#1264: the asset service role is above IAM's inline quota once CDK's overflow policy is folded " +
            "back in (iamInlinePolicyBudget.test.ts, BUDGET_EXCEPTIONS) and needs a route split; deferred " +
            "and waived as an exception by the owner (2026-11-10).",
    },
];
const MAX_WAIVED = 1;

test("the waived list does not grow", () => {
    expect(WAIVED_OVERFLOW_POLICIES.length).toBeLessThanOrEqual(MAX_WAIVED);
});

test("only CDK-generated overflow policies can be waived, each with a tracked reason", () => {
    for (const w of WAIVED_OVERFLOW_POLICIES) {
        expect(w.logicalId.source).toMatch(/OverflowPolicy\[0-9\]\+\[0-9A-F\]\{8\}\$$/);
        expect(w.reason).toMatch(/#\d+/);
        expect(w.reason).toMatch(/waived/);
    }
});

const isWaived = (id: string) => WAIVED_OVERFLOW_POLICIES.some((w) => w.logicalId.test(id));

const synthCell = (cell: Cell): SynthResult =>
    cell.mutate
        ? synthTemplate(cell.template, { mutate: cell.mutate, mutateKey: cell.name })
        : synthTemplate(cell.template);

describe.each(CELLS)("no customer managed policies ($name)", (cell) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthCell(cell);
    });

    test("the synth emits IAM roles with inline policies (control)", () => {
        expect(synth.ofType("AWS::IAM::Role").length).toBeGreaterThan(50);
        expect(synth.ofType("AWS::IAM::Policy").length).toBeGreaterThan(50);
    });

    test("no template creates an AWS::IAM::ManagedPolicy beyond the waived overflow", () => {
        const created = synth
            .ofType("AWS::IAM::ManagedPolicy")
            .map((p) => p.logicalId)
            .filter((id) => !isWaived(id));
        expect(created).toEqual([]);
    });

    test("each waived overflow policy is still synthesized (remove it from the list once it is gone)", () => {
        const ids = synth.ofType("AWS::IAM::ManagedPolicy").map((p) => p.logicalId);
        expect(
            WAIVED_OVERFLOW_POLICIES.filter((w) => !ids.some((id) => w.logicalId.test(id))).map(
                (w) => w.reason
            )
        ).toEqual([]);
    });

    test("every managed policy a role or user names is an AWS managed policy", () => {
        // An AWS managed policy is named as arn:<partition>:iam::aws:policy/<name> (the partition a
        // Ref to AWS::Partition); any other entry would be a stack-created or customer policy.
        const named = [
            ...synth.ofType("AWS::IAM::Role"),
            ...synth.ofType("AWS::IAM::User"),
            ...synth.ofType("AWS::IAM::Group"),
        ].flatMap((r) =>
            ((r.properties.ManagedPolicyArns ?? []) as any[]).map(
                (arn) => [`${r.stack} ${r.logicalId}`, JSON.stringify(arn)] as [string, string]
            )
        );
        expect(named.length).toBeGreaterThan(0);
        const notAwsManaged = named.filter(([, arn]) => !/:iam::aws:policy\//.test(arn));
        expect(notAwsManaged).toEqual([]);
    });
});
