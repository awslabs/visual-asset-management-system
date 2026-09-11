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

/**
 * The workflow builder's step list. Derived from an existing workflow so it holds in any environment:
 * every workflow has at least one step, and a second card is added by the builder itself.
 */
test.describe("Workflow builder — step definition", () => {
    /**
     * Open Edit on whatever workflow exists and land on the Pipelines step. Returns false when the
     * environment has no workflow or the caller may not edit it, so the test can skip honestly.
     */
    async function openPipelinesStep(page: import("@playwright/test").Page): Promise<boolean> {
        await gotoOrchestration(page, "workflows", "Workflows");
        const id = await firstCardId(page);
        if (!id) return false;
        const items = await openCardMenu(page, id);
        const edit = items.filter({ hasText: "Edit" });
        if ((await edit.count()) === 0) {
            await page.keyboard.press("Escape");
            return false;
        }
        await edit.first().click();
        await expect(page.getByRole("heading", { name: "Edit Workflow", level: 1 })).toBeVisible({
            timeout: 30_000,
        });
        // Advance with the wizard's own Next button — a step cannot be located by name, because the
        // global navigation owns those words. Pipelines is the third step.
        const next = page.getByRole("button", { name: "Next" });
        for (let step = 0; step < 2; step++) {
            await next.click();
        }
        await expect(page.getByRole("button", { name: "Add Pipeline" })).toBeVisible({
            timeout: 20_000,
        });
        return true;
    }

    /** The step cards' Job Name inputs, in card order. */
    function jobNameInputs(page: import("@playwright/test").Page) {
        return page.locator('input[id^="jobName-"]');
    }

    test("two steps sharing a job name block the save", async ({ page }) => {
        test.skip(!(await openPipelinesStep(page)), "No editable workflow in this environment");

        // A second card, so there are two job names to collide. Its pipeline is left unselected —
        // that is its own blocking error, so the assertion targets the job-name message specifically.
        await page.getByRole("button", { name: "Add Pipeline" }).click();
        const names = jobNameInputs(page);
        await expect(names).toHaveCount(2, { timeout: 20_000 });

        // The same name on both steps collapses them into ONE Step Functions state, so one of the
        // pipelines silently never runs. The value satisfies the charset rule so the only error it
        // can raise is the duplicate.
        await names.nth(0).fill("shared-step-name");
        await names.nth(1).fill("shared-step-name");

        // Reported on the offending card AND as a blocking error in the validation panel, which is
        // rendered on every step and is what withholds Save on Review.
        await expect(page.getByText(/already uses this job name/i).first()).toBeVisible({
            timeout: 20_000,
        });
        await expect(page.getByText(/Errors \(blocking save\)/i).first()).toBeVisible({
            timeout: 20_000,
        });
        await expect(page.getByText(/is already used by pipeline #1/i).first()).toBeVisible();

        // Differing names clear it, proving the error tracked the collision rather than the field.
        await names.nth(1).fill("other-step-name");
        await expect(page.getByText(/already uses this job name/i)).toHaveCount(0, {
            timeout: 20_000,
        });
    });

    test("a pipeline already in the workflow cannot be added a second time", async ({ page }) => {
        test.skip(!(await openPipelinesStep(page)), "No editable workflow in this environment");

        const firstSelect = page.locator('select[id^="pipeline-"]').first();
        const chosen = await firstSelect.inputValue();
        test.skip(!chosen, "The first step has no pipeline selected to duplicate");

        await page.getByRole("button", { name: "Add Pipeline" }).click();
        const selects = page.locator('select[id^="pipeline-"]');
        await expect(selects).toHaveCount(2, { timeout: 20_000 });

        // The backend keys each step's parameters, resolved config and filtered inputs by the
        // pipeline's composite id, so a second reference to one overwrites the first and only one of
        // the two steps runs. The new card must not offer the pipeline the first card holds.
        const duplicate = selects.nth(1).locator(`option[value="${chosen}"]`);
        await expect(duplicate).toHaveCount(1);
        await expect(duplicate).toBeDisabled();
        await expect(duplicate).toHaveText(/already in this workflow/i);
    });

    test("Cancel with unsaved changes asks before discarding", async ({ page }) => {
        test.skip(!(await openPipelinesStep(page)), "No editable workflow in this environment");

        // Any authored change makes the wizard dirty; a job name is the cheapest one on this step.
        await jobNameInputs(page).first().fill("cancel-guard-probe");

        // Captured WITHOUT accepting, so nothing is discarded and nothing is saved — the wizard stays
        // on screen either way.
        let confirmText = "";
        page.once("dialog", async (d) => {
            confirmText = d.message();
            await d.dismiss();
        });
        await page.getByRole("button", { name: "Cancel" }).click();
        await expect.poll(() => confirmText, { timeout: 20_000 }).toMatch(/without saving/i);
        await expect(page.getByRole("heading", { name: "Edit Workflow", level: 1 })).toBeVisible();
    });
});
