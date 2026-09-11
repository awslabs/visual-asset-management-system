/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Deterministic palette for distinguishing GeoJSON polygons across the search
 * map, mini-map thumbnails, the expanded map preview modal, and the database
 * mini-maps. Values are picked to read well over typical map tiles in both
 * light and dark mode and to stay distinct from the selected-item highlight
 * color (#0972d3) used elsewhere on the search map.
 */
export const POLYGON_COLOR_PALETTE = [
    "#d91515", // red
    "#037f0c", // green
    "#9c27b0", // purple
    "#e8810c", // orange
    "#0d6c8a", // teal
    "#a23300", // brown
    "#c4318f", // magenta
    "#5a4ab2", // indigo
    "#1f7a1f", // forest
    "#b76e00", // amber
];

/** Default polygon color when no stable key is supplied. Matches legacy behavior. */
export const DEFAULT_POLYGON_COLOR = "#d91515";

/** Stable string hash suitable for picking a palette index. */
const hashString = (s: string): number => {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
        h = (h << 5) - h + s.charCodeAt(i);
        h |= 0;
    }
    return Math.abs(h);
};

/** Pick a deterministic color from the palette for a given key. */
export const colorForKey = (key: string | undefined): string => {
    if (!key) return DEFAULT_POLYGON_COLOR;
    return POLYGON_COLOR_PALETTE[hashString(key) % POLYGON_COLOR_PALETTE.length];
};

/** Pick a palette color by an absolute index. */
export const colorForIndex = (index: number): string =>
    POLYGON_COLOR_PALETTE[Math.abs(index) % POLYGON_COLOR_PALETTE.length];

/**
 * A single colorable sub-shape extracted from an arbitrary GeoJSON value.
 * Each entry holds a Geometry that can be passed directly to a single
 * Source/Layer pair, plus a stable color.
 */
export interface ColoredGeoShape {
    /** Stable id within the parent shape (e.g. "0", "1.poly0"). */
    id: string;
    /** A GeoJSON Geometry (Polygon, LineString, Point, etc.). */
    geometry: any;
    /** Hex color for fill+outline. */
    color: string;
}

/**
 * Walk an arbitrary GeoJSON value and return one entry per renderable
 * sub-shape, each with its own deterministic color from the palette.
 *
 * Behavior:
 *   - Polygon            -> 1 colored shape
 *   - MultiPolygon       -> N colored shapes (one per polygon ring set)
 *   - LineString         -> 1 colored shape
 *   - MultiLineString    -> N colored shapes
 *   - GeometryCollection -> recurses into each geometry
 *   - Feature / FeatureCollection -> unwrapped to underlying geometries
 *   - Point / MultiPoint -> ignored (callers render points separately)
 *
 * @param baseColorOffset Starting palette index. Lets callers stagger colors
 *                        across different parent results (e.g. on the search
 *                        map) so the same sub-polygon index doesn't always
 *                        pick the same hue.
 */
export const splitGeoJsonForColoring = (geoJson: any, baseColorOffset = 0): ColoredGeoShape[] => {
    if (!geoJson || typeof geoJson !== "object") return [];

    // Unwrap Features and FeatureCollections to underlying geometries.
    if (geoJson.type === "Feature") {
        return splitGeoJsonForColoring(geoJson.geometry, baseColorOffset);
    }
    if (geoJson.type === "FeatureCollection" && Array.isArray(geoJson.features)) {
        const out: ColoredGeoShape[] = [];
        let offset = baseColorOffset;
        for (let i = 0; i < geoJson.features.length; i++) {
            const sub = splitGeoJsonForColoring(geoJson.features[i], offset);
            sub.forEach((s) => out.push({ ...s, id: `f${i}.${s.id}` }));
            offset += sub.length;
        }
        return out;
    }
    if (geoJson.type === "GeometryCollection" && Array.isArray(geoJson.geometries)) {
        const out: ColoredGeoShape[] = [];
        let offset = baseColorOffset;
        for (let i = 0; i < geoJson.geometries.length; i++) {
            const sub = splitGeoJsonForColoring(geoJson.geometries[i], offset);
            sub.forEach((s) => out.push({ ...s, id: `g${i}.${s.id}` }));
            offset += sub.length;
        }
        return out;
    }

    if (geoJson.type === "Polygon" && Array.isArray(geoJson.coordinates)) {
        return [
            {
                id: "0",
                geometry: { type: "Polygon", coordinates: geoJson.coordinates },
                color: colorForIndex(baseColorOffset),
            },
        ];
    }
    if (geoJson.type === "MultiPolygon" && Array.isArray(geoJson.coordinates)) {
        return geoJson.coordinates.map((coords: any, i: number) => ({
            id: `${i}`,
            geometry: { type: "Polygon", coordinates: coords },
            color: colorForIndex(baseColorOffset + i),
        }));
    }
    if (geoJson.type === "LineString" && Array.isArray(geoJson.coordinates)) {
        return [
            {
                id: "0",
                geometry: { type: "LineString", coordinates: geoJson.coordinates },
                color: colorForIndex(baseColorOffset),
            },
        ];
    }
    if (geoJson.type === "MultiLineString" && Array.isArray(geoJson.coordinates)) {
        return geoJson.coordinates.map((coords: any, i: number) => ({
            id: `${i}`,
            geometry: { type: "LineString", coordinates: coords },
            color: colorForIndex(baseColorOffset + i),
        }));
    }

    // Points are handled by the caller as Markers, not as colored sources.
    return [];
};

/** Convenience: hash a key into a palette index, used as a baseColorOffset. */
export const offsetForKey = (key: string | undefined): number => {
    if (!key) return 0;
    return hashString(key);
};
