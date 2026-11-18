/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * How a zipArchive run ends when its container never reports. The ZipArchiveBatchJob heartbeat clock
 * starts at submit, so it also runs out for a job still waiting in the AWS Batch queue or one that
 * failed before its container ran. pipelineEnd then looks the run's job up on the pipeline's queue by
 * job name (ListJobs) and cancels a job still waiting to start (TerminateJob), so it needs the queue
 * and both actions.
 */

import { Template } from "aws-cdk-lib/assertions";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { ZipArchiveConstruct } from "../../lib/nestedStacks/pipelines/utility/zipArchive/constructs/zipArchive-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";
import { lambdaEnvironmentsByHandler } from "../support/asl";

let template: Template;

beforeAll(() => {
    const h = makePipelineHarness("ZipArchiveRunEndStack", (c) => {
        c.app.pipelines.useZipDownloads.enabled = true;
    });
    // The zipArchive job writes its status to the zip job table and reads asset file versions.
    (h.storage as any).dynamo = {
        zipDownloadRequestsStorageTable: new dynamodb.Table(h.stack, "ZipTable", {
            partitionKey: { name: "userId", type: dynamodb.AttributeType.STRING },
        }),
        assetFileVersionsStorageTable: new dynamodb.Table(h.stack, "FileVersionsTable", {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        }),
    };
    new ZipArchiveConstruct(h.stack, "ZipArchivePipeline", {
        config: h.config,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        assetAuxiliaryBucket: h.assetAuxiliaryBucket,
        storageResources: h.storage,
        kmsKey: h.kmsKey,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });
    template = Template.fromStack(h.stack);
});

/** Every statement of every IAM policy attached to the role of the function with `handler`. */
const roleStatementsOf = (handler: string): any[] => {
    const functions = (
        Object.values(template.findResources("AWS::Lambda::Function")) as any[]
    ).filter((fn) => fn.Properties.Handler === handler);
    expect(functions).toHaveLength(1);
    const roleRef = functions[0].Properties.Role["Fn::GetAtt"][0];
    return (Object.values(template.findResources("AWS::IAM::Policy")) as any[])
        .filter((policy) => policy.Properties.Roles.some((role: any) => role.Ref === roleRef))
        .flatMap((policy) => policy.Properties.PolicyDocument.Statement);
};

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

describe("zipArchive pipelineEnd after a heartbeat timeout", () => {
    test("knows the pipeline's job queue", () => {
        const [env] = lambdaEnvironmentsByHandler(template, "pipelineEnd.lambda_handler");
        const queues = template.findResources("AWS::Batch::JobQueue");
        expect(Object.keys(queues)).toHaveLength(1);
        expect(JSON.stringify(env.BATCH_JOB_QUEUE)).toContain(Object.keys(queues)[0]);
    });

    test("may list the run's jobs and cancel one by id, and nothing wider", () => {
        const statements = roleStatementsOf("pipelineEnd.lambda_handler");
        const list = statements.filter((s) => actionsOf(s).includes("batch:ListJobs"));
        expect(list).toHaveLength(1);
        expect(list[0].Resource).toBe("*");
        expect(actionsOf(list[0])).toEqual(["batch:ListJobs"]);

        const terminate = statements.filter((s) => actionsOf(s).includes("batch:TerminateJob"));
        expect(terminate).toHaveLength(1);
        expect(actionsOf(terminate[0])).toEqual(["batch:TerminateJob"]);
        expect(JSON.stringify(terminate[0].Resource)).toMatch(/:job\/\*"/);
        expect(statements.some((s) => actionsOf(s).includes("batch:SubmitJob"))).toBe(false);
    });
});
