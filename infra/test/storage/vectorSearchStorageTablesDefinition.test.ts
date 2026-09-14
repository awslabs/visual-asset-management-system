/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Definition of the storage-stack tables behind vector search and the per-input-file-version
 * workflow lock, asserted on the CloudFormation `storageResourcesBuilder()` emits into a plain stack.
 *
 * Built directly rather than through the T1 harness because the questions here are about one
 * construct's exact shape, both with and without vector search, and a construct-level build costs a
 * few seconds where a full-app synth costs twenty. The cross-template arms (each shipped template,
 * Retain, CMK, SSM publication) live in vectorSearchStorageTables.test.ts.
 *
 * Two traps this file is built around:
 *
 * - **The L2 Table declares only key and GSI attributes.** A vector index's SearchSchema attributes
 *   must appear in AttributeDefinitions, and CreateTable rejects a SearchSchema attribute that is
 *   declared while no index uses it. So the enabled arm asserts the complete override and the
 *   disabled arm asserts the override is ABSENT (only the two key attributes remain).
 * - **`s3AssetBucketRecords` is module-level state.** Building the storage resources twice in one
 *   Jest module without clearing it fails with "There is already a Construct with name
 *   'bucketSyncCreated--...'", so every build resets it first — the same reset templateSynth.ts does.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { s3AssetBucketRecords } from "../../lib/helper/s3AssetBuckets";
import commercialTemplate from "../../config/config.template.commercial.json";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import {
    storageResources,
    storageResourcesBuilder,
} from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { ResourceNameRegistry } from "../../lib/nestedStacks/resourceNames/resourceNameRegistry";
import { newTestApp } from "../support/testApp";

/** Set on the mock config directly; vectorIndexNameHarnessParity.test.ts pins how getConfig() derives it. */
const INDEX_NAME = "vec-amazon-titan-embed-text-v2-0-1024";
const EMBEDDING_DIMENSIONS = 1024;

/**
 * The vector index's SearchSchema: seven INLINE_FILTER attributes, all strings, all written on every item
 * (a literal copy of storageBuilder-nestedStack.ts's VECTOR_INDEX_FILTER_ATTRIBUTES, so a drift on either
 * side fails here rather than surfacing as a filter the index silently cannot apply).
 */
const FILTER_ATTRIBUTES = [
    "databaseId",
    "isLatest",
    "isArchived",
    "fileClass",
    "fileExt",
    "embeddingModelId",
    "segmentKind",
];

/**
 * The INCLUDE projection the vector index carries, in the order the table definition lists it — every
 * attribute the search Lambda reads from a hit without a follow-up GetItem.
 */
const PROJECTED_ATTRIBUTES = [
    "databaseId",
    "assetId",
    "filePath",
    "versionId",
    "isLatest",
    "isArchived",
    "fileClass",
    "fileExt",
    "embeddingModelId",
    "fileSize",
    "contentType",
    "indexedAt",
    "sourceModalities",
    "previewFileKey",
    "segmentKey",
    "segmentKind",
    "segmentLabel",
    "segmentStartMs",
    "segmentEndMs",
];

const VECTOR_TABLE_KEYS = ["databaseId:assetId", "fileVersionKey"];

function mockConfig(vectorSearchEnabled: boolean): Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.vectorSearch = {
        enabled: vectorSearchEnabled,
        embeddingModelId: "amazon.titan-embed-text-v2:0",
        embeddingDimensions: EMBEDDING_DIMENSIONS,
        indexingConcurrency: 5,
    };
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    config.vectorIndexName = INDEX_NAME;
    return config;
}

interface Built {
    template: Template;
    resources: storageResources;
    registry: ResourceNameRegistry;
}

function build(vectorSearchEnabled: boolean): Built {
    s3AssetBucketRecords.length = 0;
    const config = mockConfig(vectorSearchEnabled);
    Service.SetConfig(config);
    const app = newTestApp();
    const stack = new cdk.Stack(app, "S", {
        env: { account: config.env.account, region: config.env.region },
    });
    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "CommonLayer",
        "arn:aws:lambda:us-east-1:123456789012:layer:vams-common:1"
    ) as LayerVersion;
    const registry = new ResourceNameRegistry();
    const resources = storageResourcesBuilder(
        stack,
        config,
        layer,
        undefined as unknown as ec2.IVpc,
        [],
        registry
    );
    return { template: Template.fromStack(stack), resources, registry };
}

