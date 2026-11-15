/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page, Response } from "@playwright/test";
import { apiContext, collectPageErrors } from "./support/fixtures";

/**
 * An archived asset's page renders read-only.
 *
 * The subject is the first archived asset the signed-in user can list (or E2E_ARCHIVED_ASSET,
 * written `databaseId/assetId`); the spec skips when none exists. It mutates nothing: the
 * Unarchive and Delete permanently dialogs are opened and cancelled.
 *
 * A constrained role runs only when E2E_CONSTRAINED_USERNAME / E2E_CONSTRAINED_PASSWORD are
 * set. E2E_CONSTRAINED_EXPECT names its outcome: `view` (its constraints match the archived
 * record, so it sees the read-only page) or `denied` (they do not, so it sees the access-refused
 * message and no panels). Unset accepts either, but never a half-rendered page.
 */

interface ArchivedAssetRef {
    databaseId: string;
    assetId: string;
}

// The asset's own read routes; each is asked with showArchived=true on an archived asset.
const READ_ROUTE =
    /\/(listFiles|fileInfo|getVersions|getVersion\/|metadata(\/file)?(\?|$)|asset-links(\?|$)|comments\/assets\/)/;

async function findArchivedAsset(page: Page): Promise<ArchivedAssetRef | null> {
    const named = process.env.E2E_ARCHIVED_ASSET;
    if (named) {
        const [databaseId, assetId] = named.split("/");
        expect(
            Boolean(databaseId && assetId),
            "write E2E_ARCHIVED_ASSET as <databaseId>/<assetId>"
        ).toBe(true);
        return { databaseId, assetId };
    }
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const itemsOf = async (path: string): Promise<any[]> => {
        const r = await page.request.get(`${ctx.base}${path}`, { headers });
        if (!r.ok()) return [];
        const b = await r.json().catch(() => null);
        const body = b?.message ?? b ?? {};
        const items = body?.Items ?? body?.items ?? body;
        return Array.isArray(items) ? items : [];
    };
    for (const db of (await itemsOf("/database")).slice(0, 10)) {
        const databaseId: string | undefined = db?.databaseId;
        if (!databaseId) continue;
        const assets = await itemsOf(`/database/${databaseId}/assets?showArchived=true`);
        const archived = assets.find(
            (a) =>
                a?.assetId &&
                (a.status === "archived" || String(a.databaseId ?? "").endsWith("#deleted"))
        );
        if (archived) return { databaseId, assetId: archived.assetId };
    }
    return null;
}

/** Every response from the asset's read routes, recorded while the page loads. */
function recordReadResponses(page: Page): Response[] {
    const seen: Response[] = [];
    page.on("response", (r) => {
        if (READ_ROUTE.test(r.url())) seen.push(r);
    });
    return seen;
}

async function openAssetPage(page: Page, ref: ArchivedAssetRef): Promise<void> {
    await page.goto(`/#/databases/${ref.databaseId}/assets/${ref.assetId}`, {
        waitUntil: "domcontentloaded",
    });
}

/** The archived banner, the read-only header actions, and no failing read. */
async function expectReadOnlyArchivedPage(
    page: Page,
    responses: Response[],
    errors: string[]
): Promise<void> {
    await expect(page.getByText(/ is archived$/).first()).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText(/are read-only\. Unarchive the /).first()).toBeVisible();

    // Live-only actions are gone from the header.
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Subscribe(d)?$/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Delete", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Upload Files" })).toHaveCount(0);

    // Let the panels settle, then check each read the page made.
    await page.waitForLoadState("networkidle").catch(() => {});
    const failed = responses
        .filter((r) => r.request().method() === "GET" && r.status() >= 400)
        .map((r) => `${r.status()} ${r.url()}`);
    expect(failed, `failing reads: ${failed.join(" | ")}`).toHaveLength(0);
    const withoutFlag = responses
        .filter((r) => r.request().method() === "GET" && !/[?&]showArchived=true/.test(r.url()))
        .map((r) => r.url());
    expect(withoutFlag, `reads without showArchived: ${withoutFlag.join(" | ")}`).toHaveLength(0);
    expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
}

