/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { normalizeFeaturesEnabled } from "./featuresEnabled";

describe("normalizeFeaturesEnabled", () => {
    it("returns an array unchanged (string entries only)", () => {
        expect(normalizeFeaturesEnabled(["LOCATIONSERVICES", "NOOPENSEARCH"])).toEqual([
            "LOCATIONSERVICES",
            "NOOPENSEARCH",
        ]);
    });

    it("returns an empty array for an empty array", () => {
        expect(normalizeFeaturesEnabled([])).toEqual([]);
    });

    it("returns an empty array for false (the malformed boolean case that crashed the page)", () => {
        const result = normalizeFeaturesEnabled(false);
        expect(Array.isArray(result)).toBe(true);
        expect(result).toEqual([]);
    });

    it("returns an empty array for true", () => {
        expect(normalizeFeaturesEnabled(true)).toEqual([]);
    });

    it("returns an empty array for undefined and null", () => {
        expect(normalizeFeaturesEnabled(undefined)).toEqual([]);
        expect(normalizeFeaturesEnabled(null)).toEqual([]);
    });

    it("splits a comma-separated string into trimmed entries", () => {
        expect(normalizeFeaturesEnabled("LOCATIONSERVICES, NOOPENSEARCH")).toEqual([
            "LOCATIONSERVICES",
            "NOOPENSEARCH",
        ]);
    });

    it("returns an empty array for an empty/whitespace string", () => {
        expect(normalizeFeaturesEnabled("")).toEqual([]);
        expect(normalizeFeaturesEnabled("   ")).toEqual([]);
    });

    it("drops non-string entries from a mixed array", () => {
        expect(normalizeFeaturesEnabled(["LOCATIONSERVICES", 5, null, "NOOPENSEARCH"])).toEqual([
            "LOCATIONSERVICES",
            "NOOPENSEARCH",
        ]);
    });

    it("the result always supports .includes() without throwing", () => {
        for (const input of [false, true, undefined, null, 42, {}, "X"]) {
            expect(() => normalizeFeaturesEnabled(input).includes("X")).not.toThrow();
        }
    });
});
