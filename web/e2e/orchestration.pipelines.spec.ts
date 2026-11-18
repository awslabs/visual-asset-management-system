/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page } from "@playwright/test";
import {
    allowedApiRoutes,
    AllowedApiRoute,
    collectPageErrors,
    expectNoPageOverflowX,
    facet,
    firstCardId,
    firstCardName,
    gotoOrchestration,
    openCardMenu,
    routeAllowed,
    searchBox,
    VIEWPORTS,
} from "./support/fixtures";

/**
 * Pipelines page — permanent smoke coverage. Environment-agnostic: asserts the page's controls and
 * behavior against whatever pipelines the environment happens to contain, and skips (rather than
 * fails) when a precondition is genuinely absent. Safe against an empty or a seeded sandbox.
 *
 * Role-aware and system-record-aware (#1634, D130): a card's menu lists only the actions the signed-in
 * role may call, so the expected items come from the API's allowed routes (the list the app's own
 * check reads); and a system record (the SYSTEM GenAI, preview and Zip Archive pipelines, registered
 * by default) offers View instead of Edit and keeps Archive disabled, so a case about Edit or Archive
 * picks an ordinary card.
 */

/** The API route behind each pipeline card action (PipelinesPage's `can()` checks). */
const PIPELINE_ACTION_ROUTES: Record<"edit" | "templates" | "archive", [string, string]> = {
    edit: ["PUT", "/database/{databaseId}/pipelines/{pipelineId}"],
    templates: ["GET", "/database/{databaseId}/pipelines/{pipelineId}/templates"],
    archive: ["DELETE", "/database/{databaseId}/pipelines/{pipelineId}"],
};

function mayUse(routes: AllowedApiRoute[], action: keyof typeof PIPELINE_ACTION_ROUTES): boolean {
    const [method, path] = PIPELINE_ACTION_ROUTES[action];
    return routeAllowed(routes, method, path);
}

/** A menu item of the open card menu by its exact accessible name ("View" is not "View Executions"). */
function menuItem(page: Page, name: string) {
    return page.getByRole("menuitem", { name, exact: true });
}

