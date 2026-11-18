/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Helpers for specs that look at a MapLibre map in the live app: its camera, whether a coordinate is in
 * view, and whether a frame shows the viewer backdrop's stripes. The live bundle exposes no map
 * global, so the map is found through the React fiber tree behind its `.maplibregl-map` element.
 */

import { Page } from "@playwright/test";

/** The camera of the page's (first) map and the size of its canvas, or null when no map is mounted. */
export interface MapCamera {
    center: { lng: number; lat: number };
    zoom: number;
    canvas: { width: number; height: number };
}

const FIND_MAP = `(() => {
  const el = document.querySelector('.maplibregl-map'); if (!el) return null;
  const key = Object.keys(el).find((k) => k.startsWith('__reactFiber$'));
  let f = key ? el[key] : null;
  const look = (v) => v && typeof v === 'object' && (typeof v.getCenter === 'function' ? v
    : typeof v.getMap === 'function' ? v.getMap()
    : v.current && typeof v.current.getMap === 'function' ? v.current.getMap() : null);
  for (let n = 0; f && n < 200; f = f.return, n++) {
    let s = f.memoizedState; let g = 0;
    while (s && g++ < 40) { const m = look(s.memoizedState) || look(s.memoizedState && s.memoizedState.current); if (m) return m; s = s.next; }
    const p = f.memoizedProps && (look(f.memoizedProps.mapRef) || look(f.memoizedProps.map)); if (p) return p;
  }
  return null; })()`;

/** The map's camera, or null when it is not mounted or its instance cannot be found. */
export async function mapCamera(page: Page): Promise<MapCamera | null> {
    return page.evaluate(`(() => { const m = ${FIND_MAP}; if (!m) return null;
      const c = m.getCenter(); const cv = m.getCanvas();
      return { center: { lng: c.lng, lat: c.lat }, zoom: m.getZoom(), canvas: { width: cv.clientWidth, height: cv.clientHeight } }; })()`);
}

/** Moves the page's (first) map to a centre and zoom without animation. False when no map is mounted. */
export async function mapJumpTo(
    page: Page,
    lon: number,
    lat: number,
    zoom: number
): Promise<boolean> {
    return page.evaluate(`(() => { const m = ${FIND_MAP}; if (!m) return false;
      m.jumpTo({ center: [${lon}, ${lat}], zoom: ${zoom} }); return true; })()`);
}

/**
 * Where the open popup is anchored, in CSS pixels from the top-left corner of the map's canvas, or null
 * when none is open. MapLibre places a popup with `translate(x px, y px)`, the pixel of its coordinate
 * plus the offset; the Search map gives a footprint's popup no offset, so this is the coordinate itself.
 */
export async function popupAnchorPx(page: Page): Promise<{ x: number; y: number } | null> {
    return page.evaluate(() => {
        const popup = document.querySelector(".maplibregl-popup") as HTMLElement | null;
        const match =
            popup && /translate\(([-\d.]+)px,\s*([-\d.]+)px\)\s*$/.exec(popup.style.transform);
        return match ? { x: Number(match[1]), y: Number(match[2]) } : null;
    });
}

/**
 * Where a coordinate falls in the map's canvas, in CSS pixels from its top-left corner. Computed from
 * the camera for flat Web Mercator, so the answer does not depend on the projection the basemap style
 * selects (the globe projection's own `project` returns positions for points on the far side).
 */
export function pixelOf(camera: MapCamera, lon: number, lat: number): { x: number; y: number } {
    const mercY = (deg: number) =>
        Math.log(Math.tan(Math.PI / 4 + (deg * Math.PI) / 360)) / (2 * Math.PI);
    const scale = 512 * 2 ** camera.zoom;
    let dLon = lon - camera.center.lng;
    while (dLon > 180) dLon -= 360;
    while (dLon < -180) dLon += 360;
    return {
        x: camera.canvas.width / 2 + (dLon / 360) * scale,
        y: camera.canvas.height / 2 - (mercY(lat) - mercY(camera.center.lat)) * scale,
    };
}

/** Whether a coordinate is inside the canvas, `margin` pixels in from every edge. */
export function isInView(camera: MapCamera, lon: number, lat: number, margin = 0): boolean {
    const { x, y } = pixelOf(camera, lon, lat);
    return (
        x >= margin &&
        y >= margin &&
        x <= camera.canvas.width - margin &&
        y <= camera.canvas.height - margin
    );
}

/**
 * How many pixels of a PNG screenshot are one of the two stripe colours of the visualizer frame's
 * backdrop (`.visualizer-container`: #606dbc and #465298). A viewer that fills its frame and paints
 * its own background shows none.
 */
export async function stripePixels(page: Page, png: Buffer): Promise<number> {
    return page.evaluate(async (b64: string) => {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const bmp = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
        const canvas = new OffscreenCanvas(bmp.width, bmp.height);
        const ctx = canvas.getContext("2d")!;
        ctx.drawImage(bmp, 0, 0);
        const d = ctx.getImageData(0, 0, bmp.width, bmp.height).data;
        const near = (i: number, r: number, g: number, b: number) =>
            Math.abs(d[i] - r) < 4 && Math.abs(d[i + 1] - g) < 4 && Math.abs(d[i + 2] - b) < 4;
        let n = 0;
        for (let i = 0; i < d.length; i += 4) {
            if (near(i, 0x60, 0x6d, 0xbc) || near(i, 0x46, 0x52, 0x98)) n++;
        }
        return n;
    }, png.toString("base64"));
}
