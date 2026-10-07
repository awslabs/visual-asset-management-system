/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `ComplianceAuditStorageTable` carries exactly three GSIs, each serving one listing of the audit
 * service. `AssetIndex` (databaseId:assetId, timestamp) is the per-asset history. `EventTypeIndex`
 * (eventType, timestamp) is the listing filtered to one event type. `AuditByDateGSI`
 * (allListPartition, timestamp) is the unfiltered listing: every audit row carries the constant
 * `allListPartition = "audit"`, so the newest-first global listing is ONE paged query — the same shape
 * as the `*ByDateGSI` indexes on the pipeline, workflow and workflow-execution V2 tables.
 *
 * The backend queries each index by name, so a renamed or dropped index is a runtime
 * `ValidationException` on the first request rather than a synth error; the key schema and the ALL
 * projection are pinned too, because a KEYS_ONLY projection would make every page a fan-out of GetItem
 * calls and a swapped key order would query the wrong partition. `timestamp` is the sort key of all
 * three, which is what makes `ScanIndexForward: false` mean newest-first on each.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

const synth = () => synthTemplate("commercial");

const AUDIT_TABLE_ID = /ComplianceAuditStorageTable/;

type KeyElement = { AttributeName: string; KeyType: string };
type Gsi = { IndexName: string; KeySchema: KeyElement[]; Projection: { ProjectionType: string } };
type AttributeDefinition = { AttributeName: string; AttributeType: string };

function auditTable(s: SynthResult) {
    const matches = s.where("AWS::DynamoDB::Table", (r) => AUDIT_TABLE_ID.test(r.logicalId));
    expect(matches).toHaveLength(1);
    return matches[0];
}

function indexNamed(s: SynthResult, name: string) {
    const gsi = (auditTable(s).properties.GlobalSecondaryIndexes as Gsi[]).find(
        (g) => g.IndexName === name
    );
    expect(gsi).toBeDefined();
    return gsi as Gsi;
}

describe("ComplianceAuditStorageTable indexes", () => {
    test("[control] the table synthesizes with the entryId key", () => {
        const table = auditTable(synth());
        expect(table.properties.KeySchema).toEqual([{ AttributeName: "entryId", KeyType: "HASH" }]);
    });

    test("carries exactly AssetIndex, EventTypeIndex and AuditByDateGSI", () => {
        const table = auditTable(synth());
        const names = (table.properties.GlobalSecondaryIndexes as Gsi[]).map((g) => g.IndexName);
        expect(names.sort()).toEqual(["AssetIndex", "AuditByDateGSI", "EventTypeIndex"]);
    });

    test("AssetIndex keys on (databaseId:assetId, timestamp) with an ALL projection", () => {
        const gsi = indexNamed(synth(), "AssetIndex");
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "databaseId:assetId", KeyType: "HASH" },
            { AttributeName: "timestamp", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("EventTypeIndex keys on (eventType, timestamp) with an ALL projection", () => {
        const gsi = indexNamed(synth(), "EventTypeIndex");
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "eventType", KeyType: "HASH" },
            { AttributeName: "timestamp", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("AuditByDateGSI keys on (allListPartition, timestamp) with an ALL projection", () => {
        const gsi = indexNamed(synth(), "AuditByDateGSI");
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "allListPartition", KeyType: "HASH" },
            { AttributeName: "timestamp", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("every index key attribute is declared as a string", () => {
        const table = auditTable(synth());
        const declared = Object.fromEntries(
            (table.properties.AttributeDefinitions as AttributeDefinition[]).map((a) => [
                a.AttributeName,
                a.AttributeType,
            ])
        );
        expect(declared).toEqual({
            entryId: "S",
            "databaseId:assetId": "S",
            eventType: "S",
            allListPartition: "S",
            timestamp: "S",
        });
    });
});