test.describe("Pipelines page", () => {
    test.beforeEach(async ({ page }) => {
        await gotoOrchestration(page, "pipelines", "Pipelines");
    });

    test("renders without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        // Either cards or the empty state — both are valid, depending on the environment.
        await expect
            .poll(
                async () =>
                    (await page.getByRole("button", { name: /Actions for/i }).count()) > 0 ||
                    (await page.getByText(/no pipelines/i).count()) > 0,
                { timeout: 60_000 }
            )
            .toBe(true);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("exposes the filter bar controls", async ({ page }) => {
        await expect(searchBox(page)).toBeVisible();
        for (const label of ["Execution Type", "Status", "Group by"]) {
            await expect(facet(page, label)).toBeVisible();
        }
        await expect(page.getByLabel(/include archived/i)).toBeVisible();
    });

    test("the Status facet offers enabled / disabled / archived", async ({ page }) => {
        const status = facet(page, "Status");
        for (const value of ["enabled", "disabled", "archived"]) {
            await expect(status.locator(`option[value="${value}"]`)).toHaveCount(1);
        }
    });

    test("free-text search narrows the list to a matching pipeline", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment to filter");
        await searchBox(page).fill(id!);
        await expect(page.getByText(id!, { exact: false }).first()).toBeVisible({
            timeout: 45_000,
        });
        // Searching a string that cannot match must empty the list rather than ignore the filter.
        await searchBox(page).fill("zzz-no-such-pipeline-zzz");
        await expect(page.getByRole("button", { name: /Actions for/i })).toHaveCount(0, {
            timeout: 30_000,
        });
    });

    test("a pipeline card's actions menu offers Edit / Templates / Archive", async ({ page }) => {
        const name = await firstCardName(page, { system: false });
        test.skip(!name, "No pipeline other than the system records in this environment");
        const routes = await allowedApiRoutes(page);
        test.skip(!routes, "The role's allowed API routes could not be read (no API context)");
        const may = {
            edit: mayUse(routes!, "edit"),
            templates: mayUse(routes!, "templates"),
            archive: mayUse(routes!, "archive"),
        };
        test.skip(
            !may.edit && !may.templates && !may.archive,
            "This role may use none of a pipeline card's actions, so its menu is empty"
        );
        await openCardMenu(page, name!);
        // Each action exactly when the API allows it to this role; the admin role sees all three.
        for (const [action, label] of [
            ["edit", "Edit"],
            ["templates", "Templates"],
            ["archive", "Archive"],
        ] as const) {
            if (may[action]) {
                await expect(menuItem(page, label), `${label} for this role`).toBeVisible();
            } else {
                await expect(menuItem(page, label), `${label} hidden from this role`).toHaveCount(
                    0
                );
            }
        }
        await expect(menuItem(page, "Archive")).not.toHaveAttribute("aria-disabled", "true");
        // View replaces Edit on a system record only.
        await expect(menuItem(page, "View")).toHaveCount(0);
        await page.keyboard.press("Escape");
    });

    test("a system pipeline card offers View, not Edit, and keeps Archive disabled (D130)", async ({
        page,
    }) => {
        const name = await firstCardName(page, { system: true });
        test.skip(!name, "No system pipeline in this environment");
        const routes = await allowedApiRoutes(page);
        test.skip(!routes, "The role's allowed API routes could not be read (no API context)");
        await openCardMenu(page, name!);
        await expect(menuItem(page, "View")).toBeVisible();
        await expect(menuItem(page, "Edit")).toHaveCount(0);
        if (mayUse(routes!, "templates")) {
            await expect(menuItem(page, "Templates")).toBeVisible();
        } else {
            await expect(menuItem(page, "Templates")).toHaveCount(0);
        }
        if (mayUse(routes!, "archive")) {
            // Listed, so the role learns why, but not usable: the deployment owns the record.
            const archive = menuItem(page, "Archive");
            await expect(archive).toHaveAttribute("aria-disabled", "true");
            // ContextMenu reads a disabled item's reason out through aria-description.
            await expect(archive).toHaveAttribute(
                "aria-description",
                /managed by the deployment and cannot be archived/
            );
        } else {
            await expect(menuItem(page, "Archive")).toHaveCount(0);
        }
        await page.keyboard.press("Escape");
    });

    test("the Templates action opens the template editor", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No pipelines in this environment");
        const routes = await allowedApiRoutes(page);
        test.skip(
            !!routes && !mayUse(routes, "templates"),
            "This role may not list a pipeline's templates"
        );
        const items = await openCardMenu(page, id!);
        await items.filter({ hasText: "Templates" }).click();
        await expect(page.getByRole("heading", { name: "Templates", level: 1 })).toBeVisible({
            timeout: 20_000,
        });
    });

    test("Edit on a stored pipeline opens a populated form whose save is reachable", async ({
        page,
    }) => {
        // A system record has no Edit (View opens it read-only), so the subject is an ordinary card.
        const id = await firstCardName(page, { system: false });
        test.skip(!id, "No pipeline other than the system records in this environment");
        const items = await openCardMenu(page, id!);
        const edit = items.filter({ hasText: "Edit" });
        // A DeadlineCloud pipeline in a deployment with that feature off is read-only and offers no
        // Edit action, so there is nothing to assert about the form here.
        if ((await edit.count()) === 0) {
            await page.keyboard.press("Escape");
            test.skip(true, "This pipeline offers no Edit action to this role in this deployment");
        }
        await edit.first().click();

        await expect(page.getByRole("heading", { name: "Edit Pipeline", level: 1 })).toBeVisible({
            timeout: 30_000,
        });
        // A pipeline stored by any writer — API, CLI, CDK registration, or a migration — must be
        // editable. Every stored record carries all four execution-type sub-blocks with the unused
        // ones empty, so a form that validated those blocked the save with nothing on screen.
        await expect(page.locator("#pipelineId")).toHaveValue(/.+/, { timeout: 30_000 });
        await expect(page.locator("#pipelineName")).not.toHaveValue("");

        // The save must actually leave the browser. The request is intercepted and aborted, so this
        // proves the form issues it without changing the pipeline (core specs mutate nothing).
        let updateAttempted = false;
        await page.route(/\/pipelines\//, async (route) => {
            if (route.request().method() === "PUT") {
                updateAttempted = true;
                await route.abort();
                return;
            }
            await route.continue();
        });

        // Walk the wizard with its own Next button; Save exists only on the last step. Locating a
        // step by name would match the global navigation instead of the stepper. Bounded rather than
        // "while visible" so a Next that stops advancing fails on the Update assertion below with a
        // readable message instead of looping.
        const next = page.getByRole("button", { name: "Next" });
        for (let step = 0; step < 5; step++) {
            if (!(await next.isVisible().catch(() => false))) break;
            await next.click();
        }
        const update = page.getByRole("button", { name: /^Update$/ });
        await expect(update).toBeEnabled();
        await update.click();
        await expect.poll(() => updateAttempted, { timeout: 20_000 }).toBe(true);
    });

    test("a pipeline that fails to load shows an error, not a blank Edit form", async ({
        page,
    }) => {
        const id = await firstCardName(page, { system: false });
        test.skip(!id, "No pipeline other than the system records in this environment");

        // Fails the single-pipeline read only: the list route ends at "/pipelines" with no id
        // segment, and the templates route continues past the id, so neither matches.
        await page.route(/\/pipelines\/[^/?]+(\?|$)/, async (route) => {
            if (route.request().method() === "GET") {
                await route.fulfill({
                    status: 500,
                    contentType: "application/json",
                    body: JSON.stringify({ message: "Internal Server Error" }),
                });
                return;
            }
            await route.continue();
        });

        const items = await openCardMenu(page, id!);
        const edit = items.filter({ hasText: "Edit" });
        if ((await edit.count()) === 0) {
            await page.keyboard.press("Escape");
            test.skip(true, "This pipeline offers no Edit action to this role in this deployment");
        }
        await edit.first().click();

        // An unreadable pipeline must say so. A form titled "Edit Pipeline" seeded with create
        // defaults reads as a pipeline whose configuration was wiped, and saving it would PUT to a
        // path with an empty id.
        await expect(page.getByText(/pipeline not found/i)).toBeVisible({ timeout: 30_000 });
        await expect(page.locator("#pipelineName")).toHaveCount(0);
        await expect(page.getByRole("button", { name: /^Update$/ })).toHaveCount(0);
    });
});

