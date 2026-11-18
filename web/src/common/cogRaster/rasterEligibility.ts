/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isSupportedCrs, isWebMercator, parseEpsgCode } from "./projections";

/**
 * Largest file the placed-image path will read. The path reads an overview of at most
 * PLACED_IMAGE_MAX_PIXELS, but a striped file without overviews makes the browser fetch and decode the
 * whole strip set, so the file size bounds that cost. The bytes of the image actually decoded
 * (its strip or tile byte counts) are held to the same bound.
 */
export const PLACED_IMAGE_MAX_FILE_BYTES = 128 * 1024 * 1024;

/**
 * Most pixels (width x height) of the image the placed-image path decodes. geotiff.js decodes the
 * chosen image at full resolution before it scales it down, so a file whose smallest usable image is
 * larger than this is refused before any pixel is read. It matches the TIFF image viewer's limit.
 */
export const PLACED_IMAGE_MAX_DECODE_PIXELS = 100_000_000;

/**
 * Most bytes (width x height x bands x bytes per sample) the decoded image may take in memory. It
 * bounds a many-band or 64-bit file that the pixel cap alone would let through.
 */
export const PLACED_IMAGE_MAX_DECODE_BYTES = 256 * 1024 * 1024;

/**
 * Largest ground extent (bounding-box diagonal, km) the placed-image path accepts. The image is placed
 * on four corner coordinates and MapLibre interpolates between them, so a projected raster is only
 * approximately right; the error grows with extent and stays small below this size.
 */
export const PLACED_IMAGE_MAX_DIAGONAL_KM = 100;

/** Longest side, in pixels, of the image the placed-image path draws. */
export const PLACED_IMAGE_MAX_PIXELS = 2048;

/** Shortest side the placed-image path aims for when it picks an overview. */
export const PLACED_IMAGE_TARGET_PIXELS = 2048;

/** Most rasters shown at once on one map; adding past it is refused with a message. */
export const MAX_RASTER_OVERLAYS = 8;

export const CONVERT_HINT =
    "Convert it with the TIF-to-COG pipeline (Web Mercator output) to show it as an exact overlay.";

/** The image of a TIFF the placed-image path would decode, from the header alone. */
export interface DecodePlan {
    width: number;
    height: number;
    bands: number;
    bytesPerSample: number;
    /** Sum of the image's strip or tile byte counts: what the read fetches. Null when unknown. */
    fetchBytes: number | null;
}

/** What is known about a raster before it is opened, from attributes or from its TIFF header. */
export interface RasterFacts {
    georeferenced?: boolean;
    /** "EPSG:3857", "EPSG:32633" ... ; null or missing when the file has no usable CRS. */
    crs?: string | null;
    /** Cloud-optimized: tiled, with overviews when large. */
    isCog?: boolean;
    sizeBytes?: number;
    /** [west, south, east, north] in WGS84, when it could be worked out. */
    bboxWgs84?: [number, number, number, number] | null;
    /** The image the placed-image path would decode. Required for that path (read from the header). */
    decode?: DecodePlan | null;
}

export type RefusalReason =
    | "no-georeference"
    | "unsupported-crs"
    | "not-cloud-optimized"
    | "too-large-to-place"
    | "too-many-pixels"
    | "crosses-antimeridian"
    | "size-unknown"
    | "unreadable";

export type RasterDecision =
    | { kind: "tile" }
    | { kind: "placed"; approximate: boolean; approximateNote: string | null }
    | { kind: "refuse"; reason: RefusalReason; message: string };

/** Degrees of slack, so a raster that ends exactly on longitude 180 is not taken to cross it. */
const ANTIMERIDIAN_TOLERANCE = 1e-6;

export const ANTIMERIDIAN_MESSAGE =
    "This raster crosses longitude 180 (the antimeridian), so it cannot be shown on the map or converted as it is. Split it at longitude 180 and upload each part.";

export const APPROXIMATE_PLACEMENT_LABEL = "approximate placement (not reprojected)";

export const APPROXIMATE_PLACEMENT_NOTE = `This layer is shown with ${APPROXIMATE_PLACEMENT_LABEL}. Converting the file with the TIF-to-COG pipeline gives an exact overlay.`;

/**
 * Codes the COG tile library itself accepts as Web Mercator. Other Web Mercator aliases (900913,
 * 102100, 3785) are refused by the library, so such a file is placed instead.
 */
export const TILE_MERCATOR_CODES = [3857, 102113];

/** Great-circle diagonal of a WGS84 box, in km. */
export function bboxDiagonalKm(bbox: [number, number, number, number]): number {
    const [west, south, east, north] = bbox;
    const rad = Math.PI / 180;
    const dLat = (north - south) * rad;
    const dLon = (east - west) * rad;
    const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(south * rad) * Math.cos(north * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(a)));
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

