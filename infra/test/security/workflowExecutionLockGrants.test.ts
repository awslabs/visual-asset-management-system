/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The perInputFileVersion lock table reaches the four handlers that hold or release its rows — and no
 * other workflow handler — in the shipped commercial template.
 *
 * The table lives in the storage nested stack while the handlers live in the API stack, so a statement's
 * Resource carries the cross-stack parameter reference whose NAME embeds the table's construct id; the
 * match is on that name (the same reason handlerGrantScopingWave.test.ts matches on `AssetStorageTable`).
 * Statements are read from inline policies, managed-policy overflow and role-inline documents alike.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

const LOCK_TABLE = /WorkflowExecutionLocksStorageTable/;
const WRITE_ACTIONS = ["dynamodb:PutItem", "dynamodb:DeleteItem"];

describe("perInputFileVersion lock table grants (commercial template)", () => {
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

    const lockStatements = (rolePattern: RegExp): any[] =>
        statementsForRole(rolePattern).filter((st) =>
            LOCK_TABLE.test(JSON.stringify(st.Resource ?? ""))
        );

    test("the lock table is in this synth", () => {
        // Control for every assertion below: a template without the table satisfies "no grant" trivially.
        expect(
            synth.where("AWS::DynamoDB::Table", (r) => LOCK_TABLE.test(r.logicalId))
        ).toHaveLength(1);
    });

    test.each([
        ["executeWorkflow", /^executeWorkflowServiceRole/],
        ["processWorkflowExecutionOutput", /^processWorkflowExecutionOutputServiceRole/],
        ["handleExecutionError", /^handleExecutionErrorServiceRole/],
        ["executionService", /^executionServiceServiceRole/],
    ])("%s holds put and delete on the lock table", (_name, rolePattern) => {
        const statements = lockStatements(rolePattern);
        expect(statements.length).toBeGreaterThan(0);
        const actions = actionsOf(statements);
        for (const write of WRITE_ACTIONS) {
            expect(actions).toContain(write);
        }
    });

    test("interimPipelineTracking holds nothing on the lock table", () => {
        const role = /^interimPipelineTrackingServiceRole/;
        // Control: the same scan does find this role's grants on another execution table.
        expect(
            statementsForRole(role).filter((st) =>
                /PipelineExecutionsStorageTable/.test(JSON.stringify(st.Resource ?? ""))
            ).length
        ).toBeGreaterThan(0);
        expect(lockStatements(role)).toEqual([]);
    });
});
