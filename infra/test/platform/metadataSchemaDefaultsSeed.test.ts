/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The default metadata schemas are seeded once and refreshed only while no user has edited them.
 * Create writes a default unless an edited row already exists; update rewrites a row only while it
 * exists and its `modifiedBy` is still the seeding identity. Both ignore the conditional-check
 * failure that a skipped write raises. The calls carry no synth-time value, so a deployment that
 * changes nothing here sends CloudFormation no update for them.
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Template } from "aws-cdk-lib/assertions";
import { DynamoDbMetadataSchemaDefaultsConstruct } from "../../lib/nestedStacks/apiLambda/constructs/dynamodb-metadataschema-defaults-construct";
import { newTestApp } from "../support/testApp";

const TABLE = "metadata-schema-table";
const SCHEMA_IDS = [
    "default-assetlinks-schema",
    "default-database-schema",
    "default-asset-schema",
    "default-file-schema-3dmodel",
];

function synth(flags: Partial<Record<string, boolean>> = {}): Template {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "MetadataSchemaDefaultsTestStack");
    const customResourceRole = new iam.Role(stack, "CustomResourceRole", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    new DynamoDbMetadataSchemaDefaultsConstruct(stack, "MetadataSchemaDefaults", {
        customResourceRole,
        lambdaCommonBaseLayer: undefined as any,
        storageResources: {
            dynamo: { metadataSchemaStorageTableV2: { tableName: TABLE } },
        } as any,
        config: {
            app: {
                metadataSchema: {
                    autoLoadDefaultAssetLinksSchema: true,
                    autoLoadDefaultDatabaseSchema: true,
                    autoLoadDefaultAssetSchema: true,
                    autoLoadDefaultAssetFileSchema: true,
                    ...flags,
                },
            },
        } as any,
    });
    return Template.fromStack(stack);
}

/** Each seed resource's Create and Update payloads, parsed. */
function seeds(template: Template): Array<{ create: any; update: any; raw: any }> {
    return Object.values(template.findResources("Custom::AWS")).map((r: any) => ({
        create: JSON.parse(r.Properties.Create),
        update: JSON.parse(r.Properties.Update),
        raw: r.Properties,
    }));
}

describe("default metadata schema seeding", () => {
    const template = synth();
    const all = seeds(template);

    test("all four defaults are seeded (control)", () => {
        expect(all.map((s) => s.create.parameters.Item.metadataSchemaId.S).sort()).toEqual(
            [...SCHEMA_IDS].sort()
        );
    });

    test("create writes only a missing or unedited default", () => {
        all.forEach(({ create }) => {
            expect(create.action).toBe("putItem");
            expect(create.parameters.TableName).toBe(TABLE);
            expect(create.parameters.ConditionExpression).toBe(
                "attribute_not_exists(metadataSchemaId) OR modifiedBy IN (:systemUser, :legacySystem)"
            );
            expect(create.ignoreErrorCodesMatching).toBe("ConditionalCheckFailedException");
        });
    });

    test("update refreshes only an existing, unedited default", () => {
        all.forEach(({ create, update }) => {
            expect(update.parameters.ConditionExpression).toBe(
                "attribute_exists(metadataSchemaId) AND modifiedBy IN (:systemUser, :legacySystem)"
            );
            expect(update.ignoreErrorCodesMatching).toBe("ConditionalCheckFailedException");
            expect(update.parameters.Item).toEqual(create.parameters.Item);
        });
    });

    test("the seeding identity is matched in both spellings and written in one", () => {
        all.forEach(({ create, update }) => {
            for (const call of [create, update]) {
                expect(call.parameters.ExpressionAttributeValues).toEqual({
                    ":systemUser": { S: "SYSTEM_USER" },
                    ":legacySystem": { S: "SYSTEM" },
                });
                expect(call.parameters.Item.modifiedBy).toEqual({ S: "SYSTEM_USER" });
                expect(call.parameters.Item.createdBy).toEqual({ S: "SYSTEM_USER" });
            }
        });
    });

    test("the items carry no synth-time timestamp", () => {
        all.forEach(({ create }) => {
            expect(create.parameters.Item.dateCreated).toBeUndefined();
            expect(create.parameters.Item.dateModified).toBeUndefined();
        });
    });

    test("a second synth produces identical seed calls", () => {
        const again = seeds(synth());
        expect(again.map((s) => s.raw)).toEqual(all.map((s) => s.raw));
    });

    test("a disabled flag drops only its own seed", () => {
        const withoutAsset = seeds(synth({ autoLoadDefaultAssetSchema: false }));
        expect(withoutAsset.map((s) => s.create.parameters.Item.metadataSchemaId.S).sort()).toEqual(
            SCHEMA_IDS.filter((id) => id !== "default-asset-schema").sort()
        );
    });
});
