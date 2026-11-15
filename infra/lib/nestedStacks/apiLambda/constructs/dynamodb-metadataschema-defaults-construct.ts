/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { AwsCustomResource, AwsSdkCall, PhysicalResourceId } from "aws-cdk-lib/custom-resources";
import { SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT } from "../../../helper/seedCustomResource";
import { Construct } from "constructs";
import { Config } from "../../../../config/config";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { readFileSync } from "fs";
import { join } from "path";

export interface DynamoDbMetadataSchemaDefaultsConstructProps extends cdk.StackProps {
    customResourceRole: iam.Role;
    lambdaCommonBaseLayer: LayerVersion;
    storageResources: storageResources;
    config: Config;
    /** Overrides the deployment-time file-metadata schema file; tests point it at fixtures. */
    defaultFileMetadataSchemaPath?: string;
}

/** The deployment-time GLOBAL `fileMetadata` default schema; seeds nothing while it has no fields. */
export const DEFAULT_FILE_METADATA_SCHEMA_PATH = join(
    __dirname,
    "..",
    "..",
    "..",
    "..",
    "config",
    "metadataSchema",
    "defaultFileMetadataSchema.json"
);

// Bounds and rules mirrored from backend/backend/models/metadataSchema.py and models/metadata.py,
// so the seed never writes a schema the metadata schema API would refuse to create.
const MAX_SCHEMA_FIELDS = 500;
const MAX_CONTROLLED_LIST_KEYS = 1000;
const MAX_FIELD_STRING_LENGTH = 256;
const MAX_METADATA_VALUE_LENGTH = 400000;
const MAX_FILE_KEY_TYPE_RESTRICTION_LENGTH = 1024;
// The item travels in the seed custom resource's properties, which share the nested stack
// template's size limit with every other resource; keep it well under that and DynamoDB's 400 KB.
const MAX_SERIALIZED_FIELDS_BYTES = 200 * 1024;
const OBJECT_NAME_PATTERN = /^[a-zA-Z0-9\-._\s]{1,256}$/;
const METADATA_VALUE_TYPES = [
    "string",
    "multiline_string",
    "inline_controlled_list",
    "number",
    "boolean",
    "date",
    "xyz",
    "wxyz",
    "matrix4x4",
    "geopoint",
    "geojson",
    "lla",
    "json",
];
const SCHEMA_FILE_KEYS = ["schemaName", "fileKeyTypeRestriction", "fields"];
const FIELD_KEYS = [
    "metadataFieldKeyName",
    "metadataFieldValueType",
    "required",
    "sequence",
    "dependsOnFieldKeyName",
    "controlledListKeys",
    "defaultMetadataFieldValue",
];

export interface DefaultFileMetadataSchema {
    schemaName: string;
    fileKeyTypeRestriction?: string;
    fields: SeedField[];
}

type SeedField = Record<string, unknown>;

function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
}

function isNumber(v: unknown): boolean {
    return typeof v === "number" && Number.isFinite(v);
}

function parseJsonValue(value: string, type: string): any {
    try {
        return JSON.parse(value);
    } catch {
        throw new Error(`must be valid JSON for type '${type}'`);
    }
}

const ISO_DATE_PATTERN =
    /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-](\d{2}):(\d{2}))?)?$/;

/**
 * A subset of what Python's datetime.fromisoformat accepts (after the backend maps "Z" to
 * "+00:00"): years 1 to 9999, calendar-valid days, hours 0-23, minutes and seconds 0-59, and a
 * UTC offset below 24 hours.
 */
function isIsoDate(value: string): boolean {
    const match = ISO_DATE_PATTERN.exec(value);
    if (!match) return false;
    const [year, month, day, hour, minute, second, offsetHour, offsetMinute] = match
        .slice(1)
        .map((part) => (part === undefined ? 0 : Number(part)));
    const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
    const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return (
        year >= 1 &&
        month >= 1 &&
        month <= 12 &&
        day >= 1 &&
        day <= daysInMonth[month - 1] &&
        hour <= 23 &&
        minute <= 59 &&
        second <= 59 &&
        offsetHour <= 23 &&
        offsetMinute <= 59
    );
}

