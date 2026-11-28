/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { LngLatBoundsLike } from "maplibre-gl";
import type { LocationData } from "./locationUtils";

/** Degrees added on every side, so a single point or a straight line still has an area to frame. */
export const BOUNDS_PADDING_DEGREES = 0.01;

/** The framing options of the Map view: room around the pins, and no street-level zoom on one point. */
export const FIT_BOUNDS_OPTIONS = { padding: 40, duration: 1000, maxZoom: 14 };

/**
 * The covering arc of a set of longitude intervals, as [west, east] with `east` above 180 when the arc
 * crosses the antimeridian: the full circle minus the widest stretch that no interval covers. Each
 * interval is one shape's own [min, max] (a point is a single value), so a shape that spans a wide
 * range keeps all of it covered, and a footprint split at 180 (two parts, one near -180 and one near 180)
 * beside Bay Area files frames the Pacific between them instead of the whole world.
 */
function coveringArc(intervals: Array<[number, number]>): [number, number] {
    const sorted = intervals
        .map(
            ([a, b]) =>
                [Math.max(-180, Math.min(180, a)), Math.max(-180, Math.min(180, b))] as [
                    number,
                    number
                ]
        )
        .sort((x, y) => x[0] - y[0]);
    // Merge overlapping intervals into the covered runs.
    const runs: Array<[number, number]> = [];
    for (const [start, end] of sorted) {
        const last = runs[runs.length - 1];
        if (last && start <= last[1]) last[1] = Math.max(last[1], end);
        else runs.push([start, end]);
    }
    // The widest uncovered stretch: between two runs, or round the back from the last run to the first.
    let widest = runs[0][0] + 360 - runs[runs.length - 1][1];
    let after = -1;
    for (let i = 0; i < runs.length - 1; i++) {
        const gap = runs[i + 1][0] - runs[i][1];
        if (gap > widest) {
            widest = gap;
            after = i;
        }
    }
    if (after === -1) return [runs[0][0], runs[runs.length - 1][1]];
    return [runs[after + 1][0], runs[after][1] + 360];
}

/** The longitude interval and the latitudes of each part of a GeoJSON geometry, one entry per part. */
function geometryParts(geom: any, parts: Array<{ lons: number[]; lats: number[] }>): void {
    if (!geom) return;
    const vertices = (coords: any, into: { lons: number[]; lats: number[] }) => {
        if (!Array.isArray(coords)) return;
        if (typeof coords[0] === "number" && typeof coords[1] === "number") {
            into.lons.push(coords[0]);
            into.lats.push(coords[1]);
            return;
        }
        for (const c of coords) vertices(c, into);
    };
    const part = (coords: any) => {
        const into = { lons: [] as number[], lats: [] as number[] };
        vertices(coords, into);
        if (into.lons.length) parts.push(into);
    };
    switch (geom.type) {
        case "MultiPolygon":
        case "MultiLineString":
            for (const member of geom.coordinates ?? []) part(member);
            break;
        case "MultiPoint":
            for (const member of geom.coordinates ?? []) part(member);
            break;
        case "GeometryCollection":
            for (const sub of geom.geometries ?? []) geometryParts(sub, parts);
            break;
        default:
            // Point, LineString, Polygon: one part. Unknown types with coordinates count as one part.
            if (geom.coordinates) part(geom.coordinates);
    }
}

/**
 * The box that holds every plotted result: each point marker and every vertex of each GeoJSON shape
 * (a Feature's geometry, a geometry, or each member of a GeometryCollection), with
 * BOUNDS_PADDING_DEGREES on every side. Longitudes are the covering arc of the parts' own intervals, so
 * `east` can exceed 180 for results either side of the antimeridian; MapLibre's `fitBounds` frames such a
 * box on a flat map. Null when no result carries a coordinate.
 */
export function locationDataBounds(items: LocationData[]): LngLatBoundsLike | null {
    const parts: Array<{ lons: number[]; lats: number[] }> = [];

    for (const item of items) {
        if (item.type === "point" && item.latitude !== undefined && item.longitude !== undefined) {
            parts.push({ lons: [item.longitude], lats: [item.latitude] });
            continue;
        }
        if (item.type === "geojson" && item.geoJson) {
            const geom = item.geoJson?.type === "Feature" ? item.geoJson.geometry : item.geoJson;
            geometryParts(geom, parts);
        }
    }

    if (parts.length === 0) return null;
    const lats = parts.flatMap((part) => part.lats);
    const [west, east] = coveringArc(
        parts.map((part) => [Math.min(...part.lons), Math.max(...part.lons)] as [number, number])
    );
    return [
        [west - BOUNDS_PADDING_DEGREES, Math.min(...lats) - BOUNDS_PADDING_DEGREES],
        [east + BOUNDS_PADDING_DEGREES, Math.max(...lats) + BOUNDS_PADDING_DEGREES],
    ];
}