/** Open a header action's dialog and cancel it, so nothing changes. */
async function openAndCancel(page: Page, button: string, dialogTitle: RegExp): Promise<void> {
    const action = page.getByRole("button", { name: button, exact: true });
    if ((await action.count()) === 0 || (await action.isDisabled())) return;
    await action.click();
    const dialog = page.getByRole("dialog").filter({ hasText: dialogTitle });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
}

test.describe("archived asset page (admin)", () => {
    for (const viewport of [
        { width: 1440, height: 900 },
        { width: 390, height: 844 },
    ]) {
        test(`renders read-only at ${viewport.width}px`, async ({ page }) => {
            await page.setViewportSize(viewport);
            const ref = await findArchivedAsset(page);
            test.skip(!ref, "No archived asset in this environment (or no API context)");

            const errors = collectPageErrors(page);
            const responses = recordReadResponses(page);
            await openAssetPage(page, ref!);
            await expectReadOnlyArchivedPage(page, responses, errors);

            await expect(
                page.getByRole("button", { name: "Unarchive", exact: true })
            ).toBeVisible();
            await expect(
                page.getByRole("button", { name: "Delete permanently", exact: true })
            ).toBeVisible();
            await openAndCancel(page, "Unarchive", /^Unarchive /);
            await openAndCancel(page, "Delete permanently", /delete/i);
        });
    }
});

test.describe("archived asset page (constrained role)", () => {
    const username = process.env.E2E_CONSTRAINED_USERNAME;
    const password = process.env.E2E_CONSTRAINED_PASSWORD;
    const expectation = process.env.E2E_CONSTRAINED_EXPECT;
    // A fresh session, signed in as the constrained user rather than reusing the admin state.
    test.use({ storageState: { cookies: [], origins: [] } });

    test("shows the read-only page or the unavailable message, never a half page", async ({
        browser,
        page,
    }) => {
        test.skip(
            !username || !password,
            "Set E2E_CONSTRAINED_USERNAME and E2E_CONSTRAINED_PASSWORD to run the constrained role"
        );

        // The subject comes from the admin's listing, which sees every archived asset.
        const admin = await browser.newContext({ storageState: "e2e/.auth/admin.json" });
        const ref = await findArchivedAsset(await admin.newPage());
        await admin.close();
        test.skip(!ref, "No archived asset in this environment (or no API context)");

        await page.goto("/", { waitUntil: "domcontentloaded" });
        const userField = page
            .locator('input[name="username"], input[autocomplete="username"], input[type="email"]')
            .first();
        await userField.waitFor({ state: "visible", timeout: 45_000 });
        await userField.fill(username!);
        await page
            .locator('input[name="password"], input[type="password"]')
            .first()
            .fill(password!);
        await page.getByRole("button", { name: /sign in/i }).click();
        await expect(page.getByRole("button", { name: username! })).toBeVisible({
            timeout: 45_000,
        });

        const errors = collectPageErrors(page);
        const responses = recordReadResponses(page);
        await openAssetPage(page, ref!);

        const banner = page.getByText(/ is archived$/).first();
        const unavailable = page.getByRole("heading", { name: / Information Unavailable$/ });
        await expect(banner.or(unavailable)).toBeVisible({ timeout: 45_000 });

        if (await banner.isVisible()) {
            expect(expectation, "the constrained role saw the archived page").not.toBe("denied");
            await expectReadOnlyArchivedPage(page, responses, errors);
        } else {
            expect(expectation, "the constrained role was refused the archived asset").not.toBe(
                "view"
            );
            // The asset GET answers 403, which the page names as a refusal rather than a failure
            await expect(
                page.getByText(/Access denied\. Your role does not allow you to view this /)
            ).toBeVisible();
            // Refused at the asset itself: no panel went on to read its files or metadata.
            await page.waitForLoadState("networkidle").catch(() => {});
            const panelReads = responses.map((r) => r.url());
            expect(panelReads, `panel reads after refusal: ${panelReads.join(" | ")}`).toHaveLength(
                0
            );
            expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
        }
    });
});