/** Mirrors validate_metadata_value_common for a default value; throws a message without the value. */
function checkDefaultValue(value: string, type: string): void {
    if (value === "") return;
    switch (type) {
        case "string":
        case "multiline_string":
        case "inline_controlled_list":
            return;
        case "number":
            if (!/^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$/.test(value)) {
                throw new Error("must be a valid number for type 'number'");
            }
            return;
        case "boolean":
            if (!["true", "false"].includes(value.toLowerCase())) {
                throw new Error("must be 'true' or 'false' for type 'boolean'");
            }
            return;
        case "date":
            if (!isIsoDate(value)) {
                throw new Error("must be a valid ISO date for type 'date'");
            }
            return;
        case "json":
            parseJsonValue(value, type);
            return;
        case "xyz":
        case "wxyz": {
            const parsed = parseJsonValue(value, type);
            const keys = type === "xyz" ? ["x", "y", "z"] : ["w", "x", "y", "z"];
            if (
                parsed === null ||
                typeof parsed !== "object" ||
                Array.isArray(parsed) ||
                !keys.every((k) => isNumber(parsed[k]))
            ) {
                throw new Error(`must be a JSON object with numeric ${keys.join(", ")} keys`);
            }
            return;
        }
        case "matrix4x4": {
            const parsed = parseJsonValue(value, type);
            if (
                !Array.isArray(parsed) ||
                parsed.length !== 4 ||
                !parsed.every(
                    (row) => Array.isArray(row) && row.length === 4 && row.every(isNumber)
                )
            ) {
                throw new Error("must be a 4x4 JSON array of numbers for type 'matrix4x4'");
            }
            return;
        }
        case "lla": {
            const parsed = parseJsonValue(value, type);
            if (
                parsed === null ||
                typeof parsed !== "object" ||
                Array.isArray(parsed) ||
                !isNumber(parsed.lat) ||
                !isNumber(parsed.long) ||
                !isNumber(parsed.alt) ||
                parsed.lat < -90 ||
                parsed.lat > 90 ||
                parsed.long < -180 ||
                parsed.long > 180
            ) {
                throw new Error(
                    "must be a JSON object with numeric lat (-90 to 90), long (-180 to 180) and alt"
                );
            }
            return;
        }
        default:
            // geopoint and geojson defaults need the backend's geometry checks; set them through
            // the API after deployment so they are validated there.
            throw new Error(
                `is not supported in this file for type '${type}'; set it through the API after deployment`
            );
    }
}

function checkFileExtensionList(value: string): void {
    for (const extension of value.split(",").map((e) => e.trim())) {
        if (!extension) throw new Error("each file extension must be non-empty");
        if (extension.toLowerCase() === ".all") continue;
        if (!extension.startsWith(".")) {
            throw new Error("each file extension must start with a dot (e.g., '.glb')");
        }
        if (extension.length < 2) {
            throw new Error("each file extension must have at least one character after the dot");
        }
        if (extension.slice(1).includes(".")) {
            throw new Error("each file extension may contain only one dot");
        }
        if (extension.includes("/") || extension.includes("\\")) {
            throw new Error("file extensions may not contain a path separator");
        }
    }
}

function checkStringArray(value: unknown, name: string, maxItems: number): string[] {
    if (!Array.isArray(value)) throw new Error(`${name} must be an array of strings`);
    if (value.length > maxItems) throw new Error(`${name} may hold at most ${maxItems} entries`);
    value.forEach((entry) => {
        if (typeof entry !== "string" || entry.length > MAX_FIELD_STRING_LENGTH) {
            throw new Error(
                `${name} entries must be strings of ${MAX_FIELD_STRING_LENGTH} characters or fewer`
            );
        }
    });
    return value;
}

