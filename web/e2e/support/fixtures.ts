/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, Locator, Page } from "@playwright/test";

/**
 * Reusable Playwright harness for the VAMS orchestration UI.
 *
 * Environment-agnostic by design: nothing here assumes a particular pipeline, workflow, or
 * execution exists. Helpers either work against whatever the environment happens to contain, or
 * skip the test when a precondition is genuinely absent. That makes the specs built on this
 * harness safe to run against any sandbox — empty, freshly seeded, or long-lived.
 *
 * The selector helpers are the durable value here: they encode where the app's controls actually
 * live, which is what breaks when markup changes.
 */

/** Orchestration pages are scoped under `.orchestration-root`; the global nav also has a Search. */
export function orchestrationRoot(page: Page): Locator {
    return page.locator(".orchestration-root");
}

/** The orchestration filter-bar search input, found by its label (not its placeholder); NOT the global search. */
export function searchBox(page: Page): Locator {
    return orchestrationRoot(page).getByLabel("Search", { exact: true }).first();
}

/** A native <select> facet, e.g. "Status", "Execution Type", "Database", "Group by". */
export function facet(page: Page, label: string): Locator {
    return page.getByLabel(label, { exact: true });
}

/** Rows currently rendered in the page's data table. */
export function tableRows(page: Page): Locator {
    return page.locator("table tbody tr");
}

/** The execute dialog's step rail — one navigation landmark, present on every step of the dialog. */
export function wizardRail(page: Page): Locator {
    return page.getByRole("navigation", { name: "Execution steps" });
}

/**
 * Navigate to an orchestration page and wait for it to finish its first load. Waits on the page's
 * own heading — never on specific data — so an empty environment is a valid state.
 */
