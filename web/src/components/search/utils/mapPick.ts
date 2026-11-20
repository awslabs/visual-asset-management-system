/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Prefix of the id of the fill layer the Search map draws for each GeoJSON result. */
export const FOOTPRINT_FILL_PREFIX = "geojson-fill-";

interface GeoItem {
    id: string;
    type: string;
    geoJson?: any;
}

/** The longitudes and latitudes of each part of a GeoJSON geometry, one entry per part. */
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

/** Web Mercator y (in radians of x) for a latitude, clamped to the map's limit. */
const mercatorY = (lat: number) =>
    Math.log(
        Math.tan(Math.PI / 4 + (Math.max(-85.051129, Math.min(85.051129, lat)) * Math.PI) / 360)
    );

/**
 * Shoelace area of a ring in Web Mercator, so it matches what the user sees on the flat map. A ring that
 * is not an array, or holds a vertex that is not a pair of finite numbers, is unreadable: Infinity.
 */
function ringArea(ring: any): number {
    if (!Array.isArray(ring)) return Infinity;
    if (ring.length < 3) return 0;
    for (const v of ring) {
        if (!Array.isArray(v) || !Number.isFinite(v[0]) || !Number.isFinite(v[1])) return Infinity;
    }
    let twice = 0;
    for (let i = 0; i < ring.length; i++) {
        const [lon1, lat1] = ring[i];
        const [lon2, lat2] = ring[(i + 1) % ring.length];
        const x1 = (lon1 * Math.PI) / 180;
        const x2 = (lon2 * Math.PI) / 180;
        twice += x1 * mercatorY(lat2) - x2 * mercatorY(lat1);
    }
    return Math.abs(twice) / 2;
}

/** The polygons (each a list of rings, shell first) of a geometry, collections flattened. */
function polygonsOf(geom: any, polygons: any[]): void {
    if (!geom) return;
    if (geom.type === "Polygon") polygons.push(geom.coordinates);
    else if (geom.type === "MultiPolygon") {
        if (Array.isArray(geom.coordinates)) for (const p of geom.coordinates) polygons.push(p);
        else polygons.push(undefined);
    } else if (geom.type === "GeometryCollection") {
        if (Array.isArray(geom.geometries))
            for (const g of geom.geometries) polygonsOf(g, polygons);
    }
}

/**
 * A polygon's area: its shell minus its holes, never below 0 (a malformed hole cannot go negative).
 * Infinity when the polygon, its shell or a hole is unreadable.
 */
function polygonArea(rings: any): number {
    if (!Array.isArray(rings)) return Infinity;
    const [shell, ...holes] = rings;
    const shellArea = ringArea(shell);
    let holeArea = 0;
    for (const hole of holes) holeArea += ringArea(hole);
    if (!Number.isFinite(shellArea) || !Number.isFinite(holeArea)) return Infinity;
    return Math.max(0, shellArea - holeArea);
}

/**
 * The area of a footprint as drawn on the flat map: the sum of its polygons' areas in Web Mercator
 * (shoelace), each shell minus its holes. A rotated or skewed footprint counts its real area, not its
 * box's, and a footprint split at longitude 180 is two small parts, not one shape across the world. A
 * shape with an unreadable coordinate counts as infinitely large, so it never wins a pick over a
 * readable one.
 */
export function footprintArea(geoJson: any): number {
    const geom = geoJson?.type === "Feature" ? geoJson.geometry : geoJson;
    const polygons: any[] = [];
    polygonsOf(geom, polygons);
    const area = polygons.reduce((sum, rings) => sum + polygonArea(rings), 0);
    return Number.isFinite(area) ? area : Infinity;
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
