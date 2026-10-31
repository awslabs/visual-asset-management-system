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
 * **The default-schema seed is stable and idempotent.** A Lambda-backed custom resource writes
 * `default-compliance-schema` (`backend/backend/customResources/complianceDefaultSchemaSeed.py`, whose
 * conditional put and its no-op on an existing row are tested in
 * `backend/tests/handlers/compliance/test_complianceDefaultSchemaSeed.py`). The synth-level contracts:
 * the properties carry the row without a timestamp, so two synths of the same source emit the same
 * `apiBuilder2` template (the handler stamps `registeredAt` when it writes) and a `cdk diff` of an
 * unchanged source shows no change; the handler can issue `dynamodb:PutItem` on the schema table and
 * nothing else, so it cannot touch a binding or rewrite anything beside the row; and the previous
 * `AwsCustomResource` seed is gone, so an upgrade creates the new resource (a conditional put that
 * leaves the existing row alone) and deletes the old one, which had no Delete call.
 */

import * as fs from "fs";
import * as path from "path";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

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

const SEED_HANDLER = "customResources.complianceDefaultSchemaSeed.lambda_handler";

/** The seed custom resource in apiBuilder2, with its properties and the row it writes. */
function complianceSeed(s: SynthResult) {
    const apiBuilder2 = nestedTemplateKeyFor(s, "ApiBuilder2");
    const seeds = s.where(
        "AWS::CloudFormation::CustomResource",
        (r) => r.stack === apiBuilder2 && /ComplianceDefaultSchemaSeed/.test(r.logicalId)
    );
    expect(seeds).toHaveLength(1);
    const props = seeds[0].properties;
    return { resource: seeds[0], props, item: JSON.parse(props.Item) };
}

/** The seed's handler function and every IAM statement attached to its execution role. */
function seedFunctionStatements(s: SynthResult): { fn: Resource; statements: any[] } {
    const apiBuilder2 = nestedTemplateKeyFor(s, "ApiBuilder2");
    const fns = s.where(
        "AWS::Lambda::Function",
        (r) => r.stack === apiBuilder2 && r.properties.Handler === SEED_HANDLER
    );
    expect(fns).toHaveLength(1);
    const roleId = fns[0].properties.Role["Fn::GetAtt"][0];
    const statements = s
        .where(
            "AWS::IAM::Policy",
            (r) =>
                r.stack === apiBuilder2 &&
                (r.properties.Roles ?? []).some((role: any) => role.Ref === roleId)
        )
        .flatMap((p) => p.properties.PolicyDocument.Statement);
    return { fn: fns[0], statements };
}

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

describe("ComplianceAssetStateStorageTable indexes", () => {
    test("[control] the table synthesizes with the databaseId / assetId key", () => {
        const table = assetStateTable(synth());
        expect(table.properties.KeySchema).toEqual([
            { AttributeName: "databaseId", KeyType: "HASH" },
            { AttributeName: "assetId", KeyType: "RANGE" },
        ]);
    });

    test("carries exactly SchemaNameIndex, ComplianceStateIndex and DatabaseAssetNameIndex", () => {
        const table = assetStateTable(synth());
        const names = (table.properties.GlobalSecondaryIndexes as any[]).map((g) => g.IndexName);
        expect(names.sort()).toEqual([
            "ComplianceStateIndex",
            "DatabaseAssetNameIndex",
            "SchemaNameIndex",
        ]);
    });

    test("DatabaseAssetNameIndex keys on (databaseId, assetNameSort) with an ALL projection", () => {
        // The overview's name sort. Added alone in its update: an existing table accepts only one
        // new GSI per CloudFormation update.
        const table = assetStateTable(synth());
        const gsi = (table.properties.GlobalSecondaryIndexes as any[]).find(
            (g) => g.IndexName === "DatabaseAssetNameIndex"
        );
        expect(gsi.KeySchema).toEqual([
            { AttributeName: "databaseId", KeyType: "HASH" },
            { AttributeName: "assetNameSort", KeyType: "RANGE" },
        ]);
        expect(gsi.Projection).toEqual({ ProjectionType: "ALL" });
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
            assetNameSort: "S",
        });
    });
});

