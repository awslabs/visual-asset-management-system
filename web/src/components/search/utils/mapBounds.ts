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

/** The lowest zoom of a frame, so a world-wide spread shows the whole world. */
export const MIN_FRAME_ZOOM = 0;

/**
 * The longitudes' shortest covering arc as [west, east], with `east` above 180 when the arc crosses the
 * antimeridian. The arc is the full circle minus the widest gap between neighbouring longitudes, so a
 * raster footprint split at 180 (its western part near -180, its eastern near 180) and a point at the
 * Bay Area frame the Pacific between them, not the whole world from -180 to 180.
 */
function shortestLongitudeArc(lons: number[]): [number, number] {
    const sorted = [...new Set(lons.map((lon) => Math.max(-180, Math.min(180, lon))))].sort(
        (a, b) => a - b
    );
    if (sorted.length === 1) return [sorted[0], sorted[0]];
    // The gap that closes the circle, from the last longitude round to the first.
    let widest = sorted[0] + 360 - sorted[sorted.length - 1];
    let start = 0;
    for (let i = 0; i < sorted.length - 1; i++) {
        const gap = sorted[i + 1] - sorted[i];
        if (gap > widest) {
            widest = gap;
            start = i + 1;
        }
    }
    if (start === 0) return [sorted[0], sorted[sorted.length - 1]];
    return [sorted[start], sorted[start - 1] + 360];
}

/**
 * The box that holds every plotted result: each point marker and every vertex of each GeoJSON shape
 * (a Feature's geometry, a geometry, or each member of a GeometryCollection), with
 * BOUNDS_PADDING_DEGREES on every side. Longitudes take the shortest way round the globe, so `east`
 * can exceed 180 for results either side of the antimeridian. Null when no result carries a coordinate.
 */
export function locationDataBounds(items: LocationData[]): LngLatBoundsLike | null {
    const lats: number[] = [];
    const lons: number[] = [];

    // Walk arbitrary nested coordinate arrays from a GeoJSON geometry.
    const collect = (coords: any) => {
        if (!Array.isArray(coords)) return;
        if (typeof coords[0] === "number" && typeof coords[1] === "number") {
            lons.push(coords[0]);
            lats.push(coords[1]);
            return;
        }
        for (const c of coords) collect(c);
    };

    for (const item of items) {
        if (item.type === "point" && item.latitude !== undefined && item.longitude !== undefined) {
            lats.push(item.latitude);
            lons.push(item.longitude);
            continue;
        }
        if (item.type === "geojson" && item.geoJson) {
            const geom = item.geoJson?.type === "Feature" ? item.geoJson.geometry : item.geoJson;
            if (geom?.coordinates) collect(geom.coordinates);
            if (geom?.geometries) {
                for (const sub of geom.geometries) {
                    if (sub?.coordinates) collect(sub.coordinates);
                }
            }
        }
    }

    if (lats.length === 0) return null;
    const [west, east] = shortestLongitudeArc(lons);
    return [
        [west - BOUNDS_PADDING_DEGREES, Math.min(...lats) - BOUNDS_PADDING_DEGREES],
        [east + BOUNDS_PADDING_DEGREES, Math.max(...lats) + BOUNDS_PADDING_DEGREES],
    ];
}

const mercatorY = (lat: number) =>
    Math.log(
        Math.tan(Math.PI / 4 + (Math.max(-85.051129, Math.min(85.051129, lat)) * Math.PI) / 360)
    );

/**
 * The camera (centre and zoom) that shows `bounds` in a view of `width` x `height` px with `padding` px
 * on every side, computed for the flat Web Mercator the tiles and footprints are drawn in. The map's own
 * `fitBounds` is not used because the basemap style may select the globe projection, whose fit gives a
 * view of the wrong place and scale for a box more than a few tens of degrees wide (a world-wide
 * footprint framed West Africa). Null for an unusable box or view.
 */
export function cameraForBounds(
    bounds: LngLatBoundsLike,
    view: { width: number; height: number; padding: number; maxZoom: number; minZoom?: number }
): { center: [number, number]; zoom: number } | null {
    const [[west, south], [east, north]] = bounds as [[number, number], [number, number]];
    if (![west, south, east, north].every(Number.isFinite)) return null;
    if (!(view.width > 0) || !(view.height > 0)) return null;
    // Never let padding consume the view.
    const padX = Math.min(view.padding, view.width / 4);
    const padY = Math.min(view.padding, view.height / 4);
    const lonSpan = Math.max(east - west, 1e-9);
    const worldY = Math.max((mercatorY(north) - mercatorY(south)) / (2 * Math.PI), 1e-12);
    const zoomX = Math.log2(((view.width - 2 * padX) / 512) * (360 / lonSpan));
    const zoomY = Math.log2((view.height - 2 * padY) / 512 / worldY);
    const zoom = Math.max(view.minZoom ?? MIN_FRAME_ZOOM, Math.min(zoomX, zoomY, view.maxZoom));
    const midY = (mercatorY(north) + mercatorY(south)) / 2;
    const lat = (Math.atan(Math.exp(midY)) * 360) / Math.PI - 90;
    let lng = (west + east) / 2;
    while (lng > 180) lng -= 360;
    while (lng < -180) lng += 360;
    return { center: [lng, lat], zoom };
}