function checkField(raw: unknown): SeedField {
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
        throw new Error("must be a JSON object");
    }
    const field = raw as Record<string, any>;
    const unknownKeys = Object.keys(field).filter((k) => !FIELD_KEYS.includes(k));
    if (unknownKeys.length > 0) {
        throw new Error(`has unknown key(s) ${unknownKeys.join(", ")}`);
    }
    const name = field.metadataFieldKeyName;
    if (typeof name !== "string" || name.length < 1 || name.length > MAX_FIELD_STRING_LENGTH) {
        throw new Error(
            `metadataFieldKeyName must be a string of 1 to ${MAX_FIELD_STRING_LENGTH} characters`
        );
    }
    const type =
        typeof field.metadataFieldValueType === "string"
            ? field.metadataFieldValueType.toLowerCase()
            : undefined;
    if (!type || !METADATA_VALUE_TYPES.includes(type)) {
        throw new Error(`metadataFieldValueType must be one of ${METADATA_VALUE_TYPES.join(", ")}`);
    }
    if (field.required !== undefined && typeof field.required !== "boolean") {
        throw new Error("required must be true or false");
    }
    if (
        field.sequence !== undefined &&
        field.sequence !== null &&
        (!Number.isInteger(field.sequence) ||
            field.sequence < 0 ||
            field.sequence > MAX_SCHEMA_FIELDS)
    ) {
        throw new Error(`sequence must be an integer from 0 to ${MAX_SCHEMA_FIELDS}`);
    }
    const dependsOn =
        field.dependsOnFieldKeyName === undefined || field.dependsOnFieldKeyName === null
            ? null
            : checkStringArray(
                  field.dependsOnFieldKeyName,
                  "dependsOnFieldKeyName",
                  MAX_SCHEMA_FIELDS
              );
    const controlledList =
        field.controlledListKeys === undefined || field.controlledListKeys === null
            ? null
            : checkStringArray(
                  field.controlledListKeys,
                  "controlledListKeys",
                  MAX_CONTROLLED_LIST_KEYS
              );
    if (type === "inline_controlled_list") {
        if (!controlledList || controlledList.length === 0) {
            throw new Error("controlledListKeys is required for inline_controlled_list type");
        }
    } else if (controlledList && controlledList.length > 0) {
        throw new Error(
            "controlledListKeys may only be set when metadataFieldValueType is inline_controlled_list"
        );
    }
    const defaultValue = field.defaultMetadataFieldValue;
    if (defaultValue !== undefined && defaultValue !== null) {
        if (typeof defaultValue !== "string" || defaultValue.length > MAX_METADATA_VALUE_LENGTH) {
            throw new Error(
                `defaultMetadataFieldValue must be a string of ${MAX_METADATA_VALUE_LENGTH} characters or fewer`
            );
        }
        try {
            checkDefaultValue(defaultValue, type);
        } catch (e) {
            throw new Error(`defaultMetadataFieldValue ${messageOf(e)}`);
        }
        if (type === "inline_controlled_list" && !(controlledList ?? []).includes(defaultValue)) {
            throw new Error(
                "defaultMetadataFieldValue must be one of the declared controlledListKeys"
            );
        }
    }
    return createField(
        name,
        type,
        field.required ?? false,
        dependsOn,
        defaultValue ?? null,
        field.sequence ?? null,
        controlledList
    );
}

/**
 * Reads the deployment-time default file-metadata schema.
 *
 * Returns undefined, seeding nothing, when the file is missing, blank, `{}` or has no fields.
 * Throws, naming the file, for a file that cannot be read or parsed or that defines a schema the
 * metadata schema API would refuse.
 */
export function loadDefaultFileMetadataSchema(
    filePath: string = DEFAULT_FILE_METADATA_SCHEMA_PATH
): DefaultFileMetadataSchema | undefined {
    const fail = (problem: string): never => {
        throw new Error(
            `Configuration Error: default file metadata schema file ${filePath}: ${problem}. ` +
                `Fix the file, or set "fields" to [] to seed no default file metadata schema.`
        );
    };

    let text: string;
    try {
        text = readFileSync(filePath, { encoding: "utf8", flag: "r" });
    } catch (e) {
        if ((e as NodeJS.ErrnoException)?.code === "ENOENT") return undefined;
        return fail(`could not be read (${messageOf(e)})`);
    }
    if (text.trim().length === 0) return undefined;

    let parsed: any;
    try {
        parsed = JSON.parse(text);
    } catch (e) {
        return fail(`is not valid JSON (${messageOf(e)})`);
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        return fail("must hold a JSON object");
    }
    const unknownKeys = Object.keys(parsed).filter((k) => !SCHEMA_FILE_KEYS.includes(k));
    if (unknownKeys.length > 0) {
        fail(`unknown key(s) ${unknownKeys.join(", ")}; allowed: ${SCHEMA_FILE_KEYS.join(", ")}`);
    }
    if (parsed.fields === undefined || parsed.fields === null) return undefined;
    if (!Array.isArray(parsed.fields)) fail('"fields" must be an array');
    if (parsed.fields.length === 0) return undefined;
    if (parsed.fields.length > MAX_SCHEMA_FIELDS) {
        fail(`"fields" may hold at most ${MAX_SCHEMA_FIELDS} fields`);
    }

    if (typeof parsed.schemaName !== "string" || !OBJECT_NAME_PATTERN.test(parsed.schemaName)) {
        fail(
            '"schemaName" is required and must be 1 to 256 letters, digits, spaces, hyphens, ' +
                "periods or underscores"
        );
    }
    let fileKeyTypeRestriction: string | undefined;
    const restriction = parsed.fileKeyTypeRestriction;
    if (restriction !== undefined && restriction !== null) {
        if (typeof restriction !== "string") fail('"fileKeyTypeRestriction" must be a string');
        if (restriction.length > MAX_FILE_KEY_TYPE_RESTRICTION_LENGTH) {
            fail(
                `"fileKeyTypeRestriction" must be ${MAX_FILE_KEY_TYPE_RESTRICTION_LENGTH} characters or fewer`
            );
        }
        if (restriction.trim().length > 0) {
            try {
                checkFileExtensionList(restriction);
            } catch (e) {
                fail(`"fileKeyTypeRestriction": ${messageOf(e)}`);
            }
            fileKeyTypeRestriction = restriction;
        }
    }

    const fields: SeedField[] = parsed.fields.map((raw: unknown, index: number) => {
        try {
            return checkField(raw);
        } catch (e) {
            return fail(`field ${index + 1} ${messageOf(e)}`);
        }
    });
    const names = fields.map((f) => f.metadataFieldKeyName);
    if (new Set(names).size !== names.length) {
        fail("field names (metadataFieldKeyName) must be unique within the schema");
    }
    const serializedBytes = Buffer.byteLength(JSON.stringify({ fields }), "utf8");
    if (serializedBytes > MAX_SERIALIZED_FIELDS_BYTES) {
        fail(
            `"fields" must be ${MAX_SERIALIZED_FIELDS_BYTES / 1024} KB or less when serialized ` +
                `(it is ${Math.ceil(serializedBytes / 1024)} KB)`
        );
    }

    return { schemaName: parsed.schemaName, fileKeyTypeRestriction, fields };
}

