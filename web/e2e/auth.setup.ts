/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test as setup, expect } from "@playwright/test";
import fs from "fs";
import path from "path";

/**
 * One-time Cognito login via the Amplify Authenticator UI. Saves storageState so the
 * rest of the suite runs unattended. Credentials come from env — never hardcode:
 *   E2E_USERNAME / E2E_PASSWORD
 *
 * If the account has MFA, run this project headed (`npm run e2e:auth`) and complete the
 * challenge manually the first time; the saved state then drives all specs.
 */
const ADMIN_STATE = "e2e/.auth/admin.json";
// Reuse a still-valid saved session rather than re-logging-in every run. Repeated rapid
// Cognito SRP logins can trip the edge WAF (403). The Cognito idToken lives ~1h; refresh
// the state (delete the file or set E2E_FORCE_LOGIN=1) when it expires.
const STATE_TTL_MS = 45 * 60 * 1000;

setup("authenticate as admin", async ({ page }) => {
    const username = process.env.E2E_USERNAME;
    const password = process.env.E2E_PASSWORD;
    if (!username || !password) {
        throw new Error("Set E2E_USERNAME and E2E_PASSWORD env vars before running auth.setup.ts");
    }

    fs.mkdirSync(path.dirname(ADMIN_STATE), { recursive: true });

    if (!process.env.E2E_FORCE_LOGIN && fs.existsSync(ADMIN_STATE)) {
        const ageMs = Date.now() - fs.statSync(ADMIN_STATE).mtimeMs;
        if (ageMs < STATE_TTL_MS) {
            setup.skip(true, "Reusing recent saved auth state (set E2E_FORCE_LOGIN=1 to refresh)");
            return;
        }
    }

    await page.goto("/", { waitUntil: "domcontentloaded" });

    const userButton = page.getByRole("button", { name: username });
    const userField = page
        .locator('input[name="username"], input[autocomplete="username"], input[type="email"]')
        .first();

    // The app may land already authenticated (persisted session) or on the Cognito
    // Authenticator. Race the two; only fill the form if the login screen shows.
    await Promise.race([
        userButton.waitFor({ state: "visible", timeout: 45_000 }).catch(() => {}),
        userField.waitFor({ state: "visible", timeout: 45_000 }).catch(() => {}),
    ]);

    if (await userField.isVisible().catch(() => false)) {
        await userField.fill(username);
        await page.locator('input[name="password"], input[type="password"]').first().fill(password);
        await page.getByRole("button", { name: /sign in/i }).click();
    }

    // Landing after login: the authenticated app shell renders. The signed-in user button in
    // the top navigation is a single unambiguous marker that login completed.
    await expect(page.getByRole("button", { name: username })).toBeVisible({ timeout: 45_000 });

    await page.context().storageState({ path: ADMIN_STATE });
});
