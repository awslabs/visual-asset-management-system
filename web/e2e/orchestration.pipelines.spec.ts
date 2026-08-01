/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect } from "@playwright/test";
import {
    collectPageErrors,
    facet,
    firstCardId,
    gotoOrchestration,
    openCardMenu,
    searchBox,
} from "./support/fixtures";

/**
 * Pipelines page — permanent smoke coverage. Environment-agnostic: asserts the page's controls and
 * behavior against whatever pipelines the environment happens to contain, and skips (rather than
 * fails) when a precondition is genuinely absent. Safe against an empty or a seeded sandbox.
 */

test.describe("Pipelines page", () => {
    test.beforeEach(async ({ page }) => {
        await gotoOrchestration(page, "pipelines", "Pipelines");
    });

    test("renders without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        // Either cards or the empty state — both are valid, depending on the environment.
        await expect
            .poll(
                async () =>
                    (await page.getByRole("button", { name: /Actions for/i }).count()) > 0 ||
                    (await page.getByText(/no pipelines/i).count()) > 0,
                { timeout: 60_000 }
            )
            .toBe(true);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("exposes the filter bar controls", async ({ page }) => {
        await expect(searchBox(page)).toBeVisible();
        for (const label of ["Execution Type", "Status", "Group by"]) {
            await expect(facet(page, label)).toBeVisible();
        }
        await expect(page.getByLabel(/include archived/i)).toBeVisible();
    });

    test("the Status facet offers enabled / disabled / archived", async ({ page }) => {
        const status = facet(page, "Status");
        for (const value of ["enabled", "disabled", "archived"]) {
            await expect(status.locator(`option[value="${value}"]`)).toHaveCount(1);
        }
    });

    test("free-text search narrows the list to a matching pipeline", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment to filter");
        await searchBox(page).fill(id!);
        await expect(page.getByText(id!, { exact: false }).first()).toBeVisible({
            timeout: 45_000,
        });
        // Searching a string that cannot match must empty the list rather than ignore the filter.
        await searchBox(page).fill("zzz-no-such-pipeline-zzz");
        await expect(page.getByRole("button", { name: /Actions for/i })).toHaveCount(0, {
            timeout: 30_000,
        });
    });

    test("a pipeline card's actions menu offers Edit / Templates / Archive", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");
        const items = await openCardMenu(page, id!);
        await expect(items.filter({ hasText: "Templates" })).toBeVisible();
        await expect(items.filter({ hasText: "Edit" })).toBeVisible();
        await expect(items.filter({ hasText: "Archive" })).toBeVisible();
        await page.keyboard.press("Escape");
    });

    test("the Templates action opens the template editor", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");
        const items = await openCardMenu(page, id!);
        await items.filter({ hasText: "Templates" }).click();
        await expect(page.getByRole("heading", { name: "Templates", level: 1 })).toBeVisible({
            timeout: 20_000,
        });
    });
});
