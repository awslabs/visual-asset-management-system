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

import { ALL_TEMPLATES, SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const LOCK_TABLE = /WorkflowExecutionLocksStorageTable/;

/** Role logical-id prefix -> the exact lock-table actions that role holds. */
const EXPECTED: Record<string, string[]> = {
    // Takes the locks (conditional PutItem) and rolls them back on a conflict or a failed launch.
    executeWorkflowServiceRole: ["dynamodb:DeleteItem", "dynamodb:PutItem"],
    // Release only: the end-state step, the error handler, and abort / reconcile on read.
    processWorkflowExecutionOutputServiceRole: ["dynamodb:DeleteItem"],
    handleExecutionErrorServiceRole: ["dynamodb:DeleteItem"],
    executionServiceServiceRole: ["dynamodb:DeleteItem"],
};

const actionsOf = (statements: any[]): string[] =>
    statements.flatMap((s) => (Array.isArray(s.Action) ? s.Action : [s.Action]).filter(Boolean));

/** Every IAM statement in the synth, tagged with the role logical ids its policy attaches to. */
function statementsByRole(synth: SynthResult): Map<string, any[]> {
    const byRole = new Map<string, any[]>();
    const roleIds = synth.ofType("AWS::IAM::Role").map((r) => r.logicalId);
    const add = (roleId: string, statements: any[]) =>
        byRole.set(roleId, [...(byRole.get(roleId) ?? []), ...statements]);

    for (const policy of synth.resources.filter((r) => /IAM::(Policy|ManagedPolicy)$/.test(r.type))) {
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
        const onLock = statements.filter((st) => LOCK_TABLE.test(JSON.stringify(st.Resource ?? "")));
        if (onLock.length) {
            grants.set(roleId, [...new Set(actionsOf(onLock))].sort());
        }
    }
    return grants;
}

describe.each(ALL_TEMPLATES)("concurrency lock table grants (%s template)", (name: TemplateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(name);
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

    test("no grant on the lock table uses a wildcard action", () => {
        for (const actions of lockGrants(synth).values()) {
            expect(actions.filter((a) => a.includes("*"))).toEqual([]);
        }
    });
});