/** The refusal for an image too large to decode in the browser, or null when it is within the caps. */
export function decodeRefusal(plan: DecodePlan): RasterDecision | null {
    const pixels = plan.width * plan.height;
    const bytes = pixels * Math.max(1, plan.bands) * Math.max(1, plan.bytesPerSample);
    if (pixels > PLACED_IMAGE_MAX_DECODE_PIXELS || bytes > PLACED_IMAGE_MAX_DECODE_BYTES) {
        return {
            kind: "refuse",
            reason: "too-many-pixels",
            message: `This file is ${plan.width.toLocaleString(
                "en-US"
            )} x ${plan.height.toLocaleString(
                "en-US"
            )} pixels with no smaller overview, too large to draw in the browser. ${CONVERT_HINT}`,
        };
    }
    return null;
}

/**
 * Decides how a raster is shown.
 *
 *  - tile: a COG in EPSG:3857, drawn with the COG tile protocol (exact, streamed).
 *  - placed: a small georeferenced file in a bundled CRS, drawn as one image on four corners
 *    (approximate, labelled as such).
 *  - refuse: anything else, with the sentence to show.
 *
 * The library behind the tile path reads only EPSG:3857 and draws nothing, without an error, for any
 * other CRS, so a non-3857 file never takes the tile path.
 *
 * The placed path fails closed: it needs the decode plan, the WGS84 extent and either the file size or
 * the image's byte counts. Facts that lack any of them (attributes alone, an unreadable header) are
 * refused for that path, never read in full to find out.
 */
export function decideRasterDisplay(facts: RasterFacts): RasterDecision {
    if (facts.georeferenced === false) {
        return {
            kind: "refuse",
            reason: "no-georeference",
            message:
                "This TIFF has no geographic reference, so it cannot be placed on the map. Open it in the image viewer instead.",
        };
    }

    const mercator = isWebMercator(facts.crs);
    const code = parseEpsgCode(facts.crs);
    if (mercator && facts.isCog === true && code !== null && TILE_MERCATOR_CODES.includes(code)) {
        return { kind: "tile" };
    }

    if (!mercator) {
        if (code === null) {
            return {
                kind: "refuse",
                reason: "unsupported-crs",
                message:
                    "This TIFF uses a coordinate system the map viewer does not recognise (unsupported CRS), so it cannot be placed on the map.",
            };
        }
        if (!isSupportedCrs(facts.crs)) {
            return {
                kind: "refuse",
                reason: "unsupported-crs",
                message: `This TIFF uses ${facts.crs}, an unsupported CRS for direct display. ${CONVERT_HINT}`,
            };
        }
    }

    // From here the file is georeferenced, in a bundled CRS (Web Mercator without COG layout included),
    // and goes to the placed-image path if it is small enough.
    const tooLarge: RasterDecision = {
        kind: "refuse",
        reason: mercator ? "not-cloud-optimized" : "too-large-to-place",
        message: mercator
            ? `This file is not cloud-optimized and is too large to stream directly. ${CONVERT_HINT}`
            : `This file is too large to place on the map without conversion. ${CONVERT_HINT}`,
    };
    if (facts.sizeBytes !== undefined && facts.sizeBytes > PLACED_IMAGE_MAX_FILE_BYTES) {
        return tooLarge;
    }
    if (!facts.decode) {
        return {
            kind: "refuse",
            reason: "unreadable",
            message: "This raster's header could not be read, so it is not drawn.",
        };
    }
    const pixelRefusal = decodeRefusal(facts.decode);
    if (pixelRefusal) return pixelRefusal;
    const fetchBytes = facts.decode.fetchBytes;
    if (fetchBytes !== null && fetchBytes > PLACED_IMAGE_MAX_FILE_BYTES) return tooLarge;
    if (facts.sizeBytes === undefined && fetchBytes === null) {
        return {
            kind: "refuse",
            reason: "size-unknown",
            message: `The size of this file could not be confirmed, so it is not read in the browser. ${CONVERT_HINT}`,
        };
    }
    if (!facts.bboxWgs84) {
        return {
            kind: "refuse",
            reason: "unreadable",
            message: "The extent of this raster could not be worked out, so it is not drawn.",
        };
    }
    // Corners are kept continuous across the antimeridian (projections.ts), so a raster that crosses
    // longitude 180 has a corner beyond +-180. Neither this viewer nor the COG pipeline can draw or
    // convert it, so the message says to split it, not to convert it.
    const [west, , east] = facts.bboxWgs84;
    if (west < -180 - ANTIMERIDIAN_TOLERANCE || east > 180 + ANTIMERIDIAN_TOLERANCE) {
        return {
            kind: "refuse",
            reason: "crosses-antimeridian",
            message: ANTIMERIDIAN_MESSAGE,
        };
    }
    if (bboxDiagonalKm(facts.bboxWgs84) > PLACED_IMAGE_MAX_DIAGONAL_KM) {
        return {
            kind: "refuse",
            reason: mercator ? "not-cloud-optimized" : "too-large-to-place",
            message: `This file covers too large an area to place on the map without conversion. ${CONVERT_HINT}`,
        };
    }
    // Web Mercator corners map to the map's own projection exactly; any other CRS is approximate.
    return mercator
        ? { kind: "placed", approximate: false, approximateNote: null }
        : { kind: "placed", approximate: true, approximateNote: APPROXIMATE_PLACEMENT_NOTE };
}