export async function gotoOrchestration(
    page: Page,
    route: "pipelines" | "workflows" | "executions",
    heading: RegExp | string
): Promise<void> {
    await page.goto(`/#/${route}/`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({
        timeout: 60_000,
    });
    // The list issues its first request after mount; settle before asserting on content.
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

/**
 * The deployment's feature list, read from the app's own cached config (localStorage
 * `vams_cache_config`) — what the running application itself reads. Not `/api/secure-config` through
 * `page.request`: that carries no bearer token, answers 401, and every feature then reads as disabled.
 * The entry is written after the app boots, later than `page.goto` resolving, so it is polled to a
 * short deadline; an empty list after the deadline is returned as such.
 */
export async function cachedFeaturesEnabled(page: Page): Promise<string[]> {
    if (!page.url().startsWith("http")) {
        await page.goto("/", { waitUntil: "domcontentloaded" });
    }
    const deadline = Date.now() + 20_000;
    for (;;) {
        const features = await page.evaluate(() => {
            try {
                const raw = localStorage.getItem("vams_cache_config");
                if (!raw) return null;
                const parsed = JSON.parse(raw);
                const config = parsed?.value ?? parsed ?? {};
                return (config.featuresEnabled ?? null) as string[] | null;
            } catch {
                return null;
            }
        });
        if (features && features.length) return features;
        if (Date.now() > deadline) return features ?? [];
        await page.waitForTimeout(1000);
    }
}

/**
 * The id of the first card in a card list (pipelines / workflows), or null when the environment has
 * none. Cards expose an "Actions for {id}" button, which is the stable way to read their identity.
 */
export async function firstCardId(page: Page): Promise<string | null> {
    const actions = page.getByRole("button", { name: /Actions for/i }).first();
    if ((await actions.count()) === 0) return null;
    const label = (await actions.getAttribute("aria-label")) || "";
    const m = label.match(/Actions for\s+(.+)$/i);
    return m ? m[1].trim() : null;
}

/**
 * Open a card's actions menu, filtering the list down to it first so a server-paginated list
 * (pageSize 50) does not hide the target. Returns the menu items locator.
 */
export async function openCardMenu(page: Page, cardId: string): Promise<Locator> {
    await searchBox(page).fill(cardId);
    await expect(page.getByText(cardId, { exact: false }).first()).toBeVisible({ timeout: 45_000 });
    const card = page
        .locator("div", { hasText: cardId })
        .filter({ has: page.getByRole("button", { name: /Actions for/i }) })
        .last();
    await card.scrollIntoViewIfNeeded();
    await card.getByRole("button", { name: /Actions for/i }).click();
    return page.getByRole("menuitem");
}

/**
 * The floating surface a menu item belongs to, reached from the item rather than from `[role="menu"]`.
 *
 * A page carries a dozen or more zero-size Cloudscape `<ul role="menu">` option lists (every closed
 * Select/ButtonDropdown keeps one in the DOM), and they precede the portalled Radix menu in document
 * order — so `page.locator('[role="menu"]').first()` resolves to a hidden one with a transparent
 * background. Walking up from a visible item picks the surface that is actually open.
 */
export function menuSurface(items: Locator): Locator {
    return items.first().locator('xpath=ancestor::*[@role="menu"][1]');
}

/**
 * The value cell of a label/value row in a detail or quick-view panel. Rows render the label and its
 * value as adjacent spans, so the value is the label's next sibling — scoping to it keeps an assertion
 * about one field from matching text anywhere else on the page.
 */
export function rowValue(page: Page, label: string): Locator {
    return page.getByText(label, { exact: true }).first().locator("xpath=following-sibling::*[1]");
}

/** Collect uncaught page errors for the duration of a test (crash-regression assertions). */
export function collectPageErrors(page: Page): string[] {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    return errors;
}

/**
 * Assert a table either has rows or shows its empty state — never that specific data exists. Use
 * this to prove a page renders in ANY environment. `emptyState` is the page's own empty-state copy
 * when it does not read "No … found".
 */
export async function expectTableRendered(
    page: Page,
    emptyState: RegExp = /no .*found/i
): Promise<number> {
    const rows = tableRows(page);
    await expect
        .poll(
            async () => (await rows.count()) > 0 || (await page.getByText(emptyState).count()) > 0,
            {
                timeout: 60_000,
            }
        )
        .toBe(true);
    return rows.count();
}

// ---------------------------------------------------------------------------------------------
// Responsive layout
// ---------------------------------------------------------------------------------------------

/** The widths the layout specs check: a phone, a tablet, and two common laptop widths. */
export const VIEWPORTS = {
    phone: { width: 390, height: 844 },
    tablet: { width: 768, height: 1024 },
    laptop: { width: 1280, height: 800 },
    desktop: { width: 1440, height: 900 },
} as const;

/**
 * How far the document is wider than its viewport, in pixels (0 when it fits). `clientWidth`
 * excludes a vertical scrollbar, so a page that only scrolls vertically reads 0.
 */
export async function pageOverflowX(page: Page): Promise<number> {
    return page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
}

/**
 * Assert the page does not scroll sideways. The failure message names the widest element so a
 * regression points at its cause rather than at the page.
 */
export async function expectNoPageOverflowX(page: Page, where: string): Promise<void> {
    const overflow = await pageOverflowX(page);
    if (overflow > 0) {
        const widest = await page.evaluate(() => {
            const limit = document.documentElement.clientWidth;
            let found = "";
            let right = limit;
            document.querySelectorAll("body *").forEach((el) => {
                const r = el.getBoundingClientRect();
                if (r.right > right + 0.5 && r.width > 0) {
                    right = r.right;
                    const cls = typeof el.className === "string" ? el.className : "";
                    found = `${el.tagName.toLowerCase()}.${cls.split(/\s+/).slice(0, 4).join(".")}`;
                }
            });
            return `${found} ends at ${Math.round(right)}px`;
        });
        expect(
            overflow,
            `${where}: page is ${overflow}px wider than the viewport (${widest})`
        ).toBe(0);
    }
}

/** Scroll a control into view and assert it then lies fully inside the viewport. */
export async function expectReachable(target: Locator, what: string): Promise<void> {
    await target.scrollIntoViewIfNeeded();
    const box = await target.boundingBox();
    const viewport = target.page().viewportSize();
    expect(box, `${what} has no box`).not.toBeNull();
    expect(viewport).not.toBeNull();
    expect(box!.x, `${what} starts left of the viewport`).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width, `${what} ends right of the viewport`).toBeLessThanOrEqual(
        viewport!.width + 0.5
    );
}

// ---------------------------------------------------------------------------------------------
// Asset file viewer
// ---------------------------------------------------------------------------------------------

/**
 * Click a locator, retrying when the element is present but not yet actionable.
 *
 * The asset page re-renders while its tree and detail panel settle, so a click can find its target and
 * still fail the actionability checks — Playwright reports `locator.click: Timeout 20000ms exceeded`
 * with no hint that the element was there all along. That produced intermittent failures in the viewer
 * specs on roughly one run in three, on a different case each time.
 *
 * Retrying is correct here rather than papering over a defect: the target is proven present by the
 * caller's own visibility assertion before this is reached.
 */
async function clickWhenActionable(target: Locator, what: string, attempts = 4): Promise<void> {
    let last: unknown;
    for (let attempt = 0; attempt < attempts; attempt++) {
        try {
            await target.scrollIntoViewIfNeeded().catch(() => undefined);
            await target.click({ timeout: 8_000 });
            return;
        } catch (err) {
            last = err;
            await target.page().waitForTimeout(1_500);
        }
    }
    throw new Error(
        `could not click ${what} after ${attempts} attempts: ${String(last).slice(0, 200)}`
    );
}

/**
 * Open one of an asset's files in the File Visualizer, the way a user does.
 *
 * The File Manager navigates to `.../assets/{id}/file` and passes the file through React Router
 * STATE; a direct `.../file/<encoded key>` link also opens a file, but this helper takes the path a
 * user takes. Three things about that page are easy to get wrong and cost a long detour each:
 *
 *  - Its own heading is an **h2**. The orchestration routes have an h1; this page does not, so waiting
 *    on `heading, level: 1` matches nothing and times out.
 *  - Files are **tree nodes, not table rows**, and need an EXACT text match — the panel repeats file
 *    names in its summary text, so a substring match resolves to a container.
 *  - **View File is hidden when the asset is not distributable.** A non-distributable asset therefore
 *    looks like a missing button rather than a permissions/flag condition.
 *
 * A render crash is also reported distinctly. The page's error boundary keeps the shell alive, so a
 * crash's only symptom is the heading never arriving — which otherwise reads as "the file is not
 * listed" and blames the data.
 */
export async function openAssetFile(
    page: Page,
    databaseId: string,
    assetId: string,
    filename: string
): Promise<void> {
    await openFileManager(page, databaseId, assetId);
    await selectTreePath(page, filename);

    const view = page.getByRole("button", { name: /view file/i }).first();
    await expect(view, "View File is absent — is the asset distributable?").toBeVisible({
        timeout: 30_000,
    });
    await clickWhenActionable(view, "the View File button");
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

/**
 * Land on an asset's File Manager tab with its tree rendered. The first half of {@link openAssetFile};
 * on its own for specs that select files in the tree (multi-select, the detail panel's viewer icons)
 * rather than opening one in the File Visualizer.
 */
export async function openFileManager(
    page: Page,
    databaseId: string,
    assetId: string
): Promise<void> {
    await page.goto(`/#/databases/${databaseId}/assets/${assetId}`, {
        waitUntil: "domcontentloaded",
    });
    try {
        await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible({
            timeout: 60_000,
        });
    } catch (err) {
        if (await page.getByText(/Something went wrong on this page/i).count()) {
            throw new Error(
                `the asset detail page hit its error boundary for ${databaseId}/${assetId}. The ` +
                    `shell survived, but a component threw during render — React error boundaries ` +
                    `do not surface as 'pageerror', so check the console transcript and network log.`
            );
        }
        throw err;
    }
    await page.waitForLoadState("networkidle").catch(() => undefined);

    const tab = page.getByRole("tab", { name: /file manager/i }).first();
    if ((await tab.count()) && (await tab.getAttribute("aria-selected")) !== "true") {
        await tab.click();
    }

    // Wait for the tree itself, not just the network. `networkidle` resolves before the File Manager
    // has rendered its nodes, so clicking straight after it races an empty tree — which surfaces as
    // "<file> is not listed" and looks like missing data.
    await expect
        .poll(async () => await page.getByText(/Hold Ctrl or Shift to select/i).count(), {
            timeout: 60_000,
        })
        .toBeGreaterThan(0);
}

/**
 * Select a file in the File Manager tree by its asset-relative path, expanding folders on the way.
 * A file inside a folder is not visible until its folder is expanded, so walk the path segments and
 * click each one; passing "tileset/tileset.json" as a single label matches nothing. Pass
 * `modifiers: ["Control"]` to ADD the file to the current selection (multi-select).
 */
export async function selectTreePath(
    page: Page,
    filename: string,
    options: { modifiers?: Array<"Control" | "Shift"> } = {}
): Promise<void> {
    const segments = filename.split("/").filter(Boolean);
    for (const segment of segments) {
        const node = treeNode(page, segment);
        await expect(node, `${segment} is not listed in the file tree`).toBeVisible({
            timeout: 60_000,
        });
        const isLeaf = segment === segments[segments.length - 1];
        if (isLeaf && options.modifiers?.length) {
            await node.scrollIntoViewIfNeeded().catch(() => undefined);
            await node.click({ modifiers: options.modifiers, timeout: 8_000 });
        } else {
            await clickWhenActionable(node, `tree node "${segment}"`);
        }
        if (!isLeaf) {
            // Give the tree a moment to render the newly revealed children.
            await page.waitForTimeout(1200);
        }
    }
}

/**
 * The seed fixture the compare-mode specs read, or null when none is configured.
 *
 * `E2E_COMPARE_SEED` names the JSON that `tools/VamsCLI/examples/seed_compare_smoke.py` writes, so a
 * tracked spec never carries a seed id: the ids live in a generated, git-ignored file, and a spec skips
 * with a reason when the variable is unset or the file is gone.
 */
export interface CompareSeed {
    databases: string[];
    assets: {
        a: { databaseId: string; assetId: string; files: string[] };
        b: { databaseId: string; assetId: string; files: string[] };
    };
    textFile: string;
    jsonFile: string;
    markdownFile: string;
    binaryFile: string;
    versionedFile: string;
    versionedFileMinVersions: number;
}

export function compareSeed(): CompareSeed | null {
    const path = process.env.E2E_COMPARE_SEED;
    if (!path) return null;
    try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const fs = require("fs") as typeof import("fs");
        return JSON.parse(fs.readFileSync(path, "utf8")) as CompareSeed;
    } catch {
        return null;
    }
}

/**
 * The seed fixture the viewer render cases read, or null when none is configured.
 *
 * `E2E_VIEWER_SAMPLES` names the JSON that `tools/VamsCLI/examples/seed_viewer_samples.py` writes after
 * uploading the public samples listed in `e2e/fixtures/viewer-samples.json` to one distributable asset.
 * Each sample is addressed by its manifest id, so a render case opens a known file with known content
 * rather than the first file of an extension, which in a shared environment may be anything.
 */
export interface ViewerSamples {
    databaseId: string;
    assetId: string;
    samples: Record<
        string,
        {
            key: string;
            viewer: string;
            /** Features the deployment must enable for this sample's viewer (may be empty). */
            requiresFeature?: string[];
            /** Free-form expectations the manifest entry carries for its case. */
            expect?: Record<string, any>;
        }
    >;
}

export function viewerSamples(): ViewerSamples | null {
    const path = process.env.E2E_VIEWER_SAMPLES;
    if (!path) return null;
    try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const fs = require("fs") as typeof import("fs");
        return JSON.parse(fs.readFileSync(path, "utf8")) as ViewerSamples;
    } catch {
        return null;
    }
}

