/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    APPROXIMATE_PLACEMENT_LABEL,
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
        expect((big as any).message).toMatch(/TIF-to-COG pipeline/);
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

describe("pickOverviewIndex", () => {
    it("takes the smallest image that is still at least the target", () => {
        expect(pickOverviewIndex([10000, 5000, 2500, 1250, 625])).toBe(2);
    });
});
