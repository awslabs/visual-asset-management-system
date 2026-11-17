/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    APPROXIMATE_PLACEMENT_LABEL,
    PLACED_IMAGE_MAX_DIAGONAL_KM,
    PLACED_IMAGE_MAX_FILE_BYTES,
    bboxDiagonalKm,
    decideRasterDisplay,
} from "./rasterEligibility";

const SMALL_BOX: [number, number, number, number] = [10, 50, 10.1, 50.1];

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

    it("never uses the tile path for a COG in another system", () => {
        const decision = decideRasterDisplay({
            georeferenced: true,
            crs: "EPSG:4326",
            isCog: true,
            sizeBytes: 1000,
            bboxWgs84: SMALL_BOX,
        });
        expect(decision.kind).toBe("placed");
        const big = decideRasterDisplay({
            georeferenced: true,
            crs: "EPSG:4326",
            isCog: true,
            sizeBytes: PLACED_IMAGE_MAX_FILE_BYTES + 1,
        });
        expect(big.kind).toBe("refuse");
        expect((big as any).message).toMatch(/TIF-to-COG pipeline/);
    });

    it("places a small file in a bundled system and marks it approximate", () => {
        const decision = decideRasterDisplay({
            georeferenced: true,
            crs: "EPSG:32633",
            isCog: false,
            sizeBytes: 1000,
            bboxWgs84: SMALL_BOX,
        });
        expect(decision).toMatchObject({ kind: "placed", approximate: true });
        expect((decision as any).approximateNote).toContain(APPROXIMATE_PLACEMENT_LABEL);
        expect((decision as any).approximateNote).toContain("exact overlay");
    });

    it("places a small non-COG Web Mercator file without calling it approximate", () => {
        expect(
            decideRasterDisplay({
                georeferenced: true,
                crs: "EPSG:3857",
                isCog: false,
                sizeBytes: 1000,
                bboxWgs84: SMALL_BOX,
            })
        ).toEqual({ kind: "placed", approximate: false, approximateNote: null });
    });

    it("refuses a large non-COG Web Mercator file as not cloud-optimized", () => {
        const decision = decideRasterDisplay({
            georeferenced: true,
            crs: "EPSG:3857",
            isCog: false,
            sizeBytes: PLACED_IMAGE_MAX_FILE_BYTES + 1,
        });
        expect(decision).toMatchObject({ kind: "refuse", reason: "not-cloud-optimized" });
    });

    it("refuses a file at the size limit plus one byte, and accepts the limit itself", () => {
        const at = decideRasterDisplay({
            georeferenced: true,
            crs: "EPSG:4326",
            sizeBytes: PLACED_IMAGE_MAX_FILE_BYTES,
            bboxWgs84: SMALL_BOX,
        });
        expect(at.kind).toBe("placed");
    });

    it("refuses an extent over the ground limit", () => {
        const decision = decideRasterDisplay({
            georeferenced: true,
            crs: "EPSG:4326",
            sizeBytes: 1000,
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