/**
 * A row in the asset File Manager's file tree, by name.
 *
 * Two facts make a plain `getByText(name, { exact: true })` wrong here, and both fail in ways that do
 * not look like selector problems:
 *
 *  1. **The page mounts TWO trees.** The visible one is `.directory-tree` (rows `.tree-item-name`); a
 *     second, folder-only `.folder-tree-view` (rows `.folder-tree-item-name`) is mounted for the
 *     move/copy destination picker and sits inside a `display:none` ancestor. Its folder labels ARE
 *     exact matches, so `getByText` resolves to the hidden copy and the assertion fails as "not
 *     visible" — reading like absent data rather than the wrong element. The give-away is a rect of
 *     0×0 at x=0,y=0, which is what `getBoundingClientRect` returns inside `display:none`.
 *  2. **A folder row's label includes its child count** — the node for `3DTiles` reads `3DTiles(2)`.
 *     So exact matching cannot find a folder in the visible tree at all, whatever the scope.
 *
 * Scoping to `.directory-tree` and allowing the optional `(n)` suffix handles files and folders alike.
 */
export function treeNode(page: Page, name: string): Locator {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // e2e test helper: the tree-node name is escaped with the standard metacharacter escape before
    // it is interpolated into the RegExp below.
    return page
        .locator(".directory-tree .tree-item-name")
        .filter({ hasText: new RegExp(`^${escaped}(\\(\\d+\\))?$`) }) // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp
        .first();
}

/**
 * Choose a viewer in the File Visualizer's picker, by KEYBOARD.
 *
 * When more than one viewer claims a file's extension the page deliberately selects none and shows
 * "Select viewer (required)" / "No Viewer Component Selected". That is correct behaviour, but it means
 * any assertion about such a file's rendered content has to make the selection first — and `.csv`,
 * `.json`, `.glb`, `.stp` and `.sog` all match two or more viewers.
 *
 * The selection mechanics live in `chooseSelectOption`; what is specific here is the confirmation. A
 * selection that silently failed would leave the page on its placeholder, and the caller's content
 * assertion would then fail for the wrong reason — blaming the viewer for a picker problem.
 */
export async function chooseViewer(page: Page, name: RegExp): Promise<void> {
    await chooseSelectOption(page, page.getByLabel(/select viewer/i), name, "the viewer picker");
    await expect
        .poll(async () => await page.getByText(/No Viewer Component Selected/i).count(), {
            timeout: 30_000,
        })
        .toBe(0);
}

/**
 * Choose an option in a Cloudscape Select, by KEYBOARD.
 *
 * The trigger is passed as a LOCATOR rather than a label, because these controls are not labelled
 * consistently: the viewer picker is reachable by label, while the File Manager's version selector is
 * only reachable by its button role/name. Requiring one lookup strategy would exclude the other.
 *
 * Mouse routes into these controls do not work. Recorded so they are not retried: a normal
 * `option.click()` races the dropdown's entry animation and its option-list re-render ("element is not
 * stable", then "detached from the DOM"); a forced click lands after the list has closed; dispatching
 * `.click()` on the option node in-page raises no error but never applies the selection; and a manual
 * `mouse.down()/up()` on the option's box likewise does nothing.
 *
 * The keyboard route works because the control implements the ARIA listbox pattern: opening it moves
 * focus to the listbox and highlights an option — the currently SELECTED one when the control has a
 * value, the first one otherwise. So move by INDEX — read the option texts, find where the highlight
 * starts, count ArrowDown presses from there, then Enter. Watching `aria-activedescendant` instead is
 * unreliable, because `document.querySelector("[aria-activedescendant]")` can land on another widget
 * entirely (the file tree carries one) and read an empty value while the listbox is perfectly healthy.
 *
 * Callers should confirm the selection took, in whatever terms their page expresses it — this helper
 * can only verify that an option matching `option` existed to be chosen.
 */
