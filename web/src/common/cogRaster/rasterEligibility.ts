/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isSupportedCrs, isWebMercator, parseEpsgCode } from "./projections";

/**
 * Largest file the placed-image path will read. The path reads an overview of at most
 * PLACED_IMAGE_MAX_PIXELS, but a striped file without overviews makes the browser fetch and decode the
 * whole strip set, so the file size bounds that cost.
 */
export const PLACED_IMAGE_MAX_FILE_BYTES = 128 * 1024 * 1024;

/**
 * Largest ground extent (bounding-box diagonal, km) the placed-image path accepts. The image is placed
 * on four corner coordinates and MapLibre interpolates between them, so a projected raster is only
 * approximately right; the error grows with extent and stays small below this size.
 */
export const PLACED_IMAGE_MAX_DIAGONAL_KM = 100;

/** Longest side, in pixels, of the overview the placed-image path decodes. */
export const PLACED_IMAGE_MAX_PIXELS = 2048;

/** Shortest side the placed-image path aims for when it picks an overview. */
export const PLACED_IMAGE_TARGET_PIXELS = 2048;

/** Most rasters shown at once on one map; adding past it is refused with a message. */
export const MAX_RASTER_OVERLAYS = 8;

export const CONVERT_HINT =
    "Convert it with the TIF-to-COG pipeline (Web Mercator output) to show it as an exact overlay.";

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
}

export type RefusalReason =
    | "no-georeference"
    | "unsupported-crs"
    | "not-cloud-optimized"
    | "too-large-to-place"
    | "unreadable";

export type RasterDecision =
    | { kind: "tile" }
    | { kind: "placed"; approximate: boolean; approximateNote: string | null }
    | { kind: "refuse"; reason: RefusalReason; message: string };

export const APPROXIMATE_PLACEMENT_LABEL = "approximate placement (not reprojected)";

export const APPROXIMATE_PLACEMENT_NOTE = `This layer is shown with ${APPROXIMATE_PLACEMENT_LABEL}. Converting the file with the TIF-to-COG pipeline gives an exact overlay.`;

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
 * Decides how a raster is shown.
 *
 *  - tile: a COG in EPSG:3857, drawn with the COG tile protocol (exact, streamed).
 *  - placed: a small georeferenced file in a bundled CRS, drawn as one image on four corners
 *    (approximate, labelled as such).
 *  - refuse: anything else, with the sentence to show.
 *
 * The library behind the tile path reads only EPSG:3857 and draws nothing, without an error, for any
 * other CRS, so a non-3857 file never takes the tile path.
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
    if (mercator && facts.isCog === true) {
        return { kind: "tile" };
    }

    if (!mercator) {
        if (parseEpsgCode(facts.crs) === null) {
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
    if (facts.sizeBytes !== undefined && facts.sizeBytes > PLACED_IMAGE_MAX_FILE_BYTES) {
        return {
            kind: "refuse",
            reason: mercator ? "not-cloud-optimized" : "too-large-to-place",
            message: mercator
                ? `This file is not cloud-optimized and is too large to stream directly. ${CONVERT_HINT}`
                : `This file is too large to place on the map without conversion. ${CONVERT_HINT}`,
        };
    }
    if (facts.bboxWgs84) {
        if (bboxDiagonalKm(facts.bboxWgs84) > PLACED_IMAGE_MAX_DIAGONAL_KM) {
            return {
                kind: "refuse",
                reason: mercator ? "not-cloud-optimized" : "too-large-to-place",
                message: `This file covers too large an area to place on the map without conversion. ${CONVERT_HINT}`,
            };
        }
    }
    // Web Mercator corners map to the map's own projection exactly; any other CRS is approximate.
    return mercator
        ? { kind: "placed", approximate: false, approximateNote: null }
        : { kind: "placed", approximate: true, approximateNote: APPROXIMATE_PLACEMENT_NOTE };
}
