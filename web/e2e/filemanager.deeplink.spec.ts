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
    expectNoPageOverflowX,
    expectReachable,
    findNestedAssetFile,
    openFileManager,
    selectedTreeNode,
    VIEWPORTS,
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

/**
 * The asset page at phone and desktop widths. On a phone the header columns stack and the File
 * Manager's panes stack with no separator, so the toolbar (Upload Files, Export, Automation) is on
 * screen and a focused toolbar button is visible. From xs up the panes sit side by side and the
 * separator still resizes by keyboard. Only opens and reads the page.
 */
test.describe("Asset page layout", () => {
    async function openSubject(page: Page): Promise<boolean> {
        const file = await findNestedAssetFile(page, {
            asset: process.env.E2E_FILEMANAGER_ASSET,
        });
        if (!file) return false;
        await openFileManager(page, file.databaseId, file.assetId);
        return true;
    }

    /** Header labels whose boxes must not overlap one another. */
    const HEADER_LABELS = [/ Id$/, /^Type \/ Distributable$/, /^Description$/];

    test.describe("phone", () => {
        test.use({ viewport: VIEWPORTS.phone });

        test("stacks the header and the File Manager panes and keeps the toolbar reachable", async ({
            page,
        }) => {
            test.skip(!(await openSubject(page)), "No asset with files in this environment");
            await expectNoPageOverflowX(page, "asset page");

            const boxes = [];
            for (const label of HEADER_LABELS) {
                const box = await page.getByText(label).first().boundingBox();
                if (box) boxes.push(box);
            }
            for (let i = 0; i < boxes.length; i++) {
                for (let j = i + 1; j < boxes.length; j++) {
                    const [a, b] = [boxes[i], boxes[j]];
                    const overlap =
                        a.x < b.x + b.width &&
                        b.x < a.x + a.width &&
                        a.y < b.y + b.height &&
                        b.y < a.y + a.height;
                    expect(overlap, "two asset header labels overlap").toBe(false);
                }
            }

            await expect(page.getByRole("separator", { name: "Resize panels" })).toHaveCount(0);
            const upload = page.getByRole("button", { name: "Upload Files" }).first();
            await expectReachable(upload, "Upload Files");
            await upload.focus();
            await expect(upload).toBeFocused();
            // Measured as focus leaves it, with no scroll first: the toolbar wraps inside its pane.
            const header = page.locator(".file-info-header").first();
            const fit = await header.evaluate((el) => el.scrollWidth - el.clientWidth);
            expect(fit, "the File Manager toolbar is wider than its pane").toBeLessThanOrEqual(1);
            const box = await upload.boundingBox();
            expect(box, "the focused Upload Files has no box").not.toBeNull();
            expect(
                box!.x,
                "the focused Upload Files starts left of the viewport"
            ).toBeGreaterThanOrEqual(0);
            expect(
                box!.x + box!.width,
                "the focused Upload Files ends right of the viewport"
            ).toBeLessThanOrEqual(VIEWPORTS.phone.width + 0.5);
            for (const name of [/^Export/, /^Automation/]) {
                const control = page.getByRole("button", { name }).first();
                if (await control.count()) await expectReachable(control, String(name));
            }
        });

        test("opens Export for a selected file without the tree pane in the way", async ({
            page,
        }) => {
            const file = await findNestedAssetFile(page, {
                asset: process.env.E2E_FILEMANAGER_ASSET,
            });
            test.skip(!file, "No asset with files in this environment");
            await expectDeepLinkSelects(page, file!, file!.key);

            const exportButton = page.getByRole("button", { name: "Export" }).first();
            await expectReachable(exportButton, "Export");
            // A plain click fails when another element sits over the button, as the side-by-side
            // tree pane did at this width.
            await exportButton.click({ timeout: 10_000 });
            await expect(page.getByRole("menuitem").first()).toBeVisible();
            await page.keyboard.press("Escape");
        });
    });

    test.describe("desktop", () => {
        test.use({ viewport: VIEWPORTS.desktop });

        test("keeps the panes side by side with a keyboard-resizable separator", async ({
            page,
        }) => {
            test.skip(!(await openSubject(page)), "No asset with files in this environment");
            await expectNoPageOverflowX(page, "asset page");
            const separator = page.getByRole("separator", { name: "Resize panels" }).first();
            await expect(separator).toBeVisible();
            const before = Number(await separator.getAttribute("aria-valuenow"));
            await separator.focus();
            await page.keyboard.press("ArrowRight");
            await expect(separator).toHaveAttribute("aria-valuenow", String(before + 20));
            await page.keyboard.press("ArrowLeft");
            await expect(separator).toHaveAttribute("aria-valuenow", String(before));
            await expectReachable(
                page.getByRole("button", { name: "Upload Files" }).first(),
                "Upload Files"
            );
        });
    });
});
