/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the Workstation Admin page renders, offers its tabs in order, and each tab's panel renders
 * — against any sandbox, mutating nothing.
 *
 * The page exists only where the EDH Workstations add-on is deployed, so every test skips (naming the
 * feature list in its reason) when `EDH_WORKSTATIONS` is absent from the app's cached config. Every
 * panel is gated on its own GET route, so a tab may show the no-access notice instead of its table;
 * both are valid. Registering, stopping, deactivating or deleting changes the EDH cluster and belongs
 * in an untracked ad-hoc spec (e2e/CLAUDE.md Rule 2); nothing here opens an action. The health line
 * is not asserted on: it renders only for a caller with the health route and reads the live cluster.
 */

import { test, expect } from "@playwright/test";
import { cachedFeaturesEnabled, collectPageErrors, expectTableRendered } from "./support/fixtures";
import { gotoEdh } from "./support/edhFixtures";

const FLAG = "EDH_WORKSTATIONS";
const AMI_RECIPES_FLAG = "EDH_AMI_RECIPES";
const NO_ACCESS = /You do not have access to/i;

// What each panel shows when it has no table rows. Capacity is tiles plus the profiles table; the
// tiles are its render signal.
const PANEL_SIGNAL: Record<string, string> = {
    Sessions: "No sessions are running",
    Capacity: "Total sessions|No capacity data returned",
    Images: "No images are registered",
    "AMI Recipes": "No AMI recipes are defined",
    Settings: "Lease hours|No settings available",
};

/** A panel's rows, its own empty or content signal, or the no-access notice — all valid. */
const panelRendered = (label: string) =>
    new RegExp(`${PANEL_SIGNAL[label]}|${NO_ACCESS.source}`, "i");

test.describe("EDH Workstation Admin page", () => {
    let features: string[] = [];

    test.beforeEach(async ({ page }) => {
        // A skip must name the precondition it is missing, or a run that skipped everything reads the
        // same as a run that passed everything.
        features = await cachedFeaturesEnabled(page);
        const enabled = features.includes(FLAG);
        console.log(`[edh-admin] featuresEnabled=${JSON.stringify(features)} enabled=${enabled}`);
        test.skip(
            !enabled,
            `The EDH Workstations add-on is not enabled (features: ${features.join(",")})`
        );
        await gotoEdh(page, "admin", "Workstation Admin");
    });

    test("renders without a client-side crash, with its tabs in order", async ({ page }) => {
        const errors = collectPageErrors(page);
        const tablist = page.getByRole("tablist", { name: "Workstation administration" });
        await expect(tablist).toBeVisible();
        // The AMI Recipes tab is present only where the deployment builds AMIs.
        await expect(tablist.getByRole("tab")).toHaveText([
            "Sessions",
            "Capacity",
            "Images",
            ...(features.includes(AMI_RECIPES_FLAG) ? ["AMI Recipes"] : []),
            "Settings",
        ]);
        // The page opens on the first tab whose panel the caller may read — Sessions for any caller
        // who may read it — or on Sessions with its no-access notice when no panel is readable.
        await expect(page.getByRole("tabpanel")).not.toContainText("Loading permissions");
        const opened = tablist.getByRole("tab", { selected: true });
        await expect(opened).toHaveCount(1);
        await expectTableRendered(page, panelRendered(((await opened.textContent()) ?? "").trim()));
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("switches to the Sessions, Capacity and Images tabs and renders each panel", async ({
        page,
    }) => {
        const errors = collectPageErrors(page);
        for (const label of ["Sessions", "Capacity", "Images"]) {
            const tab = page.getByRole("tab", { name: label });
            await tab.click();
            await expect(tab).toHaveAttribute("aria-selected", "true");
            await expectTableRendered(page, panelRendered(label));
        }
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });
});
