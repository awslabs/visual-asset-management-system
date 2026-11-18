/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { LngLatBoundsLike } from "maplibre-gl";
import type { LocationData } from "./locationUtils";

/** Degrees added on every side, so a single point or a straight line still has an area to frame. */
export const BOUNDS_PADDING_DEGREES = 0.01;

/** The `fitBounds` options of the Map view: room around the pins, and no street-level zoom on one point. */
export const FIT_BOUNDS_OPTIONS = { padding: 40, duration: 1000, maxZoom: 14 };

/**
 * The box that holds every plotted result: each point marker and every vertex of each GeoJSON shape
 * (a Feature's geometry, a geometry, or each member of a GeometryCollection), with
 * BOUNDS_PADDING_DEGREES on every side. Null when no result carries a coordinate.
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
    return [
        [Math.min(...lons) - BOUNDS_PADDING_DEGREES, Math.min(...lats) - BOUNDS_PADDING_DEGREES],
        [Math.max(...lons) + BOUNDS_PADDING_DEGREES, Math.max(...lats) + BOUNDS_PADDING_DEGREES],
    ];
}