/**
 * The pipeline and template wizards share the workflow builder's step rail, so they fit a phone
 * width too: the page does not scroll sideways on their first step. Opens the create forms only.
 */
test.describe("Pipeline and template wizards — fit a phone viewport", () => {
    test.use({ viewport: VIEWPORTS.phone });

    test("the pipeline create wizard does not scroll sideways", async ({ page }) => {
        await page.goto("/#/databases/GLOBAL/pipelines/create", { waitUntil: "domcontentloaded" });
        const heading = page.getByRole("heading", { name: "Create Pipeline", level: 1 });
        await heading.waitFor({ state: "visible", timeout: 30_000 }).catch(() => undefined);
        test.skip(!(await heading.isVisible()), "The create route is not allowed for this user");
        await expect(page.locator("[aria-current='step']")).toBeVisible();
        await expectNoPageOverflowX(page, "create pipeline, first step");
    });

    test("the template create wizard does not scroll sideways", async ({ page }) => {
        await gotoOrchestration(page, "pipelines", "Pipelines");
        // Templates cannot be added to a system record, so the subject is an ordinary card.
        const id = await firstCardName(page, { system: false });
        test.skip(!id, "No pipeline other than the system records in this environment");
        const items = await openCardMenu(page, id!);
        await items.filter({ hasText: "Templates" }).click();
        await expect(page.getByRole("heading", { name: "Templates", level: 1 })).toBeVisible({
            timeout: 20_000,
        });
        const listUrl = page.url().replace(/\/+$/, "");
        await page.goto(`${listUrl}/create`, { waitUntil: "domcontentloaded" });
        const heading = page.getByRole("heading", { name: "Create Template", level: 1 });
        await heading.waitFor({ state: "visible", timeout: 30_000 }).catch(() => undefined);
        test.skip(!(await heading.isVisible()), "The template create route is not allowed");
        await expect(page.locator("[aria-current='step']")).toBeVisible();
        await expectNoPageOverflowX(page, "create template, first step");
    });
});
