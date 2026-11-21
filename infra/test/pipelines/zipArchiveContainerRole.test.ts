/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The zipArchive container job role, per asset bucket record: reads under the record's prefix, writes
 * (mode 2's archive upload) only under that prefix's `pipelines/` outputs, and lists only under the
 * prefix when the record has one. Image pull and log writes are the execution role's, so the job
 * role carries no task execution managed policy.
 */

import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { ZipArchiveConstruct } from "../../lib/nestedStacks/pipelines/utility/zipArchive/constructs/zipArchive-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";

let template: Template;
let prefixedBucketLogicalId: string;
let rootBucketLogicalId: string;

beforeAll(() => {
    const h = makePipelineHarness("ZipArchiveContainerRoleStack", (c) => {
        c.app.pipelines.useZipDownloads.enabled = true;
    });
    // The harness registers one bucket at the root; add one registered under a prefix.
    const prefixed = new s3.Bucket(h.stack, "PrefixedAssetBucket");
    s3AssetBuckets.addS3AssetBucket(prefixed, "/vams/", "db2");
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
    const buckets = Object.keys(template.findResources("AWS::S3::Bucket"));
    prefixedBucketLogicalId = buckets.find((id) => id.startsWith("PrefixedAssetBucket"))!;
    rootBucketLogicalId = buckets.find((id) => id.startsWith("AssetBucket"))!;
});

const jobRole = (): any => {
    const roles = Object.entries(template.findResources("AWS::IAM::Role")).filter(([id]) =>
        id.includes("ZipArchiveContainerJobRole")
    );
    expect(roles).toHaveLength(1);
    return roles[0][1];
};

const inputStatements = (): any[] =>
    jobRole().Properties.Policies.find((p: any) => p.PolicyName === "InputBucketPolicy")
        .PolicyDocument.Statement;

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

/** The statements that name the bucket with `logicalId`, as [actions, resource JSON, conditions]. */
const statementsFor = (logicalId: string) =>
    inputStatements()
        .filter((s) => JSON.stringify(s.Resource).includes(logicalId))
        .map((s) => ({
            actions: actionsOf(s),
            resource: JSON.stringify(s.Resource),
            condition: s.Condition,
        }));

describe("zipArchive container job role", () => {
    test("writes only under each record's pipelines/ outputs", () => {
        for (const [id, prefix] of [
            [rootBucketLogicalId, ""],
            [prefixedBucketLogicalId, "vams/"],
        ]) {
            const writes = statementsFor(id).filter((s) => s.actions.includes("s3:PutObject"));
            expect(writes).toHaveLength(1);
            expect(writes[0].actions.sort()).toEqual([
                "s3:AbortMultipartUpload",
                "s3:ListMultipartUploadParts",
                "s3:PutObject",
            ]);
            expect(writes[0].resource).toContain(`/${prefix}pipelines/*`);
        }
    });

    test("reads under the record's prefix, at any version", () => {
        const reads = statementsFor(prefixedBucketLogicalId).filter((s) =>
            s.actions.includes("s3:GetObject")
        );
        expect(reads).toHaveLength(1);
        expect(reads[0].actions.sort()).toEqual(["s3:GetObject", "s3:GetObjectVersion"]);
        expect(reads[0].resource).toContain("/vams/*");
    });

    test("lists under the prefix when the record has one, and the whole bucket at the root", () => {
        const listOf = (id: string) =>
            statementsFor(id).filter((s) => s.actions.includes("s3:ListBucket"));
        const prefixed = listOf(prefixedBucketLogicalId);
        expect(prefixed).toHaveLength(1);
        expect(prefixed[0].condition).toEqual({ StringLike: { "s3:prefix": ["vams/*"] } });
        const root = listOf(rootBucketLogicalId);
        expect(root).toHaveLength(1);
        expect(root[0].condition).toBeUndefined();
    });

    test("no other statement grants a write or list action on an asset bucket", () => {
        const writeOrList = inputStatements().filter((s) =>
            actionsOf(s).some((a) => /^s3:(Put|Abort|List|Delete)/.test(a))
        );
        // One write and one list statement per record.
        expect(writeOrList).toHaveLength(4);
    });

    test("carries no task execution managed policy", () => {
        const managed = JSON.stringify(jobRole().Properties.ManagedPolicyArns ?? []);
        expect(managed).not.toContain("AmazonECSTaskExecutionRolePolicy");
        expect(managed).toContain("AWSXrayWriteOnlyAccess");
    });
});
