/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A compliance pipeline rule whose launch the workflow's concurrency lock refuses waits for the
 * running execution to end (D113). Its tracking row on `ComplianceEvaluationStorageTable` carries
 * `waitingWorkflowKey` (`<workflowDatabaseId>:<workflowId>`) and `waitingSince` only while it waits,
 * so `PipelineRuleWaitIndex` is sparse: the workflow callback queries it by workflow, oldest first,
 * on every `workflow.execution.completed` event, and launches the waiting rules over the assets the
 * ended execution read through the execute-workflow Lambda.
 *
 * Pinned here: the index (its name is queried at runtime, so a rename is a `ValidationException`,
 * not a synth error), its key order and ALL projection (the callback reads the rule's asset and
 * expiry from the index entry), the table's encryption that the index shares, and the callback's two
 * new permissions: the read of the execution input rows (by the table-grants test) and the invoke of
 * the execute Lambda its `EXECUTE_WORKFLOW_FUNCTION_NAME` names.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const synth = () => synthTemplate("commercial");

const EVALUATION_TABLE_ID = /ComplianceEvaluationStorageTable/;
const CALLBACK_HANDLER = "handlers.compliance.complianceWorkflowCallback.lambda_handler";

type KeyElement = { AttributeName: string; KeyType: string };
type Gsi = { IndexName: string; KeySchema: KeyElement[]; Projection: { ProjectionType: string } };
type AttributeDefinition = { AttributeName: string; AttributeType: string };

function evaluationTable(s: SynthResult): Resource {
    const matches = s.where("AWS::DynamoDB::Table", (r) => EVALUATION_TABLE_ID.test(r.logicalId));
    expect(matches).toHaveLength(1);
    return matches[0];
}

function callbackFunction(s: SynthResult): Resource {
    const matches = s.where(
        "AWS::Lambda::Function",
        (r) => r.properties.Handler === CALLBACK_HANDLER
    );
    expect(matches).toHaveLength(1);
    return matches[0];
}

function statementsOfRole(s: SynthResult, fn: Resource): any[] {
    const roleId = fn.properties.Role["Fn::GetAtt"][0];
    return s
        .where(
            "AWS::IAM::Policy",
            (p) =>
                p.stack === fn.stack &&
                ((p.properties.Roles as any[]) || []).some((role) => role.Ref === roleId)
        )
        .flatMap((p) => p.properties.PolicyDocument.Statement as any[]);
}

describe("ComplianceEvaluationStorageTable wait index", () => {
    test("[control] the table carries AssetIndex and ExecutionIdIndex", () => {
        const names = (evaluationTable(synth()).properties.GlobalSecondaryIndexes as Gsi[]).map(
            (g) => g.IndexName
        );
        expect(names).toEqual(expect.arrayContaining(["AssetIndex", "ExecutionIdIndex"]));
    });

    test("carries exactly AssetIndex, ExecutionIdIndex and PipelineRuleWaitIndex", () => {
        const names = (evaluationTable(synth()).properties.GlobalSecondaryIndexes as Gsi[]).map(
            (g) => g.IndexName
        );
        expect(names.sort()).toEqual(["AssetIndex", "ExecutionIdIndex", "PipelineRuleWaitIndex"]);
    });

    test("PipelineRuleWaitIndex keys on (waitingWorkflowKey, waitingSince) with an ALL projection", () => {
        const gsi = (evaluationTable(synth()).properties.GlobalSecondaryIndexes as Gsi[]).find(
            (g) => g.IndexName === "PipelineRuleWaitIndex"
        ) as Gsi;
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "waitingWorkflowKey", KeyType: "HASH" },
            { AttributeName: "waitingSince", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({ ProjectionType: "ALL" });
        const declared = Object.fromEntries(
            (evaluationTable(synth()).properties.AttributeDefinitions as AttributeDefinition[]).map(
                (a) => [a.AttributeName, a.AttributeType]
            )
        );
        expect(declared).toMatchObject({ waitingWorkflowKey: "S", waitingSince: "S" });
    });

    test("the index shares the table's server-side encryption, as the other compliance tables", () => {
        const s = synth();
        const sse = evaluationTable(s).properties.SSESpecification;
        expect(sse).toMatchObject({ SSEEnabled: true });
        const audit = s.where("AWS::DynamoDB::Table", (r) =>
            /ComplianceAuditStorageTable/.test(r.logicalId)
        )[0];
        expect(SynthResult.flatten(sse)).toEqual(
            SynthResult.flatten(audit.properties.SSESpecification)
        );
    });
});

describe("compliance workflow callback launches waiting rules", () => {
    test("[control] the callback reads the workflow-execution main rows", () => {
        const s = synth();
        const statements = statementsOfRole(s, callbackFunction(s));
        expect(statements.length).toBeGreaterThan(0);
    });

    test("EXECUTE_WORKFLOW_FUNCTION_NAME names the execute Lambda the callback may invoke", () => {
        const s = synth();
        const fn = callbackFunction(s);
        const named = fn.properties.Environment.Variables.EXECUTE_WORKFLOW_FUNCTION_NAME;
        expect(named).toBeDefined();
        const executeId = named.Ref as string;
        const execute = s.where(
            "AWS::Lambda::Function",
            (r) => r.stack === fn.stack && r.logicalId === executeId
        );
        expect(execute).toHaveLength(1);
        expect(execute[0].properties.Handler).toMatch(/executeWorkflow/);

        const invoke = statementsOfRole(s, fn).filter((st) =>
            ([] as string[]).concat(st.Action).includes("lambda:InvokeFunction")
        );
        expect(invoke).toHaveLength(1);
        expect(invoke[0].Effect).toBe("Allow");
        const resources = SynthResult.flatten(invoke[0].Resource);
        expect(resources).toContain(`\${${executeId}.Arn}`);
        // Only that function (and its versions), nothing wider.
        for (const resource of ([] as any[]).concat(invoke[0].Resource)) {
            expect(SynthResult.flatten(resource)).toContain(executeId);
        }
    });
});