export async function chooseSelectOption(
    page: Page,
    trigger: Locator,
    option: RegExp,
    describeTrigger = "the select"
): Promise<void> {
    const picker = trigger.first();
    await expect(picker, `${describeTrigger} is absent`).toBeVisible({ timeout: 60_000 });

    // Open it, and RETRY if the option list is not there a moment later. The file page is still
    // re-rendering right after navigation — `networkidle` resolves before it settles — and a click that
    // lands mid-render opens the dropdown and then loses it, so the option list reads as empty and the
    // helper reports "no viewer option matching ..." with nothing offered.
    let texts: string[] = [];
    for (let attempt = 0; attempt < 5 && texts.length === 0; attempt++) {
        if (attempt > 0) await page.waitForTimeout(2000);
        if ((await page.locator('[role="option"]').count()) === 0) {
            await clickWhenActionable(picker, describeTrigger);
        }
        await page.waitForTimeout(800);
        texts = await page.locator('[role="option"]').allTextContents();
    }

    // Move by INDEX rather than by watching `aria-activedescendant`. Reading that attribute is not
    // reliable here: `document.querySelector("[aria-activedescendant]")` returns the first match in
    // document order, and the File Manager's own file tree carries one too, so the lookup can land on
    // the tree and read an empty value while the listbox is perfectly healthy. That produced an empty
    // "highlights walked" list and a false "no viewer option matching" failure.
    //
    // The walk starts from wherever the highlight lands on open, which is NOT always option 0: a Select
    // that already has a value opens with the highlight on that value (Cloudscape's `use-select`), and
    // ArrowDown wraps from the last option back to the first. Assuming 0 sent the compare differ's
    // context-lines picker (default "3 lines", index 2) four presses toward "10 lines" — 3, 4, 0, 1 —
    // and selected "1 line". So find the starting index first and walk the modular distance.
    //
    // The selected option is read from `aria-selected`, which Cloudscape sets from the same controlled
    // `selectedOption` it uses to place the highlight. The trigger's text is NOT a substitute: it can
    // be a placeholder, "1 line" is a prefix of "10 lines", and the highlighted option's textContent
    // carries a duplicate screen-reader announcement of its own label, so text equality fails for the
    // very option that matters. When nothing is marked selected the highlight is on option 0.
    const options = page.locator('[role="option"]');
    const index = texts.findIndex((t) => option.test(t));
    expect(
        index,
        `no option matching ${option} in ${describeTrigger}; offered: ` +
            texts.map((t) => t.slice(0, 34)).join(" | ")
    ).toBeGreaterThanOrEqual(0);
    const selected = await options.evaluateAll((nodes) =>
        nodes.findIndex((n) => n.getAttribute("aria-selected") === "true")
    );
    const start = selected >= 0 ? selected : 0;
    const presses = (index - start + texts.length) % texts.length;
    for (let i = 0; i < presses; i++) {
        await page.keyboard.press("ArrowDown");
        await page.waitForTimeout(200);
    }
    await page.keyboard.press("Enter");
}

// ---------------------------------------------------------------------------------------------
// API subject lookup
// ---------------------------------------------------------------------------------------------

/** The app's API base and bearer token, read from what the running app itself holds.
 *
 * Two things had to be learned from the deployment rather than assumed:
 *
 * - **The API is NOT same-origin `/api/*`.** The app calls the API Gateway origin directly (which is
 *   why `connect-src` names it). A request to `https://<web-host>/api/database` reaches a path that
 *   expects SigV4 and answers 403 with "Invalid key=value pair ... in Authorization header", while an
 *   unknown path falls through to the S3 origin and answers AccessDenied. Both look like auth
 *   failures and neither is.
 * - **`page.request` carries no Authorization header**, and every route behind the custom authorizer
 *   answers 401 without one. The token the authorizer validates is the Cognito ID token.
 */
export async function apiContext(page: Page): Promise<{ base: string; token: string } | null> {
    if (!page.url().startsWith("http")) {
        await page.goto("/", { waitUntil: "domcontentloaded" });
    }
    const deadline = Date.now() + 20_000;
    for (;;) {
        const ctx = await page.evaluate(() => {
            let base: string | null = null;
            try {
                const raw = localStorage.getItem("vams_cache_config");
                if (raw) {
                    const parsed = JSON.parse(raw);
                    const config = parsed?.value ?? parsed ?? {};
                    base = config.api ?? null;
                }
            } catch {
                base = null;
            }
            const keys = Object.keys(localStorage);
            const idKey = keys.find((k) => /CognitoIdentityServiceProvider.*\.idToken$/.test(k));
            const token = (idKey && localStorage.getItem(idKey)) || null;
            return { base, token };
        });
        if (ctx.base && ctx.token) {
            return { base: ctx.base.replace(/\/+$/, ""), token: ctx.token };
        }
        if (Date.now() > deadline) return null;
        await page.waitForTimeout(1000);
    }
}

/** A file an asset holds, addressed the way the ViewFile route addresses it. */
export interface AssetFileRef {
    databaseId: string;
    assetId: string;
    /** Asset-relative key with a leading slash, e.g. `/BoomBox.glb`. */
    key: string;
}

export interface FindAssetFilesOptions {
    /** Search only this asset, written `databaseId/assetId`. */
    asset?: string;
    /** Skip assets not flagged distributable; the download API refuses their files. */
    distributableOnly?: boolean;
    /** Skip files larger than this many bytes. */
    maxBytes?: number;
    /** How many databases to scan, in listing order. */
    maxDatabases?: number;
    /** How many assets of each database to scan, in listing order. */
    maxAssetsPerDatabase?: number;
}

/**
 * The first file found for each of `extensions`, keyed by the lower-cased extension, or null when the
 * running app exposes no API base or ID token to look files up with.
 *
 * An extension with no file in the scanned assets is absent from the map, which is the caller's cue to
 * `test.skip` with a reason. The scan stops once every extension has a file and is bounded by
 * `maxDatabases` × `maxAssetsPerDatabase`, so a large environment costs a fixed number of requests.
 * Files are listed in basic mode, which skips the per-object enrichment a key match does not need.
 *
 * A named `asset` is listed without the scan's filters, and fails the calling test when its files
 * cannot be listed, so a mistyped or unreadable id does not pass for an asset with no such files.
 */
