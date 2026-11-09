/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the asset page across in-app navigation, and when the asset cannot be loaded.
 *
 * Moving from one asset page to another inside the app (a hash change, as a link inside the app
 * makes) keeps the page mounted, so per-asset state must follow the route:
 *
 *  - the File Manager's Download asks for the file of the asset now shown, not the previous one;
 *  - after an asset that could not be loaded, a loadable asset shows its details rather than staying
 *    on "Information Unavailable".
 *
 * And an asset the user's role may not read says access was refused (not "invalid response"),
 * without mounting the asset's details, files, metadata or map.
 *
 * Data-independent (Rule 1): the subjects are the first distributable assets with a top-level file
 * the API lists, and a test skips when the environment has too few. The access-refused test needs an
 * asset the signed-in user may NOT read, named by `E2E_DENIED_ASSET=<databaseId>/<assetId>`, and
 * skips without it; run it signed in as a constrained role (`E2E_USERNAME` / `E2E_PASSWORD`, with
 * `E2E_FORCE_LOGIN=1` when switching users).
 * Non-mutating (Rule 2): pages are only opened and read; the download request is captured and
 * aborted before it reaches the API.
 */

import { test, expect, type Page } from "@playwright/test";
import {
    collectPageErrors,
    findAssetsWithFiles,
    openFileManager,
    selectTreePath,
    type AssetFileRef,
} from "./support/fixtures";

const UNAVAILABLE = /Information Unavailable/i;

/** Navigates inside the running app, as an in-app link does, without reloading the page. */
async function goInApp(page: Page, databaseId: string, assetId: string) {
    await page.evaluate((hash) => {
        window.location.hash = hash;
    }, `#/databases/${encodeURIComponent(databaseId)}/assets/${encodeURIComponent(assetId)}`);
}

/** Waits for the asset page's File Manager tree to render. */
async function expectTreeRendered(page: Page) {
    await expect
        .poll(async () => await page.getByText(/Hold Ctrl or Shift to select/i).count(), {
            timeout: 60_000,
        })
        .toBeGreaterThan(0);
}

/** Chooses Export -> Download File for the selected file and returns the download request URL. */
async function downloadRequestUrl(page: Page): Promise<string> {
    const requested = page.waitForRequest(
        (r) => r.method() === "POST" && /\/assets\/[^/]+\/download$/.test(new URL(r.url()).pathname)
    );
    await page.getByRole("button", { name: "Export" }).first().click();
    await page.getByRole("menuitem", { name: /Download File/ }).click();
    return (await requested).url();
}

test.describe("asset page across in-app navigation", () => {
    test.beforeEach(async ({ page }) => {
        // Capture the download request and stop it there; the spec only reads which asset it names.
        await page.route(/\/assets\/[^/]+\/download$/, (route) =>
            route.request().method() === "POST" ? route.abort() : route.continue()
        );
    });

    test("Download asks for the file of the asset now shown", async ({ page }) => {
        const assets = await findAssetsWithFiles(page, 2);
        test.skip(
            !assets || assets.length < 2,
            "Fewer than two distributable assets with a top-level file (or no API context)"
        );
        const [a, b] = assets as AssetFileRef[];
        const errors = collectPageErrors(page);

        await openFileManager(page, a.databaseId, a.assetId);
        await selectTreePath(page, a.key);
        expect(await downloadRequestUrl(page)).toContain(`/assets/${a.assetId}/download`);

        await goInApp(page, b.databaseId, b.assetId);
        await expectTreeRendered(page);
        await selectTreePath(page, b.key);
        const url = await downloadRequestUrl(page);

        expect(url).toContain(`/database/${b.databaseId}/assets/${b.assetId}/download`);
        expect(url).not.toContain(`/assets/${a.assetId}/`);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("an asset that loads replaces the previous asset's error", async ({ page }) => {
        const assets = await findAssetsWithFiles(page, 1);
        test.skip(!assets || assets.length < 1, "No distributable asset with a file (or no API)");
        const [a] = assets as AssetFileRef[];

        // An asset id that does not exist fails to load, as a denied one does.
        await page.goto(
            `/#/databases/${encodeURIComponent(a.databaseId)}/assets/e2e-missing-asset-id`,
            { waitUntil: "domcontentloaded" }
        );
        await expect(page.getByRole("heading", { name: UNAVAILABLE })).toBeVisible({
            timeout: 60_000,
        });

        await goInApp(page, a.databaseId, a.assetId);

        await expectTreeRendered(page);
        await expect(page.getByRole("heading", { name: UNAVAILABLE })).toHaveCount(0);
    });
});

test.describe("asset page for an asset the role may not read", () => {
    const denied = process.env.E2E_DENIED_ASSET;

    test("says access was refused and mounts no asset content", async ({ page }) => {
        test.skip(!denied, "Set E2E_DENIED_ASSET=<databaseId>/<assetId> for a constrained user");
        const [databaseId, assetId] = denied!.split("/");
        const errors = collectPageErrors(page);
        const assetContentRequests: string[] = [];
        page.on("request", (r) => {
            const path = new URL(r.url()).pathname;
            if (
                path.includes(`/assets/${assetId}/`) &&
                /(listFiles|metadata|assetLinks)/.test(path)
            ) {
                assetContentRequests.push(path);
            }
        });

        await page.goto(
            `/#/databases/${encodeURIComponent(databaseId)}/assets/${encodeURIComponent(assetId)}`,
            { waitUntil: "domcontentloaded" }
        );

        await expect(page.getByText(/Access denied\. Your role does not allow you/)).toBeVisible({
            timeout: 60_000,
        });
        await expect(page.getByText(/invalid response/i)).toHaveCount(0);
        await expect(page.getByText(/Hold Ctrl or Shift to select/i)).toHaveCount(0);
        await page.waitForLoadState("networkidle").catch(() => undefined);
        expect(assetContentRequests, "asset content was requested for a refused asset").toEqual([]);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("a readable asset then shows its details", async ({ page }) => {
        test.skip(!denied, "Set E2E_DENIED_ASSET=<databaseId>/<assetId> for a constrained user");
        const [databaseId, assetId] = denied!.split("/");
        const assets = await findAssetsWithFiles(page, 1);
        test.skip(!assets || assets.length < 1, "No readable asset with a file for this user");
        const [a] = assets as AssetFileRef[];

        await page.goto(
            `/#/databases/${encodeURIComponent(databaseId)}/assets/${encodeURIComponent(assetId)}`,
            { waitUntil: "domcontentloaded" }
        );
        await expect(page.getByRole("heading", { name: UNAVAILABLE })).toBeVisible({
            timeout: 60_000,
        });

        await goInApp(page, a.databaseId, a.assetId);

        await expectTreeRendered(page);
        await expect(page.getByRole("heading", { name: UNAVAILABLE })).toHaveCount(0);
    });
});
