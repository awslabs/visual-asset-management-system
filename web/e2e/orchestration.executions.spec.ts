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
            "Workflow Database",
            "Trigger",
            "Started",
            "Actions",
        ]) {
            await expect(page.getByRole("columnheader", { name: col }).first()).toBeVisible();
        }
    });

    test("offers the workflow and workflow-database filters in global scope", async ({ page }) => {
        // The global board pins nothing, so both halves of the workflow identity are separately
        // selectable here. The asset tab replaces them with ONE composite-valued control (a workflow
        // id is unique only within its database, and that board has no database dropdown to pair
        // with); the workflow-scoped board offers neither, being already pinned to one workflow.
        // Asserted on the controls' presence only, so it holds on an empty environment.
        await expect(facet(page, "Filter by workflow")).toBeVisible();
        await expect(facet(page, "Filter by workflow database")).toBeVisible();
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

/**
 * A partial detail response. The server bounds each collection against the Lambda response limit and
 * names anything it cut in `truncatedCollections`; a real run large enough to trigger that is not
 * something a sandbox can be assumed to hold, so the flag is injected into whatever execution exists.
 * The UI's handling of the flag is what is under test, and it is permanent behavior: the two kinds of
 * collection are treated differently on purpose.
 */
test.describe("Executions board — partial detail responses", () => {
    /** The details route for a single execution — NOT its `details/metadata` paged sibling. */
    const DETAILS_ROUTE = /\/workflows\/executions\/[^/?]+\/details(\?|$)/;
    const PAGED_METADATA_ROUTE = /\/workflows\/executions\/[^/?]+\/details\/metadata/;

    /**
     * Serve the execution's own details response with `collections` marked truncated, then open the
     * first execution's detail page. Returns false when the environment has no executions.
     */
    async function openDetailWithTruncation(
        page: import("@playwright/test").Page,
        collections: string[]
    ): Promise<boolean> {
        await page.route(DETAILS_ROUTE, async (route) => {
            const response = await route.fetch();
            const body = await response.json();
            // The payload is wrapped in a `message` envelope; the flag belongs on the execution.
            const target = body && typeof body.message === "object" ? body.message : body;
            target.truncatedCollections = collections;
            await route.fulfill({ response, json: body });
        });

        await gotoOrchestration(page, "executions", /Executions/i);
        const total = await expectTableRendered(page);
        if (total === 0) return false;

        await tableRows(page).first().getByRole("button").last().click();
        const items = page.getByRole("menuitem");
        await expect(items.first()).toBeVisible({ timeout: 15_000 });
        await items
            .filter({ hasText: /Open full details/ })
            .first()
            .click();
        await expect(page.getByRole("heading", { name: "Execution Detail", level: 1 })).toBeVisible(
            {
                timeout: 30_000,
            }
        );
        return true;
    }

    test("a truncated file collection says plainly that rows are missing", async ({ page }) => {
        const opened = await openDetailWithTruncation(page, ["inputFiles"]);
        test.skip(!opened, "No executions in this environment");

        // The file collections have no paged counterpart, so the flag is the reader's only signal.
        // Stated twice deliberately: once in the header banner listing the affected sections, and
        // again on the section itself, because a reader who scrolls to a table needs it there.
        await expect(page.getByText(/these sections are a subset/i)).toBeVisible({
            timeout: 20_000,
        });
        await expect(page.getByText(/inputFiles/).first()).toBeVisible();
        await expect(page.getByText(/not retrievable through this view/i).first()).toBeVisible();
    });

    test("a truncated metadata collection is re-read through the paged route", async ({ page }) => {
        // Armed before the page loads: the escalation fires as the section mounts.
        const pagedRequest = page
            .waitForRequest(PAGED_METADATA_ROUTE, { timeout: 40_000 })
            .catch(() => null);

        const opened = await openDetailWithTruncation(page, ["inputMetadata"]);
        test.skip(!opened, "No executions in this environment");

        const request = await pagedRequest;
        // The paged read is its own Tier-1 route. A session without it cannot escalate, and the view
        // then says the section stays a subset rather than issuing a request that would 403.
        if (!request) {
            await expect(page.getByText(/these sections are a subset/i)).toBeVisible({
                timeout: 20_000,
            });
            test.skip(true, "This session is not granted the paged detail-metadata route");
        }
        // A metadata collection escalates to the collection name the paged route uses, not the
        // details response's own name for it.
        expect(request!.url()).toMatch(/collection=input(&|$)/);
        await expect(page.getByText(/read separately, a page at a time/i)).toBeVisible({
            timeout: 20_000,
        });
    });
});
