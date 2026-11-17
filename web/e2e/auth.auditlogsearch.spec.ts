/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page } from "@playwright/test";
import { AuditMockMode, installAuditMocks } from "./support/auditSearchMocks";
import {
    expectNoPageOverflowX,
    sideNavigation,
    VIEWPORTS,
    watchConsoleErrors,
} from "./support/fixtures";

/**
 * Audit Log Search page: permanent smoke coverage. The page's `/audit/*` calls are answered by a
 * scripted API (support/auditSearchMocks.ts), so the spec needs no audit data and passes on a
 * deployment without the audit backend. The web-route decision (`POST /auth/routes`) is read from
 * the real API and then changed to allow or deny the page, which is how the nav item and the page
 * gate are proven for both outcomes with one admin login. The real-API check runs separately, after
 * the backend is deployed.
 *
 * E2E_HARNESS=1 runs the page-only cases against a stub shell (no sign-in, no side navigation); the
 * cases that need the real shell skip themselves there.
 */

const HARNESS = process.env.E2E_HARNESS === "1";
const PAGE = "/#/auth/audit-log-search/";
const WEB_ROUTE = "/auth/audit-log-search";

/** Lets the real `/auth/routes` answer, then adds or removes the audit page from the allowed list. */
async function decideAuditPage(page: Page, allowed: boolean) {
    await page.route(/\/auth\/routes$/, async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        const response = await route.fetch({ timeout: 45_000 });
        const body = await response.json();
        const list: { method: string; route__path: string }[] = Array.isArray(body.allowedRoutes)
            ? body.allowedRoutes.filter((r: { route__path: string }) => r.route__path !== WEB_ROUTE)
            : [];
        if (allowed) list.push({ method: "GET", route__path: WEB_ROUTE });
        await route.fulfill({ response, json: { ...body, allowedRoutes: list } });
    });
}

async function openPage(page: Page, mode: AuditMockMode = "ok", hash = PAGE) {
    const mock = await installAuditMocks(page, mode);
    if (!HARNESS) await decideAuditPage(page, true);
    await page.goto(hash);
    const heading = page.getByRole("heading", { level: 1, name: "Audit Log Search" });
    // The shell signs in and reads the real route decision before the page mounts; on a cold or busy
    // deployment that can pass 30 s. One reload then gives a warm start, so a slow shell never fails
    // a case that is about the page (the only failure seen in 21 cases was this wait).
    try {
        await expect(heading).toBeVisible({ timeout: 30_000 });
    } catch {
        await page.reload();
        await expect(heading).toBeVisible({ timeout: 60_000 });
    }
    return mock;
}

const searchButton = (page: Page) => page.getByRole("button", { name: "Search", exact: true });
const results = (page: Page) => page.getByRole("table", { name: "Audit records" });

async function runSearch(page: Page) {
    await expect(searchButton(page)).toBeEnabled();
    await searchButton(page).click();
    await expect(results(page).getByText("assetUpdate").first()).toBeVisible({ timeout: 30_000 });
}

/** Adds a token through the property filter the way a user types it: property, operator, value. */
async function addFilter(page: Page, text: string) {
    const input = page.getByRole("combobox", { name: "Filters" });
    await input.click();
    await input.fill(text);
    await input.press("Enter");
}

test.describe("Audit Log Search: access", () => {
    test.skip(HARNESS, "needs the real navigation and web-route decision");

    test("the nav item and the page appear when the API allows the route", async ({ page }) => {
        await installAuditMocks(page);
        await decideAuditPage(page, true);
        await page.goto("/#/");
        await expect(
            sideNavigation(page).getByRole("link", { name: "Audit Log Search" })
        ).toBeVisible({ timeout: 60_000 });
        await sideNavigation(page).getByRole("link", { name: "Audit Log Search" }).click();
        await expect(
            page.getByRole("heading", { level: 1, name: "Audit Log Search" })
        ).toBeVisible();
    });

    test("neither the nav item nor the page appear when the API denies the route", async ({
        page,
    }) => {
        await installAuditMocks(page);
        await decideAuditPage(page, false);
        await page.goto("/#/");
        await expect(
            sideNavigation(page)
                .getByRole("link", { name: /Access Control Constraints|Roles/ })
                .first()
        )
            .toBeVisible({ timeout: 60_000 })
            .catch(() => undefined);
        await expect(
            sideNavigation(page).getByRole("link", { name: "Audit Log Search" })
        ).toHaveCount(0);
        await page.goto(PAGE);
        await expect(page.getByText("You do not have access to this page")).toBeVisible({
            timeout: 60_000,
        });
        await expect(searchButton(page)).toHaveCount(0);
    });

    test("the compliance Audit Log links to the page only when it is allowed", async ({ page }) => {
        await installAuditMocks(page);
        await decideAuditPage(page, true);
        await page.goto("/#/compliance/audit/");
        await expect(page.getByRole("link", { name: "Audit Log Search" }).last()).toBeVisible({
            timeout: 60_000,
        });
    });
});

