/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the Search page's Map view frames every located file hit.
 *
 * The search answer is route-mocked, so the spec needs no data and no raster on the deployment: it
 * supplies hits on several continents (a polygon footprint, a point, a raster-bearing .tif footprint)
 * and a footprint split at longitude 180 (a raster that crosses the antimeridian is stored as two
 * parts). Before the fix such a spread framed the middle of the world, an empty area, because the
 * basemap style's globe projection made the map's own fit-to-bounds return the wrong view.
 *
 * The map is read through its camera and checked with flat Web Mercator maths, so the assertion does
 * not depend on the projection. Skips, with a reason, when the deployment has no Map view (location
 * services off). Non-mutating: a mocked search only reads.
 */

import { test, expect, Page } from "@playwright/test";
import { gotoSearch, searchQueryBox } from "./support/fixtures";
import { MapCamera, isInView, mapCamera } from "./support/mapFixtures";

const ring = (w: number, e: number, s: number, n: number) => [
    [
        [w, s],
        [e, s],
        [e, n],
        [w, n],
        [w, s],
    ],
];

interface Probe {
    name: string;
    key: string;
    geo: any;
    /** Coordinates that must be inside the viewport. */
    inView: Array<[number, number]>;
}

const PROBES: Probe[] = [
    {
        name: "a polygon footprint in North America",
        key: "/e2e-na.tif",
        geo: { type: "Polygon", coordinates: ring(-123, -122, 37, 38) },
        inView: [
            [-123, 37],
            [-122, 38],
        ],
    },
    {
        name: "a point in Europe",
        key: "/e2e-eu.laz",
        geo: { type: "Point", coordinates: [10, 50] },
        inView: [[10, 50]],
    },
    {
        name: "a raster-bearing footprint in Asia",
        key: "/e2e-as.cog.tif",
        geo: { type: "Polygon", coordinates: ring(139, 140, 35, 36) },
        inView: [
            [139, 35],
            [140, 36],
        ],
    },
    {
        name: "a footprint split at longitude 180",
        key: "/e2e-split.tif",
        geo: {
            type: "MultiPolygon",
            coordinates: [ring(-180, -179, -1, 1), ring(179, 180, -1, 1)],
        },
        inView: [
            [179, -1],
            [-179, 1],
            [180, 0],
        ],
    },
];

const hitOf = (probe: Probe, index: number) => ({
    _index: "file",
    _id: `e2e-mapframing#${index}`,
    _score: 1,
    _source: {
        str_rectype: "file",
        str_databaseid: "e2e-mapframing",
        str_assetid: `asset-${index}`,
        str_assetname: `Asset ${index}`,
        str_key: probe.key,
        str_fileext: probe.key.split(".").pop(),
        num_filesize: 1024,
        geo_MD_location: probe.geo,
    },
});

async function openMapWithHits(page: Page, probes: Probe[]): Promise<MapCamera> {
    await page.route(/\/search$/, async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        const hits = probes.map(hitOf);
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                hits: { total: { value: hits.length, relation: "eq" }, hits },
                aggregations: {},
            }),
        });
    });
    await gotoSearch(page);
    const files = page.getByRole("button", { name: "Files", exact: true });
    const map = page.getByRole("button", { name: "Map", exact: true });
    test.skip(
        (await files.count()) === 0 || (await map.count()) === 0,
        "No Map view on this deployment (location services are off)"
    );
    await files.click();
    await map.click();
    await searchQueryBox(page).fill("e2e");
    await page.getByRole("button", { name: "Search", exact: true }).last().click();
    await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible({ timeout: 60_000 });
    // The camera eases for a second after the results arrive; wait for it to settle.
    let previous = "";
    await expect
        .poll(
            async () => {
                const camera = await mapCamera(page);
                const now = JSON.stringify(camera && [camera.center, camera.zoom]);
                const settled = camera !== null && now === previous;
                previous = now;
                return settled;
            },
            { timeout: 30_000, intervals: [700] }
        )
        .toBe(true);
    return (await mapCamera(page))!;
}

test.describe("Search map framing", () => {
    test("the viewport holds every located file hit, across continents and the antimeridian", async ({
        page,
    }) => {
        const camera = await openMapWithHits(page, PROBES);
        for (const probe of PROBES) {
            for (const [lon, lat] of probe.inView) {
                expect(
                    isInView(camera, lon, lat),
                    `${
                        probe.name
                    } (${lon}, ${lat}) is outside the viewport; camera ${JSON.stringify(camera)}`
                ).toBe(true);
            }
        }
    });

    test("one wide footprint (a latitude-cropped global raster) frames the world, not a sliver", async ({
        page,
    }) => {
        const global: Probe = {
            name: "a global raster footprint",
            key: "/e2e-global.tif",
            geo: { type: "Polygon", coordinates: ring(-180, 180, 10, 20) },
            inView: [
                [-170, 10],
                [170, 20],
                [0, 15],
            ],
        };
        const camera = await openMapWithHits(page, [global, PROBES[0]]);
        for (const [lon, lat] of global.inView) {
            expect(isInView(camera, lon, lat), `(${lon}, ${lat}) is outside the viewport`).toBe(
                true
            );
        }
        // The map cannot zoom out past the world filling its height, so the very edges (+-180) may sit a
        // few pixels outside a window wider than it is tall; everything else of the world is in view.
        expect(camera.zoom).toBeLessThan(1);
    });

    test("a single located hit is framed, not the world", async ({ page }) => {
        const camera = await openMapWithHits(page, [PROBES[0]]);
        expect(isInView(camera, -122.5, 37.5, 20)).toBe(true);
        // A one-degree footprint is framed closely, well past a whole-world view.
        expect(camera.zoom).toBeGreaterThan(5);
    });
});
