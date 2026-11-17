/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readTiffFacts, crsFromGeoKeys } from "./tiffHeader";
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