/** The one table whose KeySchema attribute names are exactly `keys`, in order. */
function tableWithKeys(template: Template, keys: string[]): any {
    const found = Object.values(template.findResources("AWS::DynamoDB::Table")).filter(
        (r) =>
            (r.Properties?.KeySchema ?? []).map((k: any) => k.AttributeName).join(",") ===
            keys.join(",")
    );
    expect(found).toHaveLength(1);
    return found[0];
}

const attributeNames = (table: any): string[] =>
    (table.Properties.AttributeDefinitions as any[]).map((a) => a.AttributeName).sort();

let on: Built;
let off: Built;

beforeAll(() => {
    on = build(true);
    off = build(false);
});

describe("VectorEmbeddingsStorageTable exists whether or not vector search is enabled", () => {
    test.each<[string, () => Built]>([
        ["enabled", () => on],
        ["disabled", () => off],
    ])(
        "vector search %s: keyed databaseId:assetId / fileVersionKey, retained, no streams, GSIs, or TTL",
        (_label, built) => {
            const table = tableWithKeys(built().template, VECTOR_TABLE_KEYS);
            expect(table.Properties.KeySchema).toEqual([
                { AttributeName: "databaseId:assetId", KeyType: "HASH" },
                { AttributeName: "fileVersionKey", KeyType: "RANGE" },
            ]);
            expect(table.Properties.BillingMode).toBe("PAY_PER_REQUEST");
            expect(table.Properties.PointInTimeRecoverySpecification).toEqual({
                PointInTimeRecoveryEnabled: true,
            });
            expect(table.Properties.TableName).toBeUndefined();
            expect(table.DeletionPolicy).toBe("Retain");
            expect(table.UpdateReplacePolicy).toBe("Retain");
            expect(table.Properties.StreamSpecification).toBeUndefined();
            expect(table.Properties.GlobalSecondaryIndexes).toBeUndefined();
            expect(table.Properties.TimeToLiveSpecification).toBeUndefined();
            expect(built().resources.dynamo.vectorEmbeddingsStorageTable).toBeInstanceOf(
                dynamodb.Table
            );
        }
    );
});

describe("the vector index is present exactly when vector search is enabled", () => {
    test("enabled: one COSINE index on `embedding` with the spec's projection and filters", () => {
        const table = tableWithKeys(on.template, VECTOR_TABLE_KEYS);
        expect(table.Properties.VectorIndexes).toEqual([
            {
                IndexName: INDEX_NAME,
                VectorAttribute: { AttributeName: "embedding" },
                Dimensions: EMBEDDING_DIMENSIONS,
                DistanceFunction: "COSINE",
                Projection: { ProjectionType: "INCLUDE", NonKeyAttributes: PROJECTED_ATTRIBUTES },
                SearchSchema: FILTER_ATTRIBUTES.map((name) => ({
                    AttributeName: name,
                    SearchSchemaElementType: "INLINE_FILTER",
                })),
            },
        ]);
    });

    test("enabled: AttributeDefinitions are the two keys plus the seven filter attributes, all strings", () => {
        const table = tableWithKeys(on.template, VECTOR_TABLE_KEYS);
        expect(attributeNames(table)).toEqual([...VECTOR_TABLE_KEYS, ...FILTER_ATTRIBUTES].sort());
        for (const definition of table.Properties.AttributeDefinitions) {
            expect(definition.AttributeType).toBe("S");
        }
    });

    test("enabled: the vector attribute itself is not declared", () => {
        // Only key and SearchSchema attributes belong in AttributeDefinitions.
        expect(attributeNames(tableWithKeys(on.template, VECTOR_TABLE_KEYS))).not.toContain(
            "embedding"
        );
    });

    test("disabled: no VectorIndexes, and only the two key attributes are declared", () => {
        // Control: the same builder emits an index when asked to, so an absence here is the branch.
        expect(tableWithKeys(on.template, VECTOR_TABLE_KEYS).Properties.VectorIndexes).toHaveLength(
            1
        );
        const table = tableWithKeys(off.template, VECTOR_TABLE_KEYS);
        expect(table.Properties.VectorIndexes).toBeUndefined();
        // A SearchSchema attribute declared with no index using it fails CreateTable.
        expect(attributeNames(table)).toEqual([...VECTOR_TABLE_KEYS].sort());
    });
});

