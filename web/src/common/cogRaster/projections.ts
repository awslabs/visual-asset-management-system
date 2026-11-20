/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import proj4 from "proj4";

/**
 * The coordinate reference systems the placed-image path can place on the map. The definitions are
 * bundled here: nothing is looked up on the network (no epsg.io), so a code that is not listed is
 * "unsupported" and the viewer says so. Adding a system means adding one entry below.
 *
 * Definitions use datum shifts only where the datum differs from WGS84 by more than a metre (the
 * British National Grid); a placed image is approximate by design, so a sub-metre shift is not worth
 * extra definition text.
 */

/** Web Mercator, and the aliases older tools write for it. */
export const WEB_MERCATOR_CODES = [3857, 900913, 102100, 102113, 3785];

const FIXED_DEFINITIONS: Record<number, string> = {
    4326: "+proj=longlat +datum=WGS84 +no_defs",
    3857: "+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +no_defs",
    4269: "+proj=longlat +datum=NAD83 +no_defs",
    4258: "+proj=longlat +ellps=GRS80 +no_defs",
    // ETRS89 / LAEA Europe
    3035: "+proj=laea +lat_0=52 +lon_0=10 +x_0=4321000 +y_0=3210000 +ellps=GRS80 +units=m +no_defs",
    // NAD83 / Conus Albers
    5070: "+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs",
    // RGF93 / Lambert-93
    2154: "+proj=lcc +lat_0=46.5 +lon_0=3 +lat_1=49 +lat_2=44 +x_0=700000 +y_0=6600000 +ellps=GRS80 +units=m +no_defs",
    // OSGB36 / British National Grid
    27700: "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy +towgs84=446.448,-125.157,542.06,0.15,0.247,0.842,-20.489 +units=m +no_defs",
    // WGS 84 / NSIDC Sea Ice Polar Stereographic North
    3413: "+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs",
    // WGS 84 / Antarctic Polar Stereographic
    3031: "+proj=stere +lat_0=-90 +lat_ts=-71 +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs",
};

/** UTM families: WGS84 326xx / 327xx, NAD83 269xx (zones 1-23), ETRS89 258xx (zones 28-37). */
function utmDefinition(code: number): string | null {
    let zone: number | null = null;
    let datum = "WGS84";
    let south = false;
    if (code >= 32601 && code <= 32660) {
        zone = code - 32600;
    } else if (code >= 32701 && code <= 32760) {
        zone = code - 32700;
        south = true;
    } else if (code >= 26901 && code <= 26923) {
        zone = code - 26900;
        datum = "NAD83";
    } else if (code >= 25828 && code <= 25837) {
        zone = code - 25800;
        datum = "ETRS89";
    }
    if (zone === null) return null;
    const ellps = datum === "WGS84" ? "+datum=WGS84" : "+ellps=GRS80";
    return `+proj=utm +zone=${zone}${south ? " +south" : ""} ${ellps} +units=m +no_defs`;
}

/** "EPSG:32633", "epsg:4326" or "32633" to a number; null when it is not an EPSG code. */
export function parseEpsgCode(crs: string | number | null | undefined): number | null {
    if (crs === null || crs === undefined) return null;
    if (typeof crs === "number") return Number.isInteger(crs) && crs > 0 ? crs : null;
    const match = /^\s*(?:epsg\s*:\s*)?(\d{3,6})\s*$/i.exec(crs);
    return match ? Number(match[1]) : null;
}

export function isWebMercator(crs: string | number | null | undefined): boolean {
    const code = parseEpsgCode(crs);
    return code !== null && WEB_MERCATOR_CODES.includes(code);
}

/** The proj4 definition for a bundled EPSG code, or null when the code is not bundled. */
export function projectionDefinition(code: number): string | null {
    if (WEB_MERCATOR_CODES.includes(code)) return FIXED_DEFINITIONS[3857];
    return FIXED_DEFINITIONS[code] ?? utmDefinition(code);
}

export function isSupportedCrs(crs: string | number | null | undefined): boolean {
    const code = parseEpsgCode(crs);
    return code !== null && projectionDefinition(code) !== null;
}

/** Converts one x/y position in a bundled CRS to [longitude, latitude]; null when it does not project. */
export function toLonLat(crs: string | number, x: number, y: number): [number, number] | null {
    const code = parseEpsgCode(crs);
    const definition = code === null ? null : projectionDefinition(code);
    if (!definition) return null;
    try {
        const [lon, lat] = proj4(definition, FIXED_DEFINITIONS[4326], [x, y]);
        if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lat) > 90) return null;
        return [lon, lat];
    } catch {
        return null;
    }
}

/**
 * The four corners of a CRS-space box as [lon, lat] in MapLibre image-source order: top-left,
 * top-right, bottom-right, bottom-left. Null when the CRS is not bundled or a corner does not project.
 */
export function cornersToLonLat(
    crs: string | number,
    box: [number, number, number, number]
): [[number, number], [number, number], [number, number], [number, number]] | null {
    const [minX, minY, maxX, maxY] = box;
    const tl = toLonLat(crs, minX, maxY);
    const tr = toLonLat(crs, maxX, maxY);
    const br = toLonLat(crs, maxX, minY);
    const bl = toLonLat(crs, minX, minY);
    if (!tl || !tr || !br || !bl) return null;
    // Keep the quad continuous across the antimeridian: shift each corner's longitude by whole turns so
    // it sits within 180 degrees of the first corner.
    const unwrap = (p: [number, number]): [number, number] => {
        let lon = p[0];
        while (lon - tl[0] > 180) lon -= 360;
        while (lon - tl[0] < -180) lon += 360;
        return [lon, p[1]];
    };
    return [tl, unwrap(tr), unwrap(br), unwrap(bl)];
}
