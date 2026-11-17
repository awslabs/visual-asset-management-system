/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { GeoTIFF, GeoTIFFImage } from "geotiff";
import { DecodePlan, RasterFacts, decodeRefusal, pickOverviewIndex } from "./rasterEligibility";
import { cornersToLonLat, isSupportedCrs } from "./projections";

/** Largest side, in pixels, above which a COG must carry an overview (the shared isCog definition). */
const OVERVIEW_REQUIRED_ABOVE = 512;

export interface TiffHeaderFacts extends RasterFacts {
    width: number;
    height: number;
    bandCount: number;
    tiled: boolean;
    /** Number of reduced-resolution images after the full-resolution one. */
    overviewCount: number;
    /** [minX, minY, maxX, maxY] in the file's own CRS, when the file is georeferenced. */
    nativeBox: [number, number, number, number] | null;
    /** The image the placed-image path decodes and its size, from the header. */
    decode: DecodePlan;
    /** Index of that image among the full-resolution image and its overviews. */
    decodeIndex: number;
}

const USER_DEFINED = 32767;

/** "EPSG:n" for a file's coordinate system, or null when it is user-defined, unset or not EPSG. */
export function crsFromGeoKeys(
    keys: Partial<Record<string, any>> | null | undefined
): string | null {
    if (!keys) return null;
    const projected = Number(keys.ProjectedCSTypeGeoKey ?? 0);
    if (projected > 0 && projected !== USER_DEFINED) return `EPSG:${projected}`;
    const geographic = Number(keys.GeographicTypeGeoKey ?? 0);
    if (projected === 0 && geographic > 0 && geographic !== USER_DEFINED) {
        return `EPSG:${geographic}`;
    }
    return null;
}

async function loadGeoTags(image: GeoTIFFImage): Promise<void> {
    const directory: any = image.fileDirectory;
    for (const tag of [
        "GeoKeyDirectory",
        "GeoDoubleParams",
        "GeoAsciiParams",
        "ModelPixelScale",
        "ModelTiepoint",
        "ModelTransformation",
        "GDAL_NODATA",
        "ColorMap",
    ]) {
        if (typeof directory?.hasTag === "function" && directory.hasTag(tag)) {
            try {
                await directory.loadValue(tag);
            } catch {
                // a tag that cannot be read leaves that fact unknown
            }
        }
    }
}

function hasGeoTransform(image: GeoTIFFImage): boolean {
    const directory: any = image.fileDirectory;
    const has = (tag: string) => typeof directory?.hasTag === "function" && directory.hasTag(tag);
    return has("ModelTransformation") || (has("ModelTiepoint") && has("ModelPixelScale"));
}

/**
 * Reads the facts the viewer needs from a TIFF's first image and its image-directory list: size,
 * layout, coordinate system and extent. Only header ranges are fetched for a remote file.
 *
 * `isCog` here is the structural part of the shared definition the extractor applies (tiled, and an
 * overview when a side is over 512 px). The viewer prefers the stored attribute when there is one.
 */
export async function readTiffFacts(tiff: GeoTIFF, sizeBytes?: number): Promise<TiffHeaderFacts> {
    const image = await tiff.getImage(0);
    await loadGeoTags(image);
    const width = image.getWidth();
    const height = image.getHeight();
    const keys = image.getGeoKeys();
    const georeferenced = hasGeoTransform(image) && keys !== null && keys !== undefined;
    const crs = georeferenced ? crsFromGeoKeys(keys) : null;

    let nativeBox: [number, number, number, number] | null = null;
    if (georeferenced) {
        try {
            const box = image.getBoundingBox();
            if (box.length === 4 && box.every(Number.isFinite)) {
                const [x0, y0, x1, y1] = box;
                let minX = Math.min(x0, x1);
                let maxX = Math.max(x0, x1);
                let minY = Math.min(y0, y1);
                let maxY = Math.max(y0, y1);
                // PixelIsPoint: the tie point is the centre of the first pixel, so the box is half a pixel off.
                if (Number(keys?.GTRasterTypeGeoKey) === 2 && width > 0 && height > 0) {
                    const halfX = (maxX - minX) / width / 2;
                    const halfY = (maxY - minY) / height / 2;
                    minX -= halfX;
                    maxX += halfX;
                    minY -= halfY;
                    maxY += halfY;
                }
                nativeBox = [minX, minY, maxX, maxY];
            }
        } catch {
            nativeBox = null;
        }
    }

    let bboxWgs84: [number, number, number, number] | null = null;
    if (nativeBox && crs && isSupportedCrs(crs)) {
        const corners = cornersToLonLat(crs, nativeBox);
        if (corners) {
            const lons = corners.map((corner) => corner[0]);
            const lats = corners.map((corner) => corner[1]);
            bboxWgs84 = [
                Math.min(...lons),
                Math.min(...lats),
                Math.max(...lons),
                Math.max(...lats),
            ];
        }
    }

    const tiled = image.isTiled === true;
    const images = await readImageList(tiff, image);
    const overviewCount = Math.max(0, images.length - 1);
    const isCog =
        tiled && (Math.max(width, height) <= OVERVIEW_REQUIRED_ABOVE || overviewCount >= 1);

    // The image the placed path would decode, chosen and sized from the header alone.
    const decodeIndex = pickOverviewIndex(
        images.map((candidate) => Math.max(candidate.getWidth(), candidate.getHeight()))
    );
    const decode = await decodePlanOf(images[decodeIndex]);

    // A ranged read reports the object's total length in Content-Range; geotiff.js keeps it.
    const sourceSize = Number((tiff as any)?.source?.fileSize);
    const knownSize =
        sizeBytes ?? (Number.isFinite(sourceSize) && sourceSize > 0 ? sourceSize : undefined);

    return {
        georeferenced,
        crs,
        isCog,
        sizeBytes: knownSize,
        bboxWgs84,
        width,
        height,
        bandCount: image.getSamplesPerPixel(),
        tiled,
        overviewCount,
        nativeBox,
        decode,
        decodeIndex,
    };
}

