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
}

async function findFileWithPreview(page: Page): Promise<PreviewSubject | null> {
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
            for (const a of assets.slice(0, 15)) {
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
            return { databaseId, assetId, key: slash(relative), previewKey: slash(preview) };
        }
    }
    return null;
}

test.describe("File page Preview toggle", () => {
    test("requests the preview file without the source file's version id and shows it", async ({
        page,
    }) => {
        const subject = await findFileWithPreview(page);
        test.skip(!subject, "No distributable asset with a generated preview file here");
        const s = subject!;

        const downloads: { key?: string; versionId?: string; status: number }[] = [];
        page.on("response", async (res) => {
            const req = res.request();
            if (req.method() !== "POST" || !/\/assets\/[^/]+\/download$/.test(res.url())) return;
            const body = req.postDataJSON() ?? {};
            downloads.push({ key: body.key, versionId: body.versionId, status: res.status() });
        });

        await openViewerFile(page, s);
        const toggle = page.getByText("Preview", { exact: true }).first();
        await expect(toggle, "the Preview toggle is offered for a file with a preview").toBeVisible(
            {
                timeout: 30_000,
            }
        );
        await toggle.click();

        const previewName = s.previewKey.split("/").filter(Boolean).pop() as string;
        await expect
            .poll(() => downloads.find((d) => d.key?.endsWith(previewName)), {
                message: `the page requested the preview file ${previewName}; saw ${JSON.stringify(
                    downloads
                )}`,
                timeout: 30_000,
            })
            .toBeTruthy();
        const request = downloads.find((d) => d.key?.endsWith(previewName))!;

        expect(request.versionId, "the source file's version id is not sent").toBeUndefined();
        expect(request.status, "the preview download is answered").toBe(200);

        const image = page.locator(".visualizer-container img").first();
        await expect(image).toBeVisible({ timeout: 30_000 });
        await expect
            .poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth), {
                message: "the preview image decoded",
            })
            .toBeGreaterThan(0);
        await expect(page.getByText(/not found in S3|Unable to load/i)).toHaveCount(0);
    });
});
