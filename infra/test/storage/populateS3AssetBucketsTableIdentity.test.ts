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
    retiredAt: string | null;
    duplicateOfBucketId: string | null;
    source: string | null;
    defaultSyncDatabaseId: string | null;
    bucketKmsKeyArn: string | null;
    isAccelerated: boolean | null;
}

interface CaseResult {
    ok: boolean;
    error: string | null;
    data: Record<string, string> | null;
    rows: Row[];
    items: Record<string, Record<string, unknown>>;
    calls: { op: string; bucketId?: string; sortKey?: string; start?: unknown }[];
    assetCalls: { index: string; key: string; value: string; limit: number | null }[];
    describeStacks: string[];
}

// The stack the harness event names (the storage nested stack) and its parent.
const STACK_ID = "arn:aws:cloudformation:us-east-1:111111111111:stack/vams-core-storage/aaaa";
const PARENT_ID = "arn:aws:cloudformation:us-east-1:111111111111:stack/vams-core/bbbb";

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

// The merge write of a configured row, the retire update, and a whole-item put (never expected).
const writes = (result: CaseResult) => result.calls.filter((call) => call.op === "write");
const updates = (result: CaseResult) => result.calls.filter((call) => call.op === "retire");
const puts = (result: CaseResult) => result.calls.filter((call) => call.op === "put");
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
        expect(writes(result)).toEqual([{ op: "write", bucketId: "id-a", sortKey: "b:assets" }]);
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

describe("record fields and the merge write (#1978)", () => {
    test("a configured row is merged, never replaced: an attribute the populator does not own stays", () => {
        const result = results.mergeKeepsUnowned;
        expect(result.ok).toBe(true);
        expect(puts(result)).toEqual([]);
        expect(writes(result)).toEqual([{ op: "write", bucketId: "id-a", sortKey: "b:assets/" }]);
        expect(result.items["id-a|b:assets/"]).toMatchObject({
            assetLayout: "{assetId}",
            source: "static",
            defaultSyncDatabaseId: "db1",
            isDefault: true,
        });
        // Owned fields the configuration no longer gives are removed, as the whole-item write did.
        expect(result.items["id-a|b:assets/"]).not.toHaveProperty("isAccelerated");
        expect(result.items["id-a|b:assets/"]).not.toHaveProperty("bucketKmsKeyArn");
    });

    test("an existing row gains source, the fallback database and the key; ids and retired rows are unchanged", () => {
        const result = results.upgradeGainsFields;
        expect(result.ok).toBe(true);
        expect(rowOf(result, "id-a")[0]).toMatchObject({
            sortKey: "b:assets/",
            source: "static",
            defaultSyncDatabaseId: "db1",
            bucketKmsKeyArn: "arn:aws:kms:us-east-1:111111111111:key/k1",
            retired: null,
        });
        expect(rowOf(result, "id-r")[0]).toMatchObject({
            retired: true,
            retiredAt: "2026-01-01T00:00:00Z",
        });
        expect(updates(result)).toEqual([]);
        expect(result.rows.map((row) => row.bucketId)).toEqual(["id-a", "id-r"]);
    });

    test("a returning row loses retired and retiredAt", () => {
        const result = results.retiredReturns;
        expect(result.rows[0]).toMatchObject({ retired: null, retiredAt: null, source: "static" });
    });

    test("a row added at run time survives a deployment unchanged", () => {
        const result = results.runtimeRowSurvives;
        expect(result.ok).toBe(true);
        expect(result.items["id-rt|r:rt/"]).toEqual({
            bucketId: "id-rt",
            "bucketName:baseAssetsPrefix": "r:rt/",
            bucketName: "r",
            baseAssetsPrefix: "rt/",
            isDefault: false,
            source: "runtime",
            status: "active",
            createdBy: "admin",
            defaultSyncDatabaseId: "dbr",
        });
        expect(result.calls.filter((call) => call.bucketId === "id-rt")).toEqual([]);
        expect(result.data?.RetiredRegistrations).toBe("");
    });

    test("a configured entry is never matched to a runtime row of the same bucket and prefix", () => {
        const result = results.runtimeRowSamePair;
        expect(result.ok).toBe(false);
        expect(result.error).toContain("overlaps the prefix a/ of the stored registration id-rt");
        expect(result.error).toContain("added at run time");
        expect(result.calls.filter((call) => call.op !== "scan")).toEqual([]);
    });
});

