/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Client-side mirror of the geospatial constraints the VAMS API enforces.
 *
 * Mirrors `GeoPointModel` (lat/lon bounds, positive radius) and `MAX_GEOJSON_POSITIONS` in
 * `backend/backend/models/search.py`, and `MAX_GEOJSON_NESTING_DEPTH` in
 * `backend/backend/models/metadata.py` — the nesting bound is shared by the search filter and the
 * `geojson` metadata value type, so it governs both surfaces. Keep the values in sync. Without the
 * mirror a slipped decimal point reaches the backend and fails the WHOLE search request with a raw
 * Pydantic message that names no field, so a typo reads as "search is broken" rather than "this box
 * is out of range".
 */

export const LATITUDE_MIN = -90;
export const LATITUDE_MAX = 90;
export const LONGITUDE_MIN = -180;
export const LONGITUDE_MAX = 180;
export const MAX_GEOJSON_POSITIONS = 100000;
export const MAX_GEOJSON_NESTING_DEPTH = 32;

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
 *
 * Walks with an explicit stack. A pasted value nests without limit, and a recursive walk hits the
 * engine's call-stack limit first — which surfaces as "Maximum call stack size exceeded" instead of
 * the message naming the limit the value actually broke.
 */
export function countGeoJsonPositions(node: any): number {
    let total = 0;
    const stack: any[] = [node];
    while (stack.length > 0) {
        const current = stack.pop();
        if (Array.isArray(current)) {
            if (current.length > 0 && current.every((entry) => typeof entry === "number")) {
                total += 1;
            } else {
                stack.push(...current);
            }
        } else if (current !== null && typeof current === "object") {
            for (const key of POSITION_BEARING_KEYS) {
                if (key in current) stack.push(current[key]);
            }
        }
    }
    return total;
}

/** The geometries a GeoJSON value carries, unwrapping Feature and FeatureCollection. */
function geometriesOf(node: any): any[] {
    if (node === null || typeof node !== "object") return [];
    if (node.type === "Feature") return node.geometry ? [node.geometry] : [];
    if (node.type === "FeatureCollection") {
        return Array.isArray(node.features)
            ? node.features.filter((f: any) => f && f.geometry).map((f: any) => f.geometry)
            : [];
    }
    return [node];
}

/**
 * Counts a GeoJSON value's GeometryCollection nesting levels, stopping once `limit` is passed.
 *
 * Mirrors the depth `_validate_geometry` counts in `backend/backend/models/metadata.py`: one level
 * per geometry in the chain, so a bare Polygon is 1 and a GeometryCollection holding a Point is 2.
 * Reports `limit + 1` for anything deeper, and walks one level at a time so the measurement itself
 * is never what runs out of stack.
 */
export function geoJsonNestingDepth(node: any, limit: number = MAX_GEOJSON_NESTING_DEPTH): number {
    let depth = 0;
    let level = geometriesOf(node);
    while (level.length > 0 && depth <= limit) {
        depth += 1;
        const next: any[] = [];
        for (const entry of level) {
            if (entry !== null && typeof entry === "object" && Array.isArray(entry.geometries)) {
                next.push(...entry.geometries);
            }
        }
        level = next;
    }
    return depth;
}
