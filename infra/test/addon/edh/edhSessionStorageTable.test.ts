/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Shape of the EDH Workstations tables as the add-on stack emits them. The session table: partition
 * key `bootstrapId`, the `sessionUuidIndex` and `userIdIndex` global secondary indexes, TTL on
 * `expiresAt`. The AMI recipe table: `pk`/`sk`, the `imageBuildArnIndex` global secondary index, no
 * TTL. Both: the add-on table defaults (RETAIN, PITR, on-demand, auto-named), no stream, and created
 * in the add-on's own nested template rather than the storage stack's.
 *
 * Synthesized from the commercial template with the add-on and AMI builds on, with the customer
 * managed key off (the template's own setting) and on, since the encryption type follows the key.
 *
 * A separate app holds one table that DOES set a name, deletion protection and a stream. No VAMS table
 * sets the first two, so without it the `toBeUndefined` assertions would pass on a misspelled property
 * name as readily as on a real absence.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Template } from "aws-cdk-lib/assertions";
import { Resource, SynthResult, expectAbsent, synthTemplate } from "../../support/templateSynth";
import { newTestApp } from "../../support/testApp";
import { enableEdhAmiBuilds, inEdhStack } from "./support/edhSynth";

const withEdh = (): SynthResult =>
    synthTemplate("commercial", { mutate: enableEdhAmiBuilds, mutateKey: "edhAmiBuilds" });

const withEdhAndCmk = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            enableEdhAmiBuilds(c);
            c.app.useKmsCmkEncryption.enabled = true;
        },
        mutateKey: "edhAmiBuildsCmk",
    });

const keySchemaOf = (r: Resource): string => JSON.stringify(r.properties.KeySchema ?? []);

/** The one table whose key schema is a lone `bootstrapId` hash key. */
const sessionTable = (s: SynthResult): Resource => {
    const matches = s.where(
        "AWS::DynamoDB::Table",
        (r) =>
            keySchemaOf(r) === JSON.stringify([{ AttributeName: "bootstrapId", KeyType: "HASH" }])
    );
    expect(matches).toHaveLength(1);
    return matches[0];
};

/** The one table carrying `imageBuildArnIndex`. */
const amiRecipeTable = (s: SynthResult): Resource => {
    const matches = s.where("AWS::DynamoDB::Table", (r) =>
        (r.properties.GlobalSecondaryIndexes ?? []).some(
            (gsi: any) => gsi.IndexName === "imageBuildArnIndex"
        )
    );
    expect(matches).toHaveLength(1);
    return matches[0];
};

const indexesByName = (r: Resource): Record<string, any> =>
    Object.fromEntries(
        (r.properties.GlobalSecondaryIndexes as any[]).map((gsi) => [gsi.IndexName, gsi])
    );

