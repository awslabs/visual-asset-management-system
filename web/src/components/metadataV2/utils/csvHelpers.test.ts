/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * CSV import under restrictMetadataOutsideSchemas follows the API's rule (#968): only a key that is
 * new to the record and not in a schema is refused. A key already stored on the record, such as one
 * a pipeline wrote back, is accepted, so exporting a record and importing it again works.
 */

import { exportToCSV, importFromCSV } from "./csvHelpers";
import { MetadataRowState } from "../types/metadata.types";

const schemaRow = {
    metadataKey: "declared",
    metadataValue: "x",
    metadataValueType: "string",
    metadataSchemaField: true,
    isNew: false,
    isEditing: false,
    hasChanges: false,
    isDeleted: false,
    editKey: "declared",
    editValue: "x",
    editType: "string",
} as MetadataRowState;

const csv = (...rows: string[]) => ["Metadata Key,Value Type,Metadata Value", ...rows].join("\n");

describe("importFromCSV with restrictMetadataOutsideSchemas", () => {
    it("accepts a key already stored on the record that no schema declares", () => {
        const { rows, errors } = importFromCSV(
            csv("declared,string,edited", "pipelineAddedKey,string,from-pipeline"),
            [schemaRow],
            true,
            ["declared", "pipelineAddedKey"]
        );

        expect(errors).toEqual([]);
        expect(rows.map((row) => row.metadataKey)).toEqual(["declared", "pipelineAddedKey"]);
    });

    it("still refuses a key that is new to the record and not in a schema", () => {
        const { errors } = importFromCSV(
            csv("declared,string,edited", "brandNewKey,string,v"),
            [schemaRow],
            true,
            ["declared", "pipelineAddedKey"]
        );

        expect(errors).toEqual([
            'Line 3: Key "brandNewKey" is not a schema field. Adding new fields is restricted.',
        ]);
    });

    it("refuses an undeclared key when no stored keys are given", () => {
        const { errors } = importFromCSV(csv("pipelineAddedKey,string,v"), [schemaRow], true);

        expect(errors).toHaveLength(1);
        expect(errors[0]).toContain('"pipelineAddedKey" is not a schema field');
    });

    it("re-imports an exported schema field whose stored value type differs from the schema", () => {
        // The API returns a schema field with its stored type, so an export carries that type and
        // importing it unchanged is not a type change (the API accepts the unchanged value too).
        const pipelineTyped = {
            ...schemaRow,
            metadataKey: "count",
            metadataValue: "not-a-number",
            metadataValueType: "string",
            editKey: "count",
            editValue: "not-a-number",
        } as MetadataRowState;

        const { rows, errors } = importFromCSV(
            exportToCSV([schemaRow, pipelineTyped]),
            [schemaRow, pipelineTyped],
            true,
            ["declared", "count"]
        );

        expect(errors).toEqual([]);
        expect(rows.map((row) => [row.metadataKey, row.metadataValueType])).toEqual([
            ["declared", "string"],
            ["count", "string"],
        ]);
    });

    it("accepts any key when the restriction is off", () => {
        const { errors } = importFromCSV(csv("brandNewKey,string,v"), [schemaRow], false, []);

        expect(errors).toEqual([]);
    });
});
