/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    APPROXIMATE_PLACEMENT_LABEL,
    crsLabel,
    DecodePlan,
    PLACED_IMAGE_MAX_DECODE_BYTES,
    PLACED_IMAGE_MAX_DECODE_PIXELS,
    PLACED_IMAGE_MAX_DIAGONAL_KM,
    PLACED_IMAGE_MAX_FILE_BYTES,
    bboxDiagonalKm,
    decideRasterDisplay,
    decodeRefusal,
    pickOverviewIndex,
} from "./rasterEligibility";

const SMALL_BOX: [number, number, number, number] = [10, 50, 10.1, 50.1];
const SMALL_DECODE: DecodePlan = {
    width: 512,
    height: 512,
    bands: 3,
    bytesPerSample: 1,
    fetchBytes: 100_000,
};
const placeable = {
    georeferenced: true,
    sizeBytes: 1000,
    bboxWgs84: SMALL_BOX,
    decode: SMALL_DECODE,
};

describe("decideRasterDisplay", () => {
    it("uses the tile path for a Web Mercator COG, whatever its size", () => {
        expect(
            decideRasterDisplay({
                georeferenced: true,
                crs: "EPSG:3857",
                isCog: true,
                sizeBytes: 5 * 1024 ** 3,
            })
        ).toEqual({ kind: "tile" });
    });

    it("tiles only the Web Mercator codes the tile library accepts", () => {
        expect(
            decideRasterDisplay({ georeferenced: true, crs: "EPSG:102113", isCog: true })
        ).toEqual({
            kind: "tile",
        });
        // The library refuses 900913; such a file is placed (exactly) instead of drawn blank.
        expect(
            decideRasterDisplay({ ...placeable, crs: "EPSG:900913", isCog: true })
        ).toMatchObject({ kind: "placed", approximate: false });
    });

    it("never uses the tile path for a COG in another system", () => {
        const decision = decideRasterDisplay({ ...placeable, crs: "EPSG:4326", isCog: true });
        expect(decision.kind).toBe("placed");
        const big = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            isCog: true,
            sizeBytes: PLACED_IMAGE_MAX_FILE_BYTES + 1,
        });
        expect(big.kind).toBe("refuse");
        expect((big as any).message).toMatch(/COG Conversion pipeline/);
    });

    it("places a small file in a bundled system and marks it approximate", () => {
        const decision = decideRasterDisplay({ ...placeable, crs: "EPSG:32633", isCog: false });
        expect(decision).toMatchObject({ kind: "placed", approximate: true });
        expect((decision as any).approximateNote).toContain(APPROXIMATE_PLACEMENT_LABEL);
        expect((decision as any).approximateNote).toContain("exact overlay");
    });

    it("places a small non-COG Web Mercator file without calling it approximate", () => {
        expect(decideRasterDisplay({ ...placeable, crs: "EPSG:3857", isCog: false })).toEqual({
            kind: "placed",
            approximate: false,
            approximateNote: null,
        });
    });

    it("refuses a large non-COG Web Mercator file as not cloud-optimized", () => {
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:3857",
            isCog: false,
            sizeBytes: PLACED_IMAGE_MAX_FILE_BYTES + 1,
        });
        expect(decision).toMatchObject({ kind: "refuse", reason: "not-cloud-optimized" });
    });

    it("accepts a file at the size limit", () => {
        const at = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            sizeBytes: PLACED_IMAGE_MAX_FILE_BYTES,
        });
        expect(at.kind).toBe("placed");
    });

    it("refuses an extent over the ground limit", () => {
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            bboxWgs84: [10, 50, 12, 52],
        });
        expect(decision).toMatchObject({ kind: "refuse", reason: "too-large-to-place" });
        expect(bboxDiagonalKm([10, 50, 12, 52])).toBeGreaterThan(PLACED_IMAGE_MAX_DIAGONAL_KM);
        expect(bboxDiagonalKm(SMALL_BOX)).toBeLessThan(PLACED_IMAGE_MAX_DIAGONAL_KM);
    });

    it("says so when there is no georeference", () => {
        const decision = decideRasterDisplay({ georeferenced: false });
        expect(decision).toMatchObject({ kind: "refuse", reason: "no-georeference" });
    });

    it("says so for a CRS that is user-defined or not bundled", () => {
        expect(decideRasterDisplay({ georeferenced: true, crs: null })).toMatchObject({
            kind: "refuse",
            reason: "unsupported-crs",
        });
        const swiss = decideRasterDisplay({ georeferenced: true, crs: "EPSG:2056" });
        expect(swiss).toMatchObject({ kind: "refuse", reason: "unsupported-crs" });
        expect((swiss as any).message).toMatch(/unsupported CRS/);
        expect((swiss as any).message).toMatch(/EPSG:2056/);
    });
});

