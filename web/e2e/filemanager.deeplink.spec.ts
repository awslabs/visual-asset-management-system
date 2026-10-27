/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec for the asset File Manager's `?filePath=` deep link.
 *
 * The link names a file by its asset-relative path. Producers do not all write the tree's own form:
 * workflow output rows, and the execution detail page's "Open in file manager" links built from them,
 * store the path without its leading slash, and the same page's input file links carry the file's full
 * stored key (the asset's S3 root key followed by the asset-relative path). The File Manager resolves
 * each form.
 *
 * Data-independent (Rule 1): the subject is the first file inside a folder the API lists, and a test
 * skips when the environment has none (the full-key test also when the listing has no root entry to
 * read the root key from). Set `E2E_FILEMANAGER_ASSET=<databaseId>/<assetId>` to scan one asset only.
 * Non-mutating (Rule 2): the page is only opened and read.
 */

import { test, expect, type Page } from "@playwright/test";
import {
    collectPageErrors,
    findNestedAssetFile,
    selectedTreeNode,
    type NestedAssetFileRef,
} from "./support/fixtures";

/** Opens the asset page with `?filePath=<filePath>` and expects `file` selected with no error. */
async function expectDeepLinkSelects(page: Page, file: NestedAssetFileRef, filePath: string) {
    const { databaseId, assetId, key } = file;
    const fileName = key.split("/").pop()!;

    const errors = collectPageErrors(page);
    await page.goto(
        `/#/databases/${encodeURIComponent(databaseId)}/assets/${encodeURIComponent(
            assetId
        )}?filePath=${encodeURIComponent(filePath)}`,
        { waitUntil: "domcontentloaded" }
    );

    await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible({
        timeout: 60_000,
    });
    const tab = page.getByRole("tab", { name: /file manager/i }).first();
    if ((await tab.count()) && (await tab.getAttribute("aria-selected")) !== "true") {
        await tab.click();
    }

    // The deep link selects the file once the listing lands; a path the tree cannot resolve
    // selects the asset root and shows the "does not exist" alert instead.
    await expect(selectedTreeNode(page)).toHaveText(fileName, { timeout: 60_000 });
    await expect(page.getByText(/does not exist in this asset/i)).toHaveCount(0);

    // The selection writes the tree's own form of the path back into the URL.
    await expect
        .poll(() => {
            const hash = new URL(page.url()).hash;
            return new URLSearchParams(hash.split("?")[1] ?? "").get("filePath");
        })
        .toBe(key);

    expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
}

test.describe("File Manager deep link", () => {
    test("selects a nested file linked without the leading slash", async ({ page }) => {
        const file = await findNestedAssetFile(page, {
            asset: process.env.E2E_FILEMANAGER_ASSET,
        });
        test.skip(!file, "No file inside a folder in this environment (or no API context)");

        await expectDeepLinkSelects(page, file!, file!.key.replace(/^\/+/, ""));
    });

    test("selects a nested file linked by its full stored key", async ({ page }) => {
        const file = await findNestedAssetFile(page, {
            asset: process.env.E2E_FILEMANAGER_ASSET,
        });
        test.skip(!file, "No file inside a folder in this environment (or no API context)");
        test.skip(
            !file!.storedKey,
            "The asset's listing has no root entry to read the root key from"
        );

        // The execution page's input file link writes the stored key with one leading slash.
        await expectDeepLinkSelects(page, file!, `/${file!.storedKey}`);
    });
});
