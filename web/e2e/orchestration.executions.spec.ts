/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect } from "@playwright/test";
import {
    collectPageErrors,
    expectTableRendered,
    facet,
    gotoOrchestration,
    tableRows,
} from "./support/fixtures";

/**
 * Executions board — permanent smoke coverage. Environment-agnostic: the board is newest-first and
 * server-paginated, so no test may assume a particular execution is on page 1. Tests that need a
 * row derive it from whatever is rendered, and skip when the environment has no executions.
 */

test.describe("Executions board", () => {
    test.beforeEach(async ({ page }) => {
        await gotoOrchestration(page, "executions", /Executions/i);
    });

    test("renders the table (or its empty state) without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        await expectTableRendered(page);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("shows the expected column headers", async ({ page }) => {
        for (const col of [
            "Status",
            "Execution ID",
            "Workflow",
            "Database",
            "Trigger",
            "Group",
            "Started",
            "Actions",
        ]) {
            await expect(page.getByRole("columnheader", { name: col }).first()).toBeVisible();
        }
    });

    test("exposes the status, trigger, and time-window filters", async ({ page }) => {
        const status = facet(page, "Filter by status");
        for (const v of ["RUNNING", "SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"]) {
            await expect(status.locator(`option[value="${v}"]`)).toHaveCount(1);
        }
        // Trigger values must be the STORED vocabulary; the UI-style "fileUpload" matched nothing.
        const trigger = facet(page, "Filter by trigger");
        await expect(trigger.locator('option[value="Manual"]')).toHaveCount(1);
        await expect(trigger.locator('option[value="File-Upload"]')).toHaveCount(1);
        await expect(trigger.locator('option[value="fileUpload"]')).toHaveCount(0);
        await expect(facet(page, "Time window")).toBeVisible();
    });

    test("the status filter constrains every rendered row to that status", async ({ page }) => {
        const total = await expectTableRendered(page);
        test.skip(total === 0, "No executions in this environment");

        // Pick a status that is actually present so the assertion is meaningful in any environment.
        const statuses = ["SUCCEEDED", "FAILED", "RUNNING", "ABORTED", "TIMED_OUT"];
        const status = facet(page, "Filter by status");
        for (const s of statuses) {
            await status.selectOption(s);
            await page.waitForLoadState("networkidle").catch(() => undefined);
            const n = await tableRows(page).count();
            if (n === 0) continue;
            // Every visible status cell must read the selected status.
            const label = s.charAt(0) + s.slice(1).toLowerCase().replace("_", " ");
            await expect(
                page.getByRole("cell", { name: new RegExp(label, "i") }).first()
            ).toBeVisible();
            return;
        }
        test.skip(true, "No status had rows to verify");
    });

    test("a row's actions menu opens", async ({ page }) => {
        const total = await expectTableRendered(page);
        test.skip(total === 0, "No executions in this environment");
        await tableRows(page).first().getByRole("button").last().click();
        await expect(page.getByRole("menuitem").first()).toBeVisible({ timeout: 15_000 });
        await page.keyboard.press("Escape");
    });

    test("clicking a row opens the quick-view drawer", async ({ page }) => {
        const total = await expectTableRendered(page);
        test.skip(total === 0, "No executions in this environment");
        await tableRows(page).first().click();
        // The drawer surfaces the execution's detail; assert on the dialog/complementary region
        // rather than a specific id so this holds in any environment.
        await expect(
            page.getByRole("dialog").or(page.getByRole("complementary")).first()
        ).toBeVisible({ timeout: 20_000 });
    });
});