describe("overlap with stored registrations (#1978)", () => {
    test("a configured prefix nested in a retired registration's prefix fails before any write", () => {
        const result = results.nestedInRetired;
        expect(result.ok).toBe(false);
        expect(result.error).toContain(
            "bucket b cannot be registered under the prefix assets/team1/: it overlaps the prefix assets/ " +
                "of the stored registration id-old (retired)"
        );
        expect(result.error).toContain("Choose a prefix that does not overlap assets/");
        expect(result.error).toContain("Overlapping bucket registrations");
        expect(result.calls.filter((call) => call.op !== "scan")).toEqual([]);
        expect(result.rows.map((row) => row.bucketId)).toEqual(["id-c", "id-old"]);
    });

    test.each([
        ["retiredNestedInConfigured", "assets/", "assets/team1/", "retired"],
        ["retiredRootOverlaps", "x/", "/", "retired"],
        ["nestedInRowBeingRetired", "old/new/", "old/", "no longer configured"],
        ["nestedInRuntimeRow", "rt/sub/", "rt/", "added at run time"],
    ])("%s fails naming both prefixes", (name, configured, stored, state) => {
        const result = results[name];
        expect(result.ok).toBe(false);
        expect(result.error).toContain(
            `under the prefix ${configured}: it overlaps the prefix ${stored} `
        );
        expect(result.error).toContain(`(${state})`);
        expect(result.calls.filter((call) => call.op !== "scan")).toEqual([]);
    });

    test("prefixes that only share leading characters, and other buckets, do not overlap", () => {
        const result = results.siblingPrefixesDoNotOverlap;
        expect(result.ok).toBe(true);
        expect(writes(result)).toHaveLength(1);
    });

    test("a Delete event is never blocked by the check", () => {
        expect(results.deleteSkipsOverlapCheck.ok).toBe(true);
    });

    test("a configured registration that already has a row is not refused for a stored overlap", () => {
        const result = results.storedOverlapNotRefused;
        expect(result.ok).toBe(true);
        // Reported, not refused.
        expect(result.data?.StoredOverlaps).toBe(
            "b prefix assets/team1/ overlaps stored registration id-old (prefix assets/)"
        );
        expect(rowOf(result, "id-old")[0]).toMatchObject({ retired: true, isDefault: false });
        expect(rowOf(result, "id-t")[0]).toMatchObject({ retired: null, isDefault: true });
    });

    test.each([
        ["reactivateOverActive", "assets/", "assets/team1/", "no longer configured", "id-team"],
        ["reactivateOverRetired", "assets/", "assets/team1/", "retired", "id-team"],
        ["forwardReactivatesOverlap", "assets/team1/", "assets/", "retired", "id-wide"],
    ])(
        "%s: configuring a retired registration again is refused when it overlaps (D2)",
        (name, configured, stored, state, storedId) => {
            const result = results[name];
            expect(result.ok).toBe(false);
            expect(result.error).toContain(
                `cannot be registered under the prefix ${configured}: it overlaps the prefix ${stored} ` +
                    `of the stored registration ${storedId} (${state})`
            );
            // Refused before any write: every row is as stored.
            expect(result.calls.filter((call) => call.op !== "scan")).toEqual([]);
        }
    );

    test.each(["rollbackReactivatesOverlap", "parentRollbackReactivatesOverlap"])(
        "%s: a rollback that re-activates an overlapping registration is reported, not refused",
        (name) => {
            const result = results[name];
            expect(result.ok).toBe(true);
            expect(rowOf(result, "id-team")[0]).toMatchObject({ retired: null });
            expect(rowOf(result, "id-wide")[0].retired).toBe(true);
            expect(result.data?.StoredOverlaps).toBe(
                "b prefix assets/team1/ overlaps stored registration id-wide (prefix assets/)"
            );
        }
    );

    test("a rollback is read from the stack's status: its own, and its parent's when its own is not one", () => {
        expect(results.rollbackReactivatesOverlap.describeStacks).toEqual([STACK_ID]);
        expect(results.parentRollbackReactivatesOverlap.describeStacks).toEqual([
            STACK_ID,
            PARENT_ID,
        ]);
        expect(results.forwardReactivatesOverlap.describeStacks).toEqual([STACK_ID, PARENT_ID]);
        expect(results.notNestedForward.describeStacks).toEqual([STACK_ID]);
        // A Create is never a rollback, and no stack is read for it.
        expect(results.newRegistration.describeStacks).toEqual([]);
    });

    test("a rollback status is a rollback whatever the synth timestamps say", () => {
        const result = results.rollbackStatusNewerTimestamp;
        expect(result.ok).toBe(true);
        expect(result.data?.StoredOverlaps).toBe(
            "b prefix new1978/ overlaps stored registration id-old (prefix /)"
        );
    });

    test.each([
        [
            "olderSynthForwardRefused",
            "a forward deploy of an assembly synthesized before the last one",
        ],
        ["stackStatusUnreadable", "a stack status that cannot be read (fail closed)"],
        ["eventWithoutStackId", "an event that names no stack"],
        ["notNestedForward", "a forward update of a stack that is not nested"],
        ["cleanupStatusIsNotRollback", "a status outside the rollback-in-progress ones"],
    ])("%s: %s runs the overlap check and refuses the new overlapping prefix (R1)", (name) => {
        const result = results[name];
        expect(result.ok).toBe(false);
        expect(result.error).toContain(
            "cannot be registered under the prefix new1978/: it overlaps the prefix / of the stored " +
                "registration id-old (retired)"
        );
        expect(result.calls.filter((call) => call.op !== "scan")).toEqual([]);
    });

    test("the rollback of a refused addition succeeds", () => {
        const result = results.rollbackAfterRefusedAdd;
        expect(result.ok).toBe(true);
        expect(result.data?.StoredOverlaps).toBe("");
        expect(rowOf(result, "id-old")[0].retired).toBe(true);
    });
});

