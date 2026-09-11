/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A metadata row stored before its type was recorded must be repairable through the editor.
 *
 * The backend GET was made tolerant of such a row so an operator could see it and fix it, and it now
 * returns the value with a null type. That alone did not make the row repairable: the editor validates
 * client-side before it submits, `validateMetadataRow` forwards the row's type to
 * `validateMetadataValue`, and a null type fell through the type switch to its `default` branch. The
 * operator opened the editor, pressed save, and got "Unknown metadata value type: null" on a field they
 * had not touched — so the write that would have supplied the missing type could never be sent. The
 * server-side error had simply moved into the browser.
 *
 * The distinction these tests pin is between ABSENT and UNRECOGNISED. An absent type carries no
 * information to check the value against, so it is not an error; a misspelled one is a real mistake and
 * must still be reported. A fix that accepted both would make every typo silently valid.
 */

import { validateMetadataValue } from "./validationHelpers";
import { MAX_GEOJSON_NESTING_DEPTH } from "../../../common/constants/geoSearch";
import { MetadataValueType } from "../types/metadata.types";

// The stored shape under test: a value the row does have, and no type. Cast because the interface
// still declares the field non-nullable — which is itself why nothing caught this at compile time.
const noType = null as unknown as MetadataValueType;

describe("validateMetadataValue with no recorded type", () => {
    it("accepts a value whose type was never recorded", () => {
        const result = validateMetadataValue("some stored text", noType);
        expect(result.isValid).toBe(true);
        expect(result.errors).toEqual([]);
    });

    it("accepts values a type check could not have validated anyway", () => {
        // Whatever the value looks like, there is no declared type to measure it against, so none of
        // these may be rejected on type grounds.
        for (const value of ["123", "true", "{}", "not json at all", "2026-01-01"]) {
            const result = validateMetadataValue(value, noType);
            expect(result.isValid).toBe(true);
        }
    });

    it("still reports a type it does not recognise", () => {
        // The control that keeps the fix narrow. Without this, accepting a null type could be widened
        // into accepting anything, and a misspelled "strng" would validate silently.
        const result = validateMetadataValue("some stored text", "strng" as MetadataValueType);
        expect(result.isValid).toBe(false);
        expect(result.errors.join(" ")).toContain("Unknown metadata value type");
    });

    it("still enforces a type that IS recorded", () => {
        // The second control: tolerating an absent type must not weaken checking a present one.
        const bad = validateMetadataValue("not-a-number", "number" as MetadataValueType);
        expect(bad.isValid).toBe(false);

        const good = validateMetadataValue("42", "number" as MetadataValueType);
        expect(good.isValid).toBe(true);
    });

    it("treats an empty value as acceptable regardless of type", () => {
        // Pre-existing behaviour, asserted so the new early return above it cannot be mistaken for the
        // thing that provides it — the two are independent and both are relied on.
        expect(validateMetadataValue("", "number" as MetadataValueType).isValid).toBe(true);
        expect(validateMetadataValue("   ", noType).isValid).toBe(true);
    });
});

/**
 * The client-side half of the GeoJSON nesting bound the metadata API enforces.
 *
 * `validate_metadata_value_common` refuses a `geojson` value whose GeometryCollections nest past
 * MAX_GEOJSON_NESTING_DEPTH, because the validator recurses once per level. The editor validates
 * before it submits, so without the same check the operator gets a raw 400 back from a save instead
 * of a message on the field.
 */
const nestedCollection = (levels: number): any => {
    let geometry: any = { type: "Point", coordinates: [1, 2] };
    for (let i = 1; i < levels; i += 1) {
        geometry = { type: "GeometryCollection", geometries: [geometry] };
    }
    return geometry;
};

describe("validateMetadataValue for the geojson type", () => {
    const geojson = "geojson" as MetadataValueType;

    it("accepts nesting at the bound", () => {
        const value = JSON.stringify(nestedCollection(MAX_GEOJSON_NESTING_DEPTH));
        expect(validateMetadataValue(value, geojson).isValid).toBe(true);
    });

    it("rejects nesting one level past the bound, naming the limit", () => {
        const value = JSON.stringify(nestedCollection(MAX_GEOJSON_NESTING_DEPTH + 1));
        const result = validateMetadataValue(value, geojson);
        expect(result.isValid).toBe(false);
        expect(result.errors.join(" ")).toContain(String(MAX_GEOJSON_NESTING_DEPTH));
    });

    it("still accepts the ordinary shapes an operator pastes", () => {
        // Without this the depth check is indistinguishable from a validator that rejects everything.
        const polygon = JSON.stringify({
            type: "Polygon",
            coordinates: [
                [
                    [0, 0],
                    [1, 0],
                    [1, 1],
                    [0, 0],
                ],
            ],
        });
        expect(validateMetadataValue(polygon, geojson).isValid).toBe(true);
        const feature = JSON.stringify({
            type: "Feature",
            geometry: nestedCollection(2),
        });
        expect(validateMetadataValue(feature, geojson).isValid).toBe(true);
    });
});
