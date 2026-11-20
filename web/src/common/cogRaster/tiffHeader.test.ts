/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readTiffFacts, crsFromGeoKeys } from "./tiffHeader";
import { decideRasterDisplay } from "./rasterEligibility";
import { loadFixtureTiff, fixtureSize } from "./fixtures.testdata";

describe("crsFromGeoKeys", () => {
    it("reads a projected code, then a geographic one", () => {
        expect(crsFromGeoKeys({ ProjectedCSTypeGeoKey: 32633 })).toBe("EPSG:32633");
        expect(crsFromGeoKeys({ GeographicTypeGeoKey: 4326 })).toBe("EPSG:4326");
    });
    it("treats user-defined and absent systems as unknown", () => {
        expect(crsFromGeoKeys({ ProjectedCSTypeGeoKey: 32767 })).toBeNull();
        expect(
            crsFromGeoKeys({ ProjectedCSTypeGeoKey: 0, GeographicTypeGeoKey: 32767 })
        ).toBeNull();
        expect(crsFromGeoKeys(null)).toBeNull();
    });
});

describe("readTiffFacts", () => {
    it("reads a Web Mercator COG as cloud-optimized, with a WGS84 extent", async () => {
        const facts = await readTiffFacts(
            await loadFixtureTiff("cog-3857.tif"),
            fixtureSize("cog-3857.tif")
        );
        expect(facts.georeferenced).toBe(true);
        expect(facts.crs).toBe("EPSG:3857");
        expect(facts.tiled).toBe(true);
        expect(facts.isCog).toBe(true);
        const [west, south, east, north] = facts.bboxWgs84!;
        // The COG driver snaps the extent to the Web Mercator tile grid, so it covers (and slightly
        // exceeds) the 10.0-10.128 E, 49.872-50.0 N source.
        expect(west).toBeLessThanOrEqual(10.0);
        expect(east).toBeGreaterThanOrEqual(10.127);
        expect(south).toBeLessThanOrEqual(49.873);
        expect(north).toBeGreaterThanOrEqual(49.999);
        expect(east - west).toBeLessThan(0.5);
    });

    it("reads a striped file as not cloud-optimized", async () => {
        const facts = await readTiffFacts(await loadFixtureTiff("striped-3857.tif"));
        expect(facts.crs).toBe("EPSG:3857");
        expect(facts.tiled).toBe(false);
        expect(facts.isCog).toBe(false);
    });

    it("reads WGS84, UTM north and UTM south coordinate systems", async () => {
        expect((await readTiffFacts(await loadFixtureTiff("wgs84-striped.tif"))).crs).toBe(
            "EPSG:4326"
        );
        const utm = await readTiffFacts(await loadFixtureTiff("utm33-float-nodata.tif"));
        expect(utm.crs).toBe("EPSG:32633");
        expect(utm.bboxWgs84![0]).toBeGreaterThan(14.9);
        const south = await readTiffFacts(await loadFixtureTiff("utm17s-uint16.tif"));
        expect(south.crs).toBe("EPSG:32717");
        expect(south.bboxWgs84![3]).toBeLessThan(0);
    });

    it("reports a file with no georeference", async () => {
        const facts = await readTiffFacts(await loadFixtureTiff("no-georeference.tif"));
        expect(facts.georeferenced).toBe(false);
        expect(facts.crs).toBeNull();
        expect(facts.bboxWgs84).toBeNull();
    });

    it("reads a CRS that is not bundled, with no WGS84 extent", async () => {
        const facts = await readTiffFacts(await loadFixtureTiff("unsupported-2056.tif"));
        expect(facts.crs).toBe("EPSG:2056");
        expect(facts.bboxWgs84).toBeNull();
    });
});

describe("readTiffFacts decode plan (D1, D2)", () => {
    it("plans the full image of a pixel bomb and refuses it from the header alone", async () => {
        const facts = await readTiffFacts(await loadFixtureTiff("pixel-bomb-4326.tif"));
        expect(facts.decode).toMatchObject({ width: 11000, height: 10000, bands: 1 });
        // Over the decode cap, so its strip byte counts are not even read.
        expect(facts.decode.fetchBytes).toBeNull();
        expect(decideRasterDisplay({ ...facts, sizeBytes: 115_707 })).toMatchObject({
            kind: "refuse",
            reason: "too-many-pixels",
        });
    });

    it("reads the strip byte counts of a small striped file, so its size is proven", async () => {
        const facts = await readTiffFacts(await loadFixtureTiff("wgs84-striped.tif"));
        expect(facts.decode.fetchBytes).toBeGreaterThan(0);
        expect(facts.decode.fetchBytes).toBeLessThan(fixtureSize("wgs84-striped.tif"));
        // No size from the caller or the source: the byte counts still let it through.
        expect(decideRasterDisplay({ ...facts, sizeBytes: undefined }).kind).toBe("placed");
    });

    it("reads the tile byte counts of a tiled file and plans its smallest usable image", async () => {
        const facts = await readTiffFacts(await loadFixtureTiff("cog-3857.tif"));
        expect(facts.decodeIndex).toBe(0);
        expect(facts.decode.fetchBytes).toBeGreaterThan(0);
    });
});