describe("custom resource properties", () => {
    test("each record carries its fallback database, and its key only when one is declared", () => {
        s3AssetBucketRecords.length = 0;
        const app = newTestApp();
        const stack = new cdk.Stack(app, "PropsStack", {
            env: { account: "111111111111", region: "us-east-1" },
        });
        const table = new dynamodb.Table(stack, "S3AssetBucketsStorageTable", {
            partitionKey: { name: "bucketId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "bucketName:baseAssetsPrefix", type: dynamodb.AttributeType.STRING },
        });
        addS3AssetBucket(s3.Bucket.fromBucketArn(stack, "A", "arn:aws:s3:::vams-a"), "/", "dba");
        addS3AssetBucket(
            s3.Bucket.fromBucketArn(stack, "B", "arn:aws:s3:::vams-b"),
            "data/",
            "dbb",
            "222222222222",
            "arn:aws:kms:us-east-1:222222222222:key/k1"
        );
        createPopulateS3AssetBucketsTableCustomResource(stack, "Populate", table);
        const resources = Object.values(
            Template.fromStack(stack).findResources("AWS::CloudFormation::CustomResource")
        );
        expect(resources).toHaveLength(1);
        const buckets = JSON.parse((resources[0] as any).Properties.buckets);
        expect(
            buckets.map((b: any) => [b.bucketName, b.defaultSyncDatabaseId, b.bucketKmsKeyArn])
        ).toEqual([
            ["vams-a", "dba", undefined],
            ["vams-b", "dbb", "arn:aws:kms:us-east-1:222222222222:key/k1"],
        ]);
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