describe("the CloudFormation-Validate schema warning for VectorIndexes is acknowledged on the CfnTable", () => {
    const acknowledgments = (built: Built) =>
        built.resources.dynamo.vectorEmbeddingsStorageTable.node.defaultChild!.node.metadata.filter(
            (entry) => entry.type === cdk.Validations.ACKNOWLEDGED_RULES_METADATA_KEY
        );

    test("enabled: exactly one acknowledgment, for F3002, with a VAMS-specific reason", () => {
        const acks = acknowledgments(on);
        expect(acks).toHaveLength(1);
        const data = acks[0].data as Record<string, string>;
        expect(Object.keys(data)).toEqual(["CloudFormation-Validate::F3002"]);
        expect(data["CloudFormation-Validate::F3002"]).toMatch(/VectorIndexes/);
        expect(data["CloudFormation-Validate::F3002"]).toMatch(/vector embeddings table/);
    });

    test("disabled: nothing is acknowledged because nothing is overridden", () => {
        // Control: the enabled arm carries one, so an empty result is the branch, not a metadata
        // key that never matches.
        expect(acknowledgments(on)).toHaveLength(1);
        expect(acknowledgments(off)).toEqual([]);
    });
});

describe("the storage stack registers the new table names for SSM publication", () => {
    test("registry carries dynamoTables/vectorEmbeddingsStorage in both arms", () => {
        expect(on.registry.has(RESOURCE_PARAM_KEYS.dynamoTables.vectorEmbeddingsStorage)).toBe(
            true
        );
        expect(off.registry.has(RESOURCE_PARAM_KEYS.dynamoTables.vectorEmbeddingsStorage)).toBe(
            true
        );
    });

    test("every table the storage stack emits is registered exactly once", () => {
        // Derived, not a literal: the registry's active + legacy table keys must equal the number of
        // AWS::DynamoDB::Table resources, so an unregistered table or a registered phantom fails here.
        const registered =
            Object.keys(RESOURCE_PARAM_KEYS.dynamoTables).length +
            Object.keys(RESOURCE_PARAM_KEYS.dynamoTablesLegacy).length;
        expect(Object.keys(on.template.findResources("AWS::DynamoDB::Table"))).toHaveLength(
            registered
        );
    });

    test("the storage stack does not register lambdaFunctions/* (the search stack publishes them)", () => {
        expect(on.registry.has(RESOURCE_PARAM_KEYS.lambdaFunctions.vectorReindexer)).toBe(false);
        expect(on.registry.has(RESOURCE_PARAM_KEYS.lambdaFunctions.crOsReindexer)).toBe(false);
    });
});

describe("WorkflowExecutionLocksStorageTable", () => {
    const LOCK_TABLE_KEYS = ["lockKey"];

    test.each<[string, () => Built]>([
        ["enabled", () => on],
        ["disabled", () => off],
    ])(
        "vector search %s: keyed lockKey, TTL on expiresAt, retained, no streams, GSIs, or vector index",
        (_label, built) => {
            const table = tableWithKeys(built().template, LOCK_TABLE_KEYS);
            expect(table.Properties.KeySchema).toEqual([
                { AttributeName: "lockKey", KeyType: "HASH" },
            ]);
            expect(table.Properties.AttributeDefinitions).toEqual([
                { AttributeName: "lockKey", AttributeType: "S" },
            ]);
            expect(table.Properties.TimeToLiveSpecification).toEqual({
                AttributeName: "expiresAt",
                Enabled: true,
            });
            expect(table.Properties.BillingMode).toBe("PAY_PER_REQUEST");
            expect(table.Properties.PointInTimeRecoverySpecification).toEqual({
                PointInTimeRecoveryEnabled: true,
            });
            expect(table.Properties.TableName).toBeUndefined();
            expect(table.DeletionPolicy).toBe("Retain");
            expect(table.UpdateReplacePolicy).toBe("Retain");
            expect(table.Properties.StreamSpecification).toBeUndefined();
            expect(table.Properties.GlobalSecondaryIndexes).toBeUndefined();
            expect(table.Properties.VectorIndexes).toBeUndefined();
            expect(built().resources.dynamo.workflowExecutionLocksStorageTable).toBeInstanceOf(
                dynamodb.Table
            );
        }
    );

    test("registry carries dynamoTables/workflowExecutionLocksStorage in both arms", () => {
        expect(
            on.registry.has(RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionLocksStorage)
        ).toBe(true);
        expect(
            off.registry.has(RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionLocksStorage)
        ).toBe(true);
    });
});
