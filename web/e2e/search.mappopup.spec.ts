/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: on the Search page's Map view, a click on a file's footprint opens that file's popup where
 * the user clicked.
 *
 * The popup used to open at the centre of the box of the whole footprint: far from the click for a large
 * or L-shaped footprint, and at longitude 0, the other side of the world, for a footprint split at
 * longitude 180 (a raster that crosses the antimeridian). Of several overlapping footprints the smallest
 * is the one that opens.
 *
 * The search answer is route-mocked, so the spec needs no data. The click is made with the real mouse at
 * the pixel of a chosen longitude and latitude (flat Web Mercator maths from the map's camera), and the
 * popup's own anchor is read from its DOM transform. Skips, with a reason, when the deployment has no
 * Map view. Non-mutating.
 */

import { test, expect, Page } from "@playwright/test";
import { gotoSearch, searchQueryBox } from "./support/fixtures";
import { mapCamera, mapJumpTo, pixelOf, popupAnchorPx } from "./support/mapPopupFixtures";

const ring = (w: number, e: number, s: number, n: number) => [
    [
        [w, s],
        [e, s],
        [e, n],
        [w, n],
        [w, s],
    ],
];

const fileHit = (name: string, geo: any, index: number) => ({
    _index: "file",
    _id: `e2e-mappopup#${name}`,
    _score: 1,
    _source: {
        str_rectype: "file",
        str_databaseid: "e2e-mappopup",
        str_assetid: `asset-${index}`,
        str_assetname: `Asset ${index}`,
        str_key: `/${name}.tif`,
        str_fileext: "tif",
        num_filesize: 2048,
        geo_MD_location: geo,
    },
});

const HITS = [
    // A large footprint, with a small one lying over part of it.
    fileHit("large", { type: "Polygon", coordinates: ring(-130, -110, 30, 45) }, 1),
    fileHit("small", { type: "Polygon", coordinates: ring(-122, -120, 36, 38) }, 2),
    // Split at longitude 180, as a raster that crosses the antimeridian is stored.
    fileHit(
        "split",
        { type: "MultiPolygon", coordinates: [ring(-180, -178, -2, 2), ring(178, 180, -2, 2)] },
        3
    ),
    // L-shaped: a long arm along the south and a tall arm up the west, so the centre of its box is empty.
    fileHit(
        "ell",
        { type: "MultiPolygon", coordinates: [ring(10, 30, 10, 12), ring(10, 12, 12, 30)] },
        4
    ),
];

async function openMap(page: Page): Promise<void> {
    await page.route(/\/search$/, async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                hits: { total: { value: HITS.length, relation: "eq" }, hits: HITS },
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
    await expect.poll(async () => (await mapCamera(page)) !== null, { timeout: 30_000 }).toBe(true);
    // Let the framing animation finish before the camera is moved by the spec.
    await page.waitForTimeout(2_500);
}

/** Clicks the map where (lon, lat) is, after centring there at `zoom`; returns the click pixel in the canvas. */
async function clickAt(
    page: Page,
    lon: number,
    lat: number,
    zoom: number,
    centre?: [number, number]
): Promise<{ x: number; y: number }> {
    expect(await mapJumpTo(page, ...(centre ?? [lon, lat]), zoom)).toBe(true);
    await page.waitForTimeout(800);
    const camera = (await mapCamera(page))!;
    const at = pixelOf(camera, lon, lat);
    const box = (await page.locator("canvas.maplibregl-canvas").first().boundingBox())!;
    await page.mouse.click(box.x + at.x, box.y + at.y);
    return at;
}

async function expectPopupAt(page: Page, click: { x: number; y: number }, what: string) {
    await expect(page.locator(".maplibregl-popup")).toBeVisible({ timeout: 10_000 });
    const anchor = await popupAnchorPx(page);
    expect(anchor, `${what}: the popup has no readable anchor`).not.toBeNull();
    expect(
        Math.hypot(anchor!.x - click.x, anchor!.y - click.y),
        `${what}: popup anchor ${JSON.stringify(anchor)} is not at the click ${JSON.stringify(
            click
        )}`
    ).toBeLessThanOrEqual(3);
}

async function closePopup(page: Page): Promise<void> {
    await page.locator(".maplibregl-popup-close-button").first().click();
    await expect(page.locator(".maplibregl-popup")).toHaveCount(0);
}

test.describe("Search map footprint popup", () => {
    test("opens at the click on a large footprint, off its centre", async ({ page }) => {
        await openMap(page);
        // Near the south-west corner of the large footprint (its box centre is (-120, 37.5)).
        const click = await clickAt(page, -128, 32, 5);
        await expectPopupAt(page, click, "large footprint");
        await expect(page.getByText("/large.tif")).toBeVisible();
    });

    test("opens at the click on a footprint split at longitude 180, on either side", async ({
        page,
    }) => {
        await openMap(page);
        const east = await clickAt(page, 179, 0, 5);
        await expectPopupAt(page, east, "split footprint, eastern part");
        await expect(page.getByText("/split.tif")).toBeVisible();
        await closePopup(page);
        const west = await clickAt(page, -179, 0, 5);
        await expectPopupAt(page, west, "split footprint, western part");
        await expect(page.getByText("/split.tif")).toBeVisible();
    });

    test("opens at the click on both arms of an L-shaped footprint", async ({ page }) => {
        await openMap(page);
        const long = await clickAt(page, 28, 11, 5);
        await expectPopupAt(page, long, "L-shaped footprint, long arm");
        await closePopup(page);
        const tall = await clickAt(page, 11, 28, 5);
        await expectPopupAt(page, tall, "L-shaped footprint, tall arm");
        await expect(page.getByText("/ell.tif")).toBeVisible();
    });

    test("opens the smaller of two overlapping footprints, at the click", async ({ page }) => {
        await openMap(page);
        const click = await clickAt(page, -121, 37, 6);
        await expectPopupAt(page, click, "overlapping footprints");
        await expect(page.getByText("/small.tif")).toBeVisible();
        await closePopup(page);
        // Outside the small one, the large footprint is what is under the click.
        const outside = await clickAt(page, -127, 33, 6);
        await expectPopupAt(page, outside, "large footprint beside the small one");
        await expect(page.getByText("/large.tif")).toBeVisible();
    });
});
