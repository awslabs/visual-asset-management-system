/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — the two vector-search storage tables across every shipped config template.
 *
 * `VectorEmbeddingsStorageTable` is created unconditionally; its vector index and the
 * AttributeDefinitions of the index's filter attributes exist only when `app.vectorSearch.enabled`.
 * The commercial template ships with vector search ON and the two restricted templates ship with it
 * OFF, so each template is synthesized twice — as shipped and with the flag flipped — and every
 * negative arm carries the positive arm of the same template as its control.
 *
 * `getConfig()` rejects `vectorSearch.enabled` in the EU Sovereign partition (no DynamoDB vector
 * search there). The harness bypasses `getConfig()`, so the eusovereign ON arm still synthesizes; it
 * proves the storage stack's branch keys on the flag alone and carries no partition gate of its own,
 * which is what lets the config rule be the single place that decision is made.
 *
 * The harness derives `vectorIndexName` after each arm's mutator has run (`buildConfig()` in
 * test/support/templateSynth.ts), so a restricted ON arm names its index from the block it was
 * mutated to. The ON assertions check the name is defined before comparing it, because an equality
 * of two undefineds passes.
 */

import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";
import { vectorIndexNameFor } from "../support/vectorIndexName";

// Six full-app synths (three shipped, three with the flag flipped) at ~20-25 s each.
jest.setTimeout(900_000);

const TEMPLATES: Record<TemplateName, any> = {
    commercial: commercialTemplate,
    govcloud: govcloudTemplate,
    eusovereign: eusovereignTemplate,
};

/** The model the ON arm uses where a template ships none (the EU Sovereign template ships ""). */
const ON_ARM_MODEL_ID = "amazon.titan-embed-text-v2:0";

const turnOn = (c: any) => {
    c.app.vectorSearch.enabled = true;
    if (!c.app.vectorSearch.embeddingModelId) {
        c.app.vectorSearch.embeddingModelId = ON_ARM_MODEL_ID;
    }
};
const turnOff = (c: any) => {
    c.app.vectorSearch.enabled = false;
};

const shipsOn = (name: TemplateName): boolean => TEMPLATES[name].app.vectorSearch.enabled === true;

/** The template with vector search on: as shipped for commercial, mutated for the restricted two. */
const synthOn = (name: TemplateName): SynthResult =>
    shipsOn(name)
        ? synthTemplate(name)
        : synthTemplate(name, { mutate: turnOn, mutateKey: "vector-search-on" });

/** The template with vector search off: as shipped for the restricted two, mutated for commercial. */
const synthOff = (name: TemplateName): SynthResult =>
    shipsOn(name)
        ? synthTemplate(name, { mutate: turnOff, mutateKey: "vector-search-off" })
        : synthTemplate(name);

/** The ON arm's vectorSearch block, so the expected index name is derived the way the harness derives it. */
function onArmVectorSearch(name: TemplateName): {
    embeddingModelId: string;
    embeddingDimensions: number;
} {
    const c = JSON.parse(JSON.stringify(TEMPLATES[name]));
    turnOn(c);
    return c.app.vectorSearch;
}

const at = (r: Resource) => `${r.stack}/${r.logicalId}`;
const keyNames = (r: Resource): string =>
    ((r.properties.KeySchema ?? []) as any[]).map((k) => k.AttributeName).join(",");

/** The one table whose KeySchema is exactly `keys`; throws rather than returning nothing. */
function tableKeyed(s: SynthResult, keys: string[]): Resource {
    const found = s.where("AWS::DynamoDB::Table", (r) => keyNames(r) === keys.join(","));
    if (found.length !== 1) {
        throw new Error(
            `${s.name}: expected one table keyed ${keys.join("/")}, found ${found.length}`
        );
    }
    return found[0];
}

const vectorTable = (s: SynthResult) => tableKeyed(s, ["databaseId:assetId", "fileVersionKey"]);
const lockTable = (s: SynthResult) => tableKeyed(s, ["lockKey"]);
const withVectorIndexes = (s: SynthResult): Resource[] =>
    s.where("AWS::DynamoDB::Table", (r) => r.properties.VectorIndexes !== undefined);
const declaredAttributes = (r: Resource): string[] =>
    ((r.properties.AttributeDefinitions ?? []) as any[]).map((a) => a.AttributeName).sort();

const FILTER_ATTRIBUTES = [
    "databaseId",
    "isLatest",
    "isArchived",
    "fileClass",
    "fileExt",
    "embeddingModelId",
    "segmentKind",
];

