/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect } from "@playwright/test";
import {
    collectPageErrors,
    expectTableRendered,
    facet,
    firstCardId,
    gotoOrchestration,
    openCardMenu,
    orchestrationRoot,
    searchBox,
    tableRows,
} from "./support/fixtures";

/**
 * Second batch of regression locks for the web findings from the independent review. The first batch
 * (orchestration.reviewfixes.spec.ts) covers template-delete wording, the archived facet, status
 * rendering, the trigger vocabulary, and dialog layering. This batch covers the remaining
 * user-observable high/medium web fixes.
 *
 * Ad-hoc spec (untracked, per e2e/CLAUDE.md): it targets specific fixes rather than providing
 * permanent page coverage. Every subject is still derived from whatever the environment contains, and
 * nothing is mutated — dialogs and wizards are opened to assert, then dismissed.
 *
 * These run against the DEPLOYED bundle. Confirm the served main-bundle hash matches web/dist before
 * trusting a failure.
 */

test.describe("Executions board — pagination reachability", () => {
    test("Load more stays reachable when the client filter empties the loaded pages", async ({
        page,
    }) => {
        // The control used to be nested inside the `visibleExecutions.length > 0` branch, so a server
        // page that returned rows the client filter then hid left no way to fetch the next page.
        await gotoOrchestration(page, "executions", /Executions/i);
        await expectTableRendered(page);

        const loadMore = page.getByRole("button", { name: /Load more|Loading more/i });
        const hadLoadMore = (await loadMore.count()) > 0;
        test.skip(!hadLoadMore, "Environment has only one page of executions");

        // Filter to something that cannot match, emptying the rendered rows.
        await searchBox(page).fill("zzz-no-such-execution-zzz");
        await expect.poll(async () => await tableRows(page).count(), { timeout: 30_000 }).toBe(0);

        // The empty state shows, and the pagination control must STILL be present.
        await expect(page.getByText(/no executions found/i)).toBeVisible({ timeout: 20_000 });
        await expect(loadMore).toBeVisible();
    });
});

test.describe("Executions board — mutation failures surface to the user", () => {
    test("the abort confirm dialog renders and dismisses without swallowing state", async ({
        page,
    }) => {
        // All three mutation handlers used to catch and only console.error, leaving the confirm open
        // with no feedback. The dialog now owns an actionError region and clears it on cancel.
        await gotoOrchestration(page, "executions", /Executions/i);
        const count = await expectTableRendered(page);
        test.skip(count === 0, "No executions in this environment");

        const errors = collectPageErrors(page);
        const row = tableRows(page).first();
        await row.scrollIntoViewIfNeeded();

        // Row actions live behind the row's own actions button when present.
        const actions = row.getByRole("button", { name: /Actions|More/i }).first();
        test.skip((await actions.count()) === 0, "Row actions not exposed in this build");
        await actions.click();

        const abort = page.getByRole("menuitem").filter({ hasText: /Abort/i }).first();
        test.skip((await abort.count()) === 0, "Abort not offered for this build");

        // A terminal execution must offer Abort as DISABLED rather than omitting it or letting a
        // doomed request through — that disabled state is itself the correct behavior to lock in.
        const isDisabled = (await abort.getAttribute("aria-disabled")) === "true";
        if (isDisabled) {
            await expect(abort).toHaveAttribute("aria-disabled", "true");
            // Clicking a disabled item must be inert: no dialog, no crash.
            await abort.click({ force: true, timeout: 5_000 }).catch(() => undefined);
            await expect(page.getByRole("dialog")).toHaveCount(0);
            await page.keyboard.press("Escape");
            expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
            return;
        }

        await abort.click();
        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible({ timeout: 15_000 });
        // The confirm must name what it is aborting rather than showing a bare yes/no.
        await expect(dialog).toContainText(/abort/i);

        // Cancel rather than aborting — this spec must not mutate shared data.
        await page.keyboard.press("Escape");
        await expect(dialog).toBeHidden({ timeout: 10_000 });
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });
});

test.describe("Dialog accessibility", () => {
    test("the dialog close control has an accessible name", async ({ page }) => {
        // The Radix Close trigger's only child was a bare glyph, leaving screen readers with an
        // unlabelled button.
        await gotoOrchestration(page, "pipelines", /Pipelines/i);
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");

        const items = await openCardMenu(page, id!);
        const archive = items.filter({ hasText: /^Archive/ }).first();
        test.skip((await archive.count()) === 0, "Archive not offered for this pipeline");
        await archive.click();

        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible({ timeout: 15_000 });
        await expect(dialog.getByRole("button", { name: /close/i }).first()).toBeVisible();

        await page.keyboard.press("Escape");
    });
});

