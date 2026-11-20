/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** The GLOBAL "Miris stream" schema seed follows the core default-schema seed contract. */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Template } from "aws-cdk-lib/assertions";
import { DynamoDbMetadataSchemaMirisConstruct } from "../../lib/nestedStacks/addon/miris/constructs/dynamodb-metadataschema-miris-construct";
import mirisStreamSchema from "../../lib/nestedStacks/addon/miris/constructs/dynamodb-metadataschema-miris-stream.json";
import { SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT } from "../../lib/helper/seedCustomResource";
import { newTestApp } from "../support/testApp";

const TABLE = "metadata-schema-table";

function synth(): Template {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "MirisStreamSchemaSeedTestStack");
    const customResourceRole = new iam.Role(stack, "CustomResourceRole", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    new DynamoDbMetadataSchemaMirisConstruct(stack, "MetadataSchemaMiris", {
        customResourceRole,
        storageResources: {
            dynamo: { metadataSchemaStorageTableV2: { tableName: TABLE } },
        } as any,
    });
    return Template.fromStack(stack);
}

/** The seed resource's Create and Update payloads, parsed. */
function seed(template: Template): { create: any; update: any; raw: any } {
    const resources = Object.values(template.findResources("Custom::AWS"));
    expect(resources).toHaveLength(1);
    const r: any = resources[0];
    return {
        create: JSON.parse(r.Properties.Create),
        update: JSON.parse(r.Properties.Update),
        raw: r.Properties,
    };
}

describe("Miris stream metadata schema seeding", () => {
    const { create, update, raw } = seed(synth());

    test("create writes the schema unless an edited row exists", () => {
        expect(create.action).toBe("putItem");
        expect(create.parameters.TableName).toBe(TABLE);
        const item = create.parameters.Item;
        expect(item.metadataSchemaId).toEqual({ S: mirisStreamSchema.metadataSchemaId });
        expect(item["databaseId:metadataEntityType"]).toEqual({ S: "GLOBAL:fileMetadata" });
        expect(item.fileKeyTypeRestriction).toEqual({
            S: mirisStreamSchema.fileKeyTypeRestriction,
        });
        expect(JSON.parse(item.fields.S)).toEqual({ fields: mirisStreamSchema.fields });
        expect(create.parameters.ConditionExpression).toBe(
            "attribute_not_exists(metadataSchemaId) OR modifiedBy = :systemUser"
        );
    });

    test("update refreshes only an existing, unedited row with the same item", () => {
        expect(update.parameters.ConditionExpression).toBe(
            "attribute_exists(metadataSchemaId) AND modifiedBy = :systemUser"
        );
        expect(update.parameters.Item).toEqual(create.parameters.Item);
    });

    test("both calls write and match SYSTEM_USER and ignore a skipped write", () => {
        for (const call of [create, update]) {
            expect(call.ignoreErrorCodesMatching).toBe("ConditionalCheckFailedException");
            expect(call.parameters.ExpressionAttributeValues).toEqual({
                ":systemUser": { S: "SYSTEM_USER" },
            });
            expect(call.parameters.Item.modifiedBy).toEqual({ S: "SYSTEM_USER" });
            expect(call.parameters.Item.createdBy).toEqual({ S: "SYSTEM_USER" });
        }
    });

    test("the seed carries the shared ServiceTimeout and no synth-time value", () => {
        expect(raw.ServiceTimeout).toBe(
            SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT.toSeconds().toString()
        );
        expect(create.parameters.Item.dateCreated).toBeUndefined();
        expect(create.parameters.Item.dateModified).toBeUndefined();
        expect(seed(synth()).raw).toEqual(raw);
    });
});