describe("decode cap (D1)", () => {
    it("refuses a 60000 x 60000 image with no overview before any decode, however small the file", () => {
        const bomb: DecodePlan = {
            width: 60000,
            height: 60000,
            bands: 1,
            bytesPerSample: 1,
            fetchBytes: 3_600_000,
        };
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            sizeBytes: 3_600_000,
            decode: bomb,
        });
        expect(decision).toMatchObject({ kind: "refuse", reason: "too-many-pixels" });
        expect((decision as any).message).toMatch(/60,000 x 60,000 pixels/);
    });

    it("refuses an image within the pixel cap whose bands take too much memory", () => {
        const side = Math.floor(Math.sqrt(PLACED_IMAGE_MAX_DECODE_PIXELS));
        const manyBands: DecodePlan = {
            width: side,
            height: side,
            bands: 8,
            bytesPerSample: 8,
            fetchBytes: 1000,
        };
        expect(side * side * 64).toBeGreaterThan(PLACED_IMAGE_MAX_DECODE_BYTES);
        expect(decodeRefusal(manyBands)).toMatchObject({ reason: "too-many-pixels" });
    });

    it("accepts an image at the caps", () => {
        expect(
            decodeRefusal({ width: 8192, height: 8192, bands: 3, bytesPerSample: 1, fetchBytes: 1 })
        ).toBeNull();
    });

    it("refuses an image whose strips or tiles fetch more than the file limit", () => {
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            decode: { ...SMALL_DECODE, fetchBytes: PLACED_IMAGE_MAX_FILE_BYTES + 1 },
        });
        expect(decision).toMatchObject({ kind: "refuse", reason: "too-large-to-place" });
    });
});

describe("the placed path fails closed (D2)", () => {
    it("refuses when neither the file size nor the image's byte counts are known", () => {
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            sizeBytes: undefined,
            decode: { ...SMALL_DECODE, fetchBytes: null },
        });
        expect(decision).toMatchObject({ kind: "refuse", reason: "size-unknown" });
    });

    it("places a file of unknown size when its byte counts prove it small", () => {
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            sizeBytes: undefined,
        });
        expect(decision.kind).toBe("placed");
    });

    it("refuses facts without a decode plan or extent, never reading the file to find out", () => {
        expect(
            decideRasterDisplay({ ...placeable, crs: "EPSG:4326", decode: undefined })
        ).toMatchObject({ kind: "refuse", reason: "unreadable" });
        expect(
            decideRasterDisplay({ ...placeable, crs: "EPSG:4326", bboxWgs84: null })
        ).toMatchObject({ kind: "refuse", reason: "unreadable" });
    });
});

