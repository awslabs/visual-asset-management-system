/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec for tag administration and its database scoping.
 *
 * The page follows the metadata-schema pattern: the scope lives in the route (`/auth/tags/:databaseId`,
 * where the id may be the `GLOBAL` sentinel), the first-load choice is rendered inline so it cannot be
 * dismissed onto an empty page, and changing scope afterwards goes through the shared
 * DatabaseSelectorWithModal.
 *
 * Non-mutating (Rule 2): forms are opened, asserted, dismissed. The mutating lifecycle lives in
 * `tools/smoketest/tag_scope_lifecycle.py` and the ad-hoc spec.
 *
 * Data-independent (Rule 1): GLOBAL always exists, so every assertion here can use it; anything that
 * needs a real database derives it from the selector's own options and skips when there are none.
 */

import { test, expect, Page } from "@playwright/test";
import { collectPageErrors } from "./support/fixtures";

const TAGS_ROOT = "/#/auth/tags";
const TAGS_GLOBAL = "/#/auth/tags/GLOBAL";

async function gotoTagsRoot(page: Page) {
    await page.goto(TAGS_ROOT);
    await expect(page.getByRole("heading", { name: /Tag Management/i }).first()).toBeVisible({
        timeout: 30000,
    });
}

async function gotoGlobalTags(page: Page) {
    await page.goto(TAGS_GLOBAL);
    await expect(page.getByRole("heading", { name: /Tag Management/i }).first()).toBeVisible({
        timeout: 30000,
    });
}

