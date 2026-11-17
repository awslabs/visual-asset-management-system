/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { GeoTIFF, GeoTIFFImage } from "geotiff";
import { RasterFacts } from "./rasterEligibility";
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
    const imageCount = await tiff.getImageCount();
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
    const overviewCount = Math.max(0, imageCount - 1);
    const isCog =
        tiled && (Math.max(width, height) <= OVERVIEW_REQUIRED_ABOVE || overviewCount >= 1);

    return {
        georeferenced,
        crs,
        isCog,
        sizeBytes,
        bboxWgs84,
        width,
        height,
        bandCount: image.getSamplesPerPixel(),
        tiled,
        overviewCount,
        nativeBox,
    };
}
