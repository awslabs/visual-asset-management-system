/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Bucket registration identity (#1889): the BEHAVIOUR of the S3 asset buckets table populator.
 *
 * The populator is inline Python in the custom resource Lambda. This test synthesizes it, extracts the
 * real inline source from the template, and runs it with `python3` against a fake boto3
 * (fixtures/populatorRegistrationHarness.py). Every case executes `lambda_handler` as deployed:
 *
 * - a registration is matched on its bucket name and its prefix with one trailing `/`, read from the
 *   stored row, so a prefix whose spelling changed keeps its bucketId and its stored key;
 * - the scan is paged to the end;
 * - a row whose registration left the configuration is retired (kept, `isDefault` cleared), never
 *   deleted, and only on Create and Update;
 * - two rows for one registration are both kept: the one assets reference (one Query per row on the
 *   asset table's bucketId index) answers lookups and carries `isDefault`, the other names it.
 *
 * The grant test checks the Lambda reads the asset table only through that index.
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

interface Row {
    bucketId: string;
    sortKey: string;
    baseAssetsPrefix: string | null;
    isDefault: boolean | null;
    retired: boolean | null;
    duplicateOfBucketId: string | null;
}

interface CaseResult {
    ok: boolean;
    error: string | null;
    data: Record<string, string> | null;
    rows: Row[];
    calls: { op: string; bucketId?: string; sortKey?: string; start?: unknown }[];
    assetCalls: { index: string; key: string; value: string; limit: number | null }[];
}

const buildStack = () => {
    s3AssetBucketRecords.length = 0;
    const app = newTestApp();
    const stack = new cdk.Stack(app, "TestStack", {
        env: { account: "111111111111", region: "us-east-1" },
    });
    const table = new dynamodb.Table(stack, "S3AssetBucketsStorageTable", {
        partitionKey: { name: "bucketId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "bucketName:baseAssetsPrefix", type: dynamodb.AttributeType.STRING },
    });
    const assetTable = new dynamodb.Table(stack, "AssetStorageTable", {
        partitionKey: { name: "databaseId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "assetId", type: dynamodb.AttributeType.STRING },
    });
    assetTable.addGlobalSecondaryIndex({
        indexName: "BucketIdGSI",
        partitionKey: { name: "bucketId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "assetId", type: dynamodb.AttributeType.STRING },
    });
    addS3AssetBucket(
        s3.Bucket.fromBucketArn(stack, "Bucket", "arn:aws:s3:::vams-x"),
        "/",
        "default"
    );
    createPopulateS3AssetBucketsTableCustomResource(
        stack,
        "PopulateS3AssetBucketsTable",
        table,
        undefined,
        { table: assetTable, bucketIdIndexName: "BucketIdGSI" }
    );
    return Template.fromStack(stack);
};

const inlineSource = (template: Template): string => {
    const functions = template.findResources("AWS::Lambda::Function");
    const sources = Object.values(functions)
        .map((fn: any) => fn.Properties?.Code?.ZipFile)
        .filter((code: unknown): code is string => typeof code === "string");
    const populator = sources.filter((code) => code.includes("verify_transfer_acceleration"));
    expect(populator).toHaveLength(1);
    return populator[0];
};

let template: Template;
let results: Record<string, CaseResult>;

beforeAll(() => {
    template = buildStack();
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "populator-identity-"));
    const sourcePath = path.join(dir, "populator_inline.py");
    fs.writeFileSync(sourcePath, inlineSource(template));
    const harness = path.join(__dirname, "fixtures", "populatorRegistrationHarness.py");
    results = JSON.parse(execFileSync("python3", [harness, sourcePath], { encoding: "utf8" }));
    fs.rmSync(dir, { recursive: true, force: true });
});

const puts = (result: CaseResult) => result.calls.filter((call) => call.op === "put");
const updates = (result: CaseResult) => result.calls.filter((call) => call.op === "update");
const rowOf = (result: CaseResult, bucketId: string) =>
    result.rows.filter((row) => row.bucketId === bucketId);

describe("registration matching", () => {
    test("a prefix whose spelling changed keeps its bucketId and its stored item", () => {
        const result = results.prefixSpellingChange;
        expect(result.ok).toBe(true);
        expect(result.rows).toHaveLength(1);
        expect(result.rows[0]).toMatchObject({
            bucketId: "id-a",
            sortKey: "b:assets",
            baseAssetsPrefix: "assets/",
            isDefault: true,
        });
        expect(puts(result)).toEqual([{ op: "put", bucketId: "id-a", sortKey: "b:assets" }]);
    });

    test("a doubled trailing slash is the same registration, written with one slash", () => {
        const result = results.prefixDoubleSlash;
        expect(result.rows).toHaveLength(1);
        expect(result.rows[0]).toMatchObject({ bucketId: "id-a", baseAssetsPrefix: "assets/" });
    });

    test("the scan is paged to the end, so a row on a later page keeps its bucketId", () => {
        const result = results.pagedScan;
        expect(result.ok).toBe(true);
        expect(result.calls.filter((call) => call.op === "scan").length).toBeGreaterThan(1);
        expect(rowOf(result, "id-3")).toHaveLength(1);
        expect(result.rows).toHaveLength(3);
    });

    test("a new registration gets a new bucketId and a sort key with one trailing slash", () => {
        const result = results.newRegistration;
        expect(result.ok).toBe(true);
        expect(result.rows).toHaveLength(1);
        expect(result.rows[0].sortKey).toBe("b:new/");
        expect(result.rows[0].baseAssetsPrefix).toBe("new/");
    });

    test("two configuration entries for one registration fail the deployment", () => {
        const result = results.twoEntriesOnePair;
        expect(result.ok).toBe(false);
        expect(result.error).toContain("register bucket b under the prefix assets/");
    });
});

describe("registrations that left the configuration", () => {
    test("are retired with isDefault cleared, and never deleted", () => {
        const result = results.retireRemoved;
        expect(result.ok).toBe(true);
        expect(rowOf(result, "id-x")[0]).toMatchObject({ retired: true, isDefault: false });
        expect(rowOf(result, "id-a")[0]).toMatchObject({ retired: null, isDefault: true });
        expect(result.calls.some((call) => call.op === "delete")).toBe(false);
        expect(result.data?.RetiredRegistrations).toBe("id-x");
    });

    test("a retired registration that is configured again keeps its bucketId and loses the flag", () => {
        const result = results.retiredReturns;
        expect(result.rows).toHaveLength(1);
        expect(result.rows[0]).toMatchObject({ bucketId: "id-a", retired: null, isDefault: true });
    });

    test("a row already retired is not written again", () => {
        const result = results.retiredStaysRetired;
        expect(updates(result)).toEqual([]);
        expect(rowOf(result, "id-x")[0].retired).toBe(true);
    });

    test("a Delete event and an empty bucket list retire nothing", () => {
        for (const name of ["deleteRetiresNothing", "emptyListRetiresNothing"]) {
            expect(results[name].ok).toBe(true);
            expect(updates(results[name])).toEqual([]);
            expect(rowOf(results[name], "id-x")[0].retired).toBeNull();
        }
    });
});

describe("two rows for one registration", () => {
    test("the row assets reference answers lookups and is the only default", () => {
        const result = results.duplicatesOneReferenced;
        expect(result.ok).toBe(true);
        expect(rowOf(result, "id-a")[0]).toMatchObject({
            isDefault: true,
            duplicateOfBucketId: null,
        });
        expect(rowOf(result, "id-b")[0]).toMatchObject({
            isDefault: false,
            duplicateOfBucketId: "id-a",
            retired: null,
        });
        expect(result.data?.DuplicateRegistrations).toContain("id-a answers lookups");
        // One Query of one item per row on the bucketId index, and no scan of the asset table.
        expect(result.assetCalls).toHaveLength(2);
        for (const call of result.assetCalls) {
            expect(call).toMatchObject({ index: "BucketIdGSI", key: "bucketId", limit: 1 });
        }
    });

    test("a primary chosen by an earlier deployment stays the primary", () => {
        const result = results.duplicatesStickyPrimary;
        expect(rowOf(result, "id-a")[0]).toMatchObject({
            isDefault: true,
            duplicateOfBucketId: null,
        });
        expect(rowOf(result, "id-b")[0].duplicateOfBucketId).toBe("id-a");
        expect(result.assetCalls).toEqual([]);
    });

    test.each([
        ["duplicatesBothReferenced"],
        ["duplicatesNoneReferenced"],
        ["duplicatesReferenceReadFails"],
        ["duplicatesWithoutAssetTable"],
    ])("%s: both rows are kept and the row the configuration spells answers lookups", (name) => {
        const result = results[name];
        expect(result.ok).toBe(true);
        expect(result.rows).toHaveLength(2);
        expect(rowOf(result, "id-b")[0]).toMatchObject({
            isDefault: true,
            duplicateOfBucketId: null,
        });
        expect(rowOf(result, "id-a")[0]).toMatchObject({
            isDefault: false,
            duplicateOfBucketId: "id-b",
            sortKey: "b:assets",
        });
    });

    test("rows of a registration that left the configuration are retired together", () => {
        const result = results.duplicatesRetiredTogether;
        expect(rowOf(result, "id-a")[0]).toMatchObject({ retired: true, isDefault: false });
        expect(rowOf(result, "id-b")[0]).toMatchObject({ retired: true, isDefault: false });
    });
});

describe("asset table grant", () => {
    test("is Query on the bucketId index only", () => {
        const policies = template.findResources("AWS::IAM::Policy");
        const statements = Object.values(policies).flatMap(
            (p: any) => p.Properties.PolicyDocument.Statement
        );
        const assetStatements = statements.filter((s: any) =>
            JSON.stringify(s.Resource).includes("AssetStorageTable")
        );
        expect(assetStatements).toHaveLength(1);
        expect(assetStatements[0].Action).toBe("dynamodb:Query");
        expect(JSON.stringify(assetStatements[0].Resource)).toContain("/index/BucketIdGSI");
    });
});