test.describe("first-load database selection", () => {
    test("asks for a database inline and cannot be dismissed onto an empty page", async ({
        page,
    }) => {
        const errors = collectPageErrors(page);
        await gotoTagsRoot(page);

        // Inline, not a modal: there must be no dismiss control that could leave the page blank.
        await expect(page.getByText(/Select .*Database/i).first()).toBeVisible();
        await expect(page.getByRole("dialog")).toHaveCount(0);

        // Escape must not clear the page — the selection is mandatory.
        await page.keyboard.press("Escape");
        await expect(page.getByText(/Select .*Database/i).first()).toBeVisible();

        expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toEqual([]);
    });

    test("offers GLOBAL with its globe marker", async ({ page }) => {
        await gotoTagsRoot(page);

        const select = page.getByTestId("database-select");
        await expect(select).toBeVisible();
        await select.click();

        // The shared DatabaseSelector labels the sentinel "🌐 GLOBAL" — capitalized, matching the rest
        // of the site, with the globe distinguishing it from a real database.
        await expect(page.getByRole("option", { name: /GLOBAL/ })).toBeVisible();
        await page.keyboard.press("Escape");
    });

    test("choosing GLOBAL navigates to the scoped route", async ({ page }) => {
        await gotoTagsRoot(page);

        await page.getByTestId("database-select").click();
        await page
            .getByRole("option", { name: /GLOBAL/ })
            .first()
            .click();

        await expect(page).toHaveURL(/#\/auth\/tags\/GLOBAL/);
        await expect(page.getByRole("heading", { name: /Tag Management/i }).first()).toBeVisible();
    });
});

test.describe("scoped tag administration", () => {
    test("names the scope in the heading and offers Change Database", async ({ page }) => {
        await gotoGlobalTags(page);

        // Title matches the metadata-schema page (no scope in it); the scope is the description.
        await expect(page.getByRole("heading", { name: /^Tag Management$/ }).first()).toBeVisible();
        await expect(page.getByText("🌐 GLOBAL").first()).toBeVisible();
        await expect(
            page.getByRole("button", { name: /Change .*Database/i }).first()
        ).toBeVisible();
    });

    test("Change Database opens a dismissible modal that leaves the page intact", async ({
        page,
    }) => {
        await gotoGlobalTags(page);

        await page
            .getByRole("button", { name: /Change .*Database/i })
            .first()
            .click();
        await expect(page.getByRole("dialog")).toBeVisible();

        // Dismissing here is safe — unlike first load, there is a populated page behind it.
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(page.getByRole("heading", { name: /Tag Management/i }).first()).toBeVisible();
    });

    test("every listed row shows a scope badge", async ({ page }) => {
        await gotoGlobalTags(page);

        const badges = page.getByText(/🌐 GLOBAL|🏢/);
        const count = await badges.count();
        test.skip(count === 0, "No tags or tag types in the GLOBAL scope of this environment");

        // In the GLOBAL scope every row is global; a 🏢 badge here would mean the scope filter leaked.
        for (let i = 0; i < Math.min(count, 10); i++) {
            await expect(badges.nth(i)).toBeVisible();
        }
        await expect(page.getByText("🏢")).toHaveCount(0);
    });
});

test.describe("create form scope is locked to the page", () => {
    test("Scope is shown read-only, not as a second database control", async ({ page }) => {
        await gotoGlobalTags(page);

        const createButton = page.getByRole("button", { name: /^Create Tag$/i }).first();
        test.skip(!(await createButton.count()), "Tag creation not permitted for this user");
        await createButton.click();

        await expect(page.getByText("Scope", { exact: true }).first()).toBeVisible();
        // The badge is the read-only rendering; the old editable scope Select must be gone, so the
        // page's database choice is the only way to pick a scope.
        await expect(page.getByText("🌐 GLOBAL").first()).toBeVisible();
        await expect(page.getByTestId("tag-scope")).toHaveCount(0);

        await page.keyboard.press("Escape");
    });
});

test.describe("the create form offers only in-scope tag types", () => {
    test("a scoped tag can only reference tag types the page itself lists", async ({ page }) => {
        // A tag's type must live in the tag's own scope, so the form may not offer anything beyond
        // what the page is showing. Derived from whatever the environment holds: the page's own
        // tag-type rows are the expected set.
        await gotoGlobalTags(page);

        const createTag = page.getByRole("button", { name: /^Create Tag$/i }).first();
        test.skip(!(await createTag.count()), "Tag creation not permitted for this user");

        // Read the names the page lists. Both tables render their header row immediately while the data
        // is still loading, so waiting on "a row is visible" captures column titles only. Every DATA
        // row carries a scope badge, which is the signal that the listing has actually arrived.
        let listed = "";
        const deadline = Date.now() + 25000;
        while (Date.now() < deadline) {
            const rows = await page.getByRole("row").allInnerTexts();
            if (rows.some((r) => /🌐|🏢/.test(r))) {
                listed = rows.join("\n");
                break;
            }
            await page.waitForTimeout(500);
        }
        test.skip(!listed, "No tags or tag types in the GLOBAL scope of this environment");

        await createTag.click();
        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible({ timeout: 20000 });
        // Scoped to the dialog: the page behind it keeps its own "Create Tag Type" button, which
        // also matches /Tag Type/ and comes first in document order.
        await dialog
            .getByRole("button", { name: /Tag Type/ })
            .first()
            .click();
        const listbox = page.locator('[role="listbox"]:visible').first();
        await expect(listbox).toBeVisible({ timeout: 15000 });
        const offered = (await listbox.innerText())
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean);
        test.skip(!offered.length, "No tag types in the GLOBAL scope of this environment");

        // Every offered type must appear in the GLOBAL listing behind the form. An out-of-scope type
        // here is the defect: the backend rejects it, so the form would offer an unusable choice.
        for (const name of offered.slice(0, 10)) {
            expect(listed, `"${name}" is offered but not listed in this scope`).toContain(name);
        }

        await page.keyboard.press("Escape");
        await page.keyboard.press("Escape");
    });
});

