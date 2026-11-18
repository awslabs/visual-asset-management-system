/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the GeoTIFF Map Viewer fills its visualizer frame.
 *
 * The viewer used to choose its own height. In a shorter frame the map was clipped and its zoom control
 * fell outside the frame; in a taller one, or in fullscreen, the frame's striped backdrop showed under
 * the map and behind the layer list. This spec opens a raster in the viewer on the View File page and
 * checks, at a phone, a tablet and a laptop width and in fullscreen, that the map's canvas is exactly its
 * column, the column and the map controls lie inside the frame, the layer list's slider stays in the
 * list, and none of the backdrop's stripe colours is visible.
 *
 * The subject is derived, never named (Rule 1): a .tif found through the API, or the file named by
 * E2E_COG_MAP_FILE (`<databaseId>/<assetId>/<file key>`). The spec skips, with the reason, when the
 * deployment has no Location Service, no .tif, or the file is not a georeferenced raster the viewer
 * draws. Non-mutating (Rule 2).
 */

import { test, expect, Page } from "@playwright/test";
import { chooseViewer, featuresEnabled, findAssetFiles, openAssetFile } from "./support/fixtures";
import { stripePixels } from "./support/mapFixtures";

const WIDTHS = [
    { name: "phone", width: 390, height: 844 },
    { name: "tablet", width: 820, height: 900 },
    { name: "laptop", width: 1280, height: 800 },
];

interface Box {
    x: number;
    y: number;
    w: number;
    h: number;
}

const boxOf = async (page: Page, selector: string): Promise<Box | null> =>
    page.evaluate((sel) => {
        const element = document.querySelector(sel);
        if (!element) return null;
        const r = element.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
    }, selector);

const inside = (inner: Box, outer: Box, slack = 1) =>
    inner.x >= outer.x - slack &&
    inner.y >= outer.y - slack &&
    inner.x + inner.w <= outer.x + outer.w + slack &&
    inner.y + inner.h <= outer.y + outer.h + slack;

/** Asserts the viewer fills its frame, with `where` naming the case in each message. */
async function expectViewerFillsFrame(page: Page, where: string, stacked: boolean): Promise<void> {
    const frame = await boxOf(page, ".visualizer-container");
    const root = await boxOf(page, '[data-testid="cog-map-viewer"]');
    const mapColumn = await boxOf(page, '[data-testid="cog-map-column"]');
    const canvas = await boxOf(page, "canvas.maplibregl-canvas");
    expect(frame && root && mapColumn && canvas, `${where}: viewer parts are mounted`).toBeTruthy();

    // No offset or transform between the canvas and its column, or the column and the frame.
    for (const key of ["x", "y", "w", "h"] as const) {
        expect(
            Math.abs(canvas![key] - mapColumn![key]),
            `${where}: canvas ${key} differs from its column (${JSON.stringify([
                canvas,
                mapColumn,
            ])})`
        ).toBeLessThanOrEqual(1);
    }
    expect(
        Math.abs(root!.x - frame!.x),
        `${where}: viewer is shifted horizontally`
    ).toBeLessThanOrEqual(1);
    expect(
        Math.abs(root!.y - frame!.y),
        `${where}: viewer is shifted vertically`
    ).toBeLessThanOrEqual(1);
    expect(
        Math.abs(root!.w - frame!.w),
        `${where}: viewer is narrower than its frame`
    ).toBeLessThanOrEqual(1);
    expect(inside(mapColumn!, frame!), `${where}: the map extends beyond its frame`).toBe(true);
    if (!stacked) {
        const list = await boxOf(page, '[data-testid="cog-layer-column"]');
        expect(inside(list!, frame!), `${where}: the layer list extends beyond its frame`).toBe(
            true
        );
        expect(root!.h, `${where}: the viewer is taller than its frame`).toBeLessThanOrEqual(
            frame!.h + 1
        );
    }

    // Every map control (zoom buttons, attribution) is drawn inside the frame.
    const controls = await page.evaluate(() =>
        [...document.querySelectorAll(".maplibregl-ctrl-group, .maplibregl-ctrl-attrib")].map(
            (e) => {
                const r = e.getBoundingClientRect();
                return { x: r.x, y: r.y, w: r.width, h: r.height };
            }
        )
    );
    expect(controls.length, `${where}: the map has controls`).toBeGreaterThan(0);
    for (const control of controls) {
        expect(inside(control, frame!), `${where}: a map control is outside the frame`).toBe(true);
    }

    // The opacity slider stays inside the layer list, whose own background is painted.
    const slider = await page.evaluate(() => {
        const list = document.querySelector('[data-testid="cog-layer-column"]');
        const input = list?.querySelector('input[type="range"], [role="slider"]');
        if (!list || !input) return null;
        const l = list.getBoundingClientRect();
        const s = input.getBoundingClientRect();
        return {
            ok: s.left >= l.left - 1 && s.right <= l.right + 1,
            bg: getComputedStyle(list).backgroundColor,
        };
    });
    expect(slider, `${where}: the layer list shows an opacity slider`).not.toBeNull();
    expect(slider!.ok, `${where}: the opacity slider is clipped`).toBe(true);
    expect(slider!.bg, `${where}: the layer list has no background`).not.toBe("rgba(0, 0, 0, 0)");

    // None of the frame's stripe colours shows anywhere in the frame.
    const png = await page.screenshot({
        clip: {
            x: frame!.x,
            y: frame!.y,
            width: Math.min(frame!.w, 2000),
            height: Math.min(frame!.h, 1400),
        },
    });
    expect(await stripePixels(page, png), `${where}: the frame's stripes show`).toBe(0);
}

