/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { defineConfig, devices } from "@playwright/test";

/**
 * Phase 8 end-to-end suite for the pipeline/workflow/execution overhaul.
 *
 * The target deployment is named by E2E_BASE_URL and has no default: the suite authenticates
 * and (for ad-hoc specs) mutates data, so a run must never fall back to some other stack.
 * Point it at the deployed app's origin (same-origin API, no CORS/proxy) or at
 * http://localhost:3001 for a local dev server.
 *
 * Auth: Cognito SRP login can't run fully headless, so the "setup" project performs
 * the login once via the Authenticator UI and saves storageState; every other spec
 * reuses it. Provide E2E_USERNAME / E2E_PASSWORD via env (never hardcode).
 */
const BASE_URL = process.env.E2E_BASE_URL;
if (!BASE_URL) {
    throw new Error(
        "E2E_BASE_URL is not set. Set it to the VAMS deployment to test before running the " +
            "e2e suite, e.g. E2E_BASE_URL=https://<your-app-host> npm run e2e " +
            "(or http://localhost:3001 against a local dev server)."
    );
}
const ADMIN_STATE = "e2e/.auth/admin.json";

export default defineConfig({
    testDir: "./e2e",
    fullyParallel: false,
    forbidOnly: !!process.env.CI,
    // One retry absorbs transient SPA load slowness against the live remote app.
    retries: 1,
    workers: 1,
    reporter: [["list"], ["html", { open: "never", outputFolder: "e2e/.report" }]],
    timeout: 90_000,
    expect: { timeout: 15_000 },
    use: {
        baseURL: BASE_URL,
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
        video: "retain-on-failure",
        actionTimeout: 20_000,
    },
    projects: [
        {
            name: "setup",
            testMatch: /auth\.setup\.ts/,
        },
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"], storageState: ADMIN_STATE },
            dependencies: ["setup"],
        },
    ],
});
