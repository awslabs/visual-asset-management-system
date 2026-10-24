/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, Page } from "@playwright/test";

/**
 * Playwright helpers for the EDH Workstations add-on pages, beside the core harness in fixtures.ts.
 * The same environment-agnostic contract applies: nothing here assumes a workstation, image or job
 * exists.
 */

/**
 * Navigate to an EDH add-on page and wait for its heading. Same contract as `gotoOrchestration`: the
 * heading is the load signal, never the data, so an empty list is a valid state.
 */
export async function gotoEdh(
    page: Page,
    route: "workstations" | "admin",
    heading: RegExp | string
): Promise<void> {
    await page.goto(`/#/edh/${route}/`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({
        timeout: 60_000,
    });
    // The list issues its first request after mount; settle before asserting on content.
    await page.waitForLoadState("networkidle").catch(() => undefined);
}
