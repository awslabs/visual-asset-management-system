/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page } from "@playwright/test";
import {
    apiContext,
    collectPageErrors,
    featureSwitches,
    findAssetsWithFiles,
    openFileManager,
    selectTreePath,
    signInAs,
} from "./support/fixtures";

/**
 * Download as Zip: the global "My Download Queue" page, the asset's "Download Queue" tab, and the file manager's
 * Export > Download as Zip entry, for a deployment with the ZIPDOWNLOADS switch on or off (the spec
 * reads which one it is from the app and asserts that side).
 *
 * Mutates nothing by default: the create dialog is opened and dismissed. With E2E_ZIP_CREATE=1 the
 * success-link check creates one job in the file manager and deletes it again. The ownership check
 * needs a job of the admin user. It uses the newest one the admin already has, or, only when
 * E2E_ZIP_CREATE=1, creates one on the first distributable asset with a file and deletes it again.
 * The second user signs in with E2E_SECOND_USERNAME / E2E_SECOND_PASSWORD; the test skips without
 * them.
 */

const ZIP_SWITCH = "ZIPDOWNLOADS";
const DISABLED = "Zip downloads are not enabled for this deployment";

async function zipEnabled(page: Page): Promise<boolean> {
    return (await featureSwitches(page)).includes(ZIP_SWITCH);
}

/**
 * Open a hash route as its own document load, then wait for the app shell. Changing the hash of a
 * page that is still booting (featureSwitches loads "/" first) lets the app's start-up land on Home
 * instead (#1125 D1); a fresh load of the deep link is what a user's bookmark or new tab does.
 */
async function openDeepLink(page: Page, hashRoute: string): Promise<void> {
    await page.goto("about:blank");
    await page.goto(`/#${hashRoute}`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await expect(page).toHaveURL(
        new RegExp(`#${hashRoute.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}`),
        { timeout: 45_000 }
    );
}

interface ApiCaller {
    get: (path: string) => Promise<{ status: number; body: any }>;
    post: (path: string, data?: any) => Promise<{ status: number; body: any }>;
    del: (path: string) => Promise<{ status: number; body: any }>;
}

async function apiCaller(page: Page): Promise<ApiCaller | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const wrap = async (r: any) => ({
        status: r.status(),
        body: await r.json().catch(() => null),
    });
    return {
        get: async (path) => wrap(await page.request.get(`${ctx.base}${path}`, { headers })),
        post: async (path, data) =>
            wrap(await page.request.post(`${ctx.base}${path}`, { headers, data: data ?? {} })),
        del: async (path) => wrap(await page.request.delete(`${ctx.base}${path}`, { headers })),
    };
}

/** The first asset the user can list that is live but not distributable, with one top-level file. */
async function findNonDistributableAsset(
    page: Page
): Promise<{ databaseId: string; assetId: string; key: string } | null> {
    const api = await apiCaller(page);
    if (!api) return null;
    const itemsOf = (body: any): any[] => {
        const b = body?.message ?? body ?? {};
        const items = b?.Items ?? b?.items ?? b;
        return Array.isArray(items) ? items : [];
    };
    for (const db of itemsOf((await api.get("/database")).body).slice(0, 8)) {
        const databaseId = db?.databaseId;
        if (!databaseId) continue;
        const assets = itemsOf((await api.get(`/database/${databaseId}/assets`)).body);
        for (const asset of assets.slice(0, 30)) {
            if (!asset?.assetId || asset.isDistributable === true || asset.status === "archived") {
                continue;
            }
            const files = itemsOf(
                (
                    await api.get(
                        `/database/${databaseId}/assets/${asset.assetId}/listFiles?basic=true`
                    )
                ).body
            );
            const file = files.find(
                (f) =>
                    f?.relativePath &&
                    !f.isFolder &&
                    !f.isArchived &&
                    f.relativePath.replace(/^\/+/, "").split("/").length === 1
            );
            if (file) {
                const key = file.relativePath.startsWith("/")
                    ? file.relativePath
                    : `/${file.relativePath}`;
                return { databaseId, assetId: asset.assetId, key };
            }
        }
    }
    return null;
}

