/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Helpers for specs that look at a MapLibre map in the live app: its camera and whether a coordinate is
 * in view. The live bundle exposes no map global, so the map is found through the React fiber tree
 * behind its `.maplibregl-map` element.
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
