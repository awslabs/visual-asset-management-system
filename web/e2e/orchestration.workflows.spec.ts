/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect } from "@playwright/test";
import {
    collectPageErrors,
    firstCardId,
    gotoOrchestration,
    openCardMenu,
    searchBox,
} from "./support/fixtures";

/**
 * Workflows page — permanent smoke coverage. Environment-agnostic: derives a workflow from whatever
 * the environment contains and skips when there is none, so it is safe against an empty sandbox.
 *
 * The Execute wizard is opened but never launched — these specs must not mutate the environment.
 */

test.describe("Workflows page", () => {
    test.beforeEach(async ({ page }) => {
        await gotoOrchestration(page, "workflows", "Workflows");
    });

    test("renders without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        await expect
            .poll(
                async () =>
                    (await page.getByRole("button", { name: /Actions for/i }).count()) > 0 ||
                    (await page.getByText(/no workflows/i).count()) > 0,
                { timeout: 60_000 }
            )
            .toBe(true);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("exposes the search control", async ({ page }) => {
        await expect(searchBox(page)).toBeVisible();
    });

    test("a workflow card's actions menu offers Edit / Execute / View Executions / Archive", async ({
        page,
    }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No workflows in this environment");
        const items = await openCardMenu(page, id!);
        await expect(items.filter({ hasText: "Edit" })).toBeVisible();
        await expect(items.filter({ hasText: /Execute/ })).toBeVisible();
        await expect(items.filter({ hasText: /Executions/ })).toBeVisible();
        await expect(items.filter({ hasText: "Archive" })).toBeVisible();
        await page.keyboard.press("Escape");
    });

    test("the Execute action opens the wizard without launching", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No workflows in this environment");
        const items = await openCardMenu(page, id!);
        await items
            .filter({ hasText: /Execute/ })
            .first()
            .click();

        // The wizard is a dialog; assert it opened, then dismiss so nothing is executed.
        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible({ timeout: 20_000 });
        // A dialog must sit above the fixed Cloudscape TopNavigation to be usable.
        const z = await dialog.evaluate(
            (el) => parseInt(window.getComputedStyle(el as HTMLElement).zIndex, 10) || 0
        );
        expect(z).toBeGreaterThan(1000);
        await page.keyboard.press("Escape");
    });

    test("View Executions deep-links to the executions board", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No workflows in this environment");
        const items = await openCardMenu(page, id!);
        await items
            .filter({ hasText: /Executions/ })
            .first()
            .click();
        await expect(page.getByRole("heading", { name: /Executions/i, level: 1 })).toBeVisible({
            timeout: 30_000,
        });
    });
});