export async function findAssetFiles(
    page: Page,
    extensions: string[],
    options: FindAssetFilesOptions = {}
): Promise<Map<string, AssetFileRef> | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const asJson = async (path: string) => {
        const r = await page.request.get(`${ctx.base}${path}`, { headers });
        if (!r.ok()) return null;
        const b = await r.json().catch(() => null);
        return b?.message ?? b ?? null;
    };
    const itemsOf = (body: any): any[] => {
        const items = body?.Items ?? body?.items ?? body ?? [];
        return Array.isArray(items) ? items : [];
    };

    const wanted = Array.from(new Set(extensions.map((e) => e.toLowerCase())));
    const found = new Map<string, AssetFileRef>();
    const collect = async (databaseId: string, assetId: string, maxBytes = Infinity) => {
        const files = await asJson(
            `/database/${databaseId}/assets/${assetId}/listFiles?basic=true`
        );
        for (const f of itemsOf(files)) {
            const relative: string = f?.relativePath ?? f?.key ?? "";
            if (!relative || f.isFolder || Number(f.size) > maxBytes) continue;
            const lower = relative.toLowerCase();
            const ext = wanted.find((e) => !found.has(e) && lower.endsWith(e));
            if (ext) {
                const key = relative.startsWith("/") ? relative : `/${relative}`;
                found.set(ext, { databaseId, assetId, key });
            }
        }
        return files !== null;
    };

    if (options.asset) {
        const [databaseId, assetId] = options.asset.split("/");
        const listed = Boolean(databaseId && assetId) && (await collect(databaseId, assetId));
        expect(
            listed,
            `the files of ${options.asset} could not be listed; write it as ` +
                `<databaseId>/<assetId> and name an asset this user can read`
        ).toBe(true);
        return found;
    }

    const databases = itemsOf(await asJson("/database"));
    for (const db of databases.slice(0, options.maxDatabases ?? 8)) {
        if (found.size === wanted.length) break;
        const databaseId = db?.databaseId;
        if (!databaseId) continue;
        const assets = itemsOf(await asJson(`/database/${databaseId}/assets`));
        for (const asset of assets.slice(0, options.maxAssetsPerDatabase ?? 20)) {
            if (found.size === wanted.length) break;
            const assetId = asset?.assetId;
            if (!assetId) continue;
            if (options.distributableOnly && asset.isDistributable !== true) continue;
            await collect(databaseId, assetId, options.maxBytes);
        }
    }
    return found;
}

/** The File Manager tree's single-selected row label, in the visible tree (see `treeNode`). */
export function selectedTreeNode(page: Page): Locator {
    return page.locator(".directory-tree .tree-item-content.selected .tree-item-name").first();
}

/**
 * A file inside a folder. `storedKey` is the S3 key the asset bucket stores it under (the asset's
 * root key followed by `key`, no leading slash), set only when the listing holds the asset's root
 * entry, the listed prefix's own object, which is where the File Manager reads the root key from.
 */
export interface NestedAssetFileRef extends AssetFileRef {
    storedKey?: string;
}

/**
 * The first live file found inside a folder (an asset-relative key with two or more segments, e.g.
 * `/out/sub/model.glb`), or null when the scan finds none or the running app exposes no API context.
 * Scans the way `findAssetFiles` does and is bounded the same way; a named `asset`
 * (`databaseId/assetId`) is listed on its own.
 */
export async function findNestedAssetFile(
    page: Page,
    options: Pick<FindAssetFilesOptions, "asset" | "maxDatabases" | "maxAssetsPerDatabase"> = {}
): Promise<NestedAssetFileRef | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const asJson = async (path: string) => {
        const r = await page.request.get(`${ctx.base}${path}`, { headers });
        if (!r.ok()) return null;
        const b = await r.json().catch(() => null);
        return b?.message ?? b ?? null;
    };
    const itemsOf = (body: any): any[] => {
        const items = body?.Items ?? body?.items ?? body ?? [];
        return Array.isArray(items) ? items : [];
    };
    const nestedIn = async (
        databaseId: string,
        assetId: string
    ): Promise<NestedAssetFileRef | null> => {
        const files = itemsOf(
            await asJson(`/database/${databaseId}/assets/${assetId}/listFiles?basic=true`)
        );
        const rootEntry = files.find(
            (f) => f?.fileName === "" && (f?.relativePath === "/" || f?.relativePath === "")
        );
        const rootKey: string =
            typeof rootEntry?.key === "string" ? rootEntry.key.replace(/^\/+|\/+$/g, "") : "";
        for (const f of files) {
            const relative: string = f?.relativePath ?? "";
            if (!relative || f.isFolder || f.isArchived || relative.endsWith("/")) continue;
            const key = relative.startsWith("/") ? relative : `/${relative}`;
            if (key.split("/").filter(Boolean).length >= 2) {
                const storedKey =
                    rootKey && f.key === `${rootKey}${key}` ? (f.key as string) : undefined;
                return { databaseId, assetId, key, storedKey };
            }
        }
        return null;
    };

    if (options.asset) {
        const [databaseId, assetId] = options.asset.split("/");
        return databaseId && assetId ? nestedIn(databaseId, assetId) : null;
    }
    const databases = itemsOf(await asJson("/database"));
    for (const db of databases.slice(0, options.maxDatabases ?? 8)) {
        const databaseId = db?.databaseId;
        if (!databaseId) continue;
        const assets = itemsOf(await asJson(`/database/${databaseId}/assets`));
        for (const asset of assets.slice(0, options.maxAssetsPerDatabase ?? 20)) {
            const assetId = asset?.assetId;
            if (!assetId) continue;
            const found = await nestedIn(databaseId, assetId);
            if (found) return found;
        }
    }
    return null;
}

/**
 * Up to `count` distributable assets, each with one live top-level file, or null when the running
 * app exposes no API context. Assets are taken in listing order across databases and the scan is
 * bounded like `findAssetFiles`, so fewer than `count` come back when the environment has fewer.
 */
export async function findAssetsWithFiles(
    page: Page,
    count: number,
    options: Pick<FindAssetFilesOptions, "maxDatabases" | "maxAssetsPerDatabase"> = {}
): Promise<AssetFileRef[] | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const asJson = async (path: string) => {
        const r = await page.request.get(`${ctx.base}${path}`, { headers });
        if (!r.ok()) return null;
        const b = await r.json().catch(() => null);
        return b?.message ?? b ?? null;
    };
    const itemsOf = (body: any): any[] => {
        const items = body?.Items ?? body?.items ?? body ?? [];
        return Array.isArray(items) ? items : [];
    };

    const found: AssetFileRef[] = [];
    const databases = itemsOf(await asJson("/database"));
    for (const db of databases.slice(0, options.maxDatabases ?? 8)) {
        const databaseId = db?.databaseId;
        if (!databaseId) continue;
        const assets = itemsOf(await asJson(`/database/${databaseId}/assets`));
        for (const asset of assets.slice(0, options.maxAssetsPerDatabase ?? 20)) {
            if (found.length >= count) return found;
            const assetId = asset?.assetId;
            if (!assetId || asset.isDistributable !== true) continue;
            const files = itemsOf(
                await asJson(`/database/${databaseId}/assets/${assetId}/listFiles?basic=true`)
            );
            const file = files.find((f) => {
                const relative: string = f?.relativePath ?? "";
                return (
                    relative &&
                    !f.isFolder &&
                    !f.isArchived &&
                    !relative.endsWith("/") &&
                    relative.replace(/^\/+/, "").split("/").length === 1
                );
            });
            if (file) {
                const relative: string = file.relativePath;
                const key = relative.startsWith("/") ? relative : `/${relative}`;
                found.push({ databaseId, assetId, key });
            }
        }
    }
    return found;
}