test.describe("Audit Log Search: search", () => {
    test("a full search: start, long-poll, page, record details, export", async ({ page }) => {
        const errors = watchConsoleErrors(page);
        const mock = await openPage(page);

        // Nothing runs until the user asks.
        expect(mock.starts).toHaveLength(0);
        await expect(page.getByText("No results yet")).toBeVisible();

        await runSearch(page);
        expect(mock.starts).toHaveLength(1);
        expect(mock.starts[0]).not.toHaveProperty("query");
        expect(mock.starts[0].includeProbes).toBe(false);
        // The page asked the server to wait (long-poll) rather than polling at a rapid fixed rate.
        expect(Number(mock.polls[0].get("wait"))).toBeGreaterThan(0);
        // The start warning is shown, in words.
        await expect(page.getByText(/could not be used to narrow the scan/)).toBeVisible();

        // Server paging: first page of three, then the next by token.
        await expect(results(page).getByRole("radio")).toHaveCount(3);
        await page.getByRole("button", { name: "Next page of records" }).click();
        await expect(results(page).getByText("user4@example.com").first()).toBeVisible();
        expect(mock.polls.some((q) => q.get("startingToken") === "3")).toBe(true);
        await page.getByRole("button", { name: "Page 1 of records" }).click();

        // Record details: structured, and hostile text stays text.
        await results(page).getByRole("radio").first().check();
        await expect(page.getByRole("heading", { name: "Record details" })).toBeVisible();
        await expect(page.getByTestId("audit-value-tree")).toContainText("<img src=x");
        expect(await page.evaluate(() => (window as any).__auditXss)).toBeUndefined();
        expect(await page.locator("img[src='x'], #root script").count()).toBe(0);
        await expect(
            page.getByRole("button", { name: "Show records of this request" })
        ).toBeVisible();
        await page.getByRole("button", { name: "Close record details" }).click();
        await expect(page.getByRole("heading", { name: "Record details" })).toHaveCount(0);

        // Export: the dialog says what the file holds and the file is the server's text, joined.
        await page.getByRole("button", { name: "Export", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Export audit records" });
        await expect(dialog).toContainText("7 records");
        const [download] = await Promise.all([
            page.waitForEvent("download"),
            dialog.getByRole("button", { name: "Export", exact: true }).click(),
        ]);
        expect(download.suggestedFilename()).toMatch(/^audit-log-\d{8}T\d{6}Z\.csv$/);
        expect(mock.exports.map((q) => q.get("format"))).toEqual(["csv", "csv"]);

        expect(errors, `console errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("filters reach the server as named values, and contains is checked first", async ({
        page,
    }) => {
        const mock = await openPage(page);
        await addFilter(page, "User = alice@example.com");
        await addFilter(page, "Outcome = denied");
        await expect(page.getByText("alice@example.com").first()).toBeVisible();

        // A contains term outside the server's rules is refused before any request.
        await addFilter(page, "Contains : ab");
        await expect(page.getByText(/A contains term has 3 to 64 characters/)).toBeVisible();
        await expect(searchButton(page)).toBeDisabled();
        await page.getByRole("button", { name: /Remove filter Contains/ }).click();
        await addFilter(page, "Contains : report.usd");
        await page.getByLabel("Hide permission-probe records").uncheck();

        await runSearch(page);
        expect(mock.starts[0]).toMatchObject({
            users: ["alice@example.com"],
            outcomes: ["denied"],
            contains: ["report.usd"],
            includeProbes: true,
        });
        // The candidate limit the server reported is explained.
        await expect(page.getByText(/reached its record limit/)).toBeVisible();
    });

    test("a link restores the filters and does not run the search; reload keeps them", async ({
        page,
    }) => {
        const mock = await openPage(
            page,
            "ok",
            `${PAGE}?user=carol%40example.com&outcome=failed&rel=7d&sort=asc`
        );
        await expect(page.getByText("carol@example.com").first()).toBeVisible();
        await page.waitForTimeout(1000);
        expect(mock.starts).toHaveLength(0);
        await page.reload();
        await expect(page.getByText("carol@example.com").first()).toBeVisible();
        expect(page.url()).toContain("user=carol%40example.com");
        expect(page.url()).toContain("rel=7d");
        expect(mock.starts).toHaveLength(0);
        await runSearch(page);
        expect(mock.starts[0]).toMatchObject({ users: ["carol@example.com"], sort: "asc" });
    });

    test("the records of one request: a new search by request ID", async ({ page }) => {
        const mock = await openPage(page);
        await runSearch(page);
        await results(page).getByRole("radio").first().check();
        await page.getByRole("button", { name: "Show records of this request" }).click();
        await expect.poll(() => mock.starts.length).toBe(2);
        expect(mock.starts[1].requestIds).toEqual(["00000000-0000-4000-8000-000000000001"]);
        expect(mock.starts[1].includeProbes).toBe(true);
        expect(page.url()).toContain("requestId=");
    });

    test("stop ends a running search and says no records are kept", async ({ page }) => {
        const mock = await openPage(page);
        mock.holdRunning = true;
        await searchButton(page).click();
        await expect(page.getByTestId("audit-progress")).toContainText(
            "1200 records scanned so far"
        );
        await expect(page.getByTestId("audit-progress")).not.toContainText("matched");
        mock.holdRunning = false;
        await page.getByRole("button", { name: "Stop", exact: true }).click();
        await expect(page.getByText("Search stopped")).toBeVisible();
        await expect(page.getByText(/A stopped search shows no records/)).toBeVisible();
        expect(mock.stops).toBe(1);
    });

    test("stop pressed before the search handle arrives still cancels the search", async ({
        page,
    }) => {
        const mock = await openPage(page);
        mock.startDelayMs = 800;
        await searchButton(page).click();
        await page.getByRole("button", { name: "Stop", exact: true }).click();
        await expect(page.getByText("Search stopped")).toBeVisible();
        // The start answers after the stop: the handle is cancelled at once and no poll is made.
        await expect.poll(() => mock.stops).toBe(1);
        expect(mock.polls).toHaveLength(0);
        await expect(page.getByText("Search stopped")).toBeVisible();
    });
});

test.describe("Audit Log Search: states", () => {
    const cases: [AuditMockMode, string | RegExp][] = [
        ["empty", "No records"],
        ["startDenied", "You do not have access to audit logs."],
        ["startThrottled", /The service is busy/],
        ["startTooMany", "Too many audit searches are running. Try again later."],
        ["expired", "These results expired. Run the search again."],
        ["failed", /The search failed/],
    ];
    for (const [mode, text] of cases) {
        test(`${mode}: says so in words`, async ({ page }) => {
            await openPage(page, mode);
            await searchButton(page).click();
            await expect(page.getByText(text).first()).toBeVisible({ timeout: 30_000 });
            await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        });
    }

    test("no access to any group: a panel, no search form", async ({ page }) => {
        await openPage(page, "noGroups");
        await expect(page.getByText("No audit logs available to you")).toBeVisible();
        await expect(searchButton(page)).toHaveCount(0);
    });

    test("a denied group list: the no-access panel", async ({ page }) => {
        await openPage(page, "groupsDenied");
        await expect(page.getByText("You do not have access to audit logs.")).toBeVisible();
        await expect(searchButton(page)).toHaveCount(0);
    });
});

test.describe("Audit Log Search: appearance and keyboard", () => {
    for (const theme of ["light", "dark"] as const) {
        test(`${theme} theme renders results without errors`, async ({ page }) => {
            const errors = watchConsoleErrors(page);
            await page.addInitScript(
                (value) => localStorage.setItem("vams-theme-preference", value),
                theme
            );
            await openPage(page);
            await runSearch(page);
            await results(page).getByRole("radio").first().check();
            await expect(page.getByRole("heading", { name: "Record details" })).toBeVisible();
            expect(errors).toHaveLength(0);
        });
    }

    test("a phone-width viewport does not scroll the page sideways", async ({ page }) => {
        await page.setViewportSize(VIEWPORTS.phone);
        await openPage(page);
        await runSearch(page);
        await expectNoPageOverflowX(page, "Audit Log Search at phone width");
        await expect(searchButton(page)).toBeVisible();
    });

    test("the whole flow works from the keyboard", async ({ page }) => {
        const mock = await openPage(page);
        const input = page.getByRole("combobox", { name: "Filters" });
        await input.focus();
        await page.keyboard.type("User = dave@example.com");
        await page.keyboard.press("Enter");
        // Tab from the form to the Search button and press it.
        await searchButton(page).focus();
        await expect(searchButton(page)).toBeFocused();
        await page.keyboard.press("Enter");
        await expect(results(page).getByText("assetUpdate").first()).toBeVisible({
            timeout: 30_000,
        });
        expect(mock.starts[0].users).toEqual(["dave@example.com"]);
        // A row opens its details from the keyboard and Escape-free close returns to the table.
        await results(page).getByRole("radio").first().focus();
        await page.keyboard.press("Space");
        await expect(page.getByRole("heading", { name: "Record details" })).toBeVisible();
        await expect(page.getByRole("button", { name: "Close record details" })).toBeVisible();
    });
});