describe("EdhSessionStorageTable", () => {
    test("keys on bootstrapId alone", () => {
        const table = sessionTable(withEdh());
        expect(table.properties.AttributeDefinitions).toEqual(
            expect.arrayContaining([{ AttributeName: "bootstrapId", AttributeType: "S" }])
        );
    });

    test("carries sessionUuidIndex on sessionId and userIdIndex on userId + createdAt", () => {
        const byName = indexesByName(sessionTable(withEdh()));
        expect(Object.keys(byName).sort()).toEqual(["sessionUuidIndex", "userIdIndex"]);

        expect(byName.sessionUuidIndex.KeySchema).toEqual([
            { AttributeName: "sessionId", KeyType: "HASH" },
        ]);
        expect(byName.sessionUuidIndex.Projection).toEqual({ ProjectionType: "ALL" });

        expect(byName.userIdIndex.KeySchema).toEqual([
            { AttributeName: "userId", KeyType: "HASH" },
            { AttributeName: "createdAt", KeyType: "RANGE" },
        ]);
        expect(byName.userIdIndex.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("expires rows on the expiresAt attribute", () => {
        expect(sessionTable(withEdh()).properties.TimeToLiveSpecification).toEqual({
            AttributeName: "expiresAt",
            Enabled: true,
        });
    });
});

describe("EdhAmiRecipeStorageTable", () => {
    test("keys on pk + sk and carries imageBuildArnIndex on imageBuildVersionArn", () => {
        const table = amiRecipeTable(withEdh());
        expect(table.properties.KeySchema).toEqual([
            { AttributeName: "pk", KeyType: "HASH" },
            { AttributeName: "sk", KeyType: "RANGE" },
        ]);
        const byName = indexesByName(table);
        expect(Object.keys(byName)).toEqual(["imageBuildArnIndex"]);
        expect(byName.imageBuildArnIndex.KeySchema).toEqual([
            { AttributeName: "imageBuildVersionArn", KeyType: "HASH" },
        ]);
        expect(byName.imageBuildArnIndex.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("has no TTL", () => {
        expect(amiRecipeTable(withEdh()).properties.TimeToLiveSpecification).toBeUndefined();
        // Control: the session table in the same synth carries one, under the property name read.
        expect(sessionTable(withEdh()).properties.TimeToLiveSpecification).toBeDefined();
    });
});

describe("EDH tables", () => {
    let controlTemplate: Template;

    beforeAll(() => {
        const app = newTestApp();
        const controlStack = new cdk.Stack(app, "Control", {
            env: { account: "123456789012", region: "us-east-1" },
        });
        new dynamodb.Table(controlStack, "ControlTable", {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
            tableName: "control-table",
            deletionProtection: true,
            stream: dynamodb.StreamViewType.NEW_IMAGE,
        });
        controlTemplate = Template.fromStack(controlStack);
    });

    test("are created in the add-on stack and not in the storage stack", () => {
        const s = withEdh();
        for (const table of [sessionTable(s), amiRecipeTable(s)]) {
            expect(inEdhStack(table)).toBe(true);
        }
        const storageTables = s.where("AWS::DynamoDB::Table", (r) =>
            /StorageResourcesBuilder/.test(r.stack)
        );
        expect(storageTables.length).toBeGreaterThan(0);
        expectAbsent(
            "EDH-keyed tables in the storage stack",
            storageTables.filter(
                (r) =>
                    keySchemaOf(r).includes('"bootstrapId"') ||
                    (r.properties.GlobalSecondaryIndexes ?? []).some(
                        (gsi: any) => gsi.IndexName === "imageBuildArnIndex"
                    )
            ),
            { description: "the storage stack emits tables", count: storageTables.length }
        );
    });

    test("use the add-on table defaults and add no stream or explicit name", () => {
        const s = withEdh();
        for (const table of [sessionTable(s), amiRecipeTable(s)]) {
            expect(table.raw.DeletionPolicy).toBe("Retain");
            expect(table.raw.UpdateReplacePolicy).toBe("Retain");
            expect(table.properties.BillingMode).toBe("PAY_PER_REQUEST");
            expect(table.properties.PointInTimeRecoverySpecification).toEqual({
                PointInTimeRecoveryEnabled: true,
            });
            expect(table.properties.TableName).toBeUndefined();
            expect(table.properties.DeletionProtectionEnabled).toBeUndefined();
            expect(table.properties.StreamSpecification).toBeUndefined();
        }

        // Control 1: the indexer-fed core tables in the same synth do carry a stream, so the
        // stream absence above is read from where CDK writes the property.
        const streamed = s
            .ofType("AWS::DynamoDB::Table")
            .filter((t) => t.properties.StreamSpecification !== undefined);
        expect(streamed.length).toBeGreaterThan(0);

        // Control 2: the control stack's table sets all three, under the same property names
        // the three toBeUndefined assertions read. No VAMS table sets the first two, so this is
        // the only positive control they have.
        const control = Object.values(controlTemplate.findResources("AWS::DynamoDB::Table"));
        expect(control).toHaveLength(1);
        expect(control[0].Properties.TableName).toBe("control-table");
        expect(control[0].Properties.DeletionProtectionEnabled).toBe(true);
        expect(control[0].Properties.StreamSpecification).toEqual({ StreamViewType: "NEW_IMAGE" });
    });

    test("take the AWS managed key without the customer managed key, and the shared key with it", () => {
        const off = withEdh();
        for (const table of [sessionTable(off), amiRecipeTable(off)]) {
            // CDK writes the AWS managed key as SSEEnabled alone.
            expect(table.properties.SSESpecification).toEqual({ SSEEnabled: true });
        }

        const on = withEdhAndCmk();
        for (const table of [sessionTable(on), amiRecipeTable(on)]) {
            expect(table.properties.SSESpecification).toEqual(
                expect.objectContaining({ SSEEnabled: true, SSEType: "KMS" })
            );
            // The storage stack's key, passed into the add-on stack; the add-on creates no key of
            // its own for a table.
            expect(table.properties.SSESpecification.KMSMasterKeyId).toBeDefined();
        }
        const edhTableKeys = on.where(
            "AWS::KMS::Key",
            (k) => inEdhStack(k) && !/EdhSessionSecretsKey/.test(k.logicalId)
        );
        expect(edhTableKeys).toEqual([]);
    });
});
