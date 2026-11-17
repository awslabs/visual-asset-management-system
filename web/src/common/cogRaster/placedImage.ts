/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { GeoTIFF, GeoTIFFImage } from "geotiff";
import { PLACED_IMAGE_MAX_PIXELS, PLACED_IMAGE_TARGET_PIXELS } from "./rasterEligibility";
import { cornersToLonLat } from "./projections";
import { TiffHeaderFacts } from "./tiffHeader";

type NumberArray = ArrayLike<number>;

export interface PixelBands {
    width: number;
    height: number;
    /** One array per band, in file order. */
    bands: NumberArray[];
    /** Value that means "no data" in every band, when the file declares one. */
    noData: number | null;
    /** TIFF PhotometricInterpretation: 0/1 gray, 2 RGB, 3 palette. */
    photometric: number;
    /** Palette as 3 x 2^bits 16-bit values (R block, G block, B block), for photometric 3. */
    colorMap?: ArrayLike<number> | null;
    /** True when an extra sample (the band after the colour bands) is an alpha band. */
    hasAlpha: boolean;
    /** Maximum value of the file's integer type, used as the stretch range for 8 and 16-bit colour. */
    typeMax: number | null;
}

/**
 * Picks the image to read: the smallest one whose longest side is still at least `target`, so the
 * result is sharp enough without decoding more than needed. Images are given as longest sides in file
 * order (full resolution first). When none reaches the target the full-resolution image (index 0) is
 * used. Returns the index.
 */
export function pickOverviewIndex(
    longestSides: number[],
    target: number = PLACED_IMAGE_TARGET_PIXELS
): number {
    let best = -1;
    longestSides.forEach((side, index) => {
        if (side >= target && (best === -1 || side < longestSides[best])) best = index;
    });
    return best === -1 ? 0 : best;
}

/** The size to read an image at: its own size, scaled down so the longest side is at most `cap`. */
export function readSize(
    width: number,
    height: number,
    cap: number = PLACED_IMAGE_MAX_PIXELS
): { width: number; height: number } {
    const longest = Math.max(width, height);
    if (longest <= cap) return { width, height };
    const scale = cap / longest;
    return {
        width: Math.max(1, Math.round(width * scale)),
        height: Math.max(1, Math.round(height * scale)),
    };
}

/** Low and high clip values for a stretch: the 2nd and 98th percentile of the valid samples. */
export function stretchRange(
    values: NumberArray,
    noData: number | null,
    maxSamples = 65536
): [number, number] {
    const step = Math.max(1, Math.floor(values.length / maxSamples));
    const sample: number[] = [];
    for (let i = 0; i < values.length; i += step) {
        const value = values[i];
        if (Number.isFinite(value) && (noData === null || value !== noData)) sample.push(value);
    }
    if (sample.length === 0) return [0, 1];
    sample.sort((a, b) => a - b);
    const low = sample[Math.floor((sample.length - 1) * 0.02)];
    const high = sample[Math.ceil((sample.length - 1) * 0.98)];
    return high > low ? [low, high] : [low, low + 1];
}

function scale(value: number, low: number, high: number): number {
    const t = (value - low) / (high - low);
    return t <= 0 ? 0 : t >= 1 ? 255 : Math.round(t * 255);
}

/**
 * Turns raster bands into 8-bit RGBA with dtype-aware scaling:
 *  - palette files go through their colour map;
 *  - 8-bit colour is copied as is;
 *  - 16-bit and float bands are stretched between their 2nd and 98th percentile (per band for colour);
 *  - a single band is drawn as gray with the same stretch;
 *  - no-data, NaN and infinite samples become transparent, as does an alpha band value of 0.
 */
