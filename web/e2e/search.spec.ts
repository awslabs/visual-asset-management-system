/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect } from "@playwright/test";
import {
    collectPageErrors,
    expectSearchRendered,
    gotoSearch,
    readFeaturesEnabled,
    searchTabs,
} from "./support/fixtures";

/**
 * Search page — permanent smoke coverage. The page is a strip of provider tabs: the asset list is
 * always present, and the unified search tab appears when the deployment enables OpenSearch or
 * vector search. Environment-agnostic: the expected tab set is derived from the feature switches
 * the app itself cached after login, and nothing here depends on particular assets existing.
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
        await expect(searchTabs(page).getByRole("tab", { name: "Search" })).toHaveCount(
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

    test("the unified tab renders rows or its No matches empty state", async ({ page }) => {
        await gotoSearch(page);
        const features = await readFeaturesEnabled(page);
        test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
        test.skip(
            !unifiedSearchOffered(features ?? []),
            "Neither OpenSearch nor vector search is enabled in this environment"
        );
        await gotoSearch(page, { tab: "unified-search" });
        await expect(searchTabs(page).getByRole("tab", { name: "Search" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        await expectSearchRendered(page);
    });

    test("choosing a tab writes it into the hash query", async ({ page }) => {
        await gotoSearch(page);
        await searchTabs(page).getByRole("tab", { name: /list$/i }).click();
        await expect(page).toHaveURL(/[?&]tab=asset-list/);
        await expectSearchRendered(page);
    });
});
