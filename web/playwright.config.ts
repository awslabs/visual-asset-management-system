/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { defineConfig, devices } from "@playwright/test";

/**
 * Phase 8 end-to-end suite for the pipeline/workflow/execution overhaul.
 *
 * Targets the deployed prod14 app by default (same-origin API, no CORS/proxy).
 * Override with E2E_BASE_URL (e.g. http://localhost:3001 for a local dev server).
 *
 * Auth: Cognito SRP login can't run fully headless, so the "setup" project performs
 * the login once via the Authenticator UI and saves storageState; every other spec
 * reuses it. Provide E2E_USERNAME / E2E_PASSWORD via env (never hardcode).
 */
const BASE_URL = process.env.E2E_BASE_URL || "https://vams5.scheurik.people.aws.dev";
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
