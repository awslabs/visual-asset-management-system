/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Locator, Page } from "@playwright/test";
import {
    chooseSelectOption,
    collectPageErrors,
    compareSeed,
    openAssetFile,
    openFileManager,
    selectTreePath,
} from "./support/fixtures";

/**
 * Compare-mode surfaces, driven against the deployed app. SEEDED: the `seeded.` prefix marks this as a
 * seed-dependent spec — it needs the fixtures `tools/VamsCLI/examples/seed_compare_smoke.py` creates
 * (two databases, a distributable asset in each, .txt/.json/.md plus a .png, one text file with >= 3
 * versions and one asset-version snapshot). No id is hardcoded here: the seed writes a JSON fixture
 * and `E2E_COMPARE_SEED` names it, so the whole file skips with a reason when it is not configured.
 *
 *   python3 tools/VamsCLI/examples/seed_compare_smoke.py --profile <name>
 *   export E2E_COMPARE_SEED=/abs/path/web/e2e/fixtures/compare-seed.json
 *   cd web && npx playwright test e2e/seeded.compare.spec.ts
 *
 * What is proven, per fix:
 *  1. A compare-only viewer (the text differ) is never listed in the VISUALIZE picker, even for one
 *     text file that matches its extensions.
 *  2. The viewer modal offers only the modes some viewer admits: two text files open straight on
 *     Compare with no Visualize/Compare toggle; one text file opens on Visualize with no toggle.
 *  3. The version file list offers a per-row "Compare" for a text file and none for the PNG, and the
 *     action diffs the snapshot's version (left) against latest (right).
 *  4. The text differ's controls — Line/Word/Character granularity, line numbers, collapse unchanged
 *     with a context-lines picker — render and change the rendered diff.
 *  5. Search "Compare Selected" enables only for a selection a compare viewer admits (count + types)
 *     and "View Selected" only for one a visualize viewer admits.
 */

const seed = compareSeed();
test.skip(
    !seed,
    "E2E_COMPARE_SEED is not set (or unreadable): run tools/VamsCLI/examples/seed_compare_smoke.py " +
        "and export the path it prints"
);

const A = seed?.assets.a;
const TEXT = seed?.textFile ?? "notes.txt";
const JSON_FILE = seed?.jsonFile ?? "config.json";
const PNG = seed?.binaryFile ?? "pixel.png";

/** The open file-viewer / compare modal. */
const dialog = (page: Page): Locator => page.getByRole("dialog").last();

/** Cloudscape SegmentedControl renders each segment as a button; the label text is the segment. */
const segment = (scope: Locator, name: string): Locator =>
    scope.getByRole("button", { name, exact: true }).first();

/** Wait for the text differ to have loaded its library and both sides. */
async function expectDifferRendered(scope: Locator): Promise<Locator> {
    const differ = scope.getByTestId("text-diff-viewer");
    await expect(differ).toBeVisible({ timeout: 60_000 });
    // The diff table (or the per-side error panes) replaces the "Loading diff..." state.
    await expect
        .poll(async () => await scope.locator('[class*="diff-container"]').count(), {
            timeout: 60_000,
        })
        .toBeGreaterThan(0);
    return differ;
}

