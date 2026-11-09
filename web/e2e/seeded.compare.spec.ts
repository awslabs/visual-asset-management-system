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
    expectNoNewCspViolations,
    treeNode,
    viewerSamples,
    watchConsoleErrors,
    watchCspViolations,
    watchRequestHosts,
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
 *     Compare with no Visualize/Compare toggle; one text file opens on Visualize WITH the toggle
 *     (a differ diffs it against another version of itself) and Compare seeds it against latest;
 *     one PNG opens on Visualize with no toggle.
 *  3. The version file list offers a per-row "Compare" for a text file and none for the PNG, and the
 *     action diffs the snapshot's version (left) against latest (right).
 *  4. The text differ's controls — Line/Word/Character granularity, line numbers, collapse unchanged
 *     with a context-lines picker — render and change the rendered diff.
 *  5. Search "Compare Selected" enables only for a selection a compare viewer admits (count + types)
 *     and "View Selected" only for one a visualize viewer admits.
 *
 * "compare mode: files without an extension" (#1198) runs on the viewer samples instead
 * (`E2E_VIEWER_SAMPLES`, from `tools/VamsCLI/examples/seed_viewer_samples.py`; its version-row case
 * also needs that script's `--asset-version`), so each block skips on its own seed.
 */

const seed = compareSeed();

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
    test.skip(
        !seed,
        "E2E_COMPARE_SEED is not set (or unreadable): run tools/VamsCLI/examples/seed_compare_smoke.py " +
            "and export the path it prints"
    );

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

    test("modal offers the toggle for one text file: Visualize first, Compare seeds it vs latest", async ({
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
        // A lone text file admits both modes (a differ diffs it against another version of itself),
        // so the toggle is offered — top-right — and Visualize is what renders first.
        const toggle = modal.getByTestId("file-viewer-mode-toggle");
        await expect(toggle).toBeVisible();
        await expect(modal.getByTestId("text-diff-viewer")).toHaveCount(0);

        // Compare seeds [this file (latest), same file at latest]: an identical pair, said so.
        await toggle.getByRole("button", { name: "Compare" }).click();
        await expect(modal.getByText(new RegExp(`Compare Files - ${TEXT}`))).toBeVisible();
        await expectDifferRendered(modal);
        await expect(modal.getByTestId("text-diff-identical-notice")).toBeVisible();
        await expect(modal.getByTestId("text-diff-side-label-left")).toHaveText(/\(latest\)/);
        await expect(modal.getByTestId("text-diff-side-label-right")).toHaveText(/\(latest\)/);

        // Back to Visualize: the single file again, no differ.
        await toggle.getByRole("button", { name: "Visualize" }).click();
        await expect(modal.getByText(new RegExp(`File Viewer - ${TEXT}`))).toBeVisible();
        await expect(modal.getByTestId("text-diff-viewer")).toHaveCount(0);
        await page.keyboard.press("Escape");
    });

    test("modal offers no toggle for one PNG: no differ admits its type", async ({ page }) => {
        await openFileManager(page, A!.databaseId, A!.assetId);
        await selectTreePath(page, PNG);

        const eye = page.getByRole("button", { name: `Visualize File ${PNG}` });
        await expect(eye).toBeVisible({ timeout: 30_000 });
        await eye.click();
        const modal = dialog(page);
        await expect(modal).toBeVisible({ timeout: 30_000 });
        await expect(modal.getByText(new RegExp(`File Viewer - ${PNG}`))).toBeVisible();
        await expect(modal.getByLabel("Viewer mode")).toHaveCount(0);
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

        // Select the first (only) asset-version snapshot; its file list appears below. The Versions
        // table first renders a loading row, which has no radio — wait for a real row's radio before
        // deciding there is nothing to select, or a present version is skipped as absent.
        const radio = page.locator("table tbody tr").getByRole("radio").first();
        const hasVersion = await radio
            .waitFor({ state: "visible", timeout: 60_000 })
            .then(() => true)
            .catch(() => false);
        test.skip(!hasVersion, "No asset version to select in this environment");
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
        // The differ highlights with react-syntax-highlighter (#1174): no new CSP violation and no
        // request outside the deployment while it renders and re-renders.
        const csp = await watchCspViolations(page);
        const hosts = watchRequestHosts(page);
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

        await expectNoNewCspViolations(csp, [], "the text differ");
        expect(await hosts.thirdParty(), "the differ reached hosts outside the deployment").toEqual(
            []
        );
        await page.keyboard.press("Escape");
    });

    test("search Compare/View Selected enable by count and type", async ({ page }) => {
        await page.goto(`/#/search/${A!.databaseId}/assets`, { waitUntil: "domcontentloaded" });
        await expect(page.getByRole("button", { name: "Files", exact: true }).first()).toBeVisible({
            timeout: 60_000,
        });
        await page.getByRole("button", { name: "Files", exact: true }).first().click();
        // An EMPTY query is match_all (the route already scopes to the seeded database); the backend
        // escapes any typed text and wraps it in wildcards, so a literal "*" matches nothing.
        const keywords = page.getByPlaceholder(/Search by keywords/i);
        await keywords.fill("");
        await keywords.press("Enter");

        const row = (name: string) =>
            page.locator("table tbody tr").filter({ hasText: name }).first();
        await expect(row(TEXT)).toBeVisible({ timeout: 60_000 });
        await expect(row(JSON_FILE)).toBeVisible({ timeout: 60_000 });
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

const samples = viewerSamples();

/**
 * #1198: an extensionless file (README, LICENSE), a dotfile and a binary with no extension in the
 * compare surfaces. Subjects are the seeded viewer samples under `/noext/`; nothing is mutated.
 */
test.describe("compare mode: files without an extension (seeded viewer samples)", () => {
    test.skip(
        !samples,
        "E2E_VIEWER_SAMPLES is not set (or unreadable): run " +
            "tools/VamsCLI/examples/seed_viewer_samples.py and export the path it prints"
    );
    const DB = samples?.databaseId ?? "";
    const ASSET = samples?.assetId ?? "";
    const keyOf = (id: string): string | undefined => samples?.samples[id]?.key;
    const nameOf = (id: string): string => (keyOf(id) ?? "").split("/").pop() ?? "";
    const needSamples = (...ids: string[]) => {
        const missing = ids.filter((id) => !keyOf(id));
        test.skip(
            missing.length > 0,
            `the seed fixture lacks ${missing.join(", ")}; re-run the seed`
        );
    };

    /**
     * The first table row with a cell naming exactly this file (the cell ends in `/<name>` or is
     * `<name>`), so "README" never matches a "README.md" row and "LICENSE" never "LICENSE.txt".
     */
    const rowOf = (page: Page, fileName: string): Locator => {
        const escaped = fileName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        // e2e test helper: the file name is escaped before it is interpolated into the RegExp.
        const exact = new RegExp(`(^|/)${escaped}$`); // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp
        return page
            .locator("table tbody tr")
            .filter({ has: page.getByRole("cell", { name: exact }) })
            .first();
    };

    /** Select the first sample through its folder, then Ctrl-add the second in the same folder. */
    async function selectPair(page: Page, first: string, second: string): Promise<void> {
        await openFileManager(page, DB, ASSET);
        await selectTreePath(page, keyOf(first)!);
        const node = treeNode(page, nameOf(second));
        await expect(node).toBeVisible({ timeout: 60_000 });
        await node.click({ modifiers: ["Control"] });
    }

    test("LICENSE vs LICENSE.txt opens the differ, with no multi-file visualizer offered", async ({
        page,
    }) => {
        needSamples("noext-license", "license-txt");
        const errors = watchConsoleErrors(page);
        await selectPair(page, "noext-license", "license-txt");

        const compareIcon = page.getByRole("button", { name: "Compare Selected Files" });
        await expect(compareIcon).toBeVisible({ timeout: 30_000 });
        await expect(page.getByRole("button", { name: "Visualize Selected Files" })).toHaveCount(0);
        await compareIcon.click();
        const modal = dialog(page);
        await expect(modal.getByText(/Compare Files - 2 Files/)).toBeVisible({ timeout: 30_000 });
        await expectDifferRendered(modal);
        // Same text both sides: no side is refused, and the pair is said to be identical.
        await expect(
            modal.locator('[data-testid^="text-diff-side-"][data-testid$="binary"]')
        ).toHaveCount(0);
        await expect(modal.getByTestId("text-diff-identical-notice")).toBeVisible();
        expect(errors, `console errors:\n${errors.join("\n")}`).toEqual([]);
        await page.keyboard.press("Escape");
    });

    test("a binary side shows its notice and the readable side still renders", async ({ page }) => {
        needSamples("binary-noext", "noext-license");
        const errors = watchConsoleErrors(page);
        await selectPair(page, "binary-noext", "noext-license");

        await page.getByRole("button", { name: "Compare Selected Files" }).click();
        const modal = dialog(page);
        // A notice on one side replaces the diff table with two per-side panes, so there is no
        // `diff-container` to wait for: assert each pane on its own instead.
        await expect(modal.getByTestId("text-diff-viewer")).toBeVisible({ timeout: 60_000 });
        await expect(modal.getByTestId("text-diff-side-binary")).toBeVisible({ timeout: 60_000 });
        await expect(modal.getByText("Binary file")).toBeVisible();
        // The readable side renders on its own pane.
        await expect(modal.getByText("Apache License", { exact: false }).first()).toBeVisible({
            timeout: 60_000,
        });
        await expect(modal.locator('[class*="diff-container"]')).toHaveCount(0);
        expect(errors, `console errors:\n${errors.join("\n")}`).toEqual([]);
        await page.keyboard.press("Escape");
    });

    test("one README offers both Visualize and Compare in the file viewer", async ({ page }) => {
        needSamples("noext-readme");
        await openFileManager(page, DB, ASSET);
        await selectTreePath(page, keyOf("noext-readme")!);
        const name = nameOf("noext-readme");

        const eye = page.getByRole("button", { name: `Visualize File ${name}` });
        await expect(eye).toBeVisible({ timeout: 30_000 });
        await eye.click();
        const modal = dialog(page);
        await expect(modal.getByText(new RegExp(`File Viewer - ${name}`))).toBeVisible({
            timeout: 30_000,
        });
        const toggle = modal.getByTestId("file-viewer-mode-toggle");
        await expect(toggle.getByRole("button", { name: "Visualize" })).toBeVisible();
        await expect(toggle.getByRole("button", { name: "Compare" })).toBeVisible();
        await expect(modal.getByText("ZLIB DATA COMPRESSION LIBRARY").first()).toBeVisible({
            timeout: 60_000,
        });
        await page.keyboard.press("Escape");
    });

    test("the version file list offers Compare for README and diffs the row's version vs latest", async ({
        page,
    }) => {
        needSamples("noext-readme");
        test.skip(
            !samples?.assetVersion,
            "the viewer samples were seeded without --asset-version, so README has no snapshot row"
        );
        const name = nameOf("noext-readme");
        await openFileManager(page, DB, ASSET);
        await page
            .getByRole("tab", { name: /^versions$/i })
            .first()
            .click();
        const radio = page.locator("table tbody tr").getByRole("radio").first();
        await expect(radio).toBeVisible({ timeout: 60_000 });
        await radio.click();

        const row = rowOf(page, name);
        await expect(row).toBeVisible({ timeout: 60_000 });
        const compare = row.getByRole("button", { name: "Compare", exact: true });
        await expect(compare).toBeVisible({ timeout: 30_000 });
        await compare.click();
        const modal = dialog(page);
        await expectDifferRendered(modal);
        await expect(modal.getByTestId("text-diff-side-label-left")).toContainText("@");
        await expect(modal.getByTestId("text-diff-side-label-right")).toContainText("(latest)");
        await page.keyboard.press("Escape");
    });

    test("search: README is viewable, README + PDF is neither viewable nor comparable, .gitignore is viewable", async ({
        page,
    }) => {
        needSamples("noext-readme", "dotfile-gitignore", "pdf");
        await page.goto(`/#/search/${DB}/assets`, { waitUntil: "domcontentloaded" });
        const filesTab = page.getByRole("button", { name: "Files", exact: true }).first();
        await expect(filesTab).toBeVisible({ timeout: 60_000 });
        await filesTab.click();
        const keywords = page.getByPlaceholder(/Search by keywords/i);
        await keywords.fill("");
        await keywords.press("Enter");

        const row = (fileName: string) => rowOf(page, fileName);
        const readme = row(nameOf("noext-readme"));
        const gitignore = row(nameOf("dotfile-gitignore"));
        const pdf = row(nameOf("pdf"));
        await expect(readme).toBeVisible({ timeout: 60_000 });
        await expect(gitignore).toBeVisible({ timeout: 60_000 });
        await expect(pdf).toBeVisible({ timeout: 60_000 });

        // The per-row eye: an extensionless file and a dotfile are judged by the key, not str_fileext.
        await expect(readme.getByRole("button", { name: /^Visualize File / })).toBeVisible();
        await expect(gitignore.getByRole("button", { name: /^Visualize File / })).toBeVisible();

        await page.getByRole("button", { name: "Multi-select to view" }).click();
        const view = page.getByRole("button", { name: /^View Selected \(\d+\)$/ });
        const compare = page.getByRole("button", { name: /^Compare Selected \(\d+\)$/ });
        await expect(view).toBeVisible({ timeout: 30_000 });

        // README alone: viewable.
        await readme.getByRole("checkbox").check();
        await expect(view).toHaveText(/\(1\)/);
        await expect(view).toBeEnabled();

        // README + PDF: no multi-file viewer and no differ covers the pair.
        await pdf.getByRole("checkbox").check();
        await expect(view).toHaveText(/\(2\)/);
        await expect(view).toBeDisabled();
        await expect(compare).toBeDisabled();

        // .gitignore alone: viewable, and View Selected opens the Text Viewer on it.
        await readme.getByRole("checkbox").uncheck();
        await pdf.getByRole("checkbox").uncheck();
        await gitignore.getByRole("checkbox").check();
        await expect(view).toBeEnabled();
        await view.click();
        const modal = dialog(page);
        await expect(modal).toBeVisible({ timeout: 30_000 });
        await expect(modal.getByText("# Logs", { exact: false }).first()).toBeVisible({
            timeout: 60_000,
        });
        await page.keyboard.press("Escape");
    });
});
