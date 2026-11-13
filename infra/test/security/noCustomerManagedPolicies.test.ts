/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * VAMS creates no customer managed IAM policy of its own, in any template it synthesizes (#1499).
 *
 * A stack-created managed policy complicates a deployment in a customer account: some organizations
 * restrict who may create one, pre-create roles, or apply permission boundaries and policy-count limits
 * that it runs into. VAMS grants roles inline, with resource-based policies, and with AWS managed
 * policies (infra/CLAUDE.md, "No customer managed policies").
 *
 * The one kind allowed is the overflow policy CDK generates itself (aws-iam Role.splitLargePolicy):
 * when a role's default policy outgrows CDK's size estimate, CDK moves statements into
 * `AWS::IAM::ManagedPolicy` resources it names `OverflowPolicy<n>` under the role, attached through the
 * policy's own `Roles`. cdk.json sets `@aws-cdk/aws-iam.arnSizeEstimate` so that estimate splits a
 * default policy before its deployed size nears IAM's 10,240-byte inline limit in every partition.
 * A policy VAMS code creates (`new iam.ManagedPolicy`, or `addManagedPolicy()` / `managedPolicies:`
 * with a stack-created policy) fails here. The cells are the shipped templates, the commercial worst
 * case for policy size (a customer managed key, the Deadline Cloud execution type and every Lambda in
 * the VPC), and the commercial template with every optional pipeline enabled.
 */

import cdkJson from "../../cdk.json";
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

/** CDK's generated overflow policy: logical id `<role id>OverflowPolicy<n><8 hex>`. */
const OVERFLOW_LOGICAL_ID = /OverflowPolicy\d+[0-9A-F]{8}$/;

/** The construct path CDK gives it, under the role it splits: `.../<role>/OverflowPolicy<n>/Resource`. */
const OVERFLOW_PATH = /\/OverflowPolicy\d+\/Resource$/;

/** Whether a managed policy resource is CDK's own overflow of a role in the same stack. */
function isCdkOverflow(synth: SynthResult, policy: any): boolean {
    if (!OVERFLOW_LOGICAL_ID.test(policy.logicalId)) return false;
    const path = (policy.raw?.Metadata ?? {})["aws:cdk:path"];
    if (path !== undefined && !OVERFLOW_PATH.test(String(path))) return false;
    const roles = (policy.properties.Roles ?? []) as any[];
    if (roles.length !== 1 || typeof roles[0]?.Ref !== "string") return false;
    const roleId: string = roles[0].Ref;
    // CDK names the overflow after the role it splits.
    if (!policy.logicalId.startsWith(roleId.replace(/[0-9A-F]{8}$/, ""))) return false;
    return synth
        .ofType("AWS::IAM::Role")
        .some((r) => r.logicalId === roleId && r.stack === policy.stack);
}

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

    test("every AWS::IAM::ManagedPolicy is a CDK-generated overflow policy", () => {
        const created = synth
            .ofType("AWS::IAM::ManagedPolicy")
            .filter((p) => !isCdkOverflow(synth, p))
            .map((p) => `${p.stack} ${p.logicalId}`);
        expect(created).toEqual([]);
    });

    test("no role holds more than two overflow policies (IAM attaches at most 10 per role)", () => {
        const perRole = new Map<string, number>();
        for (const p of synth.ofType("AWS::IAM::ManagedPolicy")) {
            const role = JSON.stringify((p.properties.Roles ?? [])[0] ?? "");
            perRole.set(role, (perRole.get(role) ?? 0) + 1);
        }
        expect(Math.max(0, ...Array.from(perRole.values()))).toBeLessThanOrEqual(2);
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

describe("the overflow check tells CDK's overflow from a hand-written policy (control)", () => {
    const role = { logicalId: "fooServiceRole12345678", stack: "s", type: "AWS::IAM::Role" };
    const fake = (policies: any[]) =>
        ({ ofType: (t: string) => (t === "AWS::IAM::Role" ? [role] : policies) } as any);
    const policy = (logicalId: string, path: string, roles: any[] = [{ Ref: role.logicalId }]) => ({
        logicalId,
        stack: "s",
        properties: { Roles: roles },
        raw: { Metadata: { "aws:cdk:path": path } },
    });

    test("CDK's overflow of a role passes", () => {
        const p = policy(
            "fooServiceRoleOverflowPolicy1ABCDEF12",
            "core/Api/foo/ServiceRole/OverflowPolicy1/Resource"
        );
        expect(isCdkOverflow(fake([p]), p)).toBe(true);
    });

    test("a hand-written managed policy fails", () => {
        const p = policy("fooTableReadPolicyABCDEF12", "core/Api/foo/TableReadPolicy/Resource");
        expect(isCdkOverflow(fake([p]), p)).toBe(false);
    });

    test("a policy named like an overflow but placed elsewhere fails", () => {
        const p = policy(
            "fooServiceRoleOverflowPolicy1ABCDEF12",
            "core/Api/foo/OverflowPolicy1x/Resource"
        );
        expect(isCdkOverflow(fake([p]), p)).toBe(false);
    });

    test("an overflow-named policy attached to another role, or to none, fails", () => {
        const other = policy(
            "fooServiceRoleOverflowPolicy1ABCDEF12",
            "core/Api/foo/ServiceRole/OverflowPolicy1/Resource",
            [{ Ref: "barServiceRole87654321" }]
        );
        const none = policy(
            "fooServiceRoleOverflowPolicy1ABCDEF12",
            "core/Api/foo/ServiceRole/OverflowPolicy1/Resource",
            []
        );
        expect(isCdkOverflow(fake([other]), other)).toBe(false);
        expect(isCdkOverflow(fake([none]), none)).toBe(false);
    });
});

test("cdk.json sets the ARN size estimate CDK's overflow split uses, for every stack and partition", () => {
    // App-wide context, so every template (commercial, GovCloud, EU Sovereign) splits on it.
    expect((cdkJson as any).context["@aws-cdk/aws-iam:minimizePolicies"]).toBe(true);
    expect((cdkJson as any).context["@aws-cdk/aws-iam.arnSizeEstimate"]).toBeGreaterThanOrEqual(
        280
    );
});
