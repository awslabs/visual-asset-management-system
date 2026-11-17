/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    bandsToRgba,
    pickOverviewIndex,
    readPlacedPixels,
    readSize,
    stretchRange,
} from "./placedImage";
import { readTiffFacts } from "./tiffHeader";
import { loadFixtureTiff } from "./fixtures.testdata";

describe("pickOverviewIndex", () => {
    it("takes the smallest image that is still at least the target", () => {
        expect(pickOverviewIndex([10000, 5000, 2500, 1250, 625])).toBe(2);
    });
    it("takes an exact match", () => {
        expect(pickOverviewIndex([4096, 2048, 1024])).toBe(1);
    });
    it("falls back to full resolution when no image reaches the target", () => {
        expect(pickOverviewIndex([1500, 750])).toBe(0);
        expect(pickOverviewIndex([])).toBe(0);
    });
});

describe("readSize", () => {
    it("keeps a small image and scales a large one to the cap", () => {
        expect(readSize(1000, 500)).toEqual({ width: 1000, height: 500 });
        expect(readSize(8192, 4096)).toEqual({ width: 2048, height: 1024 });
        expect(readSize(100, 5000)).toEqual({ width: 41, height: 2048 });
    });
});

describe("stretchRange", () => {
    it("clips to the 2nd and 98th percentile and ignores no-data and NaN", () => {
        const values = Array.from({ length: 1001 }, (_, i) => i);
        values.push(-9999, NaN);
        const [low, high] = stretchRange(values, -9999);
        expect(low).toBeGreaterThanOrEqual(19);
        expect(low).toBeLessThanOrEqual(21);
        expect(high).toBeGreaterThanOrEqual(979);
        expect(high).toBeLessThanOrEqual(981);
    });
    it("returns a usable range for a constant or empty band", () => {
        expect(stretchRange([5, 5, 5], null)).toEqual([5, 6]);
        expect(stretchRange([NaN], null)).toEqual([0, 1]);
    });
});

describe("bandsToRgba", () => {
    const base = {
        width: 2,
        height: 1,
        noData: null,
        photometric: 1,
        hasAlpha: false,
        typeMax: 255,
    };

    it("copies 8-bit colour as is, opaque", () => {
        const rgba = bandsToRgba({
            ...base,
            photometric: 2,
            bands: [
                [10, 20],
                [30, 40],
                [50, 60],
            ],
        });
        expect(Array.from(rgba)).toEqual([10, 30, 50, 255, 20, 40, 60, 255]);
    });

    it("uses an alpha band as transparency", () => {
        const rgba = bandsToRgba({
            ...base,
            photometric: 2,
            hasAlpha: true,
            bands: [
                [10, 20],
                [30, 40],
                [50, 60],
                [255, 0],
            ],
        });
        expect(rgba[3]).toBe(255);
        expect(rgba[7]).toBe(0);
    });

    it("stretches a 16-bit single band and makes no-data transparent", () => {
        const rgba = bandsToRgba({
            ...base,
            width: 4,
            noData: 0,
            typeMax: 65535,
            bands: [[0, 1000, 2000, 3000]],
        });
        expect(rgba[3]).toBe(0);
        expect(rgba[7]).toBe(255);
        expect(rgba[4]).toBeLessThan(rgba[8]);
        expect(rgba[8]).toBeLessThan(rgba[12]);
        expect(rgba[15]).toBe(255);
    });

    it("makes NaN transparent in a float band and scales the rest to the full range", () => {
        const rgba = bandsToRgba({
            ...base,
            width: 3,
            typeMax: null,
            bands: [[NaN, 0.5, 1.5]],
        });
        expect(rgba[3]).toBe(0);
        expect(rgba[7]).toBe(255);
        expect(rgba[4]).toBe(0);
        expect(rgba[8]).toBe(255);
    });

    it("keeps one stretch across the bands of 16-bit colour", () => {
        const rgba = bandsToRgba({
            ...base,
            photometric: 2,
            width: 2,
            typeMax: 65535,
            bands: [
                [0, 1000],
                [0, 1000],
                [0, 1000],
            ],
        });
        expect(rgba[4]).toBe(rgba[5]);
        expect(rgba[5]).toBe(rgba[6]);
    });

    it("maps a palette through its colour map", () => {
        const colorMap = new Array(3 * 4).fill(0);
        colorMap[1] = 65535; // red of index 1
        colorMap[4 + 2] = 65535; // green of index 2
        const rgba = bandsToRgba({
            ...base,
            photometric: 3,
            bands: [[1, 2]],
            colorMap,
        });
        expect(Array.from(rgba.slice(0, 4))).toEqual([255, 0, 0, 255]);
        expect(Array.from(rgba.slice(4, 8))).toEqual([0, 255, 0, 255]);
    });
});

describe("readPlacedPixels on the fixtures", () => {
    it("reads RGB bytes from a striped WGS84 file", async () => {
        const tiff = await loadFixtureTiff("wgs84-striped.tif");
        const facts = await readTiffFacts(tiff);
        const pixels = await readPlacedPixels(tiff, facts);
        expect([pixels.width, pixels.height]).toEqual([64, 48]);
        expect(pixels.bands).toHaveLength(3);
        expect(pixels.photometric).toBe(2);
        expect(pixels.typeMax).toBe(255);
    });

    it("reads the nodata of a float file and leaves the hole transparent", async () => {
        const tiff = await loadFixtureTiff("utm33-float-nodata.tif");
        const pixels = await readPlacedPixels(tiff, await readTiffFacts(tiff));
        expect(pixels.noData).toBe(-9999);
        const rgba = bandsToRgba(pixels);
        expect(rgba[3]).toBe(0);
        expect(rgba[(40 * 64 + 40) * 4 + 3]).toBe(255);
    });

    it("reads a 16-bit file", async () => {
        const tiff = await loadFixtureTiff("utm17s-uint16.tif");
        const pixels = await readPlacedPixels(tiff, await readTiffFacts(tiff));
        expect(pixels.typeMax).toBe(65535);
        const rgba = bandsToRgba(pixels);
        expect(rgba[(63 * 64 + 63) * 4]).toBeGreaterThan(rgba[0]);
    });

    it("converts a JPEG YCbCr file to RGB rather than reading Y, Cb and Cr as colours", async () => {
        const tiff = await loadFixtureTiff("utm13-jpeg-ycbcr.tif");
        const pixels = await readPlacedPixels(tiff, await readTiffFacts(tiff));
        expect(pixels.photometric).toBe(2);
        const rgba = bandsToRgba(pixels);
        const centre = (32 * 64 + 32) * 4;
        // The source is pure red (220, 0, 0); JPEG keeps it close.
        expect(rgba[centre]).toBeGreaterThan(190);
        expect(rgba[centre + 1]).toBeLessThan(40);
        expect(rgba[centre + 2]).toBeLessThan(40);
    });
});