/** The first live, distributable asset with an archived top-level file, and its tree root's name. */
async function findAssetWithArchivedFile(page: Page): Promise<{
    databaseId: string;
    assetId: string;
    rootName: string;
    archivedKey: string;
} | null> {
    const api = await apiCaller(page);
    if (!api) return null;
    const itemsOf = (body: any): any[] => {
        const b = body?.message ?? body ?? {};
        const items = b?.Items ?? b?.items ?? b;
        return Array.isArray(items) ? items : [];
    };
    for (const db of itemsOf((await api.get("/database")).body).slice(0, 8)) {
        const databaseId = db?.databaseId;
        if (!databaseId) continue;
        const assets = itemsOf((await api.get(`/database/${databaseId}/assets`)).body);
        for (const asset of assets.slice(0, 30)) {
            if (!asset?.assetId || asset.isDistributable !== true || asset.status === "archived") {
                continue;
            }
            const files = itemsOf(
                (
                    await api.get(
                        `/database/${databaseId}/assets/${asset.assetId}/listFiles?basic=true&includeArchived=true`
                    )
                ).body
            );
            const archived = files.find(
                (f) =>
                    f?.relativePath &&
                    !f.isFolder &&
                    f.isArchived === true &&
                    f.relativePath.replace(/^\/+/, "").split("/").length === 1
            );
            if (archived) {
                const archivedKey = archived.relativePath.startsWith("/")
                    ? archived.relativePath
                    : `/${archived.relativePath}`;
                return {
                    databaseId,
                    assetId: asset.assetId,
                    rootName: asset.assetName || asset.assetId,
                    archivedKey,
                };
            }
        }
    }
    return null;
}