/**
 * Helper function to create a metadata schema field definition
 * Matches MetadataSchemaFieldModel structure from backend/backend/models/metadataSchema.py
 */
function createField(
    metadataFieldKeyName: string,
    metadataFieldValueType: string,
    required = false,
    dependsOnFieldKeyName: string[] | null = null,
    defaultMetadataFieldValue: string | null = null,
    sequence: number | null = null,
    controlledListKeys: string[] | null = null
): any {
    return {
        metadataFieldKeyName: metadataFieldKeyName,
        metadataFieldValueType: metadataFieldValueType,
        required: required,
        sequence: sequence,
        dependsOnFieldKeyName: dependsOnFieldKeyName,
        controlledListKeys: controlledListKeys,
        defaultMetadataFieldValue: defaultMetadataFieldValue,
    };
}

/** `modifiedBy` of a default that no user has edited since a deployment wrote it. */
const SEED_WRITERS = {
    ":systemUser": { S: "SYSTEM_USER" },
    // Read-only match for rows that the 2.4.0 to 2.5.3 seeds wrote; never written by this construct.
    ":legacySystem": { S: "SYSTEM" },
};

/**
 * The create and update calls that seed one default schema item.
 *
 * Create writes the item unless a row already exists that a user has edited; update refreshes the
 * row only while it still exists and no user has edited it. Every API edit, disabling included,
 * records its editor in `modifiedBy`, so edited, disabled and deleted defaults are left alone.
 */
function seedCalls(
    tableName: string,
    item: Record<string, unknown>,
    physicalResourceId: string
): { onCreate: AwsSdkCall; onUpdate: AwsSdkCall } {
    const call = (conditionExpression: string): AwsSdkCall => ({
        service: "DynamoDB",
        action: "putItem",
        parameters: {
            TableName: tableName,
            Item: item,
            ConditionExpression: conditionExpression,
            ExpressionAttributeValues: SEED_WRITERS,
        },
        ignoreErrorCodesMatching: "ConditionalCheckFailedException",
        physicalResourceId: PhysicalResourceId.of(physicalResourceId),
    });
    return {
        onCreate: call(
            "attribute_not_exists(metadataSchemaId) OR modifiedBy IN (:systemUser, :legacySystem)"
        ),
        onUpdate: call(
            "attribute_exists(metadataSchemaId) AND modifiedBy IN (:systemUser, :legacySystem)"
        ),
    };
}

/**
 * Deploys default metadata schemas to DynamoDB table.
 *
 * A default is written on the deployment that creates its seed resource and refreshed on later
 * deployments only while no user has edited it. A release changes a shipped default only by adding
 * optional fields, never by renaming, retyping or removing one, and names the schema ID and the
 * field in its CHANGELOG entry, so administrators with an edited copy can add the field themselves.
 */
