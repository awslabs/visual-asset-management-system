/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `AssetStorageTable` carries `AssetLocationGSI`: the asset rows by bucket name (`locationBucket`) and
 * stored location (`locationKey`), which `backend/backend/common/assetLocations.py` queries by name. A
 * renamed index or attribute is a runtime `ValidationException` (or an empty answer), not a synth error,
 * so the index, its key schema and its INCLUDE projection of `bucketId` are pinned here. The table keeps
 * its other two indexes unchanged, so an upgrade adds exactly one GSI in one update (DynamoDB accepts one
 * GSI create per `UpdateTable`), and the table stays on-demand with point-in-time recovery.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

let cached: SynthResult | undefined;
const synth = (): SynthResult => (cached ??= synthTemplate("commercial"));

function assetTable(s: SynthResult) {
    const matches = s.where("AWS::DynamoDB::Table", (r) => /^AssetStorageTable/.test(r.logicalId));
    expect(matches).toHaveLength(1);
    return matches[0];
}

const gsis = (s: SynthResult): any[] => assetTable(s).properties.GlobalSecondaryIndexes as any[];

describe("AssetStorageTable location index", () => {
    test("[control] the table keys on databaseId / assetId", () => {
        expect(assetTable(synth()).properties.KeySchema).toEqual([
            { AttributeName: "databaseId", KeyType: "HASH" },
            { AttributeName: "assetId", KeyType: "RANGE" },
        ]);
    });

    test("carries BucketIdGSI, assetIdGSI and AssetLocationGSI, one index more than before", () => {
        expect(
            gsis(synth())
                .map((g) => g.IndexName)
                .sort()
        ).toEqual(["AssetLocationGSI", "BucketIdGSI", "assetIdGSI"]);
    });

    test("AssetLocationGSI keys on (locationBucket, locationKey) and projects bucketId", () => {
        const gsi = gsis(synth()).find((g) => g.IndexName === "AssetLocationGSI");
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "locationBucket", KeyType: "HASH" },
            { AttributeName: "locationKey", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({
            ProjectionType: "INCLUDE",
            NonKeyAttributes: ["bucketId"],
        });
    });

    test("the existing indexes are unchanged", () => {
        const byName = Object.fromEntries(gsis(synth()).map((g) => [g.IndexName, g]));
        expect(byName.BucketIdGSI.KeySchema).toEqual([
            { AttributeName: "bucketId", KeyType: "HASH" },
            { AttributeName: "assetId", KeyType: "RANGE" },
        ]);
        expect(byName.assetIdGSI.KeySchema).toEqual([
            { AttributeName: "assetId", KeyType: "HASH" },
            { AttributeName: "databaseId", KeyType: "RANGE" },
        ]);
        expect(byName.BucketIdGSI.Projection).toEqual({ ProjectionType: "ALL" });
        expect(byName.assetIdGSI.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("the index key attributes are strings and the billing is unchanged", () => {
        const table = assetTable(synth());
        const declared = Object.fromEntries(
            (table.properties.AttributeDefinitions as any[]).map((a) => [
                a.AttributeName,
                a.AttributeType,
            ])
        );
        expect(declared).toMatchObject({ locationBucket: "S", locationKey: "S" });
        expect(table.properties.BillingMode).toBe("PAY_PER_REQUEST");
        expect(table.properties.PointInTimeRecoverySpecification).toEqual({
            PointInTimeRecoveryEnabled: true,
        });
    });
});
