/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the Search map's "Show raster files" filter.
 *
 * The search answer is route-mocked (a TIFF footprint in North America, a point cloud in Europe), so the
 * spec needs no data. The toggle sits on the results-title row, on the right, and is on by default. Off,
 * the TIFF hit leaves the count and the framing follows the one visible hit; on, both return. Skips, with
 * a reason, when the deployment has no Map view (location services off). Non-mutating.
 */

import { test, expect, Page } from "@playwright/test";
import { gotoSearch, searchQueryBox } from "./support/fixtures";
import { isInView, mapCamera } from "./support/mapFixtures";

const ring = (w: number, e: number, s: number, n: number) => [
    [
        [w, s],
        [e, s],
        [e, n],
        [w, n],
        [w, s],
    ],
];

const HITS = [
    {
        _index: "file",
        _id: "e2e-rasterfilter#tif",
        _score: 1,
        _source: {
            str_rectype: "file",
            str_databaseid: "e2e-rasterfilter",
            str_assetid: "asset-1",
            str_assetname: "Asset 1",
            str_key: "/e2e-na.tif",
            str_fileext: "tif",
            num_filesize: 1024,
            geo_MD_location: { type: "Polygon", coordinates: ring(-123, -122, 37, 38) },
        },
    },
    {
        _index: "file",
        _id: "e2e-rasterfilter#laz",
        _score: 1,
        _source: {
            str_rectype: "file",
            str_databaseid: "e2e-rasterfilter",
            str_assetid: "asset-2",
            str_assetname: "Asset 2",
            str_key: "/e2e-eu.laz",
            str_fileext: "laz",
            num_filesize: 1024,
            geo_MD_location: { type: "Point", coordinates: [10, 50] },
        },
    },
];

async function settle(page: Page) {
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

async function openMap(page: Page) {
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
    return settle(page);
}

const toggleOf = (page: Page) => page.getByRole("checkbox", { name: "Show raster files" });

test.describe("Search map: Show raster files filter", () => {
    test("is on by default, hides the raster hit when off, and restores it when on", async ({
        page,
    }) => {
        const all = await openMap(page);
        const toggle = toggleOf(page);
        await expect(toggle).toBeChecked();
        const title = page.getByTestId("map-results-title-row");
        await expect(title).toContainText("2 of 2 results with location data");
        expect(isInView(all, -122.5, 37.5)).toBe(true);
        expect(isInView(all, 10, 50)).toBe(true);

        // Off: the TIFF hit is out of the count and the framing follows the point in Europe.
        await page.getByText("Show raster files", { exact: true }).click();
        await expect(toggle).not.toBeChecked();
        await expect(title).toContainText("1 of 2 results with location data");
        await expect(page.getByTestId("map-raster-hidden-count")).toContainText(
            "(1 raster file hidden)"
        );
        const only = await settle(page);
        if (process.env.E2E_EVIDENCE_DIR) {
            await page.screenshot({
                path: `${process.env.E2E_EVIDENCE_DIR}/search-map-raster-off.png`,
            });
        }
        expect(isInView(only, 10, 50)).toBe(true);
        expect(isInView(only, -122.5, 37.5)).toBe(false);
        expect(only.zoom).toBeGreaterThan(all.zoom);

        // On again: both hits are back in the count and in view.
        await page.getByText("Show raster files", { exact: true }).click();
        await expect(toggle).toBeChecked();
        await expect(title).toContainText("2 of 2 results with location data");
        await expect(page.getByTestId("map-raster-hidden-count")).toHaveCount(0);
        const back = await settle(page);
        if (process.env.E2E_EVIDENCE_DIR) {
            await page.screenshot({
                path: `${process.env.E2E_EVIDENCE_DIR}/search-map-raster-on.png`,
            });
        }
        expect(isInView(back, -122.5, 37.5)).toBe(true);
        expect(isInView(back, 10, 50)).toBe(true);
    });

    test("is operable from the keyboard", async ({ page }) => {
        await openMap(page);
        const toggle = toggleOf(page);
        await toggle.focus();
        await page.keyboard.press("Space");
        await expect(toggle).not.toBeChecked();
        await page.keyboard.press("Space");
        await expect(toggle).toBeChecked();
    });

    for (const width of [390, 820, 1280]) {
        test(`the toggle stays on the title row, or wraps under it, at ${width}px`, async ({
            page,
        }) => {
            // Opened wide (the view switch turns into other controls on a phone), then resized.
            await openMap(page);
            await page.setViewportSize({ width, height: 900 });
            const toggleBox = page.getByTestId("map-raster-filter");
            await expect(toggleBox).toBeVisible();
            const row = (await page.getByTestId("map-results-title-row").boundingBox())!;
            const toggle = (await toggleBox.boundingBox())!;
            const title = (await page
                .getByRole("heading", { name: /results with location data/ })
                .boundingBox())!;
            // Never outside its row or the window.
            expect(toggle.x).toBeGreaterThanOrEqual(row.x - 1);
            expect(toggle.x + toggle.width).toBeLessThanOrEqual(row.x + row.width + 1);
            expect(toggle.x + toggle.width).toBeLessThanOrEqual(width + 1);
            if (width >= 1280) {
                // Room for both: same row, the toggle at the right edge.
                expect(
                    Math.abs(toggle.y + toggle.height / 2 - (title.y + title.height / 2))
                ).toBeLessThan(40);
                expect(row.x + row.width - (toggle.x + toggle.width)).toBeLessThan(8);
            } else {
                // Narrow content area: the toggle may wrap under the title, never over it.
                expect(
                    toggle.y >= title.y + title.height - 2 || toggle.x >= title.x + title.width - 2
                ).toBe(true);
            }
            // Evidence for a review: only when a directory is given.
            if (process.env.E2E_EVIDENCE_DIR) {
                await page.screenshot({
                    path: `${process.env.E2E_EVIDENCE_DIR}/search-row-${width}.png`,
                    clip: { x: 0, y: Math.max(0, row.y - 20), width, height: 120 },
                });
            }
        });
    }
});
