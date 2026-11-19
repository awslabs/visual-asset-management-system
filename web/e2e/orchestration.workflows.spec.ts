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
    expectReachable,
    firstCardId,
    firstCardName,
    gotoOrchestration,
    openCardMenu,
    routeAllowed,
    searchBox,
    VIEWPORTS,
} from "./support/fixtures";

/**
 * Workflows page — permanent smoke coverage. Environment-agnostic: derives a workflow from whatever
 * the environment contains and skips when there is none, so it is safe against an empty sandbox.
 *
 * The Execute wizard is opened but never launched — these specs must not mutate the environment.
 *
 * Role-aware and system-record-aware (#1634, D130): a card's menu lists only the actions the signed-in
 * role may call, so the expected items come from the API's allowed routes (the list the app's own
 * check reads); and a system workflow (the SYSTEM GenAI, preview and Zip Archive workflows, registered
 * by default) offers View instead of Edit and keeps Archive disabled, so a case about Edit picks an
 * ordinary card.
 */

/** The API route behind each workflow card action (WorkflowsPage's `can()` checks). */
const WORKFLOW_ACTION_ROUTES: Record<
    "edit" | "execute" | "executions" | "archive",
    [string, string]
> = {
    edit: ["PUT", "/database/{databaseId}/workflows/{workflowId}"],
    execute: ["POST", "/workflows/{workflowDatabaseId}/{workflowId}/execute"],
    executions: ["GET", "/workflows/executions"],
    archive: ["DELETE", "/database/{databaseId}/workflows/{workflowId}"],
};

function mayUse(routes: AllowedApiRoute[], action: keyof typeof WORKFLOW_ACTION_ROUTES): boolean {
    const [method, path] = WORKFLOW_ACTION_ROUTES[action];
    return routeAllowed(routes, method, path);
}

/** A menu item of the open card menu by its exact accessible name ("View" is not "View Executions"). */
function menuItem(page: Page, name: string) {
    return page.getByRole("menuitem", { name, exact: true });
}

/** The labels of the workflow card menu, by action. */
const LABELS = {
    edit: "Edit",
    execute: "Execute",
    executions: "View Executions",
    archive: "Archive",
} as const;