// ---------------------------------------------------------------------------------------------
// Viewer render proof: pixels, Content Security Policy violations and the hosts a page reaches.
// ---------------------------------------------------------------------------------------------

/** What a canvas shows, measured from a screenshot of it alone. */
export interface CanvasStats {
    width: number;
    height: number;
    /** Distinct colours after quantizing each channel to 4 bits. */
    distinctColours: number;
    /** Share of pixels that differ clearly from the most common (background) colour. */
    foregroundFraction: number;
    /** Standard deviation of the pixels' luminance, 0-255. */
    luminanceStdDev: number;
}

/**
 * The largest visible `<canvas>` in `scope`, the viewer's rendering surface. Viewers add small
 * canvases of their own (colour pickers, mini-maps, overlays), so the first one found is not
 * necessarily the scene.
 */
export async function largestCanvas(scope: Page | Locator): Promise<Locator> {
    const canvases = scope.locator("canvas");
    // One in-page read of every canvas's box: per-element `boundingBox()` calls wait on the
    // renderer and can time out while a heavy scene keeps a software-rendered page busy.
    const areas = await canvases.evaluateAll((elements) =>
        elements.map((element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            const hidden = style.visibility === "hidden" || style.display === "none";
            return hidden ? 0 : rect.width * rect.height;
        })
    );
    let best = -1;
    let bestArea = 0;
    areas.forEach((area, i) => {
        if (area > bestArea) {
            best = i;
            bestArea = area;
        }
    });
    expect(best, "no visible <canvas> to measure").toBeGreaterThanOrEqual(0);
    return canvases.nth(best);
}

/** How long each way of capturing a canvas may take, in milliseconds. */
export interface CaptureTimeouts {
    /** The element screenshot, tried first. */
    element?: number;
    /** The page screenshot clipped to the canvas box, tried when the element one times out. */
    clip?: number;
}

const ONLY_CANVASES =
    "* { visibility: hidden !important; } canvas { visibility: visible !important; }";

/** The first line of an error, for a combined failure message. */
const firstLine = (error: unknown): string => String(error).split("\n")[0];

/**
 * A PNG of `canvas` with every other element hidden. An element screenshot waits for the element
 * to be stable and scrolls it into view, which under software rendering (SwiftShader, no GPU) can
 * outlast any timeout on a heavy scene that keeps the page busy. When it times out, the canvas box
 * is read once in the page and a page screenshot is clipped to it, which needs no stability wait.
 * Both are bounded, and a failure of both names the likely cause rather than a bare timeout.
 */
export async function captureCanvas(
    page: Page,
    canvas: Locator,
    timeouts: CaptureTimeouts = {}
): Promise<Buffer> {
    const elementTimeout = timeouts.element ?? 15_000;
    const clipTimeout = timeouts.clip ?? 30_000;
    try {
        return await canvas.screenshot({
            animations: "disabled",
            style: ONLY_CANVASES,
            timeout: elementTimeout,
        });
    } catch (elementError) {
        try {
            const box = await canvas.evaluate(
                (element) => {
                    element.scrollIntoView({ block: "center", inline: "center" });
                    const rect = element.getBoundingClientRect();
                    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                },
                undefined,
                { timeout: clipTimeout }
            );
            const viewport = page.viewportSize();
            const x = Math.max(0, box.x);
            const y = Math.max(0, box.y);
            const width = Math.min(box.width, (viewport?.width ?? x + box.width) - x);
            const height = Math.min(box.height, (viewport?.height ?? y + box.height) - y);
            if (width < 1 || height < 1) {
                throw new Error(`the canvas box ${JSON.stringify(box)} is outside the viewport`);
            }
            return await page.screenshot({
                animations: "disabled",
                style: ONLY_CANVASES,
                clip: { x, y, width, height },
                timeout: clipTimeout,
            });
        } catch (clipError) {
            throw new Error(
                `could not capture the canvas: the element screenshot did not finish in ` +
                    `${elementTimeout} ms (${firstLine(elementError)}), and the page screenshot ` +
                    `clipped to its box did not finish in ${clipTimeout} ms (${firstLine(
                        clipError
                    )}). ` +
                    `A heavy scene under software rendering can keep the page this busy; rerun on a ` +
                    `GPU host or with a smaller sample.`
            );
        }
    }
}

/**
 * Pixel statistics of `canvas`, from a screenshot of it (`captureCanvas`) taken with every other
 * element hidden, so an overlay (a HUD, a toolbar) is not measured as scene content. Reading the canvas itself
 * would not work: WebGL viewers draw without `preserveDrawingBuffer`, so `toDataURL()` comes back
 * blank. The PNG is decoded in the page with `createImageBitmap`, from bytes passed in, so the
 * decode fetches nothing the Content Security Policy could refuse.
 */
export async function canvasStats(
    page: Page,
    canvas: Locator,
    timeouts: CaptureTimeouts = {}
): Promise<CanvasStats> {
    const png = await captureCanvas(page, canvas, timeouts);
    return await page.evaluate(async (base64: string) => {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
        const surface = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = surface.getContext("2d")!;
        context.drawImage(bitmap, 0, 0);
        const { data } = context.getImageData(0, 0, bitmap.width, bitmap.height);
        const pixels = data.length / 4;
        const counts = new Map<number, number>();
        let sum = 0;
        let sumSquares = 0;
        for (let p = 0; p < data.length; p += 4) {
            const quantized =
                ((data[p] >> 4) << 8) | ((data[p + 1] >> 4) << 4) | (data[p + 2] >> 4);
            counts.set(quantized, (counts.get(quantized) ?? 0) + 1);
            const luminance = 0.2126 * data[p] + 0.7152 * data[p + 1] + 0.0722 * data[p + 2];
            sum += luminance;
            sumSquares += luminance * luminance;
        }
        let background = 0;
        let backgroundCount = -1;
        counts.forEach((count, colour) => {
            if (count > backgroundCount) {
                background = colour;
                backgroundCount = count;
            }
        });
        const [br, bg, bb] = [(background >> 8) & 15, (background >> 4) & 15, background & 15];
        let foreground = 0;
        for (let p = 0; p < data.length; p += 4) {
            const far =
                Math.abs((data[p] >> 4) - br) > 1 ||
                Math.abs((data[p + 1] >> 4) - bg) > 1 ||
                Math.abs((data[p + 2] >> 4) - bb) > 1;
            if (far) foreground += 1;
        }
        const mean = sum / pixels;
        return {
            width: bitmap.width,
            height: bitmap.height,
            distinctColours: counts.size,
            foregroundFraction: foreground / pixels,
            luminanceStdDev: Math.sqrt(Math.max(0, sumSquares / pixels - mean * mean)),
        };
    }, png.toString("base64"));
}

