/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page } from "@playwright/test";

/**
 * Regression locks for the web fixes from the independent review (66 web findings). Each fix is
 * already unit-tested; these assert the user-visible behavior against the deployed app so a
 * regression is caught end to end rather than only at the component level.
 *
 * Runs against the seeded prod14 dataset (wseed-pipe-000..079, mock pipelines/workflows, and the
 * executions produced by the smoke rounds).
 *
 * These specs test the DEPLOYED bundle, so the front end must be rebuilt and published for a fix to
 * be observable here — a source-only fix still fails against a stale deployment.
 */

/**
 * Open a pipeline card's actions menu. The list is server-paginated (pageSize 50), so a card is
 * located by first filtering the list down to it — a high seed id is not on page 1, and filtering
 * drains the remaining pages.
 */
async function openCardMenu(page: Page, cardText: string) {
    await page
        .locator(".orchestration-root")
        .getByLabel("Search", { exact: true })
        .first()
        .fill(cardText);
    await expect(page.getByText(cardText).first()).toBeVisible({ timeout: 45_000 });
    const card = page
        .locator("div", { hasText: cardText })
        .filter({ has: page.getByRole("button", { name: /Actions for/i }) })
        .last();
    await card.scrollIntoViewIfNeeded();
    await card.getByRole("button", { name: /Actions for/i }).click();
    return page.getByRole("menuitem");
}

test.describe("Template actions — delete is permanent, not an archive", () => {
    test("the template row action reads Delete and warns that it cannot be undone", async ({
        page,
    }) => {
        await page.goto("/#/pipelines/", { waitUntil: "domcontentloaded" });
        await expect(page.getByText(/wseed-pipe-\d+/).first()).toBeVisible({ timeout: 60_000 });

        // wseed-pipe-000 was seeded with a template.
        const items = await openCardMenu(page, "wseed-pipe-000");
        await items.filter({ hasText: "Templates" }).click();
        await expect(page.getByRole("heading", { name: "Templates", level: 1 })).toBeVisible({
            timeout: 20_000,
        });

        // The destructive action must say Delete — the backend hard-deletes the template row, its
        // offloaded S3 config bodies, and its tag schema. "Archive" implied it was recoverable.
        const del = page.getByRole("button", { name: /^Delete$/ }).first();
        await expect(del).toBeVisible();
        await expect(page.getByRole("button", { name: /^Archive$/ })).toHaveCount(0);

        // The confirm must state permanence. Capture the dialog text, then dismiss it so the run
        // never actually deletes seeded data.
        let confirmText = "";
        page.once("dialog", async (d) => {
            confirmText = d.message();
            await d.dismiss();
        });
        await del.click();
        await expect
            .poll(() => confirmText, { timeout: 10_000 })
            .toMatch(/permanently delete|cannot be undone/i);
    });
});

test.describe("Pipelines page — archived facet and failure feedback", () => {
    test.beforeEach(async ({ page }) => {
        await page.goto("/#/pipelines/", { waitUntil: "domcontentloaded" });
        await expect(page.getByText(/wseed-pipe-\d+/).first()).toBeVisible({ timeout: 60_000 });
    });

    test("selecting the Archived status facet reveals archived pipelines without a second toggle", async ({
        page,
    }) => {
        // Previously the Archived facet returned nothing unless Include Archived was also checked:
        // the server omits archived rows unless asked, so the facet alone matched an empty set.
        // Selecting the facet must now refetch with archived included.
        const statusFacet = page.getByLabel("Status", { exact: true });
        await expect(statusFacet).toBeVisible({ timeout: 20_000 });
        await statusFacet.selectOption("archived");

        // The include-archived checkbox must stay unchecked — the facet alone has to be sufficient.
        await expect(page.getByLabel(/include archived/i)).not.toBeChecked();
        await expect(page.locator("span", { hasText: /^Archived$/ }).first()).toBeVisible({
            timeout: 30_000,
        });
    });
});

test.describe("Executions board — status rendering and trigger filter vocabulary", () => {
    test.beforeEach(async ({ page }) => {
        await page.goto("/#/executions/", { waitUntil: "domcontentloaded" });
        await expect(page.getByRole("heading", { name: /Executions/i, level: 1 })).toBeVisible({
            timeout: 60_000,
        });
    });

    test("the board renders rows without crashing on any status value", async ({ page }) => {
        // StatusBadge previously dereferenced an unmapped status and took down the whole board.
        const errors: string[] = [];
        page.on("pageerror", (e) => errors.push(e.message));
        await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 60_000 });
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("the trigger filter offers the stored vocabulary and returns matching rows", async ({
        page,
    }) => {
        // The filter must offer the STORED vocabulary ("Manual" / "File-Upload"); sending the
        // UI-style "fileUpload" matched nothing server-side, so the board always came back empty.
        const trigger = page.getByLabel("Filter by trigger");
        await expect(trigger).toBeVisible({ timeout: 20_000 });
        await expect(trigger.locator('option[value="File-Upload"]')).toHaveCount(1);
        await expect(trigger.locator('option[value="fileUpload"]')).toHaveCount(0);

        await trigger.selectOption("Manual");
        // The seeded rounds produced Manual executions, so this filter must not empty the board.
        await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 45_000 });
    });
});

test.describe("Dialog layering", () => {
    test("a dialog renders above the Cloudscape top navigation", async ({ page }) => {
        await page.goto("/#/pipelines/", { waitUntil: "domcontentloaded" });
        await expect(page.getByText(/wseed-pipe-\d+/).first()).toBeVisible({ timeout: 60_000 });

        const items = await openCardMenu(page, "wseed-pipe-006");
        await items.filter({ hasText: "Archive" }).click();

        // The Radix dialog previously rendered beneath the fixed Cloudscape TopNavigation (z-40/50
        // vs the header's 1000+), leaving the confirm partly covered and unclickable.
        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible({ timeout: 15_000 });

        const dialogZ = await dialog.evaluate(
            (el) => parseInt(window.getComputedStyle(el as HTMLElement).zIndex, 10) || 0
        );
        expect(dialogZ).toBeGreaterThan(1000);

        // The dialog must also be the element actually receiving clicks at its own centre — a
        // z-index alone does not prove it is not overlaid.
        const hitIsInsideDialog = await dialog.evaluate((el) => {
            const r = (el as HTMLElement).getBoundingClientRect();
            const top = document.elementFromPoint(r.left + r.width / 2, r.top + 8);
            return !!top && (el as HTMLElement).contains(top);
        });
        expect(hitIsInsideDialog).toBe(true);

        // Close without archiving so seeded data is untouched.
        await page.keyboard.press("Escape");
    });
});