export class DynamoDbMetadataSchemaDefaultsConstruct extends Construct {
    constructor(
        parent: Construct,
        name: string,
        props: DynamoDbMetadataSchemaDefaultsConstructProps
    ) {
        super(parent, name);

        const tableName = props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName;
        let schemaIndex = 0;

        // Asset Links Schema - defaultAssetLinks
        if (props.config.app.metadataSchema.autoLoadDefaultAssetLinksSchema) {
            const assetLinksFields = [
                createField("Translation", "XYZ", false, null, null, 1),
                createField("Rotation", "WXYZ", false, null, null, 2),
                createField("Scale", "XYZ", false, null, null, 3),
                createField("Matrix", "MATRIX4X4", false, null, null, 4),
            ];

            const assetLinksSchemaId = "default-assetlinks-schema";
            const assetLinksCompositeKey = "GLOBAL:assetLinkMetadata";

            const assetLinksSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: assetLinksSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: assetLinksCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "assetLinkMetadata",
                    },
                    schemaName: {
                        S: "defaultAssetLinks",
                    },
                    fields: {
                        S: JSON.stringify({ fields: assetLinksFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_assetlinks_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_AssetLinksCustomResource_${schemaIndex}`,
                {
                    ...assetLinksSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // Database Schema - defaultDatabase
        if (props.config.app.metadataSchema.autoLoadDefaultDatabaseSchema) {
            const databaseFields = [createField("Location", "LLA")];

            const databaseSchemaId = "default-database-schema";
            const databaseCompositeKey = "GLOBAL:databaseMetadata";

            const databaseSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: databaseSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: databaseCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "databaseMetadata",
                    },
                    schemaName: {
                        S: "defaultDatabase",
                    },
                    fields: {
                        S: JSON.stringify({ fields: databaseFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_database_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_DatabaseCustomResource_${schemaIndex}`,
                {
                    ...databaseSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // Asset Schema - defaultAsset
        if (props.config.app.metadataSchema.autoLoadDefaultAssetSchema) {
            const assetFields = [createField("Location", "LLA")];

            const assetSchemaId = "default-asset-schema";
            const assetCompositeKey = "GLOBAL:assetMetadata";

            const assetSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: assetSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: assetCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "assetMetadata",
                    },
                    schemaName: {
                        S: "defaultAsset",
                    },
                    fields: {
                        S: JSON.stringify({ fields: assetFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_asset_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_AssetCustomResource_${schemaIndex}`,
                {
                    ...assetSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // File Schema - defaultAssetFile3dModel with file type restrictions
        if (props.config.app.metadataSchema.autoLoadDefaultAssetFileSchema) {
            const fileFields = [createField("Polygon_Count", "STRING")];

            const fileSchemaId = "default-file-schema-3dmodel";
            const fileCompositeKey = "GLOBAL:fileAttribute";

            const fileSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: fileSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: fileCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "fileAttribute",
                    },
                    schemaName: {
                        S: "defaultAssetFile3dModel",
                    },
                    fileKeyTypeRestriction: {
                        S: ".glb,.usd,.obj,.fbx,.gltf,.stl,.usdz",
                    },
                    fields: {
                        S: JSON.stringify({ fields: fileFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_file_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_FileCustomResource_${schemaIndex}`,
                {
                    ...fileSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // File Metadata Schema - deployment-time file; the file's content is its switch
        const fileMetadataSchema = loadDefaultFileMetadataSchema(
            props.defaultFileMetadataSchemaPath
        );
        if (fileMetadataSchema) {
            const fileMetadataItem: Record<string, unknown> = {
                metadataSchemaId: {
                    S: "default-file-metadata-schema",
                },
                "databaseId:metadataEntityType": {
                    S: "GLOBAL:fileMetadata",
                },
                databaseId: {
                    S: "GLOBAL",
                },
                metadataSchemaEntityType: {
                    S: "fileMetadata",
                },
                schemaName: {
                    S: fileMetadataSchema.schemaName,
                },
                fields: {
                    S: JSON.stringify({ fields: fileMetadataSchema.fields }),
                },
                enabled: {
                    BOOL: true,
                },
                createdBy: {
                    S: "SYSTEM_USER",
                },
                modifiedBy: {
                    S: "SYSTEM_USER",
                },
            };
            if (fileMetadataSchema.fileKeyTypeRestriction) {
                fileMetadataItem.fileKeyTypeRestriction = {
                    S: fileMetadataSchema.fileKeyTypeRestriction,
                };
            }

            // A fixed id, so switching the autoLoadDefault* flags above never replaces this seed
            new AwsCustomResource(this, "metadataSchemaStorageTable_FileMetadataCustomResource", {
                ...seedCalls(
                    tableName,
                    fileMetadataItem,
                    tableName + `_filemetadata_initialization`
                ),
                role: props.customResourceRole,
                serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
            });
        }

        // Add CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Custom resource role requires DynamoDB PutItem permissions for metadata schema initialization. The permissions are scoped to the specific metadata schema table created by this deployment.",
                },
            ],
            true
        );
    }
}