/** Thresholds a rendered scene clears and a cleared (empty) canvas does not. */
export interface RenderThresholds {
    minForegroundFraction?: number;
    minDistinctColours?: number;
}

/**
 * Asserts that `canvas` shows content rather than its clear colour, polling until it does: a
 * viewer draws the model some frames after the canvas appears. Returns the last measurement so
 * the caller can log it beside the baseline run.
 */
export async function expectCanvasRendered(
    page: Page,
    canvas: Locator,
    what: string,
    thresholds: RenderThresholds = {},
    timeout = 90_000,
    capture: CaptureTimeouts = {}
): Promise<CanvasStats> {
    const minForeground = thresholds.minForegroundFraction ?? 0.01;
    const minColours = thresholds.minDistinctColours ?? 8;
    let last: CanvasStats | null = null;
    await expect(async () => {
        last = await canvasStats(page, canvas, capture);
        expect(
            last.foregroundFraction >= minForeground && last.distinctColours >= minColours,
            `${what} shows no rendered content: ${JSON.stringify(last)}`
        ).toBe(true);
    }).toPass({ timeout, intervals: [1_000, 2_000, 5_000] });
    return last!;
}

/** One Content Security Policy violation a document reported. */
export interface CspViolation {
    directive: string;
    blockedURI: string;
    sourceFile: string;
    frameUrl: string;
}

/**
 * Records every `securitypolicyviolation` event in the page and its frames from the first script
 * on. Call before navigating. `violations()` reads them back; compare them against an exact
 * allow-list of the violations the development baseline already shows, so only NEW ones fail.
 */
export async function watchCspViolations(page: Page) {
    await page.addInitScript(() => {
        const store: any[] = ((window as any).__vamsCspViolations = []);
        document.addEventListener("securitypolicyviolation", (event) => {
            store.push({
                directive: event.effectiveDirective || event.violatedDirective,
                blockedURI: event.blockedURI,
                sourceFile: event.sourceFile,
            });
        });
    });
    return {
        async violations(): Promise<CspViolation[]> {
            const all: CspViolation[] = [];
            for (const frame of page.frames()) {
                const found = await frame
                    .evaluate(() => ((window as any).__vamsCspViolations ?? []) as any[])
                    .catch(() => [] as any[]);
                for (const v of found) all.push({ ...v, frameUrl: frame.url() });
            }
            return all;
        },
    };
}

/** The violations in `violations` that no pattern in `allowed` matches. */
export function unexpectedCspViolations(
    violations: CspViolation[],
    allowed: RegExp[] = []
): string[] {
    return violations
        .map(
            (v) =>
                `${v.directive} blocked ${v.blockedURI || "(inline)"} in ${
                    v.sourceFile || v.frameUrl
                }`
        )
        .filter((line) => !allowed.some((pattern) => pattern.test(line)));
}

/** Fails the test with each recorded violation that no pattern in `allowed` matches. */
export async function expectNoNewCspViolations(
    csp: { violations(): Promise<CspViolation[]> },
    allowed: RegExp[] = [],
    what = "the page"
): Promise<void> {
    const unexpected = unexpectedCspViolations(await csp.violations(), allowed);
    expect(unexpected, `new CSP violations for ${what}:\n${unexpected.join("\n")}`).toEqual([]);
}

/**
 * Records the host of every request the page, its frames and its workers make. `thirdParty()`
 * lists the hosts outside the deployment: anything but the app origin, the API host, Amazon S3,
 * Amazon Cognito and other `amazonaws.com` endpoints. That is how a viewer that reaches out to a
 * public CDN at run time (unpkg, jsDelivr) fails, which air-gapped and GovCloud deployments cannot
 * serve. `options.extraHosts` adds hosts a case legitimately reaches, and `E2E_EXTRA_HOSTS`
 * (comma-separated) adds a deployment's own custom domains.
 */
export function watchRequestHosts(page: Page, options: { extraHosts?: string[] } = {}) {
    const hosts = new Set<string>();
    const onRequest = (request: { url(): string }) => {
        try {
            const url = new URL(request.url());
            if (url.protocol === "http:" || url.protocol === "https:") hosts.add(url.host);
        } catch {
            // data:, blob: and about: URLs carry no host.
        }
    };
    page.context().on("request", onRequest);
    return {
        async thirdParty(): Promise<string[]> {
            page.context().off("request", onRequest);
            const ctx = await apiContext(page);
            const allowed = new Set<string>([new URL(page.url()).host]);
            if (ctx) allowed.add(new URL(ctx.base).host);
            [...(options.extraHosts ?? []), ...(process.env.E2E_EXTRA_HOSTS ?? "").split(",")]
                .map((host) => host.trim())
                .filter(Boolean)
                .forEach((host) => allowed.add(host));
            const awsEndpoint = /(^|\.)(amazonaws\.com|amazonaws\.com\.cn|amazoncognito\.com)$/i;
            return [...hosts]
                .filter((host) => !allowed.has(host) && !awsEndpoint.test(host.split(":")[0]))
                .sort();
        },
    };
}

// ---------------------------------------------------------------------------------------------
// Viewer cases: opening a file, the deployment's features, console and page errors.
// ---------------------------------------------------------------------------------------------

/**
 * Console and page-error text that is benign noise for a viewer case (network aborts on teardown,
 * favicon, React DevTools, the app-shell config refresh). Pass your own list to
 * `watchConsoleErrors` to extend it: `[...CONSOLE_ERROR_IGNORE, /my noise/]`.
 */
