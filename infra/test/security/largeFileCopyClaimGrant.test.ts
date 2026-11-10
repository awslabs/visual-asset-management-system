/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The large-file worker's copy-claim grant (#1271).
 *
 * `handlers/assets/sqsUploadFileLarge.py` copies a workflow run's output under a claim on its row in
 * the run's ingest ledger (`PipelineExecutionOutputFilesStorageTable`, partition
 * `ingest#<workflowExecutionId>`): a conditional `UpdateItem` takes, finalizes or releases the claim
 * and a `GetItem` reads a refused claim's state. The worker's role holds exactly those two actions,
 * inline, on that table's ARN only: no wildcard action, no index, no other table, no managed policy.
 * Asserted in every shipped template.
 */

import { ALL_TEMPLATES, Resource, SynthResult, synthTemplate } from "../support/templateSynth";

const HANDLER = "handlers.assets.sqsUploadFileLarge.lambda_handler";
const TABLE = "PipelineExecutionOutputFilesStorageTable";

interface Statement {
    Effect?: string;
    Action?: unknown;
    Resource?: unknown;
}

/** The inline statements of a role (AWS::IAM::Policy resources and the role's own Policies). */
function inlineStatements(s: SynthResult, stack: string, roleLogicalId: string): Statement[] {
    const out: Statement[] = [];
    for (const r of s.resources.filter((x) => x.stack === stack)) {
        if (
            r.type === "AWS::IAM::Policy" &&
            JSON.stringify(r.properties.Roles).includes(roleLogicalId)
        ) {
            out.push(...(r.properties.PolicyDocument?.Statement ?? []));
        }
        if (r.type === "AWS::IAM::Role" && r.logicalId === roleLogicalId) {
            for (const p of r.properties.Policies ?? [])
                out.push(...(p.PolicyDocument?.Statement ?? []));
        }
    }
    return out;
}

function managedPolicies(s: SynthResult, stack: string, roleLogicalId: string): Resource[] {
    return s.resources.filter(
        (r) =>
            r.stack === stack &&
            r.type === "AWS::IAM::ManagedPolicy" &&
            JSON.stringify(r.properties.Roles ?? []).includes(roleLogicalId)
    );
}

const asList = (v: unknown): unknown[] => (Array.isArray(v) ? v : [v]);

describe.each(ALL_TEMPLATES)("large-file worker copy-claim grant (%s)", (template) => {
    const s = synthTemplate(template);
    const fn = s.ofType("AWS::Lambda::Function").find((r) => r.properties.Handler === HANDLER);
    const role = String(fn?.properties.Role?.["Fn::GetAtt"]?.[0]);
    const stack = String(fn?.stack);

    test("the worker Lambda is emitted (control)", () => {
        expect(fn).toBeDefined();
    });

    test("its role holds GetItem and UpdateItem on the output-files table, and nothing more on it", () => {
        const onTable = inlineStatements(s, stack, role).filter((st) =>
            JSON.stringify(st.Resource ?? "").includes(TABLE)
        );
        expect(onTable).toHaveLength(1);
        const [statement] = onTable;
        expect(statement.Effect).toBe("Allow");
        expect([...(asList(statement.Action) as string[])].sort()).toEqual([
            "dynamodb:GetItem",
            "dynamodb:UpdateItem",
        ]);
        // The table ARN itself: no index ARN, no wildcard suffix.
        const resources = asList(statement.Resource);
        expect(resources).toHaveLength(1);
        expect(JSON.stringify(resources[0])).not.toMatch(/index|\*/);
        expect(JSON.stringify(resources[0])).toContain(TABLE);
    });

    test("no managed policy on the role references the table", () => {
        const managed = managedPolicies(s, stack, role).filter((p) =>
            JSON.stringify(p.properties.PolicyDocument).includes(TABLE)
        );
        expect(managed).toEqual([]);
    });
});
