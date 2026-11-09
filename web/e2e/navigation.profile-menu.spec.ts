/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect } from "@playwright/test";
import {
    collectPageErrors,
    expectReachable,
    openProfileMenu,
    profileMenuLink,
    profileMenuTrigger,
    sideNavigation,
    VIEWPORTS,
} from "./support/fixtures";

/**
 * The top-bar profile menu holds the user's own pages from the navigation registry
 * (`placement: "profile"`), then Sign out. API Key Management lives there, not in the side
 * navigation, which has no "User" section.
 *
 * Read-only and environment-agnostic: it opens the menu, follows the API keys link and asserts
 * the page heading; Sign out is checked for presence and never chosen. A role without the API keys
 * web route sees Sign out only, so the link assertions skip with a reason for such a login.
 */

const API_KEYS_LINK = "#/auth/api-keys/?mode=user";

async function gotoHome(page: import("@playwright/test").Page) {
    await page.goto("/#/", { waitUntil: "domcontentloaded" });
    await expect(
        page.locator("#headerWrapper").getByRole("button", { name: "Settings" })
    ).toBeVisible({ timeout: 60_000 });
    // The page checks fire after mount; settle so the menu holds its final items.
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

test.describe("Profile menu", () => {
    test.use({ viewport: VIEWPORTS.laptop });

    test("opens by keyboard and lists Sign out last; Escape returns focus", async ({ page }) => {
        const errors = collectPageErrors(page);
        await gotoHome(page);

        expect(await openProfileMenu(page)).toBe("menu");
        const items = page.getByRole("menuitem");
        await expect(items.last()).toHaveText("Sign out");

        await page.keyboard.press("Escape");
        await expect(items.filter({ hasText: "Sign out" })).toHaveCount(0);
        await expect(await profileMenuTrigger(page)).toBeFocused();
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("API Key Management is a link that opens the page", async ({ page }) => {
        await gotoHome(page);
        await openProfileMenu(page);

        const link = profileMenuLink(page, API_KEYS_LINK);
        const offered = await link
            .waitFor({ state: "visible", timeout: 15_000 })
            .then(() => true)
            .catch(() => false);
        test.skip(!offered, "This login's role does not allow the API keys page");

        await expect(link).toHaveText(/API Key Management/);
        await link.click();

        await expect(page).toHaveURL(/#\/auth\/api-keys\/\?mode=user$/);
        await expect(
            page.getByRole("heading", { name: "API Key Management", level: 1 })
        ).toBeVisible({ timeout: 60_000 });
        await expect(page).toHaveTitle(/API Key Management$/);
    });

    test("the side navigation has no User section and no API Key Management link", async ({
        page,
    }) => {
        await gotoHome(page);
        const nav = sideNavigation(page);
        await expect(nav.first()).toBeVisible({ timeout: 60_000 });

        await expect(nav.getByText("User", { exact: true })).toHaveCount(0);
        await expect(nav.getByRole("link", { name: "API Key Management" })).toHaveCount(0);
    });
});

test.describe("Profile menu at the phone width", () => {
    test.use({ viewport: VIEWPORTS.phone });

    test("is reachable through the More menu", async ({ page }) => {
        await gotoHome(page);

        const reachedBy = await openProfileMenu(page);
        const signOut = page.getByText("Sign out", { exact: true }).filter({ visible: true });
        await expect(signOut).toBeVisible();
        await expectReachable(signOut, `Sign out (reached by ${reachedBy})`);

        const link = profileMenuLink(page, API_KEYS_LINK);
        if (await link.count()) {
            await expectReachable(link.first(), "API Key Management");
        }
        // Never sign out: dismiss the menu.
        await page.keyboard.press("Escape");
    });
});