test.describe("Download as Zip", () => {
    test("the global My Download Queue page renders, or is absent with the feature off", async ({
        page,
    }) => {
        const errors = collectPageErrors(page);
        const enabled = await zipEnabled(page);
        await openDeepLink(page, "/zipdownloads");

        if (enabled) {
            // A role the web route check does not allow /zipdownloads sees the access page instead,
            // whatever the feature switch says; nothing else on the page can be checked then.
            const heading = page.getByRole("heading", { level: 1, name: "My Download Queue" });
            const noAccess = page.getByText("You do not have access to this page");
            await expect(heading.or(noAccess).first()).toBeVisible({ timeout: 45_000 });
            if (await noAccess.isVisible()) {
                await expect(page.getByRole("link", { name: "My Download Queue" })).toHaveCount(0);
                test.skip(true, "This login's role does not allow the My Download Queue page");
            }
            await expect(heading).toBeVisible();
            // Rows or the empty state: both are valid in any environment. The empty state is
            // rendered inside a table row, so with no jobs both sides match; .first() keeps the
            // locator strict-mode safe (D15).
            const rows = page.locator("table tbody tr");
            const empty = page.getByText("No download jobs");
            await expect(rows.first().or(empty).first()).toBeVisible({ timeout: 45_000 });
            await expect(
                page.getByRole("link", { name: "My Download Queue" }).first()
            ).toBeVisible();
            // The search and the refresh button sit together in the table's filter row.
            const search = page.getByRole("searchbox", { name: "Search download jobs" });
            const refresh = page.getByRole("button", { name: "Refresh download queue" });
            await expect(search).toBeVisible();
            await expect(refresh).toBeVisible();
            await search.fill("no-such-download-job");
            await expect(
                page.getByText("No matching download jobs").or(empty).first()
            ).toBeVisible();
            await search.fill("");
        } else {
            // The page states the feature is off (or the role has no access to the page at all),
            // and the side navigation does not offer it.
            await expect(
                page
                    .getByText("The Download Queue is not available")
                    .or(page.getByText("You do not have access to this page"))
            ).toBeVisible({ timeout: 45_000 });
            await expect(page.getByRole("link", { name: "My Download Queue" })).toHaveCount(0);
            const api = await apiCaller(page);
            if (api) {
                const res = await api.get("/download/zip");
                expect([403, 404]).toContain(res.status);
                if (res.status === 404) expect(JSON.stringify(res.body)).toContain(DISABLED);
            }
        }
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("the file manager offers Download as Zip only with the feature on", async ({ page }) => {
        const enabled = await zipEnabled(page);
        const found = await findAssetsWithFiles(page, 1);
        test.skip(!found || found.length === 0, "No distributable asset with a file here");
        const ref = found![0];
        const errors = collectPageErrors(page);

        await openFileManager(page, ref.databaseId, ref.assetId);
        await selectTreePath(page, ref.key);
        const exportButton = page.getByRole("button", { name: "Export" }).first();
        await expect(exportButton).toBeVisible({ timeout: 30_000 });
        await exportButton.click();
        const zipItem = page.getByRole("menuitem", { name: /Download as Zip/ });

        if (!enabled) {
            await expect(page.getByRole("menuitem", { name: /Download File/ })).toBeVisible();
            await expect(zipItem).toHaveCount(0);
            await expect(page.getByRole("tab", { name: "Download Queue" })).toHaveCount(0);
            return;
        }

        await expect(zipItem).toBeVisible();
        if ((await zipItem.getAttribute("aria-disabled")) === "true") {
            // The role may not create zip downloads: the entry stays listed with its reason.
            await expect(page.getByText(/does not allow creating zip downloads/)).toBeVisible();
        } else {
            await zipItem.click();
            const dialog = page.getByRole("dialog").filter({ hasText: "Download as Zip" });
            await expect(dialog).toBeVisible();
            await expect(dialog.getByText("1 file")).toBeVisible();
            await expect(dialog.getByRole("radio", { name: /Current files/ })).toBeChecked();
            await expect(dialog.getByRole("radio", { name: /ZIP \(\.zip\)/ })).toBeChecked();
            await expect(dialog.getByLabel("Archive name")).toHaveValue(/\.zip$/);
            await expect(dialog.getByText(/for 7 days/)).toBeVisible();
            await dialog.getByRole("button", { name: "Cancel" }).click(); // never create
            await expect(dialog).toBeHidden();
        }

        // The asset's own tab renders rows or its empty state.
        await page.getByRole("tab", { name: "Download Queue" }).click();
        await expect(
            page
                .locator("table tbody tr")
                .first()
                .or(page.getByText("No download jobs"))
                .or(page.getByText("Your role does not allow listing download jobs."))
                .first() // the empty state sits inside a table row (D15)
        ).toBeVisible({ timeout: 45_000 });
        // Show all users is offered only when the API's route listing allows the all-users route;
        // when it is, the toggle sits right-aligned in the filter row (right of the search box),
        // defaults to My jobs, and the all-users view lists every user's jobs with a Requested by
        // column.
        const scopeToggle = page.getByRole("toolbar", { name: "Whose download jobs to show" });
        if ((await scopeToggle.count()) > 0) {
            await expect(scopeToggle.getByRole("button", { name: "My jobs" })).toHaveAttribute(
                "aria-pressed",
                "true"
            );
            const searchBox = await page
                .getByRole("searchbox", { name: "Search download jobs" })
                .boundingBox();
            const toggleBox = await scopeToggle.boundingBox();
            expect(searchBox && toggleBox && toggleBox.x > searchBox.x + searchBox.width).toBe(
                true
            );
            await scopeToggle.getByRole("button", { name: "Show all users" }).click();
            await expect(
                page
                    .getByRole("columnheader", { name: "Requested by" })
                    .or(page.getByText("No download jobs"))
                    .first()
            ).toBeVisible({ timeout: 45_000 });
        }
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("the asset page opens on its Download Queue tab from a ?tab=zipDownloads link", async ({
        page,
    }) => {
        // The link a new zip job's success message follows (Mutates nothing).
        test.skip(!(await zipEnabled(page)), "The deployment has Download as Zip off");
        const found = await findAssetsWithFiles(page, 1);
        test.skip(!found || found.length === 0, "No distributable asset with a file here");
        const ref = found![0];
        const errors = collectPageErrors(page);
        await openDeepLink(
            page,
            `/databases/${ref.databaseId}/assets/${ref.assetId}?tab=zipDownloads`
        );
        await expect(page.getByRole("tab", { name: "Download Queue" })).toHaveAttribute(
            "aria-selected",
            "true",
            { timeout: 45_000 }
        );
        await expect(
            page
                .getByRole("searchbox", { name: "Search download jobs" })
                .or(page.getByText("Your role does not allow listing download jobs."))
                .first()
        ).toBeVisible({ timeout: 45_000 });
        // Picking another tab drops ?tab= back to the File Manager default and keeps the page.
        await page.getByRole("tab", { name: /^File Manager/ }).click();
        await expect(page).not.toHaveURL(/[?&]tab=/);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("a created zip job links to this asset's Download Queue tab, not My Download Queue", async ({
        page,
    }) => {
        test.skip(!(await zipEnabled(page)), "The deployment has Download as Zip off");
        test.skip(process.env.E2E_ZIP_CREATE !== "1", "Set E2E_ZIP_CREATE=1 to create a zip job");
        const found = await findAssetsWithFiles(page, 1);
        test.skip(!found || found.length === 0, "No distributable asset with a file here");
        const ref = found![0];
        const admin = await apiCaller(page);
        test.skip(!admin, "No API context for the user");
        const errors = collectPageErrors(page);

        await openFileManager(page, ref.databaseId, ref.assetId);
        await selectTreePath(page, ref.key);
        await page.getByRole("button", { name: "Export" }).first().click();
        const zipItem = page.getByRole("menuitem", { name: /Download as Zip/ });
        test.skip(
            (await zipItem.getAttribute("aria-disabled")) === "true",
            "The role may not create zip downloads"
        );
        await zipItem.click();
        const dialog = page.getByRole("dialog").filter({ hasText: "Download as Zip" });
        const archiveName = await dialog.getByLabel("Archive name").inputValue();
        await dialog.getByRole("button", { name: "Create zip download" }).click();
        await expect(page.getByText(`${archiveName} is being prepared.`)).toBeVisible({
            timeout: 30_000,
        });
        try {
            await expect(page.getByRole("button", { name: /My Download Queue/ })).toHaveCount(0);
            await page.getByRole("button", { name: "View Download Queue" }).click();
            // Same asset page, now on its Download Queue tab, with the new job listed.
            await expect(page).toHaveURL(
                new RegExp(
                    `#/databases/${ref.databaseId}/assets/${ref.assetId}\\?.*tab=zipDownloads`
                )
            );
            await expect(page).not.toHaveURL(/#\/zipdownloads/);
            await expect(page.getByRole("tab", { name: "Download Queue" })).toHaveAttribute(
                "aria-selected",
                "true"
            );
            await expect(page.getByText(archiveName, { exact: true })).toBeVisible({
                timeout: 45_000,
            });
            expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
        } finally {
            // Remove the job again (cancel while it runs, delete once it has finished).
            const list = await admin!.get(
                `/database/${ref.databaseId}/assets/${ref.assetId}/download/zip?maxItems=20`
            );
            const job = (list.body?.Items ?? []).find(
                (j: any) => j.archiveFileName === archiveName
            );
            if (job) {
                const jobPath = `/database/${ref.databaseId}/assets/${ref.assetId}/download/zip/${job.requestId}`;
                await admin!.del(jobPath);
                await admin!.del(jobPath);
            }
        }
    });

    test("a selection with an archived file is refused in the dialog, as the API refuses it", async ({
        page,
    }) => {
        // #1125 M2: the dialog names the archived item and offers no Create, instead of leaving it out.
        test.skip(!(await zipEnabled(page)), "The deployment has Download as Zip off");
        const ref = await findAssetWithArchivedFile(page);
        test.skip(!ref, "No distributable asset with an archived top-level file here");
        const errors = collectPageErrors(page);

        await openFileManager(page, ref!.databaseId, ref!.assetId);
        await page.getByText("Show archived files").click();
        // A selection with a folder (here the asset root) offers Export > Download as Zip.
        await selectTreePath(page, ref!.rootName);
        await selectTreePath(page, ref!.archivedKey, { modifiers: ["Control"] });
        const exportButton = page.getByRole("button", { name: "Export" }).first();
        await expect(exportButton).toBeVisible({ timeout: 30_000 });
        await exportButton.click();
        const zipItem = page.getByRole("menuitem", { name: /Download as Zip/ });
        test.skip(
            (await zipItem.getAttribute("aria-disabled")) === "true",
            "The role may not create zip downloads"
        );
        await zipItem.click();
        const dialog = page.getByRole("dialog").filter({ hasText: "Download as Zip" });
        await expect(dialog.getByText("This selection can't be downloaded as a zip")).toBeVisible();
        await expect(dialog.getByRole("list", { name: "Archived or deleted items" })).toContainText(
            ref!.archivedKey
        );
        await expect(dialog.getByRole("button", { name: "Create zip download" })).toBeDisabled();
        await expect(dialog.getByText(/left out of the archive/)).toHaveCount(0);
        await dialog.getByRole("button", { name: "Cancel" }).click(); // never create
        await expect(dialog).toBeHidden();

        // The API refuses the same item, naming it.
        const api = await apiCaller(page);
        const res = await api!.post(
            `/database/${ref!.databaseId}/assets/${ref!.assetId}/download/zip`,
            { selection: [{ type: "file", relativePath: ref!.archivedKey }] }
        );
        expect(res.status).toBeGreaterThanOrEqual(400);
        expect(res.status).toBeLessThan(500);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("a non-distributable asset offers no Export and no Download as Zip", async ({ page }) => {
        const ref = await findNonDistributableAsset(page);
        test.skip(!ref, "No live, non-distributable asset with a file here");
        await openFileManager(page, ref!.databaseId, ref!.assetId);
        await selectTreePath(page, ref!.key);
        await page.waitForLoadState("networkidle").catch(() => undefined);
        await expect(page.getByRole("button", { name: "Export" })).toHaveCount(0);
        await expect(page.getByRole("menuitem", { name: /Download as Zip/ })).toHaveCount(0);
        // The API refuses it as well, whatever the client shows.
        if (await zipEnabled(page)) {
            const api = await apiCaller(page);
            const res = await api!.post(
                `/database/${ref!.databaseId}/assets/${ref!.assetId}/download/zip`,
                { selection: [{ type: "file", relativePath: ref!.key }] }
            );
            expect(res.status).toBeGreaterThanOrEqual(400);
            expect(res.status).toBeLessThan(500);
        }
    });
});

test.describe("Download as Zip (second user)", () => {
    const username = process.env.E2E_SECOND_USERNAME;
    const password = process.env.E2E_SECOND_PASSWORD;

    test("another user cannot see, read or download the admin's zip download", async ({
        browser,
        page,
    }) => {
        test.skip(
            !username || !password,
            "Set E2E_SECOND_USERNAME and E2E_SECOND_PASSWORD to run the ownership check"
        );
        test.skip(!(await zipEnabled(page)), "The deployment has Download as Zip off");

        const admin = await apiCaller(page);
        test.skip(!admin, "No API context for the admin user");
        let job = ((await admin!.get("/download/zip?maxItems=10")).body?.Items ?? [])[0];
        let created = false;
        if (!job && process.env.E2E_ZIP_CREATE === "1") {
            const found = await findAssetsWithFiles(page, 1);
            if (found && found.length) {
                const ref = found[0];
                const res = await admin!.post(
                    `/database/${ref.databaseId}/assets/${ref.assetId}/download/zip`,
                    { selection: [{ type: "file", relativePath: ref.key }], archiveFormat: "zip" }
                );
                if (res.status === 200 && res.body?.requestId) {
                    job = res.body;
                    created = true;
                }
            }
        }
        test.skip(!job, "The admin has no zip download (set E2E_ZIP_CREATE=1 to create one)");

        const jobPath = `/database/${job.databaseId}/assets/${job.assetId}/download/zip/${job.requestId}`;
        const other = await browser.newContext({ storageState: { cookies: [], origins: [] } });
        try {
            const otherPage = await other.newPage();
            await signInAs(otherPage, username!, password!);
            const second = await apiCaller(otherPage);
            expect(second, "API context for the second user").toBeTruthy();

            // Reading, downloading or deleting it by id answers as if it does not exist (or the
            // role may not call the route at all); it never comes back.
            for (const res of [
                await second!.get(jobPath),
                await second!.post(`${jobPath}/url`),
                await second!.del(jobPath),
            ]) {
                expect([403, 404], JSON.stringify(res.body)).toContain(res.status);
                expect(JSON.stringify(res.body ?? "")).not.toContain(job.archiveFileName);
            }
            const list = await second!.get("/download/zip?maxItems=1000");
            const ids = (list.body?.Items ?? []).map((j: any) => j.requestId);
            expect(ids).not.toContain(job.requestId);

            // The page does not show it either.
            await openDeepLink(otherPage, "/zipdownloads");
            await expect(
                otherPage
                    .getByRole("heading", { level: 1, name: "My Download Queue" })
                    .or(otherPage.getByText("You do not have access to this page"))
            ).toBeVisible({ timeout: 45_000 });
            await otherPage.waitForLoadState("networkidle").catch(() => undefined);
            await expect(otherPage.getByText(job.archiveFileName, { exact: true })).toHaveCount(0);

            // The admin still has it: the second user's DELETE changed nothing.
            expect((await admin!.get(jobPath)).status).toBe(200);
        } finally {
            await other.close();
            if (created) await admin!.del(jobPath).catch(() => undefined);
            // A cancelled job still exists; a second DELETE removes it.
            if (created) await admin!.del(jobPath).catch(() => undefined);
        }
    });
});