export const CONSOLE_ERROR_IGNORE: RegExp[] = [
    /favicon/i,
    /Failed to load resource.*404/i,
    /net::ERR_ABORTED/i,
    /ResizeObserver loop/i,
    /Download the React DevTools/i,
    // App-shell config bootstrap, not a viewer. Auth.tsx re-fetches amplify-config and secure-config
    // on every page load and logs these three when the request fails at the network layer, keeping the
    // cached config so the page still renders — which is why the heading and the viewer surface both
    // appear. Those requests share the connection with a multi-MB asset download, so late in a long
    // run the edge rejects one, and the case that catches it is whichever happens to be running:
    // gramophone.usdz one round, Ifc4_CubeAdvancedBrep.ifc the next. Attributing an app-shell fetch
    // failure to the viewer under test made an environmental condition look asset-specific. A viewer's
    // own init errors are still fatal here, and a genuine config outage fails the heading assertion
    // above rather than reaching this list.
    /getAmplifyConfig: Fetch error/i,
    /Failed to refresh amplify-config/i,
    /Error getting secure-config/i,
];

/**
 * Records `console.error` messages and uncaught page errors not matched by `ignore`. Call before
 * navigating; assert the returned array is empty at the end of the case.
 */
export function watchConsoleErrors(page: Page, ignore: RegExp[] = CONSOLE_ERROR_IGNORE): string[] {
    const errors: string[] = [];
    page.on("console", (msg) => {
        if (msg.type() === "error") {
            const t = msg.text();
            if (!ignore.some((re) => re.test(t))) errors.push(`console: ${t}`);
        }
    });
    page.on("pageerror", (err) => {
        const t = err.message || String(err);
        if (!ignore.some((re) => re.test(t))) errors.push(`pageerror: ${t}`);
    });
    return errors;
}

/**
 * Opens a file on the ViewFile route as a fresh page load, as a deep link does, and waits for the
 * file's heading.
 *
 * Changing only the hash of a page that is still starting (after `featuresEnabled` loaded "/", say)
 * sometimes leaves the app on Home: its start-up navigation wins over the new hash. Loading
 * `about:blank` first makes the file route a new document, which the app routes on start. If the
 * heading still does not appear, the route is loaded once more before the case fails.
 */
export async function openViewerFile(
    page: Page,
    subject: AssetFileRef,
    timeout = 60_000
): Promise<void> {
    // Stored file keys are asset-relative with a leading slash (e.g. /BoomBox.glb); ViewFile
    // parses the segment after /file/ as the key, so it must carry that leading slash.
    const route =
        `/#/databases/${subject.databaseId}/assets/${subject.assetId}` +
        `/file/${encodeURIComponent(subject.key)}`;
    const fileName = subject.key.split("/").filter(Boolean).pop() ?? subject.key;
    // A plain substring match, not a constructed RegExp: a file name carries regex metacharacters.
    const heading = page.getByRole("heading", { name: fileName, exact: false });
    for (let attempt = 1; ; attempt++) {
        await page.goto("about:blank");
        await page.goto(route, { waitUntil: "domcontentloaded" });
        const shown = await heading
            .waitFor({ state: "visible", timeout: attempt === 1 ? timeout / 2 : timeout })
            .then(() => true)
            .catch(() => false);
        if (shown) return;
        if (attempt === 2) {
            throw new Error(
                `the file page for ${subject.key} did not show its heading after two fresh loads ` +
                    `of ${route}; the browser is on ${page.url()}`
            );
        }
    }
}

/** The deployment's feature list, read from the app's cached config (as security.spec.ts does). */
export async function featuresEnabled(page: Page): Promise<string[]> {
    if (!page.url().startsWith("http")) {
        await page.goto("/", { waitUntil: "domcontentloaded" });
    }
    const deadline = Date.now() + 20_000;
    for (;;) {
        const features = await page.evaluate(() => {
            try {
                const raw = localStorage.getItem("vams_cache_config");
                const parsed = raw ? JSON.parse(raw) : null;
                return ((parsed?.value ?? parsed ?? {}).featuresEnabled ?? null) as string[] | null;
            } catch {
                return null;
            }
        });
        if (features && features.length) return features;
        if (Date.now() > deadline) return features ?? [];
        await page.waitForTimeout(1000);
    }
}

/** The entries of `required` (one feature or a list) that `features` lacks. */
export function missingFeatures(features: string[], required: string | string[]): string[] {
    return (typeof required === "string" ? [required] : required).filter(
        (feature) => !features.includes(feature)
    );
}

/**
 * The Columnar Data Viewer's grid: the react-data-grid root (`.rdg`, `role="grid"`). The file page
 * also shows the asset-versions table, so a page-wide `columnheader` or `row` query reads that table.
 */
export function dataGrid(scope: Page | Locator): Locator {
    return scope.locator('.rdg[role="grid"]').first();
}

/** `text` as a RegExp source matching it literally. */
export function escapeRegExp(text: string): string {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Asserts that the viewer named `expected` (its `name` in the viewer config, e.g.
 * "Gaussian Splat Viewer (PlayCanvas)") is the one the file page selected. The viewer picker's
 * trigger shows the selected viewer's name first, then its description; it is rendered whenever at
 * least one viewer is offered, so this holds for a file only one viewer handles too.
 */
export async function expectViewerSelected(page: Page, expected: string): Promise<void> {
    const trigger = page.getByLabel(/select viewer/i).first();
    await expect(trigger, `no viewer picker on the page; expected ${expected}`).toBeVisible({
        timeout: 60_000,
    });
    // The pattern is the escaped viewer name, so it matches the name literally.
    const named = new RegExp(`^\\s*${escapeRegExp(expected)}`); // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp
    await expect(trigger, `the file opened in another viewer than ${expected}`).toHaveText(named, {
        timeout: 30_000,
    });
    await expect(page.getByText(/No Viewer Component Selected/i)).toHaveCount(0);
}

/**
 * Makes `expected` the file's viewer and asserts it was: when the page asks for a choice (an
 * extension several viewers claim), choose the option matching `select` (default: `expected`
 * literally), then check the selection in every case. A case therefore fails, rather than passing
 * on whichever viewer happened to open, when its viewer is not offered or another one loaded.
 */
export async function pickViewerIfAsked(
    page: Page,
    expected: string,
    select?: RegExp
): Promise<void> {
    const placeholder = page.getByText(/No Viewer Component Selected/i);
    const asked = await placeholder
        .waitFor({ state: "visible", timeout: 10_000 })
        .then(() => true)
        .catch(() => false);
    const option = select ?? new RegExp(escapeRegExp(expected)); // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp
    if (asked) await chooseViewer(page, option);
    await expectViewerSelected(page, expected);
}
