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
    searchPageHeading,
    searchViewSegment,
    searchViewSwitch,
    VIEWPORTS,
} from "./support/fixtures";

/**
 * Search page — permanent smoke coverage. The page has one title row: the h1 and, when more than
 * one view is available, a view switch. The asset list view is always available, and the Search
 * view when the deployment enables OpenSearch or vector search; with the asset list alone there is
 * no switch. Environment-agnostic: the expected views are derived from the feature switches the app
 * itself cached after login, and nothing here depends on particular assets existing.
 *
 * Layout: in the Search view the filter sidebar and the results share a resizable splitter. On a
 * phone the panes stack and the separator is dropped, so the results are on screen instead of
 * pushed out by a sidebar that opens at 400 px or the saved width. From xs up the separator still
 * resizes by keyboard; the test steps it one way and back, so the saved sidebar width ends where it
 * started. Data-independent (Rule 1): asserts the layout whether the search returns rows or its
 * empty state.
 */

/** True when the deployment offers the Search view (either engine on). */
const unifiedSearchOffered = (features: string[]) =>
    features.includes("VECTORSEARCH") || !features.includes("NOOPENSEARCH");

test.describe("Search page", () => {
    test("renders one h1 and no tab strip without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        await gotoSearch(page);
        await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
        await expect(searchPageHeading(page)).toBeVisible();
        await expect(page.getByRole("tablist")).toHaveCount(0);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("offers the view switch exactly when a search engine is enabled", async ({ page }) => {
        await gotoSearch(page);
        const features = await readFeaturesEnabled(page);
        // `null` = nothing cached yet; `[]` = cached with no switches (OpenSearch on, nothing else),
        // which is a real deployment state the switch must be asserted for, not skipped.
        test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
        if (unifiedSearchOffered(features ?? [])) {
            await expect(searchViewSegment(page, "Search")).toHaveCount(1);
            await expect(searchViewSegment(page, /list$/i)).toHaveCount(1);
        } else {
            // The asset list alone: nothing to choose, so no switch, and the list is the page.
            await expect(searchViewSwitch(page)).toHaveCount(0);
            await expect(page.getByRole("checkbox", { name: "Show archived" })).toBeVisible();
        }
    });

    test("the asset list view renders rows or its empty state", async ({ page }) => {
        await gotoSearch(page, { tab: "asset-list" });
        if (await searchViewSwitch(page).count()) {
            await expect(searchViewSegment(page, /list$/i)).toHaveAttribute("aria-pressed", "true");
        }
        await expect(page.getByRole("checkbox", { name: "Show archived" })).toBeVisible();
        await expectSearchRendered(page);
    });

    test("the Search view renders rows or one of its empty states", async ({ page }) => {
        await gotoSearch(page);
        const features = await readFeaturesEnabled(page);
        test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
        test.skip(
            !unifiedSearchOffered(features ?? []),
            "Neither OpenSearch nor vector search is enabled in this environment"
        );
        await gotoSearch(page, { tab: "unified-search" });
        await expect(searchViewSegment(page, "Search")).toHaveAttribute("aria-pressed", "true");
        await expectSearchRendered(page);
    });

    test("choosing a view writes it into the hash query and keeps focus on the switch", async ({
        page,
    }) => {
        await gotoSearch(page);
        test.skip((await searchViewSwitch(page).count()) === 0, "One view only; nothing to choose");
        // Choose whichever view is not selected, so the click is a real change. Pin it by its id
        // first: once pressed it no longer matches `pressed: false`, which would re-resolve to the
        // other segment.
        const unselected = searchViewSwitch(page).getByRole("button", { pressed: false }).first();
        const targetId = await unselected.getAttribute("data-testid");
        expect(targetId, "the unselected segment has an id").toBeTruthy();
        const target = searchViewSwitch(page).locator(`[data-testid="${targetId}"]`);
        const isList = /list$/i.test(await target.innerText());
        await target.click();
        await expect(page).toHaveURL(isList ? /[?&]tab=asset-list/ : /[?&]tab=unified-search/);
        await expect(target).toHaveAttribute("aria-pressed", "true");
        await expect(target).toBeFocused();
        await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
        await expectSearchRendered(page);
    });
});

/** The results pane of the search splitter (the sidebar is the left pane). */
function resultsPane(page: Page) {
    return page.locator(".resizable-right-panel").first();
}

/** Open the Search view, whose sidebar and results share the splitter; skip without it. */
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

    test("wraps the title row inside the width, with the view switch as a select", async ({
        page,
    }) => {
        await gotoSearch(page);
        await expectNoPageOverflowX(page, "search page title row");
        await expect(searchPageHeading(page)).toBeInViewport();
        const features = await readFeaturesEnabled(page);
        test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
        test.skip(!unifiedSearchOffered(features ?? []), "One view only; no switch to show");
        // Below 688 px Cloudscape shows a segmented control as a select under the same label.
        await expect(searchViewSwitch(page)).toHaveCount(0);
        const select = page.getByLabel("Page view", { exact: true }).filter({ visible: true });
        await expect(select).toHaveCount(1);
        const box = await select.boundingBox();
        expect(box, "view select has no box").not.toBeNull();
        expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 0.5);
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
