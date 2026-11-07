/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every API Lambda role stays at or under 90% of IAM's 10,240-character inline policy quota, in every
 * deployment cell that changes a role's policies.
 *
 * IAM refuses a role update whose inline policies add up to more than 10,240 characters, and
 * CloudFormation then rolls the whole stack back. The sizes grow with the deployment: every DynamoDB
 * table ARN carries the stack name, the GovCloud and EU Sovereign partition and Region names are
 * longer, a customer managed key adds KMS statements, and an enabled execution type adds its grants.
 * A role that fits in a commercial synth can therefore fail a GovCloud deploy, which no environment
 * here can try. This test is the check those partitions get.
 *
 * The budget is 9,216 (90%). The margin covers about 35 more stack-name characters across the ~28
 * table ARNs the largest roles reference. A role already above it is named in BUDGET_EXCEPTIONS with
 * a reason and a ceiling of its own; that list may only shrink.
 *
 * Sizes are estimates (see ../support/iamPolicySize.ts). Each cell's table is printed so a PR can
 * quote it; set VAMS_IAM_BUDGET_REPORT to a file path to also write the figures as JSON.
 */

import * as fs from "fs";
import { SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";
import * as seam from "../../lib/lambdaBuilder/systemPipelineCallbacks";
import {
    IAM_INLINE_POLICY_LIMIT,
    IAM_MANAGED_POLICY_LIMIT,
    RoleInlineSize,
    nestedTemplateKeyFor,
    roleInlineSizesInStack,
} from "../support/iamPolicySize";

jest.setTimeout(900_000);

/** 90% of the IAM limit. */
const INLINE_POLICY_BUDGET = 9216;
/** 90% of IAM's per-managed-policy limit, for the customer managed policies these roles carry. */
const MANAGED_POLICY_BUDGET = 5530;

interface Cell {
    name: string;
    template: TemplateName;
    mutate?: (c: any) => void;
    /**
     * Synthesize with one system pipeline terminal callback registered (the shipped registry is
     * empty), so the grant a feature adds is measured on the three terminal-writer roles before any
     * feature registers one. The cell's inline sizes must equal its unregistered twin's.
     */
    registeredCallbackOf?: string;
}

/**
 * The shipped templates, the commercial template with a customer managed key, and a commercial
 * worst case: a customer managed key, the Deadline Cloud execution type (commercial only) and every
 * Lambda in the VPC.
 */
const CELLS: Cell[] = [
    { name: "commercial", template: "commercial" },
    {
        name: "commercial-cmk",
        template: "commercial",
        mutate: (c) => {
            c.app.useKmsCmkEncryption.enabled = true;
        },
    },
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
    { name: "commercial-callback", template: "commercial", registeredCallbackOf: "commercial" },
    { name: "govcloud-callback", template: "govcloud", registeredCallbackOf: "govcloud" },
    { name: "eusovereign-callback", template: "eusovereign", registeredCallbackOf: "eusovereign" },
];

/** The roles of the three functions that run the system pipeline terminal writers. */
const TERMINAL_WRITER_ROLES = [
    "processWorkflowExecutionOutputServiceRole",
    "handleExecutionErrorServiceRole",
    "executionServiceServiceRole",
];

/** The nested stacks whose roles this budget covers: every API Lambda. */
const API_STACKS = ["ApiBuilder", "ApiBuilder2"];

/**
 * Roles above the budget in at least one cell, keyed by the role's logical id prefix (the Lambda's
 * construct id followed by "ServiceRole"). Each has a ceiling it must stay under in every cell, and an
 * entry whose role is back under the budget everywhere fails, so it is removed. Add to this list only
 * with a reason the reviewer accepts; MAX_EXCEPTIONS pins its length.
 */
const BUDGET_EXCEPTIONS: Record<string, { ceiling: number; reason: string }> = {
    processWorkflowExecutionOutputServiceRole: {
        ceiling: 9550,
        reason:
            "The workflow end-state handler writes the output records of every pipeline, so its role " +
            "reads and writes most execution tables; above the budget in the GovCloud and EU Sovereign " +
            "templates only. To be narrowed the way the execution service's role was.",
    },
    assetServiceServiceRole: {
        ceiling: 9300,
        reason:
            "The asset service reads and writes the asset, version and link tables and every asset " +
            "bucket; above the budget in the GovCloud template only. To be narrowed the way the " +
            "execution service's role was.",
    },
};
const MAX_EXCEPTIONS = 2;

/**
 * A synth with one callback registered: the seam is wrapped so the shipped empty registry gains the
 * error handler as a stand-in callback (an Fn::GetAtt ARN in the same nested stack, the shape a
 * feature's callback has). The spy is process-local state, so this synth never uses the shared cache.
 */
function synthWithRegisteredCallback(cell: Cell): SynthResult {
    const original = seam.attachSystemPipelineCallbacks;
    const spy = jest
        .spyOn(seam, "attachSystemPipelineCallbacks")
        .mockImplementation((scope, writers, registry) =>
            original(scope, writers, { ...registry, "Zip-Download": writers[1] })
        );
    try {
        const synth = synthTemplate(cell.template, { mutateKey: cell.name, sharedCache: false });
        expect(spy).toHaveBeenCalledTimes(1);
        return synth;
    } finally {
        spy.mockRestore();
    }
}

const synthCell = (cell: Cell): SynthResult =>
    cell.registeredCallbackOf
        ? synthWithRegisteredCallback(cell)
        : cell.mutate
        ? synthTemplate(cell.template, { mutate: cell.mutate, mutateKey: cell.name })
        : synthTemplate(cell.template);

function exceptionFor(roleId: string): { ceiling: number; reason: string } | undefined {
    const key = Object.keys(BUDGET_EXCEPTIONS).find((prefix) =>
        new RegExp(`^${prefix}[0-9A-F]{8}$`).test(roleId)
    );
    return key ? BUDGET_EXCEPTIONS[key] : undefined;
}

const report: Record<string, Record<string, number>> = {};

afterAll(() => {
    const target = process.env.VAMS_IAM_BUDGET_REPORT;
    if (target) {
        fs.writeFileSync(target, JSON.stringify(report, null, 2));
    }
});

test("the exception list does not grow", () => {
    expect(Object.keys(BUDGET_EXCEPTIONS).length).toBeLessThanOrEqual(MAX_EXCEPTIONS);
});

describe.each(CELLS)("API Lambda role inline policy budget ($name)", (cell) => {
    let sizes: RoleInlineSize[];

    beforeAll(() => {
        const synth = synthCell(cell);
        sizes = API_STACKS.flatMap((construct) =>
            roleInlineSizesInStack(synth, nestedTemplateKeyFor(synth, construct))
        ).sort((a, b) => b.total - a.total);
        report[cell.name] = Object.fromEntries(sizes.map((s) => [s.roleId, s.total]));
        for (const s of sizes) {
            for (const [policyId, size] of s.managed) {
                report[cell.name][`${s.roleId}:${policyId}`] = size;
            }
        }
        console.log(
            `[${cell.name}] API Lambda role inline policy estimates (budget ${INLINE_POLICY_BUDGET}, ` +
                `limit ${IAM_INLINE_POLICY_LIMIT}):\n` +
                sizes
                    .slice(0, 15)
                    .map((s) => `  ${String(s.total).padStart(6)}  ${s.roleId}`)
                    .join("\n")
        );
    });

    test("the API stacks emit Lambda roles with inline policies (control)", () => {
        expect(sizes.length).toBeGreaterThan(40);
        expect(sizes[0].total).toBeGreaterThan(1000);
    });

    test("every role is at or under the budget, or under its named exception's ceiling", () => {
        const over = sizes
            .filter((s) => {
                const exception = exceptionFor(s.roleId);
                return s.total > (exception ? exception.ceiling : INLINE_POLICY_BUDGET);
            })
            .map(
                (s) =>
                    `${cell.name} ${s.roleId}: ${s.total} > ${
                        exceptionFor(s.roleId)?.ceiling ?? INLINE_POLICY_BUDGET
                    }; largest statements: ` +
                    s.largestStatements.map(([n, actions]) => `${n} [${actions}]`).join("; ")
            );
        expect(over).toEqual([]);
    });

    test("every customer managed policy on these roles is at or under its own budget", () => {
        const over = sizes.flatMap((s) =>
            s.managed
                .filter(([, size]) => size > MANAGED_POLICY_BUDGET)
                .map(
                    ([policyId, size]) =>
                        `${cell.name} ${s.roleId} ${policyId}: ${size} > ${MANAGED_POLICY_BUDGET} ` +
                        `(limit ${IAM_MANAGED_POLICY_LIMIT})`
                )
        );
        expect(over).toEqual([]);
    });
});

test.each(CELLS.filter((c) => c.registeredCallbackOf))(
    "a registered callback adds nothing to the terminal writers' inline policies ($name)",
    (cell) => {
        // Runs after the cells above have filled the report.
        const registered = report[cell.name];
        const base = report[cell.registeredCallbackOf!];
        for (const prefix of TERMINAL_WRITER_ROLES) {
            const re = new RegExp(`^${prefix}[0-9A-F]{8}$`);
            const roleId = Object.keys(registered).find((k) => re.test(k));
            expect(roleId).toBeDefined();
            expect(registered[roleId!]).toBe(base[roleId!]);
            // The grant is carried by the one managed policy instead, measured by the cell's
            // managed-policy budget test.
            expect(
                Object.keys(registered).some(
                    (k) =>
                        k.startsWith(`${roleId}:`) &&
                        k.includes(seam.SYSTEM_PIPELINE_CALLBACK_POLICY_ID)
                )
            ).toBe(true);
        }
    }
);

test("every exception is still above the budget in some cell", () => {
    // Runs after the cells above have filled the report.
    const stale = Object.keys(BUDGET_EXCEPTIONS).filter((prefix) => {
        const re = new RegExp(`^${prefix}[0-9A-F]{8}$`);
        return !Object.values(report).some((cell) =>
            Object.entries(cell).some(
                ([roleId, size]) => re.test(roleId) && size > INLINE_POLICY_BUDGET
            )
        );
    });
    expect(stale).toEqual([]);
});
