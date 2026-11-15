/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The GLOBAL `fileMetadata` default schema comes from a deployment-time file,
 * infra/config/metadataSchema/defaultFileMetadataSchema.json, shipped with no fields. A file that
 * is missing, blank, `{}` or has no fields seeds nothing; a file with fields seeds one default with
 * the same create and update conditions as the code-defined defaults; a file the metadata schema
 * API would refuse fails the synth with a message naming the file.
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import {
    DEFAULT_FILE_METADATA_SCHEMA_PATH,
    DynamoDbMetadataSchemaDefaultsConstruct,
    loadDefaultFileMetadataSchema,
} from "../../lib/nestedStacks/apiLambda/constructs/dynamodb-metadataschema-defaults-construct";
import { newTestApp } from "../support/testApp";

const TABLE = "metadata-schema-table";
const CODE_DEFAULT_IDS = [
    "default-assetlinks-schema",
    "default-database-schema",
    "default-asset-schema",
    "default-file-schema-3dmodel",
];
const FILE_METADATA_ID = "default-file-metadata-schema";

let tmpDir: string;
beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-file-metadata-schema-"));
});
afterAll(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
});

let fixtureCount = 0;
function fixture(content: string | object): string {
    const file = path.join(tmpDir, `defaultFileMetadataSchema-${fixtureCount++}.json`);
    fs.writeFileSync(file, typeof content === "string" ? content : JSON.stringify(content));
    return file;
}

function synth(schemaPath?: string): Template {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "FileMetadataSchemaDefaultTestStack");
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
                },
            },
        } as any,
        defaultFileMetadataSchemaPath: schemaPath,
    });
    return Template.fromStack(stack);
}

function seeds(template: Template): Array<{ create: any; update: any; raw: any }> {
    return Object.values(template.findResources("Custom::AWS")).map((r: any) => ({
        create: JSON.parse(r.Properties.Create),
        update: JSON.parse(r.Properties.Update),
        raw: r.Properties,
    }));
}

function seededIds(template: Template): string[] {
    return seeds(template)
        .map((s) => s.create.parameters.Item.metadataSchemaId.S)
        .sort();
}

const VALID = {
    schemaName: "deploymentFileMetadata",
    fileKeyTypeRestriction: ".glb,.usd",
    fields: [
        { metadataFieldKeyName: "Author", metadataFieldValueType: "STRING", required: true },
        {
            metadataFieldKeyName: "Stage",
            metadataFieldValueType: "inline_controlled_list",
            controlledListKeys: ["draft", "final"],
            defaultMetadataFieldValue: "draft",
            sequence: 2,
        },
        {
            metadataFieldKeyName: "Origin",
            metadataFieldValueType: "LLA",
            defaultMetadataFieldValue: '{"lat": 1, "long": 2, "alt": 3}',
            dependsOnFieldKeyName: ["Author"],
        },
    ],
};

