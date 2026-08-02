/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect } from "@playwright/test";
import {
    collectPageErrors,
    expectTableRendered,
    facet,
    firstCardId,
    gotoOrchestration,
    openCardMenu,
    orchestrationRoot,
    tableRows,
} from "./support/fixtures";

/**
 * End-to-end coverage for this round's UI changes, against the DEPLOYED bundle.
 *
 * Environment-agnostic like the rest of the suite: every test either works with whatever the sandbox
 * contains or skips when a precondition is genuinely absent. Jest already proves these components
 * behave in isolation — what only Playwright can prove is that the shipped bundle behaves, which is
 * the gap a fix living in src/ silently falls into.
 */

test.describe("execute modal: workflow restriction summary", () => {
    test("the picker states what the chosen workflow accepts", async ({ page }) => {
        const errors = collectPageErrors(page);
        await gotoOrchestration(page, "executions", /Executions/i);

        const execute = page.getByRole("button", { name: /^Execute workflow$/i });
        if ((await execute.count()) === 0) {
            test.skip(true, "caller may not execute workflows in this environment");
        }
        await execute.click();
        await expect(page.getByText(/Execute a workflow/i)).toBeVisible({ timeout: 30_000 });

        // Pick the first workflow the picker offers; which one it is does not matter.
        await page.getByLabel("Workflow", { exact: true }).click();
        const options = page.getByRole("option");
        if ((await options.count()) === 0) {
            test.skip(true, "no enabled workflows in this environment");
        }
        await options.first().click();

        // The compact summary: "<n> file types · <arity> · <output>". Asserted on the shape rather
        // than on a specific workflow's values.
        await expect(
            orchestrationRoot(page)
                .getByText(/(Any file type|\d+ file types?) ·/)
                .first()
        ).toBeVisible({ timeout: 30_000 });
        expect(errors).toEqual([]);
    });

    test("the compact summary does not dump the pattern list into the picker", async ({ page }) => {
        await gotoOrchestration(page, "executions", /Executions/i);
        const execute = page.getByRole("button", { name: /^Execute workflow$/i });
        if ((await execute.count()) === 0) test.skip(true, "execute not permitted here");
        await execute.click();
        await page.getByLabel("Workflow", { exact: true }).click();
        const options = page.getByRole("option");
        if ((await options.count()) === 0) test.skip(true, "no workflows");
        await options.first().click();

        // The full breakdown belongs on the wizard's input step; the dialog stays scannable.
        await expect(page.getByText(/Accepted file types:/i)).toHaveCount(0);
    });
});

test.describe("execution output target", () => {
    test("the details page states the output path prefix", async ({ page }) => {
        await gotoOrchestration(page, "executions", /Executions/i);
        const count = await expectTableRendered(page);
        if (count === 0) test.skip(true, "no executions in this environment");

        await tableRows(page).first().click();
        // The quick-view panel opens with the output target broken into its parts.
        await expect(page.getByText(/Output Path Prefix/i).first()).toBeVisible({
            timeout: 30_000,
        });
        // Always present, even when there is no prefix — "no prefix" is itself information, and
        // hiding the row made it indistinguishable from a missing field.
        await expect(page.getByText(/None \(asset root\)|\//).first()).toBeVisible();
    });

    test("output type, database and asset are stated alongside it", async ({ page }) => {
        await gotoOrchestration(page, "executions", /Executions/i);
        const count = await expectTableRendered(page);
        if (count === 0) test.skip(true, "no executions");

        await tableRows(page).first().click();
        for (const label of [/Output Type/i, /Output Database ID/i, /Output Asset ID/i]) {
            await expect(page.getByText(label).first()).toBeVisible({ timeout: 30_000 });
        }
    });
});

test.describe("record action menus", () => {
    // The menus previously painted the same colour as the page and rows beneath them, so they read as
    // part of the table rather than as a floating layer.
    test("an action menu is visually distinct from the page behind it", async ({ page }) => {
        await gotoOrchestration(page, "pipelines", /Pipelines/i);
        const cardId = await firstCardId(page);
        if (!cardId) test.skip(true, "no pipelines in this environment");

        const items = await openCardMenu(page, cardId!);
        await expect(items.first()).toBeVisible({ timeout: 30_000 });

        // Read the menu's computed background and the page's, and require they differ. Comparing
        // computed values (rather than asserting a hex) keeps this true in both themes.
        const menuBg = await page
            .locator('[role="menu"]')
            .first()
            .evaluate((el) => getComputedStyle(el).backgroundColor);
        const pageBg = await orchestrationRoot(page)
            .first()
            .evaluate((el) => getComputedStyle(el).backgroundColor);
        expect(menuBg).not.toBe("rgba(0, 0, 0, 0)");
        expect(menuBg).not.toBe(pageBg);
    });

    test("menu items are reachable and labelled", async ({ page }) => {
        await gotoOrchestration(page, "workflows", /Workflows/i);
        const cardId = await firstCardId(page);
        if (!cardId) test.skip(true, "no workflows");
        const items = await openCardMenu(page, cardId!);
        expect(await items.count()).toBeGreaterThan(0);
        for (const text of await items.allTextContents()) {
            expect(text.trim().length).toBeGreaterThan(0);
        }
    });
});

test.describe("executions list", () => {
    test("output columns are present", async ({ page }) => {
        await gotoOrchestration(page, "executions", /Executions/i);
        await expectTableRendered(page);
        // Column headers exist whether or not the environment has rows.
        for (const header of [/Output Database/i, /Output Asset/i]) {
            await expect(page.locator("table thead").getByText(header).first()).toBeVisible({
                timeout: 30_000,
            });
        }
    });

    test("the status facet filters without crashing the page", async ({ page }) => {
        const errors = collectPageErrors(page);
        await gotoOrchestration(page, "executions", /Executions/i);
        await expectTableRendered(page);

        const status = facet(page, "Status");
        if ((await status.count()) === 0) test.skip(true, "no status facet rendered");
        await status.selectOption({ label: "SUCCEEDED" }).catch(() => undefined);
        await expectTableRendered(page);
        expect(errors).toEqual([]);
    });
});