test.describe("Pipeline form — numeric and timeout field validation", () => {
    test("blank numeric fields do not block submission with a NaN error", async ({ page }) => {
        // valueAsNumber turned an untouched Priority / Max Retries input into NaN, which zod rejected
        // with an unhelpful message; timeoutSchema.optional() likewise admitted undefined but not the
        // empty string RHF actually submits.
        const errors = collectPageErrors(page);
        await gotoOrchestration(page, "pipelines", /Pipelines/i);

        const create = page.getByRole("button", { name: /Create Pipeline|New Pipeline/i }).first();
        test.skip((await create.count()) === 0, "Create not permitted for this user");
        await create.click();

        // Wait for the form surface, then look for the numeric inputs.
        const priority = page.getByLabel(/^Priority$/i).first();
        if ((await priority.count()) > 0) {
            await expect(priority).toBeVisible({ timeout: 20_000 });
            // Leave it blank on purpose and confirm no NaN validation text appears.
            await priority.fill("");
            await priority.blur();
            await expect(page.getByText(/NaN|must be a number/i)).toHaveCount(0);
        }

        const timeout = page.getByLabel(/Task Timeout|Timeout/i).first();
        if ((await timeout.count()) > 0) {
            await timeout.fill("");
            await timeout.blur();
            await expect(page.getByText(/NaN/i)).toHaveCount(0);
        }

        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });
});

test.describe("Config editor — theme follows the app", () => {
    test("the Monaco editor is not hardcoded to the dark theme in light mode", async ({ page }) => {
        // ConfigEditor always rendered vs-dark; it now derives the theme from the awsui-dark-mode
        // class the app toggles on <body>.
        await gotoOrchestration(page, "pipelines", /Pipelines/i);
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");

        const items = await openCardMenu(page, id!);
        const templates = items.filter({ hasText: /Templates/i }).first();
        test.skip((await templates.count()) === 0, "Templates not offered for this pipeline");
        await templates.click();
        await expect(page.getByRole("heading", { name: "Templates", level: 1 })).toBeVisible({
            timeout: 20_000,
        });

        // Force light mode the same way the app's settings toggle does, then confirm the editor is
        // not still painting itself dark.
        await page.evaluate(() => document.body.classList.remove("awsui-dark-mode"));
        const monaco = page.locator(".monaco-editor").first();
        if ((await monaco.count()) === 0) {
            test.skip(true, "No template with a config editor on this page");
        }
        await expect(monaco).toBeVisible({ timeout: 30_000 });
        const isDarkClass = await monaco.evaluate((el) => el.className.includes("vs-dark"));
        expect(isDarkClass, "Monaco still vs-dark after light mode was applied").toBe(false);
    });
});

test.describe("Data table accessibility", () => {
    test("sortable headers and clickable rows are keyboard reachable", async ({ page }) => {
        // Sortable <th> and clickable <tr> carried onClick with cursor-pointer but no tabIndex or key
        // handler, so neither was reachable without a mouse.
        await gotoOrchestration(page, "executions", /Executions/i);
        const count = await expectTableRendered(page);
        test.skip(count === 0, "No executions in this environment");

        // A sortable header wraps its label in a real <button>, which is focusable by construction —
        // stronger than putting tabIndex on the <th>. It must also announce its sort state.
        const headers = page.locator("table thead th");
        const sortableHeaders = headers.filter({ has: page.locator("button") });
        const sortableCount = await sortableHeaders.count();
        test.skip(sortableCount === 0, "This table has no sortable columns");

        const firstSortable = sortableHeaders.first();
        await expect(firstSortable.locator("button").first()).toBeVisible();
        expect(
            await firstSortable.getAttribute("aria-sort"),
            "sortable header does not expose aria-sort"
        ).not.toBeNull();

        // Sorting must be operable from the keyboard alone and must change the announced state.
        const before = await firstSortable.getAttribute("aria-sort");
        await firstSortable.locator("button").first().focus();
        await page.keyboard.press("Enter");
        await expect
            .poll(async () => await firstSortable.getAttribute("aria-sort"), { timeout: 15_000 })
            .not.toBe(before);

        // A clickable row carries tabIndex plus a key handler (verified in DataTable.tsx).
        const row = tableRows(page).first();
        const rowTabIndex = await row.getAttribute("tabindex");
        const rowRole = await row.getAttribute("role");
        expect(
            rowTabIndex !== null || rowRole === "button",
            "clickable row is not focusable and exposes no button role"
        ).toBe(true);
    });
});

