/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Client-side mirror of the geospatial constraints the search API enforces.
 *
 * Mirrors `GeoPointModel` (lat/lon bounds, positive radius) and `MAX_GEOJSON_POSITIONS` in
 * `backend/backend/models/search.py`; keep the two in sync. Without the mirror a slipped decimal
 * point reaches the backend and fails the WHOLE search request with a raw Pydantic message that
 * names no field, so a typo reads as "search is broken" rather than "this box is out of range".
 */

export const LATITUDE_MIN = -90;
export const LATITUDE_MAX = 90;
export const LONGITUDE_MIN = -180;
export const LONGITUDE_MAX = 180;
export const MAX_GEOJSON_POSITIONS = 100000;

/** Keys a GeoJSON value nests its positions under, matching the backend's walk. */
const POSITION_BEARING_KEYS = ["coordinates", "geometries", "geometry", "features"];

const capitalize = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

/**
 * Strict numeric parse, returning null for anything that is not a finite number.
 *
 * `parseFloat` stops at the first character it cannot read, so it accepts trailing garbage: both
 * "47.6abc" and "47.6.2" come back as 47.6 and a slipped keystroke is silently truncated into a
 * plausible coordinate. `Number` rejects the whole string instead.
 */
export function parseCoordinate(raw: string): number | null {
    const trimmed = (raw ?? "").trim();
    if (trimmed === "") return null;
    const value = Number(trimmed);
    return Number.isFinite(value) ? value : null;
}

export interface LatLonResult {
    /** Set when the pair parsed and both values are in range. */
    point?: { lat: number; lon: number };
    /** Set otherwise: a message naming which field is wrong. */
    error?: string;
}

/**
 * Parses and range-checks one latitude/longitude pair.
 *
 * `label` prefixes the message so a bounding-box corner is identifiable ("Top-left latitude must
 * be between -90 and 90."); omit it for a bare point.
 */
export function parseLatLon(latRaw: string, lonRaw: string, label?: string): LatLonResult {
    const where = label ? `${label} ` : "";
    if ((latRaw ?? "").trim() === "" || (lonRaw ?? "").trim() === "") {
        return { error: capitalize(`${where}latitude and longitude are required.`) };
    }
    const lat = parseCoordinate(latRaw);
    const lon = parseCoordinate(lonRaw);
    if (lat === null) return { error: capitalize(`${where}latitude must be a number.`) };
    if (lon === null) return { error: capitalize(`${where}longitude must be a number.`) };
    if (lat < LATITUDE_MIN || lat > LATITUDE_MAX) {
        return {
            error: capitalize(
                `${where}latitude must be between ${LATITUDE_MIN} and ${LATITUDE_MAX}.`
            ),
        };
    }
    if (lon < LONGITUDE_MIN || lon > LONGITUDE_MAX) {
        return {
            error: capitalize(
                `${where}longitude must be between ${LONGITUDE_MIN} and ${LONGITUDE_MAX}.`
            ),
        };
    }
    return { point: { lat, lon } };
}

/**
 * Counts the coordinate positions reachable from a GeoJSON value, the same walk the backend's
 * `_count_geojson_positions` performs: a flat array of numbers is one position, and only the four
 * position-bearing keys are descended into.
 */
export function countGeoJsonPositions(node: any): number {
    if (Array.isArray(node)) {
        if (node.length > 0 && node.every((entry) => typeof entry === "number")) return 1;
        return node.reduce((sum: number, entry: any) => sum + countGeoJsonPositions(entry), 0);
    }
    if (node !== null && typeof node === "object") {
        return POSITION_BEARING_KEYS.reduce(
            (sum, key) => sum + (key in node ? countGeoJsonPositions(node[key]) : 0),
            0
        );
    }
    return 0;
}
