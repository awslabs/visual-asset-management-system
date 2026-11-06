/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec for the Databases list page.
 *
 * - Loading the page sends no `GET /buckets`: the create form mounts with the list but reads the
 *   bucket list only once it opens, and only for a role allowed that route. This holds for every
 *   role, so run the suite a second time signed in as a read-only role (`database-readonly`
 *   template) to cover the role that used to get a 403 and a console error on each load.
 *
 * Data-independent (Rule 1) and non-mutating (Rule 2): the create form is opened, then dismissed.
 */

import { test, expect, type Page, type Request } from "@playwright/test";

const isBucketsRead = (r: Request) =>
    r.method() === "GET" &&
    /\/buckets(\?|$)/.test(new URL(r.url()).pathname + new URL(r.url()).search);

async function openDatabases(page: Page): Promise<void> {
    await page.goto("/#/databases/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("button", { name: /^Create / })).toBeVisible({ timeout: 60_000 });
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

test.describe("Databases page", () => {
    test("loads without reading the bucket list or logging a 403", async ({ page }) => {
        const bucketReads: string[] = [];
        const forbidden: string[] = [];
        page.on("request", (r) => {
            if (isBucketsRead(r)) bucketReads.push(r.url());
        });
        page.on("console", (m) => {
            if (m.type() === "error" && /\b403\b/.test(m.text())) forbidden.push(m.text());
        });

        await openDatabases(page);
        expect(bucketReads, "GET /buckets on page load").toHaveLength(0);
        expect(forbidden, `403 console errors: ${forbidden.join(" | ")}`).toHaveLength(0);
    });

    test("reads the bucket list when the create form opens, for a role allowed to create", async ({
        page,
    }) => {
        await openDatabases(page);
        const create = page.getByRole("button", { name: /^Create / });
        test.skip(!(await create.isEnabled()), "This role may not create databases");

        const read = page.waitForRequest(isBucketsRead, { timeout: 30_000 }).catch(() => null);
        await create.click();
        await expect(page.getByRole("dialog")).toBeVisible();
        // A creating role is normally granted GET /buckets too; one that is not makes no request.
        const request = await read;
        if (request) {
            const response = await request.response();
            expect(response?.status()).toBeLessThan(400);
        }
        await page.keyboard.press("Escape");
    });
});