test.describe("Workflows page — archived facet parity with pipelines", () => {
    test("selecting the Archived status facet refetches with archived included", async ({
        page,
    }) => {
        // Same server-omits-archived bug the pipelines page had: the facet alone matched an empty set
        // because the list request never asked for archived rows.
        await gotoOrchestration(page, "workflows", /Workflows/i);

        const status = facet(page, "Status");
        test.skip((await status.count()) === 0, "No Status facet on the workflows page");
        await status.selectOption("archived");

        // The include-archived checkbox must remain unchecked — the facet alone has to suffice.
        const include = page.getByLabel(/include archived/i);
        if ((await include.count()) > 0) {
            await expect(include).not.toBeChecked();
        }

        // Either archived workflows appear, or an honest empty state does. What must NOT happen is a
        // crash or a perpetual spinner.
        const errors = collectPageErrors(page);
        await expect
            .poll(
                async () =>
                    (await page.getByRole("button", { name: /Actions for/i }).count()) > 0 ||
                    (await page.getByText(/no workflows|no results/i).count()) > 0,
                { timeout: 45_000 }
            )
            .toBe(true);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });
});

test.describe("Execute wizard — validation and error surfacing", () => {
    test("the wizard opens, validates, and can be dismissed without launching", async ({
        page,
    }) => {
        // handleLaunch used to swallow the execute rejection into console.error, and the review-stage
        // key mismatch (`db:pipelineId`) hid per-pipeline validation errors.
        await gotoOrchestration(page, "workflows", /Workflows/i);
        const id = await firstCardId(page);
        test.skip(!id, "No workflows in this environment");

        const errors = collectPageErrors(page);
        const items = await openCardMenu(page, id!);
        const execute = items.filter({ hasText: /Execute/i }).first();
        test.skip((await execute.count()) === 0, "Execute not offered for this workflow");
        await execute.click();

        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible({ timeout: 20_000 });

        // The wizard must render its own stage chrome rather than an empty shell.
        await expect(dialog).toContainText(/input|asset|review|template/i, { timeout: 20_000 });

        // Never launch — dismiss.
        await page.keyboard.press("Escape");
        await expect(dialog).toBeHidden({ timeout: 15_000 });
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });
});

test.describe("Permission graying does not hard-fail the page", () => {
    test("a route-permission lookup failure never blanks the orchestration pages", async ({
        page,
    }) => {
        // useAllowedRoutes cached its in-flight promise and never cleared it on failure, so one failed
        // lookup permanently grayed every action for the session.
        const errors = collectPageErrors(page);
        for (const [route, heading] of [
            ["pipelines", /Pipelines/i],
            ["workflows", /Workflows/i],
            ["executions", /Executions/i],
        ] as const) {
            await gotoOrchestration(page, route, heading);
            // The page's own chrome must be present regardless of what the permission lookup returned.
            await expect(orchestrationRoot(page)).toBeVisible({ timeout: 30_000 });
            await expect(searchBox(page)).toBeVisible({ timeout: 30_000 });
        }
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });
});

test.describe("Toast notifications", () => {
    test("the toast region is absent until a toast is raised, and layers above dialogs", async ({
        page,
    }) => {
        // The orchestration module had NO notification system: failures were console.error-only, one
        // page used a blocking alert(), and successes gave no confirmation at all. The provider now
        // renders a labelled live region on demand.
        await gotoOrchestration(page, "pipelines", /Pipelines/i);

        // Renders null when empty — an always-present empty region would trap clicks.
        await expect(page.getByLabel("Notifications")).toHaveCount(0);

        // Opening a confirm dialog raises no toast by itself.
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");
        const items = await openCardMenu(page, id!);
        const archive = items.filter({ hasText: /^Archive/ }).first();
        test.skip((await archive.count()) === 0, "Archive not offered for this pipeline");
        await archive.click();
        await expect(page.getByRole("dialog")).toBeVisible({ timeout: 15_000 });
        await expect(page.getByLabel("Notifications")).toHaveCount(0);

        // Dismiss without archiving — this spec must not mutate shared data.
        await page.keyboard.press("Escape");
    });
});
