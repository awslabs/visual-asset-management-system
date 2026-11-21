/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Asserts that a pipeline's pipelineEnd function can end the run's AWS Batch job after a Batch state's
 * own bound fires (`grantBatchStateTimeoutCleanup`): BATCH_JOB_QUEUES names every job queue in the
 * template, and its role holds `batch:ListJobs` on `*` and `batch:TerminateJob` on `job/*`, nothing
 * wider, and no other statement on `*`.
 */

import { Template } from "aws-cdk-lib/assertions";

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

export const expectBatchStateTimeoutCleanup = (template: Template): void => {
    const functions = (
        Object.values(template.findResources("AWS::Lambda::Function")) as any[]
    ).filter((fn) => fn.Properties.Handler === "pipelineEnd.lambda_handler");
    expect(functions).toHaveLength(1);
    const pipelineEnd = functions[0];

    const queues = Object.keys(template.findResources("AWS::Batch::JobQueue"));
    expect(queues.length).toBeGreaterThan(0);
    const queuesEnv = JSON.stringify(pipelineEnd.Properties.Environment.Variables.BATCH_JOB_QUEUES);
    for (const queue of queues) {
        expect([queue, queuesEnv.includes(queue)]).toEqual([queue, true]);
    }

    const roleRef = pipelineEnd.Properties.Role["Fn::GetAtt"][0];
    const statements = (Object.values(template.findResources("AWS::IAM::Policy")) as any[])
        .filter((policy) => policy.Properties.Roles.some((role: any) => role.Ref === roleRef))
        .flatMap((policy) => policy.Properties.PolicyDocument.Statement);
    const batchStatements = statements.filter((s) =>
        actionsOf(s).some((a) => a.startsWith("batch:"))
    );
    expect(batchStatements.map((s) => actionsOf(s)).sort()).toEqual([
        ["batch:ListJobs"],
        ["batch:TerminateJob"],
    ]);
    const list = batchStatements.find((s) => actionsOf(s).includes("batch:ListJobs"));
    expect(list.Resource).toBe("*");
    const terminate = batchStatements.find((s) => actionsOf(s).includes("batch:TerminateJob"));
    expect(JSON.stringify(terminate.Resource)).toMatch(/:job\/\*"/);

    // The IAM5 suppression for ListJobs on "*" applies to the whole function, so it would also
    // hide a later "*" statement on this role: the only one there is batch:ListJobs.
    const resourcesOf = (statement: any): any[] =>
        Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource];
    const wildcardStatements = statements.filter((s) => resourcesOf(s).includes("*"));
    expect(wildcardStatements.map((s) => actionsOf(s))).toEqual([["batch:ListJobs"]]);
};
