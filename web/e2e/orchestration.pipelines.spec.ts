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

    test("Edit on a stored pipeline opens a populated form whose save is reachable", async ({
        page,
    }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");
        const items = await openCardMenu(page, id!);
        const edit = items.filter({ hasText: "Edit" });
        // A DeadlineCloud pipeline in a deployment with that feature off is read-only and offers no
        // Edit action, so there is nothing to assert about the form here.
        if ((await edit.count()) === 0) {
            await page.keyboard.press("Escape");
            test.skip(true, "The first pipeline offers no Edit action in this deployment");
        }
        await edit.first().click();

        await expect(page.getByRole("heading", { name: "Edit Pipeline", level: 1 })).toBeVisible({
            timeout: 30_000,
        });
        // A pipeline stored by any writer — API, CLI, CDK registration, or a migration — must be
        // editable. Every stored record carries all four execution-type sub-blocks with the unused
        // ones empty, so a form that validated those blocked the save with nothing on screen.
        await expect(page.locator("#pipelineId")).toHaveValue(/.+/, { timeout: 30_000 });
        await expect(page.locator("#pipelineName")).not.toHaveValue("");

        // The save must actually leave the browser. The request is intercepted and aborted, so this
        // proves the form issues it without changing the pipeline (core specs mutate nothing).
        let updateAttempted = false;
        await page.route(/\/pipelines\//, async (route) => {
            if (route.request().method() === "PUT") {
                updateAttempted = true;
                await route.abort();
                return;
            }
            await route.continue();
        });

        // Walk the wizard with its own Next button; Save exists only on the last step. Locating a
        // step by name would match the global navigation instead of the stepper. Bounded rather than
        // "while visible" so a Next that stops advancing fails on the Update assertion below with a
        // readable message instead of looping.
        const next = page.getByRole("button", { name: "Next" });
        for (let step = 0; step < 5; step++) {
            if (!(await next.isVisible().catch(() => false))) break;
            await next.click();
        }
        const update = page.getByRole("button", { name: /^Update$/ });
        await expect(update).toBeEnabled();
        await update.click();
        await expect.poll(() => updateAttempted, { timeout: 20_000 }).toBe(true);
    });

    test("a pipeline that fails to load shows an error, not a blank Edit form", async ({
        page,
    }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");

        // Fails the single-pipeline read only: the list route ends at "/pipelines" with no id
        // segment, and the templates route continues past the id, so neither matches.
        await page.route(/\/pipelines\/[^/?]+(\?|$)/, async (route) => {
            if (route.request().method() === "GET") {
                await route.fulfill({
                    status: 500,
                    contentType: "application/json",
                    body: JSON.stringify({ message: "Internal Server Error" }),
                });
                return;
            }
            await route.continue();
        });

        const items = await openCardMenu(page, id!);
        const edit = items.filter({ hasText: "Edit" });
        if ((await edit.count()) === 0) {
            await page.keyboard.press("Escape");
            test.skip(true, "The first pipeline offers no Edit action in this deployment");
        }
        await edit.first().click();

        // An unreadable pipeline must say so. A form titled "Edit Pipeline" seeded with create
        // defaults reads as a pipeline whose configuration was wiped, and saving it would PUT to a
        // path with an empty id.
        await expect(page.getByText(/pipeline not found/i)).toBeVisible({ timeout: 30_000 });
        await expect(page.locator("#pipelineName")).toHaveCount(0);
        await expect(page.getByRole("button", { name: /^Update$/ })).toHaveCount(0);
    });
});
