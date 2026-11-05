/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, type Page } from "@playwright/test";
import {
    collectPageErrors,
    expectNoPageOverflowX,
    expectSearchRendered,
    gotoSearch,
    readFeaturesEnabled,
    searchTabs,
    VIEWPORTS,
} from "./support/fixtures";

/**
 * Search page — permanent smoke coverage. The page is a strip of provider tabs: the asset list is
 * always present, and the unified search tab appears when the deployment enables OpenSearch or
 * vector search. Environment-agnostic: the expected tab set is derived from the feature switches
 * the app itself cached after login, and nothing here depends on particular assets existing.
 *
 * Layout: on the unified tab the filter sidebar and the results share a resizable splitter. On a
 * phone the panes stack and the separator is dropped, so the results are on screen instead of
 * pushed out by a sidebar that opens at 400 px or the saved width. From xs up the separator still
 * resizes by keyboard; the test steps it one way and back, so the saved sidebar width ends where it
 * started. Data-independent (Rule 1): asserts the layout whether the search returns rows or its
 * empty state.
 */

/** True when the deployment offers the unified search tab (either engine on). */
const unifiedSearchOffered = (features: string[]) =>
    features.includes("VECTORSEARCH") || !features.includes("NOOPENSEARCH");

test.describe("Search page", () => {
    test("renders the tab strip and a heading without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        await gotoSearch(page);
        await expect(searchTabs(page)).toBeVisible();
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("always offers the asset-list tab", async ({ page }) => {
        await gotoSearch(page);
        await expect(searchTabs(page).getByRole("tab", { name: /list$/i })).toHaveCount(1);
    });

    test("offers the unified search tab exactly when a search engine is enabled", async ({
        page,
    }) => {
        await gotoSearch(page);
        const features = await readFeaturesEnabled(page);
        // `null` = nothing cached yet; `[]` = cached with no switches (OpenSearch on, nothing else),
        // which is a real deployment state the tab must be asserted for, not skipped.
        test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
        await expect(searchTabs(page).getByRole("tab", { name: "Primary Search" })).toHaveCount(
            unifiedSearchOffered(features ?? []) ? 1 : 0
        );
    });

    test("the asset-list tab renders rows or its empty state", async ({ page }) => {
        await gotoSearch(page, { tab: "asset-list" });
        await expect(searchTabs(page).getByRole("tab", { name: /list$/i })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        await expectSearchRendered(page);
    });

    test("the unified tab renders rows or one of its empty states", async ({ page }) => {
        await gotoSearch(page);
        const features = await readFeaturesEnabled(page);
        test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
        test.skip(
            !unifiedSearchOffered(features ?? []),
            "Neither OpenSearch nor vector search is enabled in this environment"
        );
        await gotoSearch(page, { tab: "unified-search" });
        await expect(searchTabs(page).getByRole("tab", { name: "Primary Search" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        await expectSearchRendered(page);
    });

    test("choosing a tab writes it into the hash query", async ({ page }) => {
        await gotoSearch(page);
        const tabs = searchTabs(page).getByRole("tab");
        test.skip((await tabs.count()) < 2, "Only one provider tab; nothing to choose");
        // Choose whichever tab is not selected, so the click is a real change.
        const target = searchTabs(page).getByRole("tab", { selected: false }).first();
        const isList = /list$/i.test(await target.innerText());
        await target.click();
        await expect(page).toHaveURL(isList ? /[?&]tab=asset-list/ : /[?&]tab=unified-search/);
        await expectSearchRendered(page);
    });
});

/** The results pane of the search splitter (the sidebar is the left pane). */
function resultsPane(page: Page) {
    return page.locator(".resizable-right-panel").first();
}

/** Open the unified search tab, whose sidebar and results share the splitter; skip without it. */
async function openSearch(page: Page): Promise<void> {
    await gotoSearch(page);
    const features = await readFeaturesEnabled(page);
    test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
    test.skip(
        !unifiedSearchOffered(features ?? []),
        "Neither OpenSearch nor vector search is enabled in this environment"
    );
    await gotoSearch(page, { tab: "unified-search" });
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