describe("messages say what is wrong (item 3)", () => {
    const message = (facts: any) => (decideRasterDisplay(facts) as any).message as string;
    const big = PLACED_IMAGE_MAX_FILE_BYTES + 1;

    it("(b) names a COG in UTM as a COG in that projection, not as 'not a COG'", () => {
        const text = message({ ...placeable, crs: "EPSG:32610", isCog: true, sizeBytes: big });
        expect(text).toMatch(/^This COG is in EPSG:32610 \(UTM zone 10N\)\./);
        expect(text).toMatch(/draws EPSG:3857 COGs exactly/);
        expect(text).toMatch(
            /COG Conversion pipeline, which writes <file>\.cog\.tif beside it in EPSG:3857/
        );
        expect(text).not.toMatch(/not (a )?cloud|not a COG/i);
    });

    it("(b) calls a non-cloud-optimized file in another projection a TIFF, by size and by area", () => {
        const bySize = message({ ...placeable, crs: "EPSG:4326", isCog: false, sizeBytes: big });
        expect(bySize).toMatch(/^This TIFF is in EPSG:4326 \(WGS 84, longitude\/latitude\)\./);
        expect(bySize).toMatch(/this one is too large for that/);
        const byArea = message({
            ...placeable,
            crs: "EPSG:4326",
            isCog: true,
            bboxWgs84: [10, 50, 12, 52],
        });
        expect(byArea).toMatch(/^This COG is in EPSG:4326/);
        expect(byArea).toMatch(/covers too large an area for that/);
    });

    it("(b) names the CRS of a file in one the viewer does not bundle", () => {
        const text = message({ georeferenced: true, crs: "EPSG:2056", isCog: true });
        expect(text).toMatch(/^This COG is in EPSG:2056, which the map viewer does not support/);
        expect(text).toMatch(/COG Conversion pipeline/);
    });

    it("(a) says a large EPSG:3857 file that is not a COG is not cloud-optimized", () => {
        const text = message({ ...placeable, crs: "EPSG:3857", isCog: false, sizeBytes: big });
        expect(text).toMatch(/is in EPSG:3857 but is not a Cloud Optimized GeoTIFF/);
        expect(text).toMatch(/not tiled, or it lacks the overviews a large file needs/);
        expect(text).toMatch(/COG Conversion pipeline/);
    });

    it.each([
        ["EPSG:900913", true],
        ["EPSG:3785", true],
        ["EPSG:102100", false],
    ])(
        "(a) does not call a COG in the Web Mercator alias %s 'not a COG' (r5-D1)",
        (crs, pipelineSkips) => {
            const decision = decideRasterDisplay({
                ...placeable,
                crs,
                isCog: true,
                sizeBytes: big,
            }) as any;
            expect(decision).toMatchObject({ kind: "refuse", reason: "web-mercator-alias" });
            expect(decision.message).toMatch(
                new RegExp(`^This COG is in ${crs}, a Web Mercator code`)
            );
            expect(decision.message).not.toMatch(/not a Cloud Optimized GeoTIFF/);
            if (pipelineSkips) {
                // The pipeline leaves such a COG unchanged, so it is not offered.
                expect(decision.message).toMatch(/Re-save it with the EPSG:3857 code/);
                expect(decision.message).not.toMatch(/Convert it with/);
            } else {
                expect(decision.message).toMatch(/Convert it with the COG Conversion pipeline/);
            }
            // By area too.
            const byArea = decideRasterDisplay({
                ...placeable,
                crs,
                isCog: true,
                bboxWgs84: [10, 50, 12, 52],
            }) as any;
            expect(byArea.reason).toBe("web-mercator-alias");
            expect(byArea.message).toMatch(/covers too large an area/);
        }
    );

    it("(a) says the layout could not be determined when it is unknown, not 'not a COG' (r5-D1)", () => {
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:3857",
            isCog: undefined,
            sizeBytes: big,
        }) as any;
        expect(decision).toMatchObject({ kind: "refuse", reason: "layout-unknown" });
        expect(decision.message).toMatch(/could not be determined/);
        expect(decision.message).not.toMatch(/is not a Cloud Optimized GeoTIFF/);
        expect(decision.message).toMatch(/If it is not one, the COG Conversion pipeline/);
    });

    it("still takes the tile path for a COG in EPSG:3857 or 102113", () => {
        expect(decideRasterDisplay({ ...placeable, crs: "EPSG:3857", isCog: true }).kind).toBe(
            "tile"
        );
        expect(decideRasterDisplay({ ...placeable, crs: "EPSG:102113", isCog: true }).kind).toBe(
            "tile"
        );
    });

    it("(c) says an image with too many pixels is too large to draw as an image", () => {
        const text = message({
            ...placeable,
            crs: "EPSG:4326",
            decode: { width: 60000, height: 60000, bands: 3, bytesPerSample: 1, fetchBytes: 1000 },
        });
        expect(text).toMatch(/60,000 x 60,000 pixels with no smaller overview/);
        expect(text).toMatch(/too large to draw in the browser as an image/);
    });

    it("(d) says a file with no georeferencing carries none, and does not offer conversion", () => {
        const text = message({ georeferenced: false });
        expect(text).toMatch(/no georeferencing/);
        expect(text).not.toMatch(/COG Conversion pipeline/);
    });

    it("(e) keeps the antimeridian wording, with no conversion offered", () => {
        const text = message({
            ...placeable,
            crs: "EPSG:4326",
            bboxWgs84: [179.9, 10, 180.2, 10.1],
        });
        expect(text).toMatch(/crosses longitude 180/);
        expect(text).not.toMatch(/COG Conversion pipeline/);
    });

    it("names the CRS in the projection family people know, and falls back to the text", () => {
        expect(crsLabel("EPSG:32633")).toBe("EPSG:32633 (UTM zone 33N)");
        expect(crsLabel("EPSG:32755")).toBe("EPSG:32755 (UTM zone 55S)");
        expect(crsLabel("EPSG:2154")).toBe("EPSG:2154");
        expect(crsLabel(null)).toBe("an unknown coordinate system");
    });
});

describe("antimeridian (D3)", () => {
    it("says the raster crosses longitude 180 and cannot be shown or converted, not 'convert it'", () => {
        const decision = decideRasterDisplay({
            ...placeable,
            crs: "EPSG:4326",
            bboxWgs84: [179.9, 10, 180.2, 10.1],
        });
        expect(decision).toMatchObject({ kind: "refuse", reason: "crosses-antimeridian" });
        expect((decision as any).message).toMatch(/crosses longitude 180/);
        expect((decision as any).message).toMatch(/cannot be shown on the map or converted/);
        expect((decision as any).message).not.toMatch(/COG Conversion pipeline/);
        expect(
            decideRasterDisplay({
                ...placeable,
                crs: "EPSG:4326",
                bboxWgs84: [-180.3, 10, -179.9, 10.1],
            })
        ).toMatchObject({ reason: "crosses-antimeridian" });
    });

    it("does not take a raster that ends exactly on longitude 180 to cross it", () => {
        expect(
            decideRasterDisplay({
                ...placeable,
                crs: "EPSG:4326",
                bboxWgs84: [179.9, 10, 180, 10.1],
            }).kind
        ).toBe("placed");
    });
});

describe("pickOverviewIndex", () => {
    it("takes the smallest image that is still at least the target", () => {
        expect(pickOverviewIndex([10000, 5000, 2500, 1250, 625])).toBe(2);
    });
});
