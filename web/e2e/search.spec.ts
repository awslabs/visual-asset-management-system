/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec for the Assets and Files search page layout.
 *
 * The filter sidebar and the results share a resizable splitter. On a phone the panes stack and the
 * separator is dropped, so the results are on screen instead of pushed out by a sidebar that opens at
 * 400 px or the saved width. From xs up the separator still resizes by keyboard; the test steps it
 * one way and back, so the saved sidebar width ends where it started.
 *
 * Data-independent (Rule 1): asserts the layout whether the search returns rows or its empty state.
 */

import { test, expect, type Page } from "@playwright/test";
import { expectNoPageOverflowX, VIEWPORTS } from "./support/fixtures";

/** The results pane of the search splitter (the sidebar is the left pane). */
function resultsPane(page: Page) {
    return page.locator(".resizable-right-panel").first();
}

async function openSearch(page: Page): Promise<void> {
    await page.goto("/#/search", { waitUntil: "domcontentloaded" });
    await expect(resultsPane(page)).toBeVisible({ timeout: 60_000 });
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

test.describe("Search page layout — phone", () => {
    test.use({ viewport: VIEWPORTS.phone });

    test("stacks the filters above the results, which fill the width", async ({ page }) => {
        await openSearch(page);
        await expectNoPageOverflowX(page, "search page");
        await expect(page.getByRole("separator", { name: "Resize panels" })).toHaveCount(0);

        const viewport = page.viewportSize()!;
        const box = await resultsPane(page).boundingBox();
        expect(box, "results pane has no box").not.toBeNull();
        // The pane spans the content width (viewport less the page padding), not a sliver.
        expect(box!.width).toBeGreaterThan(viewport.width * 0.75);
        expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 0.5);
    });
});

test.describe("Search page layout — desktop", () => {
    test.use({ viewport: VIEWPORTS.desktop });

    test("keeps the sidebar beside the results with a keyboard-resizable separator", async ({
        page,
    }) => {
        await openSearch(page);
        await expectNoPageOverflowX(page, "search page");
        const separator = page.getByRole("separator", { name: "Resize panels" }).first();
        await expect(separator).toBeVisible();
        const before = Number(await separator.getAttribute("aria-valuenow"));
        const max = Number(await separator.getAttribute("aria-valuemax"));
        // Step toward the side with room, then back, so the saved width is unchanged.
        const [there, back] =
            before + 20 <= max ? ["ArrowRight", "ArrowLeft"] : ["ArrowLeft", "ArrowRight"];
        await separator.focus();
        await page.keyboard.press(there);
        await expect(separator).not.toHaveAttribute("aria-valuenow", String(before));
        await page.keyboard.press(back);
        await expect(separator).toHaveAttribute("aria-valuenow", String(before));
    });
});