test.describe("Workflows page", () => {
    test.beforeEach(async ({ page }) => {
        await gotoOrchestration(page, "workflows", "Workflows");
    });

    test("renders without a client-side crash", async ({ page }) => {
        const errors = collectPageErrors(page);
        await expect
            .poll(
                async () =>
                    (await page.getByRole("button", { name: /Actions for/i }).count()) > 0 ||
                    (await page.getByText(/no workflows/i).count()) > 0,
                { timeout: 60_000 }
            )
            .toBe(true);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("exposes the search control", async ({ page }) => {
        await expect(searchBox(page)).toBeVisible();
    });

    test("a workflow card's actions menu offers Edit / Execute / View Executions / Archive", async ({
        page,
    }) => {
        const name = await firstCardName(page, { system: false });
        test.skip(!name, "No workflow other than the system records in this environment");
        const routes = await allowedApiRoutes(page);
        test.skip(!routes, "The role's allowed API routes could not be read (no API context)");
        const actions = Object.keys(LABELS) as (keyof typeof LABELS)[];
        test.skip(
            !actions.some((action) => mayUse(routes!, action)),
            "This role may use none of a workflow card's actions, so its menu is empty"
        );
        await openCardMenu(page, name!);
        // Each action exactly when the API allows it to this role; the admin role sees all four.
        for (const action of actions) {
            const item = menuItem(page, LABELS[action]);
            if (mayUse(routes!, action)) {
                await expect(item, `${LABELS[action]} for this role`).toBeVisible();
            } else {
                await expect(item, `${LABELS[action]} hidden from this role`).toHaveCount(0);
            }
        }
        await expect(menuItem(page, "View")).toHaveCount(0);
        await page.keyboard.press("Escape");
    });

    test("a system workflow card offers View, not Edit, and keeps Archive disabled (D130)", async ({
        page,
    }) => {
        const name = await firstCardName(page, { system: true });
        test.skip(!name, "No system workflow in this environment");
        const routes = await allowedApiRoutes(page);
        test.skip(!routes, "The role's allowed API routes could not be read (no API context)");
        await openCardMenu(page, name!);
        await expect(menuItem(page, "View")).toBeVisible();
        await expect(menuItem(page, "Edit")).toHaveCount(0);
        for (const action of ["execute", "executions"] as const) {
            const item = menuItem(page, LABELS[action]);
            if (mayUse(routes!, action)) await expect(item).toBeVisible();
            else await expect(item).toHaveCount(0);
        }
        if (mayUse(routes!, "archive")) {
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

    test("the Execute action opens the wizard without launching", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No workflows in this environment");
        const routes = await allowedApiRoutes(page);
        test.skip(!!routes && !mayUse(routes, "execute"), "This role may not execute workflows");
        await openCardMenu(page, id!);
        const execute = menuItem(page, "Execute");
        await expect(execute).toBeVisible();
        if ((await execute.getAttribute("aria-disabled")) === "true") {
            await page.keyboard.press("Escape");
            test.skip(true, "The first workflow is disabled or archived, so it cannot be executed");
        }
        await execute.click();

        // The wizard is a dialog; assert it opened, then dismiss so nothing is executed.
        const dialog = page.getByRole("dialog");
        await expect(dialog).toBeVisible({ timeout: 20_000 });
        // A dialog must sit above the fixed Cloudscape TopNavigation to be usable.
        const z = await dialog.evaluate(
            (el) => parseInt(window.getComputedStyle(el as HTMLElement).zIndex, 10) || 0
        );
        expect(z).toBeGreaterThan(1000);
        await page.keyboard.press("Escape");
    });

    test("View Executions deep-links to the executions board", async ({ page }) => {
        const id = await firstCardId(page);
        test.skip(!id, "No workflows in this environment");
        const routes = await allowedApiRoutes(page);
        test.skip(!!routes && !mayUse(routes, "executions"), "This role may not list executions");
        const items = await openCardMenu(page, id!);
        await items
            .filter({ hasText: /Executions/ })
            .first()
            .click();
        await expect(page.getByRole("heading", { name: /Executions/i, level: 1 })).toBeVisible({
            timeout: 30_000,
        });
    });
});

/**
 * The workflow builder's step list. Derived from an existing workflow so it holds in any environment:
 * every workflow has at least one step, and a second card is added by the builder itself.
 */
test.describe("Workflow builder — step definition", () => {
    /**
     * Open Edit on whatever workflow exists and land on the Pipelines step. Returns false when the
     * environment has no workflow or the caller may not edit it, so the test can skip honestly.
     */
    async function openPipelinesStep(page: import("@playwright/test").Page): Promise<boolean> {
        await gotoOrchestration(page, "workflows", "Workflows");
        // A system workflow opens read-only (View), so the subject is an ordinary card.
        const id = await firstCardName(page, { system: false });
        if (!id) return false;
        const items = await openCardMenu(page, id);
        const edit = items.filter({ hasText: "Edit" });
        if ((await edit.count()) === 0) {
            await page.keyboard.press("Escape");
            return false;
        }
        await edit.first().click();
        await expect(page.getByRole("heading", { name: "Edit Workflow", level: 1 })).toBeVisible({
            timeout: 30_000,
        });
        // Advance with the wizard's own Next button — a step cannot be located by name, because the
        // global navigation owns those words. Pipelines is the third step.
        const next = page.getByRole("button", { name: "Next" });
        for (let step = 0; step < 2; step++) {
            await next.click();
        }
        await expect(page.getByRole("button", { name: "Add Pipeline" })).toBeVisible({
            timeout: 20_000,
        });
        return true;
    }

    /** The step cards' Job Name inputs, in card order. */
    function jobNameInputs(page: import("@playwright/test").Page) {
        return page.locator('input[id^="jobName-"]');
    }

    test("two steps sharing a job name block the save", async ({ page }) => {
        test.skip(!(await openPipelinesStep(page)), "No editable workflow in this environment");

        // A second card, so there are two job names to collide. Its pipeline is left unselected —
        // that is its own blocking error, so the assertion targets the job-name message specifically.
        await page.getByRole("button", { name: "Add Pipeline" }).click();
        const names = jobNameInputs(page);
        await expect(names).toHaveCount(2, { timeout: 20_000 });

        // The same name on both steps collapses them into ONE Step Functions state, so one of the
        // pipelines silently never runs. The value satisfies the charset rule so the only error it
        // can raise is the duplicate.
        await names.nth(0).fill("shared-step-name");
        await names.nth(1).fill("shared-step-name");

        // Reported on the offending card AND as a blocking error in the validation panel, which is
        // rendered on every step and is what withholds Save on Review.
        await expect(page.getByText(/already uses this job name/i).first()).toBeVisible({
            timeout: 20_000,
        });
        await expect(page.getByText(/Errors \(blocking save\)/i).first()).toBeVisible({
            timeout: 20_000,
        });
        await expect(page.getByText(/is already used by pipeline #1/i).first()).toBeVisible();

        // Differing names clear it, proving the error tracked the collision rather than the field.
        await names.nth(1).fill("other-step-name");
        await expect(page.getByText(/already uses this job name/i)).toHaveCount(0, {
            timeout: 20_000,
        });
    });

    test("a pipeline already in the workflow cannot be added a second time", async ({ page }) => {
        test.skip(!(await openPipelinesStep(page)), "No editable workflow in this environment");

        const firstSelect = page.locator('select[id^="pipeline-"]').first();
        const chosen = await firstSelect.inputValue();
        test.skip(!chosen, "The first step has no pipeline selected to duplicate");

        await page.getByRole("button", { name: "Add Pipeline" }).click();
        const selects = page.locator('select[id^="pipeline-"]');
        await expect(selects).toHaveCount(2, { timeout: 20_000 });

        // The backend keys each step's parameters, resolved config and filtered inputs by the
        // pipeline's composite id, so a second reference to one overwrites the first and only one of
        // the two steps runs. The new card must not offer the pipeline the first card holds.
        const duplicate = selects.nth(1).locator(`option[value="${chosen}"]`);
        await expect(duplicate).toHaveCount(1);
        await expect(duplicate).toBeDisabled();
        await expect(duplicate).toHaveText(/already in this workflow/i);
    });

    test("Cancel with unsaved changes asks before discarding", async ({ page }) => {
        test.skip(!(await openPipelinesStep(page)), "No editable workflow in this environment");

        // Any authored change makes the wizard dirty; a job name is the cheapest one on this step.
        await jobNameInputs(page).first().fill("cancel-guard-probe");

        // Captured WITHOUT accepting, so nothing is discarded and nothing is saved — the wizard stays
        // on screen either way.
        let confirmText = "";
        page.once("dialog", async (d) => {
            confirmText = d.message();
            await d.dismiss();
        });
        // exact: the probe job name renders on the DAG node, whose accessible name would otherwise
        // also match "Cancel".
        await page.getByRole("button", { name: "Cancel", exact: true }).click();
        await expect.poll(() => confirmText, { timeout: 20_000 }).toMatch(/without saving/i);
        await expect(page.getByRole("heading", { name: "Edit Workflow", level: 1 })).toBeVisible();
    });
});

/**
 * The builder in create mode. Uses the GLOBAL database, which every deployment has, so the test holds
 * in any environment; it skips when the user may not open the create route.
 */
test.describe("Workflow builder — create", () => {
    test("Create Workflow shows a Triggers step", async ({ page }) => {
        // Land on the list first so Cancel has a page to return to.
        await gotoOrchestration(page, "workflows", "Workflows");
        await page.goto("/#/databases/GLOBAL/workflows/create", { waitUntil: "domcontentloaded" });

        const heading = page.getByRole("heading", { name: "Create Workflow", level: 1 });
        await heading.waitFor({ state: "visible", timeout: 30_000 }).catch(() => undefined);
        test.skip(!(await heading.isVisible()), "The create route is not allowed for this user");

        // Offered before anything is saved; drafted triggers are written after the create.
        await expect(page.getByText("Triggers (optional)", { exact: true })).toBeVisible();

        // Nothing was entered, so Cancel leaves without a confirm and nothing is written. A confirm
        // that did appear is dismissed, which keeps the builder on screen and fails the last check.
        page.once("dialog", (d) => d.dismiss());
        await page.getByRole("button", { name: "Cancel" }).click();
        await expect(page.getByRole("heading", { name: "Workflows", level: 1 })).toBeVisible({
            timeout: 20_000,
        });
    });
});

/**
 * The builder fits the viewport at phone and laptop widths: the step rail compacts below `sm`, and
 * the trigger table scrolls inside its own box, so the page never scrolls sideways and each trigger
 * row's Edit and Delete are reachable. Walks an existing workflow's steps with Next and never saves.
 */
for (const [name, viewport] of [
    ["phone", VIEWPORTS.phone],
    ["laptop", VIEWPORTS.laptop],
] as const) {
    test.describe(`Workflow builder — fits a ${name} viewport`, () => {
        test.use({ viewport });

        test("the create wizard does not scroll sideways", async ({ page }) => {
            await page.goto("/#/databases/GLOBAL/workflows/create", {
                waitUntil: "domcontentloaded",
            });
            const heading = page.getByRole("heading", { name: "Create Workflow", level: 1 });
            await heading.waitFor({ state: "visible", timeout: 30_000 }).catch(() => undefined);
            test.skip(
                !(await heading.isVisible()),
                "The create route is not allowed for this user"
            );
            await expect(page.locator("[aria-current='step']")).toBeVisible();
            await expectNoPageOverflowX(page, "create workflow, first step");
        });

        test("every edit step fits, and trigger rows keep Edit and Delete reachable", async ({
            page,
        }) => {
            await gotoOrchestration(page, "workflows", "Workflows");
            const id = await firstCardName(page, { system: false });
            test.skip(!id, "No workflow other than the system records in this environment");
            const items = await openCardMenu(page, id!);
            const edit = items.filter({ hasText: "Edit" });
            if ((await edit.count()) === 0) {
                await page.keyboard.press("Escape");
                test.skip(true, "The user may not edit this workflow");
            }
            await edit.first().click();
            await expect(
                page.getByRole("heading", { name: /^(Edit|View) Workflow$/, level: 1 })
            ).toBeVisible({ timeout: 30_000 });

            const next = page.getByRole("button", { name: "Next" });
            for (let step = 0; step < 5; step++) {
                const current = await page.locator("[aria-current='step']").innerText();
                await expectNoPageOverflowX(page, `edit workflow, step "${current.trim()}"`);

                const editTrigger = page.getByRole("button", { name: /^Edit trigger / });
                if ((await editTrigger.count()) > 0) {
                    await expectReachable(editTrigger.first(), "a trigger row's Edit");
                    const del = page.getByRole("button", { name: /^Delete trigger / });
                    if ((await del.count()) > 0) {
                        await expectReachable(del.first(), "a trigger row's Delete");
                    }
                }
                if (!(await next.isVisible()) || !(await next.isEnabled())) break;
                await next.click();
            }
        });
    });
}
