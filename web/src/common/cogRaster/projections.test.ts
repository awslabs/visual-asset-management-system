/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    cornersToLonLat,
    isSupportedCrs,
    isWebMercator,
    parseEpsgCode,
    projectionDefinition,
    toLonLat,
} from "./projections";

describe("parseEpsgCode", () => {
    it("accepts the common spellings", () => {
        expect(parseEpsgCode("EPSG:32633")).toBe(32633);
        expect(parseEpsgCode("epsg:4326")).toBe(4326);
        expect(parseEpsgCode(" 3857 ")).toBe(3857);
        expect(parseEpsgCode(3857)).toBe(3857);
    });
    it("rejects anything that is not an EPSG code", () => {
        expect(parseEpsgCode("Local grid")).toBeNull();
        expect(parseEpsgCode("")).toBeNull();
        expect(parseEpsgCode(null)).toBeNull();
        expect(parseEpsgCode(undefined)).toBeNull();
    });
});

describe("bundled systems", () => {
    it.each([4326, 3857, 900913, 32633, 32701, 26914, 25832, 3035, 5070, 2154, 27700, 3413, 3031])(
        "bundles EPSG:%i",
        (code) => {
            expect(projectionDefinition(code)).not.toBeNull();
            expect(isSupportedCrs(`EPSG:${code}`)).toBe(true);
        }
    );
    it("does not bundle a system it has no definition for, and never looks one up", () => {
        expect(isSupportedCrs("EPSG:2056")).toBe(false);
        expect(isSupportedCrs("EPSG:32661")).toBe(false);
        expect(toLonLat("EPSG:2056", 2600000, 1200000)).toBeNull();
    });
    it("recognises Web Mercator and its aliases", () => {
        expect(isWebMercator("EPSG:3857")).toBe(true);
        expect(isWebMercator("EPSG:900913")).toBe(true);
        expect(isWebMercator("EPSG:4326")).toBe(false);
    });
});

describe("toLonLat", () => {
    it("projects UTM 33N near the Czech border", () => {
        const [lon, lat] = toLonLat("EPSG:32633", 500000, 5000000)!;
        expect(lon).toBeCloseTo(15.0, 3);
        expect(lat).toBeCloseTo(45.15, 1);
    });
    it("projects UTM south", () => {
        const [lon, lat] = toLonLat("EPSG:32717", 300000, 9000000)!;
        expect(lon).toBeCloseTo(-82.82, 1);
        expect(lat).toBeCloseTo(-9.03, 0);
    });
    it("projects Web Mercator metres", () => {
        const [lon, lat] = toLonLat("EPSG:3857", 1113194.9079327357, 0)!;
        expect(lon).toBeCloseTo(10, 5);
        expect(lat).toBeCloseTo(0, 5);
    });
    it("keeps geographic degrees", () => {
        expect(toLonLat("EPSG:4326", 10, 50)).toEqual([10, 50]);
    });
    it("projects British National Grid and Lambert-93 to plausible places", () => {
        const [lonB, latB] = toLonLat("EPSG:27700", 530000, 180000)!; // central London
        expect(lonB).toBeCloseTo(-0.1, 0);
        expect(latB).toBeCloseTo(51.5, 0);
        const [lonL, latL] = toLonLat("EPSG:2154", 652000, 6862000)!; // Paris
        expect(lonL).toBeCloseTo(2.35, 0);
        expect(latL).toBeCloseTo(48.85, 0);
    });
});

describe("cornersToLonLat", () => {
    it("returns top-left, top-right, bottom-right, bottom-left", () => {
        const corners = cornersToLonLat("EPSG:4326", [10, 49, 11, 50])!;
        expect(corners).toEqual([
            [10, 50],
            [11, 50],
            [11, 49],
            [10, 49],
        ]);
    });
    it("is null for a system that is not bundled", () => {
        expect(cornersToLonLat("EPSG:2056", [0, 0, 1, 1])).toBeNull();
    });
    it("keeps longitudes continuous across the antimeridian", () => {
        // UTM zone 60 (174E-180E): the east edge reaches 180
        const corners = cornersToLonLat("EPSG:32660", [700000, 5000000, 900000, 5100000])!;
        const lons = corners.map((corner) => corner[0]);
        expect(Math.max(...lons) - Math.min(...lons)).toBeLessThan(10);
    });
});
