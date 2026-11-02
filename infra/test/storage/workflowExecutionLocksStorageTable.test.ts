/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: WorkflowExecutionLocksStorageTable across every shipped config template.
 *
 * The table holds one row per concurrency lock a running workflow execution holds (perAsset,
 * perInputFile, perInputFileVersion). It is created unconditionally, follows the storage builder's
 * shared table settings (retained, auto-named, on-demand, point-in-time recovery, the VAMS customer
 * managed key when `app.useKmsCmkEncryption.enabled`), carries TTL on `expiresAt`, and publishes its
 * name as one resource-name SSM parameter.
 */

import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    synthTemplate,
} from "../support/templateSynth";

// Three full-app synths at ~20-25 s each (shared with other suites through the synth cache).
jest.setTimeout(600_000);

const keyNames = (r: Resource): string =>
    ((r.properties.KeySchema ?? []) as any[]).map((k) => k.AttributeName).join(",");

/** The one table keyed by lockKey alone; throws rather than returning nothing. */
function lockTable(s: SynthResult): Resource {
    const found = s.where("AWS::DynamoDB::Table", (r) => keyNames(r) === "lockKey");
    if (found.length !== 1) {
        throw new Error(`${s.name}: expected one table keyed lockKey, found ${found.length}`);
    }
    return found[0];
}

describe("WorkflowExecutionLocksStorageTable", () => {
    test.each(ALL_TEMPLATES)("%s: one table, keyed lockKey, with TTL on expiresAt", (name) => {
        const table = lockTable(synthTemplate(name));
        expect(table.logicalId).toMatch(/^WorkflowExecutionLocksStorageTable/);
        expect(table.properties.AttributeDefinitions).toEqual([
            { AttributeName: "lockKey", AttributeType: "S" },
        ]);
        expect(table.properties.TimeToLiveSpecification).toEqual({
            AttributeName: "expiresAt",
            Enabled: true,
        });
        expect(table.properties.StreamSpecification).toBeUndefined();
    });

    test.each(ALL_TEMPLATES)(
        "%s: retained on delete and replace, no TableName, on-demand, point-in-time recovery",
        (name) => {
            const table = lockTable(synthTemplate(name));
            expect(table.raw.DeletionPolicy).toBe("Retain");
            expect(table.raw.UpdateReplacePolicy).toBe("Retain");
            // Auto-named like every VAMS table, so a retained table left by a torn-down or rolled-back
            // stack never collides with the table a redeploy of the same stack name creates.
            expect(table.properties.TableName).toBeUndefined();
            expect(table.properties.BillingMode).toBe("PAY_PER_REQUEST");
            expect(
                table.properties.PointInTimeRecoverySpecification?.PointInTimeRecoveryEnabled
            ).toBe(true);
        }
    );

    test.each(RESTRICTED_TEMPLATES)("%s: encrypted with the VAMS customer managed key", (name) => {
        const table = lockTable(synthTemplate(name));
        expect(table.properties.SSESpecification?.SSEEnabled).toBe(true);
        expect(table.properties.SSESpecification?.SSEType).toBe("KMS");
        expect(SynthResult.flatten(table.properties.SSESpecification?.KMSMasterKeyId)).toMatch(/\S/);
    });

    test("commercial ships the customer managed key off, so the table names no key", () => {
        const table = lockTable(synthTemplate("commercial"));
        expect(table.properties.SSESpecification?.KMSMasterKeyId).toBeUndefined();
    });
});

describe("ResourceNamesBuilder publishes the lock table name", () => {
    const paramsEndingWith = (s: SynthResult, suffix: string) =>
        s.where("AWS::SSM::Parameter", (r) =>
            SynthResult.flatten(r.properties.Name).endsWith(`/resourceNames/${suffix}`)
        );

    test.each(ALL_TEMPLATES)("%s: exactly one parameter for the lock table key", (name) => {
        expect(
            paramsEndingWith(
                synthTemplate(name),
                RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionLocksStorage
            )
        ).toHaveLength(1);
    });
});
