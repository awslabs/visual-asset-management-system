/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: the comment and subscription tables keyed by database and asset id.
 *
 * `CommentStorageTableV2` (`databaseId:assetId` + `commentId`, with the `AssetVersionIdLSI` local index
 * on `assetVersionId`) and `SubscriptionsStorageTableV2` (`eventName` + `entityName:databaseId:entityId`)
 * follow the storage builder's shared table settings and are the tables the handlers' resource names
 * point at. The V1 tables keep their logical ids, so an upgrade replaces nothing; their names are
 * published under `dynamoTables/legacy/` for the v2.6 to v2.7 migration and no Lambda is granted them.
 */

import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    lambdaByHandler,
    resolveReference,
    resourcesOf,
    statementsOf,
    synthTemplate,
} from "../support/templateSynth";

jest.setTimeout(600_000);

const keySchema = (r: Resource): string =>
    ((r.properties.KeySchema ?? []) as any[])
        .map((k) => `${k.AttributeName}:${k.KeyType}`)
        .join(",");

function tableByLogicalPrefix(s: SynthResult, prefix: RegExp): Resource {
    const found = s.where("AWS::DynamoDB::Table", (r) => prefix.test(r.logicalId));
    if (found.length !== 1) {
        throw new Error(`${s.name}: expected one table matching ${prefix}, found ${found.length}`);
    }
    return found[0];
}

const commentV2 = (s: SynthResult) => tableByLogicalPrefix(s, /^CommentStorageTableV2[0-9A-F]{8}$/);
const commentV1 = (s: SynthResult) => tableByLogicalPrefix(s, /^CommentStorageTable[0-9A-F]{8}$/);
const subscriptionV2 = (s: SynthResult) =>
    tableByLogicalPrefix(s, /^SubscriptionsStorageTableV2[0-9A-F]{8}$/);
const subscriptionV1 = (s: SynthResult) =>
    tableByLogicalPrefix(s, /^SubscriptionsStorageTable[0-9A-F]{8}$/);

describe("comment and subscription tables keyed by database and asset id", () => {
    test.each(ALL_TEMPLATES)("%s: the V2 key schemas and the version index", (name) => {
        const s = synthTemplate(name);
        expect(keySchema(commentV2(s))).toBe("databaseId:assetId:HASH,commentId:RANGE");
        expect(commentV2(s).properties.LocalSecondaryIndexes).toEqual([
            {
                IndexName: "AssetVersionIdLSI",
                KeySchema: [
                    { AttributeName: "databaseId:assetId", KeyType: "HASH" },
                    { AttributeName: "assetVersionId", KeyType: "RANGE" },
                ],
                Projection: { ProjectionType: "ALL" },
            },
        ]);
        expect(keySchema(subscriptionV2(s))).toBe(
            "eventName:HASH,entityName:databaseId:entityId:RANGE"
        );
    });

    test.each(ALL_TEMPLATES)("%s: the V1 tables keep their logical ids and keys", (name) => {
        const s = synthTemplate(name);
        expect(keySchema(commentV1(s))).toBe("assetId:HASH,assetVersionId:commentId:RANGE");
        expect(keySchema(subscriptionV1(s))).toBe("eventName:HASH,entityName_entityId:RANGE");
    });

    test.each(ALL_TEMPLATES)(
        "%s: retained, auto-named, on-demand, point-in-time recovery",
        (name) => {
            const s = synthTemplate(name);
            for (const table of [commentV2(s), subscriptionV2(s)]) {
                expect(table.raw.DeletionPolicy).toBe("Retain");
                expect(table.raw.UpdateReplacePolicy).toBe("Retain");
                expect(table.properties.TableName).toBeUndefined();
                expect(table.properties.BillingMode).toBe("PAY_PER_REQUEST");
                expect(
                    table.properties.PointInTimeRecoverySpecification?.PointInTimeRecoveryEnabled
                ).toBe(true);
            }
        }
    );

    test.each(RESTRICTED_TEMPLATES)("%s: encrypted with the VAMS customer managed key", (name) => {
        const s = synthTemplate(name);
        for (const table of [commentV2(s), subscriptionV2(s)]) {
            expect(table.properties.SSESpecification?.SSEType).toBe("KMS");
            expect(SynthResult.flatten(table.properties.SSESpecification?.KMSMasterKeyId)).toMatch(
                /\S/
            );
        }
    });
});