describe("default file metadata schema file", () => {
    test("the shipped file defines no fields", () => {
        const shipped = JSON.parse(fs.readFileSync(DEFAULT_FILE_METADATA_SCHEMA_PATH, "utf8"));
        expect(shipped.fields).toEqual([]);
    });

    test("the shipped empty file seeds no fileMetadata default and leaves the four others", () => {
        const all = seeds(synth());
        expect(all.map((s) => s.create.parameters.Item.metadataSchemaId.S).sort()).toEqual(
            [...CODE_DEFAULT_IDS].sort()
        );
        expect(
            all.some((s) => s.create.parameters.Item.metadataSchemaEntityType.S === "fileMetadata")
        ).toBe(false);
    });

    test.each([
        ["missing", () => path.join(tmpDir, "does-not-exist.json")],
        ["zero bytes", () => fixture("")],
        ["whitespace only", () => fixture("  \n\t \n")],
        ["an empty object", () => fixture("{}")],
        ["fields empty", () => fixture({ schemaName: "x", fields: [] })],
        ["fields absent", () => fixture({ schemaName: "x" })],
    ])("a file that is %s seeds nothing for fileMetadata", (_label, file) => {
        expect(seededIds(synth(file()))).toEqual([...CODE_DEFAULT_IDS].sort());
    });

    test("a file with fields seeds one GLOBAL fileMetadata default", () => {
        const all = seeds(synth(fixture(VALID)));
        expect(all.map((s) => s.create.parameters.Item.metadataSchemaId.S).sort()).toEqual(
            [...CODE_DEFAULT_IDS, FILE_METADATA_ID].sort()
        );
        const seed = all.find(
            (s) => s.create.parameters.Item.metadataSchemaId.S === FILE_METADATA_ID
        )!;
        const item = seed.create.parameters.Item;
        expect(item).toEqual({
            metadataSchemaId: { S: FILE_METADATA_ID },
            "databaseId:metadataEntityType": { S: "GLOBAL:fileMetadata" },
            databaseId: { S: "GLOBAL" },
            metadataSchemaEntityType: { S: "fileMetadata" },
            schemaName: { S: "deploymentFileMetadata" },
            fileKeyTypeRestriction: { S: ".glb,.usd" },
            fields: { S: expect.any(String) },
            enabled: { BOOL: true },
            createdBy: { S: "SYSTEM_USER" },
            modifiedBy: { S: "SYSTEM_USER" },
        });
        expect(JSON.parse(item.fields.S)).toEqual({
            fields: [
                {
                    metadataFieldKeyName: "Author",
                    metadataFieldValueType: "string",
                    required: true,
                    sequence: null,
                    dependsOnFieldKeyName: null,
                    controlledListKeys: null,
                    defaultMetadataFieldValue: null,
                },
                {
                    metadataFieldKeyName: "Stage",
                    metadataFieldValueType: "inline_controlled_list",
                    required: false,
                    sequence: 2,
                    dependsOnFieldKeyName: null,
                    controlledListKeys: ["draft", "final"],
                    defaultMetadataFieldValue: "draft",
                },
                {
                    metadataFieldKeyName: "Origin",
                    metadataFieldValueType: "lla",
                    required: false,
                    sequence: null,
                    dependsOnFieldKeyName: ["Author"],
                    controlledListKeys: null,
                    defaultMetadataFieldValue: '{"lat": 1, "long": 2, "alt": 3}',
                },
            ],
        });
    });

    test("the fileMetadata default uses the same create and update conditions", () => {
        const seed = seeds(synth(fixture(VALID))).find(
            (s) => s.create.parameters.Item.metadataSchemaId.S === FILE_METADATA_ID
        )!;
        expect(seed.create.action).toBe("putItem");
        expect(seed.create.parameters.TableName).toBe(TABLE);
        expect(seed.create.parameters.ConditionExpression).toBe(
            "attribute_not_exists(metadataSchemaId) OR modifiedBy IN (:systemUser, :legacySystem)"
        );
        expect(seed.update.parameters.ConditionExpression).toBe(
            "attribute_exists(metadataSchemaId) AND modifiedBy IN (:systemUser, :legacySystem)"
        );
        for (const call of [seed.create, seed.update]) {
            expect(call.ignoreErrorCodesMatching).toBe("ConditionalCheckFailedException");
            expect(call.parameters.ExpressionAttributeValues).toEqual({
                ":systemUser": { S: "SYSTEM_USER" },
                ":legacySystem": { S: "SYSTEM" },
            });
            expect(call.physicalResourceId).toEqual({ id: `${TABLE}_filemetadata_initialization` });
        }
        expect(seed.update.parameters.Item).toEqual(seed.create.parameters.Item);
        expect(seed.raw.ServiceTimeout).toBeDefined();
    });

    test("a blank fileKeyTypeRestriction is left off the item", () => {
        const seed = seeds(synth(fixture({ ...VALID, fileKeyTypeRestriction: "  " }))).find(
            (s) => s.create.parameters.Item.metadataSchemaId.S === FILE_METADATA_ID
        )!;
        expect(seed.create.parameters.Item.fileKeyTypeRestriction).toBeUndefined();
    });

    const field = (overrides: object) => ({
        ...VALID,
        fields: [{ metadataFieldKeyName: "A", metadataFieldValueType: "string", ...overrides }],
    });

    test.each([
        ["unparseable JSON", '{"fields": [', /is not valid JSON/],
        ["a JSON array", "[]", /must hold a JSON object/],
        ["an unknown top-level key", { ...VALID, feilds: [] }, /unknown key\(s\) feilds/],
        ["fields not an array", { schemaName: "x", fields: {} }, /"fields" must be an array/],
        ["no schemaName", { fields: VALID.fields }, /"schemaName" is required/],
        ["an invalid schemaName", { ...VALID, schemaName: "bad/name" }, /"schemaName"/],
        [
            "a bad fileKeyTypeRestriction",
            { ...VALID, fileKeyTypeRestriction: "glb" },
            /must start with a dot/,
        ],
        [
            "an unknown field type",
            field({ metadataFieldValueType: "colour" }),
            /field 1 metadataFieldValueType must be one of/,
        ],
        [
            "an unknown field key",
            field({ requried: true }),
            /field 1 has unknown key\(s\) requried/,
        ],
        ["an empty field name", field({ metadataFieldKeyName: "" }), /metadataFieldKeyName/],
        ["a non-boolean required", field({ required: "yes" }), /required must be true or false/],
        ["a negative sequence", field({ sequence: -1 }), /sequence must be an integer/],
        [
            "a controlled list without keys",
            field({ metadataFieldValueType: "inline_controlled_list" }),
            /controlledListKeys is required/,
        ],
        [
            "controlled-list keys on a string field",
            field({ controlledListKeys: ["a"] }),
            /controlledListKeys may only be set/,
        ],
        [
            "a default outside the controlled list",
            field({
                metadataFieldValueType: "inline_controlled_list",
                controlledListKeys: ["a"],
                defaultMetadataFieldValue: "b",
            }),
            /must be one of the declared controlledListKeys/,
        ],
        [
            "a non-numeric number default",
            field({ metadataFieldValueType: "number", defaultMetadataFieldValue: "ten" }),
            /defaultMetadataFieldValue must be a valid number/,
        ],
        [
            "an out-of-range LLA default",
            field({
                metadataFieldValueType: "lla",
                defaultMetadataFieldValue: '{"lat": 95, "long": 0, "alt": 0}',
            }),
            /defaultMetadataFieldValue must be a JSON object with numeric lat/,
        ],
        [
            "a geojson default",
            field({ metadataFieldValueType: "geojson", defaultMetadataFieldValue: "{}" }),
            /set it through the API after deployment/,
        ],
        [
            "duplicate field names",
            {
                ...VALID,
                fields: [
                    { metadataFieldKeyName: "A", metadataFieldValueType: "string" },
                    { metadataFieldKeyName: "A", metadataFieldValueType: "number" },
                ],
            },
            /field names \(metadataFieldKeyName\) must be unique/,
        ],
        [
            "too many fields",
            {
                ...VALID,
                fields: Array.from({ length: 501 }, (_v, i) => ({
                    metadataFieldKeyName: `F${i}`,
                    metadataFieldValueType: "string",
                })),
            },
            /may hold at most 500 fields/,
        ],
    ])("a file with %s fails the synth, naming the file", (_label, content, problem) => {
        const file = fixture(content);
        let error: Error | undefined;
        try {
            synth(file);
        } catch (e: any) {
            error = e;
        }
        expect(error).toBeDefined();
        expect(error!.message).toContain(file);
        expect(error!.message).toMatch(problem);
    });

    test("a file that cannot be read fails the synth", () => {
        const dir = path.join(tmpDir, "a-directory.json");
        fs.mkdirSync(dir);
        expect(() => synth(dir)).toThrow(/a-directory\.json: could not be read/);
    });

    const dateField = (value: string) =>
        field({ metadataFieldValueType: "date", defaultMetadataFieldValue: value });

    test.each([
        ["February 30", "2024-02-30"],
        ["April 31", "2024-04-31"],
        ["February 29 outside a leap year", "2023-02-29"],
        ["February 29 in a century non-leap year", "1900-02-29"],
        ["year 0", "0000-01-01"],
        ["month 0", "2024-00-10"],
        ["month 13", "2024-13-01"],
        ["day 0", "2024-01-00"],
        ["hour 24", "2024-01-01T24:00"],
        ["minute 60", "2024-01-01T23:60"],
        ["second 60", "2024-01-01T23:59:60"],
        ["a 24-hour UTC offset", "2024-01-01T10:00+24:00"],
    ])("a date default on %s fails the synth", (_label, value) => {
        expect(() => synth(fixture(dateField(value)))).toThrow(
            /field 1 defaultMetadataFieldValue must be a valid ISO date/
        );
    });

    test.each([
        "2024-02-29",
        "2000-02-29",
        "0001-01-01",
        "9999-12-31",
        "2024-04-30",
        "2024-01-01T00:00",
        "2024-01-01T23:59:59.999",
        "2024-01-01 10:00Z",
        "2024-01-01T10:00:00-23:59",
    ])("a date default of %s is seeded", (value) => {
        const seed = seeds(synth(fixture(dateField(value)))).find(
            (s) => s.create.parameters.Item.metadataSchemaId.S === FILE_METADATA_ID
        )!;
        expect(JSON.parse(seed.create.parameters.Item.fields.S).fields[0]).toMatchObject({
            metadataFieldValueType: "date",
            defaultMetadataFieldValue: value,
        });
    });

    test("the fileMetadata seed keeps its logical id when the other defaults are switched off", () => {
        const idWith = (flags: boolean) => {
            const app = newTestApp();
            const stack = new cdk.Stack(app, "FileMetadataSchemaIdTestStack");
            new DynamoDbMetadataSchemaDefaultsConstruct(stack, "MetadataSchemaDefaults", {
                customResourceRole: new iam.Role(stack, "CustomResourceRole", {
                    assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
                }),
                lambdaCommonBaseLayer: undefined as any,
                storageResources: {
                    dynamo: { metadataSchemaStorageTableV2: { tableName: TABLE } },
                } as any,
                config: {
                    app: {
                        metadataSchema: {
                            autoLoadDefaultAssetLinksSchema: flags,
                            autoLoadDefaultDatabaseSchema: flags,
                            autoLoadDefaultAssetSchema: flags,
                            autoLoadDefaultAssetFileSchema: flags,
                        },
                    },
                } as any,
                defaultFileMetadataSchemaPath: fixture(VALID),
            });
            const resources = Template.fromStack(stack).findResources("Custom::AWS");
            return Object.keys(resources).filter((id) =>
                JSON.parse(
                    resources[id].Properties.Create
                ).parameters.Item.metadataSchemaId.S.startsWith(FILE_METADATA_ID)
            );
        };
        const ids = idWith(true);
        expect(ids).toHaveLength(1);
        expect(ids[0]).toMatch(/FileMetadataCustomResource[A-F0-9]{8}$/);
        expect(idWith(false)).toEqual(ids);
    });

    test("fields larger than the seed limit fail the synth", () => {
        const big = {
            ...VALID,
            fields: Array.from({ length: 3 }, (_v, i) => ({
                metadataFieldKeyName: `F${i}`,
                metadataFieldValueType: "string",
                defaultMetadataFieldValue: "x".repeat(100000),
            })),
        };
        const file = fixture(big);
        expect(() => synth(file)).toThrow(/"fields" must be 200 KB or less when serialized/);
        expect(() => synth(file)).toThrow(file);
    });
});

describe("default value cross-check with the backend model", () => {
    const fixturePath = path.join(__dirname, "fixtures", "defaultMetadataValueCrossCheck.json");
    const cases: Array<{ type: string; value: string; file: boolean; backend: boolean }> =
        JSON.parse(fs.readFileSync(fixturePath, "utf8")).cases;

    test("the file never accepts a value the backend refuses", () => {
        expect(cases.filter((c) => c.file && !c.backend)).toEqual([]);
    });

    test.each(cases.map((c) => [c.type, c.value, c.file] as const))(
        "%s default %j: file accepts = %s",
        (type, value, accepted) => {
            const file = fixture({
                schemaName: "crossCheck",
                fields: [
                    {
                        metadataFieldKeyName: "A",
                        metadataFieldValueType: type,
                        defaultMetadataFieldValue: value,
                    },
                ],
            });
            if (accepted) {
                expect(loadDefaultFileMetadataSchema(file)).toBeDefined();
            } else {
                expect(() => loadDefaultFileMetadataSchema(file)).toThrow(
                    /defaultMetadataFieldValue/
                );
            }
        }
    );
});
