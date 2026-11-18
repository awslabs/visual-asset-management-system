/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { geometryParts } from "./mapBounds";

/** Prefix of the id of the fill layer the Search map draws for each GeoJSON result. */
export const FOOTPRINT_FILL_PREFIX = "geojson-fill-";

interface GeoItem {
    id: string;
    type: string;
    geoJson?: any;
}

/** The box of one part of a shape: [west, south, east, north]. */
type Box = [number, number, number, number];

/** The box of each part of a result's GeoJSON (a polygon of a MultiPolygon is one part). */
function partBoxes(geoJson: any): Box[] {
    const geom = geoJson?.type === "Feature" ? geoJson.geometry : geoJson;
    const parts: Array<{ lons: number[]; lats: number[] }> = [];
    geometryParts(geom, parts);
    return parts.map(
        (part) =>
            [
                Math.min(...part.lons),
                Math.min(...part.lats),
                Math.max(...part.lons),
                Math.max(...part.lats),
            ] as Box
    );
}

/**
 * The area, in square degrees, of a shape's parts' boxes. Each part counts on its own, so a footprint
 * split at longitude 180 is two small parts, not one box across the world.
 */
export function footprintArea(geoJson: any): number {
    return partBoxes(geoJson).reduce((sum, [w, s, e, n]) => sum + (e - w) * (n - s), 0);
}

/**
 * The result a click on the map should open, from the footprint fill features under the click (MapLibre
 * lists them topmost first). When several footprints overlap, the smallest is chosen, since a small
 * footprint inside or under a large one could otherwise never be opened; equal areas (a source and its
 * converted COG at the same extent) keep the topmost. Null when the click was on no footprint.
 */
export function pickFootprint<T extends GeoItem>(
    features: any[] | undefined,
    items: T[]
): T | null {
    if (!features || features.length === 0) return null;
    const byId = new Map(items.map((item) => [item.id, item]));
    let best: T | null = null;
    let bestArea = Infinity;
    for (const feature of features) {
        const layerId: string | undefined = feature?.layer?.id;
        if (!layerId || !layerId.startsWith(FOOTPRINT_FILL_PREFIX)) continue;
        const item = byId.get(layerId.slice(FOOTPRINT_FILL_PREFIX.length));
        if (!item || item.type !== "geojson" || !item.geoJson) continue;
        const area = footprintArea(item.geoJson);
        // Strictly smaller only: equal areas keep the one that is higher up.
        if (best === null || area < bestArea) {
            best = item;
            bestArea = area;
        }
    }
    return best;
}

/**
 * Where to open a footprint's popup when nobody clicked on it (the click position is the anchor
 * otherwise): the centre of the box of its largest part, so it lies on the shape, not on the box of the
 * whole of a shape split at longitude 180 (which is at longitude 0, the other side of the world).
 * Null when the shape has no coordinates.
 */
export function footprintFallbackAnchor(geoJson: any): [number, number] | null {
    const boxes = partBoxes(geoJson);
    if (boxes.length === 0) return null;
    let best = boxes[0];
    let bestArea = -1;
    for (const box of boxes) {
        const area = (box[2] - box[0]) * (box[3] - box[1]);
        if (area > bestArea) {
            best = box;
            bestArea = area;
        }
    }
    return [(best[0] + best[2]) / 2, (best[1] + best[3]) / 2];
}
