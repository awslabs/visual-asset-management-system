/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The concurrency lock table reaches exactly the four handlers that take or release its rows, with
 * exactly the DynamoDB actions the lock code issues, in every shipped config template.
 *
 * The table lives in the storage nested stack while the handlers live in the API stack, so a statement's
 * Resource carries the cross-stack parameter reference whose NAME embeds the table's construct id; the
 * match is on that name (the same reason handlerGrantScopingWave.test.ts matches on `AssetStorageTable`).
 * Statements are read from inline policies, managed-policy overflow and role-inline documents alike.
 */

import { EXECUTION_LOCK_POLICY_ID } from "../../lib/lambdaBuilder/workflowFunctions";
import { ALL_TEMPLATES, SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const LOCK_TABLE = /WorkflowExecutionLocksStorageTable/;

/** Role logical-id prefix -> the exact lock-table actions that role holds. */
const EXPECTED: Record<string, string[]> = {
    // Takes the locks (conditional PutItem) and rolls them back on a conflict or a failed launch.
    executeWorkflowServiceRole: ["dynamodb:DeleteItem", "dynamodb:PutItem"],
    // Release only (the end-state step, the error handler, and abort / reconcile on read): list the
    // execution's rows through the holder index and delete them.
    processWorkflowExecutionOutputServiceRole: ["dynamodb:DeleteItem", "dynamodb:Query"],
    handleExecutionErrorServiceRole: ["dynamodb:DeleteItem", "dynamodb:Query"],
    executionServiceServiceRole: ["dynamodb:DeleteItem", "dynamodb:Query"],
};
const HOLDER_INDEX = "WorkflowExecutionLocksByHolderGSI";

const actionsOf = (statements: any[]): string[] =>
    statements.flatMap((s) => (Array.isArray(s.Action) ? s.Action : [s.Action]).filter(Boolean));

/** Every IAM statement in the synth, tagged with the role logical ids its policy attaches to. */
function statementsByRole(synth: SynthResult): Map<string, any[]> {
    const byRole = new Map<string, any[]>();
    const roleIds = synth.ofType("AWS::IAM::Role").map((r) => r.logicalId);
    const add = (roleId: string, statements: any[]) =>
        byRole.set(roleId, [...(byRole.get(roleId) ?? []), ...statements]);

    for (const policy of synth.resources.filter((r) =>
        /IAM::(Policy|ManagedPolicy)$/.test(r.type)
    )) {
        const roles = JSON.stringify((policy.properties as any).Roles ?? []);
        const statements = ((policy.properties as any).PolicyDocument?.Statement ?? []) as any[];
        for (const roleId of roleIds.filter((id) => roles.includes(id))) {
            add(roleId, statements);
        }
    }
    for (const role of synth.ofType("AWS::IAM::Role")) {
        const inline = (((role.properties as any).Policies ?? []) as any[]).flatMap(
            (doc) => (doc.PolicyDocument?.Statement ?? []) as any[]
        );
        add(role.logicalId, inline);
    }
    return byRole;
}

/** role logical id -> sorted distinct actions it holds on the lock table (roles with none omitted). */
function lockGrants(synth: SynthResult): Map<string, string[]> {
    const grants = new Map<string, string[]>();
    for (const [roleId, statements] of statementsByRole(synth)) {
        const onLock = statements.filter((st) =>
            LOCK_TABLE.test(JSON.stringify(st.Resource ?? ""))
        );
        if (onLock.length) {
            grants.set(roleId, [...new Set(actionsOf(onLock))].sort());
        }
    }
    return grants;
}

/**
 * Every shipped template, plus the commercial template with a customer managed key and the commercial
 * worst case (key, Deadline Cloud, every Lambda in the VPC): the cells where roles are largest and CDK
 * is most likely to split a default policy into overflow policies.
 */
interface LockCell {
    label: string;
    template: TemplateName;
    mutate?: (c: any) => void;
}
const LOCK_CELLS: LockCell[] = [
    ...ALL_TEMPLATES.map((t) => ({ label: t, template: t })),
    {
        label: "commercial-cmk",
        template: "commercial" as TemplateName,
        mutate: (c: any) => {
            c.app.useKmsCmkEncryption.enabled = true;
        },
    },
    {
        label: "commercial-worst",
        template: "commercial" as TemplateName,
        mutate: (c: any) => {
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
            c.app.useGlobalVpc.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
        },
    },
];

describe.each(LOCK_CELLS)("concurrency lock table grants ($label template)", (cell: LockCell) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = cell.mutate
            ? synthTemplate(cell.template, { mutate: cell.mutate, mutateKey: cell.label })
            : synthTemplate(cell.template);
    });

    test("the lock table is in this synth", () => {
        // Control for every assertion below: a template without the table satisfies "no grant" trivially.
        expect(
            synth.where("AWS::DynamoDB::Table", (r) => LOCK_TABLE.test(r.logicalId))
        ).toHaveLength(1);
    });

    test("exactly the four lock handlers hold a grant on the table, with exactly the lock actions", () => {
        const grants = lockGrants(synth);
        const byPrefix: Record<string, string[]> = {};
        for (const [roleId, actions] of grants) {
            const prefix = Object.keys(EXPECTED).find((p) => roleId.startsWith(p)) ?? roleId;
            byPrefix[prefix] = actions;
        }
        expect(byPrefix).toEqual(EXPECTED);
    });

    test("Query on the lock table reaches the holder index only, never the table itself", () => {
        // CDK merges statements with the same actions, so one Query statement may also carry another
        // table's ARN; only the entries that name the lock table are checked.
        const rolesQueryingTheIndex = new Set<string>();
        for (const [roleId, statements] of statementsByRole(synth)) {
            for (const st of statements) {
                if (!actionsOf([st]).includes("dynamodb:Query")) {
                    continue;
                }
                const onLock = ([] as any[])
                    .concat(st.Resource ?? [])
                    .map((entry) => JSON.stringify(entry))
                    .filter((entry) => LOCK_TABLE.test(entry));
                for (const entry of onLock) {
                    expect(entry).toContain(`/index/${HOLDER_INDEX}`);
                    rolesQueryingTheIndex.add(roleId);
                }
            }
        }
        expect(rolesQueryingTheIndex.size).toBe(3);
    });

    test("PutItem and DeleteItem reach the lock table itself, never an index", () => {
        let entriesChecked = 0;
        for (const statements of statementsByRole(synth).values()) {
            for (const st of statements) {
                const actions = actionsOf([st]);
                if (!actions.some((a) => a === "dynamodb:PutItem" || a === "dynamodb:DeleteItem")) {
                    continue;
                }
                const onLock = ([] as any[])
                    .concat(st.Resource ?? [])
                    .map((entry) => JSON.stringify(entry))
                    .filter((entry) => LOCK_TABLE.test(entry));
                for (const entry of onLock) {
                    expect(entry).not.toContain("/index/");
                    entriesChecked += 1;
                }
            }
        }
        expect(entriesChecked).toBeGreaterThan(0);
    });

    test("no grant on the lock table uses a wildcard action", () => {
        for (const actions of lockGrants(synth).values()) {
            expect(actions.filter((a) => a.includes("*"))).toEqual([]);
        }
    });

    test("every lock-table statement lives in a lock policy, never a role's default or overflow policy", () => {
        // A statement added to a role's default policy can move existing statements into an overflow
        // policy, and CloudFormation updates the two separately during a deploy.
        const holders = synth.resources
            .filter((r) => /IAM::(Policy|ManagedPolicy)$/.test(r.type))
            .filter((r) =>
                (((r.properties as any).PolicyDocument?.Statement ?? []) as any[]).some((st) =>
                    LOCK_TABLE.test(JSON.stringify(st.Resource ?? ""))
                )
            )
            .map((r) => r.logicalId);
        expect(holders).toHaveLength(Object.keys(EXPECTED).length);
        // Each is a role-inline AWS::IAM::Policy (the lock policy), never an AWS::IAM::ManagedPolicy,
        // so CDK's overflow, which only splits a role's default policy, cannot move its statements.
        for (const r of synth.resources.filter((x) => holders.includes(x.logicalId))) {
            expect(r.type).toBe("AWS::IAM::Policy");
        }
        for (const logicalId of holders) {
            expect(logicalId).toMatch(new RegExp(`^[A-Za-z]+${EXECUTION_LOCK_POLICY_ID}`));
            expect(logicalId).not.toMatch(/DefaultPolicy|OverflowPolicy/);
        }
        const inlineOnRoles = synth
            .ofType("AWS::IAM::Role")
            .flatMap((role) => ((role.properties as any).Policies ?? []) as any[])
            .flatMap((doc) => (doc.PolicyDocument?.Statement ?? []) as any[])
            .filter((st) => LOCK_TABLE.test(JSON.stringify(st.Resource ?? "")));
        expect(inlineOnRoles).toEqual([]);
    });
});
