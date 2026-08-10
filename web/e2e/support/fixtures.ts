/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, Locator, Page } from "@playwright/test";

/**
 * Reusable Playwright harness for the VAMS orchestration UI.
 *
 * Environment-agnostic by design: nothing here assumes a particular pipeline, workflow, or
 * execution exists. Helpers either work against whatever the environment happens to contain, or
 * skip the test when a precondition is genuinely absent. That makes the specs built on this
 * harness safe to run against any sandbox — empty, freshly seeded, or long-lived.
 *
 * The selector helpers are the durable value here: they encode where the app's controls actually
 * live, which is what breaks when markup changes.
 */

/** Orchestration pages are scoped under `.orchestration-root`; the global nav also has a Search. */
export function orchestrationRoot(page: Page): Locator {
    return page.locator(".orchestration-root");
}

/** The orchestration filter-bar search input. Labelled (no placeholder) and NOT the global search. */
export function searchBox(page: Page): Locator {
    return orchestrationRoot(page).getByLabel("Search", { exact: true }).first();
}

/** A native <select> facet, e.g. "Status", "Execution Type", "Database", "Group by". */
export function facet(page: Page, label: string): Locator {
    return page.getByLabel(label, { exact: true });
}

/** Rows currently rendered in the page's data table. */
export function tableRows(page: Page): Locator {
    return page.locator("table tbody tr");
}

/**
 * Navigate to an orchestration page and wait for it to finish its first load. Waits on the page's
 * own heading — never on specific data — so an empty environment is a valid state.
 */
export async function gotoOrchestration(
    page: Page,
    route: "pipelines" | "workflows" | "executions",
    heading: RegExp | string
): Promise<void> {
    await page.goto(`/#/${route}/`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({
        timeout: 60_000,
    });
    // The list issues its first request after mount; settle before asserting on content.
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

/**
 * The id of the first card in a card list (pipelines / workflows), or null when the environment has
 * none. Cards expose an "Actions for {id}" button, which is the stable way to read their identity.
 */
export async function firstCardId(page: Page): Promise<string | null> {
    const actions = page.getByRole("button", { name: /Actions for/i }).first();
    if ((await actions.count()) === 0) return null;
    const label = (await actions.getAttribute("aria-label")) || "";
    const m = label.match(/Actions for\s+(.+)$/i);
    return m ? m[1].trim() : null;
}

/**
 * Open a card's actions menu, filtering the list down to it first so a server-paginated list
 * (pageSize 50) does not hide the target. Returns the menu items locator.
 */
export async function openCardMenu(page: Page, cardId: string): Promise<Locator> {
    await searchBox(page).fill(cardId);
    await expect(page.getByText(cardId, { exact: false }).first()).toBeVisible({ timeout: 45_000 });
    const card = page
        .locator("div", { hasText: cardId })
        .filter({ has: page.getByRole("button", { name: /Actions for/i }) })
        .last();
    await card.scrollIntoViewIfNeeded();
    await card.getByRole("button", { name: /Actions for/i }).click();
    return page.getByRole("menuitem");
}

/**
 * The floating surface a menu item belongs to, reached from the item rather than from `[role="menu"]`.
 *
 * A page carries a dozen or more zero-size Cloudscape `<ul role="menu">` option lists (every closed
 * Select/ButtonDropdown keeps one in the DOM), and they precede the portalled Radix menu in document
 * order — so `page.locator('[role="menu"]').first()` resolves to a hidden one with a transparent
 * background. Walking up from a visible item picks the surface that is actually open.
 */
export function menuSurface(items: Locator): Locator {
    return items.first().locator('xpath=ancestor::*[@role="menu"][1]');
}

/**
 * The value cell of a label/value row in a detail or quick-view panel. Rows render the label and its
 * value as adjacent spans, so the value is the label's next sibling — scoping to it keeps an assertion
 * about one field from matching text anywhere else on the page.
 */
export function rowValue(page: Page, label: string): Locator {
    return page.getByText(label, { exact: true }).first().locator("xpath=following-sibling::*[1]");
}

/** Collect uncaught page errors for the duration of a test (crash-regression assertions). */
export function collectPageErrors(page: Page): string[] {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    return errors;
}

/**
 * Assert a table either has rows or shows its empty state — never that specific data exists. Use
 * this to prove a page renders in ANY environment.
 */
export async function expectTableRendered(page: Page): Promise<number> {
    const rows = tableRows(page);
    await expect
        .poll(
            async () =>
                (await rows.count()) > 0 || (await page.getByText(/no .*found/i).count()) > 0,
            {
                timeout: 60_000,
            }
        )
        .toBe(true);
    return rows.count();
}