test.describe("GeoTIFF Map Viewer layout", () => {
    test("fills its frame at phone, tablet and laptop widths and in fullscreen", async ({
        page,
    }) => {
        await page.goto("/", { waitUntil: "domcontentloaded" });
        test.skip(
            !(await featuresEnabled(page)).includes("LOCATIONSERVICES"),
            "Location Service is off on this deployment, so the map viewer is not offered"
        );

        let ref: { databaseId: string; assetId: string; key: string } | undefined;
        const named = process.env.E2E_COG_MAP_FILE;
        if (named) {
            const [databaseId, assetId, ...rest] = named.split("/");
            ref = { databaseId, assetId, key: `/${rest.join("/").replace(/^\/+/, "")}` };
        } else {
            const found = await findAssetFiles(page, [".tif"], { maxBytes: 200 * 1024 * 1024 });
            ref = found?.get(".tif");
        }
        test.skip(!ref, "No .tif file is readable in this environment");

        await page.setViewportSize({ width: 1280, height: 800 });
        await openAssetFile(page, ref!.databaseId, ref!.assetId, ref!.key.replace(/^\/+/, ""));
        await page
            .getByText("Select viewer (required)")
            .first()
            .waitFor({ timeout: 30_000 })
            .catch(() => undefined);
        const offered = await page
            .getByLabel(/select viewer/i)
            .first()
            .click()
            .then(() => page.getByRole("option", { name: /GeoTIFF Map/i }).count())
            .catch(() => 0);
        await page.keyboard.press("Escape").catch(() => undefined);
        test.skip(offered === 0, "The GeoTIFF Map Viewer is not offered for this file");
        await chooseViewer(page, /GeoTIFF Map/i);

        // A georeferenced raster adds a row; a refused file adds a notice instead.
        const row = page.getByTestId("raster-layer-row").first();
        const notice = page.getByTestId("raster-notice").first();
        await expect(row.or(notice)).toBeVisible({ timeout: 60_000 });
        test.skip(
            (await row.count()) === 0,
            "The file is not a raster the viewer draws (no georeference or too large): " +
                ((await notice.innerText().catch(() => "")) || "no reason shown")
        );
        // Let the first tiles draw.
        await page.waitForTimeout(3_000);

        for (const size of WIDTHS) {
            await page.setViewportSize({ width: size.width, height: size.height });
            await page.waitForTimeout(1_200);
            await expectViewerFillsFrame(
                page,
                `${size.name} ${size.width}px`,
                size.width < 792 + 340
            );
        }

        // Fullscreen: the frame becomes the whole screen and the viewer follows it.
        await page.setViewportSize({ width: 1280, height: 800 });
        await page.waitForTimeout(800);
        const fullscreen = page.getByRole("button", { name: /full ?screen/i }).first();
        const toggle = (await fullscreen.count())
            ? fullscreen
            : page.locator(".visualizer-footer button").last();
        await toggle.click();
        await page.waitForTimeout(2_000);
        await expectViewerFillsFrame(page, "fullscreen", false);
        await page.keyboard.press("Escape");
    });
});