describe("default compliance schema seed", () => {
    test("[control] apiBuilder2 hosts the seed, and it writes the GLOBAL system schema", () => {
        const { item } = complianceSeed(synth());
        expect(item.schemaName).toEqual({ S: "default-compliance-schema" });
        expect(item.internalVersion).toEqual({ N: "1" });
        expect(item.databaseId).toEqual({ S: "GLOBAL" });
        expect(item.isSystem).toEqual({ BOOL: true });
        expect(item.registeredBy).toEqual({ S: "SYSTEM_USER" });
        expect(JSON.parse(item.schemaBody.S).schemaFormat).toBe("vams-rules-v1");
    });

    test("the row carries no timestamp: the handler stamps registeredAt when it writes", () => {
        // A value fixed at synth time is the time `cdk synth` ran, not the time the row was written,
        // and it changes the template on every synth.
        const { props, item } = complianceSeed(synth());
        expect(item.registeredAt).toBeUndefined();
        expect(Object.keys(props).sort()).toEqual(["Item", "ServiceToken", "TableName"]);
        expect(JSON.stringify(props.Item)).not.toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/);
    });

    test("two synths of the same source emit the same apiBuilder2 template", () => {
        // The second synth runs with the clock moved a year on, so any value derived from the time
        // of the synth differs between the two templates.
        const first = synth();
        jest.useFakeTimers({
            now: Date.now() + 365 * 24 * 3600 * 1000,
            doNotFake: [
                "hrtime",
                "nextTick",
                "performance",
                "queueMicrotask",
                "requestAnimationFrame",
                "cancelAnimationFrame",
                "requestIdleCallback",
                "cancelIdleCallback",
                "setImmediate",
                "clearImmediate",
                "setInterval",
                "clearInterval",
                "setTimeout",
                "clearTimeout",
            ],
        });
        let second: SynthResult;
        try {
            second = synthTemplate("commercial", {
                mutate: () => undefined,
                mutateKey: "seed-stability-second-synth",
            });
        } finally {
            jest.useRealTimers();
        }
        const key = nestedTemplateKeyFor(first, "ApiBuilder2");
        expect(nestedTemplateKeyFor(second, "ApiBuilder2")).toBe(key);
        // The top-level Metadata is left out: `setupSecurityAndLoggingEnvironmentAndPermissions`
        // adds its stack-level cdk-nag suppression once per stack address per process, so a second
        // synth in the same jest worker emits it without that block. A `cdk synth` runs one app.
        const body = (template: any) => {
            const rest = { ...template };
            delete rest.Metadata;
            return rest;
        };
        expect(Object.keys(body(first.templates[key]))).toContain("Resources");
        expect(body(second.templates[key])).toEqual(body(first.templates[key]));
    });

    test("the handler module exists where the function's handler string points", () => {
        const { fn } = seedFunctionStatements(synth());
        expect(fn.properties.Handler).toBe(SEED_HANDLER);
        const handlerFile = path.join(
            __dirname,
            "../../../backend/backend/customResources/complianceDefaultSchemaSeed.py"
        );
        expect(fs.existsSync(handlerFile)).toBe(true);
    });

    test("the handler can issue dynamodb:PutItem and no other DynamoDB action", () => {
        // A PutItem conditional on the key not existing is the whole write. No UpdateItem,
        // DeleteItem or BatchWriteItem, and no grant on a binding or any other table, so the seed
        // cannot reset what an administrator set.
        const { statements } = seedFunctionStatements(synth());
        const dynamo = statements.filter((st) =>
            actionsOf(st).some((a) => a.startsWith("dynamodb:"))
        );
        expect(dynamo).toHaveLength(1);
        expect(actionsOf(dynamo[0])).toEqual(["dynamodb:PutItem"]);
        const resources = Array.isArray(dynamo[0].Resource)
            ? dynamo[0].Resource
            : [dynamo[0].Resource];
        expect(resources).toHaveLength(1);
        expect(JSON.stringify(resources[0])).toMatch(/ComplianceSchemaStorageTable/);
    });

    test("the seed writes to the compliance schema table", () => {
        const { props } = complianceSeed(synth());
        expect(JSON.stringify(props.TableName)).toMatch(/ComplianceSchemaStorageTable/);
    });

    test("the previous AwsCustomResource seed is gone from apiBuilder2", () => {
        // An upgrade then creates the new seed (a conditional put that leaves the existing row
        // alone) and deletes the old resource, whose Delete made no call.
        const s = synth();
        const apiBuilder2 = nestedTemplateKeyFor(s, "ApiBuilder2");
        expect(s.where("Custom::AWS", (r) => r.stack === apiBuilder2)).toHaveLength(0);
    });

    test("the seeded description stays true once autoLoadDefaultSchema is turned off", () => {
        // The row outlives the seed: turning the flag off removes the custom resource but not the
        // row, so its description names the flag that wrote it and says deployments leave it alone,
        // rather than claiming the deployment provides it.
        const { item } = complianceSeed(synth());
        const description: string = item.description.S;
        expect(description).toContain("app.compliance.autoLoadDefaultSchema");
        expect(description).toContain("do not change or remove it");
        expect(description).not.toContain("Deployed automatically");
    });
});