describe("the vector index is present iff app.vectorSearch.enabled, on every template", () => {
    test.each(ALL_TEMPLATES)(
        "%s ON: one index on the vector table, named as getConfig() derives it, seven INLINE_FILTER attributes",
        (name) => {
            const s = synthOn(name);
            const table = vectorTable(s);
            const vectorSearch = onArmVectorSearch(name);
            expect(withVectorIndexes(s).map(at)).toEqual([at(table)]);

            const [index] = table.properties.VectorIndexes;
            // A defined name first: the restricted arms are mutated synths, and if the harness filled
            // vectorIndexName before the mutator ran, both sides of the equality below are undefined.
            expect(index.IndexName).toMatch(/^vec-/);
            expect(index.IndexName).toBe(vectorIndexNameFor(vectorSearch));
            expect(index.Dimensions).toBe(vectorSearch.embeddingDimensions);
            expect(index.DistanceFunction).toBe("COSINE");
            expect(index.VectorAttribute).toEqual({ AttributeName: "embedding" });
            expect(index.Projection.ProjectionType).toBe("INCLUDE");
            expect(index.SearchSchema).toEqual(
                FILTER_ATTRIBUTES.map((n) => ({
                    AttributeName: n,
                    SearchSchemaElementType: "INLINE_FILTER",
                }))
            );
            expect(declaredAttributes(table)).toEqual(
                ["databaseId:assetId", "fileVersionKey", ...FILTER_ATTRIBUTES].sort()
            );
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s OFF: no VectorIndexes anywhere, and only the key attributes are declared",
        (name) => {
            const s = synthOff(name);
            expectAbsent(
                `VectorIndexes with vectorSearch off (${name})`,
                withVectorIndexes(s).map(at),
                {
                    description: `${name} with vectorSearch on emits a vector index`,
                    count: withVectorIndexes(synthOn(name)).length,
                }
            );
            // Declaring a SearchSchema attribute with no index using it fails CreateTable.
            expect(declaredAttributes(vectorTable(s))).toEqual([
                "databaseId:assetId",
                "fileVersionKey",
            ]);
        }
    );
});

describe("both tables are retained, unnamed, and encrypted like every other VAMS table", () => {
    test.each(ALL_TEMPLATES)(
        "%s: Retain on delete and replace, no TableName, on-demand",
        (name) => {
            const s = synthTemplate(name);
            for (const table of [vectorTable(s), lockTable(s)]) {
                expect(table.raw.DeletionPolicy).toBe("Retain");
                expect(table.raw.UpdateReplacePolicy).toBe("Retain");
                expect(table.properties.TableName).toBeUndefined();
                expect(table.properties.BillingMode).toBe("PAY_PER_REQUEST");
            }
        }
    );

    test.each(RESTRICTED_TEMPLATES)("%s: both tables use the VAMS CMK", (name) => {
        const s = synthTemplate(name);
        for (const table of [vectorTable(s), lockTable(s)]) {
            expect(table.properties.SSESpecification?.SSEEnabled).toBe(true);
            expect(table.properties.SSESpecification?.SSEType).toBe("KMS");
            expect(SynthResult.flatten(table.properties.SSESpecification?.KMSMasterKeyId)).toMatch(
                /\S/
            );
        }
    });

    test("commercial ships CMK off, so neither table names a key (control for the CMK arm)", () => {
        const s = synthTemplate("commercial");
        for (const table of [vectorTable(s), lockTable(s)]) {
            expect(table.properties.SSESpecification?.KMSMasterKeyId).toBeUndefined();
        }
    });
});

describe("WorkflowExecutionLocksStorageTable", () => {
    test.each(ALL_TEMPLATES)("%s: TTL on expiresAt; no streams, GSIs, or vector index", (name) => {
        const table = lockTable(synthTemplate(name));
        expect(table.properties.TimeToLiveSpecification).toEqual({
            AttributeName: "expiresAt",
            Enabled: true,
        });
        expect(table.properties.StreamSpecification).toBeUndefined();
        expect(table.properties.GlobalSecondaryIndexes).toBeUndefined();
        expect(table.properties.VectorIndexes).toBeUndefined();
    });
});

describe("ResourceNamesBuilder publishes both table names", () => {
    const resourceNameParams = (s: SynthResult) =>
        s.where("AWS::SSM::Parameter", (r) =>
            SynthResult.flatten(r.properties.Name).includes("/resourceNames/")
        );
    const paramsEndingWith = (s: SynthResult, suffix: string) =>
        resourceNameParams(s).filter((r) =>
            SynthResult.flatten(r.properties.Name).endsWith(`/resourceNames/${suffix}`)
        );

    test.each(ALL_TEMPLATES)("%s: exactly one parameter per new table key", (name) => {
        const s = synthTemplate(name);
        expect(
            paramsEndingWith(s, RESOURCE_PARAM_KEYS.dynamoTables.vectorEmbeddingsStorage)
        ).toHaveLength(1);
        expect(
            paramsEndingWith(s, RESOURCE_PARAM_KEYS.dynamoTables.workflowExecutionLocksStorage)
        ).toHaveLength(1);
    });

    test.each(ALL_TEMPLATES)(
        "%s: the registry-published parameter count equals the registry minus lambdaFunctions",
        (name) => {
            // lambdaFunctions/* is published by the search stack, only when its feature is on, so it is
            // excluded from both sides of the comparison.
            const s = synthTemplate(name);
            const published = resourceNameParams(s).filter(
                (r) =>
                    !SynthResult.flatten(r.properties.Name).includes(
                        "/resourceNames/lambdaFunctions/"
                    )
            );
            const expected = Object.entries(RESOURCE_PARAM_KEYS)
                .filter(([category]) => category !== "lambdaFunctions")
                .reduce((n, [, keys]) => n + Object.keys(keys).length, 0);
            expect(published).toHaveLength(expected);
        }
    );
});