/** Most image directories looked at; a COG has a handful of overviews, a hostile file may have many. */
export const MAX_IMAGE_DIRECTORIES = 16;

/** Most strips or tiles whose byte counts are read; above it the fetch size is left unknown. */
export const MAX_BLOCK_COUNT = 1_000_000;

/** The full-resolution image followed by its overviews (reduced-resolution images, masks left out). */
export async function readImageList(tiff: GeoTIFF, first: GeoTIFFImage): Promise<GeoTIFFImage[]> {
    const images: GeoTIFFImage[] = [first];
    for (let index = 1; index < MAX_IMAGE_DIRECTORIES; index++) {
        let candidate: GeoTIFFImage;
        try {
            candidate = await tiff.getImage(index);
        } catch {
            break; // no further image directory
        }
        const subfileType = Number((candidate.fileDirectory as any)?.getValue?.("NewSubfileType"));
        // Bit 2 (value 4) marks a transparency mask, which is not an overview of the image.
        if (Number.isFinite(subfileType) && (subfileType & 4) !== 0) continue;
        if (candidate.getSamplesPerPixel() !== first.getSamplesPerPixel()) continue;
        images.push(candidate);
    }
    return images;
}

/** Size, sample layout and fetch size of one image, from its directory. */
async function decodePlanOf(image: GeoTIFFImage): Promise<DecodePlan> {
    const width = image.getWidth();
    const height = image.getHeight();
    const bands = image.getSamplesPerPixel();
    let bytesPerSample = 1;
    try {
        bytesPerSample = Math.max(1, Math.ceil(image.getBitsPerSample(0) / 8));
    } catch {
        bytesPerSample = 8; // unknown sample size: assume the widest
    }
    const plan: DecodePlan = { width, height, bands, bytesPerSample, fetchBytes: null };
    // Byte counts are read only for an image within the decode caps, and only when their count is
    // bounded, so a hostile header cannot make this read large.
    if (decodeRefusal(plan)) return plan;
    const directory: any = image.fileDirectory;
    const planar = Number(directory?.getValue?.("PlanarConfiguration") ?? 1) === 2 ? bands : 1;
    let blocks: number;
    if (image.isTiled) {
        const tileWidth = image.getTileWidth();
        const tileHeight = image.getTileHeight();
        blocks = Math.ceil(width / tileWidth) * Math.ceil(height / tileHeight) * planar;
    } else {
        const rows = Number(directory?.getValue?.("RowsPerStrip") ?? height) || height;
        blocks = Math.ceil(height / Math.max(1, rows)) * planar;
    }
    if (!Number.isFinite(blocks) || blocks <= 0 || blocks > MAX_BLOCK_COUNT) return plan;
    try {
        const counts = await directory.loadValue(
            image.isTiled ? "TileByteCounts" : "StripByteCounts"
        );
        if (counts && typeof counts.length === "number" && counts.length <= MAX_BLOCK_COUNT) {
            let total = 0;
            for (let i = 0; i < counts.length; i++) total += Number(counts[i]);
            if (Number.isFinite(total)) plan.fetchBytes = total;
        }
    } catch {
        plan.fetchBytes = null;
    }
    return plan;
}