export function bandsToRgba(input: PixelBands): Uint8ClampedArray {
    const { width, height, bands, noData, photometric, colorMap, hasAlpha } = input;
    const count = width * height;
    const out = new Uint8ClampedArray(count * 4);
    if (bands.length === 0) return out;

    const isNoData = (index: number): boolean => {
        // A pixel is no-data when every colour band equals the declared value, or any is not finite.
        let allEqual = noData !== null;
        for (let b = 0; b < colourBandCount(); b++) {
            const v = bands[b][index];
            if (!Number.isFinite(v)) return true;
            if (noData === null || v !== noData) allEqual = false;
        }
        return allEqual;
    };

    const colourBandCount = (): number => {
        if (photometric === 3 || bands.length === 1) return 1;
        if (bands.length === 2) return 1; // gray + alpha
        return 3;
    };

    if (photometric === 3 && colorMap && colorMap.length >= 3) {
        const size = Math.floor(colorMap.length / 3);
        for (let i = 0; i < count; i++) {
            const v = bands[0][i];
            const o = i * 4;
            if (isNoData(i) || v < 0 || v >= size) continue;
            out[o] = colorMap[v] >> 8;
            out[o + 1] = colorMap[size + v] >> 8;
            out[o + 2] = colorMap[2 * size + v] >> 8;
            out[o + 3] = 255;
        }
        return out;
    }

    const colourCount = colourBandCount();
    const fixedRange =
        input.typeMax !== null && input.typeMax <= 255
            ? ([0, input.typeMax] as [number, number])
            : null;
    const ranges: Array<[number, number]> = [];
    for (let b = 0; b < colourCount; b++) {
        ranges.push(fixedRange ?? stretchRange(bands[b], noData));
    }
    // For colour imagery keep one shared range across bands when the type is wider than 8 bits, so
    // the hue is not shifted by per-band stretching.
    if (colourCount === 3 && !fixedRange) {
        const low = Math.min(...ranges.map((r) => r[0]));
        const high = Math.max(...ranges.map((r) => r[1]));
        for (let b = 0; b < 3; b++) ranges[b] = [low, high];
    }

    const alphaBand =
        hasAlpha || bands.length === 2 ? bands[Math.min(bands.length, colourCount + 1) - 1] : null;
    const alphaMax = input.typeMax !== null && input.typeMax > 0 ? input.typeMax : 255;

    for (let i = 0; i < count; i++) {
        const o = i * 4;
        if (isNoData(i)) continue;
        if (colourCount === 1) {
            const g = scale(bands[0][i], ranges[0][0], ranges[0][1]);
            out[o] = g;
            out[o + 1] = g;
            out[o + 2] = g;
        } else {
            out[o] = scale(bands[0][i], ranges[0][0], ranges[0][1]);
            out[o + 1] = scale(bands[1][i], ranges[1][0], ranges[1][1]);
            out[o + 2] = scale(bands[2][i], ranges[2][0], ranges[2][1]);
        }
        out[o + 3] = alphaBand ? Math.round((alphaBand[i] / alphaMax) * 255) : 255;
    }
    return out;
}

/** Four corner positions as [lon, lat], in MapLibre image-source order. */
export type PlacedCorners = [
    [number, number],
    [number, number],
    [number, number],
    [number, number]
];

export interface PlacedImage {
    blob: Blob;
    corners: PlacedCorners;
    width: number;
    height: number;
}

function maxOfType(image: GeoTIFFImage): number | null {
    const format = image.getSampleFormat(0);
    const bits = image.getBitsPerSample(0);
    // SampleFormat 1 = unsigned integer, 2 = signed integer, 3 = float
    if (format === 1) return Math.pow(2, bits) - 1;
    return null;
}