test.describe("compare mode", () => {
    test("compare-only viewer is absent from the Visualize picker for one text file", async ({
        page,
    }) => {
        const errors = collectPageErrors(page);
        await openAssetFile(page, A!.databaseId, A!.assetId, TEXT);
        await expect(page.getByRole("heading", { name: new RegExp(TEXT) })).toBeVisible({
            timeout: 60_000,
        });

        const picker = page.getByLabel(/select viewer/i).first();
        await expect(picker).toBeVisible({ timeout: 60_000 });
        await picker.click();
        await expect
            .poll(async () => await page.locator('[role="option"]').count(), { timeout: 30_000 })
            .toBeGreaterThan(0);
        const offered = await page.locator('[role="option"]').allTextContents();
        await page.keyboard.press("Escape");

        expect(
            offered.some((t) => /Text Viewer/i.test(t)),
            `offered: ${offered.join(" | ")}`
        ).toBe(true);
        expect(
            offered.some((t) => /Text Diff/i.test(t)),
            `offered: ${offered.join(" | ")}`
        ).toBe(false);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("modal offers only the admitted mode: two text files open on Compare, no toggle", async ({
        page,
    }) => {
        await openFileManager(page, A!.databaseId, A!.assetId);
        await selectTreePath(page, TEXT);
        await selectTreePath(page, JSON_FILE, { modifiers: ["Control"] });

        // Two text files: a differ admits them, no multi-file text visualizer does.
        const compareIcon = page.getByRole("button", { name: "Compare Selected Files" });
        await expect(compareIcon).toBeVisible({ timeout: 30_000 });
        await expect(page.getByRole("button", { name: "Visualize Selected Files" })).toHaveCount(0);

        await compareIcon.click();
        const modal = dialog(page);
        await expect(modal).toBeVisible({ timeout: 30_000 });
        await expect(modal.getByText(/Compare Files - 2 Files/)).toBeVisible();
        await expect(modal.getByLabel("Viewer mode")).toHaveCount(0);
        await expectDifferRendered(modal);
        await page.keyboard.press("Escape");
    });

    test("modal offers only the admitted mode: one text file opens on Visualize, no toggle", async ({
        page,
    }) => {
        await openFileManager(page, A!.databaseId, A!.assetId);
        await selectTreePath(page, TEXT);

        const eye = page.getByRole("button", { name: `Visualize File ${TEXT}` });
        await expect(eye).toBeVisible({ timeout: 30_000 });
        await eye.click();
        const modal = dialog(page);
        await expect(modal).toBeVisible({ timeout: 30_000 });
        await expect(modal.getByText(new RegExp(`File Viewer - ${TEXT}`))).toBeVisible();
        await expect(modal.getByLabel("Viewer mode")).toHaveCount(0);
        // The visualize surface rendered a viewer (the text viewer), not the differ.
        await expect(modal.getByTestId("text-diff-viewer")).toHaveCount(0);
        await page.keyboard.press("Escape");
    });

    test("version file list offers Compare for a text file, not for the PNG, and diffs vs latest", async ({
        page,
    }) => {
        await openFileManager(page, A!.databaseId, A!.assetId);
        await page
            .getByRole("tab", { name: /^versions$/i })
            .first()
            .click();

        // Select the first (only) asset-version snapshot; its file list appears below.
        const versionRow = page.locator("table tbody tr").first();
        await expect(versionRow).toBeVisible({ timeout: 60_000 });
        const radio = versionRow.getByRole("radio");
        test.skip((await radio.count()) === 0, "No asset version to select in this environment");
        await radio.click();

        const textRow = page.locator("table tbody tr").filter({ hasText: TEXT }).first();
        const pngRow = page.locator("table tbody tr").filter({ hasText: PNG }).first();
        await expect(textRow).toBeVisible({ timeout: 60_000 });
        await expect(pngRow).toBeVisible({ timeout: 60_000 });

        const textCompare = textRow.getByRole("button", { name: "Compare", exact: true });
        await expect(textCompare).toBeVisible({ timeout: 30_000 });
        await expect(pngRow.getByRole("button", { name: "Compare", exact: true })).toHaveCount(0);
        // The PNG row keeps its other actions — only the differ is withheld.
        await expect(pngRow.getByRole("button", { name: /download file/i })).toBeVisible();

        await textCompare.click();
        const modal = dialog(page);
        await expect(modal).toBeVisible({ timeout: 30_000 });
        await expectDifferRendered(modal);
        // Left = the snapshot's pinned S3 version, right = latest.
        await expect(modal.getByTestId("text-diff-side-label-left")).toContainText("@");
        await expect(modal.getByTestId("text-diff-side-label-right")).toContainText("(latest)");
        await page.keyboard.press("Escape");
    });

    test("text differ controls render and change the rendered diff", async ({ page }) => {
        await openFileManager(page, A!.databaseId, A!.assetId);
        await selectTreePath(page, TEXT);
        await selectTreePath(page, JSON_FILE, { modifiers: ["Control"] });
        await page.getByRole("button", { name: "Compare Selected Files" }).click();
        const modal = dialog(page);
        const differ = await expectDifferRendered(modal);
        const controls = differ.getByTestId("text-diff-controls");

        // All controls present.
        for (const name of ["Side-by-side", "Inline", "Line", "Word", "Character"]) {
            await expect(segment(controls, name)).toBeVisible();
        }
        const lineNumbers = controls.getByText("Line numbers", { exact: true });
        const collapse = controls.getByText("Collapse unchanged", { exact: true });
        await expect(lineNumbers).toBeVisible();
        await expect(collapse).toBeVisible();
        await expect(differ.getByTestId("text-diff-context-lines")).toBeVisible();

        // Line numbers: the gutter is rendered, then gone once the toggle is off.
        const gutterCells = differ.locator('[class*="line-number"]');
        await expect.poll(async () => await gutterCells.count()).toBeGreaterThan(0);
        await lineNumbers.click();
        await expect.poll(async () => await gutterCells.count()).toBe(0);
        await lineNumbers.click();
        await expect.poll(async () => await gutterCells.count()).toBeGreaterThan(0);

        // Granularity: line-level renders no intra-line marks; character-level does.
        const marks = differ.locator('[class*="word-diff"]');
        expect(await marks.count()).toBe(0);
        await segment(controls, "Character").click();
        await expect.poll(async () => await marks.count(), { timeout: 30_000 }).toBeGreaterThan(0);
        await segment(controls, "Line").click();
        await expect.poll(async () => await marks.count(), { timeout: 30_000 }).toBe(0);

        // Collapse unchanged: the context picker leaves with the toggle and comes back with it.
        await collapse.click();
        await expect(differ.getByTestId("text-diff-context-lines")).toHaveCount(0);
        await collapse.click();
        await expect(differ.getByTestId("text-diff-context-lines")).toBeVisible();
        await chooseSelectOption(
            page,
            differ.getByTestId("text-diff-context-lines").getByRole("button"),
            /^10 lines$/,
            "the context-lines picker"
        );
        await expect(differ.getByTestId("text-diff-context-lines")).toContainText("10 lines");

        // Inline layout carries both names in its single title block.
        await segment(controls, "Inline").click();
        await expect(differ.getByTestId("text-diff-title-left")).toContainText("\u2192");
        await page.keyboard.press("Escape");
    });

    test("search Compare/View Selected enable by count and type", async ({ page }) => {
        await page.goto(`/#/search/${A!.databaseId}/assets`, { waitUntil: "domcontentloaded" });
        await expect(page.getByRole("button", { name: "Files", exact: true }).first()).toBeVisible({
            timeout: 60_000,
        });
        await page.getByRole("button", { name: "Files", exact: true }).first().click();
        await page.getByPlaceholder(/Search by keywords/i).fill("*");
        await page.getByPlaceholder(/Search by keywords/i).press("Enter");

        const row = (name: string) =>
            page.locator("table tbody tr").filter({ hasText: name }).first();
        await expect(row(TEXT)).toBeVisible({ timeout: 60_000 });
        await expect(row(PNG)).toBeVisible({ timeout: 60_000 });

        await page.getByRole("button", { name: "Multi-select to view" }).click();
        const view = page.getByRole("button", { name: /^View Selected \(\d+\)$/ });
        const compare = page.getByRole("button", { name: /^Compare Selected \(\d+\)$/ });
        await expect(view).toBeVisible({ timeout: 30_000 });

        // One text file: viewable, not comparable (below the differ's minimum).
        await row(TEXT).getByRole("checkbox").check();
        await expect(view).toHaveText(/\(1\)/);
        await expect(view).toBeEnabled();
        await expect(compare).toBeDisabled();

        // Two text files: comparable, not viewable (no multi-file text visualizer).
        await row(JSON_FILE).getByRole("checkbox").check();
        await expect(compare).toHaveText(/\(2\)/);
        await expect(compare).toBeEnabled();
        await expect(view).toBeDisabled();

        // Text + PNG: neither a differ nor a multi-file visualizer covers the pair.
        await row(JSON_FILE).getByRole("checkbox").uncheck();
        await row(PNG).getByRole("checkbox").check();
        await expect(compare).toHaveText(/\(2\)/);
        await expect(compare).toBeDisabled();
        await expect(view).toBeDisabled();

        // Opening the comparable pair lands on the differ.
        await row(PNG).getByRole("checkbox").uncheck();
        await row(JSON_FILE).getByRole("checkbox").check();
        await compare.click();
        const modal = dialog(page);
        await expect(modal).toBeVisible({ timeout: 30_000 });
        await expect(modal.getByLabel("Viewer mode")).toHaveCount(0);
        await expectDifferRendered(modal);
        await page.keyboard.press("Escape");
    });
});