describe("resource names point the handlers at V2 and the migration at V1", () => {
    const parameter = (s: SynthResult, key: string): Resource => {
        const found = s.where("AWS::SSM::Parameter", (r) =>
            SynthResult.flatten(r.properties.Name).endsWith(`/resourceNames/${key}`)
        );
        expect(found).toHaveLength(1);
        return found[0];
    };
    const namedTable = (s: SynthResult, key: string) => {
        const p = parameter(s, key);
        return resolveReference(s, p.stack, p.properties.Value);
    };

    test.each(ALL_TEMPLATES)("%s: each key names its table", (name) => {
        const s = synthTemplate(name);
        expect(namedTable(s, RESOURCE_PARAM_KEYS.dynamoTables.commentStorage)?.logicalId).toBe(
            commentV2(s).logicalId
        );
        expect(
            namedTable(s, RESOURCE_PARAM_KEYS.dynamoTables.subscriptionsStorage)?.logicalId
        ).toBe(subscriptionV2(s).logicalId);
        expect(
            namedTable(s, RESOURCE_PARAM_KEYS.dynamoTablesLegacy.commentStorageV1)?.logicalId
        ).toBe(commentV1(s).logicalId);
        expect(
            namedTable(s, RESOURCE_PARAM_KEYS.dynamoTablesLegacy.subscriptionsStorageV1)?.logicalId
        ).toBe(subscriptionV1(s).logicalId);
    });
});

describe("the comment, subscription and asset Lambdas are granted V2 only", () => {
    const handlers = [
        "handlers.comments.commentService.lambda_handler",
        "handlers.comments.addComment.lambda_handler",
        "handlers.comments.editComment.lambda_handler",
        "handlers.subscription.subscriptionService.lambda_handler",
        "handlers.subscription.checkSubscriptionService.lambda_handler",
        "handlers.subscription.unsubscribeService.lambda_handler",
        "handlers.assets.assetService.lambda_handler",
    ];

    const grantedTables = (s: SynthResult, handler: string): Set<string> => {
        const fn = lambdaByHandler(s, handler);
        const tables = new Set<string>();
        for (const statement of statementsOf(s, fn)) {
            for (const resource of ([] as unknown[]).concat(statement.Resource ?? [])) {
                const parts: unknown[] = (resource as any)?.["Fn::Join"]?.[1] ?? [resource];
                for (const part of parts) {
                    const target = resolveReference(s, fn.stack, part);
                    if (target?.type === "AWS::DynamoDB::Table") tables.add(target.logicalId);
                }
            }
        }
        return tables;
    };

    test("commercial: every handler reaches its V2 table and no V1 table", () => {
        const s = synthTemplate("commercial");
        const v1 = [commentV1(s).logicalId, subscriptionV1(s).logicalId];
        const v2 = new Set([commentV2(s).logicalId, subscriptionV2(s).logicalId]);
        for (const handler of handlers) {
            const granted = grantedTables(s, handler);
            // Control: the walk resolves at least one table for every handler.
            expect(granted.size).toBeGreaterThan(0);
            expect([...granted].some((id) => v2.has(id))).toBe(true);
            for (const id of v1) expect(granted.has(id)).toBe(false);
        }
        // resourcesOf is the flattened form; no statement names a V1 table by its logical id.
        const fn = lambdaByHandler(s, "handlers.comments.commentService.lambda_handler");
        const flattened = statementsOf(s, fn).flatMap(resourcesOf).join("\n");
        expect(flattened).not.toMatch(/CommentStorageTable[0-9A-F]{8}\b/);
    });
});