export async function readPlacedPixels(
    tiff: GeoTIFF,
    facts: TiffHeaderFacts,
    signal?: AbortSignal
): Promise<PixelBands> {
    const count = await tiff.getImageCount();
    const images: GeoTIFFImage[] = [];
    for (let i = 0; i < count; i++) images.push(await tiff.getImage(i));
    const index = pickOverviewIndex(
        images.map((image) => Math.max(image.getWidth(), image.getHeight()))
    );
    const image = images[index];
    const { width, height } = readSize(image.getWidth(), image.getHeight());

    const directory: any = image.fileDirectory;
    const photometric = Number(directory.getValue?.("PhotometricInterpretation") ?? 1);
    const extra = directory.getValue?.("ExtraSamples");
    const extraList: number[] = extra === undefined ? [] : Array.isArray(extra) ? extra : [extra];
    const noData = image.getGDALNoData();
    let colorMap: ArrayLike<number> | null = null;
    if (photometric === 3) {
        try {
            colorMap = (await directory.loadValue("ColorMap")) ?? null;
        } catch {
            colorMap = null;
        }
    }

    const resampleMethod = noData === null ? "bilinear" : "nearest";
    // ExtraSamples 1 = associated alpha, 2 = unassociated alpha
    const hasAlphaSample = extraList.some((value) => value === 1 || value === 2);

    // YCbCr (JPEG), CMYK and CIELab files are converted to RGB by the reader, so their samples are not
    // mistaken for red, green and blue.
    if (photometric === 5 || photometric === 6 || photometric === 8) {
        const rgb: any = await image.readRGB({
            width,
            height,
            interleave: false,
            resampleMethod,
            enableAlpha: hasAlphaSample,
            signal,
        } as any);
        const rgbBands = (Array.isArray(rgb) ? rgb : [rgb]) as NumberArray[];
        return {
            width,
            height,
            bands: rgbBands,
            noData,
            photometric: 2,
            colorMap: null,
            hasAlpha: rgbBands.length > 3,
            typeMax: 255,
        };
    }

    const rasters = await image.readRasters({
        width,
        height,
        interleave: false,
        // A nearest-neighbour read keeps no-data samples from blending into their neighbours.
        resampleMethod,
        signal,
    });
    const bands = Array.isArray(rasters) ? (rasters as NumberArray[]) : [rasters as NumberArray];
    const hasAlpha = hasAlphaSample;
    return {
        width,
        height,
        bands,
        noData,
        photometric,
        colorMap,
        hasAlpha: hasAlpha && bands.length > (photometric === 2 ? 3 : 1),
        typeMax: maxOfType(image),
    };
}

async function rgbaToBlob(rgba: Uint8ClampedArray, width: number, height: number): Promise<Blob> {
    const pixels = new ImageData(rgba as any, width, height);
    if (typeof OffscreenCanvas !== "undefined") {
        const canvas = new OffscreenCanvas(width, height);
        canvas.getContext("2d")!.putImageData(pixels, 0, 0);
        return canvas.convertToBlob({ type: "image/png" });
    }
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    canvas.getContext("2d")!.putImageData(pixels, 0, 0);
    return new Promise((resolve, reject) =>
        canvas.toBlob(
            (blob) => (blob ? resolve(blob) : reject(new Error("The raster could not be drawn"))),
            "image/png"
        )
    );
}

/**
 * Reads a small georeferenced TIFF by ranged reads (the smallest overview at least 2048 px, capped at
 * 2048 px), draws it to a PNG blob and works out the four WGS84 corners to place it on. Nothing is
 * reprojected: the image keeps its own grid and only the corners move, which is why the layer is
 * labelled approximate for any CRS other than Web Mercator.
 */
export async function buildPlacedImage(
    tiff: GeoTIFF,
    facts: TiffHeaderFacts,
    signal?: AbortSignal
): Promise<PlacedImage> {
    if (!facts.nativeBox || !facts.crs) throw new Error("The raster has no usable georeference");
    const corners = cornersToLonLat(facts.crs, facts.nativeBox);
    if (!corners) throw new Error("The raster's extent could not be placed on the map");
    const pixels = await readPlacedPixels(tiff, facts, signal);
    const rgba = bandsToRgba(pixels);
    const blob = await rgbaToBlob(rgba, pixels.width, pixels.height);
    return { blob, corners, width: pixels.width, height: pixels.height };
}
