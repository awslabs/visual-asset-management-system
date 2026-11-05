/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page } from "@playwright/test";
import { collectPageErrors } from "./support/fixtures";

/**
 * Route pages are code-split; a page chunk that fails to download is retried, then the page reloads
 * once, and only a chunk that still cannot be downloaded shows the "could not be downloaded" panel.
 * Environment-agnostic: the Executions page heading renders on an empty environment, and the faults
 * are injected in the browser, so nothing in the deployment is changed.
 */

const CHUNKS = [
    ["the page's own chunk", /\/assets\/ExecutionsPage-[\w-]+\.js(\?|$)/],
    ["a chunk the page imports", /\/assets\/ExecutionsBoard-[\w-]+\.js(\?|$)/],
] as const;

const DOWNLOAD_PANEL = "This page could not be downloaded";
const GENERIC_PANEL = "Something went wrong on this page";

/** Abort the first `times` requests matching `pattern`; returns a counter of all its requests. */
async function abortChunk(page: Page, pattern: RegExp, times: number) {
    const seen = { requests: 0 };
    await page.route(pattern, async (route) => {
        seen.requests += 1;
        if (seen.requests <= times) {
            await route.abort("connectionreset");
        } else {
            await route.continue();
        }
    });
    return seen;
}

async function expectExecutionsPage(page: Page) {
    await expect(page.getByRole("heading", { name: /Executions/i, level: 1 })).toBeVisible({
        timeout: 60_000,
    });
}

for (const width of [390, 768, 1280, 1440]) {
    test.describe(`Route chunk recovery at ${width} px`, () => {
        test.use({ viewport: { width, height: 900 } });

        for (const [label, pattern] of CHUNKS) {
            test(`loads the page when ${label} fails to download once`, async ({ page }) => {
                const errors = collectPageErrors(page);
                const seen = await abortChunk(page, pattern, 1);

                await page.goto("/#/executions/", { waitUntil: "domcontentloaded" });

                await expectExecutionsPage(page);
                expect(seen.requests).toBeGreaterThanOrEqual(2);
                await expect(page.getByText(DOWNLOAD_PANEL)).toHaveCount(0);
                await expect(page.getByText(GENERIC_PANEL)).toHaveCount(0);
                expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
            });
        }

        test("says the page could not be downloaded, and Reload recovers it", async ({ page }) => {
            const [, pattern] = CHUNKS[0];
            await abortChunk(page, pattern, Number.MAX_SAFE_INTEGER);

            await page.goto("/#/executions/", { waitUntil: "domcontentloaded" });

            // One in-page retry, one guarded automatic reload, then the panel.
            await expect(page.getByText(DOWNLOAD_PANEL)).toBeVisible({ timeout: 60_000 });
            await expect(page.getByText(GENERIC_PANEL)).toHaveCount(0);
            await expect(page.getByRole("button", { name: "Go to Home" })).toBeVisible();
            const guard = await page.evaluate(() =>
                window.sessionStorage.getItem("vams:chunk-reload-at")
            );
            expect(guard).not.toBeNull();

            await page.unroute(pattern);
            await page.getByRole("button", { name: "Reload" }).click();
            await expectExecutionsPage(page);
            await expect(page.getByText(DOWNLOAD_PANEL)).toHaveCount(0);
        });
    });
}
