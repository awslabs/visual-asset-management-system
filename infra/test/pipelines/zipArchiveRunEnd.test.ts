/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * How a zipArchive run submits its job and how it ends when its container never reports. The
 * ZipArchiveBatchJob state submits through executeBatchJob, which is not idempotent, so the state
 * carries no Retry. Its heartbeat clock starts at submit, so it also runs out for a job still waiting
 * in the AWS Batch queue or one that failed before its container ran, and Step Functions records a
 * heartbeat expiry as States.Timeout, the same error as the state's bound. pipelineEnd then looks the
 * run's job up on the pipeline's queue by job name (ListJobs), ends it while it is queued or running
 * (TerminateJob), and tells the bound from a lost heartbeat by the job's age.
 */

import { Template } from "aws-cdk-lib/assertions";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import {
    ZIP_ARCHIVE_BATCH_HEARTBEAT,
    ZIP_ARCHIVE_BATCH_TIMEOUT,
    ZipArchiveConstruct,
} from "../../lib/nestedStacks/pipelines/utility/zipArchive/constructs/zipArchive-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";
import { lambdaEnvironmentsByHandler, parseAsl, singleStateMachine } from "../support/asl";
import { expectBatchStateTimeoutCleanup } from "../support/batchStateTimeoutCleanup";

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

describe("zipArchive pipelineEnd after its token wait times out", () => {
    test("knows the pipeline's job queue, and may list the run's jobs and end one by id only", () => {
        expectBatchStateTimeoutCleanup(template);
        const statements = roleStatementsOf("pipelineEnd.lambda_handler");
        expect(statements.some((s) => actionsOf(s).includes("batch:SubmitJob"))).toBe(false);
    });

    test("knows the token wait's own bound, to tell it from a lost heartbeat", () => {
        // Step Functions ends the wait with States.Timeout both when the heartbeat runs out and at
        // the bound; pipelineEnd compares the job's age with this value.
        const [env] = lambdaEnvironmentsByHandler(template, "pipelineEnd.lambda_handler");
        expect(env.STATE_TIMEOUT_SECONDS).toBe(String(ZIP_ARCHIVE_BATCH_TIMEOUT.toSeconds()));
        const state = parseAsl(singleStateMachine(template)).States.ZipArchiveBatchJob;
        expect(state.TimeoutSeconds).toBe(ZIP_ARCHIVE_BATCH_TIMEOUT.toSeconds());
        expect(state.HeartbeatSeconds).toBe(ZIP_ARCHIVE_BATCH_HEARTBEAT.toSeconds());
    });
});

describe("zipArchive job submission", () => {
    test("the submitting state does not retry, so one run submits one Batch job", () => {
        // executeBatchJob submits on every call; CDK's default Retry on Lambda service exceptions
        // would start a second container on the same task token and job row.
        const definition = parseAsl(singleStateMachine(template));
        const state = definition.States.ZipArchiveBatchJob;
        expect(JSON.stringify(state.Resource)).toContain("waitForTaskToken");
        expect(state.Retry).toBeUndefined();
        expect(state.Catch.some((c: any) => c.ErrorEquals.includes("States.ALL"))).toBe(true);
    });
});
