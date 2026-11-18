/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page } from "@playwright/test";
import { apiContext, openViewerFile, type AssetFileRef } from "./support/fixtures";

/**
 * The file page's Preview toggle shows the file's generated preview, a separate S3 object beside the
 * source file with its own versions. The page must request it without the source file's S3 version
 * id; sending that id made the download API answer "File not found in S3".
 *
 * The subject is derived from the environment (`e2e/CLAUDE.md` Rule 1): the first distributable
 * asset in a bounded scan that holds a file with a preview file. The spec skips with the reason when
 * there is none, and it only reads.
 */

interface PreviewSubject extends AssetFileRef {
    previewKey: string;
    /** A file version that is not the latest, when the listing shows one (`?version=`). */
    olderVersionId?: string;
}

async function findFileWithPreview(
    page: Page,
    needOlderVersion = false
): Promise<PreviewSubject | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const asJson = async (path: string) => {
        const r = await page.request.get(`${ctx.base}${path}`, { headers });
        if (!r.ok()) return null;
        const b = await r.json().catch(() => null);
        return b?.message ?? b ?? null;
    };
    const itemsOf = (body: any): any[] => {
        const items = body?.Items ?? body?.items ?? body ?? [];
        return Array.isArray(items) ? items : [];
    };
    const slash = (k: string) => (k.startsWith("/") ? k : `/${k}`);

    const forced = process.env.E2E_PREVIEW_ASSET; // "<databaseId>/<assetId>"
    const targets: { databaseId: string; assetId: string }[] = [];
    if (forced) {
        const [databaseId, assetId] = forced.split("/");
        if (databaseId && assetId) targets.push({ databaseId, assetId });
    } else {
        for (const db of itemsOf(await asJson("/database")).slice(0, 6)) {
            if (!db?.databaseId) continue;
            const assets = itemsOf(await asJson(`/database/${db.databaseId}/assets`));
            for (const a of assets.slice(0, 50)) {
                if (a?.assetId && a.isDistributable === true) {
                    targets.push({ databaseId: db.databaseId, assetId: a.assetId });
                }
            }
        }
    }

    for (const { databaseId, assetId } of targets) {
        const files = itemsOf(await asJson(`/database/${databaseId}/assets/${assetId}/listFiles`));
        for (const f of files) {
            const relative: string = f?.relativePath ?? "";
            const preview: string = f?.previewFile ?? "";
            if (!relative || f.isFolder || f.isArchived || !preview) continue;
            const subject: PreviewSubject = {
                databaseId,
                assetId,
                key: slash(relative),
                previewKey: slash(preview),
            };
            if (!needOlderVersion) return subject;
            const info = await asJson(
                `/database/${databaseId}/assets/${assetId}/fileInfo?` +
                    `filePath=${encodeURIComponent(f.key ?? relative)}&includeVersions=true`
            );
            const older = (info?.versions ?? []).find((v: any) => v?.versionId && !v.isLatest);
            if (older) return { ...subject, olderVersionId: older.versionId };
        }
    }
    return null;
}

/**
 * Open the file's page and wait for the Preview toggle. The toggle is drawn after the asset lookup
 * settles and has been missing on a first load, so the page is loaded once more before the wait fails.
 */
async function openWithPreviewToggle(page: Page, subject: PreviewSubject, version?: string) {
    const route =
        `/#/databases/${subject.databaseId}/assets/${subject.assetId}` +
        `/file/${encodeURIComponent(subject.key)}` +
        (version ? `?version=${encodeURIComponent(version)}` : "");
    const toggle = page.getByText("Preview", { exact: true }).first();
    for (let attempt = 1; ; attempt++) {
        await page.goto("about:blank");
        await page.goto(route, { waitUntil: "domcontentloaded" });
        const shown = await toggle
            .waitFor({ state: "visible", timeout: 30_000 })
            .then(() => true)
            .catch(() => false);
        if (shown) return toggle;
        if (attempt === 2) {
            throw new Error(`the Preview toggle did not appear after two loads of ${route}`);
        }
    }
}

const NOTICE = /This preview belongs to the latest version of the file/;

function watchDownloads(page: Page) {
    const downloads: { key?: string; versionId?: string; status: number }[] = [];
    page.on("response", async (res) => {
        const req = res.request();
        if (req.method() !== "POST" || !/\/assets\/[^/]+\/download$/.test(res.url())) return;
        const body = req.postDataJSON() ?? {};
        downloads.push({ key: body.key, versionId: body.versionId, status: res.status() });
    });
    return downloads;
}

async function expectPreviewRequest(
    downloads: ReturnType<typeof watchDownloads>,
    previewKey: string
) {
    const previewName = previewKey.split("/").filter(Boolean).pop() as string;
    await expect
        .poll(() => downloads.find((d) => d.key?.endsWith(previewName)), {
            message: `the page requested the preview file ${previewName}; saw ${JSON.stringify(
                downloads
            )}`,
            timeout: 30_000,
        })
        .toBeTruthy();
    return downloads.find((d) => d.key?.endsWith(previewName))!;
}

async function expectImageShown(page: Page) {
    const image = page.locator(".visualizer-container img").first();
    await expect(image).toBeVisible({ timeout: 30_000 });
    await expect
        .poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth), {
            message: "the preview image decoded",
        })
        .toBeGreaterThan(0);
    await expect(page.getByText(/not found in S3|Unable to load/i)).toHaveCount(0);
}

test.describe("File page Preview toggle", () => {
    test("requests the preview file without the source file's version id and shows it", async ({
        page,
    }) => {
        const subject = await findFileWithPreview(page);
        test.skip(!subject, "No distributable asset with a generated preview file here");
        const s = subject!;
        const downloads = watchDownloads(page);

        const toggle = await openWithPreviewToggle(page, s);
        await toggle.click();

        const request = await expectPreviewRequest(downloads, s.previewKey);
        expect(request.versionId, "the source file's version id is not sent").toBeUndefined();
        expect(request.status, "the preview download is answered").toBe(200);
        await expectImageShown(page);
        // The latest version (no ?version=) gets no notice.
        await expect(page.getByText(NOTICE)).toHaveCount(0);
    });

    test("says the preview belongs to the latest version when an older file version is viewed", async ({
        page,
    }) => {
        const subject = await findFileWithPreview(page, true);
        test.skip(!subject?.olderVersionId, "No file with a preview and an older version here");
        const s = subject!;
        const downloads = watchDownloads(page);

        const toggle = await openWithPreviewToggle(page, s, s.olderVersionId);
        await expect(page.getByText(NOTICE), "no notice on the File tab").toHaveCount(0);
        await toggle.click();

        await expect(page.getByText(NOTICE)).toBeVisible({ timeout: 30_000 });
        const request = await expectPreviewRequest(downloads, s.previewKey);
        expect(request.versionId, "the older file version id is not sent").toBeUndefined();
        expect(request.status).toBe(200);
        await expectImageShown(page);

        await page.getByText("File", { exact: true }).first().click();
        await expect(page.getByText(NOTICE)).toHaveCount(0);
    });
});
