/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Two synth-level contracts on the compliance storage layer.
 *
 * **`ComplianceAssetStateStorageTable` carries exactly two GSIs.** `SchemaNameIndex` (schemaName,
 * complianceState) serves the per-schema paths — sweep and schema-deletion checks. `ComplianceStateIndex`
 * (complianceState, databaseId) serves the cross-database quarantine list as ONE paged query keyed on
 * the `quarantined` state. The backend queries both by name, so a renamed or dropped index is a runtime
 * `ValidationException` on the first request rather than a synth error; the key schema and the ALL
 * projection are pinned too, because a KEYS_ONLY projection would make every page a fan-out of GetItem
 * calls and a swapped key order would silently query the wrong partition.
 *
 * **The default-schema seed is idempotent.** The `AwsCustomResource` that writes
 * `default-compliance-schema` puts with `attribute_not_exists(...)` so an existing row is never
 * overwritten — and that conditional put MUST ignore `ConditionalCheckFailedException`, or every Create
 * against a table that already holds the row fails the resource, which fails `apiBuilder2`, which rolls
 * back the core stack. That Create-against-existing case is ordinary, not exotic: a flag flipped
 * false→true on a deployment that seeded earlier, a construct replacement, or a `RemovalPolicy.RETAIN`
 * table adopted by a fresh stack. The Update call is the same put, so a redeploy is a no-op rather than
 * an overwrite.
 *
 * `apiBuilder2` hosts no other `Custom::AWS` resource, which is why the seed can be located by type
 * within that stack and why it keeps its own role rather than sharing one.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

const synth = () => synthTemplate("commercial");

const ASSET_STATE_TABLE_ID = /ComplianceAssetStateStorageTable/;

/**
 * The emitted template key of the nested stack built by construct id `construct`. A substring match
 * cannot separate `ApiBuilder2` from `ApiBuilder` followed by a hash beginning with `2`: CDK names a
 * nested-stack artifact `<stack name><construct path><8 hex>`, so the fixed-width suffix is stripped
 * and the remainder must END with the construct id.
 */
function nestedTemplateKeyFor(s: SynthResult, construct: string): string {
    const keys = Object.keys(s.templates)
        .map((key) => ({ key, id: key.replace(/\.nested$/, "") }))
        .filter(({ id }) => /[0-9A-F]{8}$/.test(id) && id.slice(0, -8).endsWith(construct))
        .map(({ key }) => key);
    expect(keys).toHaveLength(1);
    return keys[0];
}

function assetStateTable(s: SynthResult) {
    const matches = s.where("AWS::DynamoDB::Table", (r) => ASSET_STATE_TABLE_ID.test(r.logicalId));
    expect(matches).toHaveLength(1);
    return matches[0];
}

/** The seed custom resource's Create/Update payloads, parsed from their Fn::Join-assembled JSON. */
function complianceSeed(s: SynthResult) {
    const apiBuilder2 = nestedTemplateKeyFor(s, "ApiBuilder2");
    const seeds = s.where("Custom::AWS", (r) => r.stack === apiBuilder2);
    expect(seeds).toHaveLength(1);
    const props = seeds[0].properties;
    const parse = (value: unknown) =>
        value === undefined ? undefined : JSON.parse(SynthResult.flatten(value));
    return { create: parse(props.Create), update: parse(props.Update), del: parse(props.Delete) };
}

describe("ComplianceAssetStateStorageTable indexes", () => {
    test("[control] the table synthesizes with the databaseId / assetId key", () => {
        const table = assetStateTable(synth());
        expect(table.properties.KeySchema).toEqual([
            { AttributeName: "databaseId", KeyType: "HASH" },
            { AttributeName: "assetId", KeyType: "RANGE" },
        ]);
    });

    test("carries exactly SchemaNameIndex and ComplianceStateIndex", () => {
        const table = assetStateTable(synth());
        const names = (table.properties.GlobalSecondaryIndexes as any[]).map((g) => g.IndexName);
        expect(names.sort()).toEqual(["ComplianceStateIndex", "SchemaNameIndex"]);
    });

    test("SchemaNameIndex keys on (schemaName, complianceState) with an ALL projection", () => {
        const table = assetStateTable(synth());
        const gsi = (table.properties.GlobalSecondaryIndexes as any[]).find(
            (g) => g.IndexName === "SchemaNameIndex"
        );
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "schemaName", KeyType: "HASH" },
            { AttributeName: "complianceState", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("ComplianceStateIndex keys on (complianceState, databaseId) with an ALL projection", () => {
        const table = assetStateTable(synth());
        const gsi = (table.properties.GlobalSecondaryIndexes as any[]).find(
            (g) => g.IndexName === "ComplianceStateIndex"
        );
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "complianceState", KeyType: "HASH" },
            { AttributeName: "databaseId", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("both index key attributes are declared as strings", () => {
        const table = assetStateTable(synth());
        const declared = Object.fromEntries(
            (table.properties.AttributeDefinitions as any[]).map((a) => [
                a.AttributeName,
                a.AttributeType,
            ])
        );
        expect(declared).toMatchObject({
            databaseId: "S",
            assetId: "S",
            schemaName: "S",
            complianceState: "S",
        });
    });
});

describe("default compliance schema seed is idempotent", () => {
    test("[control] apiBuilder2 hosts exactly one Custom::AWS resource, and it is the seed put", () => {
        const { create } = complianceSeed(synth());
        expect(create.service).toBe("DynamoDB");
        expect(create.action).toBe("putItem");
        expect(create.parameters.Item.schemaName).toEqual({ S: "default-compliance-schema" });
        expect(create.parameters.Item.isSystem).toEqual({ BOOL: true });
    });

    test("the put is conditional on the row not existing", () => {
        // The condition is what protects an existing row from being overwritten by a redeploy; the
        // ignore below is what makes that protection succeed rather than fail the resource.
        const { create } = complianceSeed(synth());
        expect(create.parameters.ConditionExpression).toBe(
            "attribute_not_exists(schemaName) AND attribute_not_exists(internalVersion)"
        );
    });

    test("ConditionalCheckFailedException is ignored", () => {
        const { create, update } = complianceSeed(synth());
        expect(create.ignoreErrorCodesMatching).toBe("ConditionalCheckFailedException");
        expect(update.ignoreErrorCodesMatching).toBe("ConditionalCheckFailedException");
    });

    test("Update is the same call as Create, and there is no Delete", () => {
        // An Update that differed from Create could rewrite the row a redeploy is meant to leave alone;
        // a Delete would remove the system schema when the resource is replaced or the stack torn down.
        const { create, update, del } = complianceSeed(synth());
        expect(update).toEqual(create);
        expect(del).toBeUndefined();
    });

    test("the physical id is a stable literal, not a response field", () => {
        // `ignoreErrorCodesMatching` is incompatible with `PhysicalResourceId.fromResponse` — an ignored
        // error returns no response to read — and a physical id that changed between Create and Update
        // would make CloudFormation replace (delete) the resource on every deploy.
        const { create, update } = complianceSeed(synth());
        expect(typeof create.physicalResourceId.id).toBe("string");
        expect(create.physicalResourceId.id).toMatch(/_default_schema_initialization$/);
        expect(create.physicalResourceId.responsePath).toBeUndefined();
        expect(update.physicalResourceId).toEqual(create.physicalResourceId);
    });
});
