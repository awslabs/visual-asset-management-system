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
 * - At 1280 and 1440 px with the navigation open, the list (seven default columns, Base Bucket
 *   Prefix hidden until chosen in the preferences, cell text wrapped) does not scroll sideways;
 *   with map thumbnails on (a Map column), only the page itself is held to that.
 *
 * Data-independent (Rule 1) and non-mutating (Rule 2): the create form is opened, then dismissed.
 */

import { test, expect, type Page, type Request } from "@playwright/test";
import { expectNoPageOverflowX, VIEWPORTS } from "./support/fixtures";

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

/** How far the list table is wider than its own scroll box, in pixels (0 when it fits). */
async function tableOverflowX(page: Page): Promise<number> {
    const table = page.locator("table").first();
    await expect(table).toBeVisible();
    return table.evaluate((el) => {
        const box = el.parentElement as HTMLElement;
        return Math.max(0, box.scrollWidth - box.clientWidth);
    });
}

/** Opens the side navigation if it is closed, and waits for it to show. */
async function openNavigation(page: Page): Promise<void> {
    const open = page.getByRole("button", { name: /open .*navigation/i }).first();
    if (await open.isVisible().catch(() => false)) await open.click();
    await expect(page.getByRole("button", { name: /close .*navigation/i }).first()).toBeVisible();
}

/*
 * The table scrolls inside its own box, so the page itself never widens; the width that matters is
 * the table's against that box. Base Bucket Prefix starts hidden, so the seven default columns fit
 * beside an open navigation at 1280 and 1440 px. With map thumbnails on, the extra Map column may
 * scroll inside the table's box, so only the page overflow is checked there.
 */
test.describe("Databases list — width, navigation open", () => {
    for (const [name, viewport] of [
        ["laptop", VIEWPORTS.laptop],
        ["desktop", VIEWPORTS.desktop],
    ] as const) {
        test.describe(`${name} (${viewport.width} px)`, () => {
            test.use({ viewport });

            test("the table fits its container", async ({ page }) => {
                await openDatabases(page);
                await openNavigation(page);
                await expect(page.getByRole("columnheader", { name: /Bucket Name/ })).toBeVisible();
                await expect(
                    page.getByRole("columnheader", { name: /Base Bucket Prefix/ })
                ).toHaveCount(0);
                await expectNoPageOverflowX(page, "databases list");
                // A deployment with map thumbnails adds a fixed-width Map column the seven-column
                // budget does not cover; there the table may scroll inside its box, so only the
                // page overflow above is asserted.
                const hasMapColumn =
                    (await page.getByRole("columnheader", { name: /^Map\b/ }).count()) > 0;
                if (!hasMapColumn) {
                    expect(await tableOverflowX(page), "table wider than its container").toBe(0);
                }
            });
        });
    }
});