test.describe("switching scope replaces the listing", () => {
    test("a specific database shows none of the GLOBAL entries", async ({ page }) => {
        // Start in GLOBAL so the page holds global rows, then switch — the previous scope's rows must
        // not survive. The list only fetches on mount, so this is the case that regressed.
        await page.goto(TAGS_GLOBAL);
        await expect(page.getByRole("heading", { name: /^Tag Management$/ }).first()).toBeVisible();

        await page
            .getByRole("button", { name: /Change .*Database/i })
            .first()
            .click();
        // The modal holds a Select; its options only exist once that dropdown is opened, and clicking
        // it before the modal settles silently opens nothing — which made this test skip rather than
        // fail, hiding the very regression it guards. Wait for the select, then for its options.
        const select = page.getByTestId("database-select");
        await expect(select).toBeVisible();
        await select.click();

        // Poll for the whole option list. The dropdown renders progressively, so a filtered locator
        // evaluated too early matched a list of one and went stale — which made this test SKIP rather
        // than fail, hiding the regression it guards.
        await expect
            .poll(() => page.getByRole("option").count(), { timeout: 15000 })
            .toBeGreaterThan(1);

        // The selector always lists GLOBAL first when it offers it, so the next entry is a real
        // database — deterministic, unlike filtering by text.
        const dbOption = page.getByRole("option").nth(1);
        const databaseName = (await dbOption.textContent())?.trim() || "";
        test.skip(
            !databaseName || /GLOBAL/.test(databaseName),
            "No database available in this environment"
        );
        await dbOption.click();

        await expect(page.getByText(`Database: ${databaseName}`).first()).toBeVisible();

        // Matches the metadata-schema page: a database scope lists that database only.
        await expect(page.getByText("🌐 GLOBAL")).toHaveCount(0, { timeout: 20000 });

        // ...and the new scope actually loaded. Without this, an empty list would satisfy the
        // assertion above for the wrong reason. A database with no tags of its own is a legitimate
        // state, so this is a skip rather than a failure.
        const ownRows = page.getByText("🏢");
        test.skip(
            (await ownRows.count()) === 0,
            `${databaseName} has no tags or tag types of its own to prove the refetch`
        );
        await expect(ownRows.first()).toBeVisible();
    });
});

test.describe("asset tag picker labels and orders by scope", () => {
    test("every option names its scope and GLOBAL sorts first", async ({ page }) => {
        // Derived from the environment: any asset will do, and the assertion is about the SHAPE of the
        // labels rather than particular tags. An asset resolves tags in its own database plus GLOBAL,
        // and a bare name is ambiguous because two databases may each own that name.
        await page.goto("/#/assets");
        // Every asset row links to #/databases/{db}/assets/{id}, but so does a row's own database
        // (no id) and the nav bar links to a bare #/assets/ — a `.first()` on a partial href match
        // picks one of those and the test skips itself. Match the full shape instead.
        const assetHref = /#\/databases\/[^/]+\/assets\/[^/]+$/;
        let href = "";
        const deadline = Date.now() + 30000;
        while (Date.now() < deadline && !href) {
            const hrefs = await page
                .locator('a[href*="/assets/"]')
                .evaluateAll((els) =>
                    els.map((e) => (e as HTMLAnchorElement).getAttribute("href") || "")
                );
            href = hrefs.find((h) => assetHref.test(h)) || "";
            if (!href) await page.waitForTimeout(500);
        }
        test.skip(!href, "No asset in this environment");

        await page.goto(href.startsWith("#") ? `/${href}` : `/#${href}`);
        // The asset page loads its detail pane asynchronously, so an immediate count() is 0 and the
        // test would skip itself while the control is merely still on its way.
        const edit = page.getByRole("button", { name: /^Edit$/ }).first();
        await edit.waitFor({ state: "visible", timeout: 40000 }).catch(() => {});
        test.skip(!(await edit.count()), "Asset editing not permitted for this user");
        await edit.click();
        await expect(page.getByRole("dialog")).toBeVisible({ timeout: 30000 });

        await page.getByRole("button", { name: /Tags/ }).first().click();
        const listbox = page.locator('[role="listbox"]:visible').first();
        await expect(listbox).toBeVisible({ timeout: 15000 });
        const lines = (await listbox.innerText())
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean);
        test.skip(lines.length === 0, "No tags available to this asset");

        // Group headers and options alike carry a parenthesised scope.
        for (const line of lines.slice(0, 15)) {
            expect(line, `"${line}" does not name a scope`).toMatch(/\((GLOBAL|[^)]+)\)/);
        }

        // GLOBAL first: no GLOBAL entry may appear after a database-scoped one.
        const scoped = lines.findIndex((l) => /\(/.test(l) && !/\(GLOBAL\)/.test(l));
        if (scoped !== -1) {
            const globalAfter = lines.slice(scoped + 1).find((l) => /\(GLOBAL\)/.test(l));
            expect(
                globalAfter,
                `GLOBAL entry "${globalAfter}" sorted below a database entry`
            ).toBeUndefined();
        }

        await page.keyboard.press("Escape");
        await page.keyboard.press("Escape");
    });
});
