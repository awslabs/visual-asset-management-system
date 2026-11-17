/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * S3 Transfer Acceleration (#1610): the BEHAVIOUR of the table populator's acceleration check.
 *
 * The populator is inline Python in the custom resource Lambda. This test synthesizes it, extracts the
 * real inline source from the template, and runs it with `python3` against a fake boto3
 * (fixtures/populatorAccelerationHarness.py), so every case below executes `verify_transfer_acceleration`
 * and `lambda_handler` as deployed: Enabled, Suspended, an empty status, AccessDenied, NoSuchBucket,
 * another client error, a cross-Region bucket (the check uses the bucket's Region), a row without the
 * flag (no call), and Delete events (no call, so a suspended acceleration never blocks stack deletion).
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { createPopulateS3AssetBucketsTableCustomResource } from "../../lib/nestedStacks/storage/customResources/populateS3AssetBucketsTable";
import { s3AssetBucketRecords, addS3AssetBucket } from "../../lib/helper/s3AssetBuckets";
import { newTestApp } from "../support/testApp";

interface CaseResult {
    ok: boolean;
    error: string | null;
    errorType: string | null;
    rows: { bucketName: string; isAccelerated: boolean | null }[];
    calls: { region: string; bucket: string }[];
}

const inlineSource = (): string => {
    s3AssetBucketRecords.length = 0;
    const app = newTestApp();
    const stack = new cdk.Stack(app, "TestStack", {
        env: { account: "111111111111", region: "us-east-1" },
    });
    const table = new dynamodb.Table(stack, "S3AssetBucketsStorageTable", {
        partitionKey: { name: "bucketId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "bucketName:baseAssetsPrefix", type: dynamodb.AttributeType.STRING },
    });
    addS3AssetBucket(
        s3.Bucket.fromBucketArn(stack, "Bucket", "arn:aws:s3:::vams-x"),
        "/",
        "default"
    );
    createPopulateS3AssetBucketsTableCustomResource(stack, "PopulateS3AssetBucketsTable", table);
    const functions = Template.fromStack(stack).findResources("AWS::Lambda::Function");
    const sources = Object.values(functions)
        .map((fn: any) => fn.Properties?.Code?.ZipFile)
        .filter((code: unknown): code is string => typeof code === "string");
    const populator = sources.filter((code) => code.includes("verify_transfer_acceleration"));
    expect(populator).toHaveLength(1);
    return populator[0];
};

let results: Record<string, CaseResult>;

beforeAll(() => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "populator-accel-"));
    const sourcePath = path.join(dir, "populator_inline.py");
    fs.writeFileSync(sourcePath, inlineSource());
    const harness = path.join(__dirname, "fixtures", "populatorAccelerationHarness.py");
    results = JSON.parse(execFileSync("python3", [harness, sourcePath], { encoding: "utf8" }));
    fs.rmSync(dir, { recursive: true, force: true });
});

describe("populator acceleration check (inline Python executed)", () => {
    test("Enabled passes on Create and Update, writes isAccelerated, and calls once", () => {
        for (const name of ["enabled", "enabledOnUpdate"]) {
            expect(results[name].ok).toBe(true);
            expect(results[name].rows).toEqual([{ bucketName: "vams-x", isAccelerated: true }]);
            expect(results[name].calls).toEqual([{ region: "us-east-1", bucket: "vams-x" }]);
        }
    });

    test.each([
        ["suspended", "Suspended"],
        ["empty", "not configured"],
    ])("%s fails naming the bucket, the Region and the fix, and writes no row", (name, status) => {
        const result = results[name];
        expect(result.ok).toBe(false);
        expect(result.error).toContain("bucket vams-x");
        expect(result.error).toContain("Region us-east-1");
        expect(result.error).toContain(`status is ${status}`);
        expect(result.error).toContain("Transfer acceleration");
        expect(result.error).toContain("set useS3TransferAcceleration to false");
        expect(result.rows).toEqual([]);
    });

    test("AccessDenied fails with the bucket, the Region, the code and the exact grant", () => {
        const result = results.accessDenied;
        expect(result.ok).toBe(false);
        // The CloudFormation failure reason is this message, not the botocore one.
        expect(result.errorType).toBe("Exception");
        expect(result.error).toContain("bucket vams-x");
        expect(result.error).toContain("Region us-east-1");
        expect(result.error).toContain("(AccessDenied)");
        expect(result.error).toContain("s3:GetAccelerateConfiguration");
        expect(result.error).toContain("bucket policy");
        expect(result.rows).toEqual([]);
    });

    test("NoSuchBucket fails with the bucket, the Region and the fix, without suggesting a grant", () => {
        const result = results.noSuchBucket;
        expect(result.ok).toBe(false);
        expect(result.errorType).toBe("Exception");
        expect(result.error).toContain("bucket vams-x");
        expect(result.error).toContain("Region us-east-1");
        expect(result.error).toContain("(NoSuchBucket)");
        expect(result.error).toContain("does not exist in that Region");
        expect(result.error).not.toContain("bucket policy");
    });

    test("any other client error also names the bucket and the Region", () => {
        const result = results.otherClientError;
        expect(result.ok).toBe(false);
        expect(result.errorType).toBe("Exception");
        expect(result.error).toContain("bucket vams-x");
        expect(result.error).toContain("(SlowDown)");
    });

    test("a cross-Region bucket is checked in its own Region", () => {
        expect(results.crossRegion.ok).toBe(true);
        expect(results.crossRegion.calls).toEqual([{ region: "us-west-2", bucket: "vams-x" }]);
        expect(results.crossRegionSuspended.ok).toBe(false);
        expect(results.crossRegionSuspended.calls).toEqual([
            { region: "eu-west-1", bucket: "vams-x" },
        ]);
        expect(results.crossRegionSuspended.error).toContain("Region eu-west-1");
    });

    test("a row without the flag makes no call and writes no attribute", () => {
        expect(results.notFlagged.ok).toBe(true);
        expect(results.notFlagged.calls).toEqual([]);
        expect(results.notFlagged.rows).toEqual([{ bucketName: "vams-x", isAccelerated: null }]);
    });

    test.each(["deleteSuspended", "deleteDenied"])(
        "%s: a Delete event never runs the check, so deletion is not blocked",
        (name) => {
            expect(results[name].ok).toBe(true);
            expect(results[name].calls).toEqual([]);
        }
    );

    test("one bucket registered under two prefixes is checked per row and both rows agree", () => {
        const result = results.twoPrefixesAgree;
        expect(result.ok).toBe(true);
        expect(result.rows).toEqual([
            { bucketName: "vams-x", isAccelerated: true },
            { bucketName: "vams-x", isAccelerated: true },
        ]);
    });
});
