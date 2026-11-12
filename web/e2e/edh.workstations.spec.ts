/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the EDH Workstations page renders, exposes its controls, and its launch wizard opens and
 * dismisses — against any sandbox, mutating nothing.
 *
 * The page exists only where the EDH Workstations add-on is deployed, so every test skips (naming the
 * feature list in its reason) when `EDH_WORKSTATIONS` is absent from the app's cached config. Launching,
 * stopping or deleting a desktop creates or destroys EC2 capacity and belongs in an untracked ad-hoc
 * spec (e2e/CLAUDE.md Rule 2); here the wizard is opened on its Image step and closed with Escape. The
 * Launch control exists only on the Review step, so nothing in this file can launch.
 */

import { test, expect } from "@playwright/test";
import {
    cachedFeaturesEnabled,
    collectPageErrors,
    expectTableRendered,
    orchestrationRoot,
} from "./support/fixtures";
import { gotoEdh } from "./support/edhFixtures";

const FLAG = "EDH_WORKSTATIONS";

test.describe("EDH Workstations page", () => {
    test.beforeEach(async ({ page }) => {
        // A skip must name the precondition it is missing, or a run that skipped everything reads the
        // same as a run that passed everything.
        const features = await cachedFeaturesEnabled(page);
        const enabled = features.includes(FLAG);
        console.log(`[edh] featuresEnabled=${JSON.stringify(features)} enabled=${enabled}`);
        test.skip(
            !enabled,
            `The EDH Workstations add-on is not enabled (features: ${features.join(",")})`
        );
        await gotoEdh(page, "workstations", "Workstations");
    });

    test("renders without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        // Either rows or the empty state — both are valid, depending on the environment.
        await expectTableRendered(page, /no workstations yet/i);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("exposes the refresh control", async ({ page }) => {
        await expect(
            orchestrationRoot(page).getByRole("button", { name: "Refresh" })
        ).toBeVisible();
    });

    test("opens the launch wizard on its Image step and dismisses it without launching", async ({
        page,
    }) => {
        // The empty state repeats the toolbar's Launch control; either one opens the wizard.
        const launch = orchestrationRoot(page)
            .getByRole("button", { name: "Launch Workstation" })
            .first();
        test.skip(
            (await launch.count()) === 0,
            "The signed-in user may not launch workstations (POST /addon/edh/workstations is not allowed)"
        );
        await launch.click();

        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible();
        await expect(dialog.getByRole("heading", { name: "Launch workstation" })).toBeVisible();
        // Image is the first step; the wizard opens there and Launch is not in the DOM yet.
        await expect(dialog.getByText("Image", { exact: true }).first()).toBeVisible();
        await expect(dialog.getByRole("button", { name: "Launch" })).toHaveCount(0);

        await page.keyboard.press("Escape");
        await expect(dialog).toBeHidden();
    });
});
